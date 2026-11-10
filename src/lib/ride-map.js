import { BkkLib, register } from '../lib/core.js';

/* The ride map's geometry (GLOSSARY.md): the route one departure row runs
   between the stop pair, and where the rider boards and alights. Which
   source wins lives here, in this order: the MÁV index (trains), the GTFS
   row's shapes.txt route, FUTÁR's trip polyline and Transitous's hop
   (weighed against each other and the vehicle's GPS), the coach_shape
   service, the GTFS stop line, and last a straight line marked
   approximate. The sources come in through adapters (rideMapSources
   below), so a test runs it on fakes, without a card or Home Assistant;
   the card only draws the answer with Leaflet. */

/* Transitous's or FUTÁR's boarding stop moves the configured pin only
   when it is this close: further off it is another stop of the name. */
const BOARD_SNAP_M = 300;
/* A FUTÁR polyline that starts this close to the boarding stop is this
   ride's; one that starts further up the line is cut to the hop. */
const FUTAR_START_M = 2500;
/* FUTÁR beats Transitous when it passes the vehicle's GPS this much
   closer, and itself within GPS_NEAR_M of it. */
const GPS_PREFER_M = 400;
const GPS_NEAR_M = 3000;
/* A Transitous route this far from the vehicle's GPS is another trip's. */
const GPS_FAR_M = 4000;
/* A Transitous train route that misses the alighting station by more
   than this, while FUTÁR's passes within TRAIN_HIT_M, is another
   train's (a number shared by two trains). */
const TRAIN_MISS_M = 3000;
const TRAIN_HIT_M = 1500;
/* Board and alight closer than this are one spot: no approximate line. */
const SAME_SPOT_M = 30;
/* A FUTÁR polyline with fewer points is no published line to keep whole. */
const PUBLISHED_MIN_POINTS = 8;

/* Where the rider gets off: the trip's own stop (GTFS or FUTÁR) first, then
   Transitous's hop end, the configured stop, and a Transitous lookup.
   Cards without a destination have no alighting stop. */
async function alightPoint(row, cfg, candidates, src) {
  if (!cfg.destKey && !cfg.destName && !cfg.destStopId) return null;
  const asPt = (p) => (Array.isArray(p) ? BkkLib.mapGpsLatLon(p[0], p[1]) : null);
  const known = (candidates || []).map(asPt).find(Boolean)
    || BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon)
    || BkkLib.knownStopCoords({ id: cfg.destStopId, name: cfg.destName, key: cfg.destKey });
  if (known || !cfg.destName) return known || null;
  try {
    const ref = await src.stopRef({
      id: cfg.destStopId,
      name: cfg.destName,
      key: cfg.destKey,
      lat: cfg.destLat,
      lon: cfg.destLon,
      area: await src.stopArea(cfg),
    }, BkkLib.vehicleBucket(row));
    return (ref && BkkLib.mapGpsLatLon(ref.lat, ref.lon)) || null;
  } catch (_e) {
    return null;
  }
}

/* {shape, approx, board, alight} for one row (see BkkLib.rideMap).
   ride: {row, cfg, hasFutar, vehicle}; src: the adapters. */
export async function rideGeometry(ride, src) {
  const row = ride.row;
  const cfg = ride.cfg || {};
  const hasFutar = !!ride.hasFutar;
  let shape = BkkLib.validMapShape(row && row.shape);
  let board = BkkLib.mapGpsLatLon(cfg.stopLat, cfg.stopLon)
    || BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
  /* MÁV nationwide rails from pfaedle OSM rails (not official 8 km chords
     on Székesfehérvár–Kelenföld, and not the Transitous hop cut). */
  if (BkkLib.isTrainRow(row)) {
    try {
      const mav = await src.mavRoute(row, cfg.stopName || '', cfg.destName || cfg.destKey || '');
      if (mav && mav.shape.length >= 2) {
        /* The train's own stations, on its own route (MÁV GTFS). */
        const ownBoard = mav.board || board;
        const ownAlight = mav.alight || await alightPoint(row, cfg, [], src);
        return {
          shape: mav.shape,
          approx: false,
          board: BkkLib.pinOnRoute(ownBoard, [], mav.shape),
          alight: ownAlight && BkkLib.pinOnRoute(ownAlight, [], mav.shape),
        };
      }
    } catch (_e) { /* MOTIS / FUTÁR still fill */ }
  }
  /* GTFS rows (Volán, city feeds) carry their own stops and shapes.txt
     route: no network beyond one shape shard, and no guessing. */
  const gtfsGeo = await src.gtfsRow(row);
  if (gtfsGeo && gtfsGeo.board) board = gtfsGeo.board;
  if (gtfsGeo && gtfsGeo.shape.length >= 2) {
    return { shape: gtfsGeo.shape, approx: false, board, alight: await alightPoint(row, cfg, [gtfsGeo.dest], src) };
  }
  const tripId = (row && row.tripId) || '';
  const canFutar = hasFutar && (
    BkkLib.rowSource(row) !== 'motis' || !!BkkLib.rowTrainNumber(row)
  );
  /* The row's own FUTÁR trip, or the sensor vehicle's (an ELVIRA train). */
  let geomTrip = '';
  if (canFutar) {
    geomTrip = BkkLib.isFutarTripId(tripId) ? tripId : '';
    const hit = ride.vehicle;
    if (!geomTrip && hit && BkkLib.isFutarTripId(hit.tripId)) geomTrip = hit.tripId;
  }
  let futarShape = [];
  /* City buses/trams: decode FUTÁR trip-details polyline here.
     hass trip_shape does not exist on bkk_stop. */
  if (geomTrip && hasFutar && !BkkLib.isTrainRow(row)) {
    try {
      futarShape = await src.futarShape(geomTrip);
    } catch (_e) { /* MOTIS hop still fills */ }
  }
  /* Same trip-details response (cached): the exact board and alight stops. */
  let futarHop = null;
  if (geomTrip && hasFutar && cfg.destKey) {
    try {
      futarHop = await src.futarHop(geomTrip, { id: cfg.stopId, name: cfg.stopName }, cfg.destKey);
    } catch (_e) { /* Transitous and the config still place the stops */ }
  }
  const published = BkkLib.keepsFullLineShape(row) && !BkkLib.isTrainRow(row)
    && futarShape.length >= PUBLISHED_MIN_POINTS;
  let motisShape = [];
  let motisCoversHead = true;
  let motisMap = null;
  let motisBoardPt = null;
  const area = await src.stopArea(cfg);
  if (!published) {
    try {
      motisMap = await src.motisHop(
        row,
        {
          id: cfg.stopId,
          name: cfg.stopName,
          lat: cfg.stopLat != null ? cfg.stopLat : (gtfsGeo && gtfsGeo.board && gtfsGeo.board[0]),
          lon: cfg.stopLon != null ? cfg.stopLon : (gtfsGeo && gtfsGeo.board && gtfsGeo.board[1]),
          area,
        },
        {
          id: cfg.destStopId,
          name: cfg.destName,
          key: cfg.destKey,
          lat: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[0] : undefined,
          lon: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[1] : undefined,
          area,
        },
        { minutesAfter: cfg.minutesAfter, mode: 'all' },
      );
      motisShape = BkkLib.validMapShape(motisMap && motisMap.shape);
      motisCoversHead = !!(motisMap && motisMap.coversHead);
      if (motisMap && motisMap.board) {
        const motisBoard = BkkLib.mapGpsLatLon(motisMap.board[0], motisMap.board[1]);
        motisBoardPt = motisBoard;
        if (motisBoard) {
          const orig = board;
          if (!orig || BkkLib.haversineMeters(orig, motisBoard) <= BOARD_SNAP_M) {
            board = motisBoard;
          }
        }
      }
    } catch (_e) { /* row shape / GPS still draw */ }
  }
  if (futarHop && futarHop.board && (!board || BkkLib.haversineMeters(board, futarHop.board) <= BOARD_SNAP_M)) {
    board = futarHop.board;
  }
  if (!board) {
    board = BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
  }
  /* coachShape is there only with Home Assistant. */
  if (!published && shape.length < 2 && BkkLib.rowSource(row) === 'gtfs' && src.coachShape) {
    try {
      const points = await src.coachShape(row.label, row.headsign || '');
      shape = BkkLib.validMapShape(BkkLib.decodePolyline(points));
    } catch (_e) { /* FUTAR / MOTIS still fill */ }
  }
  if (canFutar && BkkLib.isTrainRow(row) && BkkLib.isFutarTripId(geomTrip)) {
    /* When Transitous misses the train (BAKONY 905 from Szombathely),
       FUTÁR's own trip details still carry the whole train route, along
       the tracks. bkk_stop has no trip_shape service to ask instead. */
    /* The trip-details response is already cached for the stop pins, so
       asking for its polyline costs nothing; below it also overrules a
       Transitous match that runs elsewhere (another train's number). */
    if (futarShape.length < 2 && hasFutar) {
      try {
        futarShape = await src.futarShape(geomTrip);
      } catch (_e) { /* the stop pins still show */ }
    }
  }
  /* Direction-aware clip only when this mode does not keep the published line. */
  const clipForeignFutar = (pts) => {
    const valid = BkkLib.validMapShape(pts);
    if (BkkLib.keepsFullLineShape(row)) return valid;
    if (!board || valid.length < 2) return valid;
    const startGap = BkkLib.haversineMeters(valid[0], board);
    if (startGap <= FUTAR_START_M) return valid;
    return BkkLib.clipRideShape(
      valid,
      board,
      BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon)
        || BkkLib.knownStopCoords({
          id: cfg.destStopId, name: cfg.destName, key: cfg.destKey,
        }),
      row,
    );
  };
  if (!BkkLib.keepsFullLineShape(row)) {
    futarShape = clipForeignFutar(futarShape);
  }
  if (published) {
    shape = BkkLib.pickPublishedLineShape(motisShape, futarShape, row, motisCoversHead);
  } else {
    shape = BkkLib.combineHopShapes(
      motisShape,
      futarShape,
      motisCoversHead,
      /* The row's own line, or the coach_shape service's for a GTFS row. */
      BkkLib.isTrainRow(row) ? clipForeignFutar(shape) : shape,
    );
    const motisGap = BkkLib.shapeGpsGapMeters(motisShape, row && row.lat, row && row.lon);
    const futarGap = BkkLib.shapeGpsGapMeters(futarShape, row && row.lat, row && row.lon);
    if (motisGap != null && futarGap != null && futarGap + GPS_PREFER_M < motisGap && futarGap < GPS_NEAR_M) {
      shape = futarShape;
    } else if (motisGap != null && motisGap > GPS_FAR_M && futarShape.length >= 2
        && (futarGap == null || futarGap < motisGap)) {
      shape = futarShape;
    } else if (BkkLib.isTrainRow(row) && futarShape.length >= 2 && motisShape.length >= 2) {
      /* The train's own FUTÁR trip passes the alighting station; a
         Transitous route that misses it by kilometres is another train's. */
      const alightPt = (futarHop && futarHop.alight) || (motisMap && motisMap.dest)
        || BkkLib.knownStopCoords({ id: cfg.destStopId, name: cfg.destName, key: cfg.destKey });
      const motisOff = alightPt && BkkLib.shapeGpsGapMeters(motisShape, alightPt[0], alightPt[1]);
      const futarOff = alightPt && BkkLib.shapeGpsGapMeters(futarShape, alightPt[0], alightPt[1]);
      if (motisOff != null && futarOff != null && motisOff > TRAIN_MISS_M && futarOff < TRAIN_HIT_M) {
        shape = futarShape;
      }
    }
  }
  /* Published line stays whole (8E Újpalota→Kelenföld, not the hop). */
  const destPt = await alightPoint(row, cfg, [futarHop && futarHop.alight, motisMap && motisMap.dest], src);
  const clipped = BkkLib.clipRideShape(shape, board, destPt, row);
  if (clipped.length >= 2) shape = clipped;
  shape = BkkLib.validMapShape(shape);
  /* No shapes.txt route and nothing from Transitous: join the GTFS stops. */
  if (shape.length < 2 && gtfsGeo && gtfsGeo.stopLine.length >= 2) shape = gtfsGeo.stopLine;
  /* Every source came back empty: still show where the ride goes, as a
     line marked approximate, rather than no route at all. */
  let approx = false;
  if (shape.length < 2 && board && destPt && BkkLib.haversineMeters(board, destPt) > SAME_SPOT_M) {
    shape = [board, destPt];
    approx = true;
  }
  return {
    shape,
    approx,
    board: BkkLib.pinOnRoute(board, [futarHop && futarHop.board, motisBoardPt], shape),
    alight: destPt && BkkLib.pinOnRoute(destPt, [futarHop && futarHop.alight, motisMap && motisMap.dest], shape),
  };
}

register({
  /* The production adapters of a ride map: the card's index URLs
     (withIndexes) for the MÁV index and the city area, its FUTÁR key and
     trip cache, Transitous, and Home Assistant's coach_shape (null without
     HA). Each calls BkkLib at call time. */
  rideMapSources(opts) {
    const o = opts || {};
    const cfg = o.cfg || {};
    const lib = BkkLib.withIndexes(cfg);
    const hass = o.hass;
    return {
      mavRoute: (row, boardName, alightName) => lib.mavRouteForRow(row, boardName, alightName),
      gtfsRow: (row) => BkkLib.gtfsRowShape(row),
      futarShape: (tripId) => BkkLib.futarTripShape(o.apiKey, o.tripCache || {}, tripId),
      futarHop: (tripId, origin, destKey) => BkkLib.tripHopStops(o.apiKey, o.tripCache || {}, tripId, origin, destKey),
      stopArea: (c) => lib.stopArea(c),
      motisHop: (row, origin, dest, hopOpts) => BkkLib.motisShapeForHop(row, origin, dest, hopOpts),
      coachShape: hass ? (route, headsign) => BkkLib.coachShape(hass, route, headsign) : null,
      stopRef: (stop, bucket) => BkkLib.transitousStopRef(stop, bucket),
    };
  },
  /* A departure row's ride map as {shape, approx, board, alight}: shape the
     route ([lat, lon] points, maybe empty), approx true for the straight
     stand-in line, board and alight the stop pins (alight null without a
     destination). ride: row and cfg (the stop pair), hasFutar (a FUTÁR key
     or the integration's), vehicle (the HA sensor vehicle matched to the
     row, or null), and sources, or apiKey, tripCache and hass for the
     production ones. */
  rideMap(ride) {
    return rideGeometry(ride, ride.sources || BkkLib.rideMapSources(ride));
  },
});

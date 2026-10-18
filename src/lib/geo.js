import { PLATFORM_STRIP, pad2, tzFields } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  hm(ts) {
    if (!ts) return '';
    const f = tzFields(new Date(ts * 1000));
    return `${pad2(f.h)}:${pad2(f.mi)}`;
  },
  vehicleKind(rt) {
    if (BkkLib.isMavRoute(rt)) return 'rail';
    const t = String((rt && rt.type) || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return 'rail';
    if (t.indexOf('TRAM') >= 0) return 'tram';
    if (t.indexOf('SUBWAY') >= 0 || t.indexOf('METRO') >= 0) return 'subway';
    if (t.indexOf('TROLLEY') >= 0) return 'trolleybus';
    if (t === 'COACH' || BkkLib.isVolanRoute(rt)) return 'coach';
    return 'bus';
  },
  vehicleLoc(veh) {
    if (!veh || typeof veh !== 'object') return { lat: NaN, lon: NaN };
    const loc = veh.location || {};
    const lat = loc.lat != null ? loc.lat : veh.latitude;
    const lon = loc.lon != null ? loc.lon : (veh.longitude != null ? veh.longitude : veh.lng);
    const pt = BkkLib.mapGpsLatLon(lat, lon);
    if (!pt) return { lat: NaN, lon: NaN };
    return { lat: pt[0], lon: pt[1] };
  },
  unixSec(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return Date.now() / 1000;
    return n > 1e12 ? n / 1000 : n;
  },
  decodePolyline(encoded) {
    let precision = 5;
    let fromObject = false;
    let hadPrecision = false;
    if (encoded && typeof encoded === 'object') {
      fromObject = true;
      const p = Number(encoded.precision);
      if (Number.isFinite(p) && p >= 4 && p <= 7) {
        precision = p;
        hadPrecision = true;
      }
      encoded = encoded.points || '';
    }
    if (!encoded || typeof encoded !== 'string') return [];
    const factor = Math.pow(10, precision);
    const coords = [];
    let index = 0;
    let lat = 0;
    let lng = 0;
    try {
      while (index < encoded.length) {
        for (let xy = 0; xy < 2; xy++) {
          let result = 0;
          let shift = 0;
          let byte;
          do {
            byte = encoded.charCodeAt(index++) - 63;
            result |= (byte & 0x1f) << shift;
            shift += 5;
          } while (byte >= 0x20);
          const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
          if (xy === 0) lat += delta;
          else lng += delta;
        }
        coords.push([lat / factor, lng / factor]);
      }
    } catch (_e) {
      return [];
    }
    /* MOTIS v5 encodes at 10**precision (usually 6). A Google/FUTÁR string
       is 1e5. If a precision-6 payload was decoded as 1e5, lat is ~475.
       FUTÁR `{points, length}` has no precision field and must stay 1e5 —
       retrying those as 6 shrinks Hungary to ~4.7. */
    let result = coords;
    if (coords.length && precision === 5
        && (Math.abs(coords[0][0]) > 90 || Math.abs(coords[0][1]) > 180)) {
      if (!fromObject || hadPrecision) {
        return BkkLib.decodePolyline({ points: encoded, precision: 6 });
      }
      result = [];
    } else if (coords.length && (Math.abs(coords[0][0]) > 90 || Math.abs(coords[0][1]) > 180)) {
      result = [];
    } else if (coords.length > 2500) {
      const out = [];
      const step = (coords.length - 1) / 2499;
      for (let i = 0; i < 2500; i++) {
        const idx = Math.round(i * step);
        const pt = coords[Math.min(idx, coords.length - 1)];
        if (!out.length || out[out.length - 1][0] !== pt[0] || out[out.length - 1][1] !== pt[1]) {
          out.push(pt);
        }
      }
      const last = coords[coords.length - 1];
      if (out[out.length - 1][0] !== last[0] || out[out.length - 1][1] !== last[1]) out.push(last);
      result = out;
    }
    return result;
  },
  shapeSpanMeters(shape) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return 0;
    let minLat = 90;
    let maxLat = -90;
    let minLon = 180;
    let maxLon = -180;
    pts.forEach((p) => {
      if (p[0] < minLat) minLat = p[0];
      if (p[0] > maxLat) maxLat = p[0];
      if (p[1] < minLon) minLon = p[1];
      if (p[1] > maxLon) maxLon = p[1];
    });
    return BkkLib.haversineMeters([minLat, minLon], [maxLat, maxLon]);
  },
  pickFullerShape(a, b) {
    const sa = BkkLib.validMapShape(a);
    const sb = BkkLib.validMapShape(b);
    if (sa.length < 2) return sb;
    if (sb.length < 2) return sa;
    return BkkLib.shapeSpanMeters(sb) > BkkLib.shapeSpanMeters(sa) ? sb : sa;
  },
  stitchShapes(primary, extra) {
    const a = BkkLib.validMapShape(primary);
    const b = BkkLib.validMapShape(extra);
    if (a.length < 2) return b;
    if (b.length < 2) return a;
    const spanA = BkkLib.shapeSpanMeters(a);
    const spanB = BkkLib.shapeSpanMeters(b);
    if (spanB <= spanA + 1500) return spanB > spanA ? b : a;
    const end = a[a.length - 1];
    let best = 0;
    let bestD = 1e15;
    b.forEach((p, i) => {
      const d = BkkLib.haversineMeters(p, end);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    if (bestD > 3000) return spanB > spanA ? b : a;
    const tail = b.slice(best);
    if (tail.length < 2) return spanB > spanA ? b : a;
    return a.concat(tail.slice(1));
  },
  shapeGpsGapMeters(shape, lat, lon) {
    const gps = BkkLib.mapGpsLatLon(lat, lon);
    const pts = BkkLib.validMapShape(shape);
    if (!gps || pts.length < 1) return null;
    let best = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = BkkLib.haversineMeters(gps, pts[i]);
      if (d < best) best = d;
    }
    return best;
  },
  combineHopShapes(motisShape, futarShape, coversHead, rowShape) {
    const motis = BkkLib.validMapShape(motisShape);
    const futar = BkkLib.validMapShape(futarShape);
    const row = BkkLib.validMapShape(rowShape);
    /* A longer FUTÁR *line* (G43 from Kőbánya-Kispest) must not replace a
       MOTIS trip that already starts at the board. Same-corridor FUTÁR
       may still add a tail (9068 → Veszprém). Do not drop inbound MOTIS. */
    if (motis.length >= 2 && futar.length >= 2) {
      const startGap = BkkLib.haversineMeters(motis[0], futar[0]);
      const sameCorridor = startGap <= 2500;
      if (!sameCorridor) {
        if (coversHead === false) {
          const stitched = BkkLib.stitchShapes(motis, futar);
          return stitched.length >= 2 ? stitched : motis;
        }
        return motis;
      }
      if (coversHead === false) {
        return BkkLib.pickFullerShape(BkkLib.stitchShapes(motis, futar), futar);
      }
      return BkkLib.pickFullerShape(motis, futar);
    }
    if (motis.length >= 2) return motis;
    if (futar.length >= 2) return futar;
    return row;
  },
  finiteLatLon(lat, lon) {
    if (lat == null || lon == null || lat === '' || lon === '') return null;
    const a = Number(lat);
    const b = Number(lon);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    if (Math.abs(a) > 90 || Math.abs(b) > 180) return null;
    /* Number(null) is 0. Null-island / Gulf-of-Guinea must not become a board pin. */
    if (Math.abs(a) < 1 && Math.abs(b) < 1) return null;
    return [a, b];
  },
  RAIL_HUB_COORDS: {
    'BKK_005501024': [47.46445, 19.0219],
    'BKK_005503269': [47.1853, 18.4142],
    'BKK_005510017': [47.5005, 19.0839],
    'BKK_CSF01131': [47.5005, 19.0839],
    'BKK_CS007896': [47.4644, 19.022],
    'BKK_056216': [47.4644, 19.022],
  },
  RAIL_HUB_ALIASES: {
    'kelenfold': [47.46445, 19.0219],
    'budapest kelenfold': [47.46445, 19.0219],
    'szekesfehervar': [47.1853, 18.4142],
    'keleti': [47.5005, 19.0839],
    'keleti palyaudvar': [47.5005, 19.0839],
    'deli': [47.4988, 19.025],
    'budapest deli': [47.4988, 19.025],
  },
  knownStopCoords(stop) {
    const id = String((stop && (stop.id || stop.stopId)) || '');
    const byId = BkkLib.RAIL_HUB_COORDS[id];
    if (byId) return BkkLib.mapGpsLatLon(byId[0], byId[1]);
    const texts = [
      stop && stop.key,
      stop && stop.name,
      stop && stop.destKey,
    ];
    for (let i = 0; i < texts.length; i++) {
      const folded = BkkLib.fold(texts[i] || '');
      if (!folded) continue;
      const hit = BkkLib.RAIL_HUB_ALIASES[folded];
      if (hit) return BkkLib.mapGpsLatLon(hit[0], hit[1]);
    }
    return null;
  },
  /* Live GPS for this card: drop null-island / empty 0,0 and anything
     outside the region Hungarian services actually run in. 0.47,0.19
     (a 1e2 mis-scale of Budapest) sits in the Gulf of Guinea. */
  mapGpsLatLon(lat, lon) {
    const pt = BkkLib.finiteLatLon(lat, lon);
    if (!pt) return null;
    const a = pt[0];
    const b = pt[1];
    if (Math.abs(a) < 1 && Math.abs(b) < 1) return null;
    if (a < 42 || a > 56 || b < 8 || b > 30) return null;
    return pt;
  },
  validMapShape(shape) {
    if (!Array.isArray(shape)) return [];
    return shape.map((pt) => {
      if (!Array.isArray(pt) || pt.length < 2) return null;
      return BkkLib.finiteLatLon(pt[0], pt[1]);
    }).filter(Boolean);
  },
  sliceShapeMeters(shape, fromM, toM) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    let a = Number(fromM);
    let b = Number(toM);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return pts;
    if (b < a) {
      const tmp = a;
      a = b;
      b = tmp;
    }
    const out = [];
    const push = (pt) => {
      if (!pt) return;
      const last = out[out.length - 1];
      if (last && last[0] === pt[0] && last[1] === pt[1]) return;
      out.push(pt);
    };
    push(BkkLib.pointAlongShape(pts, a));
    let dist = 0;
    for (let i = 1; i < pts.length; i++) {
      const next = dist + BkkLib.haversineMeters(pts[i - 1], pts[i]);
      if (next > a && next < b) push(pts[i]);
      dist = next;
    }
    push(BkkLib.pointAlongShape(pts, b));
    return out.length >= 2 ? out : pts;
  },
  clipShapeToHop(shape, details, origin, dest) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    const sts = (details && details.sts) || [];
    const stops = (details && details.stops) || {};
    if (!sts.length || !origin) return pts;
    let oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) {
      return pts;
    }
    let didx = -1;
    const destObj = dest && (dest.id || dest.key || dest.name)
      ? { id: dest.id, name: dest.key || dest.name }
      : null;
    if (destObj) {
      for (let i = Math.max(0, oidx) + 1; i < sts.length; i++) {
        const stop = Object.assign({ id: sts[i].stopId }, stops[sts[i].stopId] || {});
        if (BkkLib.stopMatchesOrigin(stop, destObj)) {
          didx = i;
          break;
        }
      }
    }
    if (didx < 0) didx = sts.length - 1;
    if (oidx <= 0 && didx >= sts.length - 1) {
      return pts;
    }
    const d0 = Number(sts[oidx].shapeDistTraveled);
    const d1 = Number(sts[didx].shapeDistTraveled);
    if (!Number.isFinite(d0) || !Number.isFinite(d1) || d1 === d0) return pts;
    let polyLen = 0;
    for (let i = 1; i < pts.length; i++) {
      polyLen += BkkLib.haversineMeters(pts[i - 1], pts[i]);
    }
    const maxOfficial = Math.max(0, ...sts.map((st) => {
      const n = Number(st.shapeDistTraveled);
      return Number.isFinite(n) ? n : 0;
    }));
    if (!(polyLen > 0) || !(maxOfficial > 0)) return pts;
    const fromM = (Math.min(d0, d1) / maxOfficial) * polyLen;
    const toM = (Math.max(d0, d1) / maxOfficial) * polyLen;
    const sliced = BkkLib.sliceShapeMeters(pts, fromM, toM);
    return sliced;
  },
  /* Drop the inbound GTFS tail before the card origin. Keep everything after
     so a through train (9068 → Veszprém) is not cut at the hop destination. */
  clipShapeFromBoard(shape, board, maxM) {
    const pts = BkkLib.validMapShape(shape);
    const start = Array.isArray(board)
      ? BkkLib.mapGpsLatLon(board[0], board[1])
      : BkkLib.mapGpsLatLon(board && board.lat, board && board.lon);
    if (pts.length < 2 || !start) return pts;
    let best = 0;
    let bestD = 1e15;
    pts.forEach((p, i) => {
      const d = BkkLib.haversineMeters(p, start);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    const limit = Number.isFinite(Number(maxM)) && Number(maxM) > 0 ? Number(maxM) : 1200;
    if (bestD > limit) return pts;
    const out = pts.slice(best);
    return out.length >= 2 ? out : pts;
  },
  /* GTFS/pfaedle stores one canonical direction. A hop at Kelenföld on a
     Székesfehérvár→Kőbánya polyline must keep the prefix (toward Szfvár),
     not the Kőbánya tail. Through trains keep that end of the line. */
  clipShapeFromBoardToward(shape, board, dest, maxM) {
    const pts = BkkLib.validMapShape(shape);
    const start = Array.isArray(board)
      ? BkkLib.mapGpsLatLon(board[0], board[1])
      : BkkLib.mapGpsLatLon(board && board.lat, board && board.lon);
    const goal = Array.isArray(dest)
      ? BkkLib.mapGpsLatLon(dest[0], dest[1])
      : BkkLib.mapGpsLatLon(dest && dest.lat, dest && dest.lon);
    if (pts.length < 2 || !start) return pts;
    if (!goal) return BkkLib.clipShapeFromBoard(pts, start, maxM);
    let iBoard = 0;
    let dBoard = 1e15;
    let iDest = 0;
    let dDest = 1e15;
    pts.forEach((p, i) => {
      const db = BkkLib.haversineMeters(p, start);
      if (db < dBoard) {
        dBoard = db;
        iBoard = i;
      }
      const dd = BkkLib.haversineMeters(p, goal);
      if (dd < dDest) {
        dDest = dd;
        iDest = i;
      }
    });
    const limit = Number.isFinite(Number(maxM)) && Number(maxM) > 0 ? Number(maxM) : 1200;
    if (dBoard > limit) return pts;
    if (dDest > 8000) return BkkLib.clipShapeFromBoard(pts, start, maxM);
    if (iDest === iBoard) return BkkLib.clipShapeFromBoard(pts, start, maxM);
    const out = iDest > iBoard ? pts.slice(iBoard) : pts.slice(0, iBoard + 1);
    return out.length >= 2 ? out : pts;
  },
  clipRideShape(shape, board, dest, row) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    /* Published line stays whole. Hop clip is a last-resort for unknown modes. */
    if (BkkLib.keepsFullLineShape(row)) return pts;
    return BkkLib.clipShapeToBoardDest(pts, board, dest);
  },
  motisCoversHead(hit, row) {
    if (!hit) return false;
    const raw = String((row && (row.head || row.headsign || row.tripTo)) || '').replace(PLATFORM_STRIP, '').trim();
    if (!raw) return true;
    return BkkLib.destOnMotisRow(hit, { name: raw, key: raw });
  },
  rideMapColor(leg) {
    const hex = String((leg && leg.color) || '').replace('#', '');
    if (/^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(hex)) return '#' + hex;
    return '#0F6FC6';
  },
  MAP_ROUTE_PALETTE: [
    '#e53935', '#8e24aa', '#3949ab', '#00897b', '#f9a825', '#6d4c41',
    '#d81b60', '#00acc1', '#43a047', '#fb8c00', '#5c6bc0', '#00838f',
  ],
  /* Dark or white text, whichever reads on this line colour. */
  mapTextOn(color) {
    const hex = BkkLib.normMapHex(color);
    if (!hex) return '#ffffff';
    const lin = (i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const lum = 0.2126 * lin(1) + 0.7152 * lin(3) + 0.0722 * lin(5);
    return lum > 0.4 ? '#1a1a1a' : '#ffffff';
  },
  normMapHex(color) {
    const hex = String(color || '').replace('#', '').trim();
    if (/^[0-9a-fA-F]{3}$/.test(hex)) {
      return ('#' + hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2]).toLowerCase();
    }
    if (/^[0-9a-fA-F]{6}$/.test(hex)) return ('#' + hex).toLowerCase();
    return '';
  },
  mapRouteLabel(row) {
    return String((row && (row.label || row.route || '')) || '').trim() || '?';
  },
  mapRouteColor(row, usedByLabel) {
    const label = BkkLib.mapRouteLabel(row);
    usedByLabel = usedByLabel || {};
    if (usedByLabel[label]) return usedByLabel[label];
    const used = {};
    Object.keys(usedByLabel).forEach((k) => { used[String(usedByLabel[k]).toLowerCase()] = true; });
    let color = BkkLib.normMapHex(row && row.color);
    if (color && used[color]) color = '';
    if (!color) {
      const palette = BkkLib.MAP_ROUTE_PALETTE;
      color = palette.find((c) => !used[c]) || palette[Object.keys(usedByLabel).length % palette.length];
    }
    usedByLabel[label] = color;
    return color;
  },
  legStartPoint(leg) {
    const sh = BkkLib.validMapShape(leg && leg.shape);
    if (sh.length) return sh[0];
    return BkkLib.finiteLatLon(leg && leg.fromLat, leg && leg.fromLon);
  },
  legEndPoint(leg) {
    const sh = BkkLib.validMapShape(leg && leg.shape);
    if (sh.length) return sh[sh.length - 1];
    return BkkLib.finiteLatLon(leg && leg.toLat, leg && leg.toLon);
  },
  stitchJourneyLegCoords(journey) {
    const legs = (journey && journey.legs) || [];
    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      if (BkkLib.legStartPoint(leg) || i < 1) continue;
      const prev = BkkLib.legEndPoint(legs[i - 1]);
      if (!prev) continue;
      if (leg.fromLat == null) {
        leg.fromLat = prev[0];
        leg.fromLon = prev[1];
      }
    }
    for (let i = legs.length - 1; i >= 0; i--) {
      const leg = legs[i];
      if (BkkLib.legEndPoint(leg) || i >= legs.length - 1) continue;
      const next = BkkLib.legStartPoint(legs[i + 1]);
      if (!next) continue;
      if (leg.toLat == null) {
        leg.toLat = next[0];
        leg.toLon = next[1];
      }
    }
    return journey;
  },
  sliceShapeBetween(shape, from, to) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    const a = BkkLib.finiteLatLon(from && from[0], from && from[1]);
    const b = BkkLib.finiteLatLon(to && to[0], to && to[1]);
    if (!a || !b) return pts;
    let i0 = 0;
    let i1 = pts.length - 1;
    let d0 = 1e15;
    let d1 = 1e15;
    pts.forEach((p, i) => {
      const da = BkkLib.haversineMeters(p, a);
      const db = BkkLib.haversineMeters(p, b);
      if (da < d0) {
        d0 = da;
        i0 = i;
      }
      if (db < d1) {
        d1 = db;
        i1 = i;
      }
    });
    if (i1 < i0) {
      const tmp = i0;
      i0 = i1;
      i1 = tmp;
    }
    const sliced = pts.slice(i0, i1 + 1);
    return sliced.length >= 2 ? sliced : pts;
  },
  clipShapeToBoardDest(shape, board, dest) {
    const pts = BkkLib.validMapShape(shape);
    const a = Array.isArray(board)
      ? BkkLib.mapGpsLatLon(board[0], board[1])
      : BkkLib.mapGpsLatLon(board && board.lat, board && board.lon);
    const b = Array.isArray(dest)
      ? BkkLib.mapGpsLatLon(dest[0], dest[1])
      : BkkLib.mapGpsLatLon(dest && dest.lat, dest && dest.lon);
    if (pts.length < 2 || !a || !b) return pts;
    const sliced = BkkLib.sliceShapeBetween(pts, a, b);
    if (sliced.length < 2) return pts;
    const span = BkkLib.shapeSpanMeters(sliced);
    const orig = BkkLib.shapeSpanMeters(pts);
    if (span < 200 && orig > 2000) return pts;
    return sliced;
  },
  async fillJourneyShapes(journey) {
    if (!journey || !Array.isArray(journey.legs)) return journey;
    BkkLib.stitchJourneyLegCoords(journey);
    await Promise.all(journey.legs.map(async (leg) => {
      if (!leg || leg.walk) return;
      if (BkkLib.validMapShape(leg.shape).length >= 2) return;
      const tid = String(leg.tripId || '');
      if (!tid) return;
      try {
        let pts = await BkkLib.motisTripShape(tid);
        const a = BkkLib.finiteLatLon(leg.fromLat, leg.fromLon);
        const b = BkkLib.finiteLatLon(leg.toLat, leg.toLon);
        if (pts.length >= 2 && a && b) pts = BkkLib.sliceShapeBetween(pts, a, b);
        if (pts.length >= 2) leg.shape = pts;
      } catch (_e) { /* endpoint fallback in journeyMapSegments */ }
    }));
    BkkLib.stitchJourneyLegCoords(journey);
    return journey;
  },
  journeyMapSegments(journey, origin, dest) {
    BkkLib.stitchJourneyLegCoords(journey);
    const segs = [];
    const add = (shape, walk, color, from, to, leg) => {
      if (!Array.isArray(shape) || shape.length < 2) return;
      segs.push({
        walk: !!walk,
        color: walk ? '' : (color || '#0F6FC6'),
        label: walk ? '' : String((leg && leg.label) || ''),
        text: walk ? '' : String((leg && leg.text) || ''),
        shape,
        from: from || '',
        to: to || '',
      });
    };
    ((journey && journey.legs) || []).forEach((leg) => {
      let shape = BkkLib.validMapShape(leg.shape);
      if (shape.length < 2) {
        const a = BkkLib.finiteLatLon(leg.fromLat, leg.fromLon);
        const b = BkkLib.finiteLatLon(leg.toLat, leg.toLon);
        if (a && b) shape = [a, b];
      }
      add(shape, leg.walk, BkkLib.rideMapColor(leg), leg.from, leg.to, leg);
    });
    if (segs.length) return segs;
    const start = BkkLib.finiteLatLon(
      origin && (origin.stopLat != null ? origin.stopLat : origin.lat),
      origin && (origin.stopLon != null ? origin.stopLon : origin.lon),
    );
    const end = BkkLib.finiteLatLon(
      dest && (dest.destLat != null ? dest.destLat : dest.lat),
      dest && (dest.destLon != null ? dest.destLon : dest.lon),
    );
    if (start && end) add([start, end], false, '#0F6FC6', '', '');
    return segs;
  },
  /* Stops to mark on a journey map: where each ride boards and alights. A
     transfer at the same stop is one pin. A walk to or from the journey's
     ends gets a start or an end pin, unless it only crosses the same stop
     (Fő tér to the Fő tér pole, Astoria M to Astoria) or is so short that
     the pin would sit on the stop's own. */
  journeyStopPins(segs) {
    const list = Array.isArray(segs) ? segs : [];
    const pins = [];
    const rides = list.filter((s) => !s.walk && Array.isArray(s.shape) && s.shape.length >= 2);
    if (!rides.length) return pins;
    const first = list[0];
    const last = list[list.length - 1];
    const ownPin = (pt, name, ridePt, rideName) => BkkLib.haversineMeters(pt, ridePt) > 150
      && !BkkLib.nameEq(name, rideName);
    if (first.walk && first.shape.length && ownPin(first.shape[0], first.from, rides[0].shape[0], rides[0].from)) {
      pins.push({ kind: 'start', pt: first.shape[0], name: first.from || '' });
    }
    rides.forEach((seg) => {
      const board = seg.shape[0];
      const line = { color: seg.color, label: seg.label || '', text: seg.text || '' };
      const prev = pins[pins.length - 1];
      if (prev && prev.kind === 'alight' && BkkLib.haversineMeters(prev.pt, board) <= 60) {
        Object.assign(prev, line, { kind: 'transfer', name: prev.name || seg.from || '' });
      } else {
        pins.push(Object.assign({ kind: 'board', pt: board, name: seg.from || '' }, line));
      }
      pins.push({ kind: 'alight', pt: seg.shape[seg.shape.length - 1], name: seg.to || '', color: seg.color });
    });
    const lastRide = rides[rides.length - 1];
    const endPt = last.walk && last.shape.length ? last.shape[last.shape.length - 1] : null;
    if (endPt && ownPin(endPt, last.to, lastRide.shape[lastRide.shape.length - 1], lastRide.to)) {
      pins.push({ kind: 'end', pt: endPt, name: last.to || '' });
    }
    return pins;
  },
  haversineMeters(a, b) {
    const toRad = (d) => (d * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(b[0] - a[0]);
    const dLon = toRad(b[1] - a[1]);
    const lat1 = toRad(a[0]);
    const lat2 = toRad(b[0]);
    const h = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  },
  pointAlongShape(shape, targetMeters) {
    if (!Array.isArray(shape) || shape.length < 1) return null;
    if (shape.length === 1 || !(targetMeters > 0)) return shape[0];
    const cum = [0];
    for (let i = 1; i < shape.length; i++) {
      cum.push(cum[i - 1] + BkkLib.haversineMeters(shape[i - 1], shape[i]));
    }
    const total = cum[cum.length - 1];
    if (!(total > 0)) return shape[0];
    const tval = Math.max(0, Math.min(total, Number(targetMeters) || 0));
    let i = 1;
    while (i < cum.length && cum[i] < tval) i += 1;
    const i1 = Math.min(i, shape.length - 1);
    const i0 = Math.max(0, i1 - 1);
    const seg = cum[i1] - cum[i0];
    if (!(seg > 0)) return shape[i1];
    const f = (tval - cum[i0]) / seg;
    return [
      shape[i0][0] + (shape[i1][0] - shape[i0][0]) * f,
      shape[i0][1] + (shape[i1][1] - shape[i0][1]) * f,
    ];
  },
  estimatePositionFromSchedule(shape, stopTimes, nowSec) {
    if (!Array.isArray(shape) || shape.length < 2 || !Array.isArray(stopTimes) || stopTimes.length < 1) {
      return null;
    }
    const stops = stopTimes.map((st) => {
      const tval = st.predictedDepartureTime != null ? st.predictedDepartureTime
        : (st.predictedArrivalTime != null ? st.predictedArrivalTime
          : (st.departureTime != null ? st.departureTime : st.arrivalTime));
      const dist = st.shapeDistTraveled != null ? Number(st.shapeDistTraveled) : NaN;
      return {
        t: tval != null ? Number(tval) : NaN,
        dist: Number.isFinite(dist) ? dist : NaN,
      };
    }).filter((s) => Number.isFinite(s.t)).sort((a, b) => a.t - b.t);
    if (!stops.length) return null;
    const now = Number(nowSec);
    if (!Number.isFinite(now)) return null;
    let targetDist;
    if (now <= stops[0].t) {
      targetDist = Number.isFinite(stops[0].dist) ? stops[0].dist : 0;
    } else if (now >= stops[stops.length - 1].t) {
      const last = stops[stops.length - 1];
      if (Number.isFinite(last.dist)) targetDist = last.dist;
      else return shape[shape.length - 1];
    } else {
      let i = 0;
      while (i < stops.length - 1 && stops[i + 1].t < now) i += 1;
      const a = stops[i];
      const b = stops[i + 1];
      const span = b.t - a.t;
      const f = span > 0 ? (now - a.t) / span : 0;
      if (Number.isFinite(a.dist) && Number.isFinite(b.dist)) {
        targetDist = a.dist + (b.dist - a.dist) * f;
      } else {
        const i0 = Math.floor((i / Math.max(1, stops.length - 1)) * (shape.length - 1));
        const i1 = Math.floor(((i + 1) / Math.max(1, stops.length - 1)) * (shape.length - 1));
        const p0 = shape[Math.max(0, Math.min(shape.length - 1, i0))];
        const p1 = shape[Math.max(0, Math.min(shape.length - 1, i1))];
        return [
          p0[0] + (p1[0] - p0[0]) * f,
          p0[1] + (p1[1] - p0[1]) * f,
        ];
      }
    }
    const maxOfficial = Math.max(
      ...stops.map((s) => (Number.isFinite(s.dist) ? s.dist : 0)),
      targetDist || 0,
    );
    let polyLen = 0;
    for (let i = 1; i < shape.length; i++) {
      polyLen += BkkLib.haversineMeters(shape[i - 1], shape[i]);
    }
    const scaled = maxOfficial > 0 ? (targetDist / maxOfficial) * polyLen : targetDist;
    return BkkLib.pointAlongShape(shape, scaled);
  },
});

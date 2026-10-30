import { PLATFORM_STRIP, pad2, tzFields } from '../config.js';
import { BkkLib } from '../lib/core.js';

/* A shape whose ends lie this close is a closed loop, when it is also at
   least this many times longer than that gap. */
const SHAPE_LOOP_CLOSE_M = 100;
const SHAPE_LOOP_RATIO = 25;
/* A stretch of shape this much farther from a stop than its closest
   approach still passes that stop. */
const SHAPE_PASS_M = 80;
/* Metres of ride one metre of distance from a stop is worth: of two ways
   between the stops, a close fit beats a slightly shorter one. */
const SHAPE_PASS_WEIGHT = 4;
/* A stop this far from the shape is not on it: the timetable position is
   then taken on the straight line between the stops instead. */
const SHAPE_STOP_OFF_M = 1500;
/* A vehicle due at its first stop within this many seconds waits there on
   the map; earlier than that it is not shown at all. */
const SHAPE_WAIT_AHEAD_S = 20 * 60;

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
  /* Metres from a point to the nearest segment of a route (not just its
     vertices, which sit kilometres apart on straight railway chords). */
  distanceToShapeMeters(pt, shape) {
    const p = Array.isArray(pt) ? BkkLib.mapGpsLatLon(pt[0], pt[1]) : null;
    const pts = BkkLib.validMapShape(shape);
    if (!p || !pts.length) return null;
    const k = Math.cos((p[0] * Math.PI) / 180);
    const xy = (q) => [(q[1] - p[1]) * k * 111320, (q[0] - p[0]) * 110540];
    if (pts.length === 1) return Math.hypot(...xy(pts[0]));
    let best = Infinity;
    for (let i = 1; i < pts.length; i++) {
      const a = xy(pts[i - 1]);
      const b = xy(pts[i]);
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, -(a[0] * dx + a[1] * dy) / len)) : 0;
      const d = Math.hypot(a[0] + t * dx, a[1] + t * dy);
      if (d < best) best = d;
    }
    return best;
  },
  /* A stop pin belongs on the drawn route. When the chosen point is off it
     (a hand-kept or configured coordinate) and another source puts the stop
     on the route, that one wins. Without a better candidate the chosen
     point stays: moving it along the line would only guess. */
  pinOnRoute(chosen, candidates, shape) {
    const asPt = (q) => (Array.isArray(q) ? BkkLib.mapGpsLatLon(q[0], q[1]) : null);
    const first = asPt(chosen);
    const others = (candidates || []).map(asPt).filter(Boolean);
    if (BkkLib.validMapShape(shape).length < 2) return first || others[0] || null;
    const gap = (q) => BkkLib.distanceToShapeMeters(q, shape);
    if (first && gap(first) <= 150) return first;
    let best = null;
    let bestGap = Infinity;
    others.forEach((q) => {
      const g = gap(q);
      if (g < bestGap) {
        best = q;
        bestGap = g;
      }
    });
    if (best && bestGap <= 60) return best;
    return first || best;
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
  /* Station coordinates as the MÁV GTFS stops have them (via Transitous),
     so a pin lands on the tracks: Székesfehérvár used to point at the city
     centre, 840 m from the station. */
  RAIL_HUB_COORDS: {
    'BKK_005501024': [47.46432, 19.02063],
    'BKK_005503269': [47.18361, 18.42472],
    'BKK_005510017': [47.50042, 19.08462],
    'BKK_CSF01131': [47.50042, 19.08462],
    'BKK_CS007896': [47.46432, 19.02063],
    'BKK_056216': [47.46432, 19.02063],
  },
  RAIL_HUB_ALIASES: {
    'kelenfold': [47.46432, 19.02063],
    'budapest kelenfold': [47.46432, 19.02063],
    'szekesfehervar': [47.18361, 18.42472],
    'keleti': [47.50042, 19.08462],
    'keleti palyaudvar': [47.50042, 19.08462],
    'deli': [47.49889, 19.025],
    'budapest deli': [47.49889, 19.025],
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
  /* The foot of the perpendicular from a point to every segment of a
     shape (repeated vertices skipped): `at` is metres along the shape, `d`
     the distance to it. */
  _shapeFeet(xy, cum, p) {
    const q = xy(p);
    const feet = [];
    for (let i = 1; i < xy.pts.length; i++) {
      const a = xy.pts[i - 1];
      const b = xy.pts[i];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len2 = dx * dx + dy * dy;
      if (!len2) continue;
      const t = Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / len2));
      const d = Math.hypot(a[0] + t * dx - q[0], a[1] + t * dy - q[1]);
      feet.push({ seg: i - 1, t, d, at: cum[i - 1] + t * (cum[i] - cum[i - 1]) });
    }
    return feet;
  },
  /* Where a shape passes a point: each closest approach (a local minimum of
     the distance along the shape) no more than SHAPE_PASS_M farther than
     the closest one. Inside a segment its foot is one; at a vertex, only
     when the segment before ran towards it (that foot ends at t = 1, and
     the later foot, at t = 0, stands for the vertex). */
  _shapePasses(feet) {
    const limit = feet.reduce((m, f) => Math.min(m, f.d), Infinity) + SHAPE_PASS_M;
    return feet.filter((f, i) => f.d <= limit && (
      (f.t > 0 && f.t < 1)
      || (f.t === 0 && (i === 0 || feet[i - 1].t === 1))
      || (f.t === 1 && i === feet.length - 1)
    ));
  },
  /* The part of a shape from `from` to `to`, in that direction. Both stops
     are projected onto the shape's segments, and every pass of the shape by
     each stop counts: the ride is the shortest stretch from a pass of
     `from` forward to a pass of `to`, a close pass winning over a far one.
     An out-and-back line passes both stops twice (Miskolc ZOO, Pécs 1), and
     only the pair in the direction of travel is short. Two stops on one
     segment give that short piece, not the whole line. A closed loop
     (Szeged 78 starts past Mars tér) may run on over its end; a line whose
     two ends only happen to meet is no loop. */
  sliceShapeBetween(shape, from, to) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    const a = BkkLib.finiteLatLon(from && from[0], from && from[1]);
    const b = BkkLib.finiteLatLon(to && to[0], to && to[1]);
    if (!a || !b) return pts;
    const k = Math.cos((a[0] * Math.PI) / 180) * 111320;
    const xy = (p) => [p[1] * k, p[0] * 110540];
    xy.pts = pts.map(xy);
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(xy.pts[i][0] - xy.pts[i - 1][0], xy.pts[i][1] - xy.pts[i - 1][1]));
    }
    const total = cum[cum.length - 1];
    const loop = BkkLib._shapeIsLoop(pts, total);
    const boards = BkkLib._shapePasses(BkkLib._shapeFeet(xy, cum, a));
    const alights = BkkLib._shapePasses(BkkLib._shapeFeet(xy, cum, b));
    if (!boards.length || !alights.length) return pts;
    let pick = null;
    const consider = (pa, pb) => {
      let span = pb.at - pa.at;
      if (span < 0 && loop) span += total;
      if (span < 0) return;
      const cost = span + SHAPE_PASS_WEIGHT * (pa.d + pb.d);
      if (!pick || cost < pick.cost) pick = { pa, pb, cost, wrap: pb.at < pa.at };
    };
    boards.forEach((pa) => alights.forEach((pb) => consider(pa, pb)));
    const foot = (f) => {
      const p = pts[f.seg];
      const q = pts[f.seg + 1];
      if (f.t === 0) return p;
      if (f.t === 1) return q;
      return [p[0] + (q[0] - p[0]) * f.t, p[1] + (q[1] - p[1]) * f.t];
    };
    const between = (pa, pb, wrap) => {
      const out = [foot(pa)];
      const push = (p) => {
        const last = out[out.length - 1];
        if (last[0] !== p[0] || last[1] !== p[1]) out.push(p);
      };
      if (wrap) {
        for (let i = pa.seg + 1; i < pts.length; i++) push(pts[i]);
        /* The last vertex is the first one again: skip it on the way round. */
        for (let i = 1; i <= pb.seg; i++) push(pts[i]);
      } else {
        for (let i = pa.seg + 1; i <= pb.seg; i++) push(pts[i]);
      }
      push(foot(pb));
      return out;
    };
    let sliced;
    if (pick) {
      sliced = between(pick.pa, pick.pb, pick.wrap);
    } else {
      /* The shape runs the other way: still draw it from `from` to `to`. */
      const nearest = (list) => list.reduce((m, f) => (f.d < m.d ? f : m), list[0]);
      sliced = between(nearest(alights), nearest(boards), false).reverse();
    }
    return sliced.length >= 2 ? sliced : [a, b];
  },
  /* A closed loop: ends this close, the line many times longer than the gap. */
  _shapeIsLoop(pts, total) {
    const gap = BkkLib.haversineMeters(pts[0], pts[pts.length - 1]);
    return gap <= SHAPE_LOOP_CLOSE_M && total >= SHAPE_LOOP_RATIO * Math.max(gap, 20);
  },
  /* Metres along a shape: planar coordinates of its points and the running
     length at each (what sliceShapeBetween projects with). */
  _shapeMetric(pts) {
    const k = Math.cos((pts[0][0] * Math.PI) / 180) * 111320;
    const xy = (p) => [p[1] * k, p[0] * 110540];
    xy.pts = pts.map(xy);
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(xy.pts[i][0] - xy.pts[i - 1][0], xy.pts[i][1] - xy.pts[i - 1][1]));
    }
    return { xy, cum };
  },
  /* Where a vehicle is by its timetable: between the last stop it has left
     and the next one, as far along that run as the time has gone, measured
     along the shape (both stops projected onto it, the passes paired in the
     direction of travel as sliceShapeBetween pairs them: the short, close
     pair wins on an out-and-back line). Standing at the stop between its
     arrival and departure, on the shape where the shape serves it. A
     straight line between the stops when the shape does not pass them
     (another line's geometry, or a stop outside a clipped hop). Null before
     the first departure, from the last arrival on, or without two timed
     stops. timed: [{pt: [lat, lon], arr, dep}] in running order, seconds;
     arr or dep may be missing. A list whose first entry is marked fromBoard
     starts at the rider's boarding stop, not where the trip starts (the
     card's two-stop fallback): before that departure the vehicle is
     somewhere up the line, so there is no position. A closed loop runs on
     over its seam, as sliceShapeBetween slices it. */
  estimateShapePosition(shape, timed, nowSec) {
    const stops = [];
    (Array.isArray(timed) ? timed : []).forEach((s) => {
      const pt = s && BkkLib.mapGpsLatLon(s.pt && s.pt[0], s.pt && s.pt[1]);
      const arr = Number(s && (s.arr != null ? s.arr : s.dep));
      const dep = Number(s && (s.dep != null ? s.dep : s.arr));
      if (!pt || !Number.isFinite(arr) || !Number.isFinite(dep) || arr <= 0 || dep <= 0) return;
      stops.push({ pt, arr: Math.min(arr, dep), dep: Math.max(arr, dep) });
    });
    const now = Number(nowSec);
    if (stops.length < 2 || !Number.isFinite(now)) return null;
    if (now >= stops[stops.length - 1].arr) return null;
    const pts = BkkLib.validMapShape(shape);
    /* Before its first departure the vehicle waits at its first stop: on the
       shape where the shape passes it, else at the stop itself. Only when
       that stop is where the trip starts, and only shortly before it leaves. */
    if (now < stops[0].dep) {
      if (timed[0] && timed[0].fromBoard) return null;
      if (stops[0].dep - now > SHAPE_WAIT_AHEAD_S) return null;
      let pt = stops[0].pt;
      if (pts.length >= 2) {
        const { xy, cum } = BkkLib._shapeMetric(pts);
        const feet = BkkLib._shapeFeet(xy, cum, pt);
        const near = feet.length ? feet.reduce((m, ft) => (ft.d < m.d ? ft : m), feet[0]) : null;
        if (near && near.d <= SHAPE_PASS_M) {
          const p = pts[near.seg];
          const q = pts[near.seg + 1];
          pt = [p[0] + (q[0] - p[0]) * near.t, p[1] + (q[1] - p[1]) * near.t];
        }
      }
      return { pt, from: 0, to: 0, fraction: 0, waiting: true };
    }
    let i = 0;
    while (i + 1 < stops.length - 1 && stops[i + 1].dep <= now) i += 1;
    const a = stops[i];
    const b = stops[i + 1];
    const dwell = now >= b.arr;
    const span = b.arr - a.dep;
    const f = dwell ? 1 : (span > 0 ? Math.max(0, Math.min(1, (now - a.dep) / span)) : 1);
    const result = (pt) => ({ pt, from: i, to: i + 1, fraction: f });
    const straight = () => result([a.pt[0] + (b.pt[0] - a.pt[0]) * f, a.pt[1] + (b.pt[1] - a.pt[1]) * f]);
    if (pts.length < 2) return straight();
    const { xy, cum } = BkkLib._shapeMetric(pts);
    const total = cum[cum.length - 1];
    const loop = BkkLib._shapeIsLoop(pts, total);
    const feetA = BkkLib._shapeFeet(xy, cum, a.pt);
    const feetB = BkkLib._shapeFeet(xy, cum, b.pt);
    if (!feetA.length || !feetB.length) return straight();
    let pick = null;
    BkkLib._shapePasses(feetA).forEach((pa) => BkkLib._shapePasses(feetB).forEach((pb) => {
      let span = pb.at - pa.at;
      if (span <= 0 && loop) span += total;
      if (span <= 0) return;
      const cost = span + SHAPE_PASS_WEIGHT * (pa.d + pb.d);
      if (!pick || cost < pick.cost) pick = { pa, pb, cost, span };
    }));
    if (!pick) return straight();
    if (pick.pa.d > SHAPE_STOP_OFF_M || pick.pb.d > SHAPE_STOP_OFF_M) return straight();
    /* A stop clamped onto the shape's end lies beyond it (outside a clipped
       hop), or beside it (a terminal station next to the track): only the
       former is off the shape. Measured along the end segment, in metres. */
    const beyondEnd = (p, pt) => {
      if (p.at > 0 && p.at < total) return false;
      const q = xy(pt);
      const s0 = xy.pts[p.at <= 0 ? 0 : xy.pts.length - 2];
      const s1 = xy.pts[p.at <= 0 ? 1 : xy.pts.length - 1];
      const dx = s1[0] - s0[0];
      const dy = s1[1] - s0[1];
      const len = Math.hypot(dx, dy);
      if (!len) return false;
      const along = ((q[0] - s0[0]) * dx + (q[1] - s0[1]) * dy) / len;
      return p.at <= 0 ? along < -SHAPE_PASS_M : along > len + SHAPE_PASS_M;
    };
    if (beyondEnd(pick.pa, a.pt) || beyondEnd(pick.pb, b.pt)) return straight();
    const along = (at) => {
      if (loop && at > total) at -= total;
      let seg = 1;
      while (seg < cum.length - 1 && cum[seg] < at) seg += 1;
      const len = cum[seg] - cum[seg - 1];
      const t = len > 0 ? Math.max(0, Math.min(1, (at - cum[seg - 1]) / len)) : 0;
      const p = pts[seg - 1];
      const q = pts[seg];
      return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
    };
    return result(along(pick.pa.at + pick.span * f));
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
  /* The card, its inline map and the full map all ask for the same
     journey's shapes: one fill per journey, its trips fetched once. */
  _journeyFills: new WeakMap(),
  fillJourneyShapes(journey) {
    if (!journey || !Array.isArray(journey.legs)) return Promise.resolve(journey);
    let run = BkkLib._journeyFills.get(journey);
    if (!run) {
      /* A shape fetch that failed (Transitous down for a moment) is asked
         again next time, not kept as a straight line until a page reload. */
      const state = { failed: false };
      run = BkkLib._fillJourneyShapes(journey, state).then((done) => {
        if (state.failed && BkkLib._journeyFills.get(journey) === run) BkkLib._journeyFills.delete(journey);
        return done;
      });
      BkkLib._journeyFills.set(journey, run);
    }
    return run;
  },
  async _fillJourneyShapes(journey, state) {
    const fail = () => { if (state) state.failed = true; };
    BkkLib.stitchJourneyLegCoords(journey);
    await Promise.all(journey.legs.map(async (leg) => {
      if (!leg || leg.walk) return;
      if (BkkLib.validMapShape(leg.shape).length >= 2) return;
      /* A city feed ride (the planner's own fallback): its shapes.txt route
         between its two stops, else the line through its stops. */
      if (leg.gtfs) {
        const g = leg.gtfs;
        const pts = Array.isArray(g.stops) ? g.stops : [];
        const a = pts[g.from];
        const b = pts[g.to];
        let shape = [];
        try {
          shape = await BkkLib.gtfsShape(g.shape);
        } catch (_e) {
          shape = [];
          fail();
        }
        if (shape.length >= 2 && a && b) shape = BkkLib.sliceShapeBetween(shape, a, b);
        if (shape.length < 2) shape = BkkLib.validMapShape(pts.slice(g.from, g.to + 1).filter(Boolean));
        if (shape.length >= 2) leg.shape = shape;
        return;
      }
      const tid = String(leg.tripId || '');
      if (!tid) return;
      try {
        let pts = await BkkLib.motisTripShape(tid);
        const a = BkkLib.finiteLatLon(leg.fromLat, leg.fromLon);
        const b = BkkLib.finiteLatLon(leg.toLat, leg.toLon);
        if (pts.length >= 2 && a && b) pts = BkkLib.sliceShapeBetween(pts, a, b);
        if (pts.length >= 2) leg.shape = pts;
      } catch (_e) {
        /* endpoint fallback in journeyMapSegments */
        fail();
      }
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
});

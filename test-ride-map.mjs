/* The ride map's geometry (src/lib/ride-map.js), straight from src/: one row
   on fake shape sources, no card, DOM, Leaflet or Home Assistant. Points
   sit on the 19.0 meridian, so a distance in metres is an exact latitude
   offset for the haversine the thresholds are measured with. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BkkLib } from './src/lib/core.js';
import './src/lib/names.js';
import './src/lib/futar.js';
import './src/lib/transitous.js';
import './src/lib/geo.js';
import './src/lib/gtfs.js';
import './src/lib/services.js';
import './src/lib/departures.js';
import './src/lib/vehicles.js';
import './src/lib/rows.js';
import './src/lib/context.js';
import { rideGeometry } from './src/lib/ride-map.js';

const M_PER_DEG = 6371000 * Math.PI / 180;
const LON = 19.0;
/* m metres north (negative: south) of latitude lat, on the meridian. */
const at = (lat, m) => [lat + (m || 0) / M_PER_DEG, LON];
const BOARD = at(47.5);
const DEST = at(47.6);
const line = (from, to, n) => Array.from({ length: n }, (_v, i) => [from + (to - from) * i / (n - 1), LON]);
const near = (a, b) => !!a && !!b && BkkLib.haversineMeters(a, b) < 1;

/* The stop pair: both ends have coordinates. */
const PAIR = {
  stopId: 'BKK_A', stopName: 'Alpha', stopLat: BOARD[0], stopLon: LON,
  destKey: 'omega', destName: 'Omega', destStopId: 'BKK_O', destLat: DEST[0], destLon: LON,
  minutesAfter: 45,
};
const bus = (extra) => Object.assign({ tripId: 'BKK_C1', label: '7', headsign: 'Omega', vehicle: 'bus', rawType: 'BUS' }, extra);
const train = (extra) => Object.assign({
  tripId: 'BKK_9051_1', label: 'BAKONY', headsign: 'Omega', vehicle: 'rail', trainNumber: '905',
}, extra);

/* Fake sources with a call log; over replaces any of them (null removes it). */
function sources(over) {
  const calls = [];
  const base = {
    mavRoute: async () => null,
    gtfsRow: async () => null,
    futarShape: async () => [],
    futarHop: async () => null,
    stopArea: async () => '',
    motisHop: async () => null,
    coachShape: null,
    stopRef: async () => null,
  };
  const src = {};
  Object.keys(base).forEach((k) => {
    const fn = over && k in over ? over[k] : base[k];
    src[k] = fn ? (...args) => { calls.push([k, args]); return fn(...args); } : null;
  });
  const count = (k) => calls.filter((c) => c[0] === k).length;
  const args = (k) => (calls.find((c) => c[0] === k) || [null, null])[1];
  return { src, calls, count, args };
}
const ride = (row, extra) => Object.assign({ row, cfg: PAIR, hasFutar: true, vehicle: null }, extra);

/* Google polyline, precision 5 (what coach_shape answers). */
const encode = (pts) => {
  const e = (v) => { let x = v < 0 ? ~(v << 1) : v << 1; let o = ''; while (x >= 0x20) { o += String.fromCharCode((0x20 | (x & 0x1f)) + 63); x >>= 5; } return o + String.fromCharCode(x + 63); };
  let pl = 0; let pg = 0; let out = '';
  pts.forEach(([la, lo]) => { const a = Math.round(la * 1e5); const b = Math.round(lo * 1e5); out += e(a - pl) + e(b - pg); pl = a; pg = b; });
  return out;
};

/* 1 · MÁV index */

test('a train on the MÁV index draws its line and own stations, and asks nothing else', async () => {
  const mavLine = line(47.4, 47.7, 6);
  const f = sources({ mavRoute: async () => ({ shape: mavLine, board: at(47.46), alight: at(47.64) }) });
  const got = await rideGeometry(ride(train()), f.src);
  assert.deepEqual(got.shape, mavLine);
  assert.equal(got.approx, false);
  assert.ok(near(got.board, at(47.46)) && near(got.alight, at(47.64)));
  assert.deepEqual(f.args('mavRoute').slice(1), ['Alpha', 'Omega']);
  assert.deepEqual(f.calls.map((c) => c[0]), ['mavRoute']);
});

test('without its own stations the MÁV line takes the configured stop pair', async () => {
  const f = sources({ mavRoute: async () => ({ shape: line(47.4, 47.7, 6), board: null, alight: null }) });
  const got = await rideGeometry(ride(train(), { cfg: Object.assign({}, PAIR, { destName: '' }) }), f.src);
  assert.ok(near(got.board, BOARD) && near(got.alight, DEST));
  /* destName empty: the index is asked by the destination key. */
  assert.equal(f.args('mavRoute')[2], 'omega');
});

test('an empty or failing MÁV index falls through to the other sources', async () => {
  for (const mavRoute of [async () => ({ shape: [BOARD], board: null, alight: null }), async () => { throw new Error('x'); }]) {
    const f = sources({ mavRoute });
    const got = await rideGeometry(ride(train()), f.src);
    assert.equal(f.count('motisHop'), 1);
    assert.equal(got.approx, true);
  }
});

test('only trains ask the MÁV index', async () => {
  const f = sources();
  await rideGeometry(ride(bus()), f.src);
  assert.equal(f.count('mavRoute'), 0);
});

/* 2 · GTFS shapes.txt */

test('a GTFS row draws its shapes.txt route between its own stops, with no network', async () => {
  const shp = line(47.49, 47.61, 5);
  const f = sources({ gtfsRow: async () => ({ shape: shp, board: at(47.49), dest: at(47.61), stopLine: [] }) });
  const got = await rideGeometry(ride(bus({ tripId: 'gtfs:1' })), f.src);
  assert.deepEqual(got.shape, shp);
  assert.equal(got.approx, false);
  assert.ok(near(got.board, at(47.49)) && near(got.alight, at(47.61)));
  assert.deepEqual(f.calls.map((c) => c[0]), ['gtfsRow']);
});

test('without a shapes.txt route the GTFS stops are joined, still with their board stop', async () => {
  const stops = [at(47.49), at(47.55), at(47.61)];
  const f = sources({ gtfsRow: async () => ({ shape: [], board: at(47.49), dest: at(47.61), stopLine: stops }) });
  const got = await rideGeometry(ride(bus({ tripId: 'gtfs:1' }), { hasFutar: false }), f.src);
  assert.deepEqual(got.shape, stops);
  assert.equal(got.approx, false);
  assert.ok(near(got.board, at(47.49)));
  /* Transitous is asked from the GTFS stop's coordinates and towards its alighting stop. */
  const [, origin, dest] = f.args('motisHop');
  assert.equal(origin.lat, BOARD[0]);
  assert.equal(dest.lat, at(47.61)[0]);
});

/* 3 · FUTÁR trip shape */

test('a city row with a FUTÁR trip draws its polyline, one request per source', async () => {
  const fl = line(47.5, 47.6, 4);
  const f = sources({ futarShape: async () => fl });
  const got = await rideGeometry(ride(bus()), f.src);
  assert.deepEqual(got.shape, fl);
  assert.equal(got.approx, false);
  assert.deepEqual(f.args('futarShape'), ['BKK_C1']);
  assert.deepEqual(f.args('futarHop'), ['BKK_C1', { id: 'BKK_A', name: 'Alpha' }, 'omega']);
  assert.equal(f.count('futarShape'), 1);
  assert.equal(f.count('futarHop'), 1);
  assert.equal(f.count('stopArea'), 1);
  assert.equal(f.count('motisHop'), 1);
});

test('without a FUTÁR key no FUTÁR source is asked', async () => {
  const f = sources({ futarShape: async () => line(47.5, 47.6, 4) });
  const got = await rideGeometry(ride(bus(), { hasFutar: false, vehicle: { tripId: 'BKK_X' } }), f.src);
  assert.equal(f.count('futarShape') + f.count('futarHop'), 0);
  assert.equal(got.approx, true);
});

test('a published FUTÁR line (8+ points) is kept whole and Transitous is not asked', async () => {
  const full = line(47.4, 47.7, 8);
  const f = sources({ futarShape: async () => full, motisHop: async () => ({ shape: line(47.5, 47.6, 3), coversHead: true }) });
  const got = await rideGeometry(ride(bus()), f.src);
  assert.deepEqual(got.shape, full);
  assert.equal(f.count('motisHop'), 0);
  const g = sources({ futarShape: async () => line(47.4, 47.7, 7) });
  await rideGeometry(ride(bus()), g.src);
  assert.equal(g.count('motisHop'), 1);
});

test('a row without a FUTÁR trip uses the sensor vehicle’s', async () => {
  const f = sources();
  await rideGeometry(ride(bus({ tripId: 'elvira:1', source: 'elvira', vehicle: 'bus' }), { vehicle: { tripId: 'BKK_V9' } }), f.src);
  assert.deepEqual(f.args('futarShape'), ['BKK_V9']);
  const g = sources();
  await rideGeometry(ride(bus({ tripId: 'elvira:1', source: 'elvira' }), { vehicle: { tripId: 'gtfs:9' } }), g.src);
  assert.equal(g.count('futarShape') + g.count('futarHop'), 0);
});

test('a Transitous row asks FUTÁR only when it has a train number', async () => {
  const f = sources();
  await rideGeometry(ride(bus({ tripId: 'motis:1' }), { vehicle: { tripId: 'BKK_V9' } }), f.src);
  assert.equal(f.count('futarHop'), 0);
  const g = sources();
  await rideGeometry(ride(train({ tripId: 'motis:1' }), { vehicle: { tripId: 'BKK_V9' } }), g.src);
  assert.deepEqual(g.args('futarHop')[0], 'BKK_V9');
});

test('a train Transitous misses draws FUTÁR’s whole route, asked once, its pins on it', async () => {
  const futarLine = line(47.3, 47.7, 5);
  const f = sources({
    futarShape: async () => futarLine,
    futarHop: async () => ({ board: at(47.5), alight: at(47.6) }),
    motisHop: async () => ({ shape: [], board: at(47.5), dest: at(47.6), coversHead: false }),
  });
  const got = await rideGeometry(ride(train()), f.src);
  assert.deepEqual(got.shape, futarLine);
  assert.equal(f.count('futarShape'), 1);
  assert.ok(BkkLib.distanceToShapeMeters(got.board, got.shape) < 1 && BkkLib.distanceToShapeMeters(got.alight, got.shape) < 1);
});

/* 4 · Transitous hop */

test('Transitous’s hop draws when FUTÁR has nothing, asked with the stop pair and its area', async () => {
  const hop = line(47.5, 47.6, 3);
  const f = sources({ stopArea: async () => 'Testváros', motisHop: async () => ({ shape: hop, coversHead: true, board: null, dest: null }) });
  const got = await rideGeometry(ride(bus(), { hasFutar: false }), f.src);
  assert.deepEqual(got.shape, hop);
  const [row, origin, dest, opts] = f.args('motisHop');
  assert.equal(row.tripId, 'BKK_C1');
  assert.deepEqual(origin, { id: 'BKK_A', name: 'Alpha', lat: BOARD[0], lon: LON, area: 'Testváros' });
  assert.deepEqual(dest, { id: 'BKK_O', name: 'Omega', key: 'omega', lat: undefined, lon: undefined, area: 'Testváros' });
  assert.deepEqual(opts, { minutesAfter: 45, mode: 'all' });
});

test('a failing Transitous lookup still leaves a line', async () => {
  const f = sources({ motisHop: async () => { throw new Error('down'); } });
  const got = await rideGeometry(ride(bus(), { hasFutar: false }), f.src);
  assert.equal(got.approx, true);
});

/* 5 · coach_shape */

test('a GTFS row without its own line draws the coach_shape service’s', async () => {
  const pts = [[47.5, 19.0], [47.55, 19.01], [47.6, 19.0]];
  const f = sources({ coachShape: async () => encode(pts) });
  const got = await rideGeometry(ride(bus({ tripId: 'gtfs:azt', label: '1234', headsign: 'Omega' }), { hasFutar: false }), f.src);
  assert.equal(got.approx, false);
  assert.deepEqual(got.shape, pts);
  assert.deepEqual(f.args('coachShape'), ['1234', 'Omega']);
});

test('coach_shape is only for GTFS rows without a line, and only with Home Assistant', async () => {
  const f = sources({ coachShape: async () => encode([[47.5, 19.0], [47.6, 19.0]]) });
  await rideGeometry(ride(train()), f.src);
  await rideGeometry(ride(bus()), f.src);
  await rideGeometry(ride(bus({ tripId: 'gtfs:1', shape: line(47.5, 47.6, 3) })), f.src);
  assert.equal(f.count('coachShape'), 0);
  const noHa = sources({ coachShape: null });
  const got = await rideGeometry(ride(bus({ tripId: 'gtfs:1' })), noHa.src);
  assert.equal(got.approx, true);
});

/* 6 · approximate line */

test('no source at all still gives a line from board to alight, marked approximate', async () => {
  const f = sources();
  const got = await rideGeometry(ride(train({ tripId: 'elvira:999', source: 'elvira' })), f.src);
  assert.equal(got.approx, true);
  assert.ok(near(got.shape[0], BOARD) && near(got.shape[1], DEST));
  assert.ok(near(got.board, BOARD) && near(got.alight, DEST));
});

test('board and alight within 30 m are one spot: no line', async () => {
  const cfgAt = (m) => Object.assign({}, PAIR, { destLat: at(47.5, m)[0] });
  const inside = await rideGeometry(ride(bus(), { hasFutar: false, cfg: cfgAt(29) }), sources().src);
  assert.equal(inside.approx, false);
  assert.equal(inside.shape.length, 0);
  const outside = await rideGeometry(ride(bus(), { hasFutar: false, cfg: cfgAt(31) }), sources().src);
  assert.equal(outside.approx, true);
});

test('without a destination there is no alighting stop and no line', async () => {
  const cfg = { stopId: 'BKK_A', stopName: 'Alpha', stopLat: BOARD[0], stopLon: LON };
  const f = sources({ futarHop: async () => ({ board: null, alight: DEST }), motisHop: async () => ({ shape: [], dest: DEST }) });
  const got = await rideGeometry(ride(bus(), { cfg }), f.src);
  assert.equal(got.alight, null);
  assert.equal(got.approx, false);
  /* No destination key: FUTÁR is not asked for the hop's stops. */
  assert.equal(f.count('futarHop'), 0);
});

/* Thresholds */

test('a boarding stop from Transitous within 300 m moves the pin; further off it does not', async () => {
  const go = async (m) => {
    const f = sources({ motisHop: async () => ({ shape: [], board: at(47.5, m), coversHead: false }) });
    return rideGeometry(ride(bus(), { hasFutar: false }), f.src);
  };
  assert.ok(near((await go(290)).board, at(47.5, 290)));
  assert.ok(near((await go(310)).board, BOARD));
});

test('a boarding stop from FUTÁR within 300 m moves the pin; further off it does not', async () => {
  const go = async (m) => {
    const f = sources({ futarHop: async () => ({ board: at(47.5, -m), alight: null }) });
    return rideGeometry(ride(bus()), f.src);
  };
  assert.ok(near((await go(290)).board, at(47.5, -290)));
  assert.ok(near((await go(310)).board, BOARD));
});

test('a stop without coordinates takes Transitous’s or FUTÁR’s boarding stop at any distance', async () => {
  const cfg = Object.assign({}, PAIR, { stopLat: null, stopLon: null });
  const f = sources({ motisHop: async () => ({ shape: [], board: at(47.4), coversHead: false }) });
  assert.ok(near((await rideGeometry(ride(bus(), { cfg, hasFutar: false }), f.src)).board, at(47.4)));
});

test('an off-route boarding pin yields to the FUTÁR stop on the route', async () => {
  /* The configured stop is ~575 m from FUTÁR's (too far to move the pin)
     and ~400 m off the drawn route, which passes FUTÁR's stop. */
  const hop = [at(47.5, 1000), at(47.6)];
  const f = sources({ futarShape: async () => hop, futarHop: async () => ({ board: hop[0], alight: null }) });
  const cfg = Object.assign({}, PAIR, { stopLat: at(47.5, 600)[0], stopLon: LON + 0.0055 });
  const got = await rideGeometry(ride(bus(), { cfg }), f.src);
  assert.ok(near(got.board, hop[0]), JSON.stringify(got.board));
});

test('a FUTÁR polyline starting within 2500 m of the board is this ride’s; further off it is cut to the hop', async () => {
  /* A ferry keeps no published line, so its shapes are clipped. The final
     clip runs to FUTÁR's alighting stop (47.65); the 2500 m one to the
     configured destination (47.6). */
  const go = async (m) => {
    const f = sources({
      futarShape: async () => [at(47.5, -m), at(47.55), at(47.6), at(47.65)],
      futarHop: async () => ({ board: null, alight: at(47.65) }),
    });
    return rideGeometry(ride(bus({ tripId: 'BKK_F1', vehicle: 'ferry', rawType: 'FERRY' })), f.src);
  };
  const inside = await go(2490);
  assert.ok(near(inside.shape[inside.shape.length - 1], at(47.65)), JSON.stringify(inside.shape));
  const outside = await go(2510);
  assert.ok(near(outside.shape[outside.shape.length - 1], at(47.6)), JSON.stringify(outside.shape));
});

/* GPS at 47.55: Transitous's route runs north of it, FUTÁR's south, and
   they start far apart, so without the GPS rules Transitous's is drawn. */
const GPS = at(47.55);
const gpsRide = async (motisGap, futarGap) => {
  const motis = [at(GPS[0], motisGap), at(GPS[0], motisGap + 1000)];
  const futar = [at(GPS[0], -futarGap), at(GPS[0], -futarGap - 1000)];
  const f = sources({ futarShape: async () => futar, motisHop: async () => ({ shape: motis, coversHead: true }) });
  const got = await rideGeometry(ride(bus({ lat: GPS[0], lon: LON })), f.src);
  return near(got.shape[0], futar[0]) ? 'futar' : (near(got.shape[0], motis[0]) ? 'motis' : 'other');
};

test('FUTÁR wins when it passes the vehicle more than 400 m closer than Transitous', async () => {
  assert.equal(await gpsRide(1420, 1000), 'futar');
  assert.equal(await gpsRide(1380, 1000), 'motis');
});

test('…but only while FUTÁR itself passes within 3000 m of the vehicle', async () => {
  assert.equal(await gpsRide(3500, 2990), 'futar');
  assert.equal(await gpsRide(3500, 3010), 'motis');
});

test('a Transitous route over 4000 m from the vehicle gives way to a nearer FUTÁR one', async () => {
  assert.equal(await gpsRide(4010, 3800), 'futar');
  assert.equal(await gpsRide(3990, 3780), 'motis');
});

test('a Transitous train route missing the alighting station by 3000 m gives way to FUTÁR’s through it (within 1500 m)', async () => {
  const go = async (motisOff, futarOff) => {
    const futar = [at(47.5), at(47.6, -futarOff)];
    const motis = [at(47.6, motisOff), at(47.7, motisOff)];
    const f = sources({
      futarShape: async () => futar,
      futarHop: async () => ({ board: at(47.5), alight: DEST }),
      motisHop: async () => ({ shape: motis, coversHead: true, dest: null }),
    });
    const got = await rideGeometry(ride(train()), f.src);
    return near(got.shape[0], futar[0]) ? 'futar' : 'motis';
  };
  assert.equal(await go(3010, 1490), 'futar');
  assert.equal(await go(2990, 1490), 'motis');
  assert.equal(await go(3010, 1510), 'motis');
});

/* The alighting stop */

test('the trip’s own alighting stop wins over Transitous’s and the configured one', async () => {
  const f = sources({ futarHop: async () => ({ board: null, alight: at(47.61) }), motisHop: async () => ({ shape: [], dest: at(47.62) }) });
  assert.ok(near((await rideGeometry(ride(bus()), f.src)).alight, at(47.61)));
  const g = sources({ motisHop: async () => ({ shape: [], dest: at(47.62) }) });
  assert.ok(near((await rideGeometry(ride(bus(), { hasFutar: false }), g.src)).alight, at(47.62)));
});

test('a destination without coordinates is a known station, else looked up on Transitous', async () => {
  const hub = Object.assign({}, PAIR, { destLat: null, destLon: null, destStopId: '', destKey: 'szekesfehervar', destName: 'Székesfehérvár' });
  const f = sources();
  const got = await rideGeometry(ride(bus(), { hasFutar: false, cfg: hub }), f.src);
  assert.deepEqual(got.alight, [47.18361, 18.42472]);
  assert.equal(f.count('stopRef'), 0);

  const unknown = Object.assign({}, PAIR, { destLat: null, destLon: null, city: 'x' });
  const g = sources({ stopArea: async () => 'Testváros', stopRef: async () => ({ lat: 47.61, lon: LON }) });
  const looked = await rideGeometry(ride(bus(), { hasFutar: false, cfg: unknown }), g.src);
  assert.deepEqual(looked.alight, [47.61, LON]);
  assert.deepEqual(g.args('stopRef'), [{ id: 'BKK_O', name: 'Omega', key: 'omega', lat: null, lon: null, area: 'Testváros' }, 'bus']);
  /* One area for Transitous's hop, one for the stop lookup. */
  assert.equal(g.count('stopArea'), 2);

  const failing = sources({ stopRef: async () => { throw new Error('down'); } });
  assert.equal((await rideGeometry(ride(bus(), { hasFutar: false, cfg: unknown }), failing.src)).alight, null);
  const noName = sources();
  const keyOnly = Object.assign({}, unknown, { destName: '', destKey: 'nowhere' });
  assert.equal((await rideGeometry(ride(bus(), { hasFutar: false, cfg: keyOnly }), noName.src)).alight, null);
  assert.equal(noName.count('stopRef'), 0);
});

/* Production adapters */

test('rideMapSources hands each source its own arguments and the card’s context, read from BkkLib at call time', async () => {
  const names = ['mavRouteForRow', 'gtfsRowShape', 'futarTripShape', 'tripHopStops', 'stopArea', 'motisShapeForHop', 'coachShape', 'transitousStopRef'];
  const keep = {};
  const got = {};
  names.forEach((k) => {
    keep[k] = BkkLib[k];
    BkkLib[k] = function (...args) { got[k] = { self: this, args }; return Promise.resolve(k); };
  });
  try {
    const cfg = { mavIndex: '/local/mav.json.gz' };
    const cache = {};
    const hass = { states: {} };
    const ix = { volan: async () => null, city: async () => null, mav: async () => null };
    const ctx = BkkLib.cardContext({
      cfg: () => cfg, apiKey: () => 'k', tripCache: () => cache, hass: () => hass, indexes: () => ix,
    });
    const src = BkkLib.rideMapSources(ctx);
    const row = { tripId: 'BKK_1' };
    await src.mavRoute(row, 'A', 'B');
    assert.deepEqual(got.mavRouteForRow.args, [ix, row, 'A', 'B']);
    await src.gtfsRow(row);
    assert.deepEqual(got.gtfsRowShape.args, [row]);
    await src.futarShape('BKK_1');
    assert.deepEqual(got.futarTripShape.args, ['k', cache, 'BKK_1']);
    assert.equal(got.futarTripShape.args[1], cache);
    await src.futarHop('BKK_1', { id: 'a' }, 'omega');
    assert.deepEqual(got.tripHopStops.args, ['k', cache, 'BKK_1', { id: 'a' }, 'omega']);
    await src.stopArea(cfg);
    assert.deepEqual(got.stopArea.args, [ix, cfg]);
    await src.motisHop(row, { id: 'a' }, { id: 'b' }, { mode: 'all' });
    assert.deepEqual(got.motisShapeForHop.args, [row, { id: 'a' }, { id: 'b' }, { mode: 'all' }]);
    await src.coachShape('1234', 'Omega');
    assert.deepEqual(got.coachShape.args, [hass, '1234', 'Omega']);
    await src.stopRef({ name: 'Omega' }, 'bus');
    assert.deepEqual(got.transitousStopRef.args, [{ name: 'Omega' }, 'bus']);
    assert.equal(BkkLib.rideMapSources(BkkLib.cardContext({ cfg: () => cfg })).coachShape, null);
  } finally {
    Object.assign(BkkLib, keep);
  }
});

test('BkkLib.rideMap runs on the sources it is given', async () => {
  const f = sources({ futarShape: async () => line(47.5, 47.6, 4) });
  const got = await BkkLib.rideMap(Object.assign(ride(bus()), { sources: f.src }));
  assert.equal(got.shape.length, 4);
  assert.equal(f.count('futarShape'), 1);
});

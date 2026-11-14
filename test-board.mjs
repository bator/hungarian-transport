/* The departure board (src/lib/board.js), straight from src/: one poll on
   fake sources and a clock that stands still, no card, DOM or Home
   Assistant. The board on screen is a plain object the snapshots land on. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BkkLib } from './src/lib/core.js';
import './src/lib/names.js';
import './src/lib/futar.js';
import './src/lib/transitous.js';
import './src/lib/geo.js';
import './src/lib/gtfs.js';
import './src/lib/services.js';
import './src/lib/vehicles.js';
import './src/lib/rows.js';
import './src/lib/elvira-rows.js';
import './src/lib/context.js';
import { elviraSource } from './src/lib/board.js';

/* Row helpers compare departures with the real clock (still due?), so the
   fake one stands still at the moment the file started. */
const T0 = Math.floor(Date.now() / 1000);
const ELVIRA_HASS = { states: {}, services: { hungarian_transport: { elvira_between: {} } } };
const NO_SERVICES = { states: {}, services: {} };
/* An index resolver with no index: the ELVIRA source only hands it on. */
const noIndex = async () => { throw new Error('no index here'); };
const NO_INDEXES = { volan: noIndex, city: noIndex, mav: noIndex };
const KELENFOLD = {
  stopId: 'BKK_005501024', stopName: 'Budapest-Kelenföld',
  destKey: 'szekesfehervar', destName: 'Székesfehérvár', destStopId: 'BKK_005503269', routeIds: [],
};
const rail = (extra) => Object.assign({
  vehicle: 'rail', rawType: 'RAIL', color: '#4477aa', text: '#ffffff', platform: '', delay: 0, travel: null,
}, extra);
const ic = (id, n, label, depMin, extra) => rail(Object.assign({
  tripId: `elvira:${id}`, label, trainNumber: n, dep: T0 + depMin * 60, sched: T0 + depMin * 60, travel: 39, destHit: true,
}, extra));
const bus = (id, depMin, extra) => Object.assign({
  tripId: id, label: '7', head: 'Zugló', vehicle: 'bus', rawType: 'BUS', dep: T0 + depMin * 60, sched: T0 + depMin * 60, travel: null,
}, extra);
const ids = (rows) => (rows || []).map((r) => r.tripId).join(',');
const errKey = (banner) => (banner && banner.i18nKey) || '';

/* Fake sources with a call log; over replaces any of them. */
function sources(over) {
  const calls = [];
  const clock = { ms: T0 * 1000 };
  const base = {
    now: () => clock.ms,
    timetables: async () => [],
    elvira: async () => ({ rows: [], error: '', detail: '' }),
    transitous: async (rows) => rows,
    coachGps: async (_hass, rows) => rows,
    realtime: async () => null,
    rideTime: async () => null,
    sensors: () => {},
  };
  const src = {};
  Object.keys(base).forEach((k) => {
    const fn = (over && over[k]) || base[k];
    src[k] = k === 'now' ? fn : (...args) => { calls.push([k, args]); return fn(...args); };
  });
  const count = (k) => calls.filter((c) => c[0] === k).length;
  return { src, calls, clock, count };
}

/* A card in one object: the board on screen, its memory and generation. */
function screen(cfg, opts) {
  opts = opts || {};
  const board = Object.assign({ rows: [], banner: '', loading: false, filled: false }, opts.board);
  const shown = [];
  const gen = { now: 1 };
  const state = opts.state || { viaFutarUntil: 0 };
  const poll = (src, mine) => {
    const own = mine || gen.now;
    const tripCache = {};
    return BkkLib.nextBoard({
      ctx: BkkLib.cardContext({
        cfg: () => cfg,
        hass: () => opts.hass || NO_SERVICES,
        apiKey: () => opts.apiKey || '',
        tripCache: () => tripCache,
      }),
      mode: opts.mode || BkkLib.mode(cfg),
      state,
      board: () => board,
      show: (b) => {
        shown.push(b);
        Object.assign(board, { rows: b.rows, banner: b.banner, loading: b.loading, filled: b.filled });
      },
      current: () => own === gen.now,
      sources: src,
    });
  };
  return { board, shown, state, gen, poll, phases: () => shown.map((b) => b.phase) };
}

test('a MÁV board lists ELVIRA’s trains that reach the destination, without FUTÁR', async () => {
  let skipFutar = null;
  const { src, count } = sources({
    timetables: async (_k, _s, _r, _d, o) => { skipFutar = o.skipFutar; return []; },
    elvira: async () => ({ error: '', detail: '', rows: [
      ic('topart', '849', 'TÓPART', 20),
      ic('evening', '871', 'BALATON', 45),
      ic('other', '8312', 'GEMENC', 45, { destHit: false, travel: null }),
    ] }),
  });
  const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS });
  await card.poll(src);
  assert.equal(skipFutar, true);
  assert.equal(ids(card.board.rows), 'elvira:topart,elvira:evening');
  assert.equal(card.board.rows[0].source, 'elvira');
  assert.equal(card.board.banner, '');
  assert.equal(count('transitous'), 0);
  assert.deepEqual(card.phases(), ['listed', 'board', 'gps', 'settled', 'done']);
  assert.ok(card.shown.filter((b) => b.quiet).every((b) => ['listed', 'settled'].includes(b.phase)));
});

test('a MÁV card with a key reads FUTÁR while the integration answers not_loaded, and asks ELVIRA once for it', async () => {
  const skips = [];
  const { src, clock, count } = sources({
    timetables: async (_k, _s, _r, _d, o) => {
      skips.push(o.skipFutar);
      return o.skipFutar ? [] : [rail({ tripId: 'BKK_8494_1', label: 'IC', trainNumber: '849', dep: T0 + 1200, sched: T0 + 1200 })];
    },
    elvira: async () => ({ rows: [], error: 'not_loaded', detail: '' }),
  });
  const card = screen(Object.assign({ mav: true, apiKey: 'k' }, KELENFOLD), { hass: ELVIRA_HASS, apiKey: 'k' });
  await card.poll(src);
  assert.deepEqual(skips, [true, false]);
  assert.equal(count('elvira'), 1);
  assert.equal(ids(card.board.rows), 'BKK_8494_1');
  assert.equal(card.board.banner, '');
  assert.equal(card.state.viaFutarUntil, clock.ms + 3 * 60000);
  /* Within the three minutes the next poll stays on FUTÁR (ELVIRA only
     overlays it); past them the card tries its MÁV board again. */
  clock.ms += 20000;
  await card.poll(src);
  assert.deepEqual(skips, [true, false, false]);
  assert.equal(ids(card.board.rows), 'BKK_8494_1');
  clock.ms += 3 * 60000;
  await card.poll(src);
  assert.deepEqual(skips, [true, false, false, true, false]);
  assert.equal(ids(card.board.rows), 'BKK_8494_1');
});

test('ELVIRA answering again while the MÁV card reads FUTÁR sends it back to its board', async () => {
  const { src } = sources({
    timetables: async () => [rail({ tripId: 'BKK_8494_1', label: 'IC', trainNumber: '849', dep: T0 + 1200, sched: T0 + 1200 })],
  });
  const state = { viaFutarUntil: T0 * 1000 + 60000 };
  const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS, apiKey: 'k', state });
  await card.poll(src);
  assert.equal(state.viaFutarUntil, 0);
});

test('a key-less MÁV card whose elvira_between is briefly gone keeps its trains under the banner, without FUTÁR', async () => {
  let skipFutar = null;
  const { src, count } = sources({ timetables: async (_k, _s, _r, _d, o) => { skipFutar = o.skipFutar; return []; } });
  const card = screen(Object.assign({ mav: true }, KELENFOLD), {
    hass: { states: {}, services: {} },
    board: { rows: [ic('849', '849', 'TÓPART', 20, { source: 'elvira' })] },
  });
  await card.poll(src);
  assert.equal(skipFutar, true);
  assert.equal(count('elvira'), 0);
  assert.equal(ids(card.board.rows), 'elvira:849');
  assert.equal(errKey(card.board.banner), 'errElviraDown');
});

test('a MÁV board ELVIRA did not refresh keeps the trains still due under the banner', async () => {
  const before = [ic('849', '849', 'TÓPART', 20, { source: 'elvira' }), ic('871', '871', 'BALATON', 45, { source: 'elvira' })];
  const answers = {
    down: { rows: [], error: 'elvira_unavailable', detail: 'HTTP 503' },
    limited: { rows: [], error: 'elvira_rate_limited', detail: 'rate limited' },
    dest: { rows: [ic('871', '871', 'BALATON', 45)], error: 'elvira_unavailable', detail: 'Székesfehérvár: elvira_unavailable' },
  };
  for (const [name, answer] of Object.entries(answers)) {
    const { src, count } = sources({ elvira: async () => answer });
    const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS, board: { rows: before.map((r) => Object.assign({}, r)) } });
    await card.poll(src);
    assert.equal(ids(card.board.rows), 'elvira:849,elvira:871', name);
    assert.equal(errKey(card.board.banner), 'errElviraDown', name);
    assert.equal(count('transitous'), 0, name);
  }
  /* A call that failed (the adapter rejected): its reason is the banner's. */
  const { src } = sources({ elvira: async () => { throw { type: 'result', success: false, error: { code: 3, message: 'Connection lost' } }; } });
  const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS, board: { rows: before.map((r) => Object.assign({}, r)) } });
  await card.poll(src);
  assert.equal(ids(card.board.rows), 'elvira:849,elvira:871');
  assert.equal(card.board.banner.i18nArg, 'Connection lost');
  /* Departed trains, and Transitous rows of a filled board, are not kept. */
  const gone = screen(Object.assign({ mav: true }, KELENFOLD), {
    hass: ELVIRA_HASS,
    board: { rows: [ic('800', '800', 'OLD', -30, { source: 'elvira' }), ic('871', '871', 'BALATON', 45, { source: 'elvira' })] },
  });
  await gone.poll(sources({ elvira: async () => answers.down }).src);
  assert.equal(ids(gone.board.rows), 'elvira:871');
  const filled = screen(Object.assign({ mav: true }, KELENFOLD), {
    hass: ELVIRA_HASS, board: { rows: before.map((r) => Object.assign({}, r)), filled: true },
  });
  const fill = sources({
    elvira: async () => answers.down,
    transitous: async () => [rail({ tripId: 'motis:hu-mav_9702', label: '9702', trainNumber: '9702', dep: T0 + 1200, sched: T0 + 1200 })],
  });
  await filled.poll(fill.src);
  assert.equal(ids(filled.board.rows), 'motis:hu-mav_9702');
  assert.equal(fill.count('transitous'), 1);
});

test('the MÁV banner says why: no station code, a failed station search, or ELVIRA down', async () => {
  const cases = [
    [{ error: 'no_code', detail: 'Győr' }, 'errElviraNoCode', 'Győr'],
    [{ error: 'code_search_failed', detail: 'Győrkeresx' }, 'errFutarDown', 'Győrkeresx'],
    [{ error: 'elvira_unavailable', detail: 'HTTP 503' }, 'errElviraDown', 'HTTP 503'],
    [{ error: 'elvira_timeout', detail: '' }, 'errElviraDown', 'elvira_timeout'],
  ];
  for (const [answer, key, arg] of cases) {
    const { src } = sources({ elvira: async () => Object.assign({ rows: [] }, answer) });
    const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS });
    await card.poll(src);
    assert.equal(errKey(card.board.banner), key, answer.error);
    assert.equal(card.board.banner.i18nArg, arg, answer.error);
  }
});

test('a MÁV card whose first ELVIRA answer failed shows the Transitous fill under the banner, filled once', async () => {
  const fillRow = rail({ tripId: 'motis:hu-mav_9702', label: '9702', trainNumber: '9702', dep: T0 + 1200, sched: T0 + 1200 });
  const { src, count } = sources({
    elvira: async () => ({ rows: [], error: 'elvira_unavailable', detail: 'TimeoutError' }),
    transitous: async () => [Object.assign({}, fillRow)],
  });
  const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS, board: { loading: true } });
  await card.poll(src);
  assert.equal(ids(card.board.rows), 'motis:hu-mav_9702');
  assert.equal(errKey(card.board.banner), 'errElviraDown');
  assert.equal(card.board.filled, true);
  assert.equal(card.board.loading, false);
  assert.equal(count('transitous'), 1);
  assert.deepEqual(card.phases(), ['listed', 'gps', 'filled', 'settled', 'done']);
});

test('an empty board is filled from Transitous once; a city board (helyi) is not filled again', async () => {
  const motis = bus('motis:7', 9);
  const bkk = sources({ transitous: async (rows) => ((rows && rows.length) ? rows : [Object.assign({}, motis)]) });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k' });
  await card.poll(bkk.src);
  assert.equal(ids(card.board.rows), 'motis:7');
  assert.equal(card.board.filled, true);
  assert.equal(bkk.count('transitous'), 1);
  const city = sources();
  const helyi = screen(Object.assign({ helyi: true }, KELENFOLD));
  await helyi.poll(city.src);
  assert.equal(city.count('transitous'), 0);
  /* The planner's mode all: one train only ELVIRA confirms is no reason to
     skip the fill that brings the buses beside it. */
  const all = sources({
    elvira: async () => ({ error: '', detail: '', rows: [ic('4011', '4011', 'S36', 10, { end: 'Esztergom', platform: '2' })] }),
    transitous: async () => [bus('motis:bus187', 11), rail({ tripId: 'motis:hu-mav_4011', label: '4011', trainNumber: '4011', dep: T0 + 600, sched: T0 + 600 })],
  });
  const planner = screen(KELENFOLD, { mode: 'all', hass: ELVIRA_HASS });
  await planner.poll(all.src);
  assert.equal(all.count('transitous'), 1);
  assert.equal(ids(planner.board.rows), 'motis:hu-mav_4011,motis:bus187');
  assert.equal(planner.board.rows[0].platform, '2');
});

test('a failed poll keeps the FUTÁR rows still due; an empty, departed or filled board is filled under the banner', async () => {
  const fail = () => sources({
    timetables: async () => { throw new Error('HTTP 503'); },
    transitous: async () => [bus('motis:BAA', 15)],
  });
  const kept = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board: { rows: [bus('BKK_T1', 5)] } });
  const a = fail();
  await kept.poll(a.src);
  assert.equal(ids(kept.board.rows), 'BKK_T1');
  assert.equal(kept.board.banner.message, 'HTTP 503');
  assert.equal(kept.board.filled, false);
  assert.equal(a.count('transitous'), 0);
  for (const board of [{ rows: [] }, { rows: [bus('BKK_T1', -60)] }, { rows: [bus('motis:BAB1', 5)], filled: true }]) {
    const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board });
    const f = fail();
    await card.poll(f.src);
    assert.equal(ids(card.board.rows), 'motis:BAA');
    assert.equal(card.board.filled, true);
    assert.equal(card.board.banner.message, 'HTTP 503');
    assert.equal(f.count('transitous'), 1);
    assert.deepEqual(card.phases(), ['failing', 'failed']);
  }
});

test('the through-check starts after the first rows are painted; its trips get ride time and GPS', async () => {
  const order = [];
  const through = { fire: null };
  const { src, count } = sources({
    timetables: async (_k, _s, _r, _d, o) => {
      o.armThroughCheck(() => {
        order.push('through-check');
        through.fire = () => o.onFutarUpdate([bus('BKK_T1', 2), bus('BKK_T2', 5, { head: 'Óbuda' })]);
      });
      return [bus('BKK_T1', 2)];
    },
    rideTime: async (_k, tripId) => (tripId === 'BKK_T2' ? 15 : 7),
    transitous: async (rows) => { order.push('fill'); return rows; },
  });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k' });
  const done = card.poll(Object.assign({}, src, {
    coachGps: (hass, rows) => { order.push('gps:' + ids(rows)); return src.coachGps(hass, rows); },
  }));
  for (let i = 0; i < 10 && !through.fire; i++) await Promise.resolve();
  /* The sure row is on screen before the check runs; nothing waits on it
     but the fill and the last pass. */
  assert.equal(ids(card.board.rows), 'BKK_T1');
  assert.deepEqual(order, ['gps:BKK_T1', 'through-check']);
  through.fire();
  await done;
  assert.equal(ids(card.board.rows), 'BKK_T1,BKK_T2');
  assert.deepEqual(card.board.rows.map((r) => r.travel), [7, 15]);
  assert.deepEqual(order, ['gps:BKK_T1', 'through-check', 'gps:BKK_T1,BKK_T2']);
  assert.equal(count('transitous'), 0);
  assert.equal(card.phases().at(-1), 'done');
});

test('a poll waiting on the through-check after a failed one shows the spinner, not "no departures"', async () => {
  let fire = null;
  const { src, count } = sources({
    timetables: async (_k, _s, _r, _d, o) => {
      o.armThroughCheck(() => { fire = () => o.onFutarUpdate([bus('BKK_BAE_T1', 10)]); });
      return [];
    },
  });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), {
    apiKey: 'k', board: { rows: [bus('motis:old', 5)], filled: true, banner: new Error('BKK timeout') },
  });
  const done = card.poll(src);
  for (let i = 0; i < 10 && !fire; i++) await Promise.resolve();
  assert.equal(card.phases().at(-1), 'waiting');
  /* An empty list before the check answers is no empty board: no fill. */
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(count('transitous'), 0);
  assert.deepEqual([ids(card.board.rows), card.board.banner, card.board.loading, card.board.filled], ['', '', true, false]);
  fire();
  await done;
  assert.equal(ids(card.board.rows), 'BKK_BAE_T1');
  assert.equal(card.board.loading, false);
  assert.equal(count('transitous'), 0);
});

test('GTFS rows painted before FUTÁR answers keep the last poll’s FUTÁR rows', async () => {
  let release = null;
  const { src } = sources({
    timetables: async (_k, _s, _r, _d, o) => {
      o.onPartial([Object.assign(bus('gtfs:820', 15), { label: '820', vehicle: 'coach', source: 'gtfs' })]);
      await new Promise((r) => { release = r; });
      return [bus('BKK_800', 10, { label: '800' }), Object.assign(bus('gtfs:820', 15), { label: '820', vehicle: 'coach' })];
    },
  });
  const card = screen(Object.assign({ volan: true, apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board: { rows: [bus('BKK_800', 10, { label: '800' })] } });
  const done = card.poll(src);
  for (let i = 0; i < 10 && !release; i++) await Promise.resolve();
  assert.equal(card.phases()[0], 'partial');
  assert.equal(ids(card.board.rows), 'BKK_800,gtfs:820');
  release();
  await done;
  assert.equal(ids(card.board.rows), 'BKK_800,gtfs:820');
});

test('each poll carries the last list’s ride time and coach position over to the new rows', async () => {
  const fix = T0 - 30;
  const before = [
    bus('BKK_AZX_T1', 5, { travel: 12, travelSched: 11 }),
    { tripId: 'gtfs:azx', label: '1300', vehicle: 'coach', rawType: 'BUS', dep: T0 + 400, sched: T0 + 400, travel: 20, lat: 47.2, lon: 18.4, gpsAt: fix },
    { tripId: 'motis:azy', label: '1400', vehicle: 'coach', rawType: 'BUS', dep: T0 + 500, sched: T0 + 500, lat: 47.3, lon: 18.5 },
  ];
  /* The GPS and ride-time lookups have not answered yet when the rows paint. */
  const hold = new Promise(() => {});
  const { src } = sources({
    timetables: async () => [
      bus('BKK_AZX_T1', 5),
      { tripId: 'gtfs:azx', label: '1300', vehicle: 'coach', rawType: 'BUS', dep: T0 + 400, sched: T0 + 400, travel: 20 },
      { tripId: 'motis:azy', label: '1400', vehicle: 'coach', rawType: 'BUS', dep: T0 + 500, sched: T0 + 500 },
    ],
    coachGps: () => hold,
  });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board: { rows: before } });
  card.poll(src);
  for (let i = 0; i < 20 && !card.shown.some((b) => b.phase === 'board'); i++) await Promise.resolve();
  const [t1, coach, motis] = card.board.rows;
  assert.notEqual(t1, before[0]);
  assert.equal(t1.travel, 11);
  assert.deepEqual([coach.lat, coach.lon, coach.gpsCarried, coach.gpsAt], [47.2, 18.4, true, fix]);
  /* A position without its fix time is stamped with the poll's clock. */
  assert.deepEqual([motis.lat, motis.gpsCarried, motis.gpsAt], [47.3, true, T0]);
});

test('a reload since the poll started drops its results and stops its requests', async () => {
  let answer = null;
  const { src, count } = sources({
    elvira: () => new Promise((r) => { answer = r; }),
    transitous: async () => [bus('motis:late', 5)],
  });
  const card = screen(Object.assign({ mav: true }, KELENFOLD), { hass: ELVIRA_HASS });
  const done = card.poll(src);
  for (let i = 0; i < 10 && !card.shown.length; i++) await Promise.resolve();
  assert.deepEqual(card.phases(), ['listed']);
  card.gen.now += 1;
  answer({ rows: [ic('849', '849', 'TÓPART', 20)], error: '', detail: '' });
  await done;
  assert.deepEqual(card.phases(), ['listed']);
  assert.equal(ids(card.board.rows), '');
  assert.equal(count('transitous') + count('coachGps') + count('rideTime'), 0);
  /* A through-check that answers after the reload paints nothing. */
  let fire = null;
  const t = sources({
    timetables: async (_k, _s, _r, _d, o) => {
      o.armThroughCheck(() => { fire = () => o.onFutarUpdate([bus('BKK_T9', 9)]); });
      return [bus('BKK_T1', 2)];
    },
  });
  const other = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k' });
  const run = other.poll(t.src);
  for (let i = 0; i < 10 && !fire; i++) await Promise.resolve();
  const seen = other.shown.length;
  other.gen.now += 1;
  fire();
  await run;
  assert.equal(other.shown.length, seen);
  assert.equal(ids(other.board.rows), 'BKK_T1');
});

test('a place as origin has no board of its own', async () => {
  const { src, count } = sources();
  const card = screen({ stopId: 'geo:47.5,19.0', stopKind: 'place', stopName: 'Itthon', destKey: 'x', destName: 'X' }, { board: { rows: [bus('BKK_T1', 2)], banner: new Error('old') } });
  await card.poll(src);
  assert.deepEqual(card.phases(), ['place']);
  assert.deepEqual([ids(card.board.rows), card.board.banner], ['', '']);
  assert.equal(count('timetables'), 0);
});

/* The ELVIRA source (elviraSource): one per page, shared by every MÁV
   card and the planner. */
function elvira(answer, resolve) {
  const sent = [];
  const warns = [];
  const clock = { ms: T0 * 1000 };
  const source = elviraSource({
    call: async (_hass, data) => {
      sent.push(data);
      const a = typeof answer === 'function' ? answer(data) : answer;
      if (a instanceof Error || (a && a.reject)) throw (a.reject || a);
      return a;
    },
    now: () => clock.ms,
    warn: (...args) => warns.push(args.map(String).join(' ')),
    resolve: resolve || (async () => { throw new Error('every stop here has its station code'); }),
  });
  const ask = (destId, opts) => source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_005501024', destId || 'BKK_005503269', 60, 'Székesfehérvár', opts || {});
  return { source, sent, warns, clock, ask };
}

test('ELVIRA is not asked without the elvira_between service', async () => {
  const e = elvira({ response: { departures: [] } });
  const board = await e.source.board(NO_INDEXES, { states: {}, services: { hungarian_transport: { futar: {} } } }, 'BKK_005501024', 'BKK_005503269', 60, '', {});
  assert.deepEqual([board.error, e.sent.length], ['', 0]);
});

test('a failed ELVIRA call is asked again; three failed poll rounds pause it for minutes and warn once', async () => {
  let fail = true;
  const e = elvira(() => (fail ? new Error('unavailable') : { response: { departures: [{ tripId: 'elvira:1' }] } }));
  const poll = () => { e.clock.ms += 20000; return e.ask(); };
  assert.equal((await poll()).error, 'elvira_call_failed');
  await poll();
  assert.equal(e.sent.length, 2);
  await poll();
  const paused = await poll();
  assert.equal(e.sent.length, 3);
  assert.deepEqual([paused.error, paused.detail], ['elvira_call_failed', 'unavailable']);
  e.clock.ms += 4 * 60000;
  await poll();
  assert.equal(e.sent.length, 4);
  e.clock.ms += 4 * 60000;
  fail = false;
  const back = await poll();
  assert.deepEqual([back.error, back.rows.length, e.warns.length], ['', 1, 1]);
});

test('MÁV cards failing in one moment are one failed round; timeouts are no strike at all', async () => {
  let fail = true;
  const e = elvira(() => (fail ? { reject: { code: 'unknown_error', message: 'Connection lost' } } : { response: { departures: [{ tripId: 'elvira:1' }] } }));
  const pairs = ['BKK_005503269', 'BKK_005510009', 'BKK_005510017'];
  const pollAll = () => Promise.all(pairs.map((d) => e.ask(d)));
  const dropped = await pollAll();
  assert.ok(dropped.every((b) => b.error === 'elvira_call_failed' && b.detail === 'Connection lost'));
  e.clock.ms += 20000;
  fail = false;
  const before = e.sent.length;
  const back = await pollAll();
  assert.equal(e.sent.length - before, 3);
  assert.ok(back.every((b) => b.error === '' && b.rows.length === 1));
  fail = true;
  for (let round = 0; round < 3; round++) {
    e.clock.ms += 20000;
    await pollAll();
  }
  const asked = e.sent.length;
  e.clock.ms += 20000;
  await pollAll();
  assert.equal(e.sent.length, asked, 'paused for every card');
  const timeout = Object.assign(new Error('elvira_between did not answer in time'), { code: 'elvira_timeout' });
  const t = elvira(() => timeout);
  for (let round = 0; round < 4; round++) {
    t.clock.ms += 20000;
    assert.equal((await t.ask()).error, 'elvira_timeout');
  }
  assert.equal(t.sent.length, 4);
});

test('an ELVIRA outage warns once per station pair; rate limits neither warn nor end it', async () => {
  let answer = { response: { departures: [], error: 'elvira_unavailable', detail: 'HTTP 503' } };
  const e = elvira(() => answer);
  const first = await e.ask();
  await e.ask();
  assert.deepEqual([first.rows.length, first.error, first.detail], [0, 'elvira_unavailable', 'HTTP 503']);
  assert.equal(e.warns.length, 1);
  assert.match(e.warns[0], /elvira_unavailable \(HTTP 503\)/);
  assert.equal(e.sent.length, 2, 'an error payload is an answer: no pause');
  answer = { response: { departures: [{ label: 'IC 712' }] } };
  assert.equal((await e.ask()).rows[0].label, 'IC 712');
  answer = { response: { departures: [], error: 'elvira_unavailable', detail: 'TimeoutError' } };
  await e.ask();
  assert.equal(e.warns.length, 2);
  await e.ask('BKK_005517228');
  assert.equal(e.warns.length, 3, 'each pair has its own outage');
  answer = { response: { departures: [], error: 'elvira_rate_limited', detail: 'rate limited' } };
  const limited = await e.ask();
  answer = { response: { departures: [], error: 'elvira_unavailable', detail: 'TimeoutError' } };
  await e.ask();
  assert.deepEqual([limited.error, e.warns.length], ['elvira_rate_limited', 3]);
  answer = { response: { departures: [{ tripId: 'elvira:2' }], dest_error: 'elvira_unavailable' } };
  const dest = await e.ask();
  assert.deepEqual([dest.rows.length, dest.error, dest.detail], [1, 'elvira_unavailable', 'Székesfehérvár: elvira_unavailable']);
});

test('elvira_between gets a planned time as at, and a destination name cut to 100 characters', async () => {
  const e = elvira({ response: { departures: [] } });
  const later = T0 + 10 * 3600;
  await e.ask(null, { at: later });
  await e.ask(null, { at: later - 11 * 3600 });
  await e.ask();
  assert.equal(e.sent[0].at, later);
  assert.ok(!('at' in e.sent[1]) && !('at' in e.sent[2]));
  const longName = 'Székesfehérvár '.repeat(10);
  await e.source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_005501024', 'BKK_005503269', 60, longName, {});
  assert.equal(e.sent[3].dest_name, longName.slice(0, 100));
});

test('a stop without a station code says so; a station search that failed is not that', async () => {
  const codes = { Győr: '', Győrkeresx: null };
  const asked = [];
  const e = elvira({ response: { departures: [] } }, async (ix, _key, _id, name) => {
    asked.push(ix);
    return codes[name];
  });
  const noCode = await e.source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_005501024', '', 60, 'Győr', {});
  const failed = await e.source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_005501024', 'BKK_F0GYRX', 60, 'Győrkeresx', {});
  assert.ok(asked.length === 2 && asked.every((ix) => ix === NO_INDEXES));
  assert.deepEqual([noCode.error, noCode.detail], ['no_code', 'Győr']);
  assert.deepEqual([failed.error, failed.detail], ['code_search_failed', 'Győrkeresx']);
  assert.equal(e.sent.length, 0);
});

/* Issue #121: FUTÁR names some halts with a pole suffix (Erdőkertes is
   BKK_005510579_0, its area BKK_CS005510579_0, both picked by the MÁV
   editor). The code is the nine digits before it: the board asks ELVIRA
   with it, not "no station code". */
test('a FUTÁR station id with a pole suffix still carries its ELVIRA code (Erdőkertes, #121)', async () => {
  const e = elvira({ response: { departures: [] } });
  const fromArea = await e.source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_CS005510579_0', 'BKK_005510009', 60, 'Budapest-Nyugati',
    { originName: 'Erdőkertes' });
  const fromPole = await e.source.board(NO_INDEXES, ELVIRA_HASS, 'BKK_005510579_0', 'BKK_005510009', 60, 'Budapest-Nyugati',
    { originName: 'Erdőkertes' });
  assert.deepEqual([fromArea.error, fromPole.error], ['', '']);
  assert.deepEqual(e.sent.map((d) => [d.origin_code, d.dest_code]),
    [['005510579', '005510009'], ['005510579', '005510009']]);
  assert.equal(BkkLib.elviraStationCode('hu-mav_005501289_4'), '005501289');
  /* Still nine digits exactly, and only behind a station prefix. */
  assert.equal(BkkLib.elviraStationCode('BKK_0055105790_0'), '');
  assert.equal(BkkLib.elviraStationCode('005510579_0'), '');
  assert.equal(BkkLib.elviraStationCode('volan_777770_1'), '');
});

test('an elvira_between call that never answers ends at the card’s own timeout, as elvira_timeout', async () => {
  const hung = { connection: { sendMessagePromise: () => new Promise(() => {}) } };
  const keep = BkkLib._elviraTimeoutMs;
  BkkLib._elviraTimeoutMs = 5;
  try {
    await assert.rejects(BkkLib._elviraCall(hung, {}), (err) => err.code === 'elvira_timeout');
  } finally {
    BkkLib._elviraTimeoutMs = keep;
  }
});

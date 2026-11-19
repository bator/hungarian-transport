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
import './src/lib/departures.js';
import './src/lib/dashboard-key.js';
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

test('an empty board is filled from Transitous once, a city board (helyi) as well', async () => {
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
  assert.equal(city.count('transitous'), 1);
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

test('an empty board is not listed before its Transitous fill answers: no "no departures" flash', async () => {
  let release = null;
  const { src } = sources({
    transitous: async () => { await new Promise((r) => { release = r; }); return [bus('motis:7', 9)]; },
  });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), {
    apiKey: 'k', board: { rows: [bus('motis:7', 9)], filled: true },
  });
  const done = card.poll(src);
  for (let i = 0; i < 20 && !release; i++) await Promise.resolve();
  assert.ok(card.shown.every((b) => b.rows.length), JSON.stringify(card.phases()));
  release();
  await done;
  assert.ok(!card.phases().includes('board'));
  assert.equal(ids(card.board.rows), 'motis:7');
  /* The first load keeps its spinner until the fill answers. */
  let open = null;
  const first = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board: { loading: true } });
  const loading = first.poll(sources({
    transitous: async () => { await new Promise((r) => { open = r; }); return []; },
  }).src);
  for (let i = 0; i < 20 && !open; i++) await Promise.resolve();
  assert.equal(first.board.loading, true);
  open();
  await loading;
  assert.deepEqual([first.board.loading, ids(first.board.rows)], [false, '']);
});

test('a board ELVIRA is not asked for lists its rows once: one GPS and realtime pass', async () => {
  const { src, count } = sources({
    timetables: async () => [bus('BKK_C1', 1), bus('BKK_C2', 5)],
    realtime: async () => true,
  });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k' });
  await card.poll(src);
  assert.deepEqual(card.phases().filter((p) => p === 'board'), ['board']);
  assert.deepEqual([count('coachGps'), count('realtime'), count('sensors'), count('elvira')], [1, 1, 1, 0]);
  assert.equal(ids(card.board.rows), 'BKK_C1,BKK_C2');
});

test('a coach GPS lookup that fails after the poll went stale is no unhandled rejection', async () => {
  const unhandled = [];
  const onUnhandled = (err) => { unhandled.push(err); };
  process.on('unhandledRejection', onUnhandled);
  let answer = null;
  const { src, count } = sources({
    timetables: async () => [bus('BKK_C1', 1)],
    elvira: () => new Promise((r) => { answer = r; }),
    coachGps: async () => { throw new Error('gps broke'); },
  });
  const card = screen(KELENFOLD, { mode: 'all', hass: ELVIRA_HASS });
  const done = card.poll(src);
  for (let i = 0; i < 50 && !count('coachGps'); i++) await Promise.resolve();
  card.gen.now += 1;
  answer({ rows: [], error: '', detail: '' });
  await done;
  await new Promise((r) => setTimeout(r, 10));
  process.off('unhandledRejection', onUnhandled);
  assert.equal(count('coachGps'), 1);
  assert.deepEqual(unhandled, []);
});

test('a Transitous fill that failed keeps the fill-ins still due instead of blanking the board', async () => {
  const { src } = sources({ transitous: async () => null });
  const card = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), {
    apiKey: 'k', board: { rows: [bus('motis:7', 9), bus('motis:gone', -60)], filled: true },
  });
  await card.poll(src);
  assert.equal(ids(card.board.rows), 'motis:7');
  assert.equal(card.board.filled, true);
  /* A board FUTÁR listed is no fill to keep. */
  const own = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k', board: { rows: [bus('BKK_T1', -60)] } });
  await own.poll(sources({ transitous: async () => null }).src);
  assert.deepEqual([ids(own.board.rows), own.board.filled], ['', false]);
  /* A failed poll whose fill failed too shows the banner alone. */
  const failing = screen(Object.assign({ apiKey: 'k' }, KELENFOLD), { apiKey: 'k' });
  await failing.poll(sources({ timetables: async () => { throw new Error('HTTP 503'); }, transitous: async () => null }).src);
  assert.deepEqual([ids(failing.board.rows), failing.board.banner.message], ['', 'HTTP 503']);
});

/* Through the production sources (departures() and the real fill): a
   city board's fill used to happen inside departures(), where a failed
   one became an empty list and the board went blank. */
test('a city board whose Transitous fill failed keeps its fill-ins still due, as every other board', async () => {
  const keep = { cityDepartures: BkkLib.cityDepartures, transitousDepartures: BkkLib.transitousDepartures };
  let asked = 0;
  let answer = [bus('motis:hu-x_12', 10), bus('motis:hu-x_13', 25)];
  BkkLib.cityDepartures = async () => [];
  BkkLib.transitousDepartures = async () => { asked += 1; return answer && answer.map((r) => Object.assign({}, r)); };
  BkkLib._transitousEmptyAt.clear();
  BkkLib._transitousFailed.clear();
  const cfg = {
    helyi: true, city: 'szfv', stopId: 'szfv:1', stopName: 'Kossuth tér', destKey: 'piac', destName: 'Piac', routeIds: [],
  };
  const card = screen(cfg);
  const src = Object.assign(BkkLib.boardSources(BkkLib.cardContext({ cfg: () => cfg, indexes: () => NO_INDEXES })), {
    coachGps: async (_hass, rows) => rows,
    realtime: async () => null,
    sensors: () => {},
  });
  try {
    await card.poll(src);
    const first = [ids(card.board.rows), card.board.filled];
    answer = null;
    /* The next poll after the fill's two minutes. */
    BkkLib._transitousFilled.clear();
    card.gen.now += 1;
    await card.poll(src);
    assert.deepEqual(first, ['motis:hu-x_12,motis:hu-x_13', true]);
    assert.deepEqual([ids(card.board.rows), card.board.filled, asked], ['motis:hu-x_12,motis:hu-x_13', true, 2]);
  } finally {
    Object.assign(BkkLib, keep);
    BkkLib._transitousEmptyAt.clear();
    BkkLib._transitousFailed.clear();
    BkkLib._transitousFilled.clear();
  }
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

/* The Transitous fill (fillTransitousIfEmpty): what it asks the
   volunteer-run service for, and how often. */

/* A clean fill state, Date.now standing at ms, and transitousDepartures
   answering answer() and counting. */
function fillBench(answer) {
  const keep = { transitousDepartures: BkkLib.transitousDepartures, now: Date.now };
  const bench = { asked: 0, ms: T0 * 1000 };
  [BkkLib._transitousEmptyAt, BkkLib._transitousFailed, BkkLib._transitousFilled, BkkLib._transitousFillInflight]
    .forEach((m) => m.clear());
  Date.now = () => bench.ms;
  BkkLib.transitousDepartures = async () => { bench.asked += 1; return answer(); };
  bench.restore = () => {
    BkkLib.transitousDepartures = keep.transitousDepartures;
    Date.now = keep.now;
    [BkkLib._transitousEmptyAt, BkkLib._transitousFailed, BkkLib._transitousFilled, BkkLib._transitousFillInflight]
      .forEach((m) => m.clear());
  };
  return bench;
}
const FILL_FROM = { id: 'szfv:1', name: 'Kossuth tér' };
const FILL_TO = { key: 'piac', name: 'Piac' };
const FILL_OPTS = { mode: 'helyi', minutesAfter: 60 };

test('a fill that found departures serves the polls of the next two minutes without asking again', async () => {
  const bench = fillBench(() => [bus('motis:hu-x_12', 10), bus('motis:hu-x_13', 25)]);
  try {
    const polls = [];
    for (let i = 0; i < 3; i += 1) {
      bench.ms = (T0 + i * 45) * 1000;
      polls.push(await BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS));
    }
    assert.equal(bench.asked, 1);
    assert.deepEqual(polls.map(ids), Array(3).fill('motis:hu-x_12,motis:hu-x_13'));
    /* Each poll has row objects of its own to write ride times onto. */
    assert.notEqual(polls[0][0], polls[1][0]);
    /* After the two minutes it asks again. */
    bench.ms = (T0 + 121) * 1000;
    await BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS);
    assert.equal(bench.asked, 2);
  } finally {
    bench.restore();
  }
});

test('a kept fill whose departures have all left asks again, and another pair is no hit', async () => {
  const bench = fillBench(() => [bus('motis:hu-x_12', -5)]);
  try {
    await BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS);
    await BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS);
    assert.equal(bench.asked, 2);
    await BkkLib.fillTransitousIfEmpty([], FILL_FROM, { key: 'vasutallomas', name: 'Vasútállomás' }, FILL_OPTS);
    assert.equal(bench.asked, 3);
  } finally {
    bench.restore();
  }
});

test('three cards filling the same pair at once share one plan', async () => {
  const bench = fillBench(() => [bus('motis:hu-x_12', 10)]);
  try {
    const got = await Promise.all([1, 2, 3].map(() => BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS)));
    assert.equal(bench.asked, 1);
    assert.deepEqual(got.map(ids), Array(3).fill('motis:hu-x_12'));
    assert.equal(BkkLib._transitousFillInflight.size, 0);
  } finally {
    bench.restore();
  }
});

test('three cards failing at once pause the pair a minute and a half, not six minutes', async () => {
  const bench = fillBench(() => null);
  try {
    const got = await Promise.all([1, 2, 3].map(() => BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS)));
    assert.deepEqual(got, [null, null, null]);
    const key = BkkLib._transitousFillKey(FILL_FROM, FILL_TO, FILL_OPTS);
    assert.equal(BkkLib._transitousFailed.get(key).wait, 90 * 1000);
    /* Failures within one round count once, however they come in; the
       next round's doubles the pause. */
    BkkLib._transitousFillFailed(key);
    BkkLib._transitousFillFailed(key);
    assert.equal(BkkLib._transitousFailed.get(key).wait, 90 * 1000);
    bench.ms += 91 * 1000;
    assert.equal(await BkkLib.fillTransitousIfEmpty([], FILL_FROM, FILL_TO, FILL_OPTS), null);
    assert.equal(BkkLib._transitousFailed.get(key).wait, 180 * 1000);
    assert.equal(bench.asked, 2);
  } finally {
    bench.restore();
  }
});

test('a city board without stop coordinates fills from its GTFS stop’s coordinates and its town', async () => {
  const keep = { cityDepartures: BkkLib.cityDepartures, fillTransitousIfEmpty: BkkLib.fillTransitousIfEmpty };
  const asked = [];
  BkkLib.cityDepartures = async () => [];
  BkkLib.fillTransitousIfEmpty = async (rows, origin, dest) => {
    if (rows.length) return rows;
    asked.push([origin, dest]);
    return [];
  };
  const town = { i: 0, id: 'szfv', name: 'Székesfehérvár — helyi' };
  const stop = { id: 'szfv:1', name: 'Kossuth tér', op: 0, lat: 47.19, lon: 18.41 };
  const cityIx = { ops: [town], stops: [stop], byId: new Map([['szfv:1', stop]]) };
  let loads = 0;
  const indexes = Object.assign({}, NO_INDEXES, { city: async () => { loads += 1; return cityIx; } });
  const cfg = {
    helyi: true, city: 'szfv', stopId: 'szfv:1', stopName: 'Kossuth tér', destKey: 'piac', destName: 'Piac', routeIds: [],
  };
  try {
    const src = BkkLib.boardSources(BkkLib.cardContext({ cfg: () => cfg, indexes: () => indexes }));
    await src.transitous([], { id: 'szfv:1', name: 'Kossuth tér' }, { key: 'piac', name: 'Piac', id: '' }, { mode: 'helyi' });
    const [origin, dest] = asked[0];
    assert.deepEqual([origin.lat, origin.lon, origin.area], [47.19, 18.41, 'Székesfehérvár']);
    /* The destination by name only: no coordinates, but the board's town. */
    assert.deepEqual([dest.lat, dest.area], [undefined, 'Székesfehérvár']);
    /* A board with rows of its own asks nothing. */
    const before = loads;
    const own = [bus('gtfs:1', 5)];
    assert.equal(await src.transitous(own, origin, dest, { mode: 'helyi' }), own);
    assert.equal(loads, before);
    assert.equal(asked.length, 1);
    /* A Volán board's stop names carry their town: passed on as they are. */
    const volan = BkkLib.boardSources(BkkLib.cardContext({ cfg: () => ({ volan: true }), indexes: () => indexes }));
    const bare = { id: 'volan_1', name: 'Győr, autóbusz-állomás' };
    await volan.transitous([], bare, FILL_TO, { mode: 'volan' });
    assert.equal(asked[1][0], bare);
  } finally {
    Object.assign(BkkLib, keep);
  }
});

/* Loading a GTFS index (gtfs.js _loadGtfsIndex): its stops and lookup
   maps are built in slices, the page getting the event loop back in
   between. */

test('an index’s stops are built in slices that hand the event loop back, into the same stops and maps', async () => {
  const names = ['Kossuth tér', 'Győr, autóbusz-állomás', 'Kossuth tér', 'Széchenyi István tér', 'Pécs vasútállomás', ''];
  const raw = {
    v: 1,
    s: Array.from({ length: 60 }, (_v, i) => [`city:${i % 55}`, names[i % names.length], i % 7 ? String(i % 9) : '', i % 3]),
    g: Array.from({ length: 120 }, (_v, i) => (i % 11 ? 4700000 + i : 0)),
    o: [['a', 'A — helyi'], ['b', 'B'], ['c', 'C']],
    t: [],
  };
  const keep = { fetch: BkkLib._fetchIndexBytes, slice: BkkLib._sliceMs, next: BkkLib._nextTask };
  let yields = 0;
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(JSON.stringify(raw)) });
  try {
    BkkLib._sliceMs = Infinity;
    BkkLib._nextTask = async () => { yields += 1; };
    const whole = await BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
    assert.equal(yields, 0);
    BkkLib._sliceMs = 0;
    const sliced = await BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
    assert.equal(yields, raw.s.length - 1);
    assert.deepEqual(sliced.stops, whole.stops);
    for (const m of ['byNum', 'byKey', 'byId']) {
      assert.deepEqual([...sliced[m].keys()], [...whole[m].keys()]);
      assert.deepEqual([...sliced[m].values()].map((st) => st.i), [...whole[m].values()].map((st) => st.i));
    }
    /* Each stop as the index always had it: its name's norm() and fold(). */
    whole.stops.forEach((st, i) => {
      const g = raw.g[2 * i] || raw.g[2 * i + 1];
      assert.deepEqual(st, {
        i, id: raw.s[i][0], name: raw.s[i][1], num: raw.s[i][2], op: raw.s[i][3],
        key: BkkLib.norm(raw.s[i][1]), fold: BkkLib.fold(raw.s[i][1]),
        lat: g ? raw.g[2 * i] / 1e5 : null, lon: g ? raw.g[2 * i + 1] / 1e5 : null,
      });
    });
    assert.equal(whole.byId.get('city:3'), whole.stops[3]);
    assert.equal(whole.s, null);
    assert.equal(whole.g, null);
  } finally {
    Object.assign(BkkLib, { _fetchIndexBytes: keep.fetch, _sliceMs: keep.slice, _nextTask: keep.next });
  }
});

/* A feed that renumbered its stops (Szeged's new source, November 2026):
   the index's "al" maps each old id to the stop that took its place, so a
   card saved with the old id and the old name still finds its stop. */
test('a card saved with a renumbered stop’s old id finds the new stop by the index’s aliases, of its own operator only', async () => {
  const raw = {
    v: 29,
    s: [['szeged:SZ_1', 'Anna-kút', '62367', 0], ['szeged:SZ_2', 'Anna-kút', '62368', 0],
      ['szeged:SZ_3', 'Széchenyi tér', '62400', 0], ['pecs:1', 'Széchenyi tér', '1', 1]],
    g: [4625000, 2014000, 4625010, 2014000, 4625300, 2014000, 4607000, 1823000],
    o: [['szeged', 'Szeged — SZKT'], ['pecs', 'Pécs — Tüke Busz']],
    al: [['szeged:2430584836', 0], ['szeged:SZ_3', 0], ['szeged:999', 9], ['pecs:old', 3]],
    t: [],
  };
  const keep = BkkLib._fetchIndexBytes;
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(JSON.stringify(raw)) });
  try {
    const idx = await BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
    const saved = { id: 'szeged:2430584836', name: 'Anna-kút (Kossuth Lajos sugárút)' };
    assert.equal(BkkLib.gtfsStopById(idx, saved.id), idx.stops[0]);
    assert.equal(BkkLib.cityOpId(idx, saved), 'szeged');
    assert.equal(BkkLib.volanMatchStop(idx, saved, 0), idx.stops[0]);
    /* The stop's other poles of the same name come with it, as for a stop
       picked afresh. */
    assert.deepEqual(BkkLib.cityStopIndexes(idx, saved, 0), [0, 1]);
    /* An alias leads to its own operator's stop only. */
    assert.equal(BkkLib.volanMatchStop(idx, { id: 'pecs:old', name: '' }, 0), null);
    assert.equal(BkkLib.volanMatchStop(idx, { id: 'pecs:old', name: '' }, 1), idx.stops[3]);
    /* An id the index still has is that stop, never an alias; one naming
       no stop is dropped. */
    assert.equal(BkkLib.gtfsStopById(idx, 'szeged:SZ_3'), idx.stops[2]);
    assert.equal(BkkLib.gtfsStopById(idx, 'szeged:999'), null);
    assert.deepEqual([...idx.byAlias.keys()], ['szeged:2430584836', 'pecs:old']);
    assert.equal(idx.al, null);
    /* Without the table, the old name of the stop finds nothing: the
       bracket the old feed had is not part of the new name. */
    const bare = Object.assign({}, idx, { byAlias: new Map() });
    assert.equal(BkkLib.volanMatchStop(bare, saved, 0), null);
  } finally {
    BkkLib._fetchIndexBytes = keep;
  }
});

/* Szeged's buses left the city feed (November 2026): a local-bus card
   saved with a stop the index no longer has finds no stop rather than one
   whose name merely contains the saved name. */
test('a saved city stop the index dropped matches its own name only, not a stop whose name contains it', async () => {
  const raw = {
    v: 29,
    s: [['szeged:2429618700', 'Körtöltés utca', '2429618700', 0], ['szeged:2429618701', 'Rókusi templom', '2429618701', 0],
      ['szeged:2429618702', 'Anna-kút', '2429618702', 0], ['pecs:1', 'Töltés utca', '1', 1]],
    g: [4626000, 2013000, 4625700, 2013900, 4625000, 2014000, 4607000, 1823000],
    o: [['szeged', 'Szeged — SZKT'], ['pecs', 'Pécs — Tüke Busz']],
    t: [],
  };
  const keep = BkkLib._fetchIndexBytes;
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(JSON.stringify(raw)) });
  try {
    const idx = await BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260102', 'errCityHttp', 'errCityGunzip');
    const gone = { id: 'szeged:2429103599', name: 'Töltés utca' };
    assert.equal(BkkLib.gtfsStopById(idx, gone.id), null);
    assert.equal(BkkLib.volanMatchStop(idx, gone, 0), null);
    assert.deepEqual(BkkLib.cityStopIndexes(idx, gone, 0), []);
    assert.equal(BkkLib.volanMatchStop(idx, { id: 'szeged:2429103600', name: 'Rókus vasútállomás' }, 0), null);
    /* Its own name, exactly, is still that stop. */
    assert.equal(BkkLib.volanMatchStop(idx, { id: 'szeged:2429103601', name: 'Anna-kút' }, 0), idx.stops[2]);
    /* A stop saved by name alone still matches part of a name, and an id
       of another operator is not this one's. */
    assert.equal(BkkLib.volanMatchStop(idx, { id: '', name: 'Töltés utca' }, 0), idx.stops[0]);
    assert.equal(BkkLib.volanMatchStop(idx, { id: 'pecs:9', name: 'Töltés utca' }, 0), idx.stops[0]);
    /* The Volán index (no operator) keeps matching a name in part. */
    assert.equal(BkkLib.volanMatchStop(idx, { id: gone.id, name: 'Körtöltés' }, null), idx.stops[0]);
  } finally {
    BkkLib._fetchIndexBytes = keep;
  }
});

test('the event loop is handed back by scheduler.yield, else a message to the page, else a timer', async () => {
  const keep = { scheduler: globalThis.scheduler, MessageChannel: globalThis.MessageChannel };
  try {
    let yielded = 0;
    globalThis.scheduler = { yield() { yielded += 1; return Promise.resolve(); } };
    await BkkLib._nextTask();
    assert.equal(yielded, 1);
    globalThis.scheduler = undefined;
    let ports = 0;
    globalThis.MessageChannel = class extends keep.MessageChannel {
      constructor() { super(); ports += 1; }
    };
    await BkkLib._nextTask();
    assert.equal(ports, 1);
    globalThis.MessageChannel = undefined;
    let timer = false;
    setTimeout(() => { timer = true; }, 0);
    await BkkLib._nextTask();
    assert.ok(timer);
  } finally {
    globalThis.scheduler = keep.scheduler;
    globalThis.MessageChannel = keep.MessageChannel;
  }
});

/* A stand-in for the browser's Worker: it runs the script behind the Blob
   URL it is given, on its own scope, its messages copied as postMessage
   copies them and delivered each in a task of its own. */
async function blobText(url) {
  const { resolveObjectURL } = await import('node:buffer');
  return resolveObjectURL(url).text();
}
function fakeWorkers(over) {
  const made = [];
  class FakeWorker {
    constructor(url) {
      if (over && over.refuse) throw new Error('SecurityError');
      this.url = url;
      this.terminated = false;
      this.delivered = 0;
      made.push(this);
      const worker = this;
      this.scope = {
        postMessage(msg) {
          const copy = structuredClone(msg);
          setTimeout(() => {
            if (worker.terminated || !worker.onmessage) return;
            worker.delivered += 1;
            worker.onmessage({ data: copy });
          }, 0);
        },
      };
      this.ready = blobText(url).then((code) => { new Function('self', code)(this.scope); });
    }
    postMessage(msg) {
      if (over && over.silent) return;
      const copy = structuredClone(msg);
      const run = () => this.ready.then(() => this.scope.onmessage({ data: copy }));
      if (over && over.late) over.late.then(run);
      else run();
    }
    terminate() { this.terminated = true; }
  }
  return { FakeWorker, made };
}

test('an index is parsed in a worker when the page can start one, and comes back the same, in slices', async () => {
  const { gzipSync } = await import('node:zlib');
  const raw = {
    v: 1,
    feed: 'test',
    s: Array.from({ length: 30 }, (_v, i) => [`city:${i}`, `Megálló ${i % 4}`, String(i), i % 2]),
    g: Array.from({ length: 60 }, (_v, i) => 4700000 + i),
    o: [['a', 'A — helyi'], ['b', 'B']],
    t: Array.from({ length: 25 }, (_v, i) => [i, 'x', [0, 1], [0, 5]]),
  };
  const bytes = new Uint8Array(gzipSync(JSON.stringify(raw)));
  const keep = {
    fetch: BkkLib._fetchIndexBytes, slice: BkkLib._indexWorkerSlice, Worker: globalThis.Worker,
    broken: BkkLib._indexWorkerBroken, url: BkkLib._indexWorkerUrl,
  };
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes });
  const load = () => BkkLib._loadGtfsIndex('http://x/city-index.json.gz?v=20260101', 'errCityHttp', 'errCityGunzip');
  try {
    BkkLib._indexWorkerBroken = false;
    BkkLib._indexWorkerUrl = '';
    /* No Worker (node, an older browser): parsed on the page. */
    globalThis.Worker = undefined;
    const page = await load();
    const workers = fakeWorkers();
    globalThis.Worker = workers.FakeWorker;
    BkkLib._indexWorkerSlice = 10;
    const viaWorker = await load();
    assert.equal(workers.made.length, 1);
    assert.ok(workers.made[0].terminated);
    /* head, then 30 stops, 60 coordinates and 25 trips in slices of 10, then done. */
    assert.equal(workers.made[0].delivered, 1 + 3 + 6 + 3 + 1);
    assert.deepEqual(Object.keys(viaWorker), Object.keys(page));
    assert.deepEqual(viaWorker.t, raw.t);
    assert.deepEqual(viaWorker.stops, page.stops);
    assert.deepEqual([...viaWorker.byId.keys()], [...page.byId.keys()]);
    assert.equal(BkkLib._indexWorkerBroken, false);
  } finally {
    Object.assign(BkkLib, {
      _fetchIndexBytes: keep.fetch, _indexWorkerSlice: keep.slice, _indexWorkerBroken: keep.broken, _indexWorkerUrl: keep.url,
    });
    globalThis.Worker = keep.Worker;
  }
});

test('a file the worker cannot read keeps its error; a worker that cannot start is not asked again', async () => {
  const keep = {
    fetch: BkkLib._fetchIndexBytes, Worker: globalThis.Worker, broken: BkkLib._indexWorkerBroken,
    url: BkkLib._indexWorkerUrl, timeout: BkkLib._indexWorkerTimeoutMs,
  };
  let body = '<html>502 Bad Gateway</html>';
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(body) });
  const load = () => BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
  try {
    BkkLib._indexWorkerBroken = false;
    BkkLib._indexWorkerUrl = '';
    /* An HTML page: the worker gives up on it, and the page's own parse
       names the error as always. */
    const reading = fakeWorkers();
    globalThis.Worker = reading.FakeWorker;
    await assert.rejects(load(), (err) => err.i18nKey === 'errIndexParse');
    assert.equal(reading.made.length, 1);
    assert.equal(BkkLib._indexWorkerBroken, false);
    body = JSON.stringify({ v: 1, s: [['city:1', 'A']], t: [] });
    /* A policy that refuses the worker: parsed on the page, and no worker
       is started again. */
    const refused = fakeWorkers({ refuse: true });
    globalThis.Worker = refused.FakeWorker;
    assert.equal((await load()).stops.length, 1);
    assert.equal(BkkLib._indexWorkerBroken, true);
    const later = fakeWorkers();
    globalThis.Worker = later.FakeWorker;
    assert.equal((await load()).stops.length, 1);
    assert.equal(later.made.length, 0);
  } finally {
    Object.assign(BkkLib, {
      _fetchIndexBytes: keep.fetch, _indexWorkerBroken: keep.broken, _indexWorkerUrl: keep.url, _indexWorkerTimeoutMs: keep.timeout,
    });
    globalThis.Worker = keep.Worker;
  }
});

test('a worker past its time is asked again next load; the page reads alongside it and the first answer wins', async () => {
  const keep = {
    fetch: BkkLib._fetchIndexBytes, Worker: globalThis.Worker, broken: BkkLib._indexWorkerBroken,
    url: BkkLib._indexWorkerUrl, timeout: BkkLib._indexWorkerTimeoutMs, text: BkkLib._indexText,
  };
  const body = JSON.stringify({ v: 1, s: [['city:1', 'A']], t: [] });
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(body) });
  const load = () => BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
  try {
    BkkLib._indexWorkerBroken = false;
    BkkLib._indexWorkerUrl = '';
    BkkLib._indexWorkerTimeoutMs = 20;
    /* A worker that does not answer in time: the page's parse is used, the
       worker stopped, and workers stay in use. */
    const silent = fakeWorkers({ silent: true });
    globalThis.Worker = silent.FakeWorker;
    assert.equal((await load()).stops[0].name, 'A');
    assert.ok(silent.made[0].terminated);
    assert.equal(BkkLib._indexWorkerBroken, false);
    /* The next load asks a worker again; this one answers late, while the
       page is still reading: its index is the one used, and the page's
       own text is never parsed. */
    let release;
    const late = new Promise((r) => { release = r; });
    const slowPage = fakeWorkers({ late });
    globalThis.Worker = slowPage.FakeWorker;
    let pageRead;
    const pageDone = new Promise((r) => { pageRead = r; });
    BkkLib._indexText = async () => {
      release();
      await new Promise((r) => setTimeout(r, 80));
      pageRead();
      return JSON.stringify({ v: 1, s: [['city:1', 'from the page']], t: [] });
    };
    const realParse = JSON.parse;
    let pageParsed = 0;
    JSON.parse = (text, ...rest) => {
      if (String(text).indexOf('from the page') >= 0) pageParsed += 1;
      return realParse(text, ...rest);
    };
    try {
      const idx = await load();
      assert.equal(slowPage.made.length, 1);
      assert.equal(idx.stops[0].name, 'A');
      assert.ok(slowPage.made[0].terminated);
      await pageDone;
      await new Promise((r) => setTimeout(r, 0));
      assert.equal(pageParsed, 0);
    } finally {
      JSON.parse = realParse;
    }
    assert.equal(BkkLib._indexWorkerBroken, false);
  } finally {
    Object.assign(BkkLib, {
      _fetchIndexBytes: keep.fetch, _indexWorkerBroken: keep.broken, _indexWorkerUrl: keep.url,
      _indexWorkerTimeoutMs: keep.timeout, _indexText: keep.text,
    });
    globalThis.Worker = keep.Worker;
  }
});

/* A worker program that sends the head of an index and two slices of
   its stops, then nothing more for now: one still busy on a slow tablet. */
function stallingIndexWorker(scope) {
  scope.onmessage = () => {
    scope.postMessage({ head: { v: 1, s: null, t: [] }, long: { s: 200 } });
    scope.postMessage({ key: 's', rows: [['city:0', 'from the worker']] });
    scope.postMessage({ key: 's', rows: [['city:1', 'from the worker']] });
  };
}

test('a worker still sending slices is stopped before the page parses the index, not after', async () => {
  const keep = {
    fetch: BkkLib._fetchIndexBytes, Worker: globalThis.Worker, broken: BkkLib._indexWorkerBroken,
    url: BkkLib._indexWorkerUrl, timeout: BkkLib._indexWorkerTimeoutMs, text: BkkLib._indexText,
    main: BkkLib._indexWorkerMain,
  };
  const raw = {
    v: 1,
    s: Array.from({ length: 200 }, (_v, i) => [`city:${i}`, `Megálló ${i}`]),
    t: Array.from({ length: 200 }, (_v, i) => [i, 'x', [0, 1], [0, 5]]),
  };
  BkkLib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(JSON.stringify(raw)) });
  const load = () => BkkLib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
  const realParse = JSON.parse;
  try {
    BkkLib._indexWorkerBroken = false;
    BkkLib._indexWorkerUrl = '';
    BkkLib._indexWorkerTimeoutMs = 20;
    BkkLib._indexWorkerMain = stallingIndexWorker;
    /* The worker starts past its time, as the page starts reading; the
       page finishes gunzipping while the worker is a few slices in. */
    let release;
    const late = new Promise((r) => { release = r; });
    const workers = fakeWorkers({ late });
    globalThis.Worker = workers.FakeWorker;
    const pageText = JSON.stringify({ ...raw, s: [['city:0', 'from the page'], ...raw.s.slice(1)] });
    BkkLib._indexText = async () => {
      release();
      while (workers.made[0].delivered < 3) await new Promise((r) => setTimeout(r, 0));
      return pageText;
    };
    let atParse = null;
    JSON.parse = (text, ...rest) => {
      if (text === pageText) {
        const w = workers.made[0];
        atParse = { terminated: w.terminated, delivered: w.delivered };
      }
      return realParse(text, ...rest);
    };
    const idx = await load();
    JSON.parse = realParse;
    await new Promise((r) => setTimeout(r, 20));
    assert.ok(atParse, 'the page parsed its own text');
    assert.equal(atParse.delivered, 3);
    assert.equal(atParse.terminated, true);
    assert.equal(workers.made[0].delivered, atParse.delivered);
    assert.equal(idx.stops[0].name, 'from the page');
    assert.equal(idx.stops.length, 200);
    assert.equal(BkkLib._indexWorkerBroken, false);
  } finally {
    JSON.parse = realParse;
    Object.assign(BkkLib, {
      _fetchIndexBytes: keep.fetch, _indexWorkerBroken: keep.broken, _indexWorkerUrl: keep.url,
      _indexWorkerTimeoutMs: keep.timeout, _indexText: keep.text, _indexWorkerMain: keep.main,
    });
    globalThis.Worker = keep.Worker;
  }
});

test('stopping an index worker tells whether it had finished with the index', async () => {
  const keep = { Worker: globalThis.Worker, broken: BkkLib._indexWorkerBroken, url: BkkLib._indexWorkerUrl, main: BkkLib._indexWorkerMain };
  const raw = { v: 1, s: Array.from({ length: 50 }, (_v, i) => [`city:${i}`, 'A']), t: [] };
  const bytes = new TextEncoder().encode(JSON.stringify(raw));
  try {
    BkkLib._indexWorkerBroken = false;
    BkkLib._indexWorkerUrl = '';
    BkkLib._indexWorkerMain = stallingIndexWorker;
    /* Stopped a few slices in: no index, and the worker had not won. */
    const midway = fakeWorkers();
    globalThis.Worker = midway.FakeWorker;
    const job = BkkLib._startIndexWorker(bytes);
    while (midway.made[0].delivered < 3) await new Promise((r) => setTimeout(r, 0));
    assert.equal(job.stop(), false);
    assert.equal(await job.done, null);
    assert.equal(midway.made[0].terminated, true);
    assert.equal(midway.made[0].onmessage, null);
    /* Stopped after its answer: stop() says the worker has the index. */
    BkkLib._indexWorkerMain = keep.main;
    BkkLib._indexWorkerUrl = '';
    const whole = fakeWorkers();
    globalThis.Worker = whole.FakeWorker;
    const job2 = BkkLib._startIndexWorker(bytes);
    const idx = await job2.done;
    assert.equal(idx.s.length, 50);
    assert.equal(job2.stop(), true);
  } finally {
    Object.assign(BkkLib, { _indexWorkerBroken: keep.broken, _indexWorkerUrl: keep.url, _indexWorkerMain: keep.main });
    globalThis.Worker = keep.Worker;
  }
});

/* What Transitous answered for a bare "Vasútállomás" (2026-11): ten stops,
   none in Győr, Eger or Pécs; the bare name is listed in Kecskemét first. */
const VASUTALLOMAS = [
  ['Szob, Vasútállomás', 'de-DELFI_000005500006', 47.821, 18.862057, 'Szob'],
  ['Dorog, vasútállomás', 'hu-volan-helykozi_hkir:hkir_669153_11', 47.723915, 18.73174, 'Dorog'],
  ['Vasútállomás', 'hu-Kecskemét_SA2536', 46.913128, 19.700514, 'Kecskemét'],
  ['Vasútállomás', 'hu-Tatabánya_SA8987', 47.5826, 18.3965, 'Tatabánya'],
  ['Vasútállomás', 'hu-Várpalota_26', 47.1878, 18.1391, 'Várpalota'],
  ['Vasútállomás', 'hu-blaguss-agora_51150', 47.2381, 16.6276, 'Szombathely'],
  ['Vasútállomás', 'hu-kaposvár_632312', 46.3553, 17.7887, 'Kaposvár'],
  ['Vasútállomás', 'hu-kiskunhalas_KHL_M_37', 46.4321, 19.4776, 'Kiskunhalas'],
  ['Sárvár, vasútállomás', 'hu-volan-helykozi_hkir:hkir_553625', 47.2514, 16.9318, 'Sárvár'],
  ['Monor, vasútállomás', 'hu-volan-helykozi_hkir:hkir_778298', 47.3488, 19.4442, 'Monor'],
].map(([name, id, lat, lon, town]) => ({
  type: 'STOP', name, id, lat, lon, country: 'HU',
  areas: [{ name: 'Magyarország', adminLevel: 2 }, { name: town, adminLevel: 8 }],
}));
const GYOR_STATION = {
  type: 'STOP', name: 'Vasútállomás', id: 'hu-gyor_120', lat: 47.6838, lon: 17.6340, country: 'HU',
  areas: [{ name: 'Magyarország', adminLevel: 2 }, { name: 'Győr', adminLevel: 8 }],
};

async function placing(answers, run) {
  const keep = BkkLib.transitousGeocode;
  const asked = [];
  BkkLib.transitousGeocode = async (q) => {
    asked.push(q);
    const a = answers(q);
    if (a instanceof Error) throw a;
    return a;
  };
  try {
    return await run(asked);
  } finally {
    BkkLib.transitousGeocode = keep;
  }
}

test('a name-only stop is geocoded with its town first: a Győr "Vasútállomás" is not Kecskemét’s', async () => {
  const stop = { id: '', name: 'Vasútállomás', key: 'vasutallomas', area: 'Győr' };
  await placing((q) => (q === 'Vasútállomás Győr' ? [GYOR_STATION, ...VASUTALLOMAS] : VASUTALLOMAS), async (asked) => {
    assert.equal(await BkkLib.transitousPlace(stop), '47.6838,17.634');
    assert.deepEqual(asked, ['Vasútállomás Győr']);
  });
  /* A town answer with nothing in the town: the bare name is asked, and a
     stop in the town there is taken. */
  await placing((q) => (q === 'Vasútállomás' ? [...VASUTALLOMAS, GYOR_STATION] : VASUTALLOMAS), async (asked) => {
    assert.equal(await BkkLib.transitousPlace(stop), '47.6838,17.634');
    assert.deepEqual(asked, ['Vasútállomás Győr', 'Vasútállomás']);
  });
  /* A name that already names its town is asked as it is. */
  await placing(() => [GYOR_STATION], async (asked) => {
    await BkkLib.transitousPlace({ name: 'Győr, vasútállomás', area: 'Győr' });
    assert.deepEqual(asked, ['Győr, vasútállomás']);
  });
  /* Szombathely is in the bare answer: its town query finds it too. */
  await placing(() => VASUTALLOMAS, async () => {
    assert.equal(await BkkLib.transitousPlace({ name: 'Vasútállomás', area: 'Szombathely' }), '47.2381,16.6276');
  });
});

test('a failed town query is no stop in another town: with nothing in the town the place rejects', async () => {
  const down = new Error('HTTP 503');
  const stop = { name: 'Vasútállomás', area: 'Eger' };
  await placing((q) => (q === 'Vasútállomás Eger' ? down : VASUTALLOMAS), async () => {
    await assert.rejects(BkkLib.transitousPlace(stop), (err) => err === down);
  });
  /* The bare answer has the town's stop: that is good enough. */
  const eger = Object.assign({}, GYOR_STATION, { id: 'hu-eger_1', lat: 47.8892, lon: 20.3733, areas: [{ name: 'Eger', adminLevel: 8 }] });
  await placing((q) => (q === 'Vasútállomás Eger' ? down : [...VASUTALLOMAS, eger]), async () => {
    assert.equal(await BkkLib.transitousPlace(stop), '47.8892,20.3733');
  });
  /* Both failed: the outage still rejects, as before. */
  await placing(() => down, async () => {
    await assert.rejects(BkkLib.transitousPlace(stop));
  });
});

test('a feed area that is no town (Bahart’s Balaton) is not put in the query, and the bare answer stands', async () => {
  const siofok = {
    type: 'STOP', name: 'Siófok', id: 'hu-bahart_1', lat: 46.9077, lon: 18.0496,
    areas: [{ name: 'Siófok', adminLevel: 8 }],
  };
  const port = { id: 'bahart:SIO', name: 'Siófok', area: 'Balaton' };
  /* The town query would fail: it is not asked, and the bare name's stop
     is the answer, not a "try again". */
  await placing((q) => (q === 'Siófok' ? [siofok] : new Error('HTTP 503')), async (asked) => {
    assert.equal(await BkkLib.transitousPlace(port), '46.9077,18.0496');
    assert.deepEqual(asked, ['Siófok']);
  });
  await placing((q) => (q === 'Siófok' ? [siofok] : new Error('HTTP 503')), async (asked) => {
    assert.equal((await BkkLib.transitousStopRef(port, 'ferry')).lat, 46.9077);
    assert.deepEqual(asked, ['Siófok']);
  });
  /* A feed whose id names its town keeps the town-first query. */
  for (const stop of [
    { id: 'kecskemet-kisvasut:1', name: 'Vasútállomás', area: 'Kecskemét' },
    { id: 'volan-eger:5', name: 'Vasútállomás', area: 'Eger' },
    { id: 'hodmezovasarhely:9', name: 'Vasútállomás', area: 'Hódmezővásárhely' },
  ]) {
    await placing(() => VASUTALLOMAS, async (asked) => {
      await BkkLib.transitousPlace(stop);
      assert.equal(asked[0], `Vasútállomás ${stop.area}`);
    });
  }
  assert.equal(BkkLib.transitousTown({ id: 'volan-helyi:3', area: 'Volán helyi' }), '');
  assert.equal(BkkLib.transitousTown({ id: 'budapest-siklo:1', area: 'Budapest' }), 'Budapest');
  assert.equal(BkkLib.transitousTown({ id: 'hkir_123', area: 'Tata' }), 'Tata');
});

test('an apiKey of only spaces is no key and does not hide a sibling card’s real one', () => {
  const dash = {
    views: [{
      cards: [
        { type: 'custom:hungarian-transport-card', apiKey: '   ' },
        { type: 'custom:hungarian-transport-card', apiKey: 'real-key' },
        { type: 'custom:someone-else', apiKey: 'not-ours' },
      ],
    }],
  };
  assert.deepEqual(BkkLib.findApiKey(dash), ['real-key']);
  assert.deepEqual(BkkLib.findApiKey({ type: 'hungarian-transport-card', apiKey: '\t\n' }), []);
});

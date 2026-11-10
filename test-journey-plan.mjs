/* The journey plan (src/lib/journey-plan.js), straight from src/: one stop
   pair on a fake planner and a clock the test moves, no card, DOM or Home
   Assistant. The planner's answers wait until the test releases them. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BkkLib } from './src/lib/core.js';
import './src/lib/futar.js';
import './src/lib/journey.js';
import { generation } from './src/lib/generation.js';
import { NOW_PLAN_STALE_MS, journeyPlan } from './src/lib/journey-plan.js';

const T0 = Date.UTC(2026, 10, 10, 7, 0);
const PAIR = {
  origin: { id: 'BKK_A', name: 'Alpha', lat: 47.5, lon: 19.0 },
  dest: { id: 'BKK_B', name: 'Beta', lat: 47.6, lon: 19.1 },
  horizon: 60,
};
const SWAPPED = { origin: PAIR.dest, dest: PAIR.origin, horizon: 60 };
const DEPART = { mode: 'depart', at: T0 + 3600e3 };
const ride = (label) => ({ durationMin: 20, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label }] });
const settle = () => new Promise((r) => setTimeout(r, 0));

/* A plan on a fake planner: every call is logged and answered by the test
   (answer(i, value) or fail(i)), shapes resolve at once. */
function setup() {
  const clock = { now: T0 };
  const calls = [];
  const changes = [];
  const pending = (kind, args) => new Promise((resolve, reject) => {
    calls.push({ kind, args, resolve, reject });
  });
  const shaped = [];
  const plan = journeyPlan({
    planner: {
      now: (pair) => pending('now', [pair]),
      at: (pair, when) => pending('at', [pair, when]),
      shapes: async (journey) => { shaped.push(journey); },
    },
    now: () => clock.now,
    changed: (part) => changes.push(part),
  });
  const answer = async (i, value) => { calls[i].resolve(value); await settle(); };
  const fail = async (i) => { calls[i].reject(new Error('down')); await settle(); };
  return { plan, clock, calls, changes, shaped, answer, fail };
}

test('generation: a bump makes every earlier token stale, the current one is not', () => {
  const g = generation();
  const first = g.token();
  assert.equal(g.stale(first), false);
  const second = g.bump();
  assert.equal(g.stale(first), true);
  assert.equal(g.stale(second), false);
  assert.equal(g.token(), second);
});

test('now: a reload plans once per pair and horizon; the answer lands with its shapes', async () => {
  const { plan, calls, changes, shaped, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  plan.consider(PAIR);
  assert.equal(calls.length, 1, 'a plan already running is not asked for again');
  assert.deepEqual(calls[0].args, [PAIR]);
  assert.equal(plan.loading, true);
  /* A new look-ahead is another plan, even while one runs. */
  plan.consider(Object.assign({}, PAIR, { horizon: 240 }));
  assert.equal(calls.length, 2);
  const j = ride('7');
  await answer(1, j);
  assert.equal(plan.shown, j);
  assert.equal(plan.loading, false);
  assert.deepEqual(shaped, [j]);
  assert.deepEqual(changes, ['card', 'journey']);
  /* Planned and on screen: nothing to ask until the next reload. */
  plan.consider(Object.assign({}, PAIR, { horizon: 240 }));
  assert.equal(calls.length, 2);
});

test('now: a stop pair is required', () => {
  const { plan, calls } = setup();
  plan.consider({ origin: { id: '' }, dest: { id: 'B', name: 'Beta' }, horizon: 60 });
  plan.consider({ origin: { id: 'A' }, dest: { id: 'B', name: '' }, horizon: 60 });
  assert.equal(calls.length, 0);
  assert.equal(plan.loading, false);
});

test('now: the plan goes stale after five minutes and asks for a reload', async () => {
  const { plan, clock, calls, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  await answer(0, ride('7'));
  assert.equal(plan.stale, false);
  assert.equal(plan.wantsReload(false), false, 'a plan on screen is no reason to reload');
  clock.now += NOW_PLAN_STALE_MS;
  assert.equal(plan.stale, false);
  clock.now += 1;
  assert.equal(plan.stale, true);
  assert.equal(plan.wantsReload(true), true);
  /* The reload plans the same pair again. */
  plan.begin();
  assert.equal(plan.stale, false);
  plan.consider(PAIR);
  assert.equal(calls.length, 2);
});

test('now: the plan on screen stays while the same pair plans again, until the new one lands', async () => {
  const { plan, calls, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  const first = ride('FIRST');
  await answer(0, first);
  plan.begin();
  assert.equal(plan.shown, first, 'held over by the reload');
  plan.consider(PAIR);
  assert.equal(calls.length, 2, 'one plan per reload');
  assert.equal(plan.shown, first, 'held over while the new plan runs');
  assert.equal(plan.loading, true);
  const second = ride('SECOND');
  await answer(1, second);
  assert.equal(plan.shown, second);
  /* A replan that finds no route leaves nothing: the held plan is old. */
  plan.begin();
  plan.consider(PAIR);
  await answer(2, null);
  assert.equal(plan.shown, null);
});

test('now: a failed replan drops the held plan and stops loading', async () => {
  const { plan, fail, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  await answer(0, ride('7'));
  plan.begin();
  plan.consider(PAIR);
  await fail(1);
  assert.equal(plan.shown, null);
  assert.equal(plan.loading, false);
});

test('now: Most pressed again holds the plan on screen; a chosen time does not', async () => {
  const { plan, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  const j = ride('7');
  await answer(0, j);
  plan.setWhen({ mode: 'now', at: 0 });
  assert.equal(plan.shown, j);
  plan.setWhen(DEPART);
  assert.equal(plan.shown, null);
  plan.setWhen({ mode: 'now', at: 0 });
  assert.equal(plan.shown, null, 'nothing was planned for now since');
});

test('a new pair drops the plan, and the old pair\'s answer when it comes', async () => {
  const { plan, calls, changes, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  const token = plan.pairToken();
  plan.reset();
  assert.equal(plan.samePair(token), false);
  assert.equal(plan.loading, false);
  plan.begin();
  plan.consider(SWAPPED);
  await answer(0, ride('OLD'));
  assert.equal(plan.shown, null, 'the old pair\'s answer is not used');
  assert.deepEqual(changes, []);
  const fresh = ride('NEW');
  await answer(1, fresh);
  assert.equal(plan.shown, fresh);
  assert.equal(calls.length, 2);
});

test('a reload of the same pair drops the last run\'s answer but keeps the pair', async () => {
  const { plan, calls, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  const token = plan.pairToken();
  plan.begin();
  assert.equal(plan.samePair(token), true);
  plan.consider(PAIR);
  await answer(0, ride('LATE'));
  assert.equal(plan.shown, null);
  assert.equal(plan.loading, true, 'this run is still planning');
  await answer(1, ride('NOW'));
  assert.equal(plan.shown.legs[0].label, 'NOW');
  assert.equal(calls.length, 2);
});

test('a plan made by the journey map is the one on screen, held over too', async () => {
  const { plan, answer } = setup();
  plan.begin();
  plan.consider(PAIR);
  await answer(0, ride('7'));
  plan.begin();
  const map = ride('MAP');
  plan.adopt(map);
  assert.equal(plan.shown, map);
});

test('a chosen time plans its itineraries once, opens the first and does not go stale', async () => {
  const { plan, clock, calls, changes, answer } = setup();
  plan.setWhen(DEPART);
  plan.begin();
  plan.consider(PAIR);
  assert.equal(plan.timed, true);
  assert.equal(calls[0].kind, 'at');
  assert.deepEqual(calls[0].args, [PAIR, DEPART]);
  assert.deepEqual(changes, ['journey'], 'the box says it is searching');
  const list = [ride('A'), ride('B')];
  await answer(0, list);
  assert.equal(plan.list, list);
  assert.equal(plan.openIndex, 0);
  assert.equal(plan.shown, list[0]);
  plan.consider(PAIR);
  plan.openItinerary(-1);
  assert.equal(plan.shown, null, 'all folded');
  plan.consider(PAIR);
  assert.equal(calls.length, 1, 'planned: neither a repeat nor a folded list asks again');
  clock.now += 3 * NOW_PLAN_STALE_MS;
  assert.equal(plan.stale, false);
  assert.equal(plan.wantsReload(false), false, 'its itineraries are still the answer');
  plan.openItinerary(1);
  assert.equal(plan.shown, list[1]);
});

test('a chosen time with no route found is done; another time plans again', async () => {
  const { plan, calls, answer } = setup();
  plan.setWhen(DEPART);
  plan.begin();
  plan.consider(PAIR);
  await answer(0, []);
  assert.equal(plan.failed, false);
  assert.equal(plan.wantsReload(false), false);
  plan.consider(PAIR);
  assert.equal(calls.length, 1);
  plan.setWhen({ mode: 'arrive', at: DEPART.at });
  plan.begin();
  plan.consider(PAIR);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].args[1], { mode: 'arrive', at: DEPART.at });
});

test('a chosen time whose search failed says so and asks again on the next reload', async () => {
  const { plan, calls, fail, answer } = setup();
  plan.setWhen(DEPART);
  plan.begin();
  plan.consider(PAIR);
  await fail(0);
  assert.equal(plan.failed, true);
  assert.equal(plan.loading, false);
  assert.deepEqual(plan.list, []);
  assert.equal(plan.wantsReload(false), true);
  plan.consider(PAIR);
  assert.equal(calls.length, 2);
  await answer(1, [ride('A')]);
  assert.equal(plan.failed, false);
});

test('nothing planned or on screen: a reload is wanted', () => {
  const { plan } = setup();
  assert.equal(plan.wantsReload(false), true);
  assert.equal(plan.wantsReload(true), false, 'board rows on screen are an answer too');
});

test('FUTÁR plan-trip: both ends as places, the request\'s own parameters; null without a place or FUTÁR', async () => {
  const keep = { planPlace: BkkLib.planPlace, fetch: BkkLib.fetch };
  const asked = [];
  BkkLib.planPlace = async (_key, stop) => (stop.name === 'Nowhere' ? '' : `${stop.name}::1,2`);
  BkkLib.fetch = async (key, path, params, viaKey) => {
    asked.push({ key, path, params, viaKey });
    return { data: {} };
  };
  try {
    const got = await BkkLib.futarPlanTrip('k', { name: 'Alpha' }, { name: 'Beta' }, { numItineraries: '6' });
    assert.deepEqual(got, { data: {} });
    assert.deepEqual(asked, [{
      key: 'k',
      path: 'plan-trip.json',
      params: { fromPlace: 'Alpha::1,2', toPlace: 'Beta::1,2', mode: 'TRANSIT,WALK', numItineraries: '6' },
      viaKey: true,
    }]);
    assert.equal(Object.keys(asked[0].params).join(), 'fromPlace,toPlace,mode,numItineraries');
    assert.equal(await BkkLib.futarPlanTrip('k', { name: 'Alpha' }, { name: 'Nowhere' }, {}), null);
    assert.equal(await BkkLib.futarPlanTrip('', { name: 'Alpha' }, { name: 'Beta' }, {}), null);
    assert.equal(asked.length, 1);
    BkkLib.fetch = async () => { throw new Error('HTTP 503'); };
    await assert.rejects(BkkLib.futarPlanTrip('k', { name: 'Alpha' }, { name: 'Beta' }, {}));
  } finally {
    Object.assign(BkkLib, keep);
  }
});

/* The browser's route patterns (src/lib/route-patterns.js), straight from
   src/: a Map for localStorage, a clock that moves only when told and a
   timer that fires only when told. A "page load" is a new store over the
   same storage. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ROUTE_PATTERNS_KEEP, ROUTE_PATTERNS_KEY, ROUTE_PATTERNS_WRITE_MS, routePatternStore,
} from './src/lib/route-patterns.js';

const NOON = Date.UTC(2026, 10, 10, 11, 0);
const HOUR = 3600000;

function storage() {
  const map = new Map();
  let writes = 0;
  return {
    map,
    writes: () => writes,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { writes += 1; map.set(k, String(v)); },
    saved: () => JSON.parse(map.get(ROUTE_PATTERNS_KEY) || '{}'),
  };
}

/* A page: its store, clock and pending timers over a shared storage. */
function page(disk, at) {
  const clock = { ms: at === undefined ? NOON : at };
  const timers = [];
  const events = new EventTarget();
  const store = routePatternStore({
    storage: () => disk,
    now: () => clock.ms,
    setTimer: (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; },
    clearTimer: (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); },
    events,
  });
  const fire = () => timers.splice(0).forEach((t) => t.fn());
  return { store, clock, timers, events, fire };
}

const pattern = (name) => ({
  variants: [{ name, headsign: name, ids: ['AZP_1', 'AZP_2'] }],
  stops: {
    AZP_1: { id: 'AZP_1', name: 'Keleti', parentStationId: 'AZP_CS', lat: 47.5, lon: 19.08, code: 'F01131' },
    AZP_2: { id: 'AZP_2', name, lat: 47.46, lon: 19.02 },
  },
});

test('a pattern is stamped with the clock, kept whole for the page and slim in storage', () => {
  const disk = storage();
  const p = page(disk);
  const whole = p.store.put('R1', pattern('Kelenföld'));
  assert.equal(whole.at, NOON);
  assert.equal(whole.stops.AZP_1.lat, 47.5);
  assert.equal(p.store.kept('R1').variants[0].ids.join(), 'AZP_1,AZP_2');
  p.fire();
  const saved = disk.saved().R1;
  assert.deepEqual(saved.stops.AZP_1, { id: 'AZP_1', name: 'Keleti', parentStationId: 'AZP_CS' });
  assert.deepEqual(saved.stops.AZP_2, { id: 'AZP_2', name: 'Kelenföld', parentStationId: '' });
  assert.equal(saved.at, NOON);
});

test('many patterns in one page go back in one write, after the delay', () => {
  const disk = storage();
  const p = page(disk);
  ['A', 'B', 'C', 'D'].forEach((r) => p.store.put(r, pattern(r)));
  assert.equal(p.timers.length, 1);
  assert.equal(p.timers[0].ms, ROUTE_PATTERNS_WRITE_MS);
  assert.equal(disk.writes(), 0);
  p.fire();
  assert.equal(disk.writes(), 1);
  assert.deepEqual(Object.keys(disk.saved()).sort(), ['A', 'B', 'C', 'D']);
  /* Nothing pending: a flush writes nothing. */
  p.store.flush();
  assert.equal(disk.writes(), 1);
});

test('a page going away writes the pending patterns at once', () => {
  const disk = storage();
  const p = page(disk);
  p.store.put('R1', pattern('Kelenföld'));
  p.events.dispatchEvent(new Event('pagehide'));
  assert.equal(disk.writes(), 1);
  assert.equal(p.timers.length, 0);
  assert.ok(disk.saved().R1);
});

test('the next page load reads a pattern while it may prove a trip, not once it is three hours old', () => {
  const disk = storage();
  const first = page(disk);
  first.store.put('R1', pattern('Kelenföld'));
  first.store.flush();
  const again = page(disk, NOON + 2 * HOUR);
  assert.equal(again.store.kept('R1').stops.AZP_1.parentStationId, 'AZP_CS');
  const late = page(disk, NOON + 3 * HOUR);
  assert.equal(late.store.kept('R1'), null);
  assert.equal(late.store.kept('R2'), null);
});

test('a write keeps what another tab wrote since this page read the list; the newer copy of a route wins', () => {
  const disk = storage();
  const mine = page(disk);
  mine.store.kept('R1');
  const other = page(disk, NOON + 60000);
  other.store.put('TAB', pattern('Tab'));
  other.store.put('R1', pattern('Theirs'));
  other.store.flush();
  mine.store.put('MINE', pattern('Mine'));
  mine.store.flush();
  const saved = disk.saved();
  assert.deepEqual(Object.keys(saved).sort(), ['MINE', 'R1', 'TAB']);
  assert.equal(saved.R1.variants[0].name, 'Theirs');
  /* This page's own newer copy wins over the other tab's older one. */
  mine.clock.ms = NOON + 120000;
  mine.store.put('R1', pattern('Mine again'));
  mine.store.flush();
  assert.equal(disk.saved().R1.variants[0].name, 'Mine again');
});

test(`only the newest ${ROUTE_PATTERNS_KEEP} fresh patterns are kept`, () => {
  const disk = storage();
  const p = page(disk);
  for (let i = 0; i < ROUTE_PATTERNS_KEEP + 5; i++) {
    p.clock.ms = NOON + i * 1000;
    p.store.put(`R${i}`, pattern(String(i)));
  }
  disk.map.set(ROUTE_PATTERNS_KEY, JSON.stringify({ OLD: Object.assign(pattern('old'), { at: NOON - 4 * HOUR }) }));
  p.fire();
  const saved = disk.saved();
  assert.equal(Object.keys(saved).length, ROUTE_PATTERNS_KEEP);
  assert.ok(!('R0' in saved) && !('R4' in saved) && 'R5' in saved && `R${ROUTE_PATTERNS_KEEP + 4}` in saved);
  assert.ok(!('OLD' in saved));
});

test('fresh is Budapest’s day and three hours: a pattern proved before Budapest midnight is not fresh after it', () => {
  const { store } = page(storage());
  /* 23:30 and 00:10 (CET) share a UTC day. */
  const at = Date.UTC(2026, 10, 10, 22, 30);
  const pat = { at, variants: [{ ids: ['A'] }], stops: {} };
  assert.ok(store.fresh(pat, at + 20 * 60000));
  assert.ok(!store.fresh(pat, at + 40 * 60000));
  assert.ok(store.fresh({ at: NOON, variants: [] }, NOON + 3 * HOUR - 1));
  assert.ok(!store.fresh({ at: NOON, variants: [] }, NOON + 3 * HOUR));
  assert.ok(!store.fresh(null) && !store.fresh({ variants: [] }) && !store.fresh({ at: NOON }));
});

test('without storage (a private window, storage off or full) the page’s memory still holds the patterns', () => {
  const denied = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  const p = page(denied);
  p.store.put('R1', pattern('Kelenföld'));
  assert.doesNotThrow(() => p.fire());
  assert.equal(p.store.kept('R1').variants.length, 1);
  const none = page(undefined);
  none.store.put('R2', pattern('Kelenföld'));
  assert.doesNotThrow(() => none.store.flush());
  assert.equal(none.store.kept('R2').variants.length, 1);
  /* An unreadable list is no list. */
  const junk = storage();
  junk.map.set(ROUTE_PATTERNS_KEY, '{not json');
  assert.equal(page(junk).store.kept('R1'), null);
});

/* The library's namespace (src/lib/core.js) and a card's context
   (src/lib/context.js), straight from src/: every lib module registers its
   members once, a name defined twice is a load error, and every index read
   goes through the index resolver the caller hands in, never the default
   one. The index loaders record the URL they are asked for and fail, as
   does every other request. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cardContext, needIndexes } from './src/lib/context.js';
import { BkkLib, register } from './src/lib/core.js';
import './src/lib/names.js';
import './src/lib/futar.js';
import './src/lib/transitous.js';
import './src/lib/journey.js';
import './src/lib/geo.js';
import './src/lib/dashboard-key.js';
import './src/lib/gtfs.js';
import './src/lib/services.js';
import './src/lib/departures.js';
import './src/lib/vehicles.js';
import './src/lib/realtime.js';
import './src/lib/stop-history.js';
import './src/lib/rows.js';
import './src/lib/elvira-rows.js';
import './src/lib/board.js';
import './src/lib/journey-plan.js';
import './src/lib/ride-map.js';

test('every lib module loads beside the others: no member is registered twice', () => {
  assert.equal(typeof BkkLib.nextBoard, 'function');
  assert.equal(typeof BkkLib.mergeRows, 'function');
});

test('registering a name that is already there throws and changes nothing', () => {
  const keep = BkkLib.mergeRows;
  assert.throws(() => register({ zzGuardNew: 1, mergeRows: () => [] }), /BkkLib\.mergeRows is registered twice/);
  assert.equal(BkkLib.mergeRows, keep);
  assert.ok(!('zzGuardNew' in BkkLib));
  register({ zzGuardNew: 1 });
  try {
    assert.throws(() => register({ zzGuardNew: 2 }), /registered twice/);
    assert.equal(BkkLib.zzGuardNew, 1);
  } finally {
    delete BkkLib.zzGuardNew;
  }
});

/* The index loaders behind the resolver: they record the URL and fail, like
   a missing file; every other request fails too. */
async function withLoaders(fn) {
  const urls = [];
  const keep = {
    gtfs: BkkLib._loadGtfsIndex, mav: BkkLib._loadMavIndex, fetch: globalThis.fetch, warn: console.warn,
  };
  BkkLib._idx.clear();
  BkkLib._mavP.clear();
  BkkLib._mavEmptyAt.clear();
  BkkLib._loadGtfsIndex = async (url) => { urls.push(url); throw new Error('no index'); };
  BkkLib._loadMavIndex = async (url) => { urls.push(url); throw new Error('no index'); };
  globalThis.fetch = async () => { throw new Error('offline'); };
  console.warn = () => {};
  try {
    await fn(urls);
  } finally {
    BkkLib._loadGtfsIndex = keep.gtfs;
    BkkLib._loadMavIndex = keep.mav;
    globalThis.fetch = keep.fetch;
    console.warn = keep.warn;
    BkkLib._idx.clear();
    BkkLib._mavP.clear();
    BkkLib._mavEmptyAt.clear();
  }
}

const OWN = {
  volanIndex: 'https://own.invalid/card-volan.json.gz',
  cityIndex: 'https://own.invalid/card-city.json.gz',
  mavIndex: 'https://own.invalid/card-mav.json.gz',
};
const CITY_STOP = { id: 'szfvar:123', name: 'Piac tér' };
const STATION = { id: 'BKK_005503269', name: 'Székesfehérvár', key: 'szekesfehervar' };
const ELVIRA_HASS = { states: {}, services: { hungarian_transport: { elvira_between: {} } } };

/* Every lib entry point a card calls, and the index-reading functions
   below them, with one index resolver. */
const ENTRIES = {
  searchStops: (ix) => BkkLib.searchStops(ix, '', 'piac', 'all', ''),
  searchHelyi: (ix) => BkkLib.searchStops(ix, '', 'piac', 'helyi', 'szfvar'),
  searchPlannerStops: (ix) => BkkLib.searchPlannerStops(ix, '', 'piac'),
  reachableDests: (ix) => BkkLib.reachableDests(ix, '', {}, CITY_STOP, 'all', ''),
  reachableHelyi: (ix) => BkkLib.reachableDests(ix, '', {}, CITY_STOP, 'helyi', 'szfvar'),
  departures: (ix) => BkkLib.departures(ix, '', CITY_STOP.id, [], STATION, { mode: 'all', originName: CITY_STOP.name }),
  departuresHelyi: (ix) => BkkLib.departures(ix, '', CITY_STOP.id, [], STATION, { mode: 'helyi', city: 'szfvar', originName: CITY_STOP.name }),
  planJourney: (ix) => BkkLib.planJourney(ix, '', CITY_STOP, STATION, null, {}),
  planJourneys: (ix) => BkkLib.planJourneys(ix, '', CITY_STOP, STATION, null, { time: Date.now() + 3600000 }),
  withGtfsCoords: (ix) => BkkLib.withGtfsCoords(ix, [CITY_STOP, { id: 'volan_1', name: 'Volán' }]),
  mavRouteForRow: (ix) => BkkLib.mavRouteForRow(ix, { tripId: 'elvira:1', trainNumber: '849' }, 'A', 'B'),
  stopArea: (ix) => BkkLib.stopArea(ix, { helyi: true, city: 'szfvar' }),
  volanDepartures: (ix) => BkkLib.volanDepartures(ix, 'volan_1', STATION, 'Volán', 60),
  volanRoutesAtStop: (ix) => BkkLib.volanRoutesAtStop(ix, { id: 'volan_1' }),
  volanRemainingNames: (ix) => BkkLib.volanRemainingNames(ix, { id: 'volan_1' }, '7'),
  elviraBetween: (ix) => BkkLib.elviraBetween(ix, ELVIRA_HASS, CITY_STOP.id, STATION.id, 60, STATION.name, { originName: CITY_STOP.name }),
};

test('a card’s context hands its own index URLs to every index read, the default to none', async () => {
  await withLoaders(async (urls) => {
    const ctx = cardContext({ cfg: () => OWN });
    for (const [name, run] of Object.entries(ENTRIES)) {
      await run(ctx.indexes()).catch(() => null);
      assert.ok(urls.every((u) => u.startsWith('https://own.invalid/')), `${name}: ${urls.join(' ')}`);
    }
    /* The lib entry points the cards call, through their production sources. */
    await BkkLib.boardSources(ctx).timetables('', CITY_STOP.id, [], STATION, { mode: 'all' }).catch(() => null);
    await BkkLib.rideMapSources(ctx).mavRoute({ tripId: 'elvira:1' }, 'A', 'B').catch(() => null);
    await BkkLib.rideMapSources(ctx).stopArea({ helyi: true, city: 'szfvar' }).catch(() => null);
    await BkkLib.journeyPlanSources(ctx).now({ origin: CITY_STOP, dest: STATION, horizon: 60 }).catch(() => null);
    const bases = new Set(urls.map((u) => u.replace(/\?.*$/, '')));
    assert.deepEqual([...bases].sort(), [OWN.cityIndex, OWN.mavIndex, OWN.volanIndex]);
  });
});

test('the context reads the config as it is now: another index URL is the next read’s', async () => {
  await withLoaders(async (urls) => {
    let cfg = {};
    const ctx = cardContext({ cfg: () => cfg });
    await ctx.indexes().city().catch(() => null);
    cfg = OWN;
    await ctx.indexes().city().catch(() => null);
    assert.equal(urls.length, 2);
    assert.ok(!urls[0].startsWith('https://own.invalid/') && urls[0].includes('city-index.json.gz'));
    assert.ok(urls[1].startsWith(OWN.cityIndex));
  });
});

test('an index read without an index resolver throws; it does not read the default index', async () => {
  await withLoaders(async (urls) => {
    for (const [name, run] of Object.entries(ENTRIES)) {
      for (const bad of [undefined, null, '', 'a-futar-key', {}, { city: async () => ({}) }]) {
        await assert.rejects(async () => run(bad), TypeError, name);
      }
    }
    await assert.rejects(async () => BkkLib.elviraBoard(undefined, ELVIRA_HASS, 'a', 'b', 60, ''), TypeError);
    assert.deepEqual(urls, []);
  });
  assert.throws(() => needIndexes({ volan: () => null, city: () => null }), /index resolver is required/);
  const ix = BkkLib.indexResolver();
  assert.equal(needIndexes(ix), ix);
});

test('two cards searching at once each read their own indexes across the awaits', async () => {
  const reads = [];
  const slow = (who, kind) => async () => {
    reads.push(`${who}:${kind}`);
    await new Promise((r) => setTimeout(r, who === 'a' ? 5 : 1));
    throw new Error('no index');
  };
  const ix = (who) => ({ volan: slow(who, 'volan'), city: slow(who, 'city'), mav: slow(who, 'mav') });
  const a = cardContext({ indexes: () => ix('a') });
  const b = cardContext({ indexes: () => ix('b') });
  const keepFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('offline'); };
  try {
    await Promise.all([
      BkkLib.searchStops(a.indexes(), '', 'piac', 'all', ''),
      BkkLib.searchStops(b.indexes(), '', 'piac', 'all', ''),
    ]);
  } finally {
    globalThis.fetch = keepFetch;
  }
  assert.deepEqual(reads.filter((r) => r.startsWith('a:')).sort(), ['a:city', 'a:volan']);
  assert.deepEqual(reads.filter((r) => r.startsWith('b:')).sort(), ['b:city', 'b:volan']);
});

test('a context reads each part of its card when used; what the card leaves out is empty', () => {
  let key = 'k1';
  const cache = { trip: 1 };
  const hass = { states: {} };
  const ctx = cardContext({
    cfg: () => ({ stopId: 'BKK_1' }), apiKey: () => key, hass: () => hass, tripCache: () => cache, now: () => 42,
  });
  assert.equal(ctx.apiKey(), 'k1');
  key = 'k2';
  assert.equal(ctx.apiKey(), 'k2');
  assert.equal(ctx.tripCache(), cache);
  assert.equal(ctx.hass(), hass);
  assert.equal(ctx.now(), 42);
  assert.equal(ctx.cfg().stopId, 'BKK_1');
  const bare = cardContext({ cfg: () => null });
  assert.deepEqual([bare.cfg(), bare.apiKey(), bare.hass(), bare.tripCache()], [{}, '', null, {}]);
  assert.ok(Math.abs(bare.now() - Date.now()) < 1000);
  /* Without its own, the card's config picks the index URLs. */
  assert.doesNotThrow(() => needIndexes(bare.indexes()));
});

/* Renders the card in jsdom: it must draw its own rows, with no dependency on
   any other custom element. */
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost:8123/local/hungarian-transport/',
  pretendToBeVisual: true,
});
for (const key of ['window', 'document', 'customElements', 'HTMLElement', 'Element',
  'navigator', 'CSSStyleDeclaration', 'Node', 'Event', 'CustomEvent']) {
  globalThis[key] = dom.window[key];
}

await import('./dist/hungarian-transport.js');

const src = await readFile('./dist/hungarian-transport.js', 'utf8');

let failed = false;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failed = true;
};

check('card does not depend on a foreign element', !/bkk-stop-card-r2/.test(src));

const card = document.createElement('hungarian-transport-card');
document.body.appendChild(card);
card.hass = { states: {}, language: 'hu' };
card.setConfig({ name: 'Kelenf\u00f6ld \u2192 Sz\u00e9kesfeh\u00e9rv\u00e1r', language: 'hu' });

const html = () => card.shadowRoot.innerHTML;
check('unconfigured card asks for stops', html().includes('V\u00e1lassz forr\u00e1s'),
  html().slice(0, 120));

const now = Math.floor(Date.now() / 1000);
card._rows = [
  {
    type: 'RAIL',
    icon: 'mdi:train',
    label: 'S40',
    headsign: 'Sz\u00e9kesfeh\u00e9rv\u00e1r \u00b7 v\u00e1g.3',
    depTs: now + 5 * 60,
    schedTs: now + 3 * 60,
    attime: '10:03',
    predicted_attime: '10:05',
    wheelchair: true,
    bikesAllowed: true,
    booking: false,
    color: '4477aa',
    textcolor: 'ffffff',
    trainNumber: '903',
    delay: 120,
    travelMin: 47,
  },
  {
    type: 'BUS',
    icon: 'mdi:bus',
    label: '<script>x</script>',
    headsign: 'Duna\u00fajv\u00e1ros',
    depTs: now + 60,
    schedTs: now + 60,
    attime: '10:01',
    predicted_attime: '10:01',
    wheelchair: false,
    bikesAllowed: false,
    booking: true,
    color: 'F9AB13',
    textcolor: '000000',
    trainNumber: '',
    delay: 0,
    travelMin: null,
  },
  {
    type: 'BUS',
    icon: 'mdi:bus',
    label: '99',
    headsign: 'Mar elment',
    depTs: now - 40 * 60,
    schedTs: now - 40 * 60,
    attime: '07:20',
    predicted_attime: '07:20',
    wheelchair: false,
    bikesAllowed: false,
    booking: false,
    color: 'F9AB13',
    textcolor: '000000',
    trainNumber: '',
    delay: 0,
    travelMin: null,
  },
];
card._paint();

const out = html();
check('renders one row per departure', (out.match(/<tr[\s>]/g) || []).length === 2);
check('hides a bus that already left', !out.includes('Mar elment') && !out.includes('07:20'));
check('route badge carries the feed colours', out.includes('background:#4477aa;color:#ffffff'));
check('vehicle icon rendered', out.includes('mdi:train') && out.includes('mdi:bus'));
check('delay shown as late with struck scheduled time',
  out.includes('at late') && out.includes('class="sched">10:03'));
check('countdown from the epoch', out.includes('5 perc'));
check('travel time shown', out.includes('47 perc'));
check('pictogram tooltip is a data-tip bubble, not a title',
  out.includes('data-tip="Akad\u00e1lymentes, alacsonypadl\u00f3s j\u00e1rm\u0171"'));
check('booking pictogram rendered', out.includes('Helyjegy'));
check('row data is escaped', !out.includes('<script>') && out.includes('&lt;script&gt;'));
check('card size grows with the rows', card.getCardSize() >= 2, String(card.getCardSize()));

card._showErr(new Error('boom'));
const withErr = html();
check('error keeps the rows and adds a banner',
  withErr.includes('boom') && (withErr.match(/<tr[\s>]/g) || []).length === 2);

card.remove();
check('timers cleared on detach', card._poll === null && card._tick === null);

const en = document.createElement('hungarian-transport-card');
document.body.appendChild(en);
en.hass = { states: {}, language: 'en' };
en.setConfig({ language: 'en' });
check('english locale used', en.shadowRoot.innerHTML.includes('Pick an origin'),
  en.shadowRoot.innerHTML.slice(0, 120));

const editor = document.createElement('hungarian-transport-card-editor');
document.body.appendChild(editor);
editor.hass = { states: {}, language: 'hu' };
editor.setConfig({ language: 'hu', minutesAfter: 90 });
const edHtml = editor.innerHTML;
check('editor has a minutesAfter select', edHtml.includes('id="minutesAfter"') && edHtml.includes('<select'));
const minSel = editor.querySelector('#minutesAfter');
check('editor select shows 90', !!(minSel && minSel.value === '90'), minSel && minSel.value);
const chips = editor.querySelectorAll('#minutesAfterChips .chip');
check('editor has look-ahead chips', chips.length === 8, String(chips.length));
let emitted = null;
editor.addEventListener('config-changed', (ev) => { emitted = ev.detail.config.minutesAfter; });
const chip180 = editor.querySelector('#minutesAfterChips .chip[data-min="180"]');
if (chip180) chip180.click();
check('chip click emits minutesAfter', emitted === 180, String(emitted));
minSel.value = '60';
minSel.dispatchEvent(new Event('change', { bubbles: true }));
check('select change emits minutesAfter', emitted === 60, String(emitted));

const Lib = globalThis.HungarianTransportLib;
check('lib is exported for tests', !!(Lib && typeof Lib.polesFromPatterns === 'function'));
const origin = { id: 'BKK_CSF01131', name: 'Keleti p\u00e1lyaudvar' };
const patterns = [{
  variants: [
    { ids: ['BKK_F01159', 'BKK_MID', 'BKK_DEST'] },
    { ids: ['BKK_IN', 'BKK_F01314'] },
  ],
  stops: {
    BKK_F01159: { id: 'BKK_F01159', name: 'Keleti p\u00e1lyaudvar', parentStationId: 'BKK_CSF01131' },
    BKK_MID: { id: 'BKK_MID', name: 'Th\u00f6k\u00f6ly \u00fat' },
    BKK_DEST: { id: 'BKK_DEST', name: 'R\u00e1kospatak utca / Cs\u00f6m\u00f6ri \u00fat' },
    BKK_IN: { id: 'BKK_IN', name: 'R\u00e1kospatak utca / Cs\u00f6m\u00f6ri \u00fat' },
    BKK_F01314: { id: 'BKK_F01314', name: 'Keleti p\u00e1lyaudvar', parentStationId: 'BKK_CSF01131' },
  },
}];
const destPoles = Lib.polesFromPatterns(patterns, origin, 'r\u00e1kospatak utca / cs\u00f6m\u00f6ri \u00fat');
check('dest poles keep only outbound', destPoles.length === 1 && destPoles[0] === 'BKK_F01159',
  JSON.stringify(destPoles));
const allPoles = Lib.polesFromPatterns(patterns, origin, '');
check('undirected poles include both directions', allPoles.length === 2, JSON.stringify(allPoles));
const shaped = Lib.journeyFromPlan({
  data: { entry: { plan: { itineraries: [
    { duration: 2000, walkTime: 400, transfers: 2, legs: [
      { mode: 'BUS', duration: 900000, distance: 1000, routeShortName: '9', headsign: 'Hosszabb', from: { name: 'A' }, to: { name: 'B' } },
    ] },
    { duration: 1532, walkTime: 199, transfers: 1, startTime: 1700000000000, legs: [
      { mode: 'TROLLEYBUS', duration: 780000, distance: 4267, routeShortName: '81', routeColor: '009EE3', routeTextColor: 'FFFFFF', headsign: 'Mexik\u00f3i \u00fat', from: { name: 'Miskolci' }, to: { name: 'Mexik\u00f3i' }, startTime: 1700000120000, endTime: 1700000900000 },
      { mode: 'WALK', duration: 78000, distance: 85, from: { name: 'Mexik\u00f3i' }, to: { name: 'Oktogon' }, startTime: 1700000900000, endTime: 1700000978000 },
      { mode: 'SUBWAY', duration: 447000, distance: 2818, routeShortName: 'M1', headsign: 'V\u00f6r\u00f6smarty t\u00e9r', from: { name: 'Mexik\u00f3i' }, to: { name: 'Oktogon' }, startTime: 1700001158000, endTime: 1700001605000 },
    ] },
  ] } } },
});
check('fastest itinerary is kept', shaped && shaped.durationMin === 26 && shaped.transfers === 1 && shaped.walkMin === 3,
  JSON.stringify(shaped && { durationMin: shaped.durationMin, walkMin: shaped.walkMin }));
check('walk leg is minutes', shaped.legs[1].walk && shaped.legs[1].minutes === 1 && shaped.legs[1].meters === 85);
check('ride leg keeps the route number', shaped.legs[0].label === '81' && shaped.legs[0].minutes === 13);
check('wait is the gap after arrival',
  shaped.legs[0].waitMin === 2 && shaped.legs[2].waitMin === 3 && shaped.waitMin === 5,
  JSON.stringify(shaped && { first: shaped.legs[0].waitMin, next: shaped.legs[2] && shaped.legs[2].waitMin, total: shaped.waitMin }));
check('bkk id becomes a plan vertex',
  Lib.planPlaceVertex('Keleti', 'BKK_CSF01131') === 'Keleti::BKK:CSF01131');
const motisShaped = Lib.journeyFromMotis({
  itineraries: [{
    duration: 12540,
    transfers: 3,
    startTime: '2026-09-22T10:39:00Z',
    endTime: '2026-09-22T14:08:00Z',
    legs: [
      { mode: 'WALK', duration: 60, distance: 80, from: { name: 'START' }, to: { name: 'Miskolci \u00c1llatkert' }, startTime: '2026-09-22T10:39:00Z', endTime: '2026-09-22T10:40:00Z' },
      { mode: 'BUS', duration: 420, routeShortName: 'ZOO', from: { name: 'Miskolci \u00c1llatkert', lat: 47.5, lon: 19.05 }, to: { name: 'Fels\u0151-Majl\u00e1th', lat: 47.51, lon: 19.06 }, startTime: '2026-09-22T10:40:00Z', endTime: '2026-09-22T10:47:00Z', legGeometry: { points: '_mdryA_`vic@_pR_pR', precision: 6 } },
      { mode: 'WALK', duration: 120, distance: 160, from: { name: 'Fels\u0151-Majl\u00e1th' }, to: { name: 'Fels\u0151-Majl\u00e1th' }, startTime: '2026-09-22T10:47:00Z', endTime: '2026-09-22T10:49:00Z' },
      { mode: 'TRAM', duration: 1980, routeShortName: '1V', from: { name: 'Fels\u0151-Majl\u00e1th' }, to: { name: 'Tiszai p\u00e1lyaudvar' }, startTime: '2026-09-22T10:52:00Z', endTime: '2026-09-22T11:25:00Z' },
      { mode: 'REGIONAL_RAIL', duration: 7920, routeShortName: '', displayName: '187 HERN\u00c1D - ZEMPL\u00c9N', from: { name: 'Miskolc-Tiszai' }, to: { name: 'Budapest-Keleti' }, startTime: '2026-09-22T11:30:00Z', endTime: '2026-09-22T13:42:00Z' },
      { mode: 'SUBWAY', duration: 780, routeShortName: 'M4', routeColor: '4ca22f', routeTextColor: 'FFFFFF', from: { name: 'Keleti p\u00e1lyaudvar' }, to: { name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' }, startTime: '2026-09-22T13:50:00Z', endTime: '2026-09-22T14:03:00Z' },
    ],
  }],
});
check('MOTIS fastest itinerary uses seconds',
  motisShaped && motisShaped.durationMin === 209 && motisShaped.transfers === 3 && motisShaped.walkMin === 3,
  JSON.stringify(motisShaped && { durationMin: motisShaped.durationMin, walkMin: motisShaped.walkMin, transfers: motisShaped.transfers }));
check('MOTIS ZOO bus is seven minutes',
  motisShaped.legs[1].label === 'ZOO' && motisShaped.legs[1].minutes === 7);
check('MOTIS rail uses displayName when short name is empty',
  motisShaped.legs[4].label === '187 HERN\u00c1D - ZEMPL\u00c9N');
check('MOTIS M4 keeps route colour',
  String(motisShaped.legs[5].color).toLowerCase() === '4ca22f');
check('MOTIS wait is the gap after arrival',
  motisShaped.legs[3].waitMin === 3 && motisShaped.legs[4].waitMin === 5
  && motisShaped.legs[5].waitMin === 8 && motisShaped.waitMin === 16,
  JSON.stringify(motisShaped && motisShaped.legs.map((l) => l.waitMin)));
check('journeyFromMotis keeps a non-empty MOTIS shape',
  Array.isArray(motisShaped.legs[1].shape) && motisShaped.legs[1].shape.length >= 2
  && motisShaped.legs[1].shape[0][0] > 45 && motisShaped.legs[1].shape[0][0] < 49
  && motisShaped.legs[1].fromLat === 47.5 && motisShaped.legs[1].toLon === 19.06,
  JSON.stringify(motisShaped.legs[1].shape && motisShaped.legs[1].shape.slice(0, 2)));
check('BKK id prefers a hu-bkk Transitous hit',
  Lib.pickTransitousHit([
    { id: 'hu-volanbusz_1', name: 'Keleti p\u00e1lyaudvar', lat: 1, lon: 1 },
    { id: 'hu-bkk_CS056233', name: 'Keleti p\u00e1lyaudvar', lat: 47.5, lon: 19.08 },
  ], { id: 'BKK_CSF01131', name: 'Keleti p\u00e1lyaudvar' }).id === 'hu-bkk_CS056233');
check('Kelenf\u00f6ld MAV id prefers rail over coach station',
  Lib.pickTransitousHit([
    { id: 'at-Railway-x', name: 'Budapest-Kelenf\u00f6ld', lat: 47.46, lon: 19.02 },
    { id: 'hu-volanbusz_773538', name: 'Budapest, Kelenf\u00f6ld aut.\u00e1ll.', lat: 47.46, lon: 19.02 },
    { id: 'hu-bkk_CS056215', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s', lat: 47.46, lon: 19.02 },
  ], { id: 'BKK_005501024', name: 'Kelenf\u00f6ld' }).id === 'hu-bkk_CS056215');
check('Transitous keeps Hungarian stops',
  Lib.transitousInHungary({ id: 'hu-mvk_1', name: 'X' })
  && !Lib.transitousInHungary({ id: 'de-db_1', name: 'Berlin' }));
const origGeo = Lib.transitousGeocode;
const origSearchStops = Lib.searchStops;
Lib.transitousGeocode = async () => [{
  id: 'hu-mvk_643641', name: 'Miskolci \u00c1llatkert', lat: 48.12, lon: 20.65,
  areas: [{ name: 'Miskolc', adminLevel: 8, unique: true }],
}];
const plannerHits = await Lib.searchPlannerStops('k', '\u00c1llatkert');
check('planner search prefers Transitous',
  plannerHits.length === 1 && plannerHits[0].id === 'hu-mvk_643641'
  && plannerHits[0].lat === 48.12 && plannerHits[0].label.indexOf('Miskolc') >= 0,
  JSON.stringify(plannerHits[0]));
Lib.transitousGeocode = async () => [];
let bkkSearch = 0;
Lib.searchStops = async () => {
  bkkSearch += 1;
  return [{ id: 'BKK_X', name: 'Fallback' }];
};
const fallbackHits = await Lib.searchPlannerStops('k', 'zzzz');
check('empty Transitous search falls back to BKK',
  fallbackHits[0] && fallbackHits[0].id === 'BKK_X' && bkkSearch === 1);
Lib.transitousGeocode = origGeo;
Lib.searchStops = origSearchStops;
check('dest extras keep Transitous coordinates', (() => {
  const rows = Lib.destHitsForQuery([], 'kelen', [{
    id: 'hu-bkk_CS056215', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s', lat: 47.46, lon: 19.02,
  }]);
  return rows[0] && rows[0].lat === 47.46 && rows[0].lon === 19.02;
})());

const origPlace = Lib.planPlace;
const origFetch = Lib.fetch;
const origMotis = Lib.transitousJourney;
const origCityRail = Lib.cityRailJourney;
Lib.planPlace = async () => 'Keleti::BKK:CSF01131';
Lib.cityRailJourney = async () => null;
let motisCalls = 0;
let futarCalls = 0;
Lib.transitousJourney = async () => {
  motisCalls += 1;
  return { durationMin: 99, walkMin: 0, waitMin: 0, transfers: 0, legs: [{ walk: false, label: 'ZOO', minutes: 7 }] };
};
Lib.fetch = async () => {
  futarCalls += 1;
  return {
    data: { entry: { plan: { itineraries: [{
      duration: 600, walkTime: 0, transfers: 0,
      startTime: 1700000000000,
      legs: [{
        mode: 'BUS', duration: 600000, routeShortName: '7',
        from: { name: 'A' }, to: { name: 'B' },
        startTime: 1700000000000, endTime: 1700000600000,
      }],
    }] } } },
  };
};
const motisFirst = await Lib.planJourney('k', { id: 'BKK_A', name: 'A' }, { id: 'BKK_B', name: 'B' });
check('Transitous ride skips FUTAR',
  motisFirst && motisFirst.legs[0].label === 'ZOO' && motisCalls === 1 && futarCalls === 0,
  JSON.stringify({ label: motisFirst && motisFirst.legs[0] && motisFirst.legs[0].label, motisCalls, futarCalls }));
Lib.transitousJourney = async () => {
  motisCalls += 1;
  return null;
};
const futarNext = await Lib.planJourney('k', { id: 'miskolc:zoo', name: 'Miskolci \u00c1llatkert' }, { id: 'BKK_005501024', name: 'Kelenf\u00f6ld' });
check('empty Transitous falls back to FUTAR',
  futarNext && futarNext.legs[0].label === '7' && futarCalls === 1,
  JSON.stringify({ label: futarNext && futarNext.legs[0] && futarNext.legs[0].label, futarCalls }));
Lib.planPlace = origPlace;
Lib.fetch = origFetch;
Lib.transitousJourney = origMotis;
Lib.cityRailJourney = origCityRail;
const cityIdx = {
  c: [['1111111', '20000101', '20991231']],
  ops: [{ i: 0, id: 'miskolc', name: 'Miskolc \u2014 MVK' }],
  byNum: new Map(),
  byKey: new Map(),
  stops: [
    { i: 0, op: 0, id: 'miskolc:zoo', name: 'Miskolci \u00c1llatkert', fold: Lib.fold('Miskolci \u00c1llatkert') },
    { i: 1, op: 0, id: 'miskolc:fm1', name: 'Fels\u0151-Majl\u00e1th', fold: Lib.fold('Fels\u0151-Majl\u00e1th') },
    { i: 2, op: 0, id: 'miskolc:fm2', name: 'Fels\u0151-Majl\u00e1th', fold: Lib.fold('Fels\u0151-Majl\u00e1th') },
    { i: 3, op: 0, id: 'miskolc:tiszai', name: 'Tiszai p\u00e1lyaudvar', fold: Lib.fold('Tiszai p\u00e1lyaudvar') },
    { i: 4, op: 0, id: 'miskolc:buza', name: 'B\u00faza t\u00e9r aut\u00f3busz-\u00e1llom\u00e1s', fold: Lib.fold('B\u00faza t\u00e9r aut\u00f3busz-\u00e1llom\u00e1s') },
  ],
  t: [
    [0, 'ZOO', [0, 1], [940, 947]],
    [0, '1', [2, 3], [100, 122]],
    [0, '2', [0, 4], [0, 5]],
  ],
};
const hubPath = Lib.cityHubPath(cityIdx, cityIdx.stops[0]);
check('city feed reaches the station in two rides',
  hubPath && hubPath.hubName === 'Tiszai p\u00e1lyaudvar'
  && hubPath.edges.some((edge) => edge.route === 'ZOO')
  && hubPath.edges.some((edge) => edge.walk)
  && hubPath.edges.some((edge) => edge.route === '1'),
  JSON.stringify(hubPath && { hub: hubPath.hubName, minutes: hubPath.minutes, edges: hubPath.edges.map((e) => e.route || 'walk') }));
check('cheaper bus station is not a rail hub',
  !Lib.isRailHubName('B\u00faza t\u00e9r aut\u00f3busz-\u00e1llom\u00e1s')
  && Lib.isRailHubName('Tiszai p\u00e1lyaudvar')
  && hubPath && hubPath.hubName === 'Tiszai p\u00e1lyaudvar');
check('Tiszai tokens match Miskolc-Tiszai',
  Lib.foldTokenMatch('tiszai palyaudvar', 'Miskolc-Tiszai')
  && !Lib.foldTokenMatch('tiszai palyaudvar', 'Miskolc-G\u00f6m\u00f6ri'));
check('Kelenf\u00f6ld is a rail dest, Pap\u00edrgy\u00e1r is not',
  Lib.isRailDest({ name: 'Kelenf\u00f6ld' }) && !Lib.isRailDest({ name: 'Pap\u00edrgy\u00e1r' }));

let depCalls = 0;
const origDep = Lib.departures;
Lib.departures = async () => {
  depCalls += 1;
  await new Promise((r) => setTimeout(r, 40));
  return [];
};
const c2 = document.createElement('hungarian-transport-card');
c2.hass = { states: {}, language: 'hu' };
c2.setConfig({
  stopId: 'BKK_CSF01131',
  destKey: 'kelenf\u00f6ld',
  destName: 'Kelenf\u00f6ld',
  stopName: 'Keleti',
  routeIds: ['BKK_5400'],
  apiKey: 'k',
  language: 'hu',
});
document.body.appendChild(c2);
await new Promise((r) => setTimeout(r, 80));
check('setConfig then attach fetches once', depCalls === 1, String(depCalls));
Lib.departures = origDep;

const pts = Lib.decodePolyline('_p~iF~ps|U');
check('decodePolyline yields coordinates',
  Array.isArray(pts) && pts.length >= 1 && Number.isFinite(pts[0][0]) && Number.isFinite(pts[0][1]),
  JSON.stringify(pts.slice(0, 2)));
const motisPts = Lib.decodePolyline({ points: '_mdryA_`vic@_pR_pR', precision: 6 });
check('decodePolyline honors MOTIS precision 6 near Hungary',
  Array.isArray(motisPts) && motisPts.length >= 2
  && motisPts[0][0] > 46 && motisPts[0][0] < 49
  && motisPts[0][1] > 16 && motisPts[0][1] < 23,
  JSON.stringify(motisPts.slice(0, 2)));
const scaledWrong = Lib.decodePolyline('_mdryA_`vic@_pR_pR');
check('string polyline still uses Google 1e5',
  Array.isArray(scaledWrong) && scaledWrong.length >= 1
  && (scaledWrong[0][0] < 40 || scaledWrong[0][0] > 50),
  JSON.stringify(scaledWrong.slice(0, 1)));
const loc = Lib.vehicleLoc({ location: { lat: 47.5, lon: 19.05 } });
check('vehicleLoc reads BKK location', loc.lat === 47.5 && loc.lon === 19.05, JSON.stringify(loc));
check('basemap is OSM France without an API key',
  src.includes('tile.openstreetmap.fr/osmfr/') && !/cartocdn|carto\.com|\?key=/.test(src));

const mapped = document.createElement('hungarian-transport-card');
document.body.appendChild(mapped);
mapped.hass = { states: {}, language: 'hu' };
mapped.setConfig({ language: 'hu' });
mapped._rows = [{
  type: 'BUS', icon: 'mdi:bus', label: '7', headsign: 'Csepel',
  depTs: now + 120, schedTs: now + 120, attime: '10:02', predicted_attime: '10:02',
  color: '009EE3', textcolor: 'ffffff', hasLocation: true, tripId: 'BKK_X',
  wheelchair: false, bikesAllowed: false, booking: false, delay: 0, travelMin: null,
}];
mapped._paint();
check('mapped trip rows are clickable', mapped.shadowRoot.innerHTML.includes('row-clickable'));
check('map marker button is rendered',
  mapped.shadowRoot.innerHTML.includes('mdi:map-marker-outline')
  && mapped.shadowRoot.innerHTML.includes('class="map-open"'));
let opened = 0;
mapped._openVehicleMap = () => { opened += 1; };
const tr = mapped.shadowRoot.querySelector('tr[data-row-index]');
if (tr) tr.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('row click opens the map', opened >= 1, String(opened));
const btn = mapped.shadowRoot.querySelector('button.map-open');
opened = 0;
if (btn) btn.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('map button click opens the map', opened >= 1, String(opened));

check('all mode keeps volan coaches',
  Lib.routeMatchesMode({ type: 'COACH', id: 'volan_1' }, 'all'));
check('all mode keeps city buses',
  Lib.routeMatchesMode({ type: 'BUS', id: 'BKK_0085' }, 'all'));
check('all mode keeps mav trains',
  Lib.routeMatchesMode({ type: 'RAIL', id: 'BKK_0055' }, 'all'));
check('bkk mode still drops volan',
  !Lib.routeMatchesMode({ type: 'COACH', id: 'volan_1' }, 'bkk'));

const plan = document.createElement('hungarian-transit-stop-card-plan');
document.body.appendChild(plan);
plan.hass = { states: {}, language: 'hu' };
plan.setConfig({ language: 'hu' });
const planHtml = plan.shadowRoot.innerHTML;
check('planner title is Tervezo', planHtml.includes('Tervez\u0151'));
check('planner has origin and destination fields',
  planHtml.includes('id="pq"') && planHtml.includes('id="pdq"') && !planHtml.includes('id="routes"'));
check('planner has look-ahead chips', planHtml.includes('id="pmin"') && planHtml.includes('Meddig'));
check('planner has swap button', planHtml.includes('id="pswap"') && planHtml.includes('Csere'));
const origPlannerSearch = Lib.searchPlannerStops;
Lib.searchPlannerStops = async () => [];
plan.setConfig({ language: 'en' });
await plan._searchOrigin('zzzzzz');
const originHint = (plan.shadowRoot.querySelector('#phits') || {}).innerHTML || '';
check('planner empty origin is not a BKK-only message',
  /No stop matches this search/i.test(originHint) && !/No BKK stop/i.test(originHint),
  originHint.slice(0, 160));
Lib.searchPlannerStops = origPlannerSearch;
plan.setConfig({ language: 'hu' });
plan._rows = [];
plan._loading = false;
plan._journey = shaped;
plan._paint();
check('empty planner shows the transfer journey',
  plan.shadowRoot.innerHTML.includes('\u00c1tsz\u00e1ll\u00e1ssal')
  && plan.shadowRoot.innerHTML.includes('Gyalogl\u00e1s')
  && plan.shadowRoot.innerHTML.includes('>81<')
  && plan.shadowRoot.innerHTML.includes('background:#009EE3')
  && plan.shadowRoot.innerHTML.includes('V\u00e1rakoz\u00e1s 2 perc'));
check('empty planner journey paint includes the map button',
  plan.shadowRoot.innerHTML.includes('id="pmap"')
  && plan.shadowRoot.innerHTML.includes('T\u00e9rk\u00e9p'));
let journeyMapOpened = 0;
plan._openJourneyMap = () => { journeyMapOpened += 1; };
const pmap = plan.shadowRoot.querySelector('#pmap');
if (pmap) pmap.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('pmap click opens the journey map', journeyMapOpened >= 1, String(journeyMapOpened));
check('planner favorite chips include a Volan station',
  planHtml.includes('N\u00e9pliget'));
check('planner favorite chips include a MAV station',
  planHtml.includes('Sz\u00e9kesfeh\u00e9rv\u00e1r') || planHtml.includes('Budapest-Kelenf'));
check('planner dest search lists MAV stations outside the live sample',
  Lib.destHitsForQuery(
    [{ key: 'godollo', name: 'G\u00f6d\u00f6ll\u0151' }],
    'Miskolc-Tiszai',
    [{ id: 'BKK_005511387', name: 'Miskolc-Tiszai' }],
  ).some((h) => h.name === 'Miskolc-Tiszai'));
check('planner dest search lists MAV stations outside the live sample',
  Lib.destHitsForQuery(
    [{ key: 'godollo', name: 'G\u00f6d\u00f6ll\u0151' }],
    'Miskolc-Tiszai',
    [{ id: 'BKK_005511387', name: 'Miskolc-Tiszai' }],
  ).some((h) => h.name === 'Miskolc-Tiszai'));
check('planner dest filter matches a shorter query against Miskolc-Tiszai',
  Lib.destHitsForQuery(
    [{ key: 'miskolc-tiszai', name: 'Miskolc-Tiszai' }],
    'Miskolc',
    [],
  ).some((h) => /miskolc/i.test(h.name)));
check('elvira station code from BKK MAV id',
  Lib.elviraStationCode('BKK_005511387') === '005511387');
check('elvira station code from CS parent id',
  Lib.elviraStationCode('BKK_CS005511387') === '005511387');
check('BKK Keleti parent has no 9-digit ELVIRA code',
  Lib.elviraStationCode('BKK_CSF01131') === '');
check('Keleti palyaudvar name maps to Budapest-Keleti station code',
  Lib.elviraCodeFromName('Keleti pályaudvar') === '005510017');
check('Kelenfold name maps to Budapest-Kelenfold station code',
  Lib.elviraCodeFromName('Kelenföld vasútállomás') === '005501024');
check('Debrecen railway station shares a key with Debrecen',
  Lib.stationKey('Debrecen, vasútállomás') === Lib.stationKey('Debrecen'));
check('planner dest search merges Debrecen volan stop onto the MAV id',
  Lib.destHitsForQuery(
    [],
    'Debrecen, vasútállomás',
    [
      { id: 'hkir_217186', name: 'Debrecen, vasútállomás' },
      { id: 'BKK_005513912', name: 'Debrecen' },
    ],
  )[0].id === 'BKK_005513912');
check('elvira result parser reads response.departures',
  Lib.elviraRowsFromResult({ context: {}, response: { departures: [{ label: 'TOKAJ' }] } }).length === 1);
check('elvira result parser reads REST service_response',
  Lib.elviraRowsFromResult({ service_response: { departures: [{ label: 'HERNÁD' }, { label: 'TOKAJ' }] } }).length === 2);
check('elvira result parser reads top-level departures',
  Lib.elviraRowsFromResult({ departures: [{ label: 'IC' }] })[0].label === 'IC');
check('elvira placeholder blue takes FUTAR G43/Z30 brand colors on merge', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const merged = Lib.mergeVolanRows(
    [
      { label: 'G43', dep: dep, sched: dep, color: '#AACD46', text: '#FFFFFF' },
      { label: 'Z30', dep: dep + 120, sched: dep + 120, color: '#FFCD28', text: '#3C3C3C' },
    ],
    [
      { label: 'G43', dep: dep, sched: dep, color: '#4477aa', text: '#ffffff', platform: '5' },
      { label: 'Z30', dep: dep + 120, sched: dep + 120, color: '#4477aa', text: '#ffffff', platform: '7' },
    ],
    12,
  );
  const g43 = merged.find((r) => r.label === 'G43');
  const z30 = merged.find((r) => r.label === 'Z30');
  return g43 && z30
    && String(g43.color).replace('#', '').toUpperCase() === 'AACD46'
    && String(z30.color).replace('#', '').toUpperCase() === 'FFCD28'
    && g43.platform === '5' && z30.platform === '7';
})());
check('ELVIRA-only G43/Z30 get BKK badge colours', (() => {
  const g = Lib.applyRailBadge({ label: 'G43', color: '#4477aa' });
  const z = Lib.applyRailBadge({ label: 'Z30', color: '#4477aa' });
  const painted = Lib.asRow({
    label: 'G43', color: '#4477aa', rawType: 'RAIL', dep: Math.floor(Date.now() / 1000) + 120,
  }, 'Székesfehérvár', 'hu');
  return String(g.color).toUpperCase().indexOf('AACD46') >= 0
    && String(z.color).toUpperCase().indexOf('FFCD28') >= 0
    && String(z.text).toUpperCase().indexOf('3C3C3C') >= 0
    && String(painted.color).toUpperCase() === 'AACD46';
})());
check('ELVIRA named IC keeps label and takes FUTAR GPS by train number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', tripId: 'BKK_913_316', trainNumber: '913',
      dep: dep + 60, sched: dep, color: '#2E5EA8', text: '#FFFFFF',
      lat: 47.19751, lon: 18.16734,
    }],
    [{
      label: 'BAKONY', tripId: 'elvira:2974501', trainNumber: '913',
      dep: dep, sched: dep, color: '#4477aa', text: '#ffffff',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].label === 'BAKONY'
    && merged[0].tripId === 'BKK_913_316'
    && merged[0].lat === 47.19751
    && merged[0].lon === 18.16734;
})());
check('ELVIRA Tópart takes FUTAR GPS from BKK trip id train number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 1200;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', tripId: 'BKK_853_87', dep: dep, sched: dep,
      lat: 46.90843, lon: 18.05495,
    }],
    [{
      label: 'TÓPART', tripId: 'elvira:2888051', trainNumber: '853',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].label === 'TÓPART'
    && merged[0].tripId === 'BKK_853_87'
    && merged[0].lat === 46.90843;
})());
check('FUTAR trip id from BKK_4541_108 is 4541', Lib.trainNumberFromTripId('BKK_4541_108') === '4541');
check('elvira trip id is not a FUTAR trip', !Lib.isFutarTripId('elvira:2889849') && Lib.isFutarTripId('BKK_4541_108'));
check('MAV+dest keeps unlisted S30/S40/EC',
  Lib.keepFutarCandidate({ id: 'BKK_S30' }, new Set(['BKK_G43']), 'mav', 'székesfehérvár')
  && Lib.keepFutarCandidate({ id: 'BKK_S40' }, new Set(['BKK_G43']), 'mav', 'székesfehérvár')
  && Lib.keepFutarCandidate({ id: 'BKK_EC' }, new Set(['BKK_G43']), 'all', 'székesfehérvár'));
check('BKK+dest keeps a bus omitted from routeIds',
  Lib.keepFutarCandidate({ id: 'BKK_1335' }, new Set(['BKK_0070', 'BKK_1100']), 'bkk', 'keleti'));
check('BKK without dest still filters unselected buses',
  !Lib.keepFutarCandidate({ id: 'BKK_1335' }, new Set(['BKK_0070']), 'bkk', ''));
check('rowTimeKey joins label and sched minute',
  Lib.rowTimeKey({ label: 'S30', sched: 1790020080 }) === 'S30|' + Math.round(1790020080 / 60));
check('BALATON matches FUTAR IC 861 by train number',
  Lib.matchFutarStopTime(
    { label: 'BALATON', trainNumber: '861', sched: 1790015820, tripId: 'elvira:2890602' },
    { tripId: 'BKK_861_74', departureTime: 1790015820 },
    { shortName: null },
    { iconDisplayText: 'IC' },
  ));
check('BALATON matches FUTAR IC by sched minute when labels differ',
  Lib.matchFutarStopTime(
    { label: 'BALATON', trainNumber: '', sched: 1790015820, tripId: 'elvira:1' },
    { tripId: 'BKK_861_74', departureTime: 1790015820 },
    {},
    { iconDisplayText: 'IC' },
  ));
check('BALATON does not match a different-minute IC',
  !Lib.matchFutarStopTime(
    { label: 'BALATON', trainNumber: '861', sched: 1790015820, tripId: 'elvira:1' },
    { tripId: 'BKK_963_313', departureTime: 1790015820 + 3600 },
    {},
    { iconDisplayText: 'IC' },
  ));
check('EMMA tripShortName 861 BALATON yields train number 861',
  Lib.emmaTrainNumber('861 BALATON InterCity') === '861');
check('BALATON 861 matches EMMA vehicle by train number',
  Lib.matchEmmaVehicle(
    { label: 'BALATON', trainNumber: '861', tripId: 'elvira:2890602' },
    { lat: 47.229, lon: 18.668, trip: { tripShortName: '861 BALATON InterCity' } },
  ));
check('BAKONY 901 matches EMMA named IC by number',
  Lib.matchEmmaVehicle(
    { label: 'BAKONY', trainNumber: '901', tripId: 'elvira:1' },
    { lat: 47.15, lon: 18.04, trip: { tripShortName: '901 BAKONY InterCity', route: { longName: 'IC BAKONY' } } },
  ));
check('BAKONY does not match a different train number',
  !Lib.matchEmmaVehicle(
    { label: 'BAKONY', trainNumber: '901', tripId: 'elvira:1' },
    { lat: 47.18, lon: 18.56, trip: { tripShortName: '918 BAKONY InterCity' } },
  ));
check('applyEmmaPositions fills ELVIRA lat/lon from EMMA', (() => {
  const rows = [{ label: 'BALATON', trainNumber: '861', tripId: 'elvira:2890602' }];
  Lib.applyEmmaPositions(rows, [{
    lat: 47.2290993, lon: 18.6682205, heading: 12,
    trip: { tripShortName: '861 BALATON InterCity' },
  }]);
  return rows[0].lat === 47.2290993 && rows[0].lon === 18.6682205 && rows[0].tripId === 'elvira:2890602';
})());
check('ELVIRA GPS is kept over FUTAR overlay', (() => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', tripId: 'BKK_861_74', trainNumber: '861',
      dep: dep, sched: dep, color: '#2E5EA8',
      lat: 47.5, lon: 19.05,
    }],
    [{
      label: 'BALATON', tripId: 'elvira:2890602', trainNumber: '861',
      dep: dep, sched: dep, color: '#4477aa',
      lat: 47.2290993, lon: 18.6682205,
    }],
    12,
  );
  return merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].tripId === 'elvira:2890602'
    && merged[0].lat === 47.2290993
    && merged[0].lon === 18.6682205
    && String(merged[0].color).replace('#', '').toUpperCase() === '2E5EA8';
})());
check('map skips FUTAR resolve when ELVIRA row already has GPS',
  !Lib.needsFutarMapResolve({ tripId: 'elvira:2890602', lat: 47.229, lon: 18.668 })
  && Lib.needsFutarMapResolve({ tripId: 'elvira:2890602' })
  && !Lib.needsFutarMapResolve({ tripId: 'BKK_861_74', lat: null, lon: null }));
check('coach route keys match SZ009 with 9',
  Lib.coachRouteKeys('SZ009').indexOf('9') >= 0
  && Lib.coachRouteKeys('9').indexOf('SZ9') >= 0);
check('Volán 6990 GPS lands on the in-progress gtfs row only', (() => {
  const now = 1_790_000_000;
  const rows = [
    { label: '6990', tripId: 'gtfs:6990:a', vehicle: 'coach', dep: now - 5 * 60, sched: now - 5 * 60 },
    { label: '6990', tripId: 'gtfs:6990:b', vehicle: 'coach', dep: now + 40 * 60, sched: now + 40 * 60 },
  ];
  Lib.applyCoachGps(rows, [{ route: '6990', lat: 46.96, lon: 16.27, source: 'volan' }], now);
  return rows[0].lat === 46.96 && rows[0].lon === 16.27
    && rows[0].tripId === 'gtfs:6990:a'
    && rows[1].lat == null;
})());
check('Szombathely SZ009 matches a city row labelled 9', (() => {
  const now = 1_790_000_000;
  const rows = [{ label: '9', tripId: 'gtfs:9:a', vehicle: 'bus', dep: now + 4 * 60 }];
  Lib.applyCoachGps(rows, [{ route: 'SZ009', lat: 47.23, lon: 16.62, source: 'szombathely' }], now);
  return rows[0].lat === 47.23 && rows[0].lon === 16.62;
})());
check('a Volán row does not take a bus going somewhere else', (() => {
  const now = 1_790_000_000;
  const rows = [{
    label: '4218', tripId: 'gtfs:4218:a', vehicle: 'coach', head: 'Kocsord, hídfő',
    dep: now + 61 * 60,
  }];
  Lib.applyCoachGps(rows, [{
    route: '4218', head: 'Mátészalka, autóbusz-állomás', lat: 47.8383, lon: 22.1182,
  }], now);
  return rows[0].lat == null;
})());
check('matching headsign still gets the live GPS', (() => {
  const now = 1_790_000_000;
  const rows = [{
    label: '4218', tripId: 'gtfs:4218:b', vehicle: 'coach', head: 'Fehérgyarmat, autóbusz-állomás',
    dep: now + 61 * 60,
  }];
  Lib.applyCoachGps(rows, [{
    route: '4218', head: 'Fehérgyarmat, autóbusz-állomás', lat: 47.84, lon: 22.12,
  }], now);
  return rows[0].lat === 47.84;
})());
check('Szombathely SZ012 does not paint a Volán route 12', (() => {
  const now = 1_790_000_000;
  const rows = [{ label: '12', tripId: 'gtfs:12:v', vehicle: 'coach', head: 'Valahol', dep: now + 5 * 60 }];
  Lib.applyCoachGps(rows, [{ route: 'SZ012', head: 'Herény, Béke tér', lat: 47.23, lon: 16.62 }], now);
  return rows[0].lat == null;
})());
check('an empty-head vehicle only dots a departure within 20 minutes', (() => {
  const now = 1_790_000_000;
  const rows = [
    { label: '30Y', tripId: 'gtfs:30Y:soon', vehicle: 'bus', dep: now + 5 * 60 },
    { label: '30Y', tripId: 'gtfs:30Y:later', vehicle: 'bus', dep: now + 6 * 3600 },
  ];
  Lib.applyCoachGps(rows, [
    { route: 'SZ30Y', lat: 47.25, lon: 16.61, tripId: 'v1' },
    { route: 'SZ30Y', head: 'Másik vég', lat: 47.20, lon: 16.50, tripId: 'v2' },
  ], now);
  return rows[0].lat === 47.25 && rows[1].lat == null;
})());
check('coach GPS does not overwrite an existing train coordinate', (() => {
  const now = 1_790_000_000;
  const rows = [{
    label: '861', tripId: 'elvira:2890602', trainNumber: '861',
    dep: now, lat: 47.229, lon: 18.668,
  }];
  Lib.applyCoachGps(rows, [{ route: '861', lat: 46.1, lon: 18.1 }], now);
  return rows[0].lat === 47.229 && rows[0].lon === 18.668;
})());
check('ELVIRA S30 takes FUTAR trip id by train number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 1800;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'S30', tripId: 'BKK_4541_108', trainNumber: '4541',
      dep: dep, sched: dep, color: '#00AFF0',
    }],
    [{
      label: 'S30', tripId: 'elvira:2889849', trainNumber: '4541',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].tripId === 'BKK_4541_108'
    && String(merged[0].color).toUpperCase().indexOf('00AFF0') >= 0;
})());
check('decodePolyline accepts a FUTAR polyline object',
  Lib.decodePolyline({ points: '_p~iF~ps|U' }).length >= 1);
check('planner dest search prefers a 9-digit MAV id over a volan id',
  Lib.destHitsForQuery(
    [],
    'Miskolc',
    [
      { id: 'volan_12345', name: 'Miskolc-Tiszai' },
      { id: 'BKK_005511387', name: 'Miskolc-Tiszai' },
    ],
  )[0].id === 'BKK_005511387');

const planCfg = await customElements.get('hungarian-transit-stop-card-plan').getConfigElement();
check('planner getConfigElement is not null', !!planCfg, String(planCfg));
check('planner getConfigElement returns planner editor',
  planCfg && planCfg.tagName.toLowerCase() === 'hungarian-transit-stop-card-plan-editor',
  planCfg && planCfg.tagName);

const ped = document.createElement('hungarian-transit-stop-card-plan-editor');
document.body.appendChild(ped);
ped.hass = { states: {}, language: 'hu' };
ped.setConfig({ language: 'hu', minutesAfter: 90 });
const pedHtml = ped.innerHTML;
check('planner editor has minutesAfter', pedHtml.includes('id="minutesAfter"') && pedHtml.includes('<select'));
check('planner editor hides mode toggles',
  ped.querySelector('#modeSwitches') && ped.querySelector('#modeSwitches').style.display === 'none');
check('planner editor hides city select',
  ped.querySelector('#cityWrap') && ped.querySelector('#cityWrap').style.display === 'none');
check('planner editor still has hidden mav checkbox', !!ped.querySelector('#mav'));

const pminChips = plan.shadowRoot.querySelectorAll('#pmin .chip');
check('planner in-card minutesAfter chips', pminChips.length === 8, String(pminChips.length));
check('look-ahead chips wrap on their own row',
  !!plan.shadowRoot.querySelector('.horizon-bar #preset')
  && plan.shadowRoot.querySelector('.horizon > #pmin')
  && /\.horizon \.chips \{[^}]*flex-wrap:\s*wrap/.test(src)
  && !/\.horizon \.chips \{[^}]*overflow-x:\s*auto/.test(src));
check('city search keeps one stop from each city', (() => {
  const idx = {
    ops: [
      { id: 'miskolc', name: 'Miskolc — MVK' },
      { id: 'pecs', name: 'Pécs — Tüke Busz' },
    ],
    stops: [
      { op: 0, name: 'Tiszai pályaudvar', fold: Lib.fold('Tiszai pályaudvar') },
      { op: 0, name: 'Tiszai pu. másik', fold: Lib.fold('Tiszai pu. másik') },
      { op: 1, name: 'Tiszai tér', fold: Lib.fold('Tiszai tér') },
    ],
  };
  const hits = Lib.citySearchAll(idx, 'Tiszai');
  const ops = hits.map((s) => s.op).sort();
  return hits.length === 2 && ops[0] === 0 && ops[1] === 1;
})());
check('city stop id resolves to the szombathely operator',
  Lib.cityOpId({
    ops: [{ i: 3, id: 'szombathely', name: 'Szombathely — Blaguss' }],
    stops: [{ id: 'szombathely:2171', op: 3 }],
  }, { id: 'szombathely:2171' }) === 'szombathely');
check('same-name city stops in both directions stay together', (() => {
  const fold = Lib.fold('Olad');
  const idx = {
    stops: [
      { i: 1, id: 'szombathely:1', op: 0, fold: fold, name: 'Olad' },
      { i: 2, id: 'szombathely:2', op: 0, fold: fold, name: 'Olad' },
    ],
    byNum: new Map(),
    byKey: new Map(),
  };
  const ids = Lib.cityStopIndexes(idx, { id: 'szombathely:1', name: 'Olad' }, 0);
  return ids.indexOf(1) >= 0 && ids.indexOf(2) >= 0;
})());
const chip60 = Array.from(pminChips).find((c) => c.textContent === '60');
if (chip60) chip60.click();
check('planner chip sets minutesAfter', plan._config.minutesAfter === 60, String(plan._config && plan._config.minutesAfter));

plan._loadDests = async () => {};
plan._reloadSafe = () => {};
plan.setConfig({
  language: 'hu',
  stopId: 'BKK_A',
  stopName: 'Keleti',
  destKey: 'debrecen',
  destName: 'Debrecen',
  destStopId: 'BKK_B',
  minutesAfter: 60,
});
plan._swapPlan();
check('planner swap origin dest',
  plan._config.stopId === 'BKK_B'
  && plan._config.destStopId === 'BKK_A'
  && plan._config.stopName === 'Debrecen'
  && plan._config.destName === 'Keleti',
  JSON.stringify({
    stopId: plan._config.stopId,
    destStopId: plan._config.destStopId,
    stopName: plan._config.stopName,
    destName: plan._config.destName,
  }));

const originZoo = { id: 'miskolc:zoo', name: 'Miskolci \u00c1llatkert' };
const destKelenf = { id: 'BKK_005501024', name: 'Kelenf\u00f6ld' };
const destTiszai = { id: 'miskolc:tiszai', name: 'Tiszai p\u00e1lyaudvar' };
const destLocal = { id: 'miskolc:paper', name: 'Pap\u00edrgy\u00e1r' };
const origCity = Lib.cityIndex;
const origRide = Lib.nextCityRide;
const origSearch = Lib.searchStops;
const origElvira = Lib.elviraBetween;
Lib.cityIndex = async () => cityIdx;
Lib.nextCityRide = () => {
  const now = Math.floor(Date.now() / 1000);
  return { dep: now + 60, arr: now + 300 };
};
Lib.searchStops = async (_key, _q, mode) => {
  if (mode !== 'mav') return [];
  return [
    { id: 'BKK_005510001', name: 'Miskolc-G\u00f6m\u00f6ri' },
    { id: 'BKK_005510009', name: 'Miskolc-Tiszai' },
  ];
};
Lib.elviraBetween = async () => [];
const noRail = await Lib.cityRailJourney('k', { states: {} }, originZoo, destKelenf);
check('cityRailJourney is null without a train', noRail == null, JSON.stringify(noRail));
const toHub = await Lib.cityRailJourney('k', { states: {} }, originZoo, destTiszai);
check('city legs stand when dest is the hub',
  toHub && toHub.legs.some((leg) => leg.label === 'ZOO') && !toHub.legs.some((leg) => !leg.walk && /^(IC|EC)/.test(leg.label)),
  JSON.stringify(toHub && toHub.legs.map((l) => l.label || (l.walk ? 'walk' : ''))));
const destMiskolcTiszai = { id: 'BKK_005510009', name: 'Miskolc-Tiszai' };
const destGomori = { id: 'BKK_005510001', name: 'Miskolc-G\u00f6m\u00f6ri' };
const toMavHub = await Lib.cityRailJourney('k', { states: {} }, originZoo, destMiskolcTiszai);
check('Miskolc-Tiszai dest is the city hub',
  toMavHub && toMavHub.legs.some((leg) => leg.label === 'ZOO') && !toMavHub.legs.some((leg) => !leg.walk && /^(IC|EC)/.test(leg.label)),
  JSON.stringify(toMavHub && toMavHub.legs.map((l) => l.label || (l.walk ? 'walk' : ''))));
const gomoriMiss = await Lib.cityRailJourney('k', { states: {} }, originZoo, destGomori);
check('G\u00f6m\u00f6ri is not the Tiszai hub', gomoriMiss == null);
const skipLocal = await Lib.cityRailJourney('k', { states: {} }, originZoo, destLocal);
check('cityRailJourney skips a non-rail dest', skipLocal == null);
Lib.nextCityRide = () => null;
const noTime = await Lib.cityRailJourney('k', { states: {} }, originZoo, destTiszai);
check('missing nextCityRide yields null', noTime == null);
Lib.nextCityRide = origRide;
const rail = await Lib.railAfterCity(
  'k', { states: {} }, 'Miskolc', hubPath, destKelenf, Math.floor(Date.now() / 1000),
);
check('railAfterCity is null when ELVIRA is empty', rail == null);
Lib.elviraBetween = async () => {
  const dep = Math.floor(Date.now() / 1000) + 20 * 60;
  return [{ label: 'IC', trainNumber: '163', dep, travel: 120, headsign: 'Kelenf\u00f6ld' }];
};
const withRail = await Lib.railAfterCity(
  'k', { states: {} }, 'Miskolc', hubPath, destKelenf, Math.floor(Date.now() / 1000),
);
check('railAfterCity picks Tiszai not G\u00f6m\u00f6ri',
  withRail && withRail.train && withRail.walk.from === 'Tiszai p\u00e1lyaudvar'
  && withRail.walk.to === 'Miskolc-Tiszai'
  && withRail.train.color.toLowerCase() === '2e5ea8'
  && withRail.walk.stationWalk,
  JSON.stringify(withRail && { from: withRail.walk.from, to: withRail.walk.to, color: withRail.train.color }));
Lib.cityIndex = origCity;
Lib.nextCityRide = origRide;
Lib.searchStops = origSearch;
Lib.elviraBetween = origElvira;

check('enrichFromHass fills GPS via applyEmmaPositions', (() => {
  const rows = [{ label: 'BALATON', trainNumber: '861', tripId: 'elvira:1' }];
  Lib.enrichFromHass({
    states: {
      'sensor.x': {
        attributes: {
          vehicles: [{ lat: 47.2, lon: 18.6, trip: { tripShortName: '861 BALATON' } }],
        },
      },
    },
  }, rows);
  return rows[0].lat === 47.2 && rows[0].lon === 18.6;
})());
check('enrichFromHass keeps EMMA tripShortName over a named label', (() => {
  const rows = [{ label: 'IC', trainNumber: '861', tripId: 'elvira:2' }];
  Lib.enrichFromHass({
    states: {
      'sensor.x': {
        attributes: {
          vehicles: [{
            lat: 47.3, lon: 18.7, label: 'BALATON', trainNumber: '861',
            trip: { tripShortName: '861 BALATON' },
          }],
        },
      },
    },
  }, rows);
  return rows[0].lat === 47.3 && rows[0].lon === 18.7;
})());

process.exit(failed ? 1 : 0);

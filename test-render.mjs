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
const shortWalk = Lib.journeyFromPlan({
  data: { entry: { plan: { itineraries: [{
    duration: 20, walkTime: 20, transfers: 0, startTime: 1700000000000,
    legs: [
      {
        mode: 'WALK', duration: 20000, distance: 40,
        from: { name: 'A' }, to: { name: 'B' },
        startTime: 1700000000000, endTime: 1700000020000,
      },
      {
        mode: 'BUS', duration: 600000, distance: 1000, routeShortName: '1',
        from: { name: 'B' }, to: { name: 'C' },
        startTime: 1700000020000, endTime: 1700000620000,
      },
    ],
  }] } } },
});
check('journeyFromPlan keeps a short walk',
  shortWalk && shortWalk.legs[0].walk && shortWalk.legs[0].minutes === 1
  && shortWalk.legs[0].meters === 40,
  JSON.stringify(shortWalk && shortWalk.legs && shortWalk.legs[0]));
const futarGeom = Lib.journeyFromPlan({
  data: { entry: { plan: { itineraries: [{
    duration: 780, walkTime: 0, transfers: 0, startTime: 1700000000000,
    legs: [{
      mode: 'SUBWAY', duration: 780000, distance: 4000, routeShortName: 'M4',
      routeColor: '4CA22F',
      from: { name: 'Keleti', lat: 47.500114, lon: 19.080723 },
      to: { name: 'Kelenfold', lat: 47.464516, lon: 19.019617 },
      startTime: 1700000000000, endTime: 1700000780000,
      legGeometry: { points: 'uj|`HoumsBl}Ez|J', length: 2 },
    }],
  }] } } },
});
check('journeyFromPlan keeps FUTAR legGeometry and stop coords',
  Array.isArray(futarGeom && futarGeom.legs[0].shape) && futarGeom.legs[0].shape.length >= 2
  && futarGeom.legs[0].fromLat === 47.500114 && futarGeom.legs[0].toLon === 19.019617,
  JSON.stringify(futarGeom && futarGeom.legs[0] && {
    n: futarGeom.legs[0].shape && futarGeom.legs[0].shape.length,
    first: futarGeom.legs[0].shape && futarGeom.legs[0].shape[0],
    fromLat: futarGeom.legs[0].fromLat,
  }));
const futarSegs = Lib.journeyMapSegments(futarGeom, {}, {});
check('FUTAR planner map has a Hungary segment',
  futarSegs.length >= 1 && futarSegs[0].shape[0][0] > 45 && futarSegs[0].shape[0][0] < 49,
  JSON.stringify(futarSegs[0] && futarSegs[0].shape.slice(0, 2)));
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
check('journeyFromMotis keeps a short street walk', (() => {
  const j = Lib.journeyFromMotis({
    itineraries: [{
      duration: 600,
      transfers: 0,
      startTime: '2026-09-22T10:00:00Z',
      legs: [
        { mode: 'WALK', duration: 20, distance: 80, from: { name: 'Kossuth utca' }, to: { name: 'Meg\u00e1ll\u00f3' }, startTime: '2026-09-22T10:00:00Z', endTime: '2026-09-22T10:00:20Z' },
        { mode: 'BUS', duration: 300, routeShortName: '1', from: { name: 'Meg\u00e1ll\u00f3' }, to: { name: 'C\u00e9l' }, startTime: '2026-09-22T10:01:00Z', endTime: '2026-09-22T10:06:00Z' },
      ],
    }],
  });
  return j && j.legs[0].walk && j.legs[0].meters === 80 && j.walkMin >= 1;
})());
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
const hopSoon = new Date(Date.now() + 12 * 60000).toISOString();
const hopSooner = new Date(Date.now() + 28 * 60000).toISOString();
const hopLater = new Date(Date.now() + 40 * 60000).toISOString();
const hopEnd = new Date(Date.now() + 20 * 60000).toISOString();
const hopEnd2 = new Date(Date.now() + 36 * 60000).toISOString();
const motisBoard = Lib.rowsFromMotis({
  itineraries: [
    {
      transfers: 0,
      duration: 480,
      startTime: hopSoon,
      legs: [
        { mode: 'WALK', duration: 60, startTime: hopSoon, endTime: hopSoon },
        {
          mode: 'BUS', duration: 420, routeShortName: 'ZOO', tripId: 'hu-bkk_zoo',
          routeColor: '009EE3', routeTextColor: 'FFFFFF', headsign: 'Fels\u0151-Majl\u00e1th',
          startTime: hopSoon, endTime: hopEnd,
          legGeometry: { points: '_mdryA_`vic@_pR_pR', precision: 6 },
        },
      ],
    },
    {
      transfers: 0,
      duration: 480,
      startTime: hopSooner,
      legs: [{
        mode: 'BUS', duration: 480, routeShortName: '1', tripId: 'hu-bkk_1',
        startTime: hopSooner, endTime: hopEnd2, headsign: 'Kelenf\u00f6ld',
      }],
    },
    {
      transfers: 1,
      duration: 900,
      startTime: hopLater,
      legs: [
        { mode: 'BUS', duration: 300, routeShortName: 'X', startTime: hopLater, endTime: hopLater },
        { mode: 'SUBWAY', duration: 600, routeShortName: 'M4', startTime: hopLater, endTime: hopLater },
      ],
    },
  ],
}, { name: 'Kelenf\u00f6ld' }, 'all', 180);
check('rowsFromMotis keeps two direct hops and drops a transfer',
  motisBoard.length === 2 && motisBoard[0].label === 'ZOO' && motisBoard[1].label === '1'
  && motisBoard[0].dep > 1e9 && motisBoard[0].dep < 1e12
  && motisBoard[0].tripId.indexOf('motis:') === 0
  && Array.isArray(motisBoard[0].shape) && motisBoard[0].shape.length >= 2
  && motisBoard[0].shape[0][0] > 45 && motisBoard[0].shape[0][0] < 49,
  JSON.stringify(motisBoard.map((r) => ({ label: r.label, dep: r.dep, tripId: r.tripId }))));
const mavOnly = Lib.rowsFromMotis({
  itineraries: [{
    transfers: 0, duration: 420, startTime: hopSoon,
    legs: [{
      mode: 'BUS', duration: 420, routeShortName: 'ZOO', startTime: hopSoon, endTime: hopEnd,
    }],
  }],
}, { name: 'X' }, 'mav', 180);
check('rowsFromMotis mav mode drops a bus', mavOnly.length === 0, String(mavOnly.length));
const agramPast = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
const agramPastEnd = new Date(Date.now() - 90 * 60 * 1000).toISOString();
const agramIt = {
  itineraries: [{
    transfers: 0, duration: 18000, startTime: agramPast,
    legs: [{
      mode: 'RAIL', duration: 18000, displayName: '204 AGRAM - T\u00d3PART',
      tripId: '20260922_15:35_hu-mav_35051884_2656.',
      startTime: agramPast, endTime: agramPastEnd,
      legGeometry: { points: '_mdryA_`vic@_pR_pR', precision: 6 },
    }],
  }],
};
const agramDropped = Lib.rowsFromMotis(agramIt, { name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r' }, 'all', 180);
const agramKept = Lib.rowsFromMotis(agramIt, { name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r' }, 'all', 180, {
  now: Math.floor(Date.parse(agramPast) / 1000), keepPast: true,
});
check('rowsFromMotis drops a past AGRAM without keepPast', agramDropped.length === 0);
check('rowsFromMotis keeps AGRAM when anchored to its dep',
  agramKept.length === 1 && /AGRAM/i.test(agramKept[0].label));
let motisFetch = 0;
const origMotisFetch = Lib.transitousFetch;
const origMotisPlace = Lib.transitousPlace;
Lib.transitousPlace = async () => '47.5,19.05';
Lib.transitousFetch = async () => {
  motisFetch += 1;
  return { itineraries: [] };
};
const kept = await Lib.fillTransitousIfEmpty(
  [{ label: '7', dep: Math.floor(Date.now() / 1000) + 120, sched: Math.floor(Date.now() / 1000) + 120 }],
  { name: 'A' }, { name: 'B' }, { minutesAfter: 60, mode: 'all' },
);
check('fillTransitousIfEmpty skips Transitous when rows exist',
  motisFetch === 0 && kept.length === 1 && kept[0].label === '7',
  String(motisFetch));
Lib.transitousFetch = async () => {
  motisFetch += 1;
  return {
    itineraries: [{
      transfers: 0, duration: 420, startTime: hopSoon,
      legs: [{
        mode: 'BUS', duration: 420, routeShortName: 'ZOO', tripId: 'hu-bkk_zoo',
        startTime: hopSoon, endTime: hopEnd, headsign: 'Kelenf\u00f6ld',
        legGeometry: { points: '_mdryA_`vic@_pR_pR', precision: 6 },
      }],
    }],
  };
};
const filled = await Lib.fillTransitousIfEmpty([], { name: 'A' }, { name: 'B' }, { minutesAfter: 180, mode: 'all' });
check('fillTransitousIfEmpty uses MOTIS when the board is empty',
  motisFetch === 1 && filled.length === 1 && filled[0].label === 'ZOO'
  && filled[0].tripId.indexOf('motis:') === 0,
  JSON.stringify(filled.map((r) => r.label)));
Lib.transitousFetch = origMotisFetch;
Lib.transitousPlace = origMotisPlace;
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
  ], { id: 'BKK_005501024', name: 'Kelenf\u00f6ld' }).id === 'at-Railway-x');
check('Transitous keeps Hungarian stops',
  Lib.transitousInHungary({ id: 'hu-mvk_1', name: 'X' })
  && !Lib.transitousInHungary({ id: 'de-db_1', name: 'Berlin' }));
check('Transitous keeps Hungarian addresses without a hu- id',
  Lib.transitousInHungary({ name: 'Kossuth', lat: 47.19, lon: 18.41, type: 'ADDRESS' })
  && !Lib.transitousInHungary({ name: 'Berlin', lat: 52.52, lon: 13.40, type: 'ADDRESS' }));
check('place origins are geo, node, address and place',
  Lib.isPlaceStop({ id: 'geo:47.1,18.4' })
  && Lib.isPlaceStop({ id: 'node/[1]' })
  && Lib.isPlaceStop({ id: 'x', kind: 'address' })
  && !Lib.isPlaceStop({ id: 'BKK_1' }));
const origGeo = Lib.transitousGeocode;
const origSearchStops = Lib.searchStops;
Lib.transitousGeocode = async () => [{
  id: 'hu-mvk_643641', name: 'Miskolci \u00c1llatkert', lat: 48.12, lon: 20.65,
  areas: [{ name: 'Miskolc', adminLevel: 8, unique: true }],
}];
Lib.searchStops = async () => [];
const plannerHits = await Lib.searchPlannerStops('k', '\u00c1llatkert');
check('planner search keeps Transitous when GTFS is empty',
  plannerHits.some((h) => h.id === 'hu-mvk_643641')
  && plannerHits.find((h) => h.id === 'hu-mvk_643641').lat === 48.12
  && plannerHits.find((h) => h.id === 'hu-mvk_643641').label.indexOf('Miskolc') >= 0,
  JSON.stringify(plannerHits.map((h) => h.id)));
Lib.transitousGeocode = async () => [{
  id: 'hu-bkk_CS056215', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s', lat: 47.46, lon: 19.02,
  areas: [{ name: 'Magyarorsz\u00e1g' }],
}];
Lib.searchStops = async () => [{
  id: 'volan_773538_99', name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s',
  label: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s (Vol\u00e1n)',
}];
const mergedHits = await Lib.searchPlannerStops('k', 'Kelenf\u00f6ld');
check('planner search remaps Transitous BKK onto a FUTAR id',
  mergedHits.filter((h) => /kelenf/i.test(h.name || h.label || '')).length === 1
  && mergedHits.some((h) => h.id === 'BKK_CS056215')
  && !mergedHits.some((h) => h.id === 'volan_773538_99')
  && !mergedHits.some((h) => h.id === 'hu-bkk_CS056215')
  && Number(mergedHits.find((h) => h.id === 'BKK_CS056215').lat) === 47.46,
  JSON.stringify(mergedHits.map((h) => ({ id: h.id, lat: h.lat }))));
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
const origVolanIdx = Lib.volanIndex;
const origCityIdx = Lib.cityIndex;
const kelenfoldGtfs = {
  i: 0,
  id: 'volan_773538_99',
  name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s',
  num: '773538',
  op: 0,
  key: Lib.norm('Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s'),
  fold: Lib.fold('Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s'),
};
const mockVolanIdx = {
  s: [],
  t: [],
  stops: [kelenfoldGtfs],
  ops: [{ i: 0, id: 'volan', name: 'Vol\u00e1n' }],
  byNum: new Map([['0:773538', kelenfoldGtfs]]),
  byKey: new Map([['0:' + kelenfoldGtfs.key, kelenfoldGtfs]]),
};
Lib.volanIndex = async () => mockVolanIdx;
Lib.cityIndex = async () => { throw new Error('skip-city'); };
Lib.transitousGeocode = async () => [];
const hopGtfs = await Lib.searchStops('', 'Kelenf\u00f6ld', 'all');
check('hop searchStops all returns Vol\u00e1n GTFS from mock index',
  hopGtfs.some((h) => h.id === 'volan_773538_99'),
  JSON.stringify(hopGtfs.map((h) => h.id)));
Lib.transitousGeocode = async () => [{
  id: 'hu-volanbusz_hkir_773538',
  name: 'Budapest, Kelenf\u00f6ld aut.\u00e1ll.',
  lat: 47.464,
  lon: 19.023,
  areas: [{ name: 'Magyarorsz\u00e1g' }],
}];
const hopMotis = await Lib.searchStops('', 'Kelenf\u00f6ld aut', 'all');
check('hop searchStops remaps Transitous Vol\u00e1n id to GTFS',
  hopMotis.some((h) => h.id === 'volan_773538_99')
  && !hopMotis.some((h) => /^hu-volanbusz_/i.test(h.id)),
  JSON.stringify(hopMotis.map((h) => h.id)));
Lib.searchStops = async () => [];
const plannerRemap = await Lib.searchPlannerStops('', 'Kelenf\u00f6ld aut');
check('planner remaps Transitous Vol\u00e1n id to GTFS',
  plannerRemap.some((h) => h.id === 'volan_773538_99')
  && !plannerRemap.some((h) => /^hu-volanbusz_/i.test(h.id))
  && plannerRemap.find((h) => h.id === 'volan_773538_99').lat === 47.464,
  JSON.stringify(plannerRemap.map((h) => ({ id: h.id, lat: h.lat }))));
Lib.transitousGeocode = async () => Array.from({ length: 20 }, (_, i) => ({
  id: 'hu-bkk_CS' + String(i).padStart(6, '0'),
  name: 'Stop ' + i,
  lat: 47.5,
  lon: 19.0,
  areas: [{ name: 'Magyarorsz\u00e1g' }],
}));
Lib.searchStops = async () => Array.from({ length: 12 }, (_, i) => ({
  id: 'szombathely:x' + i, name: 'City ' + i, city: true, label: 'City ' + i,
})).concat([{
  id: 'volan_773538_99',
  name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s',
  label: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s (Vol\u00e1n)',
}]);
const capped = await Lib.searchPlannerStops('k', 'allomas');
check('planner cap keeps Vol\u00e1n GTFS among Transitous hits',
  capped.some((h) => h.id === 'volan_773538_99') && capped.length <= 24,
  JSON.stringify(capped.map((h) => h.id)));
Lib.transitousGeocode = async () => [{
  type: 'ADDRESS', id: '', name: 'Kossuth Lajos utca', lat: 47.191, lon: 18.411,
  areas: [
    { name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r', adminLevel: 8, unique: true },
    { name: 'Magyarorsz\u00e1g' },
  ],
}];
Lib.searchStops = async () => [];
const streetHits = await Lib.searchPlannerStops('k', 'Kossuth Lajos utca');
check('planner search includes a street address',
  streetHits.some((h) => h.kind === 'address' && /^geo:/.test(h.id)
    && h.label.indexOf('Sz\u00e9kesfeh\u00e9rv\u00e1r') >= 0),
  JSON.stringify(streetHits.map((h) => ({ id: h.id, kind: h.kind, label: h.label }))));
Lib.transitousGeocode = async () => [{
  type: 'PLACE', id: 'node/[1]', name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r', lat: 47.19, lon: 18.41,
  areas: [{ name: 'Magyarorsz\u00e1g' }, { name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r', adminLevel: 8, unique: true }],
}];
const cityHits = await Lib.searchPlannerStops('k', 'Sz\u00e9kesfeh\u00e9rv\u00e1r');
check('planner search includes a city place',
  cityHits.some((h) => h.kind === 'place' && h.id === 'node/[1]'),
  JSON.stringify(cityHits.map((h) => ({ id: h.id, kind: h.kind }))));
check('plannerHitLabel tags an address',
  Lib.plannerHitLabel({ kind: 'address', name: 'Kossuth', label: 'Kossuth (X)' }, 'hu').indexOf('c\u00edm') >= 0);
Lib.searchStops = origSearchStops;
Lib.volanIndex = origVolanIdx;
Lib.cityIndex = origCityIdx;
Lib.transitousGeocode = origGeo;
let seenStopOpts = null;
Lib.searchStops = async function (apiKey, q, mode, city, opts) {
  seenStopOpts = opts;
  return [];
};
Lib.transitousGeocode = async () => [{
  id: 'hu-bkk_1', name: 'X', lat: 47.5, lon: 19.08,
  areas: [{ name: 'Magyarorsz\u00e1g' }],
}];
await Lib.searchPlannerStops('k', 'X');
check('planner search skips a second Transitous volan geocode',
  seenStopOpts && seenStopOpts.skipTransitousVolan === true,
  JSON.stringify(seenStopOpts));
Lib.searchStops = origSearchStops;
Lib.transitousGeocode = origGeo;
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
const origFillMotis = Lib.fillTransitousIfEmpty;
Lib.fillTransitousIfEmpty = async (rows) => rows || [];
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
Lib.fillTransitousIfEmpty = origFillMotis;

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
const scaledWrong = Lib.decodePolyline('_p~iF~ps|U');
check('string polyline still uses Google 1e5',
  Array.isArray(scaledWrong) && scaledWrong.length >= 1
  && scaledWrong[0][0] > 38 && scaledWrong[0][0] < 39
  && scaledWrong[0][1] < -120,
  JSON.stringify(scaledWrong.slice(0, 1)));
check('decodePolyline retries precision 6 when 1e5 is off the globe',
  (() => {
    const recovered = Lib.decodePolyline('_mdryA_`vic@_pR_pR');
    return Array.isArray(recovered) && recovered.length >= 2
      && recovered[0][0] > 46 && recovered[0][0] < 49
      && recovered[0][1] > 16 && recovered[0][1] < 23;
  })());
check('FUTAR {points,length} is not retried as MOTIS precision 6',
  (() => {
    const pts = Lib.decodePolyline({ points: '_mdryA_`vic@_pR_pR', length: 2 });
    return !pts.length;
  })());
check('FUTAR Google object stays in Hungary at 1e5',
  (() => {
    const pts = Lib.decodePolyline({ points: 'uj|`HoumsBl}Ez|J', length: 2 });
    return Array.isArray(pts) && pts.length >= 2
      && pts[0][0] > 46 && pts[0][0] < 49
      && pts[0][1] > 16 && pts[0][1] < 23;
  })());
check('finiteLatLon rejects off-globe MOTIS-as-1e5 coords',
  Lib.finiteLatLon(475.18, 190.78) == null
  && Lib.finiteLatLon(47.5, 19.05) != null);
check('mapGpsLatLon drops null-island and Gulf of Guinea',
  Lib.mapGpsLatLon(0, 0) == null
  && Lib.mapGpsLatLon('', '') == null
  && Lib.mapGpsLatLon(null, null) == null
  && Lib.mapGpsLatLon(0.47, 0.19) == null
  && Lib.mapGpsLatLon(47.5, 19.05) != null
  && Lib.mapGpsLatLon(48.2, 16.37) != null);
const loc = Lib.vehicleLoc({ location: { lat: 47.5, lon: 19.05 } });
check('vehicleLoc reads BKK location', loc.lat === 47.5 && loc.lon === 19.05, JSON.stringify(loc));
const locZero = Lib.vehicleLoc({ location: { lat: '', lon: '' } });
check('vehicleLoc empty strings are not GPS',
  !Number.isFinite(locZero.lat) && !Number.isFinite(locZero.lon), JSON.stringify(locZero));
{
  const painted = Lib.asRow({
    label: '7', lat: 0, lon: 0, dep: Math.floor(Date.now() / 1000) + 120, rawType: 'BUS',
  }, 'Csepel', 'hu');
  check('asRow 0,0 is not live GPS',
    painted.hasGps === false && painted.lat == null && painted.lon == null);
}
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
let vehicleOpened = 0;
mapped._openVehicleMap = () => { vehicleOpened += 1; };
const tr = mapped.shadowRoot.querySelector('tr[data-row-index]');
if (tr) tr.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('row click opens the vehicle map', vehicleOpened === 1, String(vehicleOpened));
const btn = mapped.shadowRoot.querySelector('button.map-open');
vehicleOpened = 0;
if (btn) btn.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('map button click opens the vehicle map', vehicleOpened === 1, String(vehicleOpened));

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
check('planner does not auto-refresh after a trip is loaded',
  plan._shouldPoll() === false
  && plan._tick === null
  && card._shouldPoll() === true,
  JSON.stringify({ planPoll: plan._shouldPoll(), planTick: plan._tick, hopPoll: card._shouldPoll() }));
check('planner skips reconnect reload while a trip is on screen',
  (() => {
    plan._journey = { durationMin: 26, legs: [{ walk: false, label: 'M4' }] };
    plan._rows = [{ label: '1' }];
    const skip = plan._shouldReloadOnConnect() === false;
    plan._journey = null;
    plan._rows = [];
    return skip && plan._shouldReloadOnConnect() === true;
  })());
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
plan._err = '';
plan._config = Object.assign({}, plan._config, {
  stopId: 'geo:47.19,18.41', destKey: 'cel', destName: 'C\u00e9l',
});
plan._journey = shaped;
plan._paint();
let journeyMapOpened = 0;
plan._openJourneyMap = () => { journeyMapOpened += 1; };
{
  const pmapNow = plan.shadowRoot.querySelector('#pmap');
  if (pmapNow) pmapNow.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
}
check('empty planner shows the transfer journey',
  plan.shadowRoot.innerHTML.includes('\u00c1tsz\u00e1ll\u00e1ssal')
  && plan.shadowRoot.innerHTML.includes('Gyalogl\u00e1s')
  && plan.shadowRoot.innerHTML.includes('>81<')
  && plan.shadowRoot.innerHTML.includes('background:#009EE3')
  && plan.shadowRoot.innerHTML.includes('V\u00e1rakoz\u00e1s 2 perc'));
check('empty planner does not show no-departures over a journey',
  !/Nincs k\u00f6zelg\u0151 indul\u00e1s/.test(plan.shadowRoot.innerHTML)
  && !/No upcoming departures/.test(plan.shadowRoot.innerHTML));
check('empty planner journey paint includes the map button',
  plan.shadowRoot.innerHTML.includes('id="pmap"')
  && plan.shadowRoot.innerHTML.includes('T\u00e9rk\u00e9p'));
check('empty planner transfer summary includes an inline map',
  plan.shadowRoot.innerHTML.includes('id="pjmap"')
  && !!plan.shadowRoot.querySelector('#pjmap')
  && plan.shadowRoot.querySelector('#pjmap').classList.contains('j-map'));
check('planner source mounts an inline journey map',
  src.includes('_mountPlannerInlineMap') && src.includes('id="pjmap"'));
check('planner tears down the inline map before wiping the body',
  src.indexOf('this._teardownPlannerInlineMap();') < src.indexOf('super._paint();')
  && src.includes('this._plannerMap')
  && !/_closeVehicleMap[\s\S]{0,800}_plannerMap/.test(src));
{
  const noGeom = Lib.journeyMapSegments(
    { legs: [{ walk: true, from: 'A', to: 'B', minutes: 5 }] },
    { stopLat: 48.1, stopLon: 20.7 },
    { destLat: 47.5, destLon: 19.04 },
  );
  check('city-rail journey still maps origin to dest',
    noGeom.length === 1 && noGeom[0].shape[0][0] === 48.1 && noGeom[0].shape[1][0] === 47.5);
}
{
  const twoRide = {
    legs: [
      {
        walk: false, label: 'M3', color: '005CA5',
        shape: [[47.53, 19.07], [47.49, 19.06]],
      },
      {
        walk: false, label: 'M4', color: '4CA22F',
        fromLat: 47.49, fromLon: 19.06, toLat: 47.46, toLon: 19.02,
      },
    ],
  };
  const twoSegs = Lib.journeyMapSegments(twoRide, {}, {});
  check('two ride legs both become map segments',
    twoSegs.length === 2
    && twoSegs[0].shape[0][0] === 47.53
    && twoSegs[1].shape[1][0] === 47.46
    && twoSegs[0].color.toLowerCase() === '#005ca5'
    && twoSegs[1].color.toLowerCase() === '#4ca22f',
    JSON.stringify(twoSegs.map((s) => ({ n: s.shape.length, color: s.color, last: s.shape[s.shape.length - 1] }))));
  const lats = twoSegs.flatMap((s) => s.shape.map((p) => p[0]));
  check('two-vehicle bounds cover both rides',
    Math.min(...lats) === 47.46 && Math.max(...lats) === 47.53);
}
{
  const used = {};
  const a = Lib.mapRouteColor({ label: '7', color: '009EE3' }, used);
  const b = Lib.mapRouteColor({ label: '110', color: '009EE3' }, used);
  const c = Lib.mapRouteColor({ label: '7', color: '009EE3' }, used);
  check('same hex on two labels becomes two colors',
    a === c && a !== b && a === '#009ee3',
    JSON.stringify({ a, b, c }));
}
{
  const click = src.slice(src.indexOf('  _onRowClick(ev)'), src.indexOf('  _mapTypeLabel'));
  check('hop row click opens a single-vehicle map',
    click.includes('this._openVehicleMap(row)')
    && !click.includes('_openBoardMap'));
}
check('openVehicleMap is not aliased to a board overlay',
  !/async _openVehicleMap\(row\) \{\s*return this\._openBoardMap/.test(src));
check('combined board overlay is gone',
  !src.includes('async _openBoardMap')
  && !src.includes('boardMapLayers')
  && !src.includes('_visibleMapRows')
  && !src.includes('mapAllTrips'));
{
  const chained = {
    legs: [
      { walk: false, label: '81', fromLat: 47.50, fromLon: 19.08, toLat: 47.51, toLon: 19.09 },
      { walk: false, label: 'M1' },
    ],
  };
  Lib.stitchJourneyLegCoords(chained);
  check('stitch copies the previous ride end onto the next leg',
    chained.legs[1].fromLat === 47.51 && chained.legs[1].fromLon === 19.09);
}
{
  const origShape = Lib.motisTripShape;
  Lib.motisTripShape = async (id) => (
    String(id).indexOf('m4') >= 0 ? [[47.49, 19.06], [47.464, 19.02]] : []
  );
  const filled = {
    legs: [
      { walk: false, label: 'M3', shape: [[47.53, 19.07], [47.49, 19.06]] },
      { walk: false, label: 'M4', tripId: 'hu-bkk_m4' },
    ],
  };
  await Lib.fillJourneyShapes(filled);
  Lib.motisTripShape = origShape;
  check('fillJourneyShapes loads the second vehicle polyline',
    Lib.validMapShape(filled.legs[1].shape).length >= 2
    && filled.legs[1].shape[1][0] === 47.464);
}
check('journey map scales to every vehicle SVG path',
  src.includes('_fitJourneyView')
  && src.includes('preferCanvas: false')
  && /L\.map\(canvas, \{ scrollWheelZoom: true, preferCanvas: false \}/.test(src));
const pmap = plan.shadowRoot.querySelector('#pmap');
if (pmap) pmap.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('pmap click opens the journey map', journeyMapOpened >= 1, String(journeyMapOpened));
plan._rows = [{
  type: 'BUS', icon: 'mdi:bus', label: '7', headsign: 'Csepel',
  depTs: now + 180, schedTs: now + 180, attime: '10:03', predicted_attime: '10:03',
  color: '009EE3', textcolor: 'ffffff', hasLocation: true, tripId: 'BKK_C007',
  wheelchair: false, bikesAllowed: false, booking: false, delay: 0, travelMin: null,
}];
plan._journey = futarGeom;
plan._paint();
check('planner with BKK departures still has the map button',
  plan.shadowRoot.innerHTML.includes('id="pmap"')
  && plan.shadowRoot.innerHTML.includes('T\u00e9rk\u00e9p')
  && plan.shadowRoot.querySelector('.j-map-only'));
check('planner with BKK departures does not show the transfer itinerary',
  !plan.shadowRoot.innerHTML.includes('\u00c1tsz\u00e1ll\u00e1ssal'));
check('planner with BKK departures has no inline transfer map',
  !plan.shadowRoot.querySelector('#pjmap'));
journeyMapOpened = 0;
let vehicleMapOpened = 0;
plan._openVehicleMap = () => { vehicleMapOpened += 1; };
const pmapBoard = plan.shadowRoot.querySelector('#pmap');
if (pmapBoard) pmapBoard.dispatchEvent(new Event('click', { bubbles: true, composed: true }));
check('planner BKK-board map button opens a single-vehicle map', vehicleMapOpened === 1, String(vehicleMapOpened));
{
  const mixed = Lib.journeyFromMotis({
    itineraries: [
      {
        duration: 2760, transfers: 0, startTime: '2026-09-23T06:02:00Z',
        legs: [
          { mode: 'WALK', duration: 600, from: { name: 'START' }, to: { name: 'Zugló' } },
          { mode: 'TRAM', duration: 1920, routeShortName: '1', from: { name: 'Zugló' }, to: { name: 'Kelenföld' } },
        ],
      },
      {
        duration: 1560, transfers: 1, startTime: '2026-09-23T06:11:00Z',
        legs: [
          { mode: 'WALK', duration: 300, from: { name: 'START' }, to: { name: 'Bosnyák tér' } },
          {
            mode: 'BUS', duration: 300, routeShortName: '7E',
            from: { name: 'Bosnyák tér' }, to: { name: 'Keleti pályaudvar M' },
            startTime: '2026-09-23T06:11:00Z', endTime: '2026-09-23T06:16:00Z',
          },
          { mode: 'WALK', duration: 120, from: { name: 'Keleti pályaudvar M' }, to: { name: 'Keleti pályaudvar' } },
          {
            mode: 'SUBWAY', duration: 780, routeShortName: 'M4',
            from: { name: 'Keleti pályaudvar' }, to: { name: 'Kelenföld vasútállomás' },
            startTime: '2026-09-23T06:18:00Z', endTime: '2026-09-23T06:31:00Z',
          },
        ],
      },
    ],
  });
  check('MOTIS keeps 7E+M4 over the slower 1 tram',
    mixed && mixed.durationMin === 26 && mixed.transfers === 1
    && mixed.legs.some((leg) => !leg.walk && leg.label === 'M4'),
    JSON.stringify(mixed && { durationMin: mixed.durationMin, labels: mixed.legs.map((l) => l.label) }));
  check('faster metro transfer beats a 32 min direct',
    Lib.journeyBeatsBoard(mixed, [{ label: '1', travelMin: 32 }]));
  check('slower transfer does not replace a 20 min direct',
    !Lib.journeyBeatsBoard(mixed, [{ label: '108E', travelMin: 20 }]));
  check('direct-only itinerary stays off the board',
    !Lib.journeyBeatsBoard(futarGeom, [{ label: '7', travelMin: null }]));
}
{
  plan._config = Object.assign({}, plan._config, {
    stopId: 'BKK_F02831', stopName: 'Bosnyák tér', destKey: 'kelenfold', destName: 'Kelenföld',
  });
  plan._rows = [{
    type: 'TRAM', icon: 'mdi:tram', label: '1', headsign: 'Kelenföld',
    depTs: now + 180, schedTs: now + 180, attime: '10:03', predicted_attime: '10:03',
    color: 'FFD800', textcolor: '000000', hasLocation: true, tripId: 'BKK_1',
    wheelchair: false, bikesAllowed: false, booking: false, delay: 0, travelMin: 32,
  }];
  plan._journey = {
    durationMin: 26, walkMin: 8, waitMin: 0, transfers: 1,
    legs: [
      { walk: true, minutes: 5, meters: 400, from: 'Bosnyák tér', to: 'Bosnyák tér' },
      { walk: false, label: '7E', minutes: 5, color: '009EE3', text: 'ffffff', from: 'Bosnyák tér', to: 'Keleti' },
      { walk: true, minutes: 2, meters: 120, from: 'Keleti M', to: 'Keleti' },
      { walk: false, label: 'M4', minutes: 13, color: '4CA22F', text: 'ffffff', from: 'Keleti', to: 'Kelenföld' },
    ],
  };
  plan._paint();
  const html = plan.shadowRoot.innerHTML;
  check('faster metro itinerary is shown above direct board rows',
    html.includes('\u00c1tsz\u00e1ll\u00e1ssal') && html.includes('M4') && html.includes('7E')
    && !plan.shadowRoot.querySelector('.j-map-only'),
    html.slice(0, 400));
}
check('transitous journey asks for 12 itineraries',
  /numItineraries: '12'/.test(src));
{
  const planJFromBoard = [];
  const origPJ = Lib.planJourney;
  Lib.planJourney = async function () {
    planJFromBoard.push(1);
    return {
      durationMin: 10, walkMin: 0, waitMin: 0, transfers: 0,
      legs: [{ walk: false, minutes: 10, label: '7', from: 'A', to: 'B' }],
    };
  };
  plan._journey = null;
  plan._journeyKey = '';
  plan._journeyLoading = false;
  plan._considerJourney();
  Lib.planJourney = origPJ;
  plan._journeyKey = '';
  plan._journeyLoading = false;
  check('planner still plans a map journey when BKK rows exist',
    planJFromBoard.length >= 1,
    String(planJFromBoard.length));
}
check('planner favorite chips include a Volan station',
  planHtml.includes('N\u00e9pliget'));
check('planner favorite chips include a MAV station',
  planHtml.includes('Sz\u00e9kesfeh\u00e9rv\u00e1r') || planHtml.includes('Budapest-Kelenf'));
check('planner favorites wrap in one row with feed marks',
  plan.shadowRoot.querySelectorAll('#pfav .suggest-kind').length === 0
  && plan.shadowRoot.querySelectorAll('#pfav .suggest-feed').length >= 4
  && plan.shadowRoot.querySelectorAll('#pfav .suggest-stop').length <= 5
  && /\.suggest \{[^}]*flex-direction:\s*row/.test(src));
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
check('destHitsForQuery merges hyphen MAV key onto Keleti pályaudvar', (() => {
  const hits = Lib.destHitsForQuery(
    [
      { key: 'budapest-keleti', name: 'Budapest-Keleti', id: 'BKK_005510017' },
      { key: 'keleti', name: 'Keleti pályaudvar', id: 'BKK_CSF01131' },
    ],
    'keleti',
    [],
  );
  return hits.length === 1 && hits[0].id === 'BKK_CSF01131';
})());
check('nameEq matches a folded dest key to Kelenföld vasútállomás',
  Lib.nameEq('Kelenföld vasútállomás', 'kelenfold')
  && Lib.nameEq('Budapest-Kelenföld', 'Kelenföld vasútállomás')
  && Lib.nameEq('Keleti pályaudvar', 'keleti')
  && !Lib.nameEq('Blaha Lujza tér', 'Deák Ferenc tér'));
check('destHitsForQuery merges city-prefix Kelenföld onto the short name', (() => {
  const hits = Lib.destHitsForQuery(
    [],
    'Kelenföld',
    [
      { id: 'volan_773538_99', name: 'Budapest, Kelenföld vasútállomás' },
      { id: 'BKK_005501024', name: 'Kelenföld vasútállomás' },
    ],
  );
  return hits.length === 1 && hits[0].id === 'BKK_005501024';
})());
{
  const ormezo = Lib.collapseSameNameStops([
    { id: 'BKK_CS007896', name: 'Budapest-Kelenföld', lat: 47.4637, lon: 19.0212 },
    { id: 'BKK_CS007896', name: 'Budapest, Kelenföld vá. (Őrmező)', lat: 47.4648, lon: 19.0182 },
  ]);
  check('Őrmező bay does not split Kelenföld into two hits',
    ormezo.length === 1 && ormezo[0].id === 'BKK_CS007896',
    JSON.stringify(ormezo.map((h) => ({ id: h.id, name: h.name }))));
}
check('hyphen MAV name shares a place key with Kelenföld vasútállomás',
  Lib.placeNameKey({ name: 'Budapest-Kelenföld' }) === 'kelenfold'
  && Lib.placeNameKey({ name: 'Kelenföld vasútállomás' }) === 'kelenfold'
  && Lib.placeNameKey({ name: 'Budapest, Kelenföld vá. (Őrmező)' }) === 'kelenfold');
{
  const hyphen = Lib.collapseSameNameStops([
    { id: 'at-Railway-x', name: 'Budapest-Kelenföld', lat: 47.4637, lon: 19.0212 },
    { id: 'hu-bkk_CS056215', name: 'Kelenföld vasútállomás', lat: 47.4643, lon: 19.0197 },
    { id: 'BKK_005501024', name: 'Budapest, Kelenföld vasútállomás', lat: 47.464, lon: 19.022 },
    { id: 'BKK_CS007896', name: 'Kelenföld vasútállomás', lat: 47.4643, lon: 19.0197 },
    { id: 'way/[1]', name: 'Budapest-Kelenföld', lat: 47.4654, lon: 19.0216, kind: 'place' },
  ]);
  check('Budapest-Kelenföld railway names collapse onto the BKK parent',
    hyphen.length === 1 && hyphen[0].id === 'BKK_CS007896',
    JSON.stringify(hyphen.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const keleti = Lib.collapseSameNameStops([
    { id: 'BKK_005510017', name: 'Budapest-Keleti', lat: 47.500, lon: 19.084 },
    { id: 'BKK_CSF01131', name: 'Keleti pályaudvar', lat: 47.500, lon: 19.084 },
  ]);
  check('Budapest-Keleti merges onto the BKK Keleti parent',
    keleti.length === 1 && keleti[0].id === 'BKK_CSF01131',
    JSON.stringify(keleti.map((h) => h.id)));
}
{
  const osm = Lib.collapseSameNameStops([
    { id: 'way/[1]', name: 'Blaha Lujza tér', lat: 47.4965, lon: 19.0700, kind: 'place' },
    { id: 'hu-bkk_CSF01291', name: 'Blaha Lujza tér', lat: 47.4965, lon: 19.0700 },
  ]);
  check('OSM place does not beat a Transitous BKK stop',
    osm.length === 1 && osm[0].id === 'hu-bkk_CSF01291',
    JSON.stringify(osm.map((h) => h.id)));
}
{
  const stopJunk = Lib.groupStops([
    { id: 'STOP_773064_1-F01165', name: 'Blaha Lujza tér M', locationType: 1, lat: 47.4968, lon: 19.0700 },
    { id: 'BKK_F01165', name: 'Blaha Lujza tér M', lat: 47.4968, lon: 19.0700 },
    { id: 'BKK_CSF01116', name: 'Blaha Lujza tér', locationType: 1, lat: 47.4968, lon: 19.0700 },
  ]);
  check('groupStops prefers BKK parent over STOP_ area',
    stopJunk.length === 1 && stopJunk[0].id === 'BKK_CSF01116',
    JSON.stringify(stopJunk.map((h) => h.id)));
}
{
  const collapsedStop = Lib.collapseSameNameStops([
    { id: 'STOP_773541_2-F02192', name: 'Móricz Zsigmond körtér M', lat: 47.4778, lon: 19.047 },
    { id: 'BKK_CS056221', name: 'Móricz Zsigmond körtér', lat: 47.4778, lon: 19.047 },
  ]);
  check('STOP_ id does not beat BKK CS in collapse',
    collapsedStop.length === 1 && collapsedStop[0].id === 'BKK_CS056221',
    JSON.stringify(collapsedStop.map((h) => h.id)));
}
{
  const kind = Lib.collapseSameNameStops([
    { id: 'BKK_CS007896', name: 'Kelenföld vasútállomás', lat: 47.464, lon: 19.02 },
    { id: 'way/[1]', name: 'Budapest-Kelenföld', lat: 47.465, lon: 19.021, kind: 'place' },
  ]);
  check('OSM place kind does not stick on a BKK stop',
    kind.length === 1 && kind[0].id === 'BKK_CS007896' && kind[0].kind !== 'place',
    JSON.stringify(kind));
}
{
  const mixed = Lib.collapseSameNameStops([
    { id: 'volan_1', name: 'Budapest, Kelenföld aut.áll.', lat: 47.464, lon: 19.023 },
    { id: 'BKK_005501024', name: 'Kelenföld vasútállomás', lat: 47.465, lon: 19.022 },
  ]);
  check('coach station and railway station stay two hits',
    mixed.length === 2
    && mixed.some((h) => /aut/i.test(h.name))
    && mixed.some((h) => h.id === 'BKK_005501024'),
    JSON.stringify(mixed.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const cities = Lib.collapseSameNameStops([
    { id: 'miskolc:1', name: 'Kossuth tér', label: 'Kossuth tér (Miskolc)', lat: 48.1, lon: 20.78, city: true },
    { id: 'pecs:1', name: 'Kossuth tér', label: 'Kossuth tér (Pécs)', lat: 46.07, lon: 18.23, city: true },
  ]);
  check('same stop name in two cities stays two hits',
    cities.length === 2,
    JSON.stringify(cities.map((h) => h.label || h.name)));
}
{
  const near = Lib.collapseSameNameStops([
    { id: 'BKK_A', name: 'Blaha Lujza tér', lat: 47.4965, lon: 19.0700 },
    { id: 'hu-bkk_B', name: 'Blaha Lujza tér', lat: 47.4976, lon: 19.0700 },
  ]);
  check('same name 150m apart collapses to one hit',
    near.length === 1 && near[0].id === 'BKK_A' && Number(near[0].lat) === 47.4965,
    JSON.stringify(near));
}
{
  const far = Lib.collapseSameNameStops([
    { id: 'BKK_A', name: 'Kossuth Lajos utca', lat: 47.500, lon: 19.050 },
    { id: 'BKK_B', name: 'Kossuth Lajos utca', lat: 47.518, lon: 19.050 },
  ]);
  check('same name 2km apart stays two hits',
    far.length === 2,
    JSON.stringify(far.map((h) => h.id)));
}
{
  const district = Lib.collapseSameNameStops([
    {
      id: 'BKK_CS007896', name: 'Kelenföld vasútállomás',
      label: 'Kelenföld vasútállomás (Budapest)', lat: 47.4637, lon: 19.0212,
    },
    {
      id: 'node/[269800455]', name: 'Kelenföld',
      label: 'Kelenföld (Budapest)', lat: 47.4652, lon: 19.0411, kind: 'place',
    },
  ]);
  check('OSM district Kelenföld merges onto the railway station',
    district.length === 1
    && district[0].id === 'BKK_CS007896'
    && /vasútállomás/i.test(district[0].name)
    && district[0].kind !== 'place',
    JSON.stringify(district.map((h) => ({ id: h.id, name: h.name, kind: h.kind }))));
}
check('Nyugati pu and train-station aliases share the railway key',
  Lib.placeNameKey({ name: 'Budapest-Nyugati pu' }) === 'nyugati'
  && Lib.placeNameKey({ name: 'Nyugati pályaudvar' }) === 'nyugati'
  && Lib.placeNameKey({ name: 'Budapest Keleti train station' }) === 'keleti');
check('Kőbánya-Kispest hyphen is not treated as a city prefix',
  Lib.placeNameKey({ name: 'Kőbánya-Kispest' }) === 'kobanya kispest'
  && Lib.placeNameKey({ name: 'Budapest, Kőbánya-Kispest' }) === 'kobanya kispest'
  && Lib.placeNameKey({ name: 'Corvin-negyed' }) === 'corvin negyed');
check('coach station aliases share one key',
  Lib.placeNameKey({ name: 'Budapest, Népliget aut.áll.' }) === 'nepliget aut all'
  && Lib.placeNameKey({ name: 'Budapest, Népliget autóbusz-pályaudvar' }) === 'nepliget aut all'
  && Lib.placeNameKey({ name: 'Budapest Népliget bus station' }) === 'nepliget aut all'
  && Lib.placeNameKey({ name: 'Székesfehérvár, autóbusz-állomás' }) === 'aut all');
{
  const nepliget = Lib.collapseSameNameStops([
    { id: 'hu-volanbusz_hkir_773521', name: 'Budapest, Népliget autóbusz-pályaudvar', lat: 47.47447, lon: 19.09861 },
    { id: 'al-ShowMeBus_x', name: 'Budapest Népliget bus station', lat: 47.47437, lon: 19.09855 },
    { id: 'sk-Regiojet_S-1', name: 'Budapešť, Népliget aut.áll.', lat: 47.47451, lon: 19.09858 },
  ]);
  check('Népliget coach aliases collapse onto the Hungarian Volán stop',
    nepliget.length === 1 && nepliget[0].id === 'hu-volanbusz_hkir_773521',
    JSON.stringify(nepliget.map((h) => h.id)));
}
{
  const pu = Lib.collapseSameNameStops([
    { id: 'BKK_CSF00936', name: 'Nyugati pályaudvar', lat: 47.51077, lon: 19.05611 },
    { id: 'sk-zsr_5500728', name: 'Budapest-Nyugati pu', lat: 47.51250, lon: 19.05893 },
  ]);
  check('Nyugati pu merges onto the BKK parent',
    pu.length === 1 && pu[0].id === 'BKK_CSF00936',
    JSON.stringify(pu.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const ors = Lib.collapseSameNameStops([
    { id: 'BKK_CSF01580', name: 'Örs vezér tere', lat: 47.503586, lon: 19.137192 },
    { id: 'BKK_008020', name: 'Örs vezér tere M+H', lat: 47.50093, lon: 19.136307 },
  ]);
  check('Örs vezér tere M+H merges onto the parent',
    ors.length === 1 && ors[0].id === 'BKK_CSF01580',
    JSON.stringify(ors.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const kk = Lib.collapseSameNameStops([
    { id: 'ua-ukrzaliznytsya_5500005', name: 'Kőbánya-Kispest', lat: 47.46365, lon: 19.14967 },
    { id: 'hu-volanbusz_hkir_773528', name: 'Budapest, Kőbánya-Kispest', lat: 47.46329, lon: 19.14758 },
    { id: 'BKK_CSF01552', name: 'Kőbánya-Kispest', lat: 47.4634, lon: 19.1492 },
  ]);
  check('Kőbánya-Kispest railway names collapse onto the BKK parent',
    kk.length === 1 && kk[0].id === 'BKK_CSF01552',
    JSON.stringify(kk.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const shops = Lib.collapseSameNameStops([
    { id: 'BKK_CS007896', name: 'Kelenföld vasútállomás', lat: 47.4637, lon: 19.0212 },
    { id: 'node/[1]', name: 'Borháló Kelenföld', lat: 47.4507, lon: 19.0401, kind: 'place' },
    { id: 'node/[2]', name: 'Elektro Kelenföld', lat: 47.4653, lon: 19.0246, kind: 'place' },
  ]);
  check('OSM shop places drop when a real stop exists',
    shops.length === 1 && shops[0].id === 'BKK_CS007896',
    JSON.stringify(shops.map((h) => ({ id: h.id, name: h.name }))));
}
{
  const czech = Lib.collapseSameNameStops([
    { id: 'hu-volanbusz_hkir_773538', name: 'Budapest, Kelenföld aut.áll.', lat: 47.46381, lon: 19.02275 },
    { id: 'cz-JDF-merged_JDFS-31574', name: 'Budapest,Kelenföld aut.áll.', lat: 47.47451, lon: 19.09858 },
  ]);
  check('foreign coach feed merges onto the Hungarian aut.áll.',
    czech.length === 1 && czech[0].id === 'hu-volanbusz_hkir_773538',
    JSON.stringify(czech.map((h) => h.id)));
}
check('elvira result parser reads response.departures',
  Lib.elviraRowsFromResult({ context: {}, response: { departures: [{ label: 'TOKAJ' }] } }).length === 1);
check('elvira result parser reads REST service_response',
  Lib.elviraRowsFromResult({ service_response: { departures: [{ label: 'HERNÁD' }, { label: 'TOKAJ' }] } }).length === 2);
check('elvira result parser reads top-level departures',
  Lib.elviraRowsFromResult({ departures: [{ label: 'IC' }] })[0].label === 'IC');
{
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep: dep, sched: dep,
  }, extra);
  check('numbered through express is dropped from the hop merge',
    Lib.mergeVolanRows([], [rail({
      label: '16907', trainNumber: '16907', booking: true, tripId: 'elvira:3640191',
    })], 12).length === 0);
  check('numbered through személy is dropped from the hop merge',
    Lib.mergeVolanRows([], [rail({
      label: '9792', trainNumber: '9792', booking: false, tripId: 'elvira:2888904',
    })], 12).length === 0);
  check('another numbered személy is dropped the same way',
    Lib.mergeVolanRows([], [rail({
      label: '9704', trainNumber: '9704', booking: false, tripId: 'elvira:2888905',
    })], 12).length === 0);
  check('generic EX plus a long train number is dropped',
    Lib.mergeVolanRows([], [rail({
      label: 'EX', trainNumber: '16907', booking: true, tripId: 'elvira:1',
    })], 12).length === 0);
  check('generic EN plus a long train number is dropped',
    Lib.mergeVolanRows([], [rail({
      label: 'EN', trainNumber: '4240', booking: true, tripId: 'elvira:2',
    })], 12).length === 0);
  check('generic IC keeps a named overlay slot',
    Lib.mergeVolanRows([], [rail({
      label: 'IC', trainNumber: '852', booking: true, tripId: 'elvira:1',
    })], 12)[0].label === 'IC');
  check('named IC stays on the hop',
    Lib.mergeVolanRows([], [rail({
      label: 'BAKONY', trainNumber: '904', booking: true, tripId: 'elvira:2931213',
    })], 12)[0].label === 'BAKONY');
  check('suburban Z30 stays on the hop',
    Lib.mergeVolanRows([], [rail({
      label: 'Z30', trainNumber: '4542', booking: false, tripId: 'elvira:2887496',
    })], 12)[0].label === 'Z30');
  check('city bus number is not treated as a rail badge',
    Lib.mergeVolanRows([], [{
      label: '7', vehicle: 'bus', dep: dep, sched: dep, tripId: 'BKK_X',
    }], 12)[0].label === '7');
}
{
  const g = Lib.applyRailBadge({
    label: 'GÖCSEJ', color: '#4477aa', text: '#ffffff', vehicle: 'rail',
  });
  const t = Lib.applyRailBadge({
    label: 'TÓPART', color: '#4477aa', text: '#ffffff', vehicle: 'rail',
  });
  const s = Lib.applyRailBadge({
    label: 'S', color: '#4477aa', text: '#ffffff', vehicle: 'rail',
  });
  check('named IC Göcsej gets IC badge colours',
    String(g.color).replace('#', '').toUpperCase() === '2E5EA8'
    && String(g.text).replace('#', '').toUpperCase() === 'FFFFFF');
  check('named IC Tópart gets IC badge colours',
    String(t.color).replace('#', '').toUpperCase() === '2E5EA8');
  check('generic S becomes Sz with személy colours',
    s.label === 'Sz'
    && String(s.color).replace('#', '').toUpperCase() === '5C6B7A');
  {
    const card = document.createElement('hungarian-transport-card');
    card.setConfig({ language: 'hu' });
    const gHtml = card._rowHtml(g, 'hu', 0);
    const tHtml = card._rowHtml(t, 'hu', 1);
    check('named long-distance badges use the long CSS class',
      gHtml.includes('class="badge long"') && tHtml.includes('class="badge long"'),
      JSON.stringify({ g: gHtml.includes('badge long'), t: tHtml.includes('badge long') }));
  }
  check('S40 keeps suburban cyan not IC blue',
    String(Lib.applyRailBadge({ label: 'S40', color: '#4477aa', vehicle: 'rail' }).color)
      .replace('#', '').toUpperCase() === '00AFF0');
}
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
check('west IC BKK_8661_47 is train 866 not 8661',
  Lib.trainNumberFromTripId('BKK_8661_47') === '866'
  && Lib.trainNumberFromTripId('BKK_8741_47') === '874'
  && Lib.trainNumberFromTripId('BKK_865_74') === '865');
check('BKK_90681_106 is train 9068 not 906',
  Lib.trainNumberFromTripId('BKK_90681_106') === '9068'
  && Lib.trainNumberFromTripId('BKK_4541_108') === '4541');
check('elvira trip id is not a FUTAR trip',
  !Lib.isFutarTripId('elvira:2889849') && Lib.isFutarTripId('BKK_4541_108')
  && !Lib.isFutarTripId('motis:hu-bkk_1'));
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
check('emmaTrainNumber reads IC 856 TÓPART',
  Lib.emmaTrainNumber('IC 856 TÓPART') === '856'
  && Lib.emmaTrainNumber('S40') === '');
check('pickMotisHopRow matches generic IC to IC 856 TÓPART', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    { label: 'IC 856 TÓPART', trainNumber: '856', dep, shape: [[47.465, 19.021], [47.183, 18.424]] },
    { label: 'G43', trainNumber: '3546', dep: dep + 60, shape: [[47.46, 19.15], [47.18, 18.42]] },
  ], { label: 'IC', trainNumber: '856', dep, vehicle: 'rail' });
  return hit && hit.trainNumber === '856';
})());
check('BALATON 861 matches EMMA vehicle by train number',
  Lib.matchEmmaVehicle(
    { label: 'BALATON', trainNumber: '861', tripId: 'elvira:2890602' },
    { lat: 47.229, lon: 18.668, trip: { tripShortName: '861 BALATON InterCity' } },
  ));
check('BALATON does not match a Balatonfured headsign',
  !Lib.matchEmmaVehicle(
    { label: 'BALATON', tripId: 'elvira:1' },
    { lat: 46.9, lon: 17.8, trip: { tripShortName: '9724 szemelyvonat', tripHeadsign: 'Balatonf\u00fcred' } },
  ));
check('BALATON 866 merges onto FUTAR BKK_8661_47', (() => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', tripId: 'BKK_8661_47', trainNumber: '',
      dep: dep, sched: dep, color: '#2E5EA8',
    }],
    [{
      label: 'BALATON', tripId: 'elvira:2891044', trainNumber: '866',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].tripId === 'BKK_8661_47'
    && Lib.rowTrainNumber(merged[0]) === '866';
})());
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
check('applyEmmaPositions replaces an existing FUTAR pin with EMMA', (() => {
  const rows = [{
    label: 'BALATON', trainNumber: '861', tripId: 'elvira:2890602',
    lat: 47.5, lon: 19.05,
  }];
  Lib.applyEmmaPositions(rows, [{
    lat: 47.2290993, lon: 18.6682205,
    trip: { tripShortName: '861 BALATON InterCity' },
  }]);
  return rows[0].lat === 47.2290993 && rows[0].lon === 18.6682205;
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
    && merged[0].tripId === 'BKK_861_74'
    && merged[0].lat === 47.2290993
    && merged[0].lon === 18.6682205
    && String(merged[0].color).replace('#', '').toUpperCase() === '2E5EA8';
})());
check('ELVIRA GPS still wins if FUTAR was added first', (() => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'BALATON', tripId: 'elvira:2890602', trainNumber: '861',
      dep: dep, sched: dep, color: '#4477aa',
      lat: 47.2290993, lon: 18.6682205,
    }],
    [{
      label: 'IC', tripId: 'BKK_861_74', trainNumber: '861',
      dep: dep, sched: dep, color: '#2E5EA8',
      lat: 47.5, lon: 19.05,
    }],
    12,
  );
  return merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].lat === 47.2290993
    && merged[0].lon === 18.6682205;
})());
check('map skips FUTAR resolve when ELVIRA row already has GPS',
  !Lib.needsFutarMapResolve({ tripId: 'elvira:2890602', lat: 47.229, lon: 18.668 })
  && Lib.needsFutarMapResolve({ tripId: 'elvira:2890602' })
  && !Lib.needsFutarMapResolve({ tripId: 'BKK_861_74', lat: null, lon: null }));
check('hop map uses Transitous full trip for trains and buses',
  src.includes('motisShapeForHop')
  && src.includes('/api/v5/stoptimes')
  && src.includes('_mapBoardMarker')
  && src.includes('combineHopShapes')
  && src.includes('stitchShapes')
  && src.includes('if (canFutar)')
  && !src.includes('clipShapeAfterPoint')
  && !src.includes('truncatedMotis')
  && !src.includes('!hasShape && BkkLib.isTrainRow(row)')
  && !/shape = BkkLib.clipShapeToHop\(/.test(src));
check('transitous host is api.transitous.org',
  src.includes("const TRANSITOUS = 'https://api.transitous.org'")
  && src.includes('String(TRANSITOUS).replace(/\\/$/, \'\')')
  && !src.includes('transitous.de')
  && !src.includes('europe.motis-project.de'));
check('isTrainRow is rail and elvira only',
  Lib.isTrainRow({ vehicle: 'rail', tripId: 'elvira:1', trainNumber: '866' })
  && Lib.isTrainRow({ vehicle: 'rail', label: 'S40', tripId: 'BKK_4343_70', trainNumber: '4343' })
  && !Lib.isTrainRow({ vehicle: 'bus', label: '7', tripId: 'BKK_C007' }));
check('hop map still tries FUTAR geometry when MOTIS misses the headsign',
  src.includes('const canFutar')
  && src.includes('if (canFutar)')
  && src.includes('hassTripShape')
  && src.includes('matchHassVehicle'));
check('train with GPS skips FUTAR hop shape',
  !Lib.allowFutarHopShape({ vehicle: 'rail', tripId: 'elvira:1' }, true)
  && Lib.allowFutarHopShape({ vehicle: 'rail', tripId: 'elvira:1' }, false)
  && Lib.allowFutarHopShape({ vehicle: 'bus', tripId: 'BKK_C007' }, true));
check('transitous fetch has no User-Agent header', !src.includes("'User-Agent'"));
check('pickMotisHopRow matches ELVIRA ADRIA to Transitous AGRAM', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    {
      label: '204 AGRAM - TÓPART', trainNumber: '204', dep, vehicle: 'rail',
      shape: [[47.498, 19.025], [46.249, 16.956]],
      motisTripId: '20260922_15:35_hu-mav_35051884_2656.',
    },
    {
      label: 'IC 856 TÓPART', trainNumber: '856', dep: dep + 7200, vehicle: 'rail',
      shape: [[47.465, 19.021], [47.183, 18.424]],
    },
  ], { label: 'ADRIA', trainNumber: '1240', dep, vehicle: 'rail' });
  return hit && /AGRAM/i.test(hit.label);
})());
check('pickMotisHopRow does not alias TÓPART onto AGRAM at another hour', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    {
      label: '204 AGRAM - TÓPART', trainNumber: '204', dep, vehicle: 'rail',
      shape: [[47.498, 19.025], [46.249, 16.956]],
    },
    {
      label: 'IC 856 TÓPART', trainNumber: '856', dep: dep + 7200, vehicle: 'rail',
      shape: [[47.465, 19.021], [47.183, 18.424]],
    },
  ], { label: 'TÓPART', trainNumber: '856', dep: dep + 7200, vehicle: 'rail' });
  return hit && hit.trainNumber === '856';
})());
check('pickMotisHopRow unique close rail dep matches a renamed IC', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    {
      label: '204 AGRAM - TÓPART', trainNumber: '204', dep, vehicle: 'rail',
      shape: [[47.498, 19.025], [46.249, 16.956]],
    },
  ], { label: 'FOO', trainNumber: '9999', dep, vehicle: 'rail' });
  return hit && /AGRAM/i.test(hit.label);
})());
check('namedTrainAliasMatch is ADRIA↔AGRAM only',
  Lib.namedTrainAliasMatch('ADRIA', '204 AGRAM - TÓPART')
  && Lib.namedTrainAliasMatch('AGRAM', 'ADRIA')
  && !Lib.namedTrainAliasMatch('TÓPART', '204 AGRAM - TÓPART')
  && !Lib.namedTrainAliasMatch('BALATON', '204 AGRAM - TÓPART'));
check('pickMotisVehicle matches Adria by dest stop and time without a shared name', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'Z30', dep, vehicle: 'rail', motisTripId: 'z30',
      nextNames: ['Budapest-Déli'], head: 'Budapest-Déli',
    },
    {
      label: '204 AGRAM - TÓPART', trainNumber: '204', dep, vehicle: 'rail',
      motisTripId: 'agram', nextNames: ['Székesfehérvár', 'Siófok', 'Gyékényes'],
      head: 'Gyékényes',
    },
  ], { label: 'ADRIA', trainNumber: '1240', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return hit && hit.motisTripId === 'agram';
})());
check('pickMotisVehicle does not treat hop dest headsign as destOn', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'G43', dep: dep + 180, vehicle: 'rail', head: 'Székesfehérvár',
      shape: [[47.46, 19.14], [47.18, 18.42]], motisTripId: 'g43',
    },
    {
      label: '204 AGRAM - TÓPART', trainNumber: '204', dep, vehicle: 'rail',
      head: 'Gyékényes', shape: [[47.5, 19.02], [46.25, 16.95]], motisTripId: 'agram',
    },
  ], { label: 'ADRIA', trainNumber: '1240', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return hit && hit.motisTripId === 'agram';
})());
check('pickMotisVehicle prefers a same-label S40 over a destOn IC', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'IC 916 BAKONY', trainNumber: '916', dep: dep + 600, vehicle: 'rail',
      motisTripId: 'bakony', nextNames: ['Székesfehérvár', 'Szombathely'],
    },
    {
      label: 'S40', dep, vehicle: 'rail', motisTripId: 's40',
      nextNames: ['Tárnok'],
    },
  ], { label: 'S40', trainNumber: '4343', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return hit && hit.motisTripId === 's40';
})());
check('pickMotisVehicle does not put Göcsej on a nearby G43', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'G43', dep: dep + 120, vehicle: 'rail', motisTripId: 'g43',
      nextNames: ['Székesfehérvár'], head: 'Székesfehérvár',
      shape: [[47.46, 19.02], [47.18, 18.42]],
    },
    {
      label: 'Z30', dep: dep + 60, vehicle: 'rail', motisTripId: 'z30',
      nextNames: ['Székesfehérvár'],
      shape: [[47.46, 19.02], [47.18, 18.42]],
    },
  ], { label: 'GÖCSEJ', trainNumber: '954', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return !hit;
})());
check('pickMotisVehicle unique nearby G43 does not bind named IC past 90s', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'G43', dep: dep + 120, vehicle: 'rail', motisTripId: 'g43',
      nextNames: ['Székesfehérvár'], head: 'Székesfehérvár',
      shape: [[47.46, 19.02], [47.18, 18.42]],
    },
  ], { label: 'GÖCSEJ', trainNumber: '954', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return !hit;
})());
check('pickMotisVehicle still matches Göcsej by train number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    {
      label: 'G43', dep, vehicle: 'rail', motisTripId: 'g43',
      nextNames: ['Székesfehérvár'],
    },
    {
      label: 'GÖCSEJ', trainNumber: '954', dep, vehicle: 'rail', motisTripId: 'gocsej',
      nextNames: ['Székesfehérvár', 'Szombathely'],
    },
  ], { label: 'GÖCSEJ', trainNumber: '954', dep, vehicle: 'rail' }, { name: 'Székesfehérvár' });
  return hit && hit.motisTripId === 'gocsej';
})());
{
  const gap = Lib.shapeGpsGapMeters(
    [[47.18, 18.42], [47.46, 19.02]],
    47.50, 19.55,
  );
  check('GPS far from a suburban hop is a large shape gap',
    gap > 40000, String(gap));
}
{
  const card = document.createElement('hungarian-transport-card');
  card.setConfig({
    language: 'hu', apiKey: 'x',
    stopLat: 47.46, stopLon: 19.02,
  });
  card._hass = { connection: {} };
  const origMotis = Lib.motisShapeForHop;
  const origHass = Lib.hassTripShape;
  const motisShape = [[47.18, 18.42], [47.46, 19.02]];
  const futarShape = [[47.50, 19.54], [47.23, 16.62]];
  try {
    Lib.motisShapeForHop = async () => ({ shape: motisShape, coversHead: true });
    Lib.hassTripShape = async () => futarShape;
    const got = await card._loadRowMapShape({
      label: 'GÖCSEJ', trainNumber: '954', vehicle: 'rail',
      tripId: 'BKK_954_1', lat: 47.50, lon: 19.55,
      shape: motisShape,
    });
    check('loadRowMapShape prefers FUTAR when GPS is far from MOTIS',
      got.shape.length === 2
      && got.shape[0][1] === 19.54
      && got.shape[1][1] === 16.62,
      JSON.stringify(got.shape));
  } finally {
    Lib.motisShapeForHop = origMotis;
    Lib.hassTripShape = origHass;
  }
}
check('pickMotisHopRow matches a bus route number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    { label: '7', dep, vehicle: 'bus', shape: [[47.48, 19.05], [47.42, 19.07]] },
    { label: '7E', dep: dep + 120, vehicle: 'bus', shape: [[47.50, 19.06], [47.40, 19.08]] },
  ], { label: '7', dep, vehicle: 'bus' });
  return hit && hit.label === '7';
})());
check('pickMotisVehicle does not match bus 7 to 17 or 7E', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
    { label: '17', dep, vehicle: 'bus', shape: [[47.48, 19.05], [47.42, 19.07]], motisTripId: '17' },
    { label: '7E', dep: dep + 30, vehicle: 'bus', shape: [[47.50, 19.06], [47.40, 19.08]], motisTripId: '7e' },
    { label: '7', dep: dep + 60, vehicle: 'bus', shape: [[47.49, 19.04], [47.41, 19.06]], motisTripId: '7' },
  ], { label: '7', dep, vehicle: 'bus' });
  return hit && hit.motisTripId === '7';
})());
check('pickMotisHopRow matches train number not a later same-label hop', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisHopRow([
    { label: 'S40', trainNumber: '4343', dep, shape: [[47.465, 19.021], [47.183, 18.424]] },
    { label: 'S40', trainNumber: '4350', dep: dep + 3600, shape: [[47.46, 19.15], [47.18, 18.42]] },
  ], { label: 'S40', trainNumber: '4343', dep, vehicle: 'rail' });
  return hit && hit.trainNumber === '4343' && hit.shape[0][1] < 19.05;
})());
{
  const orig = Lib.transitousFetch;
  let path = '';
  Lib.transitousFetch = async (p) => {
    if (String(p).indexOf('/trip') >= 0) path = p;
    return {
      legs: [{
        mode: 'REGIONAL_RAIL',
        from: { name: 'Kobanya-Kispest' },
        to: { name: 'Szekesfehervar' },
        legGeometry: { points: '_mdryA_`vic@_pR_pR', precision: 6 },
      }],
    };
  };
  const pts = await Lib.motisTripShape('motis:hu-mav_x');
  Lib.transitousFetch = orig;
  check('motisTripShape loads the full Transitous trip',
    path === '/api/v5/trip' && pts.length >= 2 && pts[0][0] > 45 && pts[0][0] < 49,
    JSON.stringify({ path, n: pts.length, first: pts[0] }));
}
check('map overlay CSS pins Leaflet SVG over tiles',
  src.includes('leaflet-overlay-pane') && src.includes('max-width:none!important'));
check('hop map polyline is unclipped on a canvas renderer',
  src.includes('noClip: true') && src.includes('preferCanvas: true'));
{
  const vm = src.slice(src.indexOf('async _openVehicleMap'), src.indexOf('  _syncOpenMap()'));
  check('hop vehicle map draws one polyline before fitBounds',
    vm.includes('this._mapRouteLayers.push')
    && vm.includes('L.polyline(shape')
    && vm.indexOf('L.polyline(shape') < vm.lastIndexOf('applyView();'));
  check('hop vehicle map loads one row, not every due trip',
    vm.includes('_loadRowMapShape')
    && !vm.includes('_visibleMapRows')
    && !vm.includes('boardMapLayers')
    && !vm.includes('mapAllTrips'));
}
check('hop map loads FUTAR geometry through Home Assistant',
  src.includes('hassTripShape') && src.includes("'trip_shape'"));
{
  const shape = [[47.49923, 19.02468], [47.11915, 17.91143]];
  const wrapped = await Lib.hassTripShape({
    connection: { sendMessagePromise: async () => ({ response: { shape } }) },
  }, 'x', 'BKK_90681_106');
  const flat = await Lib.hassTripShape({
    connection: { sendMessagePromise: async () => ({ shape }) },
  }, 'x', 'BKK_90681_106');
  check('hassTripShape reads wrapped and flat service payloads',
    wrapped.length === 2 && wrapped[1][1] === 17.91143
    && flat.length === 2 && flat[0][0] === 47.49923);
}
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
check('Volán GPS also lands on a motis hop row', (() => {
  const now = 1_790_000_000;
  const rows = [{
    label: '6990', tripId: 'motis:hu-volanbusz_1', vehicle: 'coach',
    head: 'Sz\u00e9kesfeh\u00e9rv\u00e1r', dep: now + 4 * 60,
  }];
  Lib.applyCoachGps(rows, [{
    route: '6990', head: 'Sz\u00e9kesfeh\u00e9rv\u00e1r, aut\u00f3busz-\u00e1llom\u00e1s',
    lat: 47.19, lon: 18.41,
  }], now);
  return rows[0].lat === 47.19 && rows[0].lon === 18.41;
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
check('GPS ELVIRA 9068 still takes FUTAR trip id', (() => {
  const dep = Math.floor(Date.now() / 1000) + 1800;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'Sz', tripId: 'BKK_90681_106', trainNumber: '9068',
      dep: dep, sched: dep, color: '#2E5EA8',
    }],
    [{
      label: 'Sz', tripId: 'elvira:9068', trainNumber: '9068',
      dep: dep, sched: dep, lat: 47.464, lon: 19.021, color: '#4477aa',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].tripId === 'BKK_90681_106'
    && merged[0].lat === 47.464;
})());
check('enrichFromHass copies FUTAR trip id onto an ELVIRA GPS row', (() => {
  const rows = [{
    label: 'Sz', tripId: 'elvira:9068', trainNumber: '9068',
    lat: 47.464, lon: 19.021, attime: '22:47',
  }];
  Lib.enrichFromHass({
    states: {
      'sensor.vonatok_pestrol': {
        attributes: {
          vehicles: [{ tripId: 'BKK_90681_106', trainNumber: '9068', attime: '22:47' }],
        },
      },
    },
  }, rows);
  return rows[0].tripId === 'BKK_90681_106' && rows[0].lat === 47.464;
})());
check('enrichFromHass keeps EMMA GPS over a FUTAR vehicle pin', (() => {
  const rows = [{
    label: 'Sz', tripId: 'elvira:9068', trainNumber: '9068',
    lat: 47.464, lon: 19.021, attime: '22:47',
  }];
  Lib.enrichFromHass({
    states: {
      'sensor.vonatok_pestrol': {
        attributes: {
          vehicles: [{
            tripId: 'BKK_90681_106', trainNumber: '9068', attime: '22:47',
            lat: 47.5, lon: 19.05,
          }],
        },
      },
    },
  }, rows);
  return rows[0].tripId === 'BKK_90681_106'
    && rows[0].lat === 47.464
    && rows[0].lon === 19.021;
})());
check('hop map does not fetch FUTAR trip-details in the browser',
  !/details = await BkkLib.tripDetails\(cfg\.apiKey/.test(src)
  && src.includes("BkkLib.hassService(hass, 'trip_shape'"));
check('decodePolyline accepts a FUTAR polyline object',
  Lib.decodePolyline({ points: '_p~iF~ps|U' }).length >= 1);
check('clipShapeToHop drops the prefix before origin', (() => {
  const shape = [
    [47.463629, 19.149626],
    [47.468643, 19.087938],
    [47.464321, 19.020634],
    [47.183082, 18.423996],
  ];
  const details = {
    sts: [
      { stopId: 'BKK_KK', shapeDistTraveled: 0 },
      { stopId: 'BKK_FERENC', shapeDistTraveled: 5499 },
      { stopId: 'BKK_005501024', shapeDistTraveled: 11301 },
      { stopId: 'BKK_005503269', shapeDistTraveled: 74479 },
    ],
    stops: {
      BKK_KK: { id: 'BKK_KK', name: 'Kőbánya-Kispest', lat: 47.463629, lon: 19.149626 },
      BKK_FERENC: { id: 'BKK_FERENC', name: 'Ferencváros', lat: 47.468643, lon: 19.087938 },
      BKK_005501024: { id: 'BKK_005501024', name: 'Budapest-Kelenföld', lat: 47.464321, lon: 19.020634 },
      BKK_005503269: { id: 'BKK_005503269', name: 'Székesfehérvár', lat: 47.183082, lon: 18.423996 },
    },
  };
  const clipped = Lib.clipShapeToHop(
    shape,
    details,
    { id: 'BKK_005501024', name: 'Budapest-Kelenföld' },
    { id: 'BKK_005503269', key: 'székesfehérvár', name: 'Székesfehérvár' },
  );
  if (!clipped || clipped.length < 2) return false;
  const lons = clipped.map((pt) => pt[1]);
  return Math.max(...lons) < 19.08 && clipped[0][1] < 19.05;
})());
check('pickFullerShape keeps the longer Veszprem branch', (() => {
  const motis = [[47.4989, 19.0250], [47.4643, 19.0206], [47.1833, 18.4250]];
  const futar = [[47.4989, 19.0250], [47.4643, 19.0206], [47.1833, 18.4250], [47.0960, 17.9090]];
  const picked = Lib.pickFullerShape(motis, futar);
  return picked[picked.length - 1][1] === 17.9090;
})());
check('stitchShapes appends the Veszprem tail after MOTIS Szekesfehervar', (() => {
  const motis = [[47.4989, 19.0250], [47.4643, 19.0206], [47.1833, 18.4250]];
  const futar = [
    [47.4989, 19.0250],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.2000, 18.1400],
    [47.0960, 17.9090],
  ];
  const stitched = Lib.stitchShapes(motis, futar);
  return stitched[0][0] === 47.4989
    && stitched[stitched.length - 1][1] === 17.9090
    && stitched.length >= motis.length;
})());
check('combineHopShapes stitches when MOTIS misses the Veszprem headsign', (() => {
  const motis = [[47.4989, 19.0250], [47.4643, 19.0206], [47.1833, 18.4250]];
  const futar = [
    [47.4989, 19.0250],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.2000, 18.1400],
    [47.0960, 17.9090],
  ];
  const combined = Lib.combineHopShapes(motis, futar, false, []);
  return combined[combined.length - 1][1] === 17.9090
    && combined.length > motis.length;
})());
check('motisCoversHead is false when 9068 tripTo is Szekesfehervar',
  !Lib.motisCoversHead(
    { tripTo: 'Székesfehérvár', nextNames: ['Martonvásár', 'Velence'] },
    { head: 'Veszprém', headsign: 'Veszprém' },
  )
  && Lib.motisCoversHead(
    { tripTo: 'Zagreb', nextNames: [] },
    { head: 'Zagreb' },
  )
  && Lib.motisCoversHead(
    { tripTo: 'Székesfehérvár', nextNames: ['Várpalota', 'Veszprém'] },
    { headsign: 'Veszprém' },
  ));
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

check('planner in-card minutesAfter is a select',
  plan.shadowRoot.querySelector('#pmin')
  && plan.shadowRoot.querySelector('#pmin').tagName === 'SELECT'
  && plan.shadowRoot.querySelectorAll('#pmin option').length === 8,
  String(plan.shadowRoot.querySelectorAll('#pmin option').length));
check('look-ahead is a compact select row',
  !!plan.shadowRoot.querySelector('.horizon #preset')
  && plan.shadowRoot.querySelector('.horizon > #pmin')
  && /\.horizon \{[^}]*flex-direction:\s*row/.test(src)
  && !/\.horizon-bar/.test(src)
  && !/\.horizon \.chips \{[^}]*flex-wrap:\s*wrap/.test(src));
check('planner picked stops are buttons',
  plan.shadowRoot.querySelector('#pstop')
  && plan.shadowRoot.querySelector('#pstop').tagName === 'BUTTON'
  && plan.shadowRoot.querySelector('#pdest').tagName === 'BUTTON');
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
const pmin = plan.shadowRoot.querySelector('#pmin');
pmin.value = '60';
pmin.dispatchEvent(new Event('change'));
check('planner look-ahead select sets minutesAfter', plan._config.minutesAfter === 60, String(plan._config && plan._config.minutesAfter));
plan._config = Object.assign({}, plan._config, { stopName: 'Keleti', destName: 'Debrecen' });
plan._syncPickerLabels();
check('picked origin hides the search field',
  plan.shadowRoot.querySelector('#pq').classList.contains('compact-hide')
  && plan.shadowRoot.querySelector('#pdq').classList.contains('compact-hide'));
plan.shadowRoot.querySelector('#pstop').dispatchEvent(new Event('click'));
check('clicking picked origin shows search again',
  !plan.shadowRoot.querySelector('#pq').classList.contains('compact-hide'));

const origPlanJ = Lib.planJourney;
const planJArgs = [];
Lib.planJourney = async function (_key, _o, _d, _h, opts) {
  planJArgs.push(opts && opts.minutesAfter);
  return {
    durationMin: 20, walkMin: 1, waitMin: 0, transfers: 0,
    legs: [{ walk: false, minutes: 20, label: '1', from: 'A', to: 'B' }],
  };
};
plan._rows = [];
plan._journey = null;
plan._journeyKey = '';
plan._journeyLoading = false;
plan._config = Object.assign({}, plan._config, {
  stopId: 'geo:1,2', stopName: 'Utca', destName: 'Cel', destStopId: 'X', minutesAfter: 60,
});
plan._considerJourney();
check('look-ahead is passed into planJourney', planJArgs[0] === 60, String(planJArgs[0]));
plan._config = Object.assign({}, plan._config, { minutesAfter: 240 });
plan._considerJourney();
check('look-ahead change replans while a journey is loading', planJArgs[1] === 240, String(planJArgs[1]));
Lib.planJourney = origPlanJ;
plan._gen += 1;
plan._journeyLoading = false;
if (plan._poll) { clearInterval(plan._poll); plan._poll = null; }
plan._reloadSafe = () => {};

const origTransitousFetch = Lib.transitousFetch;
const origTransitousPlace = Lib.transitousPlace;
Lib.transitousPlace = async (stop) => (stop && stop.id === 'iso-origin' ? '1,2' : '9,9');
let journeyPlan = null;
Lib.transitousFetch = async (path, params) => {
  if (params && params.fromPlace === '1,2') journeyPlan = params;
  return { itineraries: [] };
};
await Lib.transitousJourney({ id: 'iso-origin' }, { id: 'iso-dest' }, { minutesAfter: 30 });
check('transitous journey uses searchWindow from look-ahead',
  journeyPlan && journeyPlan.searchWindow === '1800',
  JSON.stringify(journeyPlan));
Lib.transitousFetch = origTransitousFetch;
Lib.transitousPlace = origTransitousPlace;

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
const toHubGeo = await Lib.cityRailJourney(
  'k', { states: {} },
  { id: 'miskolc:zoo', name: 'Miskolci \u00c1llatkert', lat: 48.12, lon: 20.78 },
  { id: 'miskolc:tiszai', name: 'Tiszai p\u00e1lyaudvar', lat: 48.17, lon: 20.81 },
);
check('cityRailJourney keeps origin dest coordinates',
  toHubGeo && toHubGeo.legs[0].fromLat === 48.12 && toHubGeo.legs[0].fromLon === 20.78
  && toHubGeo.legs[toHubGeo.legs.length - 1].toLat === 48.17
  && toHubGeo.legs[toHubGeo.legs.length - 1].toLon === 20.81,
  JSON.stringify(toHubGeo && toHubGeo.legs.map((l) => ({ from: l.fromLat, to: l.toLat }))));
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

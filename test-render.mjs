/* Renders the card in jsdom: it must draw its own rows, with no dependency on
   any other custom element. */
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost:8123/local/hungarian-transport/',
  pretendToBeVisual: true,
});
for (const key of ['window', 'document', 'customElements', 'HTMLElement', 'Element',
  'navigator', 'CSSStyleDeclaration', 'Node', 'Event', 'CustomEvent']) {
  /* Node 21+ ships a getter-only globalThis.navigator, so plain assignment throws. */
  Object.defineProperty(globalThis, key, {
    value: dom.window[key], configurable: true, writable: true,
  });
}

await import('./dist/hungarian-transport.js');

/* Source-text checks read the modules in src/; behaviour checks run the built bundle. */
const readSources = async () => {
  const { readdir, readFile: rf } = await import('node:fs/promises');
  const files = (await readdir('./src', { recursive: true }))
    .filter((f) => f.endsWith('.js')).sort();
  return (await Promise.all(files.map((f) => rf(`./src/${f}`, 'utf8')))).join('\n');
};
const src = await readSources();

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
check('booking pictogram rendered',
  out.includes('class="picto booking"') && out.includes('>H<')
    && out.includes('data-tip="Helyjegy v\u00e1lt\u00e1sa k\u00f6telez\u0151"'));
check('row data is escaped', !out.includes('<script>') && out.includes('&lt;script&gt;'));
check('card size grows with the rows', card.getCardSize() >= 2, String(card.getCardSize()));

{
  /* The 15 s tick only rewrites countdown text; a full repaint happens only
     when a departure leaves the list. */
  /* The clock is set from `now`, the second the rows were built from, so a
     slow CI runner cannot push the bus past its grace minute. */
  const realNow = Date.now;
  const firstRow = () => card.shadowRoot.querySelector('tr[data-row-index]');
  const before = firstRow();
  /* +100 s: the S40 (+300 s) shows 3 perc, the bus (+60 s) is 40 s late,
     still inside its grace minute. */
  Date.now = () => (now + 100) * 1000;
  card._tickPaint();
  const cds = [...card.shadowRoot.querySelectorAll('tr[data-row-index] .cd')].map((el) => el.textContent);
  check('tick keeps the table and updates countdowns in place',
    firstRow() === before && cds.includes('3 perc'), JSON.stringify(cds));
  /* +3 min: the bus due at +1 min is past its grace minute, the S40 due at
     +5 min stays. */
  Date.now = () => (now + 180) * 1000;
  card._tickPaint();
  const left = [...card.shadowRoot.querySelectorAll('tr[data-row-index]')];
  const leftHtml = left.map((tr) => tr.innerHTML).join('');
  check('tick repaints once a departure leaves the list',
    firstRow() !== before && left.length === 1
      && leftHtml.includes('>S40<') && leftHtml.includes('2 perc')
      && !leftHtml.includes('&lt;script&gt;'),
    JSON.stringify({ rows: left.length, html: leftHtml.slice(0, 200) }));
  Date.now = realNow;

  /* Row index follows the rows on screen, not _rows with departed entries. */
  const saved = card._rows;
  const gone = Object.assign({}, saved[2]);
  const live = Object.assign({}, saved[0], { headsign: 'Clicked train' });
  card._rows = [gone, live];
  card._paint();
  const tr = firstRow();
  const picked = card._rowFromClick({ target: tr });
  check('clicking the first visible row opens that row, not a departed one',
    !!picked && picked.headsign === 'Clicked train', picked && picked.headsign);
  card._rows = saved;
  card._paint();
}

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
check('BKK CSF parent maps to Transitous child F stop',
  Lib.bkkTransitousStopIds('BKK_CSF01507').indexOf('hu-bkk_F01507') >= 0
  && Lib.bkkTransitousStopIds('BKK_005501024').length === 0);
check('Mester / Konyves geocode prefers the named stop over Kobanyai BKK hit',
  Lib.pickTransitousHit([
    { id: 'hu-Kecskemét_SA445', name: 'Könyves Kálmán körút 55.', lat: 46.89, lon: 19.67 },
    { id: 'hu-volanbusz_hkir_773520', name: 'Mester utca / Könyves Kálmán körút', lat: 47.47, lon: 19.08 },
    { id: 'hu-bkk_008148', name: 'Kőbányai út / Könyves Kálmán körút', lat: 47.486, lon: 19.107 },
  ], { id: 'BKK_CSF01507', name: 'Mester utca / Könyves Kálmán körút' }, 'tram').id === 'hu-volanbusz_hkir_773520');
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
check('finiteLatLon rejects null-island from Number(null)',
  Lib.finiteLatLon(null, null) == null
  && Lib.finiteLatLon(0, 0) == null
  && Lib.finiteLatLon('', '') == null
  && Lib.finiteLatLon(undefined, undefined) == null);
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
check('transitous journey asks for 12 itineraries by default',
  src.includes('numItineraries: String(opts.numItineraries || 12)'));
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
check('planner has no preset origin suggestions',
  plan.shadowRoot.querySelectorAll('#pfav .suggest-stop').length === 0
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
check('ELVIRA calls the bundled Hungarian transport service',
  src.includes("BkkLib.callServiceResponse(hass, 'hungarian_transport', 'elvira_between'")
  && !src.includes("'bkk_stop', 'elvira_between'"));
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
  check('numbered through express stays on the hop',
    Lib.mergeVolanRows([], [rail({
      label: '16907', trainNumber: '16907', booking: true, tripId: 'elvira:3640191',
    })], 12)[0].label === '16907');
  check('numbered through személy stays on the hop',
    Lib.mergeVolanRows([], [rail({
      label: '9792', trainNumber: '9792', booking: false, tripId: 'elvira:2888904',
    })], 12)[0].label === '9792');
  check('another numbered személy stays the same way',
    Lib.mergeVolanRows([], [rail({
      label: '9704', trainNumber: '9704', booking: false, tripId: 'elvira:2888905',
    })], 12)[0].label === '9704');
  check('generic EX plus a long train number stays as EX',
    Lib.mergeVolanRows([], [rail({
      label: 'EX', trainNumber: '16907', booking: true, tripId: 'elvira:1',
    })], 12)[0].label === 'EX');
  check('generic EN plus a long train number stays as EN',
    Lib.mergeVolanRows([], [rail({
      label: 'EN', trainNumber: '4240', booking: true, tripId: 'elvira:2',
    })], 12)[0].label === 'EN');
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
  check('numbered FUTAR overlay does not replace G43',
    Lib.mergeVolanRows([rail({
      label: '19707', trainNumber: '19707', dep: dep, sched: dep,
    })], [rail({
      label: 'G43', trainNumber: '19707', dep: dep, sched: dep, color: '#AACD46',
    })], 12).length === 1
    && Lib.mergeVolanRows([rail({
      label: '19707', trainNumber: '19707', dep: dep, sched: dep,
    })], [rail({
      label: 'G43', trainNumber: '19707', dep: dep, sched: dep, color: '#AACD46',
    })], 12)[0].label === 'G43');
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
check('overlay copies ELVIRA platform and booking onto a FUTAR host', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', dep, sched: dep, color: '#2E5EA8',
      platform: '4', booking: true, trainNumber: '914', tripId: 'elvira:1',
    }],
    [{
      label: 'IC', dep, sched: dep, color: '#4477aa', tripId: 'BKK_914_1',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].platform === '4'
    && merged[0].booking === true
    && merged[0].tripId === 'BKK_914_1'
    && Lib.rowTrainNumber(merged[0]) === '914';
})());
check('GÖCSEJ ELVIRA platform merges onto FUTAR IC 955 even without trainNumber on FUTAR', (() => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#2E5EA8', tripId: 'BKK_955_313',
    }],
    [{
      label: 'GÖCSEJ', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#4477aa', platform: '6', trainNumber: '955', tripId: 'elvira:2975814',
    }],
    12,
  );
  return merged.length === 1
    && merged[0].platform === '6'
    && merged[0].label === 'GÖCSEJ'
    && Lib.rowTrainNumber(merged[0]) === '955';
})());
check('GÖCSEJ 967 same-minute merge keeps origin vágány', (() => {
  const dep = Math.floor(Date.now() / 1000) + 120;
  const merged = Lib.mergeVolanRows(
    [{
      label: 'IC', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#2E5EA8', trainNumber: '967', tripId: 'BKK_967_313',
    }],
    [{
      label: 'GÖCSEJ', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#4477aa', platform: '6', trainNumber: '967', tripId: 'elvira:2939299',
    }],
    12,
  );
  return merged.length === 1 && merged[0].platform === '6';
})());
check('origin overlayOnly copies booking onto FUTAR without dest hit and drops GEMENC', (() => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = Lib.mergeVolanRows(
    [
      rail({ label: 'IC', trainNumber: '966', tripId: 'BKK_966_313' }),
      rail({
        label: 'G43', trainNumber: '3511', tripId: 'BKK_3511_108',
        dep: dep + 120, sched: dep + 120,
      }),
      rail({
        label: 'Z30', trainNumber: '4543', tripId: 'BKK_4543_108',
        dep: dep + 240, sched: dep + 240,
      }),
    ],
    [
      rail({
        label: 'GÖCSEJ', trainNumber: '966', booking: true,
        platform: '6', tripId: 'elvira:2975814', destHit: false,
      }),
      rail({
        label: 'GEMENC', trainNumber: '800', booking: true,
        dep: dep + 60, sched: dep + 60, tripId: 'elvira:gemenc', destHit: false,
      }),
      rail({
        label: 'G43', trainNumber: '3511', booking: false,
        dep: dep + 120, sched: dep + 120, tripId: 'elvira:g43', destHit: false,
      }),
      rail({
        label: 'Z30', trainNumber: '4543', booking: false,
        dep: dep + 240, sched: dep + 240, tripId: 'elvira:z30', destHit: false,
      }),
    ],
    12,
    { overlayOnly: true },
  );
  const gocsej = merged.find((r) => Lib.rowTrainNumber(r) === '966');
  const g43 = merged.find((r) => r.label === 'G43');
  const z30 = merged.find((r) => r.label === 'Z30');
  return merged.length === 3
    && gocsej && gocsej.booking === true && gocsej.platform === '6' && gocsej.label === 'GÖCSEJ'
    && g43 && g43.booking !== true
    && z30 && z30.booking !== true
    && !merged.some((r) => String(r.label || '').toUpperCase() === 'GEMENC');
})());
check('overlayOnly does not pair GEMENC onto a same-minute FUTAR IC', (() => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = Lib.mergeVolanRows(
    [rail({ label: 'IC', trainNumber: '966', tripId: 'BKK_966_313' })],
    [rail({
      label: 'GEMENC', trainNumber: '800', booking: true,
      platform: '3', tripId: 'elvira:gemenc', destHit: false,
    })],
    12,
    { overlayOnly: true },
  );
  return merged.length === 1
    && merged[0].label === 'IC'
    && Lib.rowTrainNumber(merged[0]) === '966'
    && merged[0].booking !== true
    && merged[0].platform !== '3';
})());
check('overlayOnly does not byTime-merge two ICs at the same minute', (() => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = Lib.mergeVolanRows(
    [rail({ label: 'IC', tripId: 'BKK_966_313' })],
    [rail({
      label: 'IC', booking: true, platform: '3',
      tripId: 'elvira:other-ic', destHit: false,
    })],
    12,
    { overlayOnly: true },
  );
  return merged.length === 1
    && merged[0].label === 'IC'
    && merged[0].booking !== true
    && merged[0].platform !== '3'
    && merged[0].tripId === 'BKK_966_313';
})());
check('overlayOnly matches repeated train numbers to the correct departure', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL',
  }, extra);
  const merged = Lib.mergeVolanRows(
    [
      rail({ label: 'IC', trainNumber: 'IC 914', dep, sched: dep, tripId: 'BKK_914_1' }),
      rail({
        label: 'IC', trainNumber: 'IC 914', dep: dep + 3600, sched: dep + 3600,
        tripId: 'BKK_914_2',
      }),
    ],
    [
      rail({
        label: 'IC', trainNumber: '914', dep: dep + 3600, sched: dep + 3600,
        booking: true, tripId: 'elvira:914',
      }),
    ],
    12,
    { overlayOnly: true },
  );
  return merged.length === 2
    && merged[0].booking !== true
    && merged[1].booking === true
    && merged[0].tripId === 'BKK_914_1'
    && merged[1].tripId === 'BKK_914_2';
})());
check('overlayOnly matches ELVIRA booking when scheduled departures differ by minutes', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL',
  }, extra);
  const merged = Lib.mergeVolanRows(
    [
      rail({ label: 'IC', trainNumber: 'IC 914', dep, sched: dep, tripId: 'BKK_914_1' }),
      rail({
        label: 'IC', trainNumber: 'IC 914', dep: dep + 3600, sched: dep + 3600,
        tripId: 'BKK_914_2',
      }),
    ],
    [
      rail({
        label: 'IC', trainNumber: '914', dep: dep + 3720, sched: dep + 3720,
        booking: true, tripId: 'elvira:914',
      }),
    ],
    12,
    { overlayOnly: true },
  );
  return merged.length === 2
    && merged[0].booking !== true
    && merged[1].booking === true
    && merged[1].tripId === 'BKK_914_2';
})());
check('Székesfehérvár name maps to ELVIRA station code',
  Lib.elviraCodeFromName('Székesfehérvár') === '005503269');
check('MOTIS scheduledTrack becomes the row platform', (() => {
  const start = new Date(Date.now() + 10 * 60 * 1000).toISOString();
  const end = new Date(Date.now() + 70 * 60 * 1000).toISOString();
  const rows = Lib.rowsFromMotis({
    itineraries: [{
      transfers: 0,
      startTime: start,
      endTime: end,
      legs: [{
        mode: 'RAIL',
        routeShortName: 'IC',
        tripShortName: '914',
        headsign: 'Szombathely',
        startTime: start,
        endTime: end,
        duration: 3600,
        from: { scheduledTrack: '4' },
        tripId: 'hu-mav_914',
      }],
    }],
  }, { name: 'Székesfehérvár' }, 'mav', 180);
  return rows.length === 1 && rows[0].platform === '4';
})());
check('asRow keeps platform badge and helyjegy flag', (() => {
  const painted = Lib.asRow({
    label: 'IC', rawType: 'RAIL', dep: Math.floor(Date.now() / 1000) + 120,
    platform: '4', booking: true, head: 'Székesfehérvár',
  }, 'Székesfehérvár', 'hu');
  const html = (() => {
    const card = document.createElement('hungarian-transport-card');
    card.setConfig({ language: 'hu' });
    return card._rowHtml(painted, 'hu', 0);
  })();
  return painted.platform === '4' && painted.booking === true
    && html.includes('class="plat"') && html.includes('>4<')
    && html.includes('class="picto booking"') && html.includes('>H<');
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
check('hop map uses MÁV GTFS full line for trains, Transitous for buses',
  src.includes('motisShapeForHop')
  && src.includes('mavShapeForRow')
  && src.includes('mav-index.json.gz')
  && src.includes('/api/v5/stoptimes')
  && src.includes('_mapBoardMarker')
  && src.includes('combineHopShapes')
  && src.includes('stitchShapes')
  && src.includes('clipShapeFromBoard')
  && src.includes('clipShapeFromBoardToward')
  && src.includes('clipRideShape')
  && src.includes('clipShapeToBoardDest')
  && src.includes('keepsFullLineShape')
  && src.includes('pickPublishedLineShape')
  && src.includes('futarTripShape')
  && src.includes('bkkTransitousStopIds')
  && src.includes('if (canFutar)')
  && src.includes('pfaedle OSM rails')
  && !src.includes('clipShapeAfterPoint')
  && !src.includes('truncatedMotis')
  && !src.includes('!hasShape && BkkLib.isTrainRow(row)')
  && !/shape = BkkLib.clipShapeToHop\(/.test(src));
check('mavShapeIndexKey prefers suburban line over colliding train numbers', (() => {
  const idx = { n: { 19707: 1, 9068: 2, 861: 3 }, l: { G43: 10, S40: 11 } };
  const g43 = Lib.mavShapeIndexKey({ label: 'G43', trainNumber: '19707', vehicle: 'rail' }, idx);
  const g43long = Lib.mavShapeIndexKey(
    { label: 'G43 19707', trainNumber: '19707', vehicle: 'rail' }, idx,
  );
  const ic = Lib.mavShapeIndexKey({ label: 'BALATON', trainNumber: '861', vehicle: 'rail' }, idx);
  const sz = Lib.mavShapeIndexKey({ label: 'Sz', trainNumber: '9068', vehicle: 'rail' }, idx);
  const s40 = Lib.mavShapeIndexKey({ label: 'S40', trainNumber: '4343', vehicle: 'rail' }, idx);
  return g43 && g43.kind === 'l' && g43.key === 'G43'
    && g43long && g43long.kind === 'l' && g43long.key === 'G43'
    && ic && ic.kind === 'n' && ic.key === '861'
    && sz && sz.kind === 'n' && sz.key === '9068'
    && s40 && s40.kind === 'l' && s40.key === 'S40';
})());
check('mavShapeForRow prefers suburban line then train number',
  src.includes('suburbanLineLabel(label)')
  && src.includes('idx.l[line]')
  && src.includes('idx.l[label]')
  && src.includes('idx.n[num]'));
check('mav index miss is not cached',
  src.includes('const empty = !idx || !Array.isArray(idx.p) || idx.p.length < 1')
  && src.includes('if (empty) cache.delete(url)'));
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
check('Leaflet loads from the card folder, not a CDN',
  !src.includes('unpkg.com')
  && src.includes("new URL('leaflet/leaflet.js', ASSET_BASE)")
  && src.includes("new URL('leaflet/leaflet.css', ASSET_BASE)"));
check('GTFS indexes revalidate instead of bypassing the HTTP cache',
  (src.match(/cache: 'no-cache'/g) || []).length === 1
  && (src.match(/BkkLib\._fetchIndexBytes\(url\)/g) || []).length === 2);
check('hop map polyline is unclipped SVG on the overlay pane',
  src.includes('noClip: true')
  && src.includes("pane: 'overlayPane'")
  && /L\.map\(canvas, \{ scrollWheelZoom: true, preferCanvas: false \}/.test(src)
  && !/L\.map\(canvas, \{ scrollWheelZoom: true, preferCanvas: true \}/.test(src)
  && !src.includes('L.canvas({ padding'));
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
check('hop map bus shape uses FUTAR trip-details polyline',
  src.includes('futarTripShape')
  && src.includes('packed && packed.polyline')
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
check('combineHopShapes keeps MOTIS hop when FUTAR G43 starts at Kobanya', (() => {
  const motis = [[47.4651, 19.0208], [47.35, 18.8], [47.185, 18.414]];
  const futar = [
    [47.463, 19.1498], [47.464, 19.08], [47.4651, 19.0208], [47.35, 18.8], [47.185, 18.414],
  ];
  const combined = Lib.combineHopShapes(motis, futar, true, futar);
  return combined[0][1] < 19.05 && combined[0][1] > 19.00;
})());
check('combineHopShapes keeps MOTIS inbound from Deli on 9068', (() => {
  const motis = [
    [47.4996, 19.0247],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.0960, 17.9090],
  ];
  const futar = [
    [47.463, 19.1498],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.0960, 17.9090],
  ];
  const combined = Lib.combineHopShapes(motis, futar, true, futar);
  return combined[0][0] > 47.49 && combined[combined.length - 1][1] === 17.9090;
})());
check('clipShapeFromBoard drops G43 inbound before Kelenfold', (() => {
  const futar = [
    [47.463, 19.1498], [47.464, 19.08], [47.4651, 19.0208], [47.35, 18.8], [47.185, 18.414],
  ];
  const clipped = Lib.clipShapeFromBoard(futar, [47.46445, 19.0219]);
  return clipped[0][1] < 19.05 && clipped[clipped.length - 1][1] < 18.5;
})());
check('clipShapeFromBoard keeps Veszprem tail after Kelenfold', (() => {
  const full = [
    [47.4989, 19.0250],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.0960, 17.9090],
  ];
  const clipped = Lib.clipShapeFromBoard(full, [47.46445, 19.0219]);
  return clipped[0][1] < 19.05
    && clipped[clipped.length - 1][1] === 17.9090;
})());
check('clipShapeFromBoard keeps full 2B if board is 2km off the tracks', (() => {
  const full = [
    [47.5144, 19.0469],
    [47.469814, 19.082997],
    [47.452218, 19.09187],
    [47.4330, 19.1050],
  ];
  const clipped = Lib.clipShapeFromBoard(full, [47.4858, 19.1071]);
  return clipped.length === full.length && clipped[0][0] === 47.5144;
})());
check('clipShapeToBoardDest keeps 2B hop Mester to Timot, drops Jaszai and Pacsirtatelep', (() => {
  const full = [
    [47.5144, 19.0469],
    [47.4816, 19.0687],
    [47.469814, 19.082997],
    [47.4606, 19.0871],
    [47.452218, 19.09187],
    [47.4330, 19.1050],
  ];
  const clipped = Lib.clipShapeToBoardDest(
    full,
    [47.469814, 19.082997],
    [47.452218, 19.09187],
  );
  const lats = clipped.map((pt) => pt[0]);
  return clipped.length >= 2
    && Math.max(...lats) < 47.48
    && Math.min(...lats) > 47.44
    && clipped[0][0] > 47.46
    && clipped[clipped.length - 1][0] < 47.46;
})());
check('keepsFullLineShape is tram/metro/trolley/bus/coach/rail',
  Lib.keepsFullLineShape({ vehicle: 'tram', label: '2B' })
  && Lib.keepsFullLineShape({ type: 'TRAM', label: '1' })
  && Lib.keepsFullLineShape({ vehicle: 'rail' })
  && Lib.keepsFullLineShape({ type: 'SUBWAY' })
  && Lib.keepsFullLineShape({ type: 'TROLLEYBUS' })
  && Lib.keepsFullLineShape({ vehicle: 'bus', label: '8E' })
  && Lib.keepsFullLineShape({ type: 'BUS' })
  && Lib.keepsFullLineShape({ vehicle: 'coach' }));
check('tram hop map does not clip origin to dest',
  src.includes('keepsFullLineShape')
  && src.includes('stop-to-stop hop cut')
  && src.includes('Published line stays whole')
  && src.includes('clipRideShape')
  && /if \(BkkLib\.keepsFullLineShape\(row\)\) return pts/.test(src)
  && !/const railHop = kind === 'rail'/.test(src));
check('clipShapeToBoardDest refuses a collapsed dest geocode', (() => {
  const full = [
    [47.5144, 19.0469],
    [47.469814, 19.082997],
    [47.452218, 19.09187],
    [47.4330, 19.1050],
  ];
  const clipped = Lib.clipShapeToBoardDest(
    full,
    [47.469814, 19.082997],
    [47.4699, 19.0830],
  );
  return clipped.length === full.length && clipped[0][0] === 47.5144;
})());
check('clipShapeFromBoardToward drops Kobanya on G43 Kelenfold to Szekesfehervar', (() => {
  const line = [
    [47.183, 18.425],
    [47.31, 18.72],
    [47.46445, 19.0219],
    [47.464, 19.08],
    [47.464, 19.150],
  ];
  const clipped = Lib.clipShapeFromBoardToward(
    line,
    [47.46445, 19.0219],
    [47.1853, 18.4142],
  );
  const lons = clipped.map((pt) => pt[1]);
  return clipped.length >= 2
    && Math.max(...lons) < 19.05
    && clipped[0][1] < 18.5
    && clipped[clipped.length - 1][1] < 19.05;
})());
check('clipShapeFromBoardToward keeps Veszprem tail on 9068 past Szekesfehervar', (() => {
  const full = [
    [47.4989, 19.0250],
    [47.4643, 19.0206],
    [47.1833, 18.4250],
    [47.0960, 17.9090],
  ];
  const clipped = Lib.clipShapeFromBoardToward(
    full,
    [47.46445, 19.0219],
    [47.1853, 18.4142],
  );
  return clipped[0][1] < 19.05
    && clipped[clipped.length - 1][1] === 17.9090;
})());
check('clipRideShape tram keeps full 2B line', (() => {
  const full = [
    [47.5144, 19.0469],
    [47.469814, 19.082997],
    [47.4330, 19.1050],
  ];
  const clipped = Lib.clipRideShape(
    full,
    [47.469814, 19.082997],
    [47.452218, 19.09187],
    { vehicle: 'tram', label: '2B' },
  );
  return clipped.length === full.length && clipped[0][0] === 47.5144;
})());
check('clipRideShape bus 8E Keleti hop keeps full Ujpalota line', (() => {
  const full = [
    [47.5690, 19.1520],
    [47.5310, 19.1200],
    [47.5004, 19.0840],
    [47.4940, 19.0600],
    [47.4896, 19.0617],
    [47.4860, 19.0570],
    [47.4830, 19.0530],
    [47.4770, 19.0470],
    [47.4645, 19.0220],
  ];
  const clipped = Lib.clipRideShape(
    full,
    [47.5004, 19.0840],
    [47.4645, 19.0220],
    { vehicle: 'bus', label: '8E', type: 'BUS' },
  );
  const coach = Lib.clipRideShape(
    full,
    [47.5004, 19.0840],
    [47.4645, 19.0220],
    { vehicle: 'coach', type: 'COACH' },
  );
  return clipped.length === full.length
    && clipped[0][0] === 47.5690
    && clipped[clipped.length - 1][0] === 47.4645
    && coach.length === full.length
    && coach[0][0] === 47.5690;
})());
check('clipRideShape train keeps full G43 line', (() => {
  const full = [
    [47.183, 18.425],
    [47.46445, 19.0219],
    [47.464, 19.150],
  ];
  const clipped = Lib.clipRideShape(
    full,
    [47.46445, 19.0219],
    [47.1853, 18.4142],
    { vehicle: 'rail', label: 'G43' },
  );
  return clipped.length === full.length && clipped[clipped.length - 1][1] === 19.150;
})());
check('pickPublishedLineShape keeps FUTAR 8E over MOTIS hop chord', (() => {
  const hop = [[47.5004, 19.0840], [47.4645, 19.0220]];
  const line = [
    [47.5690, 19.1520],
    [47.5310, 19.1200],
    [47.5004, 19.0840],
    [47.4940, 19.0600],
    [47.4896, 19.0617],
    [47.4860, 19.0570],
    [47.4830, 19.0530],
    [47.4770, 19.0470],
    [47.4645, 19.0220],
  ];
  const picked = Lib.pickPublishedLineShape(
    hop, line, { vehicle: 'bus', label: '8E', type: 'BUS' }, true,
  );
  const train = Lib.pickPublishedLineShape(
    hop, line, { vehicle: 'rail', label: 'G43' }, true,
  );
  return picked.length === line.length
    && picked[0][0] === 47.5690
    && picked[picked.length - 1][0] === 47.4645
    && train[0][0] === hop[0][0];
})());
check('futarTripShape reads trip-details polyline not hass trip_shape',
  src.includes('async futarTripShape')
  && src.includes('packed && packed.polyline')
  && src.includes('pickPublishedLineShape')
  && src.includes('bkk_stop has no trip_shape')
  && src.includes('1.4.7-rev.3'));
check('knownStopCoords resolves destKey without a BKK id',
  Lib.knownStopCoords({ key: 'székesfehérvár' })[0] > 47.18
  && Lib.knownStopCoords({ name: 'Budapest-Kelenföld' })[1] < 19.03
  && Lib.knownStopCoords({ key: 'keleti' })[0] > 47.49);
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

const origMavIndex = Lib.mavIndex;
const ic856 = Lib.encodePolyline
  ? null
  : [[47.498889, 19.025], [47.183611, 18.424722], [46.440833, 16.986667]];
Lib.mavIndex = async () => ({
  n: { 856: 0 },
  l: { G43: 1 },
  p: [
    '_p~iF~ps|U_ulLnnqC_mqNvxq`@',
    Lib.decodePolyline && ic856 ? '' : '',
  ],
});
/* Build a real encoded string via decode round-trip: encode in the test by
   feeding decodePolyline's inverse is not exported, so pack lat/lon as a
   precision-5 polyline generated here. */
function encodePts(pts) {
  const factor = 1e5;
  let plat = 0;
  let plng = 0;
  let out = '';
  const enc = (v) => {
    v = v < 0 ? ~(v << 1) : (v << 1);
    let s = '';
    while (v >= 0x20) {
      s += String.fromCharCode((0x20 | (v & 0x1f)) + 63);
      v >>= 5;
    }
    s += String.fromCharCode(v + 63);
    return s;
  };
  pts.forEach((p) => {
    const ilat = Math.round(p[0] * factor);
    const ilng = Math.round(p[1] * factor);
    out += enc(ilat - plat) + enc(ilng - plng);
    plat = ilat;
    plng = ilng;
  });
  return out;
}
const poly856 = encodePts(ic856);
const polyG43 = encodePts([[47.186, 18.434], [47.478, 19.12]]);
Lib.mavIndex = async () => ({ n: { 856: 0 }, l: { G43: 1 }, p: [poly856, polyG43] });
const mavIc = await Lib.mavShapeForRow({ label: 'IC 856 TÓPART', trainNumber: '856', vehicle: 'rail' });
check('mavShapeForRow IC 856 is Déli to Nagykanizsa',
  mavIc.length === 3
  && Math.abs(mavIc[0][1] - 19.025) < 1e-3
  && Math.abs(mavIc[mavIc.length - 1][1] - 16.987) < 1e-2,
  JSON.stringify(mavIc));
const mavG43 = await Lib.mavShapeForRow({ label: 'G43', vehicle: 'rail' });
check('mavShapeForRow G43 uses the line code',
  mavG43.length === 2 && Math.abs(mavG43[0][1] - 18.434) < 1e-3,
  JSON.stringify(mavG43));
Lib.mavIndex = origMavIndex;

/* BKK key held by the integration: FUTÁR goes through hungarian_transport.futar
   and the key never appears in a browser request or the card config. */
{
  const calls = [];
  const browserFetches = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    browserFetches.push(String(url));
    return realFetch ? realFetch(url, init) : { ok: false, status: 599 };
  };
  const hassWithKey = {
    states: {},
    language: 'en',
    services: { hungarian_transport: { futar: {}, elvira_between: {} } },
    callService: async (domain, service, data) => {
      calls.push({ domain, service, data });
      return { context: {}, response: { data: { status: 'OK', data: { list: [] } } } };
    },
  };
  const noKey = { states: {}, language: 'en', services: { hungarian_transport: { elvira_between: {} } } };
  check('card key: own key wins, integration beats a borrowed key',
    Lib.cardApiKey({ apiKey: 'own' }, hassWithKey, 'borrowed') === 'own'
    && Lib.cardApiKey({}, hassWithKey, 'borrowed') === ''
    && Lib.cardApiKey({}, noKey, 'borrowed') === 'borrowed'
    && Lib.hasFutar('', hassWithKey) && !Lib.hasFutar('', noKey) && Lib.hasFutar('k', noKey));

  Lib._hass = hassWithKey;
  const data = await Lib.fetch('', 'search.json', { query: 'Keleti' }, true);
  check('integration key calls hungarian_transport.futar, not BKK from the browser',
    calls.length === 1 && calls[0].domain === 'hungarian_transport' && calls[0].service === 'futar'
    && calls[0].data.path === 'search.json' && calls[0].data.params.query === 'Keleti'
    && data.status === 'OK'
    && !browserFetches.some((u) => u.includes('go.bkk.hu')),
    JSON.stringify({ calls, browserFetches }));

  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  const edEvents = [];
  ed.addEventListener('config-changed', (ev) => edEvents.push(ev.detail.config));
  ed.setConfig({ type: 'custom:hungarian-transport-card', language: 'en' });
  ed.hass = hassWithKey;
  await new Promise((r) => setTimeout(r, 20));
  const okWrap = ed.querySelector('#apiKeyOkWrap') || (ed.shadowRoot && ed.shadowRoot.querySelector('#apiKeyOkWrap'));
  const okText = ed.querySelector('#apiKeyOkText') || (ed.shadowRoot && ed.shadowRoot.querySelector('#apiKeyOkText'));
  check('editor hides the key field and says the integration provides it',
    !!okWrap && okWrap.style.display !== 'none'
    && !!okText && /integration/i.test(okText.textContent),
    okText ? okText.textContent : 'no okText');
  check('editor never writes a key into the card config',
    !edEvents.some((c) => c && c.apiKey),
    JSON.stringify(edEvents));
  ed.remove();

  /* A rejected integration key brings the field back. */
  const hassBadKey = Object.assign({}, hassWithKey, {
    callService: async () => { throw { code: 'home_assistant_error', message: 'BKK FUTÁR rejected the configured key (HTTP 403).' }; },
  });
  Lib._hass = hassBadKey;
  const ed2 = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed2);
  ed2.setConfig({ type: 'custom:hungarian-transport-card', language: 'en' });
  ed2.hass = hassBadKey;
  await new Promise((r) => setTimeout(r, 20));
  const wrap2 = ed2.querySelector('#apiKeyWrap');
  const okWrap2 = ed2.querySelector('#apiKeyOkWrap');
  check('editor shows the key field when the integration key is rejected',
    !!wrap2 && wrap2.style.display !== 'none' && !!okWrap2 && okWrap2.style.display === 'none',
    JSON.stringify({ wrap: wrap2 && wrap2.style.display, ok: okWrap2 && okWrap2.style.display }));
  ed2.remove();

  /* A key borrowed from a sibling card while HA was starting gives way to the
     integration once its futar service appears. */
  const dashboard = { views: [{ cards: [{ type: 'custom:hungarian-transport-card', apiKey: 'sibling-key' }] }] };
  const starting = {
    states: {},
    language: 'en',
    services: { hungarian_transport: { elvira_between: {} } },
    connection: {
      sendMessagePromise: async (msg) => (msg.type === 'lovelace/config' ? dashboard : {}),
    },
  };
  Lib._hass = starting;
  const borrower = document.createElement('hungarian-transport-card');
  document.body.appendChild(borrower);
  borrower.setConfig({ language: 'en' });
  borrower.hass = starting;
  await new Promise((r) => setTimeout(r, 20));
  const borrowed = borrower._apiKey();
  borrower.hass = Object.assign({}, starting, { services: hassWithKey.services });
  check('borrowed sibling key stays out of the config and yields to the integration',
    borrowed === 'sibling-key' && !borrower._config.apiKey
    && borrower._apiKey() === '' && borrower._hasFutar(),
    JSON.stringify({ borrowed, now: borrower._apiKey(), cfg: borrower._config.apiKey || '' }));
  borrower.remove();
  Lib._hass = null;
  globalThis.fetch = realFetch;
}

/* Map routes for GTFS rows: never borrow another line's route. */
{
  const dep = 1800000000;
  const shp = [[46.35, 17.79], [46.36, 17.76]];
  const busRow = { label: '32', dep, vehicle: 'bus', rawType: 'BUS', tripId: 'gtfs:32:20261019:600:1' };
  const other = { label: '5600', dep: dep + 30, vehicle: 'bus', shape: shp };
  const same = { label: '32', dep: dep + 240, vehicle: 'bus', shape: shp };
  check('short bus label does not take another line at the same time',
    Lib.pickMotisVehicle([other], busRow, null) === null);
  check('short bus label takes its own line',
    Lib.pickMotisVehicle([other, same], busRow, null) === same);
  const ferryRow = { label: 'Siófok-Balatonfüred-Tihany', dep, vehicle: 'ferry', rawType: 'FERRY', tripId: 'gtfs:x' };
  check('a ferry is not matched with a bus leaving the same quay',
    Lib.vehicleBucket(ferryRow) === 'ferry'
    && Lib.pickMotisVehicle([{ label: '6038', dep: dep + 20, vehicle: 'bus', shape: shp }], ferryRow, null) === null);
  const kinds = [0, 11, 4, 3, 700, 900].map((rt) => Lib.gtfsRouteKind(rt, 'bus', 'BUS').vehicle);
  check('GTFS route_type picks tram, trolleybus, ferry, or keeps the bus default',
    JSON.stringify(kinds) === JSON.stringify(['tram', 'trolleybus', 'ferry', 'bus', 'bus', 'tram']),
    JSON.stringify(kinds));
  const hits = [
    { id: 'hu-bkk_F00867', name: 'Béla utca', lat: 47.58, lon: 19.08, areas: [{ name: 'Budapest' }] },
    { id: 'hu-volanbusz_hkir_665919', name: 'Székesfehérvár, Béla utca', lat: 47.22, lon: 18.46,
      areas: [{ name: 'Székesfehérvári járás' }, { name: 'Székesfehérvár' }] },
  ];
  const picked = Lib.pickTransitousHit(hits, { id: 'szekesfehervar:1', name: 'Béla utca', area: 'Székesfehérvár' }, 'bus');
  check('stop lookup stays inside the operator city',
    picked && picked.id === 'hu-volanbusz_hkir_665919', picked && picked.id);
  const kapos = Lib.pickTransitousHit([
    { id: 'hu-kaposvár_720884', name: 'Autóbusz-állomás', areas: [{ name: 'Kaposvár' }] },
    { id: 'hu-volanbusz_hkir_549762', name: 'Kapuvár, autóbusz-állomás', areas: [{ name: 'Kapuvár' }] },
  ], { id: 'kaposvar:1', name: 'Autóbusz-állomás', area: 'Kaposvár' }, 'bus');
  check('a bus station asked by name is not penalised as a bus station',
    kapos && kapos.id === 'hu-kaposvár_720884', kapos && kapos.id);
}

/* GTFS rows draw their shapes.txt route from the sharded shapes folder,
   with no Transitous lookup; without a shape they fall back to the stops. */
{
  const { gzipSync } = await import('node:zlib');
  const base = 'http://localhost:8123/hungarian-transport/';
  const enc = 'oehyG_r|yBg^_jAg^_q@o}@_|B';
  const fixture = {
    v: 29,
    sh: { dir: 'city-shapes-test', n: 64 },
    feed: '',
    s: [['t:1', 'Alpha', '1', 0], ['t:2', 'Beta', '2', 0], ['t:3', 'Gamma', '3', 0]],
    g: [4625000, 2014000, 4626000, 2016000, 4627000, 2018000],
    c: [['1111111', '20200101', '20991231']],
    a: [[]],
    r: [[]],
    t: [[0, '7', [0, 1, 2], [480, 490, 500], 3, 65]],
    o: [['test', 'Test — helyi']],
  };
  const files = {
    [base + 'city-index.json.gz']: gzipSync(JSON.stringify(fixture)),
    [base + 'city-shapes-test/01.json.gz']: gzipSync(JSON.stringify({ v: 1, p: ['', enc] })),
  };
  const asked = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url).split('?')[0];
    asked.push(u);
    return files[u] ? new Response(files[u], { status: 200 }) : new Response('', { status: 404 });
  };
  try {
    const idx = await Lib._loadGtfsIndex(base + 'city-index.json.gz?v=1', 'errIndexHttp', 'errIndexGunzip');
    check('index stops carry their coordinates',
      idx.stops[1].lat === 46.26 && idx.stops[1].lon === 20.16, JSON.stringify(idx.stops[1]));
    const geo = Lib.gtfsTripGeo(idx, idx.t[0], 0, 2);
    check('departure geometry points at shard 01, slot 1',
      geo.shape && geo.shape.dir === 'city-shapes-test' && geo.shape.id === 65 && geo.stops.length === 3,
      JSON.stringify(geo));
    const geoCard = document.createElement('hungarian-transport-card');
    geoCard._config = { stopId: 't:1', stopName: 'Alpha', destKey: 'gamma', destName: 'Gamma', helyi: true, city: 'test' };
    const row = { tripId: 'gtfs:7:20261019:480:0', label: '7', vehicle: 'bus', rawType: 'BUS', dep: 1800000000, gtfs: geo };
    asked.length = 0;
    const out = await geoCard._loadRowMapShape(row);
    check('GTFS row draws its shapes.txt route without Transitous',
      out.shape.length === 4 && Math.abs(out.shape[1][1] - 20.152) < 1e-6
      && out.board[0] === 46.25 && !asked.some((u) => u.includes('transitous')),
      JSON.stringify({ n: out.shape.length, board: out.board, asked }));
    const plain = Object.assign({}, row, { gtfs: Object.assign({}, geo, { shape: null }) });
    const out2 = await geoCard._loadRowMapShape(plain);
    check('without a shape the route falls back to the GTFS stops',
      out2.shape.length === 3 && out2.shape[2][0] === 46.27, JSON.stringify(out2.shape));
    const asRowKept = Lib.asRow(Object.assign({ head: 'Gamma', text: '#fff', color: '#000' }, row), 'Gamma', 'hu');
    check('asRow keeps the GTFS geometry', asRowKept.gtfs === geo);
  } finally {
    globalThis.fetch = realFetch;
  }
}

/* Planner: plan for a chosen time, itinerary list and timeline. */
{
  const iso = (h, m) => Date.UTC(2026, 9, 19, h, m);
  const motis = Lib.journeysFromMotis({ itineraries: [
    { startTime: '2026-10-19T06:00:00Z', endTime: '2026-10-19T07:10:00Z', duration: 4200, transfers: 1, legs: [
      { mode: 'WALK', from: { name: 'START' }, to: { name: 'Népliget' }, startTime: '2026-10-19T06:00:00Z', endTime: '2026-10-19T06:04:00Z', duration: 240, distance: 300 },
      { mode: 'COACH', routeShortName: '1205', from: { name: 'Népliget' }, to: { name: 'END' }, startTime: '2026-10-19T06:05:00Z', endTime: '2026-10-19T07:10:00Z', duration: 3900, headsign: 'Székesfehérvár', routeColor: 'F9AB13', routeTextColor: '000000' },
    ] },
    { startTime: '2026-10-19T05:50:00Z', endTime: '2026-10-19T07:30:00Z', duration: 6000, transfers: 2, legs: [
      { mode: 'BUS', routeShortName: '99', from: { name: 'A' }, to: { name: 'B' }, startTime: '2026-10-19T05:50:00Z', endTime: '2026-10-19T07:30:00Z', duration: 6000 },
    ] },
  ] });
  check('every MOTIS itinerary is kept with its times',
    motis.length === 2 && motis[0].startMs === iso(6, 0) && motis[0].endMs === iso(7, 10)
    && motis[0].legs[1].startMs === iso(6, 5), JSON.stringify(motis.map((j) => [j.startMs, j.endMs])));
  Lib.nameJourneyEnds(motis[0], { name: 'Népliget autóbusz-pályaudvar' }, { name: 'Székesfehérvár' });
  check('START and END become the picked places',
    motis[0].legs[0].from === 'Népliget autóbusz-pályaudvar' && motis[0].legs[1].to === 'Székesfehérvár');
  const kept = Lib.dropDominatedJourneys(motis);
  check('an itinerary that leaves earlier and arrives later than another is dropped',
    kept.length === 1 && kept[0] === motis[0], JSON.stringify(kept.map((j) => j.startMs)));
  const ft = Lib.futarPlanTime({ time: iso(6, 30), arriveBy: true });
  check('FUTÁR plan-trip gets Budapest date and time',
    ft.date === '2026-10-19' && ft.time === '08:30' && ft.arriveBy === 'true', JSON.stringify(ft));

  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  pc.hass = { states: {}, language: 'hu', services: {} };
  const asked = [];
  const origPlanJourneys = Lib.planJourneys;
  const origPlanJourney = Lib.planJourney;
  Lib.planJourneys = async (key, o, d, h, opts) => { asked.push(opts); return [motis[0], Object.assign({}, motis[0], { startMs: iso(6, 30), endMs: iso(7, 40) })]; };
  Lib.planJourney = async () => null;
  pc.setConfig({ language: 'hu', stopId: 'hkir_773521', stopName: 'Népliget', destStopId: 'hkir_665908', destName: 'Székesfehérvár', destKey: 'szekesfehervar' });
  await new Promise((r) => setTimeout(r, 20));
  const root = pc.shadowRoot;
  const seg = [...root.querySelectorAll('#pwhen button')].map((b) => b.textContent);
  check('planner offers Most, Indulás and Érkezés', JSON.stringify(seg) === JSON.stringify(['Most', 'Indulás', 'Érkezés']), JSON.stringify(seg));
  check('planner title is the card title, not origin → destination again',
    root.querySelector('.head').textContent === 'Tervező');
  check('now mode shows the look-ahead and hides the time field',
    !root.querySelector('#pmin').hidden && root.querySelector('#pat').hidden);
  root.querySelector('[data-when="depart"]').click();
  await new Promise((r) => setTimeout(r, 20));
  const at = root.querySelector('#pat');
  check('Indulás plans for the picked time',
    pc._when.mode === 'depart' && pc._when.at > 0 && asked.length === 1
    && asked[0].time === pc._when.at && !asked[0].arriveBy, JSON.stringify(asked));
  check('time mode shows the time field and hides the look-ahead and live board',
    !at.hidden && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(at.value)
    && root.querySelector('#pmin').hidden && root.querySelector('.body').hidden, at.value);
  check('time field round-trips through Budapest time',
    pc._parseWhenInput(pc._whenInputValue(iso(6, 5))) === iso(6, 5));
  const heads = root.querySelectorAll('.itin-head');
  check('itineraries are listed with the first one open',
    heads.length === 2 && root.querySelectorAll('.itin.open').length === 1
    && !!root.querySelector('.itin.open #pjmap') && root.innerHTML.includes('Útvonalak'),
    String(heads.length));
  check('itinerary shows times, duration and the line badge',
    root.querySelector('.itin-time').textContent.includes('–')
    && root.querySelector('.itin-dur').textContent === '1 ó 10 p'
    && root.querySelector('.itin-legs').innerHTML.includes('>1205<'));
  check('timeline lists walk, wait, ride and arrival',
    [...root.querySelectorAll('.itin.open .tl li')].map((li) => li.className).join(',') === 'tl-walk,tl-wait,tl-ride,tl-end'
    && root.querySelector('.tl-ride .tl-stop').textContent === 'Népliget'
    && root.querySelector('.tl-end .tl-stop').textContent === 'Székesfehérvár');
  heads[1].click();
  check('tapping another itinerary opens it instead',
    root.querySelectorAll('.itin.open').length === 1
    && root.querySelectorAll('.itin-head')[1].getAttribute('aria-expanded') === 'true');
  root.querySelector('[data-when="arrive"]').click();
  await new Promise((r) => setTimeout(r, 20));
  check('Érkezés asks for arrival by the same time',
    asked.length === 2 && asked[1].arriveBy === true && asked[1].time === pc._when.at
    && root.innerHTML.includes('-ig'), JSON.stringify(asked[1]));
  root.querySelector('[data-when="now"]').click();
  await new Promise((r) => setTimeout(r, 20));
  check('Most goes back to the live board',
    pc._when.mode === 'now' && root.querySelector('#pat').hidden && !root.querySelector('#pmin').hidden);
  root.querySelector('[data-when="depart"]').click();
  await new Promise((r) => setTimeout(r, 20));
  root.querySelector('#preset').click();
  check('Új resets the time to now', pc._when.mode === 'now');
  Lib.planJourneys = origPlanJourneys;
  Lib.planJourney = origPlanJourney;
  pc.remove();
}

/* National pass (Országbérlet) on seat-reservation trains. */
{
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({ vehicle: 'rail', rawType: 'RAIL', dep, sched: dep }, extra);
  const merged = Lib.mergeVolanRows(
    [rail({ label: 'IC', trainNumber: '950', tripId: 'BKK_950_313' })],
    [rail({ label: 'GÖCSEJ', trainNumber: '950', booking: true, nationalPass: true, reservationFreeSection: true, tripId: 'elvira:950', destHit: false })],
    12,
    { overlayOnly: true },
  );
  check('ELVIRA overlay copies the national-pass flags onto the FUTÁR train',
    merged.length === 1 && merged[0].booking === true && merged[0].nationalPass === true
    && merged[0].reservationFreeSection === true, JSON.stringify(merged[0]));
  const asRow = Lib.asRow(merged[0], 'Székesfehérvár', 'hu');
  check('asRow keeps the national-pass flags', asRow.nationalPass === true && asRow.reservationFreeSection === true);

  const pc = document.createElement('hungarian-transport-card');
  document.body.appendChild(pc);
  pc.hass = { states: {}, language: 'hu' };
  pc.setConfig({ language: 'hu' });
  const now2 = Math.floor(Date.now() / 1000);
  const base = { type: 'RAIL', icon: 'mdi:train', headsign: 'Szombathely', depTs: now2 + 600, schedTs: now2 + 600,
    attime: '10:10', predicted_attime: '10:10', color: '4477aa', textcolor: 'ffffff', delay: 0, travelMin: 50 };
  pc._rows = [
    Object.assign({}, base, { label: 'GÖCSEJ', booking: true, nationalPass: true, reservationFreeSection: true }),
    Object.assign({}, base, { label: 'TENKES', booking: true, nationalPass: false, depTs: now2 + 900 }),
    Object.assign({}, base, { label: 'S40', booking: false, nationalPass: true, depTs: now2 + 1200 }),
  ];
  pc._paint();
  const rowsHtml = [...pc.shadowRoot.querySelectorAll('tr[data-row-index]')].map((tr) => tr.innerHTML);
  check('seat-reservation train valid with the pass shows OB next to H',
    rowsHtml[0].includes('class="picto pass"') && rowsHtml[0].includes('>OB<')
    && rowsHtml[0].includes('data-tip="Országbérlettel igénybe vehető"'), rowsHtml[0].slice(0, 300));
  check('H tooltip mentions the reservation-free domestic section',
    rowsHtml[0].includes('Helyjegy váltása kötelező · a kijelölt belföldi szakaszon 2. osztályon helyjegy nélkül is'));
  check('seat-reservation train without the pass gets no OB', rowsHtml[1].includes('>H<') && !rowsHtml[1].includes('>OB<'));
  check('no OB on trains without a seat reservation', !rowsHtml[2].includes('>OB<') && !rowsHtml[2].includes('>H<'));
  pc.remove();
}

/* Map stop pins: boarding and alighting of every ride. */
{
  const pins = Lib.journeyStopPins([
    { walk: true, shape: [[47.496, 18.994], [47.501, 19.001]], from: 'Otthon', to: 'Fő tér' },
    { walk: false, shape: [[47.501, 19.001], [47.520, 19.050]], from: 'Fő tér', to: 'Zugló vasútállomás' },
    { walk: false, shape: [[47.5202, 19.0502], [47.530, 19.090]], from: 'Zugló vasútállomás', to: 'Mester utca' },
    { walk: true, shape: [[47.530, 19.090], [47.534, 19.097]], from: 'Mester utca', to: 'Munkahely' },
  ]);
  check('journey pins: start, board, transfer at the same stop, alight, end',
    JSON.stringify(pins.map((p) => [p.kind, p.name])) === JSON.stringify([
      ['start', 'Otthon'], ['board', 'Fő tér'], ['transfer', 'Zugló vasútállomás'],
      ['alight', 'Mester utca'], ['end', 'Munkahely'],
    ]),
    JSON.stringify(pins.map((p) => [p.kind, p.name])));
  const walked = Lib.journeyStopPins([
    { walk: false, shape: [[47.50, 19.00], [47.52, 19.05]], from: 'A', to: 'B' },
    { walk: true, shape: [[47.52, 19.05], [47.53, 19.06]], from: 'B', to: 'C' },
    { walk: false, shape: [[47.53, 19.06], [47.55, 19.10]], from: 'C', to: 'D' },
  ]);
  check('journey pins: a walk between rides keeps both stops',
    JSON.stringify(walked.map((p) => p.kind + ':' + p.name)) === JSON.stringify(['board:A', 'alight:B', 'board:C', 'alight:D']),
    JSON.stringify(walked.map((p) => p.kind + ':' + p.name)));
  const short = Lib.journeyStopPins([
    { walk: true, shape: [[47.4640, 19.0196], [47.4641, 19.0197]], from: 'Kelenföld vasútállomás', to: 'Kelenföld vasútállomás' },
    { walk: false, shape: [[47.4641, 19.0197], [47.4890, 19.0610]], from: 'Kelenföld vasútállomás', to: 'Astoria M' },
    { walk: true, shape: [[47.4890, 19.0610], [47.4893, 19.0614]], from: 'Astoria M', to: 'Astoria' },
  ]);
  check('journey pins: a few steps to the stop add no start or end pin',
    JSON.stringify(short.map((p) => p.kind)) === JSON.stringify(['board', 'alight']),
    JSON.stringify(short.map((p) => p.kind + ':' + p.name)));
  const sameStop = Lib.journeyStopPins([
    { walk: true, shape: [[47.5425, 19.1375], [47.54095, 19.13916]], from: 'Fő tér', to: 'Fő tér' },
    { walk: false, shape: [[47.54095, 19.13916], [47.51175, 19.09915]], from: 'Fő tér', to: 'Astoria M' },
    { walk: true, shape: [[47.51175, 19.09915], [47.5140, 19.1020]], from: 'Astoria M', to: 'Astoria' },
  ]);
  check('journey pins: a walk across the same stop adds no start or end pin',
    JSON.stringify(sameStop.map((p) => p.kind)) === JSON.stringify(['board', 'alight']),
    JSON.stringify(sameStop.map((p) => p.kind + ':' + p.name)));
  check('journey pins: no rides, no pins',
    Lib.journeyStopPins([{ walk: true, shape: [[47.5, 19], [47.6, 19.1]], from: 'A', to: 'B' }]).length === 0);
}
{
  const cache = {
    'trip:BKK_PINTEST': {
      sts: [{ stopId: 'S0' }, { stopId: 'S1' }, { stopId: 'S2' }, { stopId: 'S3' }],
      stops: {
        S0: { name: 'Kelenföld vasútállomás', lat: 47.4641, lon: 19.0197 },
        S1: { name: 'Fő tér', lat: 47.5701, lon: 19.1202 },
        S2: { name: 'Bosnyák tér', lat: 47.5143, lon: 19.1105 },
        S3: { name: 'Zugló vasútállomás', lat: 47.5163, lon: 19.0889 },
      },
      polyline: '',
      vehicle: null,
      nowSec: 0,
    },
  };
  const hop = await Lib.tripHopStops('test-key', cache, 'BKK_PINTEST', { id: 'BKK_NOPE', name: 'Fő tér' }, 'zugló vasútállomás');
  check('FUTÁR trip gives exact board and alight stops',
    !!hop && hop.board[0] === 47.5701 && hop.alight[0] === 47.5163,
    JSON.stringify(hop));
  const noDest = await Lib.tripHopStops('test-key', cache, 'BKK_PINTEST', { id: 'BKK_NOPE', name: 'Fő tér' }, 'Kelenföld vasútállomás');
  check('FUTÁR alight is only a stop after boarding',
    !!noDest && !!noDest.board && noDest.alight === null, JSON.stringify(noDest));
}
{
  const made = [];
  const layer = () => ({ addTo() { return this; }, setLatLng() {}, setIcon() {}, redraw() {} });
  const fakeMap = {
    setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {},
  };
  const fakeL = {
    map: () => fakeMap,
    tileLayer: () => layer(),
    divIcon: (o) => o,
    marker: (pt, o) => {
      const m = Object.assign(layer(), { pt, html: o.icon.html, anchor: o.icon.iconAnchor, size: o.icon.iconSize });
      made.push(m);
      return m;
    },
    polyline: () => layer(),
    latLngBounds: () => ({ extend() {} }),
  };
  const prevL = window.L;
  window.L = fakeL;
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const pinCard = document.createElement('hungarian-transport-card');
  document.body.appendChild(pinCard);
  pinCard.setConfig({
    language: 'hu', stopId: 'BKK_CSF03154', stopName: 'Fő tér',
    destKey: 'zugló', destName: 'Zugló vasútállomás', routeIds: [],
  });
  pinCard._loadRowMapShape = async () => ({
    shape: [[47.5701, 19.1202], [47.5163, 19.0889]], board: [47.5701, 19.1202], alight: [47.5163, 19.0889],
  });
  await pinCard._openVehicleMap({ label: '7', headsign: 'Albertfalva', color: '009EE3', tripId: 'BKK_T1' });
  const board = made.find((m) => m.html.includes('ht-stop-board'));
  const alight = made.find((m) => m.html.includes('ht-stop-alight'));
  check('row map marks the boarding stop with its name',
    !!board && board.pt[0] === 47.5701 && board.html.includes('Fő tér') && board.html.includes('Felszállás'),
    board && board.html);
  check('row map marks the alighting stop with its name',
    !!alight && alight.pt[0] === 47.5163 && alight.html.includes('Zugló vasútállomás') && alight.html.includes('Leszállás'),
    alight && alight.html);
  const foot = document.querySelector('.ht-map-overlay .ht-map-foot');
  check('row map footer explains both pins',
    !!foot && foot.textContent.includes('● Felszállás') && foot.textContent.includes('○ Leszállás'),
    foot && foot.textContent);
  pinCard._closeVehicleMap();
  made.length = 0;
  await pinCard._openVehicleMap({
    label: '1', headsign: 'Kelenföld', color: 'FFD800', textcolor: '000000', icon: 'mdi:tram',
    tripId: 'BKK_T2', hasGps: true, lat: 47.53, lon: 19.11,
  });
  const veh = made.find((m) => m.html.includes('ht-veh-dot'));
  check('the vehicle is a disc with its type icon, not a stop dot',
    !!veh && veh.html.includes('<ha-icon icon="mdi:tram">') && veh.html.includes('--ink:#000000')
    && !veh.html.includes('ht-stop-pin'),
    veh && veh.html);
  check('the vehicle marker is centred on its position',
    !!veh && JSON.stringify(veh.anchor) === JSON.stringify([16, 16]) && JSON.stringify(veh.size) === JSON.stringify([32, 32])
    && !/\.ht-veh-dot\{[^}]*translate/.test(src),
    veh && JSON.stringify({ anchor: veh.anchor, size: veh.size }));
  pinCard._closeVehicleMap();
  check('closing the map clears both stop markers',
    !pinCard._mapBoardMarker && !pinCard._mapAlightMarker && !document.querySelector('.ht-map-overlay'));

  pinCard.setConfig({ language: 'hu', stopId: 'BKK_CSF03154', stopName: 'Fő tér', routeIds: [] });
  check('a card without a destination has no alighting stop',
    (await pinCard._rowAlightPoint({ label: '7' }, [[47.5, 19.1]])) === null);
  pinCard.setConfig({ language: 'hu', stopId: 'BKK_CSF03154', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds: [] });
  const first = await pinCard._rowAlightPoint({ label: '7' }, [null, [47.5163, 19.0889], [47.1, 19.1]]);
  check('the trip’s own stop wins for the alighting point', !!first && first[0] === 47.5163, JSON.stringify(first));
  pinCard.remove();

  made.length = 0;
  plan._drawJourneyPolylines(fakeL, fakeMap, [
    { walk: false, color: '#005CA5', shape: [[47.50, 19.00], [47.52, 19.05]], from: 'Kelenföld', to: 'Deák Ferenc tér' },
    { walk: false, color: '#FFD800', shape: [[47.52, 19.05], [47.53, 19.06]], from: 'Deák Ferenc tér', to: 'Astoria' },
  ], { labels: true });
  check('journey map pins boarding, transfer and alighting with names',
    made.length === 3
    && made[0].html.includes('ht-stop-board') && made[0].html.includes('Kelenföld')
    && made[1].html.includes('ht-stop-transfer') && made[1].html.includes('Deák Ferenc tér')
    && made[2].html.includes('ht-stop-alight') && made[2].html.includes('Astoria'),
    JSON.stringify(made.map((m) => m.html.replace(/<[^>]+>/g, ' ').trim())));
  made.length = 0;
  plan._drawJourneyPolylines(fakeL, fakeMap, [
    { walk: false, color: '#005CA5', shape: [[47.50, 19.00], [47.52, 19.05]], from: 'Kelenföld', to: 'Deák Ferenc tér' },
  ], { interactive: false });
  check('the small journey preview has pins without labels',
    made.length === 2 && !made.some((m) => m.html.includes('ht-stop-lbl')));
  window.L = prevL;
  globalThis.requestAnimationFrame = prevRaf;
}

/* Rail pins: exact station points, and a pin stays on the drawn route. */
{
  const chord = [[47.4643, 19.0206], [47.1836, 18.4247]];
  const mid = [47.32395, 18.72265];
  check('route distance measures to the segment, not just its far-apart vertices',
    Lib.distanceToShapeMeters(mid, chord) < 50 && Lib.distanceToShapeMeters([47.4643, 19.0206], chord) < 1,
    String(Math.round(Lib.distanceToShapeMeters(mid, chord))));
  const route = [[47.2000, 18.4600], [47.1836, 18.4247], [47.1700, 18.3900]];
  const centre = [47.1853, 18.4142];
  const station = [47.18330, 18.42495];
  const picked = Lib.pinOnRoute(centre, [null, station], route);
  check('an off-route pin yields to a source that puts the stop on the route',
    picked[0] === station[0] && picked[1] === station[1], JSON.stringify(picked));
  check('the city-centre point really is off this route',
    Lib.distanceToShapeMeters(centre, route) > 500, String(Math.round(Lib.distanceToShapeMeters(centre, route))));
  const kept = Lib.pinOnRoute([47.1836, 18.4247], [[47.19, 18.40]], route);
  check('an on-route pin is kept', kept[0] === 47.1836, JSON.stringify(kept));
  const alone = Lib.pinOnRoute(centre, [], route);
  check('without a better source the pin is not guessed along the line', alone[0] === centre[0], JSON.stringify(alone));
  const szfv = Lib.knownStopCoords({ id: 'BKK_005503269', name: 'Székesfehérvár' });
  check('Székesfehérvár is the railway station, not the city centre',
    !!szfv && Lib.haversineMeters(szfv, [47.183613, 18.424723]) < 60, JSON.stringify(szfv));
  check('every hub alias points at its station',
    ['kelenföld', 'székesfehérvár', 'keleti', 'déli'].every((n) => {
      const pt = Lib.knownStopCoords({ name: n });
      return !!pt && Lib.distanceToShapeMeters(pt, [[pt[0], pt[1]]]) === 0;
    }));
}

/* Trains Transitous misses still get a route: FUTÁR's trip details. */
{
  const keep = {};
  ['mavShapeForRow', 'motisShapeForHop', 'hassTripShape', 'futarTripShape', 'tripHopStops', 'stopArea', 'transitousStopRef']
    .forEach((k) => { keep[k] = Lib[k]; });
  const futarLine = [[47.2300, 16.6200], [47.1836, 18.4247], [47.3157, 18.7880], [47.4643, 19.0206], [47.4989, 19.0250]];
  let futarCalls = 0;
  let motisResult = { shape: [], board: [47.1836, 18.4247], dest: [47.4643, 19.0206], coversHead: false, hit: null };
  Lib.mavShapeForRow = async () => [];
  Lib.motisShapeForHop = async () => motisResult;
  Lib.hassTripShape = async () => [];
  Lib.futarTripShape = async () => { futarCalls += 1; return futarLine; };
  Lib.tripHopStops = async () => ({ board: [47.1836, 18.4247], alight: [47.4643, 19.0206] });
  Lib.stopArea = async () => '';
  Lib.transitousStopRef = async () => null;
  const trainCard = document.createElement('hungarian-transport-card');
  document.body.appendChild(trainCard);
  trainCard.setConfig({
    language: 'hu', stopId: 'BKK_005503269', stopName: 'Székesfehérvár', mav: true,
    destKey: 'budapest-kelenföld', destName: 'Budapest-Kelenföld', apiKey: 'test-key', routeIds: [],
  });
  const train = { label: 'BAKONY', headsign: 'Budapest-Déli', type: 'RAIL', tripId: 'BKK_9051_316', trainNumber: '905' };
  const got = await trainCard._loadRowMapShape(train);
  check('a train Transitous misses draws FUTÁR’s whole route',
    futarCalls === 1 && Lib.validMapShape(got.shape).length === futarLine.length && got.shape[0][1] === 16.62,
    JSON.stringify({ futarCalls, n: Lib.validMapShape(got.shape).length }));
  check('its stop pins sit on that route',
    Lib.distanceToShapeMeters(got.board, got.shape) < 20 && Lib.distanceToShapeMeters(got.alight, got.shape) < 20,
    JSON.stringify({ board: got.board, alight: got.alight }));
  futarCalls = 0;
  motisResult = Object.assign({}, motisResult, { shape: [[47.1836, 18.4247], [47.3157, 18.7880], [47.4643, 19.0206]], coversHead: true });
  await trainCard._loadRowMapShape(train);
  check('with a Transitous route the FUTÁR trip is not fetched for the shape', futarCalls === 0, String(futarCalls));
  trainCard.remove();
  Object.assign(Lib, keep);
}

/* MÁV index schema 2: every stop pattern of a train, with its stations. */
{
  const st = [
    ['Szombathely', 4723510, 1663160], ['Székesfehérvár', 4718361, 1842472],
    ['Martonvásár', 4731570, 1878800], ['Budapest-Kelenföld', 4746432, 1902063],
  ];
  const enc = (pts) => {
    const e = (v) => { let x = v < 0 ? ~(v << 1) : v << 1; let o = ''; while (x >= 0x20) { o += String.fromCharCode((0x20 | (x & 0x1f)) + 63); x >>= 5; } return o + String.fromCharCode(x + 63); };
    let pl = 0; let pg = 0; let out = '';
    pts.forEach(([la, lo]) => { const a = Math.round(la * 1e5); const b = Math.round(lo * 1e5); out += e(a - pl) + e(b - pg); pl = a; pg = b; });
    return out;
  };
  const longLine = [[47.2351, 16.6316], [47.20, 17.5], [47.18361, 18.42472], [47.3157, 18.788], [47.46432, 19.02063]];
  const shortLine = [[47.18361, 18.42472], [47.3157, 18.788], [47.46432, 19.02063]];
  const idx2 = { v: 2, st, p: [enc(longLine), enc(shortLine)], n: { 905: [[0, [0, 1, 2, 3]], [1, [1, 2, 3]]] }, l: {} };
  const pick = Lib.pickMavVariant(idx2.n['905'], st, 'Székesfehérvár', 'Kelenföld vasútállomás');
  check('MÁV variant: serves both stations in order, BKK naming matches',
    !!pick && pick.variant[0] === 0 && pick.board === 1 && pick.alight === 3, JSON.stringify(pick));
  const keepIdx = Lib.mavIndex;
  Lib.mavIndex = async () => idx2;
  const route = await Lib.mavRouteForRow({ label: 'BAKONY', trainNumber: '905', type: 'RAIL' }, 'Székesfehérvár', 'Budapest-Kelenföld');
  check('MÁV route: the variant’s line and its own station pins',
    !!route && route.shape.length === 5 && route.board[0] === 47.18361 && route.alight[0] === 47.46432,
    JSON.stringify(route && { n: route.shape.length, board: route.board, alight: route.alight }));
  Lib.mavIndex = async () => ({ v: 1, p: [enc(shortLine)], n: { 905: 0 }, l: {} });
  const old = await Lib.mavRouteForRow({ label: 'BAKONY', trainNumber: '905', type: 'RAIL' }, '', '');
  check('MÁV route: an old schema-1 index still draws', !!old && old.shape.length === 3 && old.board === null);

  Lib.mavIndex = async () => idx2;
  const trainCard = document.createElement('hungarian-transport-card');
  document.body.appendChild(trainCard);
  trainCard.setConfig({ language: 'hu', stopId: 'BKK_005503269', stopName: 'Székesfehérvár', mav: true,
    destKey: 'budapest-kelenföld', destName: 'Budapest-Kelenföld', routeIds: [] });
  const got = await trainCard._loadRowMapShape({ label: 'BAKONY', headsign: 'Budapest-Déli', type: 'RAIL', tripId: 'elvira:905', trainNumber: '905' });
  check('a train row draws the index route with pins on its own stations',
    got.shape.length === 5 && got.board[0] === 47.18361 && got.alight[0] === 47.46432 && !got.approx,
    JSON.stringify({ n: got.shape.length, board: got.board, alight: got.alight }));
  trainCard.remove();
  Lib.mavIndex = keepIdx;
}
{
  const keep = {};
  ['mavIndex', 'gtfsRowShape', 'motisShapeForHop', 'hassTripShape', 'futarTripShape', 'tripHopStops', 'stopArea', 'transitousStopRef', 'coachShape']
    .forEach((k) => { keep[k] = Lib[k]; });
  Lib.mavIndex = async () => ({ v: 2, st: [], p: [], n: {}, l: {} });
  Lib.gtfsRowShape = async () => null;
  Lib.motisShapeForHop = async () => ({ shape: [], board: null, dest: null, coversHead: false, hit: null });
  Lib.hassTripShape = async () => [];
  Lib.futarTripShape = async () => [];
  Lib.tripHopStops = async () => null;
  Lib.stopArea = async () => '';
  Lib.transitousStopRef = async () => null;
  const lone = document.createElement('hungarian-transport-card');
  document.body.appendChild(lone);
  lone.setConfig({ language: 'hu', stopId: 'BKK_005503269', stopName: 'Székesfehérvár', mav: true,
    destKey: 'budapest-kelenföld', destName: 'Budapest-Kelenföld', routeIds: [] });
  const got = await lone._loadRowMapShape({ label: 'IC', headsign: 'Budapest-Déli', type: 'RAIL', tripId: 'elvira:999', trainNumber: '999' });
  check('no source at all still gives a line, marked approximate',
    got.approx === true && got.shape.length === 2 && got.shape[0][0] === got.board[0],
    JSON.stringify(got));
  lone.remove();
  Object.assign(Lib, keep);
}

/* Review fixes (1.9.1): each block runs the real code path the bug was in.
   The maps above still have resize frames queued; from here on they run. */
globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
{
  /* The real MÁV loader, not a mocked mavIndex: schema 2 needs its st table. */
  const { gzipSync } = await import('node:zlib');
  const encPts = (pts) => {
    let out = '';
    let plat = 0;
    let plng = 0;
    const one = (v) => {
      let x = v < 0 ? ~(v << 1) : (v << 1);
      let s = '';
      while (x >= 0x20) { s += String.fromCharCode((0x20 | (x & 0x1f)) + 63); x >>= 5; }
      return s + String.fromCharCode(x + 63);
    };
    pts.forEach(([la, lo]) => {
      const a = Math.round(la * 1e5);
      const b = Math.round(lo * 1e5);
      out += one(a - plat) + one(b - plng);
      plat = a;
      plng = b;
    });
    return out;
  };
  const idx = {
    v: 2,
    feed: 'test',
    st: [
      ['Székesfehérvár', 4718361, 1842472],
      ['Budapest-Kelenföld', 4746432, 1902063],
      ['Szombathely', 4723000, 1662000],
      ['Veszprém', 4709000, 1791000],
    ],
    p: [
      encPts([[47.23, 16.62], [47.09, 17.91], [47.18361, 18.42472]]),
      encPts([[47.18361, 18.42472], [47.3, 18.7], [47.46432, 19.02063]]),
    ],
    /* The longest pattern first, as the builder writes it; it does not
       reach Kelenföld, so the card must read st to skip it. */
    n: { 905: [[0, [2, 3, 0]], [1, [0, 1]]] },
    l: {},
  };
  const gz = gzipSync(Buffer.from(JSON.stringify(idx)));
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    arrayBuffer: async () => gz.buffer.slice(gz.byteOffset, gz.byteOffset + gz.byteLength),
  });
  const keepIdx = Lib.mavIndex;
  Lib.mavIndex = origMavIndex;
  Lib._mavP.clear();
  const loaded = await Lib.mavIndex();
  check('MÁV index loader keeps the station table', Array.isArray(loaded.st) && loaded.st.length === 4 && loaded.v === 2,
    JSON.stringify(Object.keys(loaded)));
  const route = await Lib.mavRouteForRow({ label: 'IC', trainNumber: '905', type: 'RAIL' }, 'Székesfehérvár', 'Budapest-Kelenföld');
  check('MÁV variant and pins come from the loaded index',
    !!route && !!route.board && !!route.alight && route.board[0] === 47.18361 && route.alight[0] === 47.46432
    && route.shape[route.shape.length - 1][0] === 47.46432,
    JSON.stringify(route && { board: route.board, alight: route.alight, end: route.shape[route.shape.length - 1] }));
  globalThis.fetch = realFetch;
  Lib._mavP.clear();
  Lib.mavIndex = keepIdx;
}
{
  /* A body that stalls after its headers ends at the timeout. */
  const realFetch = globalThis.fetch;
  const keepMs = Lib._indexTimeoutMs;
  Lib._indexTimeoutMs = 50;
  globalThis.fetch = async (_url, init) => ({
    ok: true, status: 200,
    arrayBuffer: () => new Promise((_res, rej) => {
      init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }),
  });
  const within = (p) => Promise.race([p, new Promise((r) => setTimeout(() => r('hung'), 2000))]);
  const mav = await within(Lib._loadMavIndex('http://x/mav-index.json.gz'));
  check('a stalled MÁV index body times out to an empty index',
    mav !== 'hung' && Array.isArray(mav.p) && mav.p.length === 0, String(mav === 'hung' ? 'hung' : JSON.stringify(mav)));
  const gtfs = await within(Lib._loadGtfsIndex('http://x/volan-index.json.gz', 'errVolanHttp', 'errVolanGunzip')
    .then(() => 'resolved', (err) => err && err.i18nKey));
  check('a stalled GTFS index body times out with a fetch error', gtfs === 'errIndexFetch', String(gtfs));
  globalThis.fetch = realFetch;
  Lib._indexTimeoutMs = keepMs;
  check('Transitous reads its body under the timeout', /return await res\.json\(\)/.test(src));
}
{
  /* GTFS times count from noon minus 12 h; on DST days that is not midnight. */
  const at = (ymd, mins) => new Date((Lib.gtfsDayBase(ymd) + mins * 60) * 1000).toISOString();
  check('GTFS 08:00 on the autumn DST day is 08:00 CET', at('20261025', 480) === '2026-10-25T07:00:00.000Z', at('20261025', 480));
  check('GTFS 08:00 on the spring DST day is 08:00 CEST', at('20270328', 480) === '2027-03-28T06:00:00.000Z', at('20270328', 480));
  check('GTFS 08:00 on an ordinary day is 08:00 local', at('20261024', 480) === '2026-10-24T06:00:00.000Z', at('20261024', 480));
  check('GTFS 25:30 is 01:30 the next morning', at('20261020', 1530) === '2026-10-20T23:30:00.000Z', at('20261020', 1530));
  const day = Lib.volanDay(0, Date.UTC(2026, 9, 25, 10, 0));
  check('service-day minutes on the DST day follow the GTFS base',
    day.ymd === '20261025' && day.mins === 660 && day.base === Lib.gtfsDayBase('20261025'), JSON.stringify(day));
  const prev = Lib.volanDay(-1, Date.UTC(2026, 9, 25, 10, 0));
  const next = Lib.volanDay(1, Date.UTC(2026, 9, 25, 10, 0));
  check('yesterday runs past 24:00 and tomorrow is still ahead',
    prev.ymd === '20261024' && prev.mins === 660 + 1440 + 60 && next.ymd === '20261026' && next.mins < 0,
    JSON.stringify({ prev, next }));
  check('no GTFS time is counted from midnight any more', !/\.midnight\b/.test(src));
}
{
  /* Cached trip details: the ride is measured inside one snapshot. */
  const T0 = 1800000000;
  const cache = {
    'trip:BKK_TRAVELTEST': {
      sts: [
        { stopId: 'S1', departureTime: T0, predictedDepartureTime: T0 + 300 },
        { stopId: 'S2', arrivalTime: T0 + 600, predictedArrivalTime: T0 + 900 },
        { stopId: 'S3', arrivalTime: T0 + 900, predictedArrivalTime: T0 + 1200 },
      ],
      stops: { S1: { name: 'Fő tér' }, S2: { name: 'Köztes' }, S3: { name: 'Zugló vasútállomás' } },
      polyline: '', vehicle: null, nowSec: T0,
    },
  };
  /* The delay grew from 5 to 11 minutes since the snapshot. */
  const ride = await Lib.travelMin('test-key', 'BKK_TRAVELTEST', 'Zugló vasútállomás', T0 + 660, { id: 'S1', name: 'Fő tér' }, cache);
  check('a grown delay does not shorten the ride', ride === 15, String(ride));
  cache['trip:BKK_TRAVELPLAN'] = {
    sts: [
      { stopId: 'S1', departureTime: T0 },
      { stopId: 'S3', arrivalTime: T0 + 1080, predictedArrivalTime: T0 + 1500 },
    ],
    stops: cache['trip:BKK_TRAVELTEST'].stops, polyline: '', vehicle: null, nowSec: T0,
  };
  const plan = await Lib.travelMin('test-key', 'BKK_TRAVELPLAN', 'Zugló vasútállomás', T0 + 420, { id: 'S1', name: 'Fő tér' }, cache);
  check('without an origin prediction both ends use the timetable', plan === 18, String(plan));
}
{
  const { refreshSeconds } = await import('./src/config.js');
  check('refresh that is not a number falls back to 45 s',
    refreshSeconds('30s') === 45 && refreshSeconds(undefined) === 45 && refreshSeconds(-5) === 45 && refreshSeconds('') === 45);
  check('refresh is clamped to 15 s .. 1 h',
    refreshSeconds(5) === 15 && refreshSeconds('60') === 60 && refreshSeconds(99999) === 3600);
  check('the card polls with the parsed refresh, one run at a time',
    /refreshSeconds\(cfg\.refresh\)/.test(src) && /if \(busy\) return;/.test(src) && /\}, every\);/.test(src));
}
{
  /* Two opens racing the first Leaflet load leave one overlay, and it closes. */
  const layer = () => ({ addTo() { return this; }, setLatLng() {}, setIcon() {}, redraw() {} });
  const fakeMap = () => ({
    setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {},
  });
  const fakeL = {
    map: () => fakeMap(), tileLayer: () => layer(), divIcon: (o) => o, marker: () => layer(),
    polyline: () => layer(), latLngBounds: () => ({ extend() {} }),
  };
  const overlays = () => document.querySelectorAll('.ht-map-overlay').length;
  const raceCard = document.createElement('hungarian-transport-card');
  document.body.appendChild(raceCard);
  raceCard.setConfig({ language: 'hu', stopId: 'BKK_CSF03154', stopName: 'Fő tér', routeIds: [] });
  raceCard._loadRowMapShape = async () => ({ shape: [[47.57, 19.12], [47.51, 19.08]], board: [47.57, 19.12], alight: null });
  let release;
  const gate = new Promise((r) => { release = r; });
  raceCard._ensureLeaflet = () => gate;
  const a = raceCard._openVehicleMap({ label: '7', headsign: 'Albertfalva', tripId: 'BKK_RACE_A' });
  const b = raceCard._openVehicleMap({ label: '9', headsign: 'Óbuda', tripId: 'BKK_RACE_B' });
  release(fakeL);
  await Promise.all([a, b]);
  check('two map opens during the Leaflet load leave one overlay',
    overlays() === 1 && raceCard._openMapKey && raceCard._openMapKey.includes('BKK_RACE_B'),
    String(overlays()));
  raceCard._closeVehicleMap();
  check('that overlay closes', overlays() === 0, String(overlays()));
  raceCard._ensureLeaflet = async () => fakeL;
  await raceCard._openVehicleMap({ label: '7', headsign: 'Albertfalva', tripId: 'BKK_RACE_C' });
  raceCard.remove();
  check('a detached card takes its map overlay with it', overlays() === 0, String(overlays()));

  const planCard = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(planCard);
  planCard.setConfig({ language: 'hu', stopId: 'BKK_CSF03154', stopName: 'Fő tér', destName: 'Astoria', routeIds: [] });
  planCard._ensureLeaflet = async () => fakeL;
  planCard._loadRowMapShape = raceCard._loadRowMapShape;
  planCard._journey = { legs: [], durationMin: 10, transfers: 0, walkMin: 1, waitMin: 0 };
  const keep = { fillJourneyShapes: Lib.fillJourneyShapes, journeyMapSegments: Lib.journeyMapSegments };
  let releaseShapes;
  Lib.fillJourneyShapes = () => new Promise((r) => { releaseShapes = r; });
  Lib.journeyMapSegments = () => [{ walk: false, color: '#005CA5', shape: [[47.5, 19.0], [47.52, 19.05]], from: 'Fő tér', to: 'Astoria' }];
  const journey = planCard._openJourneyMap();
  await planCard._openVehicleMap({ label: '7', headsign: 'Albertfalva', tripId: 'BKK_RACE_D' });
  releaseShapes();
  await journey;
  check('a row map opened while the journey map loads stays the only overlay',
    overlays() === 1 && planCard._openMapKey !== 'journey', String(overlays()));
  planCard._closeVehicleMap();
  check('the row map still closes', overlays() === 0, String(overlays()));
  Object.assign(Lib, keep);
  planCard.remove();
}

/* Review follow-ups: name matching, journey choice, planner state, polling. */
{
  check('a place key does not join two named towns',
    !Lib.nameEq('Győr, autóbusz-állomás', 'Szeged, autóbusz-állomás')
    && !Lib.nameEq('Kecskemét, Széchenyi tér', 'Szeged, Széchenyi tér')
    && Lib.nameEq('Szeged, autóbusz-állomás', 'Szeged, autóbusz-állomás')
    && Lib.nameEq('Budapest-Keleti', 'Keleti pályaudvar')
    && Lib.nameEq('Budapest-Kelenföld', 'Kelenföld vasútállomás'));
  const towns = Lib.destHitsForQuery([
    { key: 'győr, autóbusz-állomás', name: 'Győr, autóbusz-állomás', id: 'volan_1' },
    { key: 'szeged, autóbusz-állomás', name: 'Szeged, autóbusz-állomás', id: 'volan_2' },
  ], '', []);
  check('two towns’ bus stations stay two destinations, each with its own route key',
    towns.length === 2 && towns[0].key === 'győr, autóbusz-állomás' && towns[1].key === 'szeged, autóbusz-állomás',
    JSON.stringify(towns.map((h) => h.key)));
  const merged = Lib.destHitsForQuery([
    { key: 'kelenföld', name: 'Kelenföld vasútállomás', id: 'BKK_005501024' },
  ], 'kelen', [{ id: 'volan_773538_99', name: 'Budapest, Kelenföld vasútállomás' }]);
  check('a search hit and a listed destination merge, keeping the list key for routes',
    merged.length === 1 && merged[0].key === 'kelenföld' && merged[0].routeKeys.join() === 'kelenföld',
    JSON.stringify(merged));
  const fold = (n) => ({ key: Lib.norm(n), fold: Lib.fold(n) });
  check('a destination town matches whole words, not the start of another town',
    !Lib.volanDestHit(fold('Tatabánya, autóbusz-állomás'), 'tata', Lib.fold('Tata'))
    && !Lib.volanDestHit(fold('Egerszalók, posta'), 'eger', Lib.fold('Eger'))
    && Lib.volanDestHit(fold('Tata, vasútállomás'), 'tata', Lib.fold('Tata'))
    && Lib.volanDestHit(fold('Budapest, Kelenföld vasútállomás'), 'kelenföld', Lib.fold('Kelenföld')));
}
{
  const at = (min) => new Date(Date.UTC(2026, 9, 20, 8, min)).toISOString();
  const soon = { startTime: at(5), endTime: at(65), duration: 3600, legs: [{ mode: 'BUS' }] };
  const later = { startTime: at(170), endTime: at(228), duration: 3480, legs: [{ mode: 'BUS' }] };
  check('the journey that arrives first wins, not the shortest of a three-hour window',
    Lib.bestItinerary([later, soon], (it) => Date.parse(it.endTime)) === soon);
  const direct = { endTime: at(65), duration: 3600, legs: [{ mode: 'RAIL' }] };
  const change = { endTime: at(65), duration: 3600, legs: [{ mode: 'BUS' }, { mode: 'WALK' }, { mode: 'TRAM' }] };
  check('an equal arrival prefers fewer rides', Lib.bestItinerary([change, direct], (it) => Date.parse(it.endTime)) === direct);
  const { t } = await import('./src/i18n.js');
  check('the English journey summary counts transfers',
    t('en', 'plannerJourneySummary', [30, 0, 2, 0]).includes('0 transfers')
    && t('en', 'plannerJourneySummary', [30, 1, 2, 0]).includes('1 transfer ')
    && t('en', 'plannerJourneySummary', [30, 2, 2, 0]).includes('2 transfers'));
}
{
  /* After midnight, yesterday's 24:30 trip is tonight's 00:30 ride. */
  const realNow = Date.now;
  Date.now = () => Date.UTC(2026, 9, 20, 22, 20);
  const idx = { c: [['1111111', '20200101', '20301231']], t: [[0, 'R1', [0, 1], [1470, 1480]]] };
  const ride = Lib.nextCityRide(idx, 0, 1, 'R1', Math.floor(Date.now() / 1000));
  Date.now = realNow;
  check('a city ride from yesterday’s service day is found tonight',
    !!ride && ride.dep === Date.UTC(2026, 9, 20, 22, 30) / 1000, JSON.stringify(ride));
}
{
  const m = new Map();
  Lib._cacheIndex(m, 'http://x/volan-index.json.gz?v=20261017', Promise.resolve(1), 8);
  Lib._cacheIndex(m, 'http://x/city-index.json.gz?v=20261017', Promise.resolve(2), 8);
  Lib._cacheIndex(m, 'http://x/volan-index.json.gz?v=20261018', Promise.resolve(3), 8);
  check('a new day drops yesterday’s copy of the same index, and only that',
    m.size === 2 && m.has('http://x/volan-index.json.gz?v=20261018') && m.has('http://x/city-index.json.gz?v=20261017'),
    JSON.stringify(Array.from(m.keys())));
  let reads = 0;
  const conn = { sendMessagePromise: async () => { reads += 1; return { views: [] }; } };
  const k1 = await Lib.apiKeyFromDashboard({ panelUrl: 'dash-memo', connection: conn });
  const n1 = reads;
  const k2 = await Lib.apiKeyFromDashboard({ panelUrl: 'dash-memo', connection: conn });
  check('a dashboard without a key is not read again on the next update',
    k1 === '' && k2 === '' && n1 === 2 && reads === 2, String(reads));
  const keepSearch = Lib.searchStops;
  let searches = 0;
  Lib.searchStops = async () => { searches += 1; return []; };
  const c1 = await Lib.elviraResolveCode('test-key', 'BKK_NOCODE', 'Sehollakó');
  const s1 = searches;
  const c2 = await Lib.elviraResolveCode('test-key', 'BKK_NOCODE', 'Sehollakó');
  Lib.searchStops = keepSearch;
  check('a station without an ELVIRA code is not searched on every poll',
    c1 === '' && c2 === '' && s1 > 0 && searches === s1, String(searches));
}
{
  /* 45 direct rows fill the list, yet a through bus leaving sooner belongs in it. */
  const now = Math.floor(Date.now() / 1000);
  const stopTimes = [];
  const trips = {};
  for (let i = 0; i < 45; i++) {
    stopTimes.push({ tripId: `A${i}`, departureTime: now + 120 + i * 60 });
    trips[`A${i}`] = { routeId: 'R_A', tripHeadsign: 'Zugló' };
  }
  stopTimes.push({ tripId: 'B1', departureTime: now + 150 });
  trips.B1 = { routeId: 'R_B', tripHeadsign: 'Rákospalota' };
  const board = { data: { entry: { stopTimes }, references: { trips, routes: { R_A: { shortName: '7', type: 'BUS' }, R_B: { shortName: '9', type: 'BUS' } }, stops: {} } } };
  const keep = { departureBoard: Lib.departureBoard, tripGoesTo: Lib.tripGoesTo, originPoles: Lib.originPoles };
  Lib.departureBoard = async () => board;
  Lib.originPoles = async () => [];
  Lib.tripGoesTo = async (_k, _c, tripId) => tripId === 'B1';
  let armed = null;
  let full = null;
  const early = await Lib.departures('test-key', 'BKK_F1', [], { key: 'zugló', name: 'Zugló' }, {
    mode: 'bkk', minutesAfter: 180, cache: {},
    armThroughCheck: (fn) => { armed = fn; },
    onFutarUpdate: (rows) => { full = rows; },
  });
  if (armed) armed();
  await new Promise((r) => setTimeout(r, 20));
  Object.assign(Lib, keep);
  check('a full list of direct rows still checks the through trips leaving before its end',
    !!armed && !!full && full.length === early.length && full.some((r) => r.tripId === 'B1')
    && full.map((r) => r.dep).every((d, i, a) => i === 0 || a[i - 1] <= d),
    JSON.stringify({ armed: !!armed, early: early.length, full: full && full.length, b1: !!(full && full.some((r) => r.tripId === 'B1')) }));
}
{
  /* A through-check row is a new object: it still gets its ride time. */
  const keep = { departures: Lib.departures, travelMin: Lib.travelMin };
  const now = Math.floor(Date.now() / 1000);
  const base = { label: '7', vehicle: 'bus', rawType: 'BUS', color: '#009EE3', text: '#ffffff' };
  const sureRow = Object.assign({ tripId: 'BKK_T1', head: 'Zugló', dep: now + 120, sched: now + 120 }, base);
  const throughRow = Object.assign({ tripId: 'BKK_T2', head: 'Óbuda', dep: now + 300, sched: now + 300 }, base);
  Lib.departures = async (_k, _s, _r, _d, opts) => {
    opts.armThroughCheck(() => {
      setTimeout(() => opts.onFutarUpdate([Object.assign({ travel: null }, sureRow), Object.assign({ travel: null }, throughRow)]), 5);
    });
    return [Object.assign({ travel: null }, sureRow)];
  };
  Lib.travelMin = async (_k, tripId) => (tripId === 'BKK_T2' ? 15 : 7);
  const hop = document.createElement('hungarian-transport-card');
  document.body.appendChild(hop);
  hop.setConfig({ language: 'hu', apiKey: 'test-key', stopId: 'BKK_F1', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds: [] });
  hop._considerJourney = () => {};
  /* setConfig starts the reload; wait for its through-check to land. */
  for (let i = 0; i < 50; i++) {
    const done = (hop._rawRows || []).find((r) => r.tripId === 'BKK_T2' && r.travel != null);
    if (done && !hop._reloadActive) break;
    await new Promise((r) => setTimeout(r, 20));
  }
  const t2 = (hop._rawRows || []).find((r) => r.tripId === 'BKK_T2');
  const t1 = (hop._rawRows || []).find((r) => r.tripId === 'BKK_T1');
  check('a row the through-check adds gets its ride time',
    !!t2 && t2.travel === 15 && !!t1 && t1.travel === 7,
    JSON.stringify((hop._rawRows || []).map((r) => ({ id: r.tripId, travel: r.travel }))));
  hop.remove();
  Object.assign(Lib, keep);
}
{
  /* A failed Leaflet load is tried again on the next open. */
  const prevL = window.L;
  window.L = undefined;
  const probe = document.createElement('hungarian-transport-card');
  document.body.appendChild(probe);
  const p1 = probe._ensureLeaflet();
  const s1 = document.getElementById('hungarian-transport-leaflet-js');
  s1.dispatchEvent(new window.Event('error'));
  const r1 = await p1.then(() => 'ok', () => 'failed');
  await new Promise((r) => setTimeout(r, 0));
  const p2 = probe._ensureLeaflet();
  const s2 = document.getElementById('hungarian-transport-leaflet-js');
  check('a failed Leaflet load is retried, not remembered', r1 === 'failed' && p2 !== p1 && !!s2 && s2 !== s1);
  p2.catch(() => {});
  s2.dispatchEvent(new window.Event('error'));
  await new Promise((r) => setTimeout(r, 0));
  probe.remove();
  window.L = prevL;
}
{
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_F1', stopName: 'Fő tér', minutesAfter: 45 });
  let reloads = 0;
  pc._reloadSafe = () => { reloads += 1; };
  pc._loadDests = async () => {};
  pc._destRoutes = { 'győr, autóbusz-állomás': ['volan_1'], 'aut all': ['BKK_2'] };
  pc._journey = { legs: [{ walk: false }] };
  pc._rows = [{ label: 'old' }];
  const gen0 = pc._gen;
  pc._pickDest({ key: 'aut all', name: 'Győr, autóbusz-állomás', id: 'volan_x', routeKeys: ['győr, autóbusz-állomás', 'aut all'] });
  check('a picked town keeps its name in the destination key and all its routes',
    pc._config.destKey === 'győr, autóbusz-állomás'
    && pc._config.routeIds.indexOf('volan_1') >= 0 && pc._config.routeIds.indexOf('BKK_2') >= 0,
    JSON.stringify({ key: pc._config.destKey, ids: pc._config.routeIds }));
  check('picking a destination drops the old pair’s journey and rows',
    pc._journey === null && pc._rows.length === 0 && pc._gen > gen0 && reloads === 1);
  pc._journey = { legs: [{ walk: false }] };
  pc._config = Object.assign({}, pc._config, { destStopId: 'volan_x', destName: 'Győr, autóbusz-állomás' });
  const gen1 = pc._gen;
  pc._swapPlan();
  check('a swap plans the new pair at once, without the old journey',
    pc._journey === null && pc._gen > gen1 && reloads === 2 && pc._config.stopName === 'Győr, autóbusz-állomás');
  pc._rows = [{ label: 'x' }];
  pc._loadedAt = Date.now();
  const fresh = pc._shouldReloadOnConnect();
  pc._loadedAt = Date.now() - 10 * 60 * 1000;
  const stale = pc._shouldReloadOnConnect();
  check('the planner plans again when shown after a while', fresh === false && stale === true);
  pc._paintMinutesAfter();
  const sel = pc._planEl('pmin');
  check('a look-ahead that is not a preset still shows in the select',
    !!sel && sel.value === '45', sel && sel.value);
  const realNow = Date.now;
  Date.now = () => Date.UTC(2026, 9, 24, 22, 30);
  const label = pc._whenText(Date.UTC(2026, 9, 26, 6, 30));
  Date.now = realNow;
  check('on the DST night, tomorrow is the next calendar day', label.startsWith('holnap'), label);
  pc.remove();
}

/* Origin suggestions: the stops this user picks, from their HA storage. */
{
  const DAY = 86400000;
  const now = Date.now();
  const stored = [
    { id: 'hkir_773521', name: 'Budapest, Népliget autóbusz-pályaudvar', n: 2, t: now - DAY },
    { id: 'BKK_005503269', name: 'Székesfehérvár', n: 5, t: now - 120 * DAY },
    { id: 'BKK_CSF03154', name: 'Fő tér', n: 1, t: now - 2 * DAY },
  ];
  const writes = [];
  const userHass = {
    states: {}, language: 'hu', connection: {},
    callWS: async (msg) => {
      if (msg.type === 'frontend/get_user_data') return { value: msg.key === Lib.STOP_HISTORY_KEY ? stored : null };
      if (msg.type === 'frontend/set_user_data') { writes.push(msg); return null; }
      return null;
    },
  };
  Lib._stopHistory = null;
  const own = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(own);
  own.hass = userHass;
  own.setConfig({ language: 'hu' });
  await new Promise((r) => setTimeout(r, 0));
  own._paintFav();
  const chips = Array.from(own.shadowRoot.querySelectorAll('#pfav .suggest-stop')).map((b) => b.textContent);
  check('origin suggestions are the user’s own stops, most used first, with their feed mark',
    chips.length === 3 && chips[0] === 'VNépliget' && chips[1] === 'BFő tér' && chips[2] === 'MSzékesfehérvár'
    && !own.shadowRoot.innerHTML.includes('Keleti pályaudvar'),
    JSON.stringify(chips));
  await own._pickOrigin({ id: 'BKK_CSF03154', name: 'Fő tér', lat: 47.57, lon: 19.12 });
  await new Promise((r) => setTimeout(r, 0));
  const saved = writes.length ? writes[writes.length - 1] : null;
  const foTer = saved && saved.value.find((e) => e.id === 'BKK_CSF03154');
  check('a picked origin is counted and stored for this user',
    !!saved && saved.key === Lib.STOP_HISTORY_KEY && !!foTer && foTer.n === 2 && foTer.lat === 47.57,
    JSON.stringify(saved && saved.value.map((e) => [e.id, e.n])));
  own._pickDest({ key: 'baja, autóbusz-állomás', name: 'Baja, autóbusz-állomás', id: 'volan_baja', routeKeys: [] });
  await new Promise((r) => setTimeout(r, 0));
  const afterDest = writes[writes.length - 1].value;
  check('a picked destination counts too, without its route keys',
    afterDest.some((e) => e.id === 'volan_baja' && !('routeKeys' in e) && !('key' in e)),
    JSON.stringify(afterDest.map((e) => e.id)));
  own.remove();
  Lib._stopHistory = null;
  const bare = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(bare);
  bare.hass = { states: {}, language: 'hu', connection: {}, callWS: async () => ({ value: null }) };
  bare.setConfig({ language: 'hu' });
  await new Promise((r) => setTimeout(r, 0));
  check('with no history there are no suggestions', bare.shadowRoot.querySelectorAll('#pfav .suggest-stop').length === 0);
  bare.remove();
  Lib._stopHistory = null;
  const capped = [];
  for (let i = 0; i < 30; i++) capped.push({ id: `S${i}`, name: `Stop ${i}`, n: i + 1, t: now });
  Lib._stopHistory = { conn: null, p: Promise.resolve(Lib._cleanStopHistory(capped)) };
  const kept = await Lib.recordStop(null, { id: 'NEW', name: 'New stop' });
  check('the history keeps the 24 most used stops', kept.length === 24 && kept[0].id === 'S29' && !kept.some((e) => e.id === 'S0'),
    JSON.stringify(kept.map((e) => e.id).slice(0, 3)));
  Lib._stopHistory = null;
}

process.exit(failed ? 1 : 0);

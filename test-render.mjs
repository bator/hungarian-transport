/* Renders the card in jsdom: it must draw its own rows, with no dependency on
   any other custom element. */
import { JSDOM } from 'jsdom';
import { errText } from './src/i18n.js';

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
  /* jsdom has no layout engine, so the phone layout is checked on the
     stylesheet: the board is a size container, and below its breakpoint the
     headsign owns the one flexible track instead of getting what the fixed
     columns leave (a letter or two at 320-400 px). */
  const rulesOf = (cssText) => {
    const el = document.createElement('style');
    el.textContent = cssText;
    document.head.appendChild(el);
    const rules = [...el.sheet.cssRules];
    el.remove();
    return rules;
  };
  const ruleFor = (rules, sel) => rules.find((r) => r.selectorText === sel);
  const prop = (rule, name) => (rule ? rule.style.getPropertyValue(name).trim() : '');
  const hopRules = rulesOf(card.shadowRoot.querySelector('style').textContent);
  check('departure board is a named inline-size container',
    /^ht-deps\s*\/\s*inline-size$/.test(prop(ruleFor(hopRules, '.body'), 'container')),
    prop(ruleFor(hopRules, '.body'), 'container'));
  const narrow = hopRules.find((r) => /^ht-deps\s*\(max-width:\s*\d+px\)$/.test(r.containerText || ''));
  const breakpoint = narrow ? Number(/(\d+)px/.exec(narrow.containerText)[1]) : 0;
  /* A 320-400 px card leaves a 296-376 px board; a 500 px desktop section
     column leaves 476 px and keeps the table layout. */
  check('narrow rows cover phone cards and spare a desktop column',
    breakpoint >= 376 && breakpoint < 476, narrow ? narrow.containerText : 'no @container ht-deps rule');
  const inner = narrow ? [...narrow.cssRules] : [];
  const tracks = prop(ruleFor(inner, 'tbody'), 'grid-template-columns').split(/\s+(?![^(]*\))/);
  const flexible = tracks.map((tr, i) => (/\dfr\b/.test(tr) ? i + 1 : 0)).filter(Boolean);
  check('narrow grid has exactly one flexible track and it may shrink to fit',
    flexible.length === 1 && /^minmax\(0(px)?,\s*1fr\)$/.test(tracks[flexible[0] - 1])
      && prop(ruleFor(inner, 'tr'), 'grid-template-columns') === 'subgrid',
    JSON.stringify(tracks));
  const narrowDest = ruleFor(inner, 'td.dest');
  const area = prop(narrowDest, 'grid-area').split('/').map((s) => Number(s.trim()));
  check('narrow headsign takes the flexible track and the map column beside it',
    area.length === 4 && area[0] === 1 && area[1] === flexible[0] && area[3] > area[1] + 1,
    prop(narrowDest, 'grid-area'));
  check('narrow headsign may shrink, wraps once and then ellipsizes',
    prop(narrowDest, 'min-width') === '0' && prop(narrowDest, 'max-width') === 'none'
      && prop(narrowDest, 'white-space') === 'normal'
      && prop(narrowDest, '-webkit-line-clamp') === '2',
    narrowDest ? narrowDest.style.cssText : 'no narrow td.dest rule');
  const startCol = (sel) => Number(prop(ruleFor(inner, sel), 'grid-area').split('/')[1]);
  check('narrow pictograms sit under the headsign and the times keep their own columns',
    startCol('td.flags') === flexible[0] && Number(prop(ruleFor(inner, 'td.flags'), 'grid-area').split('/')[0]) === 2
      && startCol('td.when') > flexible[0] + 1 && startCol('td.in') > startCol('td.when'),
    ['td.flags', 'td.when', 'td.in'].map((s) => prop(ruleFor(inner, s), 'grid-area')).join(' | '));
  const wideDest = ruleFor(hopRules, 'td.dest');
  check('wide headsign keeps the one-line ellipsis table cell',
    prop(wideDest, 'max-width') === '0' && prop(wideDest, 'white-space') === 'nowrap'
      && prop(wideDest, 'text-overflow') === 'ellipsis');

  /* The planner shares the board; its own picker rules must not reach the
     departure cells (a bare .route / .when once turned td.route into a grid). */
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  pc.hass = { states: {}, language: 'hu', services: {} };
  pc.setConfig({ language: 'hu' });
  pc._rows = card._rows;
  pc._paint();
  const cells = [...pc.shadowRoot.querySelectorAll('tr[data-row-index] td')];
  const plannerRules = [...pc.shadowRoot.querySelectorAll('style')].slice(1)
    .flatMap((el) => rulesOf(el.textContent));
  const leaks = plannerRules.filter((r) => r.selectorText && !/\btd\b/.test(r.selectorText)
    && cells.some((td) => { try { return td.matches(r.selectorText); } catch { return false; } }));
  check('planner rows render departure cells', cells.length >= 7, String(cells.length));
  check('planner picker rules do not style departure cells',
    plannerRules.length > 20 && leaks.length === 0, leaks.map((r) => r.selectorText).join(', '));
  const plannerNarrow = plannerRules.find((r) => r.containerText === narrow?.containerText);
  const plannerCellPad = prop(ruleFor([...(plannerNarrow ? plannerNarrow.cssRules : [])], 'ha-card.is-planner td'), 'padding');
  check('planner drops its roomy cell padding on the narrow board',
    ['0', '0px'].includes(plannerCellPad),
    plannerNarrow ? plannerNarrow.cssText.slice(0, 160) : 'no matching planner @container rule');
  pc.remove();
}

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
check('planner does not auto-refresh, but its countdowns tick',
  plan._shouldPoll() === false
  && plan._tick !== null
  && card._shouldPoll() === true,
  JSON.stringify({ planPoll: plan._shouldPoll(), planTick: !!plan._tick, hopPoll: card._shouldPoll() }));
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
check('planner has look-ahead chips', planHtml.includes('id="pmin"') && planHtml.includes('El\u0151retekint\u00e9s'));
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
  plan.shadowRoot.querySelectorAll('#pfav .suggest-stop').length === 0);
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
check('loose trip-id number drops one digit only where the readings differ',
  Lib.trainNumberLooseFromTripId('BKK_8624_67') === '862'
  && Lib.trainNumberLooseFromTripId('BKK_2461_311') === '246'
  && Lib.trainNumberLooseFromTripId('BKK_8661_47') === ''
  && Lib.trainNumberLooseFromTripId('BKK_90681_106') === ''
  && Lib.trainNumberLooseFromTripId('BKK_865_74') === ''
  && Lib.trainNumberLooseFromTripId('elvira:2889849') === '');
/* Rákospatak utca, 2026-10-19: bus 7 to Albertfalva ran on the ad-hoc
   FUTÁR trip BKK_1792439004037 (a millisecond timestamp), which was shown
   as a train number. No Hungarian train number has more than five digits. */
check('a trip id body longer than five digits is no train number',
  Lib.trainNumberFromTripId('BKK_1792439004037') === ''
  && Lib.trainNumberFromTripId('BKK_179243') === ''
  && Lib.trainNumberFromTripId('BKK_25431_12') === '25431'
  && Lib.trainNumberLooseFromTripId('BKK_1792439004037') === ''
  && Lib.rowTrainNumber({ tripId: 'BKK_1792439004037' }) === ''
  && Lib.vehicleBucket({ tripId: 'BKK_1792439004037' }) === 'bus');
await (async () => {
  /* FUTÁR rows read a train number off the trip id only when they are rail:
     a bus on a numeric ad-hoc trip id has none. */
  const keep = { departureBoard: Lib.departureBoard, originPoles: Lib.originPoles };
  const now = Math.floor(Date.now() / 1000);
  const board = { data: {
    entry: { stopTimes: [
      { tripId: 'BKK_1792439004037', departureTime: now + 120 },
      { tripId: 'BKK_17924', departureTime: now + 180 },
      { tripId: 'BKK_4541_108', departureTime: now + 240 },
      { tripId: 'BKK_8624_67', departureTime: now + 300 },
      { tripId: 'BKK_2461_311', departureTime: now + 360 },
    ] },
    references: {
      trips: {
        BKK_1792439004037: { routeId: 'R7', tripHeadsign: 'Albertfalva vasútállomás' },
        BKK_17924: { routeId: 'R7', tripHeadsign: 'Albertfalva vasútállomás' },
        BKK_4541_108: { routeId: 'RS', tripHeadsign: 'Albertfalva vasútállomás' },
        BKK_8624_67: { routeId: 'RIC', tripHeadsign: 'Albertfalva vasútállomás' },
        BKK_2461_311: { routeId: 'RIC', tripHeadsign: 'Albertfalva vasútállomás', tripShortName: '246' },
      },
      routes: {
        R7: { id: 'R7', shortName: '7', type: 'BUS' },
        RS: { id: 'RS', shortName: 'S30', type: 'SUBURBAN_RAILWAY' },
        RIC: { id: 'RIC', shortName: 'IC', type: 'RAIL' },
      },
      stops: {},
    },
  } };
  Lib.departureBoard = async () => board;
  Lib.originPoles = async () => [];
  const dest = { key: 'albertfalva vasútállomás', name: 'Albertfalva vasútállomás' };
  const rows = await Lib.departures('k', 'BKK_F1', [], dest, { mode: 'all', minutesAfter: 60, cache: {} });
  Object.assign(Lib, keep);
  const num = (id) => (rows.find((r) => r.tripId === id) || {}).trainNumber;
  check('a FUTÁR bus row takes no train number from its trip id; rail rows still do',
    rows.length === 5 && num('BKK_1792439004037') === '' && num('BKK_17924') === ''
    && num('BKK_4541_108') === '4541' && num('BKK_8624_67') === '8624'
    && num('BKK_2461_311') === '246',
    JSON.stringify(rows.map((r) => [r.tripId, r.trainNumber])));
})();
/* Kelenföld → Székesfehérvár, 2026-10-19: FUTÁR gives two ICs no
   tripShortName, so their numbers are read off the trip id (8624, 2461);
   ELVIRA has them as BALATON 862 and CITADELLA 246, platform 4. */
const icFutar = (extra) => Object.assign({
  label: 'IC', color: '#2E5EA8', text: '#FFFFFF', vehicle: 'rail', rawType: 'RAIL',
  platform: '', booking: false, delay: 0,
}, extra);
const icElvira = (extra) => Object.assign({
  vehicle: 'rail', rawType: 'RAIL', color: '#4477aa', text: '#ffffff',
  head: 'Székesfehérvár', platform: '4', booking: true, destHit: true, delay: 0,
}, extra);
check('FUTÁR ICs BKK_8624_67 and BKK_2461_311 take ELVIRA BALATON 862 and CITADELLA 246', (() => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 25 * 60;
  const merged = Lib.mergeVolanRows(
    [
      icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 }),
      icFutar({ tripId: 'BKK_2461_311', trainNumber: '2461', head: 'Ljubljana', dep: t1, sched: t1 }),
    ],
    [
      icElvira({ tripId: 'elvira:balaton', label: 'BALATON', trainNumber: '862', dep: t0, sched: t0 }),
      icElvira({ tripId: 'elvira:citadella', label: 'CITADELLA', trainNumber: '246', dep: t1, sched: t1 }),
    ],
    12,
    { overlayOnly: true },
  );
  const balaton = merged.find((r) => r.tripId === 'BKK_8624_67');
  const citadella = merged.find((r) => r.tripId === 'BKK_2461_311');
  return merged.length === 2
    && !!balaton && balaton.label === 'BALATON' && balaton.platform === '4'
    && balaton.booking === true && balaton.trainNumber === '862' && balaton.head === 'Keszthely'
    && !!citadella && citadella.label === 'CITADELLA' && citadella.platform === '4'
    && citadella.booking === true && citadella.trainNumber === '246'
    /* EMMA GPS goes by the number: 8624 never met "862 BALATON". */
    && Lib.matchEmmaVehicle(balaton, { trip: { tripShortName: '862 BALATON InterCity' } });
})());
check('a trip-id IC takes no ELVIRA train one digit shorter outside the 5-minute window', (() => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const merged = Lib.mergeVolanRows(
    [icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 })],
    [icElvira({
      tripId: 'elvira:balaton', label: 'BALATON', trainNumber: '862',
      dep: t0 + 6 * 60, sched: t0 + 6 * 60,
    })],
    12,
    { overlayOnly: true },
  );
  return merged.length === 1 && merged[0].label === 'IC' && merged[0].trainNumber === '8624'
    && merged[0].platform === '' && merged[0].booking !== true;
})());
check('an IC ELVIRA renumbered stays one row when FUTÁR and ELVIRA merge onto it again', (() => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const futar = () => [icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 })];
  const elvira = [icElvira({ tripId: 'elvira:862', label: 'IC', trainNumber: '862', dep: t0, sched: t0 })];
  const first = Lib.mergeVolanRows(futar(), elvira, 12, { overlayOnly: true });
  const again = Lib.mergeVolanRows(Lib.mergeVolanRows(futar(), first, 12), elvira, 12, { overlayOnly: true });
  return first.length === 1 && first[0].trainNumber === '862' && first[0].platform === '4'
    && again.length === 1 && again[0].trainNumber === '862' && again[0].platform === '4'
    && again[0].booking === true && again[0].tripId === 'BKK_8624_67';
})());
check('suburban 4-digit trip ids take no 3-digit ELVIRA train in the same minute', (() => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 30 * 60;
  const merged = Lib.mergeVolanRows(
    [
      icFutar({
        label: 'Z30', color: '#FFCD28', tripId: 'BKK_4541_108', trainNumber: '4541',
        head: 'Székesfehérvár', dep: t0, sched: t0,
      }),
      icFutar({
        label: 'S', color: '#4477aa', tripId: 'BKK_4561_108', trainNumber: '4561',
        head: 'Székesfehérvár', dep: t1, sched: t1,
      }),
    ],
    [
      icElvira({ tripId: 'elvira:454', label: 'IC', trainNumber: '454', platform: '2', dep: t0 + 60, sched: t0 + 60 }),
      icElvira({ tripId: 'elvira:456', label: 'IC', trainNumber: '456', platform: '2', dep: t1, sched: t1 }),
    ],
    12,
    { overlayOnly: true },
  );
  return merged.length === 2
    && merged.every((r) => r.platform === '' && r.booking !== true)
    && merged[0].label === 'Z30' && merged[0].trainNumber === '4541'
    && merged[1].label === 'S' && merged[1].trainNumber === '4561';
})());
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
check('the unused FUTÁR trip resolver and its helpers are gone',
  !/futarTripIdForRow|matchFutarStopTime|rowTimeKey|needsFutarMapResolve|hasLocation/.test(src));
check('EMMA tripShortName 861 BALATON yields train number 861',
  Lib.emmaTrainNumber('861 BALATON InterCity') === '861');
check('emmaTrainNumber reads IC 856 TÓPART',
  Lib.emmaTrainNumber('IC 856 TÓPART') === '856'
  && Lib.emmaTrainNumber('S40') === '');
check('pickMotisVehicle matches generic IC to IC 856 TÓPART', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
check('hop map uses MÁV GTFS full line for trains, Transitous for buses',
  src.includes('motisShapeForHop')
  && src.includes('mav-index.json.gz')
  && src.includes('/api/v5/stoptimes')
  && src.includes('_mapBoardMarker')
  && src.includes('combineHopShapes')
  && src.includes('stitchShapes')
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
{
  /* An empty or failed MÁV index is kept for a while: every train-row map
     open must not wait 45 s on a stalled server again. */
  const keepLoad = Lib._loadMavIndex;
  Lib._mavP.clear();
  Lib._mavEmptyAt.clear();
  let loads = 0;
  Lib._loadMavIndex = async () => { loads += 1; return { n: {}, l: {}, p: [] }; };
  const first = await Lib.mavIndex();
  await Lib.mavIndex();
  check('an empty MÁV index is not refetched at once',
    loads === 1 && Array.isArray(first.p) && first.p.length === 0 && Lib._mavEmptyAt.size === 1,
    JSON.stringify({ loads, emptyAt: Lib._mavEmptyAt.size }));
  const url = Array.from(Lib._mavEmptyAt.keys())[0];
  Lib._mavEmptyAt.set(url, Date.now() - Lib._mavEmptyRetryMs - 1);
  Lib._loadMavIndex = async () => { loads += 1; throw new Error('stalled'); };
  const retried = await Lib.mavIndex();
  await Lib.mavIndex();
  check('after the hold the MÁV index is fetched again, and a failure is held too',
    loads === 2 && retried.p.length === 0 && Lib._mavEmptyAt.has(url),
    JSON.stringify({ loads }));
  Lib._mavEmptyAt.set(url, Date.now() - Lib._mavEmptyRetryMs - 1);
  Lib._loadMavIndex = async () => { loads += 1; return { v: 2, st: [], n: { 1: 0 }, l: {}, p: ['abc'] }; };
  const good = await Lib.mavIndex();
  await Lib.mavIndex();
  check('a loaded MÁV index clears the hold and stays cached',
    loads === 3 && good.p.length === 1 && !Lib._mavEmptyAt.has(url),
    JSON.stringify({ loads, held: Lib._mavEmptyAt.has(url) }));
  Lib._mavP.clear();
  Lib._mavEmptyAt.clear();
  Lib._loadMavIndex = keepLoad;
}
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
  && src.includes('matchHassVehicle'));
check('pickMotisVehicle matches ELVIRA ADRIA to Transitous AGRAM', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
check('pickMotisVehicle does not alias TÓPART onto AGRAM at another hour', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
check('pickMotisVehicle unique close rail dep matches a renamed IC', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
  let serviceCalls = 0;
  card._hass = { connection: { sendMessagePromise: async () => { serviceCalls += 1; return {}; } } };
  const origMotis = Lib.motisShapeForHop;
  const origHassService = Lib.hassService;
  const motisShape = [[47.18, 18.42], [47.46, 19.02]];
  try {
    Lib.motisShapeForHop = async () => ({ shape: motisShape, coversHead: true });
    Lib.hassService = async () => { serviceCalls += 1; return null; };
    const got = await card._loadRowMapShape({
      label: 'GÖCSEJ', trainNumber: '954', vehicle: 'rail',
      tripId: 'BKK_954_1', lat: 47.50, lon: 19.55,
      shape: motisShape,
    });
    /* bkk_stop has no trip_shape service: a train map never asks HA for one. */
    check('loadRowMapShape never calls Home Assistant for a train shape',
      serviceCalls === 0 && got.shape.length === 2 && got.shape[1][1] === 19.02,
      JSON.stringify({ serviceCalls, shape: got.shape }));
  } finally {
    Lib.motisShapeForHop = origMotis;
    Lib.hassService = origHassService;
  }
}
check('pickMotisVehicle matches a bus route number', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
check('pickMotisVehicle matches train number not a later same-label hop', (() => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const hit = Lib.pickMotisVehicle([
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
check('no card code asks bkk_stop for a trip_shape',
  !src.includes('hassTripShape') && !src.includes("'trip_shape'"));
{
  /* The bkk_stop companion is optional: without it (or without the service)
     no call_service round trip is made, the call resolves empty at once. */
  let calls = 0;
  const conn = { sendMessagePromise: async () => { calls += 1; return { vehicles: [{ id: 'v1' }], points: 'abc' }; } };
  const bare = { connection: conn, services: { hungarian_transport: { futar: {} } } };
  const withCompanion = { connection: conn, services: { bkk_stop: { coach_positions: {}, coach_shape: {} } } };
  check('hasCompanionService reads hass.services.bkk_stop',
    !Lib.hasCompanionService(bare, 'coach_positions') && !Lib.hasCompanionService(null, 'coach_shape')
    && !Lib.hasCompanionService({ connection: conn, services: { bkk_stop: {} } }, 'coach_shape')
    && Lib.hasCompanionService(withCompanion, 'coach_positions'));
  Lib._coachPosCache = null;
  const noPos = await Lib.coachPositions(bare);
  const noShape = await Lib.coachShape(bare, '1050', 'Győr');
  const noSvc = await Lib.hassService(bare, 'coach_positions', {});
  check('without the companion coachPositions and coachShape return empty with no service call',
    calls === 0 && Array.isArray(noPos) && noPos.length === 0 && noShape === '' && noSvc === null,
    JSON.stringify({ calls, noPos, noShape, noSvc }));
  Lib._coachPosCache = null;
  const pos = await Lib.coachPositions(withCompanion);
  const shape = await Lib.coachShape(withCompanion, '1050', 'Győr');
  check('with the companion the coach services are called',
    calls === 2 && pos.length === 1 && pos[0].id === 'v1' && shape === 'abc',
    JSON.stringify({ calls, pos, shape }));
  Lib._coachPosCache = null;
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
  && src.includes('packed && packed.polyline'));
check('decodePolyline accepts a FUTAR polyline object',
  Lib.decodePolyline({ points: '_p~iF~ps|U' }).length >= 1);
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
  && plan.shadowRoot.querySelector('.horizon > #pmin'));
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
const mavIc = ((await Lib.mavRouteForRow({ label: 'IC 856 TÓPART', trainNumber: '856', vehicle: 'rail' }, '', '')) || {}).shape || [];
check('mavRouteForRow IC 856 is Déli to Nagykanizsa',
  mavIc.length === 3
  && Math.abs(mavIc[0][1] - 19.025) < 1e-3
  && Math.abs(mavIc[mavIc.length - 1][1] - 16.987) < 1e-2,
  JSON.stringify(mavIc));
const mavG43 = ((await Lib.mavRouteForRow({ label: 'G43', vehicle: 'rail' }, '', '')) || {}).shape || [];
check('mavRouteForRow G43 uses the line code',
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

/* Expected outages are part of the service response, not service errors:
   {data: null, error, detail} from futar, {departures: [], error, detail}
   from elvira_between. */
{
  const viaWs = (response) => ({
    states: {},
    language: 'en',
    services: { hungarian_transport: { futar: {}, elvira_between: {} } },
    connection: { sendMessagePromise: async () => ({ context: {}, response }) },
  });
  const failure = async (hass) => {
    Lib._hass = hass;
    try {
      await Lib.fetch('', 'search.json', { query: 'Keleti' }, true);
      return null;
    } catch (err) {
      return err;
    }
  };
  const down = await failure(viaWs({ data: null, error: 'futar_unavailable', detail: 'HTTP 503' }));
  check('futar_unavailable is a translated outage banner carrying the detail',
    !!down && down.code === 'futar_unavailable' && down.i18nKey === 'errFutarDown'
    && errText('hu', down).includes('HTTP 503') && errText('en', down) === 'BKK FUTÁR is unavailable (HTTP 503). The card tries again on the next refresh.',
    down && errText('en', down));
  const bare = await failure(viaWs({ data: null, error: 'futar_unavailable' }));
  check('futar error without detail names the error key',
    !!bare && errText('hu', bare).includes('futar_unavailable'), bare && bare.message);
  const rejected = await failure(viaWs({ data: null, error: 'futar_auth', detail: 'UNAUTHORIZED' }));
  check('futar_auth is the bad-key banner, in the card\u2019s language',
    !!rejected && rejected.code === 'futar_auth' && rejected.i18nKey === 'errApiKeyBad'
    && errText('hu', rejected).startsWith('A BKK API kulcs érvénytelen'),
    rejected && rejected.message);
  Lib._hass = viaWs({ data: null, error: 'futar_auth', detail: 'UNAUTHORIZED' });
  const probe = await Lib.probeApiKey('');
  check('key probe reads futar_auth as a bad key, whatever the detail',
    !probe.ok && probe.code === 'errApiKeyBad', JSON.stringify(probe));
  const limited = await failure(viaWs({ data: null, error: 'futar_rate_limited', detail: 'rate limited' }));
  check('futar_rate_limited keeps its code for the board fallback and reads as transient',
    !!limited && limited.code === 'futar_rate_limited' && limited.i18nKey === 'errFutarRateLimited',
    limited && limited.message);
  const empty = await failure(viaWs({}));
  check('futar response without data or error is still an empty response',
    !!empty && empty.message === 'BKK empty response', empty && empty.message);
  Lib._hass = null;

  const warns = [];
  const realWarn = console.warn;
  console.warn = (...args) => { warns.push(args.map(String).join(' ')); };
  try {
    Lib._elviraDownUntil = 0;
    Lib._elviraWarned = false;
    let answer = { departures: [], error: 'elvira_unavailable', detail: 'HTTP 503' };
    let asked = 0;
    const hass = {
      states: {},
      language: 'en',
      services: { hungarian_transport: { elvira_between: {} } },
      connection: { sendMessagePromise: async () => { asked += 1; return { context: {}, response: answer }; } },
    };
    const poll = () => Lib.elviraBetween(hass, 'BKK_005510009', 'BKK_005501024', 60, 'Szeged');
    const first = await poll();
    const second = await poll();
    check('elvira_unavailable gives no rows and warns once per outage, not per poll',
      Array.isArray(first) && first.length === 0 && Array.isArray(second) && second.length === 0
      && warns.length === 1 && /elvira_unavailable \(HTTP 503\)/.test(warns[0]),
      JSON.stringify(warns));
    check('an error payload is an answer: no backoff, the next poll still asks',
      asked === 2 && Lib._elviraDownUntil === 0, JSON.stringify({ asked, until: Lib._elviraDownUntil }));
    answer = { departures: [{ label: 'IC 712' }] };
    const back = await poll();
    answer = { departures: [], error: 'elvira_unavailable', detail: 'TimeoutError' };
    await poll();
    check('a good ELVIRA answer ends the outage; the next one warns again',
      back.length === 1 && back[0].label === 'IC 712' && warns.length === 2
      && /TimeoutError/.test(warns[1]),
      JSON.stringify(warns));
    const other = () => Lib.elviraBetween(hass, 'BKK_005510009', 'BKK_005517228', 60, 'Debrecen');
    await other();
    await poll();
    check('each station pair has its own outage',
      warns.length === 3, JSON.stringify(warns));
    answer = { departures: [], error: 'elvira_rate_limited', detail: 'rate limited' };
    const limited = await poll();
    answer = { departures: [], error: 'elvira_unavailable', detail: 'TimeoutError' };
    await poll();
    check('elvira_rate_limited gives no rows, no outage warning, and leaves the outage as it was',
      Array.isArray(limited) && limited.length === 0 && warns.length === 3
      && Lib._elviraDownUntil === 0, JSON.stringify(warns));
    Lib._elviraOutages.clear();
    answer = { departures: [], error: 'elvira_rate_limited', detail: 'rate limited' };
    await poll();
    check('a rate-limited answer does not mark a pair that was fine as down',
      warns.length === 3 && Lib._elviraOutages.size === 0, JSON.stringify(warns));
  } finally {
    console.warn = realWarn;
    Lib._elviraOutages.clear();
    Lib._elviraDownUntil = 0;
    Lib._elviraWarned = false;
  }
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
  ['mavRouteForRow', 'motisShapeForHop', 'futarTripShape', 'tripHopStops', 'stopArea', 'transitousStopRef']
    .forEach((k) => { keep[k] = Lib[k]; });
  const futarLine = [[47.2300, 16.6200], [47.1836, 18.4247], [47.3157, 18.7880], [47.4643, 19.0206], [47.4989, 19.0250]];
  let futarCalls = 0;
  let motisResult = { shape: [], board: [47.1836, 18.4247], dest: [47.4643, 19.0206], coversHead: false, hit: null };
  Lib.mavRouteForRow = async () => null;
  Lib.motisShapeForHop = async () => motisResult;
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
  /* The number read off a FUTÁR trip id is another train's (8714, Székesfehérvár
     to Siófok) for an Ajka–Déli train: its patterns serve the boarding station
     but neither the alighting one nor the headsign, so there is no route from
     the index, and the trip's own polyline draws instead. */
  const st3 = st.concat([['Siófok', 4690500, 1805600], ['Budapest-Déli', 4750000, 1902400]]);
  const collide = [[0, [1, 4]], [0, [3, 1, 4]]];
  check('MÁV variant: a pattern serving the boarding station only, towards another terminus, is no match',
    Lib.pickMavVariant(collide, st3, 'Székesfehérvár', 'Budapest-Kelenföld', 'Budapest-Déli') === null);
  const viaHead = [[0, [1, 4]], [0, [0, 1, 5]]];
  const byHead = Lib.pickMavVariant(viaHead, st3, 'Székesfehérvár', 'Kelenföld vasútállomás', 'Budapest-Déli');
  check('MÁV variant: without the alighting station, the pattern ending at the row\u2019s headsign serves',
    !!byHead && byHead.variant[1][2] === 5 && byHead.board === 1 && byHead.alight === -1, JSON.stringify(byHead));
  check('MÁV variant: without a boarding station the first pattern is taken',
    !!Lib.pickMavVariant(collide, st3, '', '', '') && Lib.pickMavVariant([], st3, '', '', '') === null);
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
  ['mavIndex', 'gtfsRowShape', 'motisShapeForHop', 'futarTripShape', 'tripHopStops', 'stopArea', 'transitousStopRef', 'coachShape']
    .forEach((k) => { keep[k] = Lib[k]; });
  Lib.mavIndex = async () => ({ v: 2, st: [], p: [], n: {}, l: {} });
  Lib.gtfsRowShape = async () => null;
  Lib.motisShapeForHop = async () => ({ shape: [], board: null, dest: null, coversHead: false, hit: null });
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
  check('refresh is clamped to 20 s (the shared board lifetime) .. 1 h',
    refreshSeconds(5) === 20 && refreshSeconds(15) === 20 && refreshSeconds('60') === 60 && refreshSeconds(99999) === 3600);
  check('the card polls with the parsed refresh, one run at a time',
    /refreshSeconds\(cfg\.refresh\)/.test(src) && /if \(pollRun\.busy\) return;/.test(src) && /\}, every\);/.test(src));
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
    t('en', 'plannerJourneySummary', [30, 0, 2, 0]).includes('direct')
    && t('hu', 'plannerJourneySummary', [30, 0, 2, 0]).includes('átszállás nélkül')
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
  /* Móricz Zsigmond körtér (2026-10-21): FUTÁR gives the tram poles a stand
     code (49 → Deák from B1). The parent station's board names the pole on
     each stop time; a single pole's board leaves stopId off and names the
     pole in its entry. Both rows carry B1, and the card shows it as a stop,
     not as a rail track. */
  const now = Math.floor(Date.now() / 1000);
  const refs = (stopTimes) => ({
    trips: Object.fromEntries(stopTimes.map((st) => [st.tripId, { routeId: 'BKK_3490', tripHeadsign: 'Deák Ferenc tér M' }])),
    routes: { BKK_3490: { shortName: '49', type: 'TRAM', color: 'FFD800', textColor: '000000' } },
    stops: {
      BKK_CSF02198: { name: 'Móricz Zsigmond körtér' },
      BKK_F02203: { name: 'Móricz Zsigmond körtér M', platformCode: 'B1', parentStationId: 'BKK_CSF02198' },
    },
  });
  const parentTimes = [{ tripId: 'T_PARENT', stopId: 'BKK_F02203', departureTime: now + 120 }];
  const poleTimes = [{ tripId: 'T_POLE', departureTime: now + 240 }];
  const boards = {
    BKK_CSF02198: { data: { entry: { stopId: 'BKK_CSF02198', stopTimes: parentTimes }, references: refs(parentTimes) } },
    BKK_F02203: { data: { entry: { stopId: 'BKK_F02203', stopTimes: poleTimes }, references: refs(poleTimes) } },
  };
  const keep = { departureBoard: Lib.departureBoard, tripGoesTo: Lib.tripGoesTo, originPoles: Lib.originPoles };
  Lib.departureBoard = async (_k, pid) => boards[pid];
  Lib.originPoles = async () => [];
  Lib.tripGoesTo = async () => true;
  const dest = { key: 'deák ferenc tér', name: 'Deák Ferenc tér' };
  const load = (stopId) => Lib.departures('test-key', stopId, [], dest, { mode: 'bkk', minutesAfter: 10, cache: {} });
  const fromParent = (await load('BKK_CSF02198')).find((r) => r.tripId === 'T_PARENT');
  const fromPole = (await load('BKK_F02203')).find((r) => r.tripId === 'T_POLE');
  Object.assign(Lib, keep);
  check('a tram row has its pole\'s stand code from the parent board and from the pole\'s own board alike',
    !!fromParent && !!fromPole && fromParent.platform === 'B1' && fromPole.platform === 'B1',
    JSON.stringify({ parent: fromParent && fromParent.platform, pole: fromPole && fromPole.platform }));
  const card = document.createElement('hungarian-transport-card');
  card.setConfig({ language: 'hu' });
  const tram = Lib.asRow(Object.assign({}, fromPole || {}), 'Deák Ferenc tér', 'hu');
  const train = Lib.asRow({ label: 'S30', rawType: 'RAIL', dep: now + 120, platform: '4', head: 'Székesfehérvár' }, 'Székesfehérvár', 'hu');
  const tramHu = card._rowHtml(tram, 'hu', 0);
  const tramEn = card._rowHtml(tram, 'en', 0);
  const trainHu = card._rowHtml(train, 'hu', 1);
  check('a tram stand code is a stop box with its own tooltip, a train keeps the track box',
    tramHu.includes('class="plat stand" title="Megállóhely B1">B1<')
    && tramEn.includes('class="plat stand" title="Stop B1">B1<')
    && !tramHu.includes('Vágány') && !tramEn.includes('Platform')
    && trainHu.includes('class="plat" title="Vágány 4">4<'),
    JSON.stringify({ tramHu: (tramHu.match(/<span class="plat[^<]*<\/span>/) || [''])[0], trainHu: (trainHu.match(/<span class="plat[^<]*<\/span>/) || [''])[0] }));
}
{
  /* The MÁV hop card end to end: ELVIRA lands on the first FUTÁR rows, then
     the through-check's fresh FUTÁR copies (numbers read off the trip id
     again) merge onto those rows and ELVIRA once more. */
  const keep = { departures: Lib.departures, elviraBetween: Lib.elviraBetween, travelMin: Lib.travelMin };
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 25 * 60;
  const board = () => [
    icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0, travel: null }),
    icFutar({ tripId: 'BKK_2461_311', trainNumber: '2461', head: 'Ljubljana', dep: t1, sched: t1, travel: null }),
  ];
  Lib.departures = async (_k, _s, _r, _d, opts) => {
    opts.armThroughCheck(() => { setTimeout(() => opts.onFutarUpdate(board()), 30); });
    return board();
  };
  Lib.elviraBetween = async () => [
    icElvira({ tripId: 'elvira:balaton', label: 'BALATON', trainNumber: '862', dep: t0, sched: t0, travel: 45 }),
    icElvira({ tripId: 'elvira:citadella', label: 'CITADELLA', trainNumber: '246', dep: t1, sched: t1, travel: 42 }),
  ];
  Lib.travelMin = async () => 45;
  const hop = document.createElement('hungarian-transport-card');
  hop.hass = { states: {}, language: 'hu', services: { hungarian_transport: { elvira_between: {} } } };
  document.body.appendChild(hop);
  hop.setConfig({
    language: 'hu', apiKey: 'test-key', mav: true, routeIds: [],
    stopId: 'BKK_005501024', stopName: 'Budapest-Kelenföld',
    destKey: 'szekesfehervar', destName: 'Székesfehérvár', destStopId: 'BKK_005503269',
  });
  hop._considerJourney = () => {};
  for (let i = 0; i < 50; i++) {
    if (!hop._reloadActive && (hop._rawRows || []).some((r) => r.platform === '4')) break;
    await new Promise((r) => setTimeout(r, 20));
  }
  const raw = hop._rawRows || [];
  const at = (tripId) => raw.findIndex((r) => r.tripId === tripId);
  const html = (tripId) => (at(tripId) >= 0 ? hop._rowHtml(hop._rows[at(tripId)], 'hu', at(tripId)) : '');
  const shows = (tripId, num) => html(tripId).includes('class="plat"') && html(tripId).includes('>4<')
    && html(tripId).includes('class="picto booking"') && html(tripId).includes(`>${num}</span>`);
  check('MÁV hop card shows platform 4 and the reservation mark on both trip-id ICs',
    raw.length === 2 && shows('BKK_8624_67', '862') && shows('BKK_2461_311', '246')
    && raw[at('BKK_8624_67')].label === 'BALATON' && raw[at('BKK_2461_311')].label === 'CITADELLA',
    JSON.stringify(raw.map((r) => ({ id: r.tripId, label: r.label, n: r.trainNumber, p: r.platform, b: r.booking }))));
  hop.remove();
  Object.assign(Lib, keep);
}
{
  /* Planner, Kelenföld → Székesfehérvár (2026-10-19 21:33): ELVIRA answers
     with the whole Kelenföld StationInfo, so G44 4711 to Budapest-Déli
     (platform 2) leaves in the same minute as Sz 8738 to Siófok (platform 4),
     and S30 4541 with 9068 to Veszprém. The planner's board rows carry the
     train number as their label (Transitous): the branded G44/S30 row must
     not land on them, and each takes its own ELVIRA platform, like the hop card. */
  const keep = {
    departures: Lib.departures, elviraBetween: Lib.elviraBetween, travelMin: Lib.travelMin,
    fillTransitousIfEmpty: Lib.fillTransitousIfEmpty, attachCoachGps: Lib.attachCoachGps,
  };
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 35 * 60;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', color: '#4477aa', text: '#ffffff', platform: '', delay: 0, travel: null,
  }, extra);
  const board = () => [
    rail({ tripId: 'motis:hu-mav_8738', label: '8738', trainNumber: '8738', head: 'Siófok', dep: t0 + 30, sched: t0 }),
    rail({ tripId: 'motis:hu-mav_9068', label: '9068', trainNumber: '9068', head: 'Veszprém', dep: t1 + 30, sched: t1 }),
  ];
  Lib.departures = async () => board();
  Lib.fillTransitousIfEmpty = async (rows) => rows || [];
  Lib.attachCoachGps = async (_h, rows) => rows;
  Lib.elviraBetween = async () => [
    rail({ tripId: 'elvira:4711', label: 'G44', trainNumber: '4711', head: 'Budapest-Déli', platform: '2', dep: t0, sched: t0 }),
    rail({ tripId: 'elvira:8738', label: '8738', trainNumber: '8738', head: 'Siófok', platform: '4', dep: t0 + 30, sched: t0, destHit: true }),
    rail({ tripId: 'elvira:4541', label: 'S30', trainNumber: '4541', head: 'Budapest-Déli', platform: '2', dep: t1, sched: t1 }),
    rail({ tripId: 'elvira:9068', label: '9068', trainNumber: '9068', head: 'Veszprém', platform: '4', dep: t1 + 30, sched: t1, destHit: true }),
  ];
  Lib.travelMin = async () => null;
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc.hass = { states: {}, language: 'hu', services: { hungarian_transport: { elvira_between: {} } } };
  pc._considerJourney = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.setConfig({
    language: 'hu', apiKey: 'test-key', routeIds: [],
    stopId: 'hu-mav_005501024', stopName: 'Kelenföld vasútállomás',
    destKey: 'székesfehérvár', destName: 'Székesfehérvár', destStopId: 'hu-mav_005503269',
  });
  for (let i = 0; i < 50; i++) {
    if (!pc._reloadActive && (pc._rawRows || []).some((r) => r.platform)) break;
    await new Promise((r) => setTimeout(r, 20));
  }
  const raw = pc._rawRows || [];
  const row = (n) => raw.find((r) => r.trainNumber === n) || {};
  check('planner MÁV rows keep their own label and ELVIRA platform, not a same-minute G44/S30',
    raw.length === 2
    && row('8738').label === '8738' && row('8738').platform === '4' && row('8738').head === 'Siófok'
    && row('9068').label === '9068' && row('9068').platform === '4' && row('9068').head === 'Veszprém'
    && !raw.some((r) => /^(G44|S30)$/.test(r.label)),
    JSON.stringify(raw.map((r) => ({ id: r.tripId, label: r.label, n: r.trainNumber, p: r.platform }))));
  pc.remove();
  Object.assign(Lib, keep);
}
check('a branded suburban train in the same minute stays off another numbered train', (() => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const host = (n, head) => ({
    tripId: 'motis:' + n, label: n, trainNumber: n, head, vehicle: 'rail', rawType: 'RAIL', platform: '', dep: t0, sched: t0,
  });
  const merged = Lib.mergeVolanRows(
    [host('8738', 'Siófok')],
    [{ tripId: 'elvira:4711', label: 'G44', trainNumber: '4711', head: 'Budapest-Déli', vehicle: 'rail', rawType: 'RAIL', platform: '2', dep: t0, sched: t0 }],
    12,
    { overlayOnly: true },
  );
  /* Without a number of its own, the branded row is still the same departure. */
  const unnumbered = Lib.mergeVolanRows(
    [host('8738', 'Siófok')],
    [{ tripId: 'gtfs:G44:1', label: 'G44', trainNumber: '', head: 'Siófok', vehicle: 'rail', rawType: 'RAIL', platform: '', dep: t0, sched: t0 }],
    12,
  );
  return merged.length === 1 && merged[0].label === '8738' && merged[0].platform === ''
    && unnumbered.length === 1 && unnumbered[0].label === 'G44' && unnumbered[0].trainNumber === '8738';
})());
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
  /* Drawn by the card itself once the history arrives: no manual paint. */
  const chipsNow = () => Array.from(own.shadowRoot.querySelectorAll('#pfav .suggest-stop')).map((b) => b.textContent);
  const chips = chipsNow();
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
  own._resetPlan();
  const afterReset = chipsNow();
  check('after a reset the suggestions show the stops just picked',
    afterReset.some((c) => c.includes('Baja')) && afterReset[0] === 'BFő tér'
    && !own._planEl('pfav').classList.contains('hidden'),
    JSON.stringify(afterReset));
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

/* ---- Card-side review batch ---- */

/* Sections view sizing. */
{
  const hop = document.createElement('hungarian-transport-card');
  const pl = document.createElement('hungarian-transit-stop-card-plan');
  const g1 = hop.getGridOptions();
  const g2 = pl.getGridOptions();
  check('both cards publish grid options for the sections view',
    g1.columns === 12 && g1.min_columns === 6 && g1.rows === 'auto'
    && g2.columns === 12 && g2.min_columns === 6 && g2.rows === 'auto' && g2.min_rows === 6,
    JSON.stringify({ g1, g2 }));
}

/* setConfig normalises hand-written YAML and tells a cosmetic edit from a data change. */
{
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  let reloads = 0;
  c._reloadSafe = () => { reloads += 1; };
  c.setConfig({ stopId: 42, stopName: 'Fő tér', destKey: 'x', destName: 7, routeIds: '9, 109,,', minutesAfter: 9999, volan: true });
  check('setConfig normalises routeIds, ids, names and minutesAfter',
    c._config.stopId === '42' && c._config.destName === '7'
    && JSON.stringify(c._config.routeIds) === '["9","109"]' && c._config.minutesAfter === 480
    && reloads === 1,
    JSON.stringify({ cfg: c._config, reloads }));
  c.setConfig({ stopId: '42', stopName: 'Fő tér', destKey: 'x', destName: '7', routeIds: 7, volan: true });
  check('a non-list routeIds becomes an empty list', JSON.stringify(c._config.routeIds) === '[]' && reloads === 2,
    JSON.stringify(c._config.routeIds));
  c._tripCache = { keep: 1 };
  c._rows = [{ label: 'kept', headsign: 'x', depTs: Math.floor(Date.now() / 1000) + 600 }];
  c._paint();
  c.setConfig({ stopId: '42', stopName: 'Fő tér', destKey: 'x', destName: '7', routeIds: [], volan: true, name: 'Új cím' });
  check('a title-only change repaints without a reload and keeps the rows and caches',
    reloads === 2 && c._tripCache.keep === 1 && c._rows.length === 1
    && c.shadowRoot.querySelector('.head').textContent === 'Új cím'
    && c.shadowRoot.innerHTML.includes('>kept<'),
    JSON.stringify({ reloads, head: c.shadowRoot.querySelector('.head').textContent }));
  c.setConfig({ stopId: '42', stopName: 'Fő tér', destKey: 'x', destName: '7', routeIds: [], volan: true, name: 'Új cím', language: 'en' });
  check('a language-only change does not reload either', reloads === 2, String(reloads));
  c.setConfig({ stopId: '42', stopName: 'Fő tér', destKey: 'x', destName: '7', routeIds: [], volan: true, name: 'Új cím', language: 'en', minutesAfter: 60 });
  check('a look-ahead change reloads', reloads === 3 && c._tripCache.keep === undefined, String(reloads));
  c.remove();

  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  ed.setConfig({ routeIds: 'a, b', stopId: 5, volan: true });
  check('the editor normalises its config the same way',
    JSON.stringify(ed._config.routeIds) === '["a","b"]' && ed._config.stopId === '5',
    JSON.stringify(ed._config));
  ed.remove();
}

/* Both stops set but FUTÁR out of reach: say so, not "no departures". */
{
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ stopId: 'BKK_F00001', stopName: 'Keleti', destKey: 'blaha', destName: 'Blaha' });
  await new Promise((r) => setTimeout(r, 10));
  const err = c.shadowRoot.querySelector('.err');
  check('without FUTÁR access the card shows the no-API-key error instead of no departures',
    !!err && err.textContent.includes('Nincs BKK API kulcs')
    && !c.shadowRoot.innerHTML.includes('Nincs közelgő indulás'),
    JSON.stringify({ err: err && err.textContent, body: c.shadowRoot.querySelector('.body').innerHTML.slice(0, 80) }));
  c.remove();
  const v = document.createElement('hungarian-transport-card');
  document.body.appendChild(v);
  v.hass = { states: {}, language: 'hu', services: {} };
  v._reloadSafe = () => {};
  v.setConfig({ stopId: 'volan_1', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa', volan: true });
  /* The ready branch paints "loading" before its first await; the not-ready
     branch sets the error synchronously. The fetch itself is then discarded. */
  const running = v._reload();
  const readyPath = !v._err && v._loading === true;
  v._gen += 1;
  running.catch(() => {});
  check('a Volán board without a key is not an error', readyPath, JSON.stringify({ err: v._err, loading: v._loading }));
  v.remove();
}

/* Background tab: no polling, no ticking; back in front: one run and the timers again. */
{
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  let reloads = 0;
  c._reloadSafe = () => { reloads += 1; };
  c.setConfig({ stopId: 'volan_1', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa', volan: true });
  c._poll = setInterval(() => {}, 1e6);
  let vis = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => vis });
  vis = 'hidden';
  document.dispatchEvent(new Event('visibilitychange'));
  check('a hidden page stops the poll and the tick', c._poll === null && c._tick === null && reloads === 1,
    JSON.stringify({ poll: !!c._poll, tick: !!c._tick, reloads }));
  c._startTick();
  check('the tick does not start while hidden', c._tick === null);
  vis = 'visible';
  document.dispatchEvent(new Event('visibilitychange'));
  check('back in front the card reloads once and ticks again', reloads === 2 && c._tick !== null,
    JSON.stringify({ reloads, tick: !!c._tick }));
  c.remove();
  c._tick = setInterval(() => {}, 1e6);
  vis = 'hidden';
  document.dispatchEvent(new Event('visibilitychange'));
  check('a detached card no longer listens to visibility', c._tick !== null && reloads === 2);
  clearInterval(c._tick);
  c._tick = null;
  delete document.visibilityState;
  check('hop card ticks and polls stop on hidden in source',
    src.includes("document.addEventListener('visibilitychange'") && src.includes("document.removeEventListener('visibilitychange'"));
}

/* Editor: typing the title must not restart a destination load in flight. */
{
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  const keepReach = Lib.reachableDests;
  let loads = 0;
  let release = null;
  Lib.reachableDests = () => { loads += 1; return new Promise((r) => { release = r; }); };
  ed.setConfig({ volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'G' });
  ed.setConfig({ volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'Gy' });
  ed.setConfig({ volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'Győ' });
  check('intermediate setConfigs do not restart the destination load',
    loads === 1 && ed._destsLoading && ed._loadingStop === 'volan_1', JSON.stringify({ loads, loading: ed._destsLoading }));
  release({ dests: [{ key: 'pápa', name: 'Pápa' }], destRoutes: { 'pápa': ['volan_9'] } });
  await new Promise((r) => setTimeout(r, 0));
  ed.setConfig({ volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'Győr' });
  check('once loaded the stop is remembered and not loaded again',
    loads === 1 && ed._loadedStop === 'volan_1' && !ed._destsLoading && ed._dests.length === 1,
    JSON.stringify({ loads, loaded: ed._loadedStop }));
  ed.setConfig({ volan: true, stopId: 'volan_2', stopName: 'Pápa', name: 'Győr' });
  check('a new stop loads its destinations', loads === 2 && ed._loadingStop === 'volan_2', String(loads));
  release({ dests: [], destRoutes: {} });
  await new Promise((r) => setTimeout(r, 0));
  Lib.reachableDests = keepReach;
  ed.remove();
}

/* Stop history: per HA user in localStorage, null from HA is an answer, writes queue. */
{
  const store = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
  });
  const key = Lib.STOP_HISTORY_KEY;
  store.set(key, JSON.stringify([{ id: 'SHARED', name: 'Shared' }]));
  store.set(`${key}:u1`, JSON.stringify([{ id: 'MINE', name: 'Mine' }]));
  Lib._stopHistory = null;
  const failing = { connection: { a: 1 }, user: { id: 'u1' }, callWS: async () => { throw new Error('offline'); } };
  const fallback = await Lib.loadStopHistory(failing);
  check('a failed get_user_data falls back to this user\'s own browser copy',
    fallback.length === 1 && fallback[0].id === 'MINE', JSON.stringify(fallback));
  Lib._stopHistory = null;
  const empty = { connection: { b: 1 }, user: { id: 'u1' }, callWS: async () => ({ value: null }) };
  const none = await Lib.loadStopHistory(empty);
  check('a null answer from Home Assistant shows this user’s own browser picks, never the shared old key',
    none.length === 1 && none[0].id === 'MINE', JSON.stringify(none));
  Lib._stopHistory = null;
  const writes = [];
  const u2 = {
    connection: { c: 1 },
    user: { id: 'u2' },
    callWS: async (msg) => {
      if (msg.type === 'frontend/set_user_data') { writes.push(msg.value); return null; }
      return { value: [] };
    },
  };
  const both = await Promise.all([
    Lib.recordStop(u2, { id: 'P', name: 'Pápa' }),
    Lib.recordStop(u2, { id: 'Q', name: 'Győr' }),
  ]);
  const final = await Lib.loadStopHistory(u2);
  check('two picks in one tick both reach the history',
    final.some((e) => e.id === 'P') && final.some((e) => e.id === 'Q')
    && both[1].length === 2 && writes.length === 2 && writes[1].length === 2,
    JSON.stringify({ final: final.map((e) => e.id), writes: writes.map((w) => w.map((e) => e.id)) }));
  check('the browser copy is keyed by the HA user, and the old shared copy goes',
    store.has(`${key}:u2`) && JSON.parse(store.get(`${key}:u2`)).length === 2
    && !store.has(key),
    JSON.stringify(Array.from(store.keys())));
  Lib._stopHistory = null;
  delete globalThis.localStorage;
}

/* Destination search: a destination whose name is inside the query must not match. */
{
  const dests = [{ key: 'eger', name: 'Eger' }, { key: 'egerszalok', name: 'Egerszalók' }];
  const forEger = Lib.destHitsForQuery(dests, 'Eger', []).map((h) => h.name);
  const forSzalok = Lib.destHitsForQuery(dests, 'Egerszalók', []).map((h) => h.name);
  const words = Lib.destHitsForQuery([{ key: 'kelenfold', name: 'Kelenföld' }], 'kelenföld vasútállomás', []).map((h) => h.name);
  check('destHitsForQuery: "Egerszalók" does not pull Eger, "Eger" still offers both',
    forEger.length === 2 && forSzalok.length === 1 && forSzalok[0] === 'Egerszalók' && words.length === 1,
    JSON.stringify({ forEger, forSzalok, words }));
}

/* Planner: invalidation stops a running destination search, detach clears timers, focus in the shadow root. */
{
  const pl = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pl);
  pl.hass = { states: {}, language: 'hu' };
  pl._reloadSafe = () => {};
  pl.setConfig({ language: 'hu' });
  const seq0 = pl._destSearchSeq;
  pl._invalidatePlan();
  check('_invalidatePlan bumps the destination search sequence', pl._destSearchSeq === seq0 + 1);
  check('the planner ticks its countdowns', pl._tick !== null && !src.includes('_startTick() {}'));
  const q = pl._planEl('pq');
  if (q) q.focus();
  pl._syncPickerLabels();
  check('planner reads focus from its shadow root, not from document',
    !/document\.activeElement/.test(src.replace(/\/\*[\s\S]*?\*\//g, ''))
    && (!q || pl.shadowRoot.activeElement === q),
    String(!!q));
  pl._searchTimer = setTimeout(() => {}, 1e6);
  pl._destTimer = setTimeout(() => {}, 1e6);
  pl.remove();
  check('planner detach clears the search debounce timers', pl._searchTimer === null && pl._destTimer === null);
}

/* The planner's city fallback draws its rides along their own shapes. */
{
  const st = (i, id, name, lat, lon) => ({ i, op: 0, id, name, fold: Lib.fold(name), key: Lib.norm(name), lat, lon });
  const geoIdx = {
    url: 'http://localhost:8123/hungarian-transport/city-index.json.gz',
    sh: { dir: 'city-shapes-geo', n: 1 },
    c: [['1111111', '20000101', '20991231']],
    ops: [{ i: 0, id: 'miskolc', name: 'Miskolc — MVK' }],
    byNum: new Map(),
    byKey: new Map(),
    stops: [
      st(0, 'miskolc:zoo', 'Miskolci Állatkert', 48.1000, 20.7000),
      st(1, 'miskolc:fm1', 'Felső-Majláth', 48.1100, 20.7100),
      st(2, 'miskolc:fm2', 'Felső-Majláth', 48.1101, 20.7102),
      st(3, 'miskolc:tiszai', 'Tiszai pályaudvar', 48.1200, 20.8000),
    ],
    t: [
      [0, 'ZOO', [0, 1], [940, 947], 3, 0],
      [0, '1', [2, 3], [100, 122], 0, 0],
    ],
  };
  const keep = { cityIndex: Lib.cityIndex, nextCityRide: Lib.nextCityRide, gtfsShape: Lib.gtfsShape };
  const now = Math.floor(Date.now() / 1000);
  Lib.cityIndex = async () => geoIdx;
  Lib.nextCityRide = (idx, fromI, toI, routes) => {
    const t = idx.t.find((x) => [].concat(routes).includes(x[1]));
    return { dep: now + 60, arr: now + 300, trip: t, o: t[2].indexOf(fromI), d: t[2].indexOf(toI) };
  };
  /* One shapes.txt route for both lines: bends between their stops. */
  const bend = [[48.1000, 20.7000], [48.1050, 20.7080], [48.1100, 20.7100], [48.1101, 20.7102], [48.1180, 20.7600], [48.1200, 20.8000]];
  Lib.gtfsShape = async () => bend;
  const journey = await Lib.cityRailJourney('k', { states: {} },
    { id: 'miskolc:zoo', name: 'Miskolci Állatkert' },
    { id: 'miskolc:tiszai', name: 'Tiszai pályaudvar' });
  const rides = (journey && journey.legs || []).filter((l) => !l.walk);
  check('city fallback rides carry their trip geometry and stop coordinates',
    rides.length === 2 && rides.every((l) => l.gtfs && l.fromLat != null && l.toLat != null)
    && rides[0].fromLat === 48.1 && rides[1].toLon === 20.8,
    JSON.stringify(rides.map((l) => ({ label: l.label, gtfs: !!l.gtfs, from: [l.fromLat, l.fromLon], to: [l.toLat, l.toLon] }))));
  await Lib.fillJourneyShapes(journey);
  const segs = Lib.journeyMapSegments(journey, {}, {});
  const zoo = segs.find((sg) => sg.label === 'ZOO');
  const one = segs.find((sg) => sg.label === '1');
  check('the map draws each city ride along its shape, not a straight line',
    !!zoo && zoo.shape.length >= 3 && zoo.shape.some((pt) => pt[0] === 48.105)
    && !!one && one.shape.length >= 3 && one.shape.some((pt) => pt[1] === 20.76),
    JSON.stringify(segs.map((sg) => ({ label: sg.label, walk: sg.walk, n: sg.shape.length }))));
  Lib.gtfsShape = async () => [];
  const bare = await Lib.cityRailJourney('k', { states: {} },
    { id: 'miskolc:zoo', name: 'Miskolci Állatkert' },
    { id: 'miskolc:tiszai', name: 'Tiszai pályaudvar' });
  await Lib.fillJourneyShapes(bare);
  const bareRide = bare.legs.find((l) => l.label === 'ZOO');
  check('without a shape a city ride follows its own stops',
    !!bareRide && Array.isArray(bareRide.shape) && bareRide.shape.length === 2 && bareRide.shape[1][0] === 48.11,
    JSON.stringify(bareRide && bareRide.shape));
  Object.assign(Lib, keep);
  const realNow = Date.now;
  Date.now = () => Date.UTC(2026, 9, 20, 22, 20);
  const rideIdx = { c: [['1111111', '20200101', '20301231']], t: [[0, 'R1', [0, 1], [1470, 1480]]] };
  const ride = Lib.nextCityRide(rideIdx, 0, 1, 'R1', Math.floor(Date.now() / 1000));
  Date.now = realNow;
  check('nextCityRide names the trip and its stop positions',
    !!ride && ride.trip === rideIdx.t[0] && ride.o === 0 && ride.d === 1, JSON.stringify(ride && { o: ride.o, d: ride.d }));
}

/* ---- Third card review batch ---- */

const tick = () => new Promise((r) => setTimeout(r, 0));
const deferred = () => {
  let resolve;
  const p = new Promise((r) => { resolve = r; });
  return { p, resolve };
};

/* Home Assistant's real order: setConfig first, hass after. With `language:
   auto` the first paint is in the browser's language (jsdom: en-US). */
{
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.setConfig({ language: 'auto' });
  const before = pc._planEl('plab1').textContent;
  pc.hass = { states: {}, language: 'hu', services: {} };
  const seg = [...pc._planEl('pwhen').querySelectorAll('button')].map((b) => b.textContent);
  const got = {
    before,
    lab1: pc._planEl('plab1').textContent,
    lab2: pc._planEl('plab2').textContent,
    seg,
    head: pc.shadowRoot.querySelector('.head').textContent,
    hint: pc._planEl('pdhits').textContent,
    reset: pc._planEl('preset').textContent,
  };
  check('a planner set up before hass draws its pickers again in the hass language',
    got.before === 'Origin' && got.lab1 === 'Forrás' && got.lab2 === 'Cél'
    && got.seg[0] === 'Most' && got.seg[1] === 'Indulás' && got.head === 'Tervező'
    && got.hint.startsWith('Válassz forrás') && got.reset === 'Új',
    JSON.stringify(got));
  pc.remove();

  const hop = document.createElement('hungarian-transport-card');
  hop._reloadSafe = () => {};
  document.body.appendChild(hop);
  hop.setConfig({ language: 'auto' });
  const hopBefore = hop.shadowRoot.querySelector('.head').textContent;
  hop.hass = { states: {}, language: 'hu' };
  const hopAfter = hop.shadowRoot.querySelector('.head').textContent;
  check('the hop card title without stops is translated and follows the hass language',
    hopBefore === 'Hungarian transport' && hopAfter === 'Magyar közlekedés',
    JSON.stringify({ hopBefore, hopAfter }));
  hop.remove();
}

/* Editor picks set or clear the stop's id, coordinates and kind. */
{
  const ped = document.createElement('hungarian-transit-stop-card-plan-editor');
  document.body.appendChild(ped);
  ped.hass = { states: {}, language: 'hu' };
  ped._probeKey = () => {};
  ped._loadDests = async () => {};
  let last = null;
  ped.addEventListener('config-changed', (ev) => { last = ev.detail.config; });
  ped.setConfig({
    language: 'hu', stopId: 'BKK_OLD', stopName: 'Régi', stopLat: 1, stopLon: 2, stopKind: 'place',
    destKey: 'x', destName: 'X', destStopId: 'BKK_OLDD', destLat: 3, destLon: 4, destKind: 'address',
  });
  ped._pickStop({ id: 'BKK_F1', name: 'Fő tér', lat: 47.5, lon: 19.1 }, 'origin');
  check('an origin picked in the editor replaces the old stop’s coordinates and kind, and clears the destination',
    !!last && last.stopId === 'BKK_F1' && last.stopLat === 47.5 && last.stopLon === 19.1
    && !('stopKind' in last) && last.destKey === '' && last.destName === ''
    && !('destStopId' in last) && !('destLat' in last) && !('destLon' in last) && !('destKind' in last),
    JSON.stringify(last));
  ped.setConfig(last);
  ped._destRoutes = { blaha: ['BKK_9'] };
  ped._pickStop({ id: 'BKK_F2', key: 'blaha', name: 'Blaha Lujza tér' }, 'dest');
  check('a destination picked in the editor carries its stop id, and the planner editor bakes in no name',
    last.destStopId === 'BKK_F2' && last.destKey === 'blaha' && JSON.stringify(last.routeIds) === '["BKK_9"]'
    && !('destLat' in last) && !last.name,
    JSON.stringify(last));
  ped.remove();

  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.setConfig(last);
  pc._swapPlan();
  check('a planner set up in the editor can swap its stops',
    pc._config.stopId === 'BKK_F2' && pc._config.stopName === 'Blaha Lujza tér'
    && pc._config.destStopId === 'BKK_F1' && pc._config.destName === 'Fő tér' && pc._config.destLat === 47.5,
    JSON.stringify(pc._config));
  pc.remove();

  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  let hopLast = null;
  ed.addEventListener('config-changed', (ev) => { hopLast = ev.detail.config; });
  ed.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr' });
  ed._pickStop({ id: 'volan_2', key: 'pápa', name: 'Pápa', lat: 47.33, lon: 17.47, kind: 'stop' }, 'dest');
  check('the hop editor still names its card after the pair, with the destination’s coordinates and kind',
    hopLast.name === 'Győr → Pápa' && hopLast.destStopId === 'volan_2'
    && hopLast.destLat === 47.33 && hopLast.destLon === 17.47 && hopLast.destKind === 'stop',
    JSON.stringify(hopLast));
  ed.setConfig(hopLast);
  ed._el('helyi').checked = true;
  ed._onModeToggle('helyi');
  check('a mode change clears both stops with their coordinates',
    hopLast.helyi === true && hopLast.stopId === '' && !('destStopId' in hopLast)
    && !('destLat' in hopLast) && !('destKind' in hopLast) && !('stopLat' in hopLast),
    JSON.stringify(hopLast));
  ed.remove();
}

/* An empty city board: Transitous once, then a ten-minute pause. */
{
  const keep = {
    cityDepartures: Lib.cityDepartures,
    transitousFetch: Lib.transitousFetch,
    fillTransitousIfEmpty: Lib.fillTransitousIfEmpty,
  };
  Lib._geocodeCache.clear();
  Lib._transitousEmptyAt.clear();
  const asked = { geocode: 0, plan: 0 };
  Lib.cityDepartures = async () => [];
  Lib.transitousFetch = async (path, params) => {
    if (/geocode/.test(path)) {
      asked.geocode += 1;
      return [{ id: 'hu-szeged_' + params.text, name: params.text, lat: 46.25, lon: 20.15 }];
    }
    if (/plan/.test(path)) asked.plan += 1;
    return { itineraries: [] };
  };
  const c = document.createElement('hungarian-transport-card');
  c._reloadSafe = () => {};
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  c.setConfig({ helyi: true, city: 'szeged', stopId: 'szeged:1', stopName: 'Mars tér', destKey: 'dugonics ter', destName: 'Dugonics tér' });
  await c._reload();
  const first = Object.assign({}, asked);
  await c._reload();
  const second = Object.assign({}, asked);
  check('an empty city board asks Transitous once per pair: two geocodes and one plan, then nothing',
    first.geocode === 2 && first.plan === 1 && second.geocode === 2 && second.plan === 1,
    JSON.stringify({ first, second }));
  c.remove();

  let fills = 0;
  Lib.fillTransitousIfEmpty = async (rows) => { fills += 1; return rows; };
  const v = document.createElement('hungarian-transport-card');
  v._reloadSafe = () => {};
  v.setConfig({ helyi: true, city: 'szeged', stopId: 'szeged:1', stopName: 'Mars tér', destKey: 'x', destName: 'X' });
  await v._reload();
  const helyiFills = fills;
  v.setConfig({ volan: true, stopId: 'volan_1', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa' });
  const keepVolan = Lib.volanDepartures;
  Lib.volanDepartures = async () => [];
  await v._reload();
  Lib.volanDepartures = keepVolan;
  check('the hop card leaves a city board’s fill to departures(), and still fills a Volán board itself',
    helyiFills === 1 && fills === 2, JSON.stringify({ helyiFills, fills }));
  if (v._tick) { clearInterval(v._tick); v._tick = null; }
  Lib.fillTransitousIfEmpty = keep.fillTransitousIfEmpty;

  const realNow = Date.now;
  const t0 = realNow();
  Date.now = () => t0;
  Lib._transitousEmptyAt.clear();
  const keepDeps = Lib.transitousDepartures;
  let plans = 0;
  let answer = [];
  Lib.transitousDepartures = async () => { plans += 1; return answer; };
  const o = { id: 'A', name: 'A' };
  const d = { key: 'b', name: 'B' };
  const opts = { mode: 'all', minutesAfter: 60 };
  await Lib.fillTransitousIfEmpty([], o, d, opts);
  await Lib.fillTransitousIfEmpty([], o, d, opts);
  const paused = plans;
  await Lib.fillTransitousIfEmpty([], o, { key: 'c', name: 'C' }, opts);
  const otherPair = plans;
  Date.now = () => t0 + 11 * 60 * 1000;
  answer = [{ label: '7', dep: Math.floor(t0 / 1000) + 900 }];
  const after = await Lib.fillTransitousIfEmpty([], o, d, opts);
  await Lib.fillTransitousIfEmpty([], o, d, opts);
  Date.now = realNow;
  check('an empty fill pauses that pair for ten minutes; another pair, and a fill with rows, ask again',
    paused === 1 && otherPair === 2 && after.length === 1 && plans === 4,
    JSON.stringify({ paused, otherPair, plans, after: after.length }));
  Lib.transitousDepartures = keepDeps;

  Lib._geocodeCache.clear();
  let geo = 0;
  let fail = true;
  Lib.transitousFetch = async () => {
    geo += 1;
    if (fail) throw new Error('Transitous timeout');
    return [{ id: 'hu-x', name: 'Y', lat: 47.1, lon: 19.1 }];
  };
  const p1 = await Lib.transitousPlace({ name: 'Ismeretlen megálló' });
  fail = false;
  const p2 = await Lib.transitousPlace({ name: 'Ismeretlen megálló' });
  const p3 = await Lib.transitousPlace({ name: 'Ismeretlen megálló' });
  check('a geocode answer stays for the page, a failed one is asked again',
    p1 === '' && p2 === '47.1,19.1' && p3 === p2 && geo === 2, JSON.stringify({ p1, p2, p3, geo }));
  Lib._geocodeCache.clear();
  Lib._transitousEmptyAt.clear();
  Object.assign(Lib, { cityDepartures: keep.cityDepartures, transitousFetch: keep.transitousFetch });
}

/* sliceShapeBetween keeps the ride's direction. */
{
  const same = (p, q) => !!p && !!q && p[0] === q[0] && p[1] === q[1];
  const loop = [];
  for (let i = 0; i < 36; i++) {
    const a = (i * 10 * Math.PI) / 180;
    loop.push([46.25 + 0.01 * Math.sin(a), 20.15 + 0.014 * Math.cos(a)]);
  }
  loop.push(loop[0].slice());
  const round = Lib.sliceShapeBetween(loop, loop[30], loop[5]);
  check('on a closed loop the ride runs on over the shape’s end, the short way round',
    round.length === 12 && same(round[0], loop[30]) && same(round[round.length - 1], loop[5])
    && !round.some((pt) => same(pt, loop[20])),
    JSON.stringify({ n: round.length }));
  const line = [[47.0, 19.0], [47.0, 19.01], [47.0, 19.02], [47.0, 19.03]];
  const back = Lib.sliceShapeBetween(line, line[3], line[1]);
  check('a shape that runs the other way is cut from the boarding stop to the destination',
    back.length === 3 && same(back[0], line[3]) && same(back[2], line[1]), JSON.stringify(back));
  const lasso = [
    [47.0, 19.0], [47.0, 19.01], [47.0, 19.02], [47.01, 19.03],
    [47.02, 19.02], [47.0003, 19.0101], [47.0003, 19.0049],
  ];
  const later = Lib.sliceShapeBetween(lasso, lasso[4], lasso[1]);
  const end = later[later.length - 1];
  check('a line that passes the destination twice takes the pass after the boarding stop',
    later.length === 3 && same(later[0], lasso[4]) && same(later[1], lasso[5])
    && end[0] === 47.0003 && Math.abs(end[1] - 19.01) < 1e-9
    && !later.some((pt) => [0, 1, 2, 3].some((i) => same(pt, lasso[i]))),
    JSON.stringify(later));
  const keepShape = Lib.gtfsShape;
  Lib.gtfsShape = async () => loop;
  const journey = { legs: [{ walk: false, label: '78', gtfs: { shape: { dir: 'x', id: 1 }, stops: [loop[30], loop[5]], from: 0, to: 1 } }] };
  await Lib.fillJourneyShapes(journey);
  Lib.gtfsShape = keepShape;
  const leg = journey.legs[0];
  check('a city ride on a loop line is drawn the short way round',
    Array.isArray(leg.shape) && leg.shape.length === 12 && leg.shape[0][0] === loop[30][0],
    JSON.stringify(leg.shape && leg.shape.length));
}

/* A re-applied config after in-card picks restores its stops and reloads; HA layout keys do not reload. */
{
  const cfg = { language: 'hu', stopId: 'BKK_A', stopName: 'A', destKey: 'b', destName: 'B', destStopId: 'BKK_B' };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  let reloads = 0;
  pc._reloadSafe = () => { reloads += 1; };
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.setConfig(cfg);
  pc._pickDest({ key: 'c', name: 'C', id: 'BKK_C' });
  const afterPick = reloads;
  pc._rows = [{ label: 'C-row', headsign: 'C', depTs: Math.floor(Date.now() / 1000) + 600 }];
  pc.setConfig(Object.assign({}, cfg, { name: 'Új cím' }));
  check('re-applying the configured stops after an in-card pick brings them back and reloads',
    afterPick === 2 && reloads === 3 && pc._config.destName === 'B' && pc._config.destStopId === 'BKK_B',
    JSON.stringify({ afterPick, reloads, dest: pc._config.destName }));
  const beforeLayout = reloads;
  pc.setConfig(Object.assign({}, cfg, {
    name: 'Új cím', grid_options: { columns: 6 }, visibility: [{ condition: 'screen' }],
    view_layout: { position: 'main' }, layout_options: { grid_rows: 4 }, card_mod: { style: 'x' },
  }));
  check('Home Assistant layout keys alone do not reload', reloads === beforeLayout, String(reloads));
  pc.remove();
}

/* Picks, clears and resets cancel the searches still typed or in flight. */
{
  const keepSearch = Lib.searchStops;
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  ed.setConfig({ language: 'hu', volan: true });
  let pending = deferred();
  Lib.searchStops = () => pending.p;
  const running = ed._search('Győr', 'origin');
  ed._searchTimer = setTimeout(() => {}, 1e6);
  ed._destTimer = setTimeout(() => {}, 1e6);
  ed._pickStop({ id: 'volan_1', name: 'Győr' }, 'origin');
  pending.resolve([{ id: 'volan_9', name: 'Győrszentiván' }]);
  await running;
  check('an editor pick drops the origin search in flight and the typed timers',
    ed._el('hits').innerHTML === '' && ed._searchTimer === null && ed._destTimer === null,
    ed._el('hits').innerHTML.slice(0, 80));
  pending = deferred();
  const again = ed._search('Pá', 'origin');
  await tick();
  ed._search('', 'origin');
  pending.resolve([{ id: 'volan_8', name: 'Pápa' }]);
  await again;
  check('clearing the editor’s origin field cancels its search', ed._el('hits').innerHTML === '',
    ed._el('hits').innerHTML.slice(0, 80));
  Lib.searchStops = keepSearch;
  ed.remove();

  const keepPlanner = Lib.searchPlannerStops;
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.hass = { states: {}, language: 'hu' };
  pc.setConfig({ language: 'hu' });
  pending = deferred();
  Lib.searchPlannerStops = () => pending.p;
  const origin = pc._searchOrigin('Kele');
  await pc._pickOrigin({ id: 'BKK_F1', name: 'Fő tér' });
  pending.resolve([{ id: 'BKK_X', name: 'Keleti' }]);
  await origin;
  check('a planner pick drops the origin search in flight', pc._planEl('phits').innerHTML === '',
    pc._planEl('phits').innerHTML.slice(0, 80));
  pc._searchTimer = setTimeout(() => {}, 1e6);
  pc._destTimer = setTimeout(() => {}, 1e6);
  pc._resetPlan();
  check('a planner reset clears the typed search timers', pc._searchTimer === null && pc._destTimer === null);
  pc._destTimer = setTimeout(() => {}, 1e6);
  pc._config = Object.assign({}, pc._config, { stopId: 'BKK_F1', stopName: 'Fő tér' });
  pc._pickDest({ key: 'blaha', name: 'Blaha' });
  check('a planner destination pick clears the destination timer', pc._destTimer === null);
  Lib.searchPlannerStops = keepPlanner;
  pc.remove();
}

/* Stop history waits for hass and never reads the old shared browser key. */
{
  const store = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
  });
  store.set(Lib.STOP_HISTORY_KEY, JSON.stringify([{ id: 'SHARED', name: 'Shared stop' }]));
  Lib._stopHistory = null;
  const anonymous = await Lib.loadStopHistory(null);
  check('without a user the shared browser key is not read', anonymous.length === 0, JSON.stringify(anonymous));
  Lib._stopHistory = null;
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu' });
  await tick();
  const chips = () => [...pc.shadowRoot.querySelectorAll('#pfav .suggest-stop')].map((b) => b.textContent);
  const beforeHass = chips();
  check('before hass the planner loads no suggestions', beforeHass.length === 0 && Lib._stopHistory === null,
    JSON.stringify(beforeHass));
  pc.hass = {
    states: {}, language: 'hu', connection: {}, user: { id: 'u9' },
    callWS: async (msg) => (msg.type === 'frontend/get_user_data' ? { value: [{ id: 'BKK_F9', name: 'Saját megálló' }] } : null),
  };
  await tick();
  await tick();
  const withHass = chips();
  check('with hass the suggestions are this user’s', withHass.length === 1 && withHass[0].includes('Saját megálló'),
    JSON.stringify(withHass));
  pc.remove();
  Lib._stopHistory = null;
  delete globalThis.localStorage;
}

/* An origin from the YAML loads its reachable destinations once hass is there. */
{
  const keepReach = Lib.reachableDests;
  let loads = 0;
  Lib.reachableDests = async (_k, _c, stop) => {
    loads += 1;
    return { dests: [{ key: 'b', name: 'B from ' + stop.id }], destRoutes: { b: ['BKK_9'] } };
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_X', stopName: 'X' });
  const beforeHass = loads;
  pc.hass = { states: {}, language: 'hu' };
  await tick();
  pc.hass = { states: {}, language: 'hu' };
  await tick();
  const once = loads;
  const listed = pc._planEl('pdhits').textContent;
  pc.setConfig({ language: 'hu', stopId: 'BKK_X', stopName: 'X', name: 'Cím' });
  const titleOnly = loads;
  pc.setConfig({ language: 'hu', stopId: 'BKK_Y', stopName: 'Y' });
  await tick();
  check('a configured origin loads its destinations once hass is set, and again for a new origin',
    beforeHass === 0 && once === 1 && listed.includes('B from BKK_X') && titleOnly === 1 && loads === 2,
    JSON.stringify({ beforeHass, once, titleOnly, loads, listed: listed.slice(0, 40) }));
  Lib.reachableDests = keepReach;
  pc.remove();
}

/* The shared departure board cache drops boards five minutes old (kept that
   long only to stand in for a rate-limited refresh). */
{
  const keepFetch = Lib.fetch;
  const realNow = Date.now;
  const t0 = realNow();
  Lib._boards.clear();
  Lib.fetch = async () => ({ data: { entry: { stopTimes: [] } } });
  Date.now = () => t0;
  await Lib.departureBoard('k', 'S1', 60);
  await Lib.departureBoard('k', 'S2', 60);
  const both = Lib._boards.size;
  Date.now = () => t0 + 5 * 60 * 1000;
  await Lib.departureBoard('k', 'S3', 60);
  const left = Array.from(Lib._boards.keys());
  Date.now = realNow;
  check('an old departure board leaves the cache on the next insert',
    both === 2 && left.length === 1 && left[0] === 'key:k|S3|60', JSON.stringify({ both, left }));
  Lib._boards.clear();
  Lib.fetch = keepFetch;
}

/* futar_rate_limited is the integration's own limit, not BKK down: the last
   board (up to five minutes old) answers instead, so the card shows no
   "BKK rate limited" banner. An outage still fails. */
{
  const keepFetch = Lib.fetch;
  const realNow = Date.now;
  const t0 = realNow();
  Lib._boards.clear();
  const board = { data: { entry: { stopTimes: [{ departureTime: 1 }] } } };
  let fail = null;
  Lib.fetch = async () => {
    if (!fail) return board;
    const err = new Error(`BKK ${fail}`);
    err.code = fail;
    throw err;
  };
  const attempt = async () => {
    try {
      return await Lib.departureBoard('', 'S1', 60);
    } catch (err) {
      return err;
    }
  };
  Date.now = () => t0;
  await attempt();
  Date.now = () => t0 + 30000;
  fail = 'futar_rate_limited';
  const limited = await attempt();
  fail = 'futar_unavailable';
  const down = await attempt();
  fail = 'futar_rate_limited';
  Date.now = () => t0 + 5 * 60 * 1000;
  const tooOld = await attempt();
  Date.now = realNow;
  check('a rate-limited board refresh falls back to the last board, an outage does not',
    limited === board && down instanceof Error && down.code === 'futar_unavailable'
    && tooOld instanceof Error && tooOld.code === 'futar_rate_limited',
    JSON.stringify({ limited: limited === board, down: down && down.code, tooOld: tooOld && tooOld.code }));
  Lib._boards.clear();
  Lib.fetch = keepFetch;
}

/* Small i18n: the Helyi switch, the look-ahead hint, the map notice button, a hidden API key. */
{
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'en' };
  ed._probeKey = () => {};
  ed.setConfig({ language: 'en' });
  const enLocal = ed._el('lblHelyi').textContent;
  const enHint = ed._el('hintMinutesAfter').textContent;
  const key = ed._el('apiKey');
  const show = ed._el('apiKeyShow');
  const hidden = key.type;
  const showLabel = show.textContent;
  show.click();
  const shown = key.type;
  const hideLabel = show.textContent;
  show.click();
  ed.setConfig({ language: 'hu' });
  check('the editor translates the city switch and the look-ahead range matches the clamp',
    enLocal === 'Local' && ed._el('lblHelyi').textContent === 'Helyi'
    && enHint.includes('15–480') && ed._el('hintMinutesAfter').textContent.includes('15–480 perc'),
    JSON.stringify({ enLocal, enHint }));
  check('the API key is a password field with a show/hide button',
    hidden === 'password' && shown === 'text' && key.type === 'password'
    && showLabel === 'Show' && hideLabel === 'Hide' && show.getAttribute('aria-pressed') === 'false',
    JSON.stringify({ hidden, shown, showLabel, hideLabel }));
  ed.remove();

  const c = document.createElement('hungarian-transport-card');
  c._reloadSafe = () => {};
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  c.setConfig({ language: 'hu' });
  c._showMapNotice('Nincs adat');
  const btn = document.querySelector('.ht-map-notice button');
  const huOk = btn && btn.textContent;
  c._closeVehicleMap();
  c.setConfig({ language: 'en' });
  c._showMapNotice('No data');
  const enOk = document.querySelector('.ht-map-notice button').textContent;
  c._closeVehicleMap();
  check('the map notice button is translated', huOk === 'Rendben' && enOk === 'OK', JSON.stringify({ huOk, enOk }));
  c.remove();
}

/* ---- Fourth review: planner and route library ---- */

const settleTicks = async (n) => {
  for (let i = 0; i < (n || 6); i++) await new Promise((r) => setTimeout(r, 0));
};
const shapeLen = (pts) => pts.reduce((m, p, i) => (i ? m + Lib.haversineMeters(pts[i - 1], p) : 0), 0);

/* Rides on real city shapes (the live index's polylines) are cut between
   their two stops: the hop, not the whole line nor a detour round it. */
{
  const cases = [
    { line: 'Miskolc ZOO', from: 'Papírgyár', to: 'Majális park', a: [48.11425, 20.66282], b: [48.11561, 20.65928], straight: 303,
      enc: 'khsdHwje}Bt@j@aDvCZxB_f@lcBkArAo@rEy@^aCeAmA\\HvCnAzGcArIsIZ{CsA_AyBi@gGoBwBeKRTr@' },
    { line: 'Miskolc 29', from: 'Egyetemi kollégiumok', to: 'Tanulmányi épületek', a: [48.07844, 20.77096], b: [48.08124, 20.76697], straight: 430,
      enc: 'qwqdHayo}BDxA~Ad@EuKl^uBjA|AZ`P|X}@xFnHfAXz@YxDoHpEo@ZsD{FeG_AkFBeCjD{NpEqKdCgMBgCc@wBgA}AgIAwAmBGiDhCo]c@gFeBqG^oBbAe@vDhDbAJdAoAFwBmAoBiDGuBaAkIyKkA{Ca@gEU{b@x@cObDmKzBoDfDwCvKmDlNsBxHmHlWmEPrA{DlNkApGIjD}AbBwHy@aBbENtArMrN|CWmCsYj@kFfCyL`BqElBw@bD|@l@kGhEkRfDuH`@eG}ElDkI_Cyd@dEuBbB}E`MsChCyb@zDgDvAwD|FqUvBwA`AbFzQtBvEzEtGZtAsLhIoA?yGoHeCeEu@l@HlD[d@kCa@o@kA{@Q}YrB~D|iAtKpfCf@rBj@`L\\h@jBQ]cM' },
    { line: 'Pécs 1', from: 'Vásártér', to: 'Nagy Imre út', a: [46.05033, 18.21308], b: [46.04713, 18.21428], straight: 368,
      enc: 'eedxGuh_nBAn@tBJEyEv@kFbEuDfAaFwBgk@{F`AkPsh@iAaFS_E|N_EtPmB|ShGpEEdDmCrIeO`D_CnpAc[uA{IuAwCyDcYYwD\\yGWyE~ZeLzMhDeFn]m@tVy@nCmAtAqTfJoPpD}EZI`@iFdBam@xNaD~B{IvNyCvCuEH}SiGuPlB}N~Dz@`IzNne@f@lEdHkAzB~f@IpDmAfE}FlEPlIUr@gAVu@w@' },
  ];
  cases.forEach((c) => {
    const shape = Lib.validMapShape(Lib.decodePolyline(c.enc));
    const ride = Lib.sliceShapeBetween(shape, c.a, c.b);
    const m = shapeLen(ride);
    check(`${c.line} ${c.from} → ${c.to} is drawn as its own hop, not the whole line`,
      m >= c.straight - 1 && m < 1.5 * c.straight + 100 && m < shapeLen(shape) / 3
      && Lib.haversineMeters(ride[0], c.a) < 40 && Lib.haversineMeters(ride[ride.length - 1], c.b) < 40,
      JSON.stringify({ ride: Math.round(m), line: Math.round(shapeLen(shape)) }));
  });
  const seg = [[47.0, 19.0], [47.0, 19.02]];
  const piece = Lib.sliceShapeBetween(seg, [47.0001, 19.005], [47.0001, 19.008]);
  check('two stops on one segment give that piece, not the whole segment',
    piece.length === 2 && piece[0][0] === 47.0 && Math.abs(piece[0][1] - 19.005) < 1e-9
    && Math.abs(piece[1][1] - 19.008) < 1e-9,
    JSON.stringify(piece));
  /* Out on one side of the street, back on the other, ending where it
     started: the ride between two stops of the way back is that stretch. */
  const outBack = [[47.0, 19.0], [47.0, 19.01], [47.0, 19.02], [47.0002, 19.02], [47.0002, 19.01], [47.0002, 19.0001]];
  const back = Lib.sliceShapeBetween(outBack, [47.0003, 19.012], [47.0003, 19.006]);
  check('an out-and-back line whose ends meet rides the way back, not round the line',
    shapeLen(back) < 600 && back.every((pt) => pt[0] === 47.0002),
    JSON.stringify({ m: Math.round(shapeLen(back)), back }));
}

/* City stops carry their coordinates and town, so a pick is planned from
   that stop, not from a geocode of its bare name. */
{
  const keep = {};
  ['_fetchIndexBytes', 'cityIndex', 'transitousFetch', 'searchStops', 'searchStopsTransitous', 'addTransitousVolanStops']
    .forEach((k) => { keep[k] = Lib[k]; });
  const raw = {
    v: 29,
    o: [['tatabanya', 'Tatabánya — T-Busz'], ['pecs', 'Pécs — Tüke Busz']],
    s: [
      ['tatabanya:SP1', 'Fő tér', '1', 0],
      ['tatabanya:SP2', 'Kórház', '2', 0],
      ['pecs:1', 'Autóbusz-állomás', '1', 1],
    ],
    g: [4758400, 1839400, 4757000, 1840000, 4607000, 1822000],
    c: [['1111111', '20000101', '20991231']],
    t: [[0, '1', [0, 1], [600, 610]]],
  };
  Lib._fetchIndexBytes = async () => ({ status: 200, bytes: new TextEncoder().encode(JSON.stringify(raw)) });
  const idx = await Lib._loadGtfsIndex('http://x/city-index.json?v=20260101', 'errCityHttp', 'errCityGunzip');
  Lib._fetchIndexBytes = keep._fetchIndexBytes;
  check('a loaded index lets go of its raw stop rows and finds a stop by id',
    idx.s === null && idx.stops.length === 3 && Lib.gtfsStopById(idx, 'pecs:1').name === 'Autóbusz-állomás');
  const tata = Lib.opIx(idx, 'tatabanya');
  const hits = Lib.gtfsSearchStops(idx, 'fő tér', tata);
  check('a city search hit has the stop’s coordinates and town',
    hits.length === 1 && hits[0].lat === 47.584 && hits[0].lon === 18.394 && hits[0].area === 'Tatabánya',
    JSON.stringify(hits));
  const reach = Lib.gtfsReachableDests(idx, { id: 'tatabanya:SP1', name: 'Fő tér' }, tata);
  check('a reachable city destination has coordinates and town',
    reach.dests.length === 1 && reach.dests[0].name === 'Kórház' && reach.dests[0].lat === 47.57
    && reach.dests[0].area === 'Tatabánya' && reach.dests[0].key === Lib.norm('Kórház'),
    JSON.stringify(reach.dests));
  Lib.cityIndex = async () => idx;
  const bkk = { id: 'BKK_F01020', name: 'Kossuth Lajos utca' };
  const [placed, same] = await Lib.withGtfsCoords([{ id: 'tatabanya:SP1', name: 'Fő tér' }, bkk]);
  check('a city stop picked without coordinates gets them from its index',
    placed.lat === 47.584 && placed.lon === 18.394 && placed.area === 'Tatabánya' && same === bkk,
    JSON.stringify(placed));
  Lib._geocodeCache.clear();
  Lib._geocodeEmptyAt.clear();
  const calls = [];
  Lib.transitousFetch = async (path, params) => {
    calls.push({ path, params });
    if (path === '/api/v5/plan') return { itineraries: [] };
    return [{ name: 'Fő tér', lat: 47.5414, lon: 19.0466, type: 'STOP', id: 'hu-bkk_F01234' }];
  };
  await Lib.planJourney('', { id: 'tatabanya:SP1', name: 'Fő tér' },
    { id: 'BKK_005501024', name: 'Kelenföld', lat: 47.4643, lon: 19.0206 }, null, {});
  const planned = calls.find((c) => c.path === '/api/v5/plan');
  check('Tatabánya’s "Fő tér" is planned from Tatabánya, not geocoded into Budapest',
    !!planned && planned.params.fromPlace === '47.584,18.394' && !calls.some((c) => c.path === '/api/v1/geocode'),
    JSON.stringify(calls.map((c) => `${c.path} ${c.params.fromPlace || c.params.text}`)));
  Lib.searchStopsTransitous = async () => [];
  Lib.addTransitousVolanStops = async () => {};
  Lib.searchStops = async () => [
    { id: 'tatabanya:SP1', name: 'Fő tér', label: 'Fő tér (Tatabánya)', city: true },
    { id: 'BKK_F01234', name: 'Fő tér', lat: 47.5414, lon: 19.0466 },
  ];
  const picker = await Lib.searchPlannerStops('', 'fő tér');
  const inTata = picker.find((h) => h.id === 'tatabanya:SP1');
  check('Budapest’s "Fő tér" does not swallow Tatabánya’s in the origin search',
    picker.length === 2 && !!inTata && inTata.lat === 47.584 && inTata.area === 'Tatabánya',
    JSON.stringify(picker.map((h) => [h.id, h.lat])));
  Object.assign(Lib, keep);
}

/* One destination row per place and town. */
{
  const gyor = { key: 'győr, autóbusz-állomás', name: 'Győr, autóbusz-állomás', id: 'hkir_100' };
  const pecs = { key: 'autóbusz-állomás', name: 'Autóbusz-állomás', id: 'pecs:1' };
  const two = Lib.destHitsForQuery([gyor, pecs], '', []);
  check('Pécs’s "Autóbusz-állomás" stays its own destination beside Győr’s',
    two.length === 2 && two[0].id === 'hkir_100' && two[1].id === 'pecs:1' && two[1].routeKeys.join() === pecs.key,
    JSON.stringify(two.map((h) => [h.id, h.key])));
  const fo = Lib.destHitsForQuery([], 'fő tér', [
    { id: 'tatabanya:SP1', name: 'Fő tér', label: 'Fő tér (Tatabánya)' },
    { id: 'BKK_F01234', name: 'Fő tér' },
  ]);
  check('Budapest’s "Fő tér" does not join "Fő tér (Tatabánya)"',
    fo.length === 2 && fo[0].id === 'tatabanya:SP1' && fo[1].id === 'BKK_F01234',
    JSON.stringify(fo.map((h) => h.id)));
  const szeged = Lib.destHitsForQuery([
    { key: 'a', name: 'Londoni körút (Bakay Nándor utca)', id: 'szeged:1', lat: 46.25482, lon: 20.13882 },
    { key: 'b', name: 'Londoni körút (Kálvária sugárút)', id: 'szeged:2', lat: 46.25192, lon: 20.13861 },
    { key: 'c', name: 'Londoni körút', id: 'szeged:3', lat: 46.25199, lon: 20.13797 },
  ], '', []);
  check('Szeged’s cross streets keep two "Londoni körút" stops apart, the bare name joins the pole beside it',
    szeged.length === 2 && szeged[0].routeKeys.join() === 'a' && szeged[1].routeKeys.join() === 'b,c',
    JSON.stringify(szeged.map((h) => h.routeKeys)));
  check('two cross streets of one town are two stops, a bare name may be either',
    !Lib.nameEq('Szeged, Londoni k\u00f6r\u00fat (Bakay N\u00e1ndor utca)', 'Szeged, Londoni k\u00f6r\u00fat (K\u00e1lv\u00e1ria sug\u00e1r\u00fat)')
    && !Lib.nameEq('Londoni k\u00f6r\u00fat (Bakay N\u00e1ndor utca)', 'Londoni k\u00f6r\u00fat (K\u00e1lv\u00e1ria sug\u00e1r\u00fat)')
    && Lib.nameEq('Londoni k\u00f6r\u00fat', 'Londoni k\u00f6r\u00fat (Bakay N\u00e1ndor utca)'));
  check('a cross street in brackets is part of the stop, not its town',
    Lib.stopTownKey({ id: 'szeged:1', name: 'Londoni körút (Bakay Nándor utca)' }) === 'szeged'
    && Lib.stopTownKey({ id: 'x', name: 'Fő tér', label: 'Fő tér (Tatabánya)' }) === 'tatabanya'
    && Lib.stopTownKey({ id: 'volan-gyor:hkir_1', name: 'Pinty utca' }) === 'gyor');
}

/* The origin search: an address or place gives way only to a stop of its
   own name and place, or of its town; feeds’ namesakes far apart stay two. */
{
  const kept = Lib.collapseSameNameStops([
    { id: 'BKK_F00001', name: 'Kossuth Lajos utca', lat: 47.4935, lon: 19.0603 },
    {
      id: 'geo:47.191000,18.410000', name: 'Kossuth Lajos utca 12', label: 'Kossuth Lajos utca 12 (Székesfehérvár)',
      lat: 47.191, lon: 18.41, kind: 'address',
    },
    {
      id: 'node/[5]', name: 'Vörösmarty Színház', label: 'Vörösmarty Színház (Székesfehérvár)',
      lat: 47.19, lon: 18.41, kind: 'place',
    },
  ]);
  check('a Budapest stop does not hide an address or a place in Székesfehérvár',
    kept.length === 3, JSON.stringify(kept.map((h) => h.id)));
  const own = Lib.collapseSameNameStops([
    { id: 'BKK_F00001', name: 'Kossuth Lajos utca', lat: 47.4935, lon: 19.0603 },
    {
      id: 'geo:47.493600,19.060400', name: 'Kossuth Lajos utca', label: 'Kossuth Lajos utca (Budapest)',
      lat: 47.4936, lon: 19.0604, kind: 'address',
    },
  ]);
  check('an address of the stop’s own name and place goes into that stop',
    own.length === 1 && own[0].id === 'BKK_F00001', JSON.stringify(own.map((h) => h.id)));
  const apart = Lib.collapseSameNameStops([
    { id: 'BKK_F01234', name: 'Fő tér', lat: 47.5414, lon: 19.0466 },
    { id: 'tatabanya:SP1', name: 'Fő tér', lat: 47.584, lon: 18.394 },
  ]);
  check('two feeds’ "Fő tér" 50 km apart stay two stops', apart.length === 2, JSON.stringify(apart.map((h) => h.id)));
  check('a bus station in brackets is not a railway hub',
    !Lib.isRailHubName('Könyvtár (Buszpályaudvar)') && Lib.isRailHubName('Kaposvár vasútállomás'));
}

/* A trip's destination stop: a town by whole word, any other name exactly,
   and with coordinates the namesake in the right town. */
{
  const st = (name, lat, lon) => ({ key: Lib.norm(name), fold: Lib.fold(name), name, lat, lon });
  const korhaz = [Lib.norm('Kórház'), Lib.fold('Kórház')];
  check('"Kórház" is that stop, not "Kórház tér" nor "Megyei Kórház"',
    !Lib.volanDestHit(st('Kórház tér'), ...korhaz)
    && !Lib.volanDestHit(st('Megyei Kórház'), ...korhaz)
    && Lib.volanDestHit(st('Kórház'), ...korhaz));
  const gyorTer = [Lib.norm('Győr, Kossuth tér'), Lib.fold('Győr, Kossuth tér')];
  check('one town’s "Kossuth tér" is not another’s',
    !Lib.volanDestHit(st('Sopron, Kossuth tér'), ...gyorTer) && Lib.volanDestHit(st('Győr, Kossuth tér'), ...gyorTer));
  const near = { lat: 47.687, lon: 17.635 };
  check('a "Kossuth tér" without a town is the one by the picked coordinates',
    !Lib.volanDestHit(st('Sopron, Kossuth tér', 47.685, 16.59), 'kossuth tér', 'kossuth ter', near)
    && Lib.volanDestHit(st('Győr, Kossuth tér', 47.688, 17.634), 'kossuth tér', 'kossuth ter', near));
  const day = Lib.volanDay(0);
  const idx = {
    c: [['1111111', '20000101', '20991231']],
    ops: [],
    byNum: new Map(),
    byKey: new Map(),
    stops: ['Budapest, Népliget', 'Tata, Kórház tér', 'Tata, autóbusz-állomás']
      .map((name, i) => ({ i, op: 0, id: `hkir_${i + 1}`, num: String(i + 1), name, key: Lib.norm(name), fold: Lib.fold(name) })),
    t: [[0, '1000', [0, 1, 2], [day.mins + 5, day.mins + 50, day.mins + 55]]],
  };
  const toTown = Lib.gtfsDepartures(idx, 'hkir_1', { key: 'tata', name: 'Tata' }, 'Budapest, Népliget', null, '#F9AB13', '#000000', 'coach', 'COACH', 120);
  const toKorhaz = Lib.gtfsDepartures(idx, 'hkir_1', { key: Lib.norm('Kórház'), name: 'Kórház' }, 'Budapest, Népliget', null, '#F9AB13', '#000000', 'coach', 'COACH', 120);
  check('a town of the index is any of its stops, a stop name is not a part of another',
    toTown.length === 1 && toTown[0].travel === 45 && toKorhaz.length === 0,
    JSON.stringify({ town: toTown.map((r) => r.travel), korhaz: toKorhaz.length }));
}

/* Stop history: a failed read is not kept, and every write reads the
   server again, so two devices add up instead of overwriting each other. */
{
  const store = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
  });
  const localKey = `${Lib.STOP_HISTORY_KEY}:u4`;
  store.set(localKey, JSON.stringify([{ id: 'LOCAL', name: 'Local copy', n: 1, t: 1 }]));
  let server = [{ id: 'OTHER', name: 'Other device', n: 3, t: Date.now() - 1000 }];
  let down = true;
  let reads = 0;
  const writes = [];
  const hass = {
    connection: { four: 1 },
    user: { id: 'u4' },
    callWS: async (msg) => {
      if (msg.type === 'frontend/get_user_data') {
        reads += 1;
        if (down) throw new Error('offline');
        return { value: server };
      }
      if (msg.type === 'frontend/set_user_data') {
        writes.push(msg.value);
        server = msg.value;
      }
      return null;
    },
  };
  Lib._stopHistory = null;
  const first = await Lib.loadStopHistory(hass);
  down = false;
  const second = await Lib.loadStopHistory(hass);
  await Lib._stopHistoryWrite;
  check('a failed history read shows the browser copy and is asked again; the browser’s picks then reach the server',
    first.length === 1 && first[0].id === 'LOCAL' && second.some((e) => e.id === 'OTHER') && second.some((e) => e.id === 'LOCAL')
    && writes.length === 1 && reads === 3 && ['OTHER', 'LOCAL'].every((id) => server.some((e) => e.id === id)),
    JSON.stringify({ first: first.map((e) => e.id), second: second.map((e) => e.id), server: server.map((e) => e.id), reads }));
  server = server.concat([{ id: 'LATER', name: 'Picked on the phone', n: 1, t: Date.now() }]);
  await Lib.recordStop(hass, { id: 'MINE', name: 'Picked here' });
  const sent = writes[writes.length - 1] || [];
  check('a pick reads the server again and keeps the other device’s stops',
    writes.length === 2 && ['OTHER', 'LATER', 'MINE', 'LOCAL'].every((id) => sent.some((e) => e.id === id))
    && sent.find((e) => e.id === 'OTHER').n === 3,
    JSON.stringify(sent.map((e) => [e.id, e.n])));
  down = true;
  await Lib.recordStop(hass, { id: 'OFFLINE', name: 'Picked offline' });
  check('when the server cannot be read, a pick stays in the browser and the server copy is left alone',
    writes.length === 2 && JSON.parse(store.get(localKey)).some((e) => e.id === 'OFFLINE'),
    JSON.stringify({ writes: writes.length }));
  Lib._stopHistory = null;
  delete globalThis.localStorage;
}

/* City feed → railway: the station is found for hubs named just
   "Vasútállomás", only a city stop starts it, and the itinerary has its
   own times and the vehicles' real termini. */
{
  const keep = {};
  ['cityIndex', 'searchStops', 'elviraBetween', 'volanServiceOk'].forEach((k) => { keep[k] = Lib[k]; });
  const day = Lib.volanDay(0);
  const m = day.mins;
  const szfIdx = {
    c: [['1111111', '20000101', '20991231']],
    ops: [{ i: 0, id: 'szekesfehervar', name: 'Székesfehérvár — helyi' }],
    byNum: new Map(),
    byKey: new Map(),
    stops: [
      ['szekesfehervar:1', 'Kossuth utca', 47.19, 18.41],
      ['szekesfehervar:2', 'Vasútállomás', 47.1838, 18.4245],
      ['szekesfehervar:3', 'Palotaváros', 47.2, 18.4],
    ].map(([id, name, lat, lon], i) => ({ i, op: 0, id, name, key: Lib.norm(name), fold: Lib.fold(name), lat, lon })),
    t: [[0, '32', [0, 1, 2], [m + 10, m + 20, m + 30]]],
  };
  let indexLoads = 0;
  Lib.cityIndex = async () => { indexLoads += 1; return szfIdx; };
  const stations = [
    { id: 'BKK_005503269', name: 'Székesfehérvár', lat: 47.18361, lon: 18.42472 },
    { id: 'BKK_005503999', name: 'Székesfehérvár-Repülőtér', lat: 47.16, lon: 18.39 },
  ];
  Lib.searchStops = async (_k, _q, mode) => (mode === 'mav' ? stations : []);
  const rideArr = day.base + (m + 20) * 60;
  const trainDep = rideArr + 16 * 60;
  const asked = [];
  Lib.elviraBetween = async (_hass, from, to, _mins, destName) => {
    asked.push({ from, to, destName });
    return [{ label: 'IC 900', dep: trainDep, travel: 50, head: 'Budapest-Déli', destHit: true }];
  };
  const fromBkk = await Lib.cityRailJourney('', { states: {} }, { id: 'BKK_F01020', name: 'Kossuth Lajos utca' },
    { id: 'BKK_005501024', name: 'Budapest-Kelenföld' });
  check('a BKK origin is not matched onto some city feed’s stop',
    fromBkk === null && indexLoads === 0, JSON.stringify({ fromBkk, indexLoads }));
  const j = await Lib.cityRailJourney('', { states: {} }, { id: 'szekesfehervar:1', name: 'Kossuth utca' },
    { id: 'BKK_005501024', name: 'Budapest-Kelenföld' });
  const bus = j && j.legs.find((leg) => leg.label === '32');
  const train = j && j.legs.find((leg) => leg.label === 'IC 900');
  check('a hub called just "Vasútállomás" reaches its railway station and the train',
    !!train && train.from === 'Székesfehérvár' && asked.length === 1 && asked[0].from === 'BKK_005503269'
    && asked[0].to === '005501024',
    JSON.stringify({ legs: j && j.legs.map((l) => l.label || 'walk'), asked }));
  check('the city ride and the train are headed by their own termini',
    !!bus && bus.headsign === 'Palotaváros' && bus.to === 'Vasútállomás'
    && train.headsign === 'Budapest-Déli' && asked[0].destName === '',
    JSON.stringify({ bus: bus && bus.headsign, train: train && train.headsign }));
  check('the city→rail itinerary runs from the first bus to the arrival',
    !!j && j.startMs === bus.startMs && j.endMs === (trainDep + 50 * 60) * 1000
    && j.durationMin === 76 && bus.waitMin === 0,
    JSON.stringify(j && { start: j.startMs, end: j.endMs, dur: j.durationMin, wait: bus && bus.waitMin }));
  const noCoords = { hubName: 'Vasútállomás', hubFold: 'vasutallomas' };
  Lib.searchStops = async () => stations.map((s) => ({ id: s.id, name: s.name }));
  const byTown = await Lib.railStationAtHub('', 'Székesfehérvár', noCoords);
  Lib.searchStops = async () => [{ id: 'BKK_005510001', name: 'Pécs-Külváros' }, { id: 'BKK_005510002', name: 'Pécs' }];
  const pecs = await Lib.railStationAtHub('', 'Pécs', { hubName: 'Főpályaudvar', hubFold: 'fopalyaudvar' });
  Lib.searchStops = async () => [{ id: 'BKK_005510009', name: 'Miskolc-Tiszai' }, { id: 'BKK_005510001', name: 'Miskolc-Gömöri' }];
  const gomori = await Lib.railStationAtHub('', 'Miskolc', { hubName: 'Gömöri pályaudvar', hubFold: 'gomori palyaudvar' });
  check('a hub’s station by the town when the hub has no word of its own, by the hub’s word in a two-station town',
    byTown && byTown.id === 'BKK_005503269' && pecs && pecs.name === 'Pécs' && gomori && gomori.name === 'Miskolc-Gömöri',
    JSON.stringify([byTown && byTown.name, pecs && pecs.name, gomori && gomori.name]));
  let serviceChecks = 0;
  Lib.volanServiceOk = (...args) => { serviceChecks += 1; return keep.volanServiceOk(...args); };
  const fresh = Object.assign({}, szfIdx);
  const p1 = Lib.cityHubPath(fresh, fresh.stops[0]);
  const built = serviceChecks;
  const p2 = Lib.cityHubPath(fresh, fresh.stops[0]);
  check('a city feed’s ride graph is built once a day, not on every question',
    built > 0 && serviceChecks === built && p1 && p2 && p2.hubName === p1.hubName && p1.hubLat === 47.1838,
    JSON.stringify({ built, after: serviceChecks }));
  Object.assign(Lib, keep);
}

/* Transitous: an empty geocode is asked again later, a trip's route once,
   one journey's shapes once, and both ends are looked up together. */
{
  const keep = {};
  ['transitousFetch', 'transitousPlace', 'transitousJourney', 'planPlace', 'fetch', 'cityRailJourney']
    .forEach((k) => { keep[k] = Lib[k]; });
  const realNow = Date.now;
  Lib._geocodeCache.clear();
  Lib._geocodeEmptyAt.clear();
  let geocodes = 0;
  Lib.transitousFetch = async () => { geocodes += 1; return []; };
  await Lib.transitousGeocode('Sehol sincs');
  await Lib.transitousGeocode('Sehol sincs');
  const once = geocodes;
  const later = realNow() + 11 * 60 * 1000;
  Date.now = () => later;
  try {
    await Lib.transitousGeocode('Sehol sincs');
  } finally {
    Date.now = realNow;
  }
  check('an empty geocode answer is asked again after ten minutes, not kept for the page',
    once === 1 && geocodes === 2, JSON.stringify({ once, geocodes }));
  Lib._geocodeCache.clear();
  Lib._geocodeEmptyAt.clear();
  Lib._motisTrips.clear();
  let trips = 0;
  const line = encodePts([[47.5, 19.0], [47.51, 19.02], [47.52, 19.05]]);
  Lib.transitousFetch = async (path) => {
    if (path === '/api/v5/trip') trips += 1;
    return { legs: [{ mode: 'BUS', legGeometry: { points: line, precision: 5 } }] };
  };
  const journey = { legs: [{ walk: false, label: '7', tripId: 'hu-bkk_T1', fromLat: 47.5, fromLon: 19.0, toLat: 47.52, toLon: 19.05 }] };
  const fillA = Lib.fillJourneyShapes(journey);
  const fillB = Lib.fillJourneyShapes(journey);
  await Promise.all([fillA, fillB]);
  const again = await Lib.motisTripShape('motis:hu-bkk_T1');
  check('one journey’s shapes are filled once and a trip’s route is fetched once',
    fillA === fillB && trips === 1 && again.length === 3 && journey.legs[0].shape.length >= 2,
    JSON.stringify({ trips, shape: journey.legs[0].shape && journey.legs[0].shape.length }));
  let release;
  const gate = new Promise((r) => { release = r; });
  const placing = [];
  Lib.transitousPlace = async (stop) => { placing.push(stop.name); await gate; return '47.5,19.0'; };
  Lib.transitousFetch = async () => ({ itineraries: [] });
  const board = Lib.transitousDepartures({ name: 'A' }, { name: 'B', key: 'b' }, {});
  await settleTicks(2);
  const boardBoth = placing.length;
  Lib.transitousJourney = async () => null;
  Lib.cityRailJourney = async () => null;
  Lib.fetch = async () => ({ data: { entry: { plan: { itineraries: [] } } } });
  const planning = [];
  Lib.planPlace = async (_k, stop) => { planning.push(stop.name); await gate; return stop.name + '::47.5,19.0'; };
  const plan = Lib.planJourney('k', { id: 'BKK_A', name: 'A' }, { id: 'BKK_B', name: 'B' }, null, {});
  await settleTicks(2);
  const planBoth = planning.length;
  release();
  await Promise.all([board, plan]);
  check('both ends of a board lookup and of a FUTÁR plan are placed at the same time',
    boardBoth === 2 && planBoth === 2, JSON.stringify({ boardBoth, planBoth }));
  Object.assign(Lib, keep);
  check('the helpers nothing calls are gone',
    ['allowFutarHopShape', 'pickMotisHopRow', 'clipShapeToHop', 'clipShapeFromBoardToward', 'clipShapeFromBoard',
      'estimatePositionFromSchedule', 'mavShapeForRow', 'sliceShapeMeters', 'pointAlongShape']
      .every((k) => typeof Lib[k] === 'undefined'));
}

/* Planner state: hints follow the language, a chosen time's itineraries
   are not planned again on return, and a new origin drops the old one's
   destinations at once. */
{
  const keep = { reachableDests: Lib.reachableDests };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu' });
  const hint = (id) => (pc._planEl(id).querySelector('.hint') || {}).textContent || '';
  const huPick = hint('pdhits');
  pc.setConfig({ language: 'en' });
  check('a language change in the editor rewrites the destination hint',
    huPick === 'Válassz forrás és cél megállót.' && hint('pdhits') === 'Pick an origin and a destination stop.',
    JSON.stringify([huPick, hint('pdhits')]));
  pc._loadDests = async () => {};
  pc.setConfig({ language: 'auto', stopId: 'BKK_A', stopName: 'A' });
  pc.hass = { states: {}, language: 'hu' };
  pc._dests = [{ key: 'xaver', name: 'Xavér utca' }];
  pc._planEl('pdq').value = 'zzz';
  pc._renderDestHits([], 'mode.empty');
  pc.hass = { states: {}, language: 'en' };
  check('a language change keeps a typed query’s "no results", in the new language',
    hint('pdhits') === 'No stop matches this search.' && !pc._planEl('pdhits').querySelector('.hit'),
    pc._planEl('pdhits').innerHTML);
  pc._when = { mode: 'depart', at: Date.now() + 3600000 };
  pc._rows = [];
  pc._loadedAt = Date.now();
  pc._journeyLoading = false;
  pc._journeys = [{ legs: [] }];
  pc._journey = null;
  pc._openJourney = -1;
  const folded = pc._shouldReloadOnConnect();
  pc._journeys = [];
  pc._journeysDone = true;
  const none = pc._shouldReloadOnConnect();
  check('a chosen time with its itineraries folded, or none found, is not planned again on return',
    !folded && !none, JSON.stringify({ folded, none }));
  pc._when = { mode: 'now', at: 0 };
  delete pc._loadDests;
  let release;
  Lib.reachableDests = () => new Promise((r) => { release = r; });
  pc._dests = [{ key: 'old', name: 'Old place' }];
  pc._destRoutes = { old: ['R1'] };
  pc.setConfig({ language: 'en', stopId: 'BKK_NEW', stopName: 'New' });
  await settleTicks();
  const during = { dests: pc._dests.length, routes: Object.keys(pc._destRoutes).length, loading: pc._destsLoading };
  if (release) release({ dests: [{ key: 'new', name: 'New place' }], destRoutes: { new: ['R2'] } });
  await settleTicks();
  check('a new origin from the editor drops the old destinations while its own load',
    during.dests === 0 && during.routes === 0 && during.loading && pc._dests.length === 1 && pc._dests[0].key === 'new',
    JSON.stringify(during));
  Lib.reachableDests = keep.reachableDests;
  pc.remove();
}

/* The inline journey map stays across board repaints, is replaced with a
   new journey and goes with the card; both maps credit their tiles, and
   Leaflet's controls are laid out inside the shadow root too. */
{
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const made = [];
  const tiles = [];
  const fits = [];
  const layer = () => ({ addTo() { return this; } });
  const L = {
    map: (el, opts) => {
      const map = {
        opts,
        setView() { return this; },
        invalidateSize() {},
        fitBounds(b) { fits.push(b); },
        removeLayer() {},
        remove() { this.removed = true; },
        on() {},
      };
      made.push(map);
      return map;
    },
    tileLayer: (url, o) => { tiles.push({ url, o }); return layer(); },
    polyline: () => layer(),
    marker: () => layer(),
    divIcon: (o) => o,
    latLngBounds: (pts) => ({ pts }),
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._ensureLeaflet = async () => L;
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'geo:47.19,18.41', stopName: 'A', destKey: 'b', destName: 'B' });
  const ride = { walk: false, label: '7', color: '009EE3', minutes: 20, from: 'A', to: 'B', shape: [[47.5, 19.0], [47.52, 19.05]] };
  pc._rows = [];
  pc._journey = { durationMin: 20, transfers: 0, walkMin: 0, waitMin: 0, legs: [ride] };
  pc._paint();
  await settleTicks();
  pc._paint();
  pc._showErr(new Error('x'));
  await settleTicks();
  check('a board repaint keeps the inline journey map',
    made.length === 1 && !made[0].removed && !!pc.shadowRoot.querySelector('#pjmap'), String(made.length));
  check('the inline map credits OpenStreetMap France',
    tiles.length === 1 && /OpenStreetMap France/.test(tiles[0].o.attribution) && /openstreetmap\.fr\/osmfr/.test(tiles[0].url));
  pc._journey = { durationMin: 25, transfers: 0, walkMin: 0, waitMin: 0, legs: [Object.assign({}, ride, { minutes: 25 })] };
  pc._paint();
  await settleTicks();
  check('another journey replaces the inline map', made.length === 2 && made[0].removed && !made[1].removed, String(made.length));
  await pc._openJourneyMap();
  const big = made[made.length - 1];
  check('the full journey map is SVG, wheel-zoomable and fitted to the route',
    made.length === 3 && big.opts.preferCanvas === false && big.opts.scrollWheelZoom === true
    && fits.length > 0 && /OpenStreetMap France/.test(tiles[tiles.length - 1].o.attribution),
    JSON.stringify(big.opts));
  pc._closeVehicleMap();
  const css = Array.from(pc.shadowRoot.querySelectorAll('style')).map((el) => el.textContent).join('\n');
  const el = document.createElement('style');
  el.textContent = css;
  document.head.appendChild(el);
  const rules = [...el.sheet.cssRules].filter((r) => r.selectorText);
  el.remove();
  /* The value the last rule naming that selector gives, as the cascade would. */
  const prop = (sel, name) => rules
    .filter((r) => r.selectorText.split(',').map((x) => x.trim()).indexOf(sel) >= 0)
    .map((r) => r.style.getPropertyValue(name).trim())
    .filter(Boolean)
    .pop() || '';
  check('Leaflet’s attribution sits in the inline map’s bottom-right corner',
    prop('.journey-plan .j-map .leaflet-bottom', 'position') === 'absolute'
    && /^0(px)?$/.test(prop('.journey-plan .j-map .leaflet-bottom', 'bottom'))
    && /^0(px)?$/.test(prop('.journey-plan .j-map .leaflet-right', 'right'))
    && prop('.journey-plan .j-map .leaflet-right .leaflet-control', 'float') === 'right',
    JSON.stringify([prop('.journey-plan .j-map .leaflet-bottom', 'position'), prop('.journey-plan .j-map .leaflet-bottom', 'bottom')]));
  check('suggestions and the look-ahead are single rows',
    prop('.plan .suggest', 'flex-direction') === 'row' && prop('.plan .horizon', 'flex-direction') === 'row'
    && !rules.some((r) => /\.horizon-bar|\.horizon \.chips/.test(r.selectorText)));
  pc.remove();
  check('leaving the view removes the inline map', made[1].removed);
  globalThis.requestAnimationFrame = prevRaf;
}

/* Transitous itineraries, its fetch, the index fetch and the hop clip. */
{
  const keep = { transitousFetch: Lib.transitousFetch, fetchBytes: Lib._fetchIndexBytes };
  const asked = [];
  Lib.transitousFetch = async (path, params) => { asked.push(params); return { itineraries: [] }; };
  await Lib.transitousPlan({ name: 'A', lat: 47.5, lon: 19.0 }, { name: 'B', lat: 47.4, lon: 19.1 }, {});
  check('a Transitous journey asks for 12 itineraries by default',
    asked.length === 1 && asked[0].numItineraries === '12', JSON.stringify(asked));
  Lib.transitousFetch = keep.transitousFetch;
  const prevFetch = globalThis.fetch;
  const sent = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), init });
    return { ok: true, status: 200, json: async () => [], arrayBuffer: async () => new TextEncoder().encode('{}').buffer };
  };
  try {
    await Lib.transitousFetch('/api/v1/geocode', { text: 'x' });
    await Lib._fetchIndexBytes('http://x/volan-index.json.gz');
  } finally {
    globalThis.fetch = prevFetch;
  }
  const headers = (sent[0] && sent[0].init && sent[0].init.headers) || {};
  check('a Transitous fetch sends no User-Agent of its own',
    sent.length === 2 && !Object.keys(headers).some((h) => /user-agent/i.test(h)) && headers.Accept === 'application/json',
    JSON.stringify(headers));
  check('an index fetch revalidates instead of bypassing the HTTP cache',
    sent[1] && sent[1].init.cache === 'no-cache', JSON.stringify(sent[1] && sent[1].init));
  const viaBytes = [];
  Lib._fetchIndexBytes = async (url) => { viaBytes.push(url); return { status: 404, bytes: null }; };
  await Lib._loadMavIndex('http://x/mav-index.json.gz');
  await Lib._loadGtfsIndex('http://x/city-index.json.gz', 'errCityHttp', 'errCityGunzip').catch(() => null);
  Lib._fetchIndexBytes = keep.fetchBytes;
  check('both index loaders go through that fetch', viaBytes.length === 2, JSON.stringify(viaBytes));
  const full = [[47.5144, 19.0469], [47.469814, 19.082997], [47.452218, 19.09187], [47.4330, 19.1050]];
  const tram = Lib.clipRideShape(full, [47.469814, 19.082997], [47.452218, 19.09187], { vehicle: 'tram', label: '2B' });
  const ferry = Lib.clipRideShape(full, [47.469814, 19.082997], [47.452218, 19.09187], { vehicle: 'ferry', label: 'D11' });
  check('a tram’s hop map keeps the published line, a ferry’s is cut to the hop',
    tram.length === full.length && ferry.length === 2 && ferry[0][0] === 47.469814 && ferry[1][0] === 47.452218,
    JSON.stringify({ tram: tram.length, ferry }));
}

/* Fourth review: hop card and editor. */
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const settle = async (el) => {
  for (let i = 0; i < 100 && el._reloadActive; i++) await wait(10);
};
{
  /* Every FUTÁR board failing is an error, not an empty timetable; one
     pole failing still leaves the others. */
  const keep = { departureBoard: Lib.departureBoard, originPoles: Lib.originPoles };
  const now = Math.floor(Date.now() / 1000);
  const good = { data: {
    entry: { stopTimes: [{ tripId: 'T1', departureTime: now + 300 }] },
    references: { trips: { T1: { routeId: 'R1', tripHeadsign: 'Zugló' } }, routes: { R1: { id: 'R1', shortName: '7', type: 'BUS' } }, stops: {} },
  } };
  Lib.originPoles = async () => ['P1', 'P2'];
  Lib.departureBoard = async () => { throw new Error('BKK HTTP 401'); };
  const dest = { key: 'zugló', name: 'Zugló' };
  let thrown = null;
  try {
    await Lib.departures('revoked', 'BKK_F1', ['R1'], dest, { mode: 'bkk', minutesAfter: 60, cache: {} });
  } catch (err) { thrown = err; }
  let mavThrown = null;
  try {
    await Lib.departures('revoked', 'BKK_005510017', ['R1'], dest, { mode: 'mav', minutesAfter: 60, cache: {} });
  } catch (err) { mavThrown = err; }
  Lib.departureBoard = async (_k, pid) => {
    if (pid === 'P1') throw new Error('BKK timeout');
    return good;
  };
  const partial = await Lib.departures('k', 'BKK_F1', ['R1'], dest, { mode: 'bkk', minutesAfter: 60, cache: {} });
  Object.assign(Lib, keep);
  check('every failed FUTÁR board rejects departures() with the first error (bkk and mav)',
    !!thrown && thrown.message === 'BKK HTTP 401' && !!mavThrown && mavThrown.message === 'BKK HTTP 401',
    JSON.stringify({ bkk: thrown && thrown.message, mav: mavThrown && mavThrown.message }));
  check('one failed pole still returns the other pole\'s rows',
    partial.length === 1 && partial[0].tripId === 'T1', JSON.stringify(partial.map((r) => r.tripId)));
}
{
  /* The shared board cache keeps a card key apart from the integration. */
  const keepFetch = Lib.fetch;
  Lib._boards.clear();
  Lib._boardInflight.clear();
  const calls = [];
  Lib.fetch = async (apiKey) => {
    calls.push(apiKey);
    if (apiKey === 'revoked') throw new Error('BKK HTTP 401');
    return { data: { entry: { stopTimes: [] } } };
  };
  await Lib.departureBoard('', 'S9', 60);
  let err = null;
  try { await Lib.departureBoard('revoked', 'S9', 60); } catch (e) { err = e; }
  await Lib.departureBoard('', 'S9', 60);
  await Lib.departureBoard('good', 'S9', 60);
  Lib.fetch = keepFetch;
  Lib._boards.clear();
  check('a card with its own key never gets the board fetched with another key or the integration',
    JSON.stringify(calls) === JSON.stringify(['', 'revoked', 'good']) && !!err && /401/.test(err.message),
    JSON.stringify({ calls, err: err && err.message }));
}
{
  /* The card shows that error, and still fills the board from Transitous. */
  const keep = { departures: Lib.departures, fillTransitousIfEmpty: Lib.fillTransitousIfEmpty };
  const now = Math.floor(Date.now() / 1000);
  Lib.departures = async () => { throw new Error('BKK HTTP 401'); };
  Lib.fillTransitousIfEmpty = async () => [{
    tripId: 'motis:1', label: '7', vehicle: 'bus', rawType: 'BUS', head: 'Zugló', dep: now + 300, sched: now + 300,
  }];
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  c.setConfig({ language: 'hu', apiKey: 'revoked', stopId: 'BKK_F1', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds: [] });
  await settle(c);
  const errEl = c.shadowRoot.querySelector('.err');
  check('a failed FUTÁR board shows its error, with Transitous rows under it',
    !!errEl && errEl.textContent.includes('BKK HTTP 401') && errEl.style.display !== 'none'
    && c.shadowRoot.querySelectorAll('tr').length === 1 && !c.shadowRoot.innerHTML.includes('Nincs közelgő indulás'),
    JSON.stringify({ err: errEl && errEl.textContent, rows: c.shadowRoot.querySelectorAll('tr').length }));
  c.remove();
  Object.assign(Lib, keep);
}
{
  /* A new pair drops the old pair's rows, even when its first fetch fails. */
  const keep = { departures: Lib.departures, fillTransitousIfEmpty: Lib.fillTransitousIfEmpty };
  const now = Math.floor(Date.now() / 1000);
  let fail = false;
  Lib.departures = async () => {
    if (fail) throw new Error('BKK timeout');
    return [{ tripId: 'BKK_A1', label: '7', vehicle: 'bus', rawType: 'BUS', head: 'Zugló', dep: now + 300, sched: now + 300 }];
  };
  Lib.fillTransitousIfEmpty = async (rows) => rows || [];
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  c.setConfig({ language: 'hu', apiKey: 'k', stopId: 'BKK_F1', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds: [] });
  await settle(c);
  const before = c.shadowRoot.querySelectorAll('tr').length;
  fail = true;
  c.setConfig({ language: 'hu', apiKey: 'k', stopId: 'BKK_F2', stopName: 'Astoria', destKey: 'óbuda', destName: 'Óbuda', routeIds: [] });
  await settle(c);
  const head = c.shadowRoot.querySelector('.head').textContent;
  check('a new pair whose first fetch fails shows no rows of the old pair',
    before === 1 && c.shadowRoot.querySelectorAll('tr').length === 0 && (c._rawRows || []).length === 0
    && head.includes('Astoria') && c.shadowRoot.querySelector('.err').textContent.includes('BKK timeout'),
    JSON.stringify({ before, after: c.shadowRoot.querySelectorAll('tr').length, head }));
  c.remove();
  Object.assign(Lib, keep);
}
{
  /* Opening the editor keeps the configured routes; a card without any
     gets them filled in. */
  const keep = { reachableDests: Lib.reachableDests };
  Lib.reachableDests = async () => ({
    dests: [{ key: 'zugló', name: 'Zugló' }], destRoutes: { zugló: ['BKK_NIGHT'] },
  });
  const hass = { states: {}, language: 'hu', services: { hungarian_transport: { futar: {} } } };
  const open = async (routeIds) => {
    const ed = document.createElement('hungarian-transport-card-editor');
    document.body.appendChild(ed);
    ed._probeKey = () => {};
    const emitted = [];
    ed.addEventListener('config-changed', (ev) => emitted.push(ev.detail.config));
    ed.hass = hass;
    ed.setConfig({ language: 'hu', stopId: 'BKK_F1', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds });
    await wait(20);
    ed.remove();
    return emitted;
  };
  const configured = await open(['BKK_7', 'BKK_9']);
  const bare = await open([]);
  Object.assign(Lib, keep);
  Lib._hass = null;
  check('opening the editor does not rewrite a card\'s routes with the ones running now',
    configured.length === 0, JSON.stringify(configured.map((c) => c.routeIds)));
  check('a card without routes gets the reachable ones',
    bare.length === 1 && JSON.stringify(bare[0].routeIds) === '["BKK_NIGHT"]', JSON.stringify(bare.map((c) => c.routeIds)));
}
{
  /* The editor's "A → B" title follows the next pair; a typed one stays. */
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  let last = null;
  ed.addEventListener('config-changed', (ev) => { last = ev.detail.config; });
  ed.setConfig({ language: 'hu', stopId: 'A1', stopName: 'Alfa', destKey: 'béta', destName: 'Béta', name: 'Alfa → Béta', routeIds: ['R'] });
  ed._pickStop({ key: 'gamma', name: 'Gamma' }, 'dest');
  const sameOrigin = last.name;
  ed.setConfig(last);
  ed._pickStop({ id: 'D1', name: 'Delta' }, 'origin');
  ed.setConfig(last);
  const afterOrigin = last.name;
  ed._pickStop({ key: 'eps', name: 'Epszilon' }, 'dest');
  const newPair = last.name;
  ed.setConfig(Object.assign({}, last, { name: 'Munkába' }));
  ed._pickStop({ key: 'zeta', name: 'Zéta' }, 'dest');
  const typed = last.name;
  ed.remove();
  check('the auto title follows a new destination and a new pair, a typed title stays',
    sameOrigin === 'Alfa → Gamma' && afterOrigin === 'Alfa → Gamma' && newPair === 'Delta → Epszilon' && typed === 'Munkába',
    JSON.stringify({ sameOrigin, afterOrigin, newPair, typed }));
}
{
  /* Volán and city editors do not copy a sibling card's key in. */
  const keep = { apiKeyFromDashboard: Lib.apiKeyFromDashboard, searchStops: Lib.searchStops };
  let asked = 0;
  Lib.apiKeyFromDashboard = async () => { asked += 1; return 'sibling-key'; };
  Lib.searchStops = async () => [];
  const make = async (cfg) => {
    const ed = document.createElement('hungarian-transport-card-editor');
    document.body.appendChild(ed);
    ed._probeKey = async () => {};
    ed._loadDests = async () => {};
    ed.setConfig(cfg);
    ed.hass = { states: {}, language: 'hu', connection: {} };
    await ed._search('Győr', 'origin');
    await wait(5);
    ed.remove();
    return { saved: ed._config.apiKey, used: ed._apiKey() };
  };
  const volanKey = (await make({ language: 'hu', volan: true })).used;
  const helyiKey = (await make({ language: 'hu', helyi: true, city: 'gyor' })).used;
  const volanAsked = asked;
  const bkk = await make({ language: 'hu' });
  Object.assign(Lib, keep);
  Lib._hass = null;
  check('a Volán or city editor never borrows a sibling card\'s API key',
    volanAsked === 0 && !volanKey && !helyiKey, JSON.stringify({ volanAsked, volanKey, helyiKey }));
  /* A card-level key is deprecated: the sibling's key serves the editor's
     lookups but is not copied into the new card's YAML. */
  check('a BKK editor borrows it for its lookups without saving it into the card',
    bkk.used === 'sibling-key' && !bkk.saved, JSON.stringify({ used: !!bkk.used, saved: !!bkk.saved }));
}
{
  /* The banner is worded at paint: a language switch rewords it. */
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'auto', stopId: 'BKK_F00001', stopName: 'Keleti', destKey: 'blaha', destName: 'Blaha' });
  await settle(c);
  const hu = c.shadowRoot.querySelector('.err').textContent;
  c.hass = { states: {}, language: 'en', services: {} };
  const en = c.shadowRoot.querySelector('.err').textContent;
  c.remove();
  check('the error banner follows a language change',
    hu.startsWith('Nincs BKK API kulcs') && en.startsWith('No BKK API key'), JSON.stringify({ hu, en }));

  const keep = { cityIndex: Lib.cityIndex };
  Lib.cityIndex = async () => ({ ops: [{ id: 'gyor', name: 'Győr' }] });
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  ed.setConfig({ language: 'hu', helyi: true });
  await wait(5);
  const huCity = ed._el('city').options[0].textContent;
  ed._error(Object.assign(new Error('no key'), { i18nKey: 'errNoApiKey' }));
  const huErr = ed._el('err').textContent;
  ed.setConfig({ language: 'en', helyi: true });
  await wait(5);
  const enCity = ed._el('city').options[0].textContent;
  const enErr = ed._el('err').textContent;
  ed.remove();
  Object.assign(Lib, keep);
  check('the editor rewords the city placeholder and its error after a language switch',
    huCity === 'Válassz várost' && enCity === 'Pick a city'
    && huErr.startsWith('Nincs BKK API kulcs') && enErr.startsWith('No BKK API key'),
    JSON.stringify({ huCity, enCity, huErr, enErr }));
}
{
  /* The open map: footer state kept across polls, the vehicle placed once
     GPS shows up for a row whose ids changed, no fitBounds after a close,
     focus on the close button and back on the row's button. */
  const made = [];
  let removed = false;
  let fitAfterRemove = 0;
  const layer = (pt) => ({
    pt, addTo() { return this; }, setLatLng(p) { this.pt = p; }, setIcon(i) { this.icon = i; }, redraw() {},
    getLatLng() { return this.pt ? { lat: this.pt[0], lng: this.pt[1] } : null; },
  });
  const fakeMap = {
    setView() { return this; }, invalidateSize() {}, removeLayer() {}, on() {},
    fitBounds() { if (removed) fitAfterRemove += 1; },
    remove() { removed = true; },
  };
  const fakeL = {
    map: () => { removed = false; return fakeMap; },
    tileLayer: () => layer(), divIcon: (o) => o, polyline: () => layer(), latLngBounds: () => ({ extend() {} }),
    marker: (pt, o) => {
      const m = Object.assign(layer(pt), { html: (o.icon && o.icon.html) || '' });
      made.push(m);
      return m;
    },
  };
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const c = document.createElement('hungarian-transport-card');
  c._reloadSafe = () => {};
  document.body.appendChild(c);
  c.setConfig({ language: 'hu', stopId: 'BKK_F1', stopName: 'Fő tér', destKey: 'zugló', destName: 'Zugló', routeIds: [] });
  c._ensureLeaflet = async () => fakeL;
  c._loadRowMapShape = async () => ({
    shape: [[47.57, 19.12], [47.51, 19.08]], approx: true, board: [47.57, 19.12], alight: [47.51, 19.08],
  });
  const now = Math.floor(Date.now() / 1000);
  const row = {
    label: '7', headsign: 'Zugló', tripId: 'elvira:77', depTs: now + 300, schedTs: now + 300,
    attime: '10:05', predicted_attime: '10:05', color: '009EE3', textcolor: 'ffffff', icon: 'mdi:bus', hasGps: false,
  };
  c._rows = [row];
  c._paint();
  c.shadowRoot.querySelector('.map-open').focus();
  const hadFocus = c.shadowRoot.activeElement && c.shadowRoot.activeElement.classList.contains('map-open');
  await c._openVehicleMap(c._shownRows[0]);
  const closeBtn = document.querySelector('.ht-map-overlay .ht-map-close');
  const focusedClose = document.activeElement === closeBtn;
  const foot = () => document.querySelector('.ht-map-overlay .ht-map-foot').textContent;
  const opened = foot();
  const noVehicle = !made.some((m) => m.html.includes('ht-veh-dot'));
  /* A poll: the ELVIRA row now carries the FUTÁR trip id, a vehicle id and GPS. */
  c._rows = [Object.assign({}, row, { tripId: 'BKK_77', vehicleId: 'V1', hasGps: true, lat: 47.55, lon: 19.1 })];
  c._paint();
  c._syncOpenMap();
  const veh = made.find((m) => m.html.includes('ht-veh-dot'));
  const placedAt = veh && veh.pt.slice();
  const synced = foot();
  c._rows = [Object.assign({}, c._rows[0], { lat: 47.56, lon: 19.11 })];
  c._syncOpenMap();
  const moved = veh && veh.pt[0] === 47.56;
  c._closeVehicleMap();
  const backOnRow = c.shadowRoot.activeElement && c.shadowRoot.activeElement.classList.contains('map-open');
  check('the open map keeps its "approximate line" footer when GPS arrives',
    opened.startsWith('Közelítő vonal') && synced.startsWith('Közelítő vonal') && synced.includes('Élő pozíció'),
    JSON.stringify({ opened, synced }));
  check('a vehicle that gets GPS after the map opened is placed, and followed, though its ids changed',
    noVehicle && !!placedAt && placedAt[0] === 47.55 && moved, JSON.stringify({ noVehicle, placedAt, now: veh && veh.pt }));
  check('the map moves focus to its close button and back to the row\'s map button',
    hadFocus && focusedClose && backOnRow, JSON.stringify({ hadFocus, focusedClose, backOnRow }));
  await c._openVehicleMap(c._shownRows[0]);
  c._closeVehicleMap();
  await wait(120);
  check('a map closed right after it opened is not fitted any more', removed && fitAfterRemove === 0,
    JSON.stringify({ removed, fitAfterRemove }));
  c._showMapNotice('Nincs adat');
  const ok = document.querySelector('.ht-map-notice button');
  const okFocused = document.activeElement === ok;
  document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  check('the map notice takes focus and closes on Escape',
    okFocused && !document.querySelector('.ht-map-overlay'), JSON.stringify({ okFocused }));
  c.remove();
  globalThis.requestAnimationFrame = prevRaf;
}
{
  /* A card that is built but not on the page holds no gate slot, does not
     tick and does not fetch; attaching it starts all three. */
  const gate0 = Lib._gateCards;
  const c = document.createElement('hungarian-transport-card');
  let reloads = 0;
  c._reload = async () => { reloads += 1; };
  c.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa' });
  await wait(5);
  const idle = { gate: Lib._gateCards - gate0, tick: c._tick !== null, reloads };
  document.body.appendChild(c);
  await wait(5);
  const live = { gate: Lib._gateCards - gate0, tick: c._tick !== null, reloads };
  c.remove();
  check('setConfig on a detached card waits for connectedCallback',
    idle.gate === 0 && !idle.tick && idle.reloads === 0 && live.gate === 1 && live.tick && live.reloads === 1
    && Lib._gateCards === gate0, JSON.stringify({ idle, live }));
}
{
  /* Index overrides belong to the card or editor using them. */
  const keep = { _indexCached: Lib._indexCached, departures: Lib.departures, fillTransitousIfEmpty: Lib.fillTransitousIfEmpty };
  const urls = [];
  Lib._indexCached = (url) => { urls.push(url); return Promise.resolve({ ops: [] }); };
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  /* Each card / editor looks its indexes up through its own view (Lib.withIndexes(config), J-2): the
     module-wide lookup is never switched by a card, so it stays on the bundled index meanwhile. */
  ed.setConfig({ language: 'hu', volan: true, volanIndex: 'https://example.invalid/editor-volan.json.gz' });
  Lib.withIndexes(ed._config).volanIndex();
  Lib.volanIndex();
  ed.setConfig({ language: 'hu', volan: true });
  Lib.withIndexes(ed._config).volanIndex();
  ed.remove();
  /* like the real departures(): the index through `this`, the card's own view */
  Lib.departures = async function departuresStub() { this.volanIndex(); return []; };
  Lib.fillTransitousIfEmpty = async (rows) => rows || [];
  const runCard = async (extra) => {
    const c = document.createElement('hungarian-transport-card');
    c._reloadSafe = () => {};
    document.body.appendChild(c);
    c.setConfig(Object.assign({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa' }, extra));
    await c._reload();
    c.remove();
  };
  await runCard({ volanIndex: 'https://example.invalid/card-volan.json.gz' });
  await runCard({});
  Object.assign(Lib, keep);
  check('a card or editor without its own index URL is back on the bundled index',
    urls.length === 5 && urls[0].includes('editor-volan') && urls[1].includes('/volan-index.json.gz')
    && urls[2].includes('/volan-index.json.gz')
    && urls[3].includes('card-volan') && urls[4].includes('/volan-index.json.gz'),
    JSON.stringify(urls));
}
{
  /* ELVIRA only when the integration has the service; after a failure, a
     pause and one warning. */
  const realNow = Date.now;
  const keep = { callServiceResponse: Lib.callServiceResponse };
  const keepWarn = console.warn;
  let calls = 0;
  let fail = true;
  const warns = [];
  Lib.callServiceResponse = async () => {
    calls += 1;
    if (fail) throw new Error('unavailable');
    return { response: { departures: [{ tripId: 'elvira:1' }] } };
  };
  console.warn = (...a) => warns.push(a.map(String).join(' '));
  Lib._elviraDownUntil = 0;
  Lib._elviraWarned = false;
  const ask = (hass) => Lib.elviraBetween(hass, 'BKK_005510017', 'BKK_005503269', 60, 'Székesfehérvár', {});
  const without = { states: {}, services: { hungarian_transport: { futar: {} } } };
  const withSvc = { states: {}, services: { hungarian_transport: { elvira_between: {} } } };
  await ask(without);
  const noService = calls;
  await ask(withSvc);
  await ask(withSvc);
  const backedOff = calls;
  let t = realNow() + 4 * 60000;
  Date.now = () => t;
  await ask(withSvc);
  const retried = calls;
  t += 4 * 60000;
  fail = false;
  const rows = await ask(withSvc);
  Date.now = realNow;
  console.warn = keepWarn;
  Object.assign(Lib, keep);
  Lib._elviraDownUntil = 0;
  Lib._elviraWarned = false;
  check('ELVIRA is not called without the elvira_between service',
    noService === 0, String(noService));
  check('a failed ELVIRA call pauses ELVIRA for minutes and warns once',
    backedOff === 1 && retried === 2 && warns.length === 1 && rows.length === 1,
    JSON.stringify({ backedOff, retried, warns: warns.length, rows: rows.length }));
}
{
  /* One scan of hass.states per change, and one pattern per row. */
  let scans = 0;
  const target = {
    'sensor.x': { attributes: { vehicles: [
      { lat: 47.2, lon: 18.6, trip: { tripShortName: '861 BALATON' } },
      { lat: 47.3, lon: 18.7, trip: { tripShortName: '9724 személyvonat' } },
      { lat: 47.4, lon: 18.8, trip: { tripShortName: '2000 TÓPART' } },
    ] } },
  };
  const states = new Proxy(target, { ownKeys(tgt) { scans += 1; return Reflect.ownKeys(tgt); } });
  const hass = { states };
  const RealRegExp = globalThis.RegExp;
  let patterns = 0;
  globalThis.RegExp = class extends RealRegExp {
    constructor(...args) { super(...args); patterns += 1; }
  };
  let rows;
  for (let i = 0; i < 4; i++) {
    rows = [
      { label: 'BALATON', trainNumber: '861', tripId: 'elvira:1' },
      { label: 'TÓPART', trainNumber: '', tripId: 'elvira:2' },
    ];
    Lib.enrichFromHass(hass, rows);
  }
  globalThis.RegExp = RealRegExp;
  const fresh = Lib.collectHassVehicles({ states: Object.assign({}, target) });
  check('enrichFromHass scans hass.states once per states object and builds one pattern per row',
    scans === 1 && patterns === 4 * 2 && rows[0].lat === 47.2 && rows[1].lat === 47.4 && fresh.length === 3,
    JSON.stringify({ scans, patterns, lat: rows.map((r) => r.lat) }));
}
{
  /* Back after a short tab switch the poll picks up; after a long one, a run. */
  const keep = { departures: Lib.departures, fillTransitousIfEmpty: Lib.fillTransitousIfEmpty };
  const now = Math.floor(Date.now() / 1000);
  let runs = 0;
  /* Cards left on the page by earlier checks hear the same events. */
  Lib.departures = async (_k, stopId) => {
    if (stopId === 'volan_vis') runs += 1;
    return [{ tripId: 'gtfs:1', label: '700', vehicle: 'coach', rawType: 'BUS', head: 'Pápa', dep: now + 600, sched: now + 600 }];
  };
  Lib.fillTransitousIfEmpty = async (rows) => rows || [];
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu' };
  c.setConfig({ language: 'hu', volan: true, stopId: 'volan_vis', stopName: 'Győr', destKey: 'pápa', destName: 'Pápa', refresh: 60 });
  await settle(c);
  const first = runs;
  const realNow = Date.now;
  let t = realNow();
  Date.now = () => t;
  let vis = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => vis });
  vis = 'hidden';
  document.dispatchEvent(new Event('visibilitychange'));
  const stopped = c._poll === null;
  t += 10000;
  vis = 'visible';
  document.dispatchEvent(new Event('visibilitychange'));
  await wait(10);
  const quick = { runs: runs - first, poll: c._poll !== null, tick: c._tick !== null };
  vis = 'hidden';
  document.dispatchEvent(new Event('visibilitychange'));
  t += 120000;
  vis = 'visible';
  document.dispatchEvent(new Event('visibilitychange'));
  await settle(c);
  await wait(10);
  const long = { runs: runs - first, poll: c._poll !== null };
  Date.now = realNow;
  delete document.visibilityState;
  c.remove();
  Object.assign(Lib, keep);
  check('a short tab switch resumes the poll without a refetch, a long one refetches',
    first === 1 && stopped && quick.runs === 0 && quick.poll && quick.tick && long.runs === 1 && long.poll,
    JSON.stringify({ first, stopped, quick, long }));
}
{
  /* The loader retries a download that broke off, not a bundle that threw. */
  const { copyFileSync, mkdtempSync, rmSync, writeFileSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { pathToFileURL } = await import('node:url');
  const dir = mkdtempSync(join(tmpdir(), 'ht-loader-eval-'));
  writeFileSync(join(dir, 'hungarian-transport.js'),
    'globalThis.__htEvalRuns = (globalThis.__htEvalRuns || 0) + 1;\n'
    + 'throw new TypeError("Cannot read properties of undefined (reading \'define\')");\n');
  copyFileSync('dist/hungarian-transport-loader.js', join(dir, 'hungarian-transport-loader.js'));
  const warns = [];
  const errors = [];
  const keepWarn = console.warn;
  const keepErr = console.error;
  console.warn = (...a) => warns.push(a.map(String).join(' '));
  console.error = (...a) => errors.push(a.map(String).join(' '));
  await import(`${pathToFileURL(join(dir, 'hungarian-transport-loader.js')).href}?v=9.9.9`);
  for (let i = 0; i < 80 && !warns.length && !errors.length; i++) await wait(25);
  console.warn = keepWarn;
  console.error = keepErr;
  rmSync(dir, { recursive: true, force: true });
  check('the loader does not retry a bundle that threw while running',
    globalThis.__htEvalRuns === 1 && warns.length === 0 && errors.length === 1,
    JSON.stringify({ runs: globalThis.__htEvalRuns, warns, errors }));
}
{
  /* Every editor label names its control. */
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed.setConfig({ language: 'hu' });
  const labels = [...ed.querySelectorAll('label[id]')];
  const missing = labels.filter((l) => !l.htmlFor || !ed.querySelector('#' + l.htmlFor)).map((l) => l.id);
  ed.remove();
  check('editor labels point at their fields', labels.length === 7 && missing.length === 0,
    JSON.stringify({ n: labels.length, missing }));
}

/* ---- Bugs found while shooting the user manual ---- */

/* A Volán coach's map is a coach's, not a train's; a ferry's a ferry's. */
{
  const prevL = window.L;
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const layer = () => ({ addTo() { return this; }, setLatLng() {}, setIcon() {}, redraw() {} });
  window.L = {
    map: () => ({ setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {} }),
    tileLayer: () => layer(),
    divIcon: (o) => o,
    marker: () => layer(),
    polyline: () => layer(),
    latLngBounds: () => ({ extend() {} }),
  };
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c._reloadSafe = () => {};
  c._loadRowMapShape = async () => ({ shape: [[47.68, 17.63], [47.33, 17.47]], board: [47.68, 17.63], alight: null });
  const head = async (language, row) => {
    c.setConfig({ language, stopId: 'volan_1', stopName: 'Győr', volan: true, routeIds: [] });
    await c._openVehicleMap(row);
    const meta = document.querySelector('.ht-map-overlay .ht-map-head .meta');
    const out = meta ? [meta.firstElementChild.textContent, meta.querySelector('.sub').textContent] : [];
    c._closeVehicleMap();
    return out;
  };
  const coach = { label: '1180', headsign: 'Pápa', vehicle: 'coach', type: 'COACH', tripId: 'gtfs:1180-1' };
  const ferry = { label: 'D12', headsign: 'Római fürdő', vehicle: 'ferry', type: 'FERRY', tripId: 'BKK_D12' };
  const huCoach = await head('hu', coach);
  const enCoach = await head('en', coach);
  check('a Volán map header shows a coach, not a train',
    huCoach[0].startsWith('\u{1F68C} ') && huCoach[1] === 'Távolsági busz'
    && enCoach[0].startsWith('\u{1F68C} ') && enCoach[1] === 'Coach',
    JSON.stringify({ huCoach, enCoach }));
  const huFerry = await head('hu', ferry);
  const enFerry = await head('en', ferry);
  check('a ferry map header shows a ferry, not a vehicle pin',
    huFerry[0].startsWith('⛴ ') && huFerry[1] === 'Hajó'
    && enFerry[0].startsWith('⛴ ') && enFerry[1] === 'Ferry',
    JSON.stringify({ huFerry, enFerry }));
  c.remove();
  window.L = prevL;
  globalThis.requestAnimationFrame = prevRaf;
}

/* The editor lists the reachable destinations as soon as they load, not
   only once the destination field is focused. */
{
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  const keepReach = Lib.reachableDests;
  let release = null;
  Lib.reachableDests = () => new Promise((r) => { release = r; });
  ed.setConfig({ volan: true, language: 'hu', stopId: 'volan_1', stopName: 'Győr' });
  const box = ed.querySelector('#destHits');
  const loadingShown = !!box && box.textContent.includes('Elérhető célok betöltése');
  release({ dests: [{ key: 'pápa', name: 'Pápa' }, { key: 'tata', name: 'Tata' }], destRoutes: {} });
  await settleTicks();
  const names = [...box.querySelectorAll('button.hit')].map((b) => b.textContent);
  check('loaded destinations replace the loading hint without focusing the field',
    loadingShown && !ed._destsLoading && JSON.stringify(names) === '["Pápa","Tata"]'
    && !box.textContent.includes('betöltése'),
    JSON.stringify({ loadingShown, names, box: box && box.textContent }));
  Lib.reachableDests = keepReach;
  ed.remove();
}

/* Folding an itinerary, or opening another, right after its map was drawn
   must not leave Leaflet to throw from a removed map. The fake models
   Leaflet 1.9.4: an animated zoom ends on its own 250 ms timer, which reads
   the map pane remove() has deleted ("reading '_leaflet_pos'"), and any view
   change on a removed map throws. */
{
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const made = [];
  const thrown = [];
  const late = [];
  const layer = () => ({ addTo() { return this; }, setLatLng() {}, setIcon() {}, redraw() {} });
  const L = {
    map: (el, opts) => {
      const map = {
        opts: opts || {},
        loaded: false,
        removed: false,
        view(name, o) {
          if (this.removed) {
            late.push(name);
            throw new TypeError("Cannot read properties of undefined (reading '_leaflet_pos')");
          }
          const animated = this.loaded && this.opts.zoomAnimation !== false && !(o && o.animate === false);
          if (name !== 'invalidateSize') this.loaded = true;
          if (animated) setTimeout(() => { if (this.removed) thrown.push(name); }, 250);
        },
        setView(c, z, o) { this.view('setView', o); return this; },
        fitBounds(b, o) { this.view('fitBounds', o); return this; },
        invalidateSize() { this.view('invalidateSize', { animate: false }); return this; },
        removeLayer() {},
        remove() { this.removed = true; return this; },
        on() {},
      };
      made.push(map);
      return map;
    },
    tileLayer: () => layer(),
    polyline: () => layer(),
    marker: () => layer(),
    divIcon: (o) => o,
    latLngBounds: (pts) => ({ pts, extend() {} }),
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._ensureLeaflet = async () => L;
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'geo:47.19,18.41', stopName: 'A', destKey: 'b', destName: 'B' });
  const ride = (minutes) => ({ walk: false, label: '7', color: '009EE3', minutes, from: 'A', to: 'B', shape: [[47.5, 19.0], [47.52, 19.05]] });
  const journey = (min) => ({ durationMin: min, transfers: 0, walkMin: 0, waitMin: 0, legs: [ride(min)] });
  pc._rows = [];
  pc._when = { mode: 'depart', at: Date.now() + 3600 * 1000 };
  pc._journeys = [journey(20), journey(25)];
  pc._openJourney = 0;
  pc._journey = pc._journeys[0];
  pc._paint();
  await settleTicks();
  const first = made[0];
  const heads = () => pc.shadowRoot.querySelectorAll('.itin-head');
  heads()[0].click();
  heads()[1].click();
  await settleTicks();
  await new Promise((r) => setTimeout(r, 320));
  check('folding an itinerary, or opening another, right after its map was drawn throws nothing',
    made.length === 2 && first.removed && !made[1].removed && pc._plannerMap === made[1]
    && thrown.length === 0 && late.length === 0,
    JSON.stringify({ maps: made.length, thrown, late }));
  check('the inline map never animates a zoom', made.every((m) => m.opts.zoomAnimation === false),
    JSON.stringify(made.map((m) => m.opts.zoomAnimation)));

  await pc._openJourneyMap();
  pc._closeVehicleMap();
  pc._loadRowMapShape = async () => ({ shape: [[47.5, 19.0], [47.52, 19.05]], board: [47.5, 19.0], alight: null });
  await pc._openVehicleMap({ label: '7', headsign: 'B', color: '009EE3', tripId: 'BKK_T7' });
  pc._closeVehicleMap();
  await new Promise((r) => setTimeout(r, 320));
  check('closing the journey or a vehicle map right after it opened throws nothing',
    made.length === 4 && made[2].removed && made[3].removed && thrown.length === 0 && late.length === 0,
    JSON.stringify({ maps: made.length, thrown, late }));
  pc.remove();
  globalThis.requestAnimationFrame = prevRaf;
}


/* J-2: the index overrides (volanIndex / cityIndex / mavIndex) belong to ONE card. Two editors search
   at the same time, only one of them with its own Volán index; the search reads the index again after
   a Transitous round trip, i.e. after an await, when the other card has already run its own lookup. */
{
  const keep = { fetchIdx: Lib._fetchIndexBytes, geo: Lib.transitousGeocode };
  const asked = [];
  Lib._fetchIndexBytes = async (url) => { asked.push(String(url)); return { status: 404, bytes: null }; };
  const gates = [];
  Lib.transitousGeocode = () => { const d = deferred(); gates.push(d); return d.p; };
  const mk = (extra) => {
    const ed = document.createElement('hungarian-transport-card-editor');
    document.body.appendChild(ed);
    ed.hass = { states: {}, language: 'hu' };
    ed._probeKey = () => {};
    ed._loadDests = async () => {};
    ed._fillKey = async () => {};
    ed.setConfig({ language: 'hu', volan: true, ...extra });
    return ed;
  };
  const own = 'https://a.invalid/own/volan-index.json.gz';
  const a = mk({ volanIndex: own });
  const b = mk({});
  const runA = a._search('Győr', 'origin');
  await tick();
  const runB = b._search('Pápa', 'origin');
  await tick();
  gates.forEach((g) => g.resolve([]));
  await tick();
  gates.forEach((g) => g.resolve([]));
  await Promise.all([runA, runB]);
  const volanAsked = asked.filter((u) => /volan-index\.json\.gz/.test(u));
  const ownAsked = volanAsked.filter((u) => u.startsWith(own));
  const bundledAsked = volanAsked.filter((u) => !u.startsWith(own));
  check('an index override stays with its own card across awaits (two editors, one override)',
    ownAsked.length >= 2 && ownAsked.length === bundledAsked.length,
    JSON.stringify({ own: ownAsked.length, bundled: bundledAsked.length, gates: gates.length }));
  a.remove();
  b.remove();
  Lib._fetchIndexBytes = keep.fetchIdx;
  Lib.transitousGeocode = keep.geo;
}


/* O-1: a favourite whose name is not text in the YAML (`name: 1234`) must not break the editor. */
{
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  let thrown = null;
  try {
    ed.setConfig({ language: 'hu', favorites: [{ id: 'BKK_F1', name: 1234 }, { id: 'BKK_F2', name: 'Deák tér / M1' }] });
    ed._paintFav();
  } catch (err) { thrown = String(err && err.message || err); }
  const chips = Array.from(ed._el('fav') ? ed._el('fav').querySelectorAll('button.chip') : []).map((b) => b.textContent);
  check('a non-text favourite name (name: 1234) paints as text, nothing throws',
    thrown === null && chips[0] === '1234' && chips[1] === 'Deák tér', JSON.stringify({ thrown, chips }));
  ed.remove();
}


/* J-3: a BKK key in the card YAML is deprecated (it travels in the request URL and any logged-in Home
   Assistant user can read it from lovelace/config). It still works; the card warns once per page,
   without the key in the text, and the editor says so next to the field. */
{
  const secret = 'deadbeef-1111-2222-3333-cafebabe0000';
  const warns = [];
  const keepWarn = console.warn;
  console.warn = (...args) => { warns.push(args.map(String).join(' ')); };
  Lib._warnedCardKey = false;
  const mkCard = (extra) => {
    const c = document.createElement('hungarian-transport-card');
    c._reloadSafe = () => {};
    c.setConfig(Object.assign({ language: 'hu', stopId: 'BKK_F01', stopName: 'Deák tér' }, extra));
    return c;
  };
  mkCard({ apiKey: secret });
  mkCard({ apiKey: secret });
  mkCard({});
  console.warn = keepWarn;
  const mine = warns.filter((w) => /apiKey/.test(w) && /deprecated/i.test(w));
  check('a card-level apiKey warns once per page that it is deprecated, without the key itself',
    mine.length === 1 && !warns.some((w) => w.includes(secret)), JSON.stringify({ n: mine.length, any: warns.length }));
  check('the card-level apiKey still reaches the card (no behaviour change)',
    mkCard({ apiKey: secret })._config.apiKey === secret);

  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  ed._fillKey = async () => {};
  ed.setConfig({ language: 'hu', apiKey: secret });
  const note = ed._el('apiKeyDeprecatedWrap');
  const shownWithKey = !!note && note.style.display !== 'none' && /elavult/i.test(ed._el('apiKeyDeprecated').textContent);
  ed.setConfig({ language: 'hu' });
  const hiddenWithout = !!note && note.style.display === 'none';
  ed.setConfig({ language: 'en', apiKey: secret });
  const english = /deprecated/i.test(ed._el('apiKeyDeprecated').textContent);
  check('the editor marks a card-level key as deprecated (hu/en), and only when the card has one',
    shownWithKey && hiddenWithout && english && !ed._el('apiKeyDeprecated').textContent.includes(secret),
    JSON.stringify({ shownWithKey, hiddenWithout, english }));
  /* The note follows the YAML key in every mode: a Volán or city card does
     not use the key, but its config still holds it. */
  const shown = () => note.style.display !== 'none' && !!ed._el('apiKeyDeprecated').textContent;
  ed.setConfig({ language: 'hu', apiKey: secret });
  const bkkShown = shown();
  ed.setConfig({ language: 'hu', apiKey: secret, volan: true });
  const volanShown = shown();
  ed.setConfig({ language: 'hu', volan: true });
  const volanHidden = !shown();
  ed.setConfig({ language: 'hu', apiKey: secret, helyi: true, city: 'gyor' });
  const helyiShown = shown();
  ed.setConfig({ language: 'hu', helyi: true, city: 'gyor' });
  const helyiHidden = !shown();
  ed.remove();
  const opened = (cfg) => {
    const e = document.createElement('hungarian-transport-card-editor');
    document.body.appendChild(e);
    e.hass = { states: {}, language: 'hu' };
    e._probeKey = () => {};
    e._loadDests = async () => {};
    e._fillKey = async () => {};
    e.setConfig(cfg);
    const w = e._el('apiKeyDeprecatedWrap');
    const on = !!w && w.style.display !== 'none' && /elavult/i.test(e._el('apiKeyDeprecated').textContent);
    e.remove();
    return on;
  };
  const opensVolan = opened({ language: 'hu', volan: true, apiKey: secret });
  const opensHelyi = opened({ language: 'hu', helyi: true, city: 'gyor', apiKey: secret });
  const opensVolanBare = opened({ language: 'hu', volan: true });
  check('the deprecation note follows the card key across BKK, Volán and Local modes',
    bkkShown && volanShown && volanHidden && helyiShown && helyiHidden
    && opensVolan && opensHelyi && !opensVolanBare,
    JSON.stringify({ bkkShown, volanShown, volanHidden, helyiShown, helyiHidden, opensVolan, opensHelyi, opensVolanBare }));
  /* A borrowed key is not this card's to remove: the "ok" line says where it
     comes from, and the card's own note stays off. */
  const eb = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(eb);
  eb.hass = { states: {}, language: 'en' };
  eb._probeKey = () => {};
  eb._loadDests = async () => {};
  eb._fillKey = async () => {};
  eb.setConfig({ language: 'en' });
  eb._borrowedKey = secret;
  eb._keyOk = true;
  eb._syncApiKey();
  const okText = eb._el('apiKeyOkText').textContent;
  check('a borrowed key folds the field with its own line and no card-key note',
    eb._el('apiKeyWrap').style.display === 'none' && /another card/i.test(okText) && !okText.includes(secret)
    && eb._el('apiKeyDeprecatedWrap').style.display === 'none' && eb._el('apiKey').value === '',
    JSON.stringify({ okText }));
  eb.remove();
}

/* ---- Planner A–Z review (2026-10-21) ---- */

/* City → rail: every route of a stop pair is kept, and the ride (and the
   station) is chosen by when it gets there, not by ride time alone. */
{
  const keep = {};
  ['cityIndex', 'railAfterCity'].forEach((k) => { keep[k] = Lib[k]; });
  const realNow = Date.now;
  const cityOf = (names, trips) => {
    const stops = names.map((n, i) => ({
      i, id: 'miskolc:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 48.1 + i * 0.01, lon: 20.78,
    }));
    return {
      stops, t: trips, c: [['1111111', '20200101', '20991231']], a: [], r: [],
      ops: [{ i: 0, id: 'miskolc', name: 'Miskolc — helyi' }],
      byId: new Map(stops.map((s) => [s.id, s])), byNum: new Map(), byKey: new Map(),
    };
  };
  /* X: 5 minutes, hourly 06:00–19:00. Y: 10 minutes, every 10 minutes. */
  const pairTrips = () => {
    const t = [];
    for (let h = 6; h <= 19; h++) t.push([0, 'X', [0, 1], [h * 60, h * 60 + 5], 3]);
    for (let m = 0; m < 24 * 60; m += 10) t.push([0, 'Y', [0, 1], [m, m + 10], 3]);
    return t;
  };
  const at = (h, min) => Date.UTC(2026, 9, 20, h - 2, min);
  const rideAt = async (h, min) => {
    Date.now = () => at(h, min);
    Lib.cityIndex = async () => cityOf(['Búza tér', 'Tiszai pályaudvar'], pairTrips());
    try {
      const j = await Lib.cityRailJourney('', null, { id: 'miskolc:0', name: 'Búza tér' }, { id: '', name: 'Tiszai pályaudvar' });
      const ride = j && j.legs.find((l) => !l.walk);
      return ride ? [ride.label, ride.startMs, ride.endMs] : null;
    } finally {
      Date.now = realNow;
    }
  };
  const morning = await rideAt(8, 17);
  const evening = await rideAt(20, 17);
  const onTheHour = await rideAt(8, 57);
  check('a city ride takes the line that arrives first, not the fastest that runs at dawn or ended for the day',
    !!morning && morning[0] === 'Y' && morning[1] === at(8, 20) && morning[2] === at(8, 30)
    && !!evening && evening[0] === 'Y' && evening[1] === at(20, 20)
    && !!onTheHour && onTheHour[0] === 'X' && onTheHour[2] === at(9, 5),
    JSON.stringify({ morning, evening, onTheHour }));

  /* Tiszai: 5 minutes, once at 06:00. Gömöri: 10 minutes, every 10. */
  const hubTrips = [[0, 'X', [0, 1], [360, 365], 3]];
  for (let m = 0; m < 24 * 60; m += 10) hubTrips.push([0, 'Y', [0, 2], [m, m + 10], 3]);
  const asked = [];
  /* A train from each station, by when the city gets there, or per station. */
  let trainFrom = (_hub, ready) => ({ dep: ready + 600, arr: ready + 4200 });
  Lib.railAfterCity = async (_k, _h, _city, path, dest, ready) => {
    asked.push(path.hubName);
    const tr = trainFrom(path.hubName, ready);
    if (!tr) return null;
    return {
      walk: { walk: true, stationWalk: true, minutes: 6, from: path.hubName, to: 'station', startMs: ready * 1000, endMs: (ready + 360) * 1000 },
      train: { walk: false, label: 'IC', minutes: 60, from: 'station', to: dest.name, startMs: tr.dep * 1000, endMs: tr.arr * 1000 },
      ready: tr.arr,
    };
  };
  const viaHubAt = async (trips) => {
    Lib.cityIndex = async () => cityOf(['Búza tér', 'Tiszai pályaudvar', 'Gömöri pályaudvar'], trips);
    Date.now = () => at(8, 17);
    try {
      return await Lib.cityRailJourney('', null, { id: 'miskolc:0', name: 'Búza tér' }, { id: 'BKK_005501024', name: 'Kelenföld' });
    } finally {
      Date.now = realNow;
    }
  };
  const viaHub = await viaHubAt(hubTrips);
  const hubRide = viaHub && viaHub.legs.find((l) => !l.walk);
  const hubWalk = viaHub && viaHub.legs.find((l) => l.stationWalk);
  check('the station is the one the city reaches first, waits included',
    !!hubWalk && hubWalk.from === 'Gömöri pályaudvar' && !!hubRide && hubRide.label === 'Y' && hubRide.startMs === at(8, 20),
    JSON.stringify({ from: hubWalk && hubWalk.from, ride: hubRide && [hubRide.label, hubRide.startMs] }));

  /* Tiszai now hourly (there 09:05), Gömöri at 08:30: the train decides. */
  const hourly = [];
  for (let h = 6; h <= 19; h++) hourly.push([0, 'X', [0, 1], [h * 60, h * 60 + 5], 3]);
  for (let m = 0; m < 24 * 60; m += 10) hourly.push([0, 'Y', [0, 2], [m, m + 10], 3]);
  const sec = (h, min) => at(h, min) / 1000;
  trainFrom = (hub) => (hub === 'Gömöri pályaudvar'
    ? { dep: sec(10, 0), arr: sec(12, 0) }
    : { dep: sec(9, 20), arr: sec(11, 0) });
  const laterTrain = await viaHubAt(hourly);
  trainFrom = (hub) => (hub === 'Gömöri pályaudvar' ? null : { dep: sec(9, 20), arr: sec(11, 0) });
  asked.length = 0;
  const noTrain = await viaHubAt(hourly);
  const fromOf = (j) => { const w = j && j.legs.find((l) => l.stationWalk); return w ? w.from : null; };
  check('the station is chosen by the arrival at the destination, and one without a train is not the end',
    fromOf(laterTrain) === 'Tiszai pályaudvar' && laterTrain.endMs === at(11, 0)
    && fromOf(noTrain) === 'Tiszai pályaudvar' && noTrain.endMs === at(11, 0)
    && asked.length === 2,
    JSON.stringify({ later: fromOf(laterTrain), none: fromOf(noTrain), asked }));
  Object.assign(Lib, keep);
}

/* City → rail on the map: the walk to the station and the train start at
   the station's own place, so both are drawn. */
{
  const keep = {};
  ['cityIndex', 'searchStops', 'elviraResolveCode', 'elviraBetween', 'gtfsShape', 'hasFutar'].forEach((k) => { keep[k] = Lib[k]; });
  const names = ['Búza tér', 'Tiszai pályaudvar'];
  const stops = names.map((n, i) => ({
    i, id: 'miskolc:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 48.10 + i * 0.01, lon: 20.78 + i * 0.02,
  }));
  const t = [];
  for (let m = 0; m < 24 * 60; m += 10) t.push([0, 'Y', [0, 1], [m, m + 10], 3]);
  const idx = {
    stops, t, c: [['1111111', '20200101', '20991231']], a: [], r: [],
    ops: [{ i: 0, id: 'miskolc', name: 'Miskolc — helyi' }],
    byId: new Map(stops.map((s) => [s.id, s])), byNum: new Map(), byKey: new Map(),
  };
  Lib.hasFutar = () => true;
  Lib.cityIndex = async () => idx;
  Lib.gtfsShape = async () => [];
  Lib.searchStops = async () => [{ id: 'BKK_005510009', name: 'Miskolc-Tiszai', lat: 48.1035, lon: 20.8104 }];
  Lib.elviraResolveCode = async () => '005501024';
  Lib.elviraBetween = async () => [{
    label: 'IC', trainNumber: '563', dep: Math.floor(Date.now() / 1000) + 3600, travel: 140, head: 'Kelenföld',
  }];
  const j = await Lib.cityRailJourney('k', { states: {} },
    { id: 'miskolc:0', name: 'Búza tér', lat: 48.10, lon: 20.78 },
    { id: 'BKK_005501024', name: 'Kelenföld', lat: 47.4643, lon: 19.0206 });
  if (j) await Lib.fillJourneyShapes(j);
  const segs = j ? Lib.journeyMapSegments(j, {}, {}) : [];
  const walk = j && j.legs.find((l) => l.stationWalk);
  const train = j && j.legs.find((l) => l.label === 'IC');
  check('the map of a city → rail journey draws the walk to the station and the train',
    !!walk && walk.toLat === 48.1035 && !!train && train.fromLon === 20.8104
    && segs.some((sg) => sg.walk) && segs.some((sg) => sg.label === 'IC') && segs.some((sg) => sg.label === 'Y'),
    JSON.stringify(segs.map((sg) => (sg.walk ? 'walk' : sg.label))));
  Object.assign(Lib, keep);
}

/* A coach station leads a town's stops: fold() turns its hyphen into a space. */
{
  const mk = (names) => ({
    stops: names.map((n, i) => ({ i, id: 's' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n) })),
    byNum: new Map(), byKey: new Map(), byId: new Map(),
  });
  const listed = Lib.gtfsSearchStops(mk(['Győr, Szent István út', 'Győr, Árpád út', 'Győr, Adyváros', 'Győr, autóbusz-állomás']), 'Győr', null)
    .map((s) => s.name);
  const matched = Lib.volanMatchStop(mk(['Gyor alma', 'Győr, autóbusz-állomás']), { id: '', name: 'Győr' }, null);
  check('a town’s coach station comes first in the stop search and the name match',
    listed[0] === 'Győr, autóbusz-állomás' && !!matched && matched.name === 'Győr, autóbusz-állomás',
    JSON.stringify({ listed, matched: matched && matched.name }));
  const closed = Lib.gtfsSearchStops(mk(['Győr, Adyváros', 'Győr, autóbuszállomás']), 'Győr', null).map((s) => s.name);
  check('a coach station written closed ("autóbuszállomás") leads too',
    closed[0] === 'Győr, autóbuszállomás' && Lib.isCoachStationFold(Lib.fold('Győr autóbusz-állomás')), JSON.stringify(closed));
}

/* Stop history: a pick written while the server could not be read is not
   kept as this connection's list, and the planner reads again on the next
   hass instead of keeping the browser copy. */
{
  const store = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
  });
  let up = false;
  let reads = 0;
  let server = [{ id: 'S1', name: 'Other device stop', n: 9, t: Date.now() }];
  const callWS = async (m) => {
    if (m.type === 'frontend/get_user_data') {
      reads += 1;
      if (!up) throw new Error('ws down');
      return { value: server };
    }
    if (m.type === 'frontend/set_user_data') server = m.value;
    return null;
  };
  const hass = { states: {}, language: 'hu', user: { id: 'u13' }, connection: { az: 1 }, callWS };
  Lib._stopHistory = null;
  await Lib.recordStop(hass, { id: 'L1', name: 'Local pick' });
  up = true;
  const shown = (await Lib.loadStopHistory(hass)).map((e) => e.id);
  await Lib._stopHistoryWrite;
  check('after a pick made offline, the server’s list is read once the websocket is back',
    shown.includes('S1'), JSON.stringify(shown));
  check('a pick made offline stays in the suggestions and reaches the server once it can be read',
    shown.includes('L1') && server.some((e) => e.id === 'L1') && server.some((e) => e.id === 'S1'),
    JSON.stringify({ shown, server: server.map((e) => e.id) }));

  up = false;
  reads = 0;
  Lib._stopHistory = null;
  store.clear();
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  document.body.appendChild(pc);
  pc.hass = hass;
  pc.setConfig({ language: 'hu' });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const failedReads = reads;
  up = true;
  /* hass changes many times a minute: no new read, nor a repaint, before
     the minute is up. */
  let paints = 0;
  const paint = pc._paintFav.bind(pc);
  pc._paintFav = () => { paints += 1; paint(); };
  for (let i = 0; i < 5; i++) pc.hass = Object.assign({}, hass);
  await new Promise((r) => setTimeout(r, 0));
  const readsInMinute = reads;
  const paintsInMinute = paints;
  const realNow = Date.now;
  Date.now = () => realNow() + 61 * 1000;
  try {
    pc.hass = Object.assign({}, hass);
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    Date.now = realNow;
  }
  const chips = Array.from(pc.shadowRoot.querySelectorAll('#pfav .suggest-stop')).map((b) => b.textContent);
  check('after a failed history read the planner waits a minute, then asks again',
    readsInMinute === failedReads && paintsInMinute === 0 &&
    failedReads >= 1 && reads > failedReads && chips.some((c) => c.includes('Other device stop')),
    JSON.stringify({ failedReads, readsInMinute, paintsInMinute, reads, chips }));
  pc.remove();
  Lib._stopHistory = null;
  delete globalThis.localStorage;
}

/* City → rail: a station farther away is still a candidate when the
   nearest one has many more ways in; and a ride is chosen by arrival
   across service days. */
{
  const keep = {};
  ['cityIndex', 'railAfterCity'].forEach((k) => { keep[k] = Lib[k]; });
  const realNow = Date.now;
  /* 0 origin, 1 Tiszai, 2 Gömöri, 3..32 stops between origin and Tiszai:
     31 two-minute ways to Tiszai, one eleven-minute ride to Gömöri. */
  const names = ['Babits Mihály utca', 'Tiszai pályaudvar', 'Gömöri pályaudvar'];
  for (let i = 0; i < 30; i++) names.push('Köztes ' + i);
  const stops = names.map((n, i) => ({
    i, id: 'miskolc:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 48.1 + i * 0.001, lon: 20.78,
  }));
  const t = [];
  for (let r = 0; r < 30; r++) {
    for (let h = 5; h < 23; h++) t.push([0, 'T' + r, [0, 3 + r, 1], [h * 60 + r, h * 60 + r + 1, h * 60 + r + 2], 3]);
  }
  for (let m = 0; m < 24 * 60; m += 10) t.push([0, 'G', [0, 2], [m, m + 11], 3]);
  const idx = {
    stops, t, c: [['1111111', '20200101', '20991231']], a: [], r: [],
    ops: [{ i: 0, id: 'miskolc', name: 'Miskolc — helyi' }],
    byId: new Map(stops.map((s) => [s.id, s])), byNum: new Map(), byKey: new Map(),
  };
  const asked = [];
  Lib.cityIndex = async () => idx;
  Lib.railAfterCity = async (_k, _h, _c, path) => { asked.push(path.hubName); return null; };
  Date.now = () => Date.UTC(2026, 9, 20, 6, 17);
  let j;
  try {
    j = await Lib.cityRailJourney('', null, { id: 'miskolc:0', name: 'Babits Mihály utca' }, { id: 'BKK_005510124', name: 'Miskolc-Gömöri' });
  } finally {
    Date.now = realNow;
  }
  const hubs = new Set(Lib.cityHubPaths(idx, stops[0]).map((p) => p.hubName));
  check('a farther station is not crowded out by the nearest one’s many ways in, and is the trip when it is the destination',
    hubs.has('Gömöri pályaudvar') && !!j && j.legs.length === 1 && j.legs[0].label === 'G' && asked.length === 0,
    JSON.stringify({ hubs: Array.from(hubs), legs: j && j.legs.map((l) => l.label || 'walk'), asked }));
  Object.assign(Lib, keep);

  /* 23:45: route A leaves 23:50 and arrives 00:40; route B leaves 00:05
     tomorrow and arrives 00:15. */
  Date.now = () => Date.UTC(2026, 9, 20, 21, 45);
  const rideIdx = { c: [['1111111', '20200101', '20301231']], t: [[0, 'A', [0, 1], [1430, 1480]], [0, 'B', [0, 1], [5, 15]]] };
  let ride;
  try {
    ride = Lib.nextCityRide(rideIdx, 0, 1, ['A', 'B'], Math.floor(Date.now() / 1000));
  } finally {
    Date.now = realNow;
  }
  check('a ride arriving after midnight loses to tomorrow’s earlier arrival on another route',
    !!ride && ride.trip === rideIdx.t[1] && ride.arr === Date.UTC(2026, 9, 20, 22, 15) / 1000,
    JSON.stringify(ride && { route: ride.trip[1], arr: ride.arr }));
}

/* ---- A–Z review: hop card and editor ---- */

/* A through trip (the destination mid-route, headsign elsewhere) is found
   only by the through-check: the first load keeps its spinner until it
   answers, and a poll keeps the trips it already checked while a new
   trip's details are still on the way. */
{
  const keepFetch = Lib.fetch;
  const keepFill = Lib.fillTransitousIfEmpty;
  const t0 = Math.floor(Date.now() / 1000);
  let trips = ['AZTHRU_T1', 'AZTHRU_T2'];
  const held = new Set(trips);
  const gates = new Map();
  const stops = {
    AZTHRU_O: { id: 'AZTHRU_O', name: 'Azorigo' },
    AZTHRU_D: { id: 'AZTHRU_D', name: 'Azcelpont' },
    AZTHRU_E: { id: 'AZTHRU_E', name: 'Azvegallomas' },
  };
  Lib._boards.clear();
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.fetch = async (key, path, params) => {
    if (path === 'arrivals-and-departures-for-stop.json') {
      return { data: {
        entry: { stopTimes: trips.map((t, i) => ({ tripId: t, stopId: 'AZTHRU_O', departureTime: t0 + 300 + i * 600 })) },
        references: {
          trips: Object.fromEntries(trips.map((t) => [t, { routeId: 'AZTHRU_R', tripHeadsign: 'Azvegallomas' }])),
          routes: { AZTHRU_R: { id: 'AZTHRU_R', shortName: '7', type: 'BUS' } },
          stops: { AZTHRU_O: stops.AZTHRU_O },
        },
      } };
    }
    if (path === 'trip-details.json') {
      if (held.has(params.tripId)) await new Promise((r) => gates.set(params.tripId, r));
      const i = trips.indexOf(params.tripId);
      return { data: { entry: { stopTimes: [
        { stopId: 'AZTHRU_O', departureTime: t0 + 300 + i * 600 },
        { stopId: 'AZTHRU_D', arrivalTime: t0 + 900 + i * 600 },
        { stopId: 'AZTHRU_E', arrivalTime: t0 + 1500 + i * 600 },
      ] }, references: { stops } } };
    }
    return { data: { entry: {}, references: {} } };
  };
  const release = () => {
    gates.forEach((r, id) => { held.delete(id); r(); });
    gates.clear();
  };
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'AZTHRU_O', stopName: 'Azorigo', destKey: 'azcelpont', destName: 'Azcelpont', apiKey: 'k' });
  await settleTicks(30);
  const body = () => c.shadowRoot.querySelector('.body').textContent;
  const firstText = body();
  const firstLoading = c._loading && !/Nincs közelgő/.test(firstText);
  release();
  await settleTicks(30);
  const loaded = c._rows.length;
  trips = ['AZTHRU_T1', 'AZTHRU_T2', 'AZTHRU_T3'];
  held.add('AZTHRU_T3');
  Lib._boards.clear();
  const poll = c._pollRun && c._pollRun.run();
  await settleTicks(30);
  const midPoll = c._rows.length;
  const waited = gates.has('AZTHRU_T3');
  release();
  await poll;
  await settleTicks(30);
  const afterPoll = c._rows.length;
  check('a through-trip board keeps loading, then keeps its rows while a poll checks a new trip',
    firstLoading && loaded === 2 && waited && midPoll === 2 && afterPoll === 3,
    JSON.stringify({ firstText: firstText.trim(), loaded, waited, midPoll, afterPoll }));
  c.remove();
  release();
  Lib.fetch = keepFetch;
  Lib.fillTransitousIfEmpty = keepFill;
  Lib._boards.clear();
}

/* Saved routeIds are what ran in the next three hours when the card was set
   up: at night only the night bus. They must not hide a day line that
   leaves from another pole of the same hub. */
{
  const keepFetch = Lib.fetch;
  const t0 = Math.floor(Date.now() / 1000);
  const S = {
    AZP_P1: { id: 'AZP_P1', name: 'Azhub', parentStationId: 'AZP_CSF1' },
    AZP_P2: { id: 'AZP_P2', name: 'Azhub', parentStationId: 'AZP_CSF1' },
    AZP_D: { id: 'AZP_D', name: 'Azpcel' },
  };
  Lib._boards.clear();
  Lib.fetch = async (key, path, params) => {
    if (path === 'route-details.json') {
      const pole = params.routeId === 'AZP_N' ? 'AZP_P1' : 'AZP_P2';
      return { data: { entry: { variants: [{ name: 'v', stopIds: [pole, 'AZP_D'] }] }, references: { stops: S } } };
    }
    if (path === 'arrivals-and-departures-for-stop.json') {
      const day = params.stopId !== 'AZP_P1';
      const tid = day ? 'AZP_DAY1' : 'AZP_NIGHT1';
      return { data: {
        entry: { stopTimes: [{ tripId: tid, stopId: params.stopId === 'AZP_CSF1' ? 'AZP_P2' : params.stopId, departureTime: t0 + 600 }] },
        references: {
          trips: { [tid]: { routeId: day ? 'AZP_D7' : 'AZP_N', tripHeadsign: 'Azpcel' } },
          routes: { AZP_D7: { id: 'AZP_D7', shortName: '7', type: 'BUS' }, AZP_N: { id: 'AZP_N', shortName: '907', type: 'BUS' } },
          stops: S,
        },
      } };
    }
    return { data: {} };
  };
  const rows = await Lib.departures('k', 'AZP_CSF1', ['AZP_N'], { key: 'azpcel', name: 'Azpcel' },
    { originName: 'Azhub', mode: 'bkk', minutesAfter: 180, deferGps: true });
  const labels = rows.map((r) => r.label).sort();
  check('saved night routeIds do not hide a day line from another pole',
    JSON.stringify(labels) === '["7","907"]', JSON.stringify(labels));
  Lib.fetch = keepFetch;
  Lib._boards.clear();
}

/* An "A → B" title the editor wrote follows the next pair after a mode or
   city change, which clears the pair first. A typed title stays. */
{
  const keepReach = Lib.reachableDests;
  Lib.reachableDests = async () => ({ dests: [], destRoutes: {} });
  const mk = () => {
    const ed = document.createElement('hungarian-transport-card-editor');
    document.body.appendChild(ed);
    ed._probeKey = () => {};
    ed.hass = { states: {}, language: 'hu', services: { hungarian_transport: { futar: {} } } };
    ed.addEventListener('config-changed', (e) => { ed._last = e.detail.config; });
    return ed;
  };
  const pickPair = (ed) => {
    ed._pickStop({ id: 'volan_az1', name: 'Azkelenföld' }, 'origin');
    ed.setConfig(ed._last);
    ed._pickStop({ key: 'azfehervar', name: 'Azfehérvár' }, 'dest');
    return ed._last.name;
  };
  const a = mk();
  a.setConfig({ language: 'hu', name: 'Keleti → Szolnok', stopId: 'BKK_A', stopName: 'Keleti', destKey: 'szolnok', destName: 'Szolnok' });
  a._el('volan').checked = true;
  a._onModeToggle('volan');
  a.setConfig(a._last);
  const afterMode = pickPair(a);
  a.remove();
  const b = mk();
  b.setConfig({ language: 'hu', helyi: true, city: 'gyor', name: 'Győr A → Győr B', stopId: 'gyor_1', stopName: 'Győr A', destKey: 'gyor b', destName: 'Győr B' });
  const sel = b._el('city');
  const opt = document.createElement('option');
  opt.value = 'pecs';
  sel.appendChild(opt);
  sel.value = 'pecs';
  sel.dispatchEvent(new Event('change'));
  b.setConfig(b._last);
  const afterCity = pickPair(b);
  b.remove();
  const c = mk();
  c.setConfig({ language: 'hu', name: 'Munkába', stopId: 'BKK_A', stopName: 'Keleti', destKey: 'szolnok', destName: 'Szolnok' });
  c._el('volan').checked = true;
  c._onModeToggle('volan');
  c.setConfig(c._last);
  const typed = pickPair(c);
  c.remove();
  check('an auto title follows the next pair after a mode or city change; a typed one stays',
    afterMode === 'Azkelenföld → Azfehérvár' && afterCity === 'Azkelenföld → Azfehérvár' && typed === 'Munkába',
    JSON.stringify({ afterMode, afterCity, typed }));
  Lib.reachableDests = keepReach;
}

/* The planner editor (mode "all") lists destinations without any BKK key:
   the GTFS indexes need none, as the planner card itself allows. */
{
  const keepReach = Lib.reachableDests;
  let calls = 0;
  Lib.reachableDests = async () => { calls += 1; return { dests: [{ key: 'azfehervar', name: 'Azfehérvár' }], destRoutes: {} }; };
  const ed = document.createElement('hungarian-transit-stop-card-plan-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed._fillKey = async () => {};
  ed.hass = { states: {}, language: 'hu', services: {} };
  ed.setConfig({ language: 'hu', stopId: 'volan_az2', stopName: 'Azkelenföld' });
  await settleTicks();
  const names = [...ed.querySelectorAll('#destHits button.hit')].map((x) => x.textContent);
  check('the planner editor loads destinations without a BKK key',
    calls === 1 && JSON.stringify(names) === '["Azfehérvár"]', JSON.stringify({ calls, names }));
  ed.remove();
  Lib.reachableDests = keepReach;
}

/* BKK and MÁV searches with no key anywhere say the key is missing, not
   "no stops found" for a search that never ran. */
{
  const keepFetch = Lib.fetch;
  let calls = 0;
  Lib.fetch = async () => { calls += 1; throw new Error('no request expected'); };
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = () => {};
  ed._fillKey = async () => {};
  ed.hass = { states: {}, language: 'hu', services: {} };
  const said = async (cfg) => {
    ed.setConfig(Object.assign({ language: 'hu' }, cfg));
    await ed._search('Keleti', 'origin');
    return { hits: ed.querySelector('#hits').textContent, err: ed.querySelector('#err').textContent };
  };
  const bkk = await said({});
  const mav = await said({ mav: true });
  const missing = (r) => /Nincs BKK API kulcs/.test(r.err) && !/Nincs BKK megálló|Nincs/.test(r.hits);
  check('a keyless BKK or MÁV search names the missing key and sends nothing',
    missing(bkk) && missing(mav) && calls === 0, JSON.stringify({ bkk, mav, calls }));
  ed.remove();
  Lib.fetch = keepFetch;
}

/* A sibling pole listed before the stop itself still leaves the stop's own
   board first, so a capped board discovers the rest of the hub. */
{
  const keepFetch = Lib.fetch;
  const t0 = Math.floor(Date.now() / 1000);
  const S = {
    AZQ_P0: { id: 'AZQ_P0', name: 'Azqhub' },
    AZQ_P1: { id: 'AZQ_P1', name: 'Azqhub' },
    AZQ_P3: { id: 'AZQ_P3', name: 'Azqhub' },
    AZQ_D: { id: 'AZQ_D', name: 'Azqcel' },
  };
  const firstPole = { AZQ_A: 'AZQ_P0', AZQ_B: 'AZQ_P1', AZQ_D7: 'AZQ_P3' };
  const boardTrip = {
    AZQ_P0: ['AZQ_TA', 'AZQ_A', 600],
    AZQ_P1: ['AZQ_T7A', 'AZQ_D7', 700],
    AZQ_P3: ['AZQ_T7B', 'AZQ_D7', 1300],
  };
  const boards = [];
  Lib._boards.clear();
  Lib.fetch = async (key, path, params) => {
    if (path === 'route-details.json') {
      return { data: { entry: { variants: [{ name: 'v', stopIds: [firstPole[params.routeId], 'AZQ_D'] }] }, references: { stops: S } } };
    }
    if (path === 'arrivals-and-departures-for-stop.json') {
      boards.push(params.stopId);
      const [tid, rid, at] = boardTrip[params.stopId] || [];
      return { data: {
        entry: { stopTimes: tid ? [{ tripId: tid, stopId: params.stopId, departureTime: t0 + at }] : [] },
        references: {
          trips: tid ? { [tid]: { routeId: rid, tripHeadsign: 'Azqcel' } } : {},
          routes: { AZQ_A: { id: 'AZQ_A', shortName: '1', type: 'BUS' }, AZQ_D7: { id: 'AZQ_D7', shortName: '7', type: 'BUS' } },
          stops: S,
        },
      } };
    }
    return { data: {} };
  };
  const rows = await Lib.departures('k', 'AZQ_P1', ['AZQ_A', 'AZQ_B'], { key: 'azqcel', name: 'Azqcel' },
    { originName: 'Azqhub', mode: 'bkk', minutesAfter: 180, deferGps: true });
  check('the stop\'s own board goes first even when a sibling pole is listed before it',
    boards[0] === 'AZQ_P1' && boards.includes('AZQ_P3') && rows.length === 3,
    JSON.stringify({ boards, rows: rows.map((r) => r.label) }));
  Lib.fetch = keepFetch;
  Lib._boards.clear();
}

/* "all" lists destinations at once without a key; a sibling card's key
   borrowed afterwards reloads the list with the BKK destinations. */
{
  const keepDash = Lib.apiKeyFromDashboard;
  const keepReach = Lib.reachableDests;
  let giveKey = null;
  Lib.apiKeyFromDashboard = () => new Promise((r) => { giveKey = r; });
  const keys = [];
  Lib.reachableDests = async (apiKey) => {
    keys.push(apiKey || '');
    return apiKey
      ? { dests: [{ key: 'azkeleti', name: 'Azkeleti' }, { key: 'azfehervar', name: 'Azfehérvár' }], destRoutes: {} }
      : { dests: [{ key: 'azfehervar', name: 'Azfehérvár' }], destRoutes: {} };
  };
  const ed = document.createElement('hungarian-transit-stop-card-plan-editor');
  document.body.appendChild(ed);
  ed._probeKey = async () => {};
  ed.hass = { states: {}, language: 'hu', services: {} };
  ed.setConfig({ language: 'hu', stopId: 'volan_az3', stopName: 'Azkelenföld' });
  await settleTicks();
  const before = [...ed.querySelectorAll('#destHits button.hit')].map((x) => x.textContent);
  giveKey('azsibling');
  await settleTicks();
  const after = [...ed.querySelectorAll('#destHits button.hit')].map((x) => x.textContent);
  check('a key borrowed after a keyless destination load reloads the list',
    JSON.stringify(before) === '["Azfehérvár"]' && keys.length === 2 && keys[1] === 'azsibling'
    && JSON.stringify(after) === '["Azkeleti","Azfehérvár"]',
    JSON.stringify({ before, after, calls: keys.length, last: keys[keys.length - 1] === 'azsibling' }));
  ed.remove();
  Lib.apiKeyFromDashboard = keepDash;
  Lib.reachableDests = keepReach;
}

/* A failed run's banner and Transitous rows go once FUTÁR answers again,
   even while the through-check is still running. */
{
  const keepFetch = Lib.fetch;
  const keepFill = Lib.fillTransitousIfEmpty;
  const t0 = Math.floor(Date.now() / 1000);
  let down = true;
  const gates = [];
  const stops = { AZE_O: { id: 'AZE_O', name: 'Azeorigo' }, AZE_D: { id: 'AZE_D', name: 'Azecel' }, AZE_E: { id: 'AZE_E', name: 'Azevege' } };
  Lib._boards.clear();
  Lib.fillTransitousIfEmpty = async (rows) => (rows.length ? rows : [{
    tripId: 'motis:aze1', label: 'T9', dep: t0 + 400, sched: t0 + 400, head: 'Azecel', vehicle: 'bus', rawType: 'BUS',
    color: '#000000', text: '#ffffff', travel: null,
  }]);
  Lib.fetch = async (key, path) => {
    if (path === 'arrivals-and-departures-for-stop.json') {
      if (down) throw new Error('HTTP 503');
      return { data: {
        entry: { stopTimes: [{ tripId: 'AZE_T1', stopId: 'AZE_O', departureTime: t0 + 300 }] },
        references: {
          trips: { AZE_T1: { routeId: 'AZE_R', tripHeadsign: 'Azevege' } },
          routes: { AZE_R: { id: 'AZE_R', shortName: '5', type: 'BUS' } },
          stops: { AZE_O: stops.AZE_O },
        },
      } };
    }
    if (path === 'trip-details.json') {
      await new Promise((r) => gates.push(r));
      return { data: { entry: { stopTimes: [
        { stopId: 'AZE_O', departureTime: t0 + 300 },
        { stopId: 'AZE_D', arrivalTime: t0 + 900 },
        { stopId: 'AZE_E', arrivalTime: t0 + 1500 },
      ] }, references: { stops } } };
    }
    return { data: { entry: {}, references: {} } };
  };
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'AZE_O', stopName: 'Azeorigo', destKey: 'azecel', destName: 'Azecel', apiKey: 'k' });
  await settleTicks(30);
  const broke = !!c._err && c._rows.length === 1;
  down = false;
  Lib._boards.clear();
  const poll = c._pollRun ? c._pollRun.run() : c._reload();
  await settleTicks(30);
  const mid = { err: !!c._err, rows: c._rows.length, loading: c._loading, waiting: gates.length };
  gates.splice(0).forEach((r) => r());
  await poll;
  await settleTicks(30);
  const end = c._rows.map((r) => r.label);
  check('a recovered board drops the old error and Transitous rows while the through-check runs',
    broke && !mid.err && mid.rows === 0 && mid.loading && mid.waiting === 1 && JSON.stringify(end) === '["5"]',
    JSON.stringify({ broke, mid, end }));
  c.remove();
  gates.splice(0).forEach((r) => r());
  Lib.fetch = keepFetch;
  Lib.fillTransitousIfEmpty = keepFill;
  Lib._boards.clear();
}

/* FUTÁR rows a failed poll kept under its banner stay when the next poll
   recovers and its through-check is still running; only the banner goes. */
{
  const keepFetch = Lib.fetch;
  const keepFill = Lib.fillTransitousIfEmpty;
  const t0 = Math.floor(Date.now() / 1000);
  let phase = 'ok';
  let trip = 'AZK_T1';
  const gates = [];
  const stops = { AZK_O: { id: 'AZK_O', name: 'Azkorigo' }, AZK_D: { id: 'AZK_D', name: 'Azkcel' }, AZK_E: { id: 'AZK_E', name: 'Azkvege' } };
  Lib._boards.clear();
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.fetch = async (key, path, params) => {
    if (path === 'arrivals-and-departures-for-stop.json') {
      if (phase === 'down') throw new Error('HTTP 503');
      return { data: {
        entry: { stopTimes: [{ tripId: trip, stopId: 'AZK_O', departureTime: t0 + 300 }] },
        references: {
          trips: { [trip]: { routeId: 'AZK_R', tripHeadsign: 'Azkvege' } },
          routes: { AZK_R: { id: 'AZK_R', shortName: '5', type: 'BUS' } },
          stops: { AZK_O: stops.AZK_O },
        },
      } };
    }
    if (path === 'trip-details.json') {
      if (params.tripId !== 'AZK_T1') await new Promise((r) => gates.push(r));
      return { data: { entry: { stopTimes: [
        { stopId: 'AZK_O', departureTime: t0 + 300 },
        { stopId: 'AZK_D', arrivalTime: t0 + 900 },
        { stopId: 'AZK_E', arrivalTime: t0 + 1500 },
      ] }, references: { stops } } };
    }
    return { data: { entry: {}, references: {} } };
  };
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'AZK_O', stopName: 'Azkorigo', destKey: 'azkcel', destName: 'Azkcel', apiKey: 'k' });
  await settleTicks(30);
  const ids = () => c._rawRows.map((r) => r.tripId);
  const first = ids();
  phase = 'down';
  Lib._boards.clear();
  await c._pollRun.run();
  await settleTicks(30);
  const kept = { err: !!c._err, ids: ids() };
  phase = 'ok';
  trip = 'AZK_T2';
  Lib._boards.clear();
  const poll = c._pollRun.run();
  await settleTicks(30);
  const mid = { err: !!c._err, ids: ids(), loading: c._loading, waiting: gates.length };
  gates.splice(0).forEach((r) => r());
  await poll;
  await settleTicks(30);
  const end = ids();
  check('FUTÁR rows kept under a banner stay while the recovered poll checks a new trip',
    JSON.stringify(first) === '["AZK_T1"]' && kept.err && JSON.stringify(kept.ids) === '["AZK_T1"]'
    && !mid.err && JSON.stringify(mid.ids) === '["AZK_T1"]' && !mid.loading && mid.waiting === 1
    && JSON.stringify(end) === '["AZK_T2"]',
    JSON.stringify({ first, kept, mid, end }));
  c.remove();
  gates.splice(0).forEach((r) => r());
  Lib.fetch = keepFetch;
  Lib.fillTransitousIfEmpty = keepFill;
  Lib._boards.clear();
}

/* The integration's FUTÁR coming up after a keyless "all" list loaded
   reloads the destinations once, not on every later state update. */
{
  const keepReach = Lib.reachableDests;
  const access = [];
  Lib.reachableDests = async () => {
    access.push(ed._keyAccess());
    return { dests: [{ key: 'azfehervar', name: 'Azfehérvár' }], destRoutes: {} };
  };
  const ed = document.createElement('hungarian-transit-stop-card-plan-editor');
  document.body.appendChild(ed);
  ed._probeKey = async () => {};
  ed._fillKey = async () => {};
  ed.hass = { states: {}, language: 'hu', services: {} };
  ed.setConfig({ language: 'hu', stopId: 'volan_az4', stopName: 'Azkelenföld' });
  await settleTicks();
  const futarHass = () => ({ states: {}, language: 'hu', services: { hungarian_transport: { futar: {} } } });
  ed.hass = futarHass();
  await settleTicks();
  ed.hass = futarHass();
  await settleTicks();
  check('FUTÁR appearing in hass reloads a keyless destination list once',
    JSON.stringify(access) === '["","integration"]', JSON.stringify(access));
  ed.remove();
  Lib.reachableDests = keepReach;
}

/* A parent station's own board already lists its child poles: an uncapped
   one is not read again pole by pole on every poll. */
{
  const keepFetch = Lib.fetch;
  const t0 = Math.floor(Date.now() / 1000);
  const S = {
    AZC_P1: { id: 'AZC_P1', name: 'Azchub', parentStationId: 'AZC_CSF' },
    AZC_D: { id: 'AZC_D', name: 'Azccel' },
  };
  const boards = [];
  Lib._boards.clear();
  Lib.fetch = async (key, path) => {
    if (path === 'route-details.json') {
      return { data: { entry: { variants: [{ name: 'v', stopIds: ['AZC_P1', 'AZC_D'] }] }, references: { stops: S } } };
    }
    if (path === 'arrivals-and-departures-for-stop.json') {
      boards.push(1);
      return { data: {
        entry: { stopTimes: [
          { tripId: 'AZC_T1', stopId: 'AZC_P1', departureTime: t0 + 600 },
          { tripId: 'AZC_T2', stopId: 'AZC_P1', departureTime: t0 + 59 * 60 },
        ] },
        references: {
          trips: { AZC_T1: { routeId: 'AZC_R', tripHeadsign: 'Azccel' }, AZC_T2: { routeId: 'AZC_R', tripHeadsign: 'Azccel' } },
          routes: { AZC_R: { id: 'AZC_R', shortName: '9', type: 'BUS' } },
          stops: S,
        },
      } };
    }
    return { data: {} };
  };
  const rows = await Lib.departures('k', 'AZC_CSF', ['AZC_R'], { key: 'azccel', name: 'Azccel' },
    { originName: 'Azchub', mode: 'bkk', minutesAfter: 60, deferGps: true });
  check('an uncapped parent board is not read again through its saved child pole',
    boards.length === 1 && rows.length === 2, JSON.stringify({ boards: boards.length, rows: rows.length }));
  Lib.fetch = keepFetch;
  Lib._boards.clear();
}

/* ---- Planner review, fifth round: city → rail ---- */

/* A destination named only by its town ends at the town's own station,
   not the nearer Rókus or Kiskundorozsma one of the same feed; an origin
   that cannot reach it does not end at another station instead. */
{
  const keep = {};
  ['cityIndex', 'railAfterCity'].forEach((k) => { keep[k] = Lib[k]; });
  const realNow = Date.now;
  const names = ['Tisza Lajos körút', 'Szeged vasútállomás', 'Rókus vasútállomás', 'Kiskundorozsma vasútállomás', 'Dorozsmai út'];
  const stops = names.map((n, i) => ({
    i, id: 'szeged:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 46.2 + i * 0.02, lon: 20.1,
  }));
  const t = [];
  for (let m = 0; m < 24 * 60; m += 10) {
    t.push([0, 'R', [0, 2], [m, m + 5], 3]);
    t.push([0, 'S', [0, 1], [m, m + 15], 3]);
    t.push([0, 'K', [4, 3], [m, m + 4], 3]);
  }
  const idx = {
    stops, t, c: [['1111111', '20200101', '20991231']], a: [], r: [],
    ops: [{ i: 0, id: 'szeged', name: 'Szeged — SZKT' }],
    byId: new Map(stops.map((s) => [s.id, s])), byNum: new Map(), byKey: new Map(),
  };
  Lib.cityIndex = async () => idx;
  const asked = [];
  Lib.railAfterCity = async (_k, _h, _c, path) => { asked.push(path.hubName); return null; };
  Date.now = () => Date.UTC(2026, 9, 20, 8, 3);
  let main;
  let far;
  try {
    main = await Lib.cityRailJourney('', null, { id: 'szeged:0', name: 'Tisza Lajos körút' }, { id: 'BKK_005517228', name: 'Szeged' });
    far = await Lib.cityRailJourney('', null, { id: 'szeged:4', name: 'Dorozsmai út' }, { id: 'BKK_005517228', name: 'Szeged' });
  } finally {
    Date.now = realNow;
  }
  const lastRide = main && [...main.legs].reverse().find((l) => !l.walk);
  check('a destination named by its town ends at that town’s own station, not the first one reached',
    !!lastRide && lastRide.to === 'Szeged vasútállomás' && lastRide.label === 'S',
    JSON.stringify(main && main.legs.map((l) => [l.label || 'walk', l.to])));
  check('an origin that cannot reach the town’s station does not end at another one of the town',
    far == null || !far.legs.some((l) => l.to === 'Kiskundorozsma vasútállomás'),
    JSON.stringify({ legs: far && far.legs.map((l) => [l.label || 'walk', l.to]), asked }));
  Object.assign(Lib, keep);
}

/* The origin's other pole is a start of its own: no 3 minute walk to it
   duplicates its paths and eats the station's budget, and a walk that is
   taken first is part of the path it costs. */
{
  const names = ['Fő tér', 'Fő tér', 'Vasútállomás'];
  for (let i = 0; i < 6; i++) names.push('Köztes ' + i);
  const stops = names.map((n, i) => ({
    i, id: 'pecs:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 46.07 + i * 0.001, lon: 18.23,
  }));
  const t = [];
  /* Five ways in from pole 0 (10–13 minutes), one from pole 1 (20): the
     six the station's budget holds, when no walk between the poles
     copies pole 0's at 13–16. */
  for (let r = 0; r < 4; r++) {
    for (let h = 5; h < 23; h++) t.push([0, 'A' + r, [0, 3 + r, 2], [h * 60, h * 60 + 5, h * 60 + 10 + r], 3]);
  }
  for (let h = 5; h < 23; h++) t.push([0, 'B', [1, 2], [h * 60, h * 60 + 20], 3]);
  const idx = { stops, t, c: [['1111111', '20200101', '20991231']], a: [], r: [], ops: [{ i: 0, id: 'pecs', name: 'Pécs — Tüke Busz' }] };
  const paths = Lib.cityHubPaths(idx, stops[0]);
  const sig = (p) => p.edges.map((e) => (e.walk ? 'walk' : e.fromI + '>' + e.toI + ':' + e.routes.join(','))).join('/');
  const sigs = paths.map(sig);
  check('the origin’s other pole adds no walked duplicate, and its own line keeps a place',
    new Set(sigs).size === sigs.length && sigs.some((x) => x === '1>2:B')
    && paths.every((p) => p.edges.reduce((sum, e) => sum + e.minutes, 0) === p.minutes),
    JSON.stringify(paths.map((p) => [p.minutes, sig(p)])));

  const keepGraph = Lib._cityHubGraph;
  const two = [
    { i: 0, op: 0, fold: 'fo ter', name: 'Fő tér' },
    { i: 1, op: 0, fold: 'fo ter b', name: 'Fő tér B' },
    { i: 2, op: 0, fold: 'vasutallomas', name: 'Vasútállomás' },
  ];
  Lib._cityHubGraph = () => ({
    rideAt: new Map([[1, [{ from: 1, to: 2, ride: 10, route: '7', routes: ['7'] }]]]),
    nightAt: new Map(),
    nightUntil: -1,
    walkAt: new Map([[0, [1]], [1, [0]]]),
  });
  let walked;
  try {
    walked = Lib.cityHubPaths({ stops: two }, two[0]);
  } finally {
    Lib._cityHubGraph = keepGraph;
  }
  check('a walk to a neighbouring pole before the first ride is one of the path’s edges',
    walked.length === 1 && walked[0].minutes === 13 && walked[0].edges.length === 2
    && walked[0].edges[0].walk && walked[0].edges[0].minutes === 3 && walked[0].edges[0].from === 'Fő tér',
    JSON.stringify(walked));
}

/* Sunday 03:00: Saturday's night line still runs (27:20, 40 minutes),
   while the day lines, faster and many, start at 05:00. */
{
  const keep = { cityIndex: Lib.cityIndex };
  const realNow = Date.now;
  const names = ['Malomvölgyi út', 'Főpályaudvar'];
  for (let i = 0; i < 8; i++) names.push('Köztes ' + i);
  const stops = names.map((n, i) => ({
    i, id: 'pecs:' + i, name: n, num: '', op: 0, key: Lib.norm(n), fold: Lib.fold(n), lat: 46.07 + i * 0.001, lon: 18.23,
  }));
  const t = [];
  for (let r = 0; r < 8; r++) {
    for (let h = 5; h < 23; h++) t.push([0, 'D' + r, [0, 2 + r, 1], [h * 60, h * 60 + 3, h * 60 + 6 + r], 3]);
  }
  /* Calendar 1: Saturdays only. */
  t.push([1, 'N', [0, 1], [27 * 60 + 20, 28 * 60], 3]);
  const idx = {
    stops, t, c: [['1111111', '20200101', '20991231'], ['0000010', '20200101', '20991231']], a: [], r: [],
    ops: [{ i: 0, id: 'pecs', name: 'Pécs — Tüke Busz' }],
    byId: new Map(stops.map((s) => [s.id, s])), byNum: new Map(), byKey: new Map(),
  };
  Lib.cityIndex = async () => idx;
  Date.now = () => Date.UTC(2026, 10, 1, 2, 0);
  let j;
  let noon;
  try {
    j = await Lib.cityRailJourney('', null, { id: 'pecs:0', name: 'Malomvölgyi út' }, { id: 'BKK_005515131', name: 'Pécs' });
    Date.now = () => Date.UTC(2026, 10, 1, 11, 0);
    noon = Lib.cityHubPaths(idx, stops[0]);
  } finally {
    Date.now = realNow;
  }
  check('after midnight yesterday’s night line is ridden, not the first day line at dawn',
    !!j && j.legs.length === 1 && j.legs[0].label === 'N'
    && j.legs[0].startMs === Date.UTC(2026, 10, 1, 2, 20) && j.legs[0].endMs === Date.UTC(2026, 10, 1, 3, 0),
    JSON.stringify(j && j.legs.map((l) => [l.label || 'walk', l.startMs, l.endMs])));
  check('once the night is over its line is not offered',
    noon.length > 0 && noon.every((p) => p.edges.every((e) => e.routes.indexOf('N') < 0)),
    JSON.stringify(noon.map((p) => p.edges.map((e) => e.routes.join(',')))));
  Object.assign(Lib, keep);
}

/* At a chosen time the full map of an itinerary without geometry does not
   plan again for "now" and replace the open itinerary. */
{
  const keep = { planJourney: Lib.planJourney, fillJourneyShapes: Lib.fillJourneyShapes };
  let planned = 0;
  const fresh = { durationMin: 9, transfers: 0, walkMin: 0, waitMin: 0, legs: [] };
  Lib.planJourney = async () => { planned += 1; return fresh; };
  Lib.fillJourneyShapes = async () => {};
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_A', stopName: 'A', destKey: 'b', destName: 'B' });
  const open = { durationMin: 30, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label: '7', minutes: 30, from: 'A', to: 'B' }] };
  pc._when = { mode: 'depart', at: Date.now() + 3600 * 1000 };
  pc._journeys = [open];
  pc._openJourney = 0;
  pc._journey = open;
  try {
    await pc._openJourneyMap();
  } finally {
    Object.assign(Lib, keep);
  }
  check('a chosen time’s itinerary map does not plan for now or replace the open itinerary',
    planned === 0 && pc._journey === open && pc._journeys[0] === open,
    JSON.stringify({ planned, replaced: pc._journey !== open }));
  pc._closeVehicleMap();
  pc.remove();
}

/* With a destination and every saved pole a child of the parent, only the
   parent's board is read: when it fails, the child poles' boards stand in
   instead of the whole board failing. */
{
  const keep = { op: Lib.originPoles, kids: Lib.childPolesOf, board: Lib.departureBoard };
  const t0 = Math.floor(Date.now() / 1000);
  const board = (pid) => ({ data: {
    entry: { stopId: pid, stopTimes: [{ stopId: pid, tripId: 'AZP_T' + pid, departureTime: t0 + (pid === 'AZP_K1' ? 300 : 900) }] },
    references: {
      trips: { ['AZP_T' + pid]: { routeId: 'AZP_R', tripHeadsign: 'Azpcel' } },
      routes: { AZP_R: { id: 'AZP_R', shortName: '7', type: 'BUS' } },
      stops: {},
    },
  } });
  const calls = [];
  let opCalls = 0;
  Lib.originPoles = async () => {
    opCalls += 1;
    return ['AZP_P', 'AZP_K1', 'AZP_K2'];
  };
  Lib.childPolesOf = () => new Set(['AZP_K1', 'AZP_K2']);
  Lib.departureBoard = async (_k, pid) => {
    calls.push(pid);
    if (pid === 'AZP_P') throw new Error('parent timeout');
    return board(pid);
  };
  let rows = null;
  let threw = '';
  try {
    rows = await Lib.departures('k', 'AZP_P', ['AZP_R'], { key: 'azpcel', name: 'Azpcel' },
      { originName: 'Azporigo', mode: 'bkk', minutesAfter: 60, deferGps: true, cache: {} });
  } catch (err) {
    threw = String(err && err.message);
  }
  check('a failed parent board falls back to its saved child poles',
    !threw && rows && rows.length === 2 && calls.indexOf('AZP_K1') >= 0 && calls.indexOf('AZP_K2') >= 0,
    JSON.stringify({ threw, rows: rows && rows.map((r) => r.tripId), calls }));
  /* The failed parent board has no span: it is not "capped", so the
     outage does not set off discovery's extra lookups. */
  check('a failed parent board does not set off route discovery',
    opCalls === 1 && calls.length === 3, JSON.stringify({ opCalls, calls }));
  Object.assign(Lib, { originPoles: keep.op, childPolesOf: keep.kids, departureBoard: keep.board });
}

/* One failed poll keeps the FUTÁR rows on screen under the banner: they
   are not swapped for a Transitous fill. */
{
  const keep = { dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, gps: Lib.attachCoachGps, travel: Lib.travelMin };
  const t0 = Math.floor(Date.now() / 1000);
  let n = 0;
  Lib.departures = async () => {
    n += 1;
    if (n === 1) return [{ tripId: 'BKK_AZQ_T1', label: '7', head: 'Azqcel', dep: t0 + 300, sched: t0 + 300, vehicle: 'bus', rawType: 'BUS', travel: null }];
    throw new Error('HTTP 503');
  };
  Lib.fillTransitousIfEmpty = async (rows) => ((rows && rows.length) ? rows
    : [{ tripId: 'motis:AZQ', label: '7', head: 'Azqcel', dep: t0 + 400, sched: t0 + 400, vehicle: 'bus', rawType: 'BUS' }]);
  Lib.attachCoachGps = async (_h, rows) => rows;
  Lib.travelMin = async () => 5;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'BKK_AZQ_O', stopName: 'Azqorigo', destKey: 'azqcel', destName: 'Azqcel', apiKey: 'k' });
  await settleTicks(30);
  const ids = () => (c._rawRows || []).map((r) => r.tripId);
  const first = ids();
  await c._pollRun.run();
  await settleTicks(30);
  const after = ids();
  check('a failed poll keeps the FUTÁR rows instead of a Transitous fill',
    JSON.stringify(first) === '["BKK_AZQ_T1"]' && JSON.stringify(after) === '["BKK_AZQ_T1"]'
    && !!c._err && !c._rowsFilled,
    JSON.stringify({ first, after, err: !!c._err, filled: c._rowsFilled }));
  c.remove();
  Object.assign(Lib, { departures: keep.dep, fillTransitousIfEmpty: keep.fill, attachCoachGps: keep.gps, travelMin: keep.travel });
}

/* Only a MÁV station's id carries an ELVIRA code: a Szeged city stop's
   longer number is not one, and does not make the destination a station. */
check('a city stop number is not an ELVIRA station code',
  Lib.elviraStationCode('szeged:2430584836') === ''
  && !Lib.isRailDest({ id: 'szeged:2430584836', name: 'Azr utca' })
  && Lib.elviraStationCode('hu-mav_005501024') === '005501024'
  && Lib.elviraStationCode('005510017') === '005510017'
  && Lib.elviraStationCode('BKK_0055110170') === '');

/* A stop without coordinates (lat: null) is looked up by name, not
   planned from 0,0. */
{
  const keep = { geo: Lib.transitousGeocode, fetch: Lib.fetch };
  Lib.transitousGeocode = async () => [{ id: 'azs', name: 'Azsplace', lat: 47.49, lon: 19.06, type: 'STOP' }];
  Lib.fetch = async () => ({ data: { references: { stops: {} } } });
  const place = await Lib.transitousPlace({ id: 'BKK_AZS', name: 'Azsplace', lat: null, lon: null });
  const vertex = await Lib.planPlace('k', { id: '', name: 'Azsplace', lat: null, lon: null });
  check('missing coordinates are not 0,0 for Transitous or the FUTÁR planner',
    place === '47.49,19.06' && vertex === '', JSON.stringify({ place, vertex }));
  Object.assign(Lib, { transitousGeocode: keep.geo, fetch: keep.fetch });
}

/* A GTFS row without its own line gets the coach_shape service's. */
{
  const keep = {
    gtfs: Lib.gtfsRowShape, motis: Lib.motisShapeForHop, area: Lib.stopArea,
    coach: Lib.coachShape, decode: Lib.decodePolyline,
  };
  const pts = [[46.90, 18.05], [46.95, 18.10], [47.00, 18.15], [47.05, 18.20]];
  Lib.gtfsRowShape = async () => null;
  Lib.motisShapeForHop = async () => null;
  Lib.stopArea = async () => '';
  Lib.coachShape = async () => 'azt';
  Lib.decodePolyline = (enc) => (enc === 'azt' ? pts.map((p) => p.slice()) : keep.decode(enc));
  const c = document.createElement('hungarian-transport-card');
  c.hass = { states: {}, language: 'hu', services: {} };
  c._config = { language: 'hu', stopId: 'volan_azt', stopName: 'Aztorigo', destKey: 'aztcel', destName: 'Aztcel', destLat: 47.05, destLon: 18.20, apiKey: 'k' };
  const got = await c._loadRowMapShape({ tripId: 'gtfs:azt', label: '1234', headsign: 'Aztcel', vehicle: 'coach', rawType: 'BUS' });
  check('a GTFS row map draws the coach_shape service line',
    got && got.shape.length >= 2 && !got.approx, JSON.stringify(got && { n: got.shape.length, approx: got.approx }));
  Object.assign(Lib, {
    gtfsRowShape: keep.gtfs, motisShapeForHop: keep.motis, stopArea: keep.area,
    coachShape: keep.coach, decodePolyline: keep.decode,
  });
}

/* One vehicle filed under two route keys (12 and SZ12) goes to one row. */
{
  const t0 = Math.floor(Date.now() / 1000);
  const rows = [
    { tripId: 'gtfs:azu1', label: '12', headsign: '', dep: t0 + 60, vehicle: 'bus' },
    { tripId: 'gtfs:azu2', label: 'SZ12', headsign: '', dep: t0 + 120, vehicle: 'bus' },
  ];
  Lib.applyCoachGps(rows, [{ route: 'SZ12', lat: 47.2, lon: 18.4, head: '', tripId: 'azuv1' }], t0);
  const withGps = rows.filter((r) => Lib.mapGpsLatLon(r.lat, r.lon)).length;
  check('one coach GPS position is given to one row only', withGps === 1,
    JSON.stringify(rows.map((r) => [r.label, r.lat, r.lon])));
}

/* A carried coach position only bridges the lookup: matched, it is
   replaced; unmatched, it goes. */
{
  const t0 = Math.floor(Date.now() / 1000);
  const rows = [
    { tripId: 'gtfs:azv1', label: '1100', headsign: '', dep: t0 + 60, vehicle: 'coach', lat: 47.1, lon: 18.1, gpsCarried: true },
    { tripId: 'gtfs:azv2', label: '1200', headsign: '', dep: t0 + 60, vehicle: 'coach', lat: 47.3, lon: 18.3, gpsCarried: true },
  ];
  Lib.applyCoachGps(rows, [{ route: '1100', lat: 47.15, lon: 18.15, head: '', tripId: 'azvv1' }], t0);
  check('a carried coach position is replaced or dropped by the lookup',
    rows[0].lat === 47.15 && !rows[0].gpsCarried && rows[1].lat == null && !rows[1].gpsCarried,
    JSON.stringify(rows));
}

/* A poll's GTFS rows arrive before FUTÁR: the FUTÁR rows on screen stay
   beside them until FUTÁR answers. */
{
  const keep = {
    op: Lib.originPoles, board: Lib.departureBoard, volan: Lib.volanDepartures,
    fill: Lib.fillTransitousIfEmpty, gps: Lib.attachCoachGps, travel: Lib.travelMin,
  };
  const t0 = Math.floor(Date.now() / 1000);
  const board = { data: {
    entry: { stopId: 'BKK_AZW_O', stopTimes: [{ tripId: 'BKK_AZW_T1', departureTime: t0 + 600 }] },
    references: {
      trips: { BKK_AZW_T1: { routeId: 'AZW_R', tripHeadsign: 'Azwcel' } },
      routes: { AZW_R: { id: 'AZW_R', shortName: '800', type: 'COACH' } },
      stops: {},
    },
  } };
  let gate = null;
  Lib.originPoles = async () => [];
  Lib.departureBoard = async () => {
    if (gate) await gate.p;
    return board;
  };
  Lib.volanDepartures = async () => [{ tripId: 'gtfs:azw', label: '820', head: 'Azwcel', dep: t0 + 900, sched: t0 + 900, vehicle: 'coach', rawType: 'BUS', travel: 30 }];
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.attachCoachGps = async (_h, rows) => rows;
  Lib.travelMin = async () => null;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'BKK_AZW_O', stopName: 'Azworigo', destKey: 'azwcel', destName: 'Azwcel', volan: true, apiKey: 'k' });
  await settleTicks(30);
  const first = c._rows.map((r) => r.label).sort().join(',');
  const seen = [];
  const paint = c._paint.bind(c);
  c._paint = () => {
    seen.push(c._rows.map((r) => r.label).sort().join(','));
    paint();
  };
  let release = null;
  gate = { p: new Promise((r) => { release = r; }) };
  const poll = c._pollRun.run();
  await settleTicks(30);
  release();
  await poll;
  await settleTicks(30);
  check('GTFS rows painted before FUTÁR answers keep the FUTÁR rows',
    first === '800,820' && seen.length > 0 && seen.every((s) => s === '800,820'),
    JSON.stringify({ first, seen }));
  c.remove();
  Object.assign(Lib, {
    originPoles: keep.op, departureBoard: keep.board, volanDepartures: keep.volan,
    fillTransitousIfEmpty: keep.fill, attachCoachGps: keep.gps, travelMin: keep.travel,
  });
}

/* Each poll builds new rows: the ride time and the coach GPS of the last
   list stay on them while this run fills them again. */
{
  const keep = { dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, gps: Lib.attachCoachGps, travel: Lib.travelMin };
  const t0 = Math.floor(Date.now() / 1000);
  let travelGate = null;
  let gpsGate = null;
  Lib.departures = async () => [
    { tripId: 'BKK_AZX_T1', label: '7', head: 'Azxcel', dep: t0 + 300, sched: t0 + 300, vehicle: 'bus', rawType: 'BUS', travel: null },
    { tripId: 'gtfs:azx', label: '1300', head: 'Azxcel', dep: t0 + 400, sched: t0 + 400, vehicle: 'coach', rawType: 'BUS', travel: 20 },
  ];
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.travelMin = async () => {
    if (travelGate) await travelGate.p;
    return 12;
  };
  Lib.attachCoachGps = async (_h, rows) => {
    if (gpsGate) {
      await gpsGate.p;
      return rows;
    }
    rows.forEach((r) => {
      if (/^gtfs:/.test(r.tripId)) {
        r.lat = 47.2;
        r.lon = 18.4;
      }
    });
    return rows;
  };
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'BKK_AZX_O', stopName: 'Azxorigo', destKey: 'azxcel', destName: 'Azxcel', apiKey: 'k' });
  await settleTicks(30);
  const pick = () => {
    const bus = c._rows.find((r) => r.label === '7') || {};
    const coach = (c._rawRows || []).find((r) => r.tripId === 'gtfs:azx') || {};
    return { travel: bus.travelMin, lat: coach.lat };
  };
  const first = pick();
  const gates = [];
  const mk = () => {
    const g = {};
    g.p = new Promise((r) => { g.r = r; });
    gates.push(g);
    return g;
  };
  travelGate = mk();
  gpsGate = mk();
  const poll = c._pollRun.run();
  await settleTicks(30);
  const mid = pick();
  gates.forEach((g) => g.r());
  await poll;
  await settleTicks(30);
  check('ride time and coach GPS do not blink out during a poll',
    first.travel === 12 && first.lat === 47.2 && mid.travel === 12 && mid.lat === 47.2,
    JSON.stringify({ first, mid }));
  c.remove();
  Object.assign(Lib, { departures: keep.dep, fillTransitousIfEmpty: keep.fill, attachCoachGps: keep.gps, travelMin: keep.travel });
}

/* A key probe that could not reach FUTÁR is asked again after a while:
   the key is not marked bad, and it does not stay unverified for good. */
{
  const keep = { probe: Lib.probeApiKey, city: Lib.cityIndex };
  let calls = 0;
  let fail = true;
  Lib.probeApiKey = async () => {
    calls += 1;
    return fail ? { ok: false, code: 'errApiKeyProbe' } : { ok: true };
  };
  Lib.cityIndex = async () => ({ ops: [] });
  const hass = { states: {}, language: 'hu', services: { hungarian_transport: { futar: {} } } };
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.setConfig({ type: 'custom:hungarian-transport-card', language: 'hu' });
  ed.hass = hass;
  await settleTicks();
  const afterFail = { calls, keyOk: ed._keyOk };
  ed.hass = Object.assign({}, hass, { states: { x: 1 } });
  await settleTicks();
  const inWindow = calls;
  fail = false;
  ed._probeRetryMs = 0;
  ed._probeRetryAt = 0;
  ed.hass = Object.assign({}, hass, { states: { x: 2 } });
  await settleTicks();
  check('an unreachable key probe is retried later, not marked bad for good',
    afterFail.calls === 1 && afterFail.keyOk == null && inWindow === 1 && calls === 2 && ed._keyOk === true
    && !ed.querySelector('#err').textContent,
    JSON.stringify({ afterFail, inWindow, calls, keyOk: ed._keyOk, err: ed.querySelector('#err').textContent }));
  ed.remove();
  Object.assign(Lib, { probeApiKey: keep.probe, cityIndex: keep.city });
}

/* A search typed while the sibling-key lookup runs waits for that lookup
   instead of starting a second one. */
{
  const keep = { dash: Lib.apiKeyFromDashboard };
  let calls = 0;
  let release = null;
  Lib.apiKeyFromDashboard = async () => {
    calls += 1;
    await new Promise((r) => { release = r; });
    return '';
  };
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed._probeKey = async () => {};
  ed.setConfig({ type: 'custom:hungarian-transport-card', language: 'hu' });
  ed.hass = { states: {}, language: 'hu', services: {} };
  await settleTicks();
  const search = ed._search('Azy', 'origin');
  await settleTicks();
  const during = calls;
  if (release) release();
  await search;
  await settleTicks();
  check('a search during the sibling-key lookup does not start a second one',
    during === 1, JSON.stringify({ during, calls }));
  ed.remove();
  Lib.apiKeyFromDashboard = keep.dash;
}

/* An index file that is not an index (an HTML page) is not downloaded and
   parsed again on every lookup; a relative index URL still finds shapes. */
{
  const keep = { bytes: Lib._fetchIndexBytes, gz: Lib._fetchJsonGz };
  const fetched = [];
  Lib._fetchIndexBytes = async (url) => {
    fetched.push(url);
    if (/azzbad/.test(url)) return { status: 200, bytes: new TextEncoder().encode('<html>no</html>') };
    return { status: 200, bytes: new TextEncoder().encode(JSON.stringify({ v: 1, s: [], t: [] })) };
  };
  const bad = Lib.withIndexes({ volanIndex: 'http://localhost:8123/local/azzbad/volan-index.json.gz' });
  const errs = [];
  for (let i = 0; i < 3; i++) {
    try {
      await bad.volanIndex();
    } catch (err) {
      errs.push(err && err.i18nKey);
    }
  }
  const badFetches = fetched.filter((u) => /azzbad/.test(u)).length;
  const idx = await Lib.withIndexes({ cityIndex: '/local/azzrel/city-index.json.gz' }).cityIndex();
  const shardUrls = [];
  Lib._fetchJsonGz = async (url) => {
    shardUrls.push(url);
    return { p: [] };
  };
  let shapeErr = '';
  try {
    await Lib.gtfsShape({ base: idx.url, dir: 'sh-azz', n: 1, id: 0 });
  } catch (err) {
    shapeErr = String(err && err.message);
  }
  check('a broken index is not refetched on every lookup',
    badFetches === 1 && errs.length === 3 && errs.every((k) => k === 'errIndexParse'),
    JSON.stringify({ badFetches, errs }));
  check('a relative index URL still resolves its shape shards',
    !shapeErr && shardUrls[0] === 'http://localhost:8123/local/azzrel/sh-azz/00.json.gz',
    JSON.stringify({ url: idx.url, shapeErr, shardUrls }));
  Object.assign(Lib, { _fetchIndexBytes: keep.bytes, _fetchJsonGz: keep.gz });
}

/* ---- Sixth review: hop card and its libraries ---- */

/* A failed poll keeps only FUTÁR rows still due: an outage longer than
   the kept rows fills the board from Transitous instead of leaving only
   the banner. */
{
  const keep = { dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, gps: Lib.attachCoachGps, travel: Lib.travelMin };
  const t0 = Math.floor(Date.now() / 1000);
  let n = 0;
  let fills = 0;
  Lib.departures = async () => {
    n += 1;
    if (n === 1) return [{ tripId: 'BKK_BAA_T1', label: '7', head: 'Baacel', dep: t0 + 300, sched: t0 + 300, vehicle: 'bus', rawType: 'BUS', travel: 4 }];
    throw new Error('HTTP 503');
  };
  Lib.fillTransitousIfEmpty = async (rows) => {
    if (rows && rows.length) return rows;
    fills += 1;
    return [{ tripId: 'motis:BAA', label: '7', head: 'Baacel', dep: t0 + 900, sched: t0 + 900, vehicle: 'bus', rawType: 'BUS' }];
  };
  Lib.attachCoachGps = async (_h, rows) => rows;
  Lib.travelMin = async () => 4;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'BKK_BAA_O', stopName: 'Baaorigo', destKey: 'baacel', destName: 'Baacel', apiKey: 'k' });
  await settleTicks(30);
  await c._pollRun.run();
  await settleTicks(30);
  const keptWhileDue = (c._rawRows || []).map((r) => r.tripId).join(',');
  const fillsWhileDue = fills;
  /* The outage outlasts the kept row: it has left by the next poll. */
  c._rawRows.forEach((r) => { r.dep = t0 - 3600; r.sched = t0 - 3600; });
  c._rows.forEach((r) => { r.depTs = t0 - 3600; r.schedTs = t0 - 3600; });
  await c._pollRun.run();
  await settleTicks(30);
  const shown = (c._shownRows || []).map((r) => r.tripId).join(',');
  check('an outage longer than the kept FUTÁR rows fills the board from Transitous',
    keptWhileDue === 'BKK_BAA_T1' && fillsWhileDue === 0 && fills === 1
    && shown === 'motis:BAA' && c._rowsFilled && !!c._err,
    JSON.stringify({ keptWhileDue, fillsWhileDue, fills, shown, filled: c._rowsFilled, err: !!c._err }));
  c.remove();
  Object.assign(Lib, { departures: keep.dep, fillTransitousIfEmpty: keep.fill, attachCoachGps: keep.gps, travelMin: keep.travel });
}

/* A successful poll's Transitous fill (FUTÁR answered with nothing) is
   not this board's own rows: the next failed poll fills it again instead
   of keeping the Transitous rows as if FUTÁR had sent them. */
{
  const keep = { dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, gps: Lib.attachCoachGps, travel: Lib.travelMin };
  const t0 = Math.floor(Date.now() / 1000);
  let n = 0;
  let fills = 0;
  Lib.departures = async () => {
    n += 1;
    if (n === 1) return [];
    throw new Error('HTTP 503');
  };
  Lib.fillTransitousIfEmpty = async (rows) => {
    if (rows && rows.length) return rows;
    fills += 1;
    return [{ tripId: 'motis:BAB' + fills, label: '7', head: 'Babcel', dep: t0 + 900, sched: t0 + 900, vehicle: 'bus', rawType: 'BUS' }];
  };
  Lib.attachCoachGps = async (_h, rows) => rows;
  Lib.travelMin = async () => null;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { states: {}, language: 'hu', services: {} };
  c.setConfig({ language: 'hu', stopId: 'BKK_BAB_O', stopName: 'Baborigo', destKey: 'babcel', destName: 'Babcel', apiKey: 'k' });
  await settleTicks(30);
  const first = { ids: (c._rawRows || []).map((r) => r.tripId).join(','), filled: c._rowsFilled };
  await c._pollRun.run();
  await settleTicks(30);
  const after = (c._rawRows || []).map((r) => r.tripId).join(',');
  check('a Transitous fill on a successful poll is refilled by a failed one',
    first.ids === 'motis:BAB1' && first.filled === true && fills === 2
    && after === 'motis:BAB2' && c._rowsFilled && !!c._err,
    JSON.stringify({ first, fills, after, filled: c._rowsFilled, err: !!c._err }));
  c.remove();
  Object.assign(Lib, { departures: keep.dep, fillTransitousIfEmpty: keep.fill, attachCoachGps: keep.gps, travelMin: keep.travel });
}

/* A position the sensor or EMMA gives a GTFS/MOTIS row is enrichment's
   own: carried to the next poll, it is not dropped by a coach lookup that
   matched no coach (the map dot blinked out every other poll). */
{
  const t0 = Math.floor(Date.now() / 1000);
  const hass = { states: { 'sensor.bac': { attributes: { vehicles: [
    { tripId: 'elvira:bac', trainNumber: '2512', label: '2512', lat: 47.5, lon: 19.0 },
    { label: 'BACNEV', lat: 47.7, lon: 19.3 },
  ] } } } };
  const rows = [
    { tripId: 'gtfs:BAC1', label: 'G20', trainNumber: '2512', vehicle: 'rail', dep: t0 + 600, sched: t0 + 600, lat: 47.4, lon: 18.9, gpsCarried: true },
    { tripId: 'motis:BAC2', label: 'BACNEV', vehicle: 'rail', dep: t0 + 700, sched: t0 + 700, lat: 47.6, lon: 19.2, gpsCarried: true },
  ];
  Lib.enrichFromHass(hass, rows);
  const flags = rows.map((r) => !!r.gpsCarried);
  Lib.applyCoachGps(rows, [], t0);
  check('a sensor or EMMA position is not dropped by an unmatched coach lookup',
    JSON.stringify(flags) === '[false,false]'
    && rows[0].lat === 47.5 && rows[0].lon === 19 && rows[1].lat === 47.7 && rows[1].lon === 19.3,
    JSON.stringify({ flags, rows: rows.map((r) => [r.lat, r.lon, r.gpsCarried]) }));
}

/* The same, through the card's polls: a MOTIS train whose position comes
   from a sensor keeps it on every poll while the coach lookup is slow and
   matches nothing. */
{
  const keep = {
    dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, elvira: Lib.hasElvira,
    coach: Lib.coachPositions, travel: Lib.travelMin,
  };
  const t0 = Math.floor(Date.now() / 1000);
  Lib.departures = async () => [{ tripId: 'motis:BAD', label: 'S70', vehicle: 'rail', rawType: 'RAIL', trainNumber: '5270', dep: t0 + 600, sched: t0 + 600, head: 'Badcel' }];
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.hasElvira = () => false;
  Lib.coachPositions = async () => {
    await new Promise((r) => setTimeout(r, 5));
    return [];
  };
  Lib.travelMin = async () => null;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { language: 'hu', services: {}, states: { 'sensor.bad': { attributes: { vehicles: [{ trainNumber: '5270', label: 'S70', lat: 47.6, lon: 19.1 }] } } } };
  c.setConfig({ language: 'hu', stopId: 'BKK_BAD_O', stopName: 'Badorigo', destKey: 'badcel', destName: 'Badcel', mav: true, apiKey: 'k' });
  await new Promise((r) => setTimeout(r, 40));
  const seen = [];
  for (let i = 0; i < 3; i++) {
    await c._pollRun.run();
    await new Promise((r) => setTimeout(r, 20));
    seen.push((c._rows || []).map((r) => (r.hasGps ? r.lat + ',' + r.lon : 'none')).join('|'));
  }
  check('a sensor position stays on the card on every poll',
    seen.length === 3 && seen.every((s) => s === '47.6,19.1'), JSON.stringify(seen));
  c.remove();
  Object.assign(Lib, {
    departures: keep.dep, fillTransitousIfEmpty: keep.fill, hasElvira: keep.elvira,
    coachPositions: keep.coach, travelMin: keep.travel,
  });
}

/* After a failed load, a poll waiting on the through-check with nothing
   on the board shows the spinner, not "no departures". */
{
  const keep = { dep: Lib.departures, fill: Lib.fillTransitousIfEmpty, elvira: Lib.hasElvira, gps: Lib.attachCoachGps };
  const t0 = Math.floor(Date.now() / 1000);
  let fail = true;
  let release = null;
  Lib.departures = async (_k, _s, _r, _d, opts) => {
    if (fail) throw new Error('BKK timeout');
    opts.armThroughCheck(() => {
      new Promise((r) => { release = r; }).then(() => opts.onFutarUpdate([
        { tripId: 'BKK_BAE_T1', label: '9', vehicle: 'bus', rawType: 'BUS', dep: t0 + 600, sched: t0 + 600, head: 'Baecel' },
      ]));
    });
    return [];
  };
  Lib.fillTransitousIfEmpty = async (rows) => rows || [];
  Lib.hasElvira = () => false;
  Lib.attachCoachGps = async (_h, rows) => rows;
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c.hass = { language: 'en', states: {}, services: { hungarian_transport: { futar: {} } } };
  c.setConfig({ language: 'en', stopId: 'BKK_BAE_O', stopName: 'Baeorigo', destKey: 'baecel', destName: 'Baecel' });
  await settleTicks(30);
  const msg = () => (c.shadowRoot.querySelector('.msg') || {}).textContent || '';
  const failed = { msg: msg(), err: c._elErr.textContent };
  fail = false;
  const poll = c._pollRun.run();
  await settleTicks(30);
  const pending = { msg: msg(), err: c._elErr.textContent };
  if (release) release();
  await poll;
  await settleTicks(30);
  check('a poll after a failed load shows the spinner until the through-check answers',
    !!failed.err && pending.msg === 'Loading…' && !pending.err
    && (c._rows || []).map((r) => r.tripId).join(',') === 'BKK_BAE_T1',
    JSON.stringify({ failed, pending, rows: (c._rows || []).length }));
  c.remove();
  Object.assign(Lib, { departures: keep.dep, fillTransitousIfEmpty: keep.fill, hasElvira: keep.elvira, attachCoachGps: keep.gps });
}

/* One EMMA vehicle is one train: two unnumbered rows with the same name
   do not both get its position, and a numbered row comes first. */
{
  const t0 = Math.floor(Date.now() / 1000);
  const twin = [
    { tripId: 'gtfs:BAF1', label: 'BAFNEV', vehicle: 'rail', dep: t0 + 300 },
    { tripId: 'gtfs:BAF2', label: 'BAFNEV', vehicle: 'rail', dep: t0 + 3900 },
  ];
  Lib.applyEmmaPositions(twin, [{ label: 'BAFNEV', lat: 46.9, lon: 17.9, trip: { tripShortName: 'BAFNEV' } }]);
  const numbered = [
    { tripId: 'gtfs:BAG1', label: 'BAGNEV', vehicle: 'rail', dep: t0 + 300 },
    { tripId: 'gtfs:BAG2', label: 'BAGNEV', trainNumber: '1952', vehicle: 'rail', dep: t0 + 3900 },
  ];
  Lib.applyEmmaPositions(numbered, [{ label: 'BAGNEV', lat: 46.8, lon: 17.8, trip: { tripShortName: '1952 BAGNEV' } }]);
  const pos = (list) => list.map((r) => (Lib.mapGpsLatLon(r.lat, r.lon) ? r.lat : null));
  check('one EMMA vehicle is given to one row only',
    JSON.stringify(pos(twin)) === '[46.9,null]' && JSON.stringify(pos(numbered)) === '[null,46.8]',
    JSON.stringify({ twin: pos(twin), numbered: pos(numbered) }));
}

/* City feed → rail: the destination is a hub only by its own place when
   both have one, and by name only in its own town's feed. Székesfehérvár's
   "Vasútállomás" is not Szombathely's, and "Győr" is not Győr-Szabadhegy
   2.2 km off: those journeys take the train, or there is none. */
{
  const keep = {};
  ['cityIndex', 'searchStops', 'elviraBetween'].forEach((k) => { keep[k] = Lib[k]; });
  const day = Lib.volanDay(0);
  const m = day.mins;
  const idx = {
    c: [['1111111', '20000101', '20991231']],
    ops: [
      { i: 0, id: 'szekesfehervar', name: 'Székesfehérvár — helyi' },
      { i: 1, id: 'szombathely', name: 'Szombathely — Blaguss' },
      { i: 2, id: 'volan-gyor', name: 'Győr' },
    ],
    byNum: new Map(),
    byKey: new Map(),
    stops: [
      ['szekesfehervar:B419', 'Agyag utca', 47.2, 18.43, 0],
      ['szekesfehervar:2', 'Vasútállomás', 47.1838, 18.4245, 0],
      ['szombathely:51150', 'Vasútállomás', 47.23702, 16.63181, 1],
      ['volan-gyor:1', 'Győr (Gyirmót), Ménfői út', 47.64, 17.66, 2],
      ['volan-gyor:2', 'Győr, Szabadhegy, vasútállomás', 47.6721, 17.6612, 2],
    ].map(([id, name, lat, lon, op], i) => ({ i, op, id, name, key: Lib.norm(name), fold: Lib.fold(name), lat, lon })),
    t: [[0, '38', [0, 1], [m + 10, m + 29]], [0, '37', [3, 4], [m + 10, m + 30]]],
  };
  Lib.cityIndex = async () => idx;
  /* A bare "Vasútállomás" is answered with some town's station. */
  Lib.searchStops = async (_k, q, mode) => {
    if (mode !== 'mav') return [];
    if (/szombathely/i.test(q)) return [{ id: 'BKK_005504034', name: 'Szombathely', lat: 47.2335, lon: 16.6297 }];
    if (/győr/i.test(q)) {
      return [
        { id: 'BKK_005504200', name: 'Győr-Szabadhegy', lat: 47.6725, lon: 17.6605 },
        { id: 'BKK_005504209', name: 'Győr', lat: 47.6815, lon: 17.6337 },
      ];
    }
    return [{ id: 'BKK_005503269', name: 'Székesfehérvár', lat: 47.18361, lon: 18.42472 }];
  };
  let asked = [];
  let trains = true;
  Lib.elviraBetween = async (_hass, from, to) => {
    asked.push({ from, to });
    if (!trains) return [];
    return [{ label: 'IC 900', dep: day.base + (m + 50) * 60, travel: 90, head: 'Terminus', destHit: true }];
  };
  const hass = { states: {} };
  const legsOf = (j) => (j ? j.legs.map((l) => (l.walk ? 'walk' : l.label) + '>' + l.to) : null);
  const agyag = { id: 'szekesfehervar:B419', name: 'Agyag utca' };
  const szhely = { id: 'szombathely:51150', name: 'Vasútállomás', lat: 47.23702, lon: 16.63181 };
  const last = (j) => j && j.legs[j.legs.length - 1];

  const placed = await Lib.cityRailJourney('', hass, agyag, szhely);
  check('another town’s "Vasútállomás" with coordinates is reached by train, not by the bus to this town’s',
    !!placed && last(placed).label === 'IC 900' && last(placed).to === 'Szombathely'
    && placed.legs.some((l) => l.label === '38' && l.to === 'Vasútállomás')
    && asked.length === 1 && asked[0].from === 'BKK_005503269' && asked[0].to === '005504034',
    JSON.stringify({ legs: legsOf(placed), asked }));
  asked = [];
  const named = await Lib.cityRailJourney('', hass, agyag, { id: szhely.id, name: szhely.name });
  check('without coordinates another town’s "Vasútállomás" is not this town’s by name',
    !!named && last(named).label === 'IC 900' && asked.length === 1 && asked[0].to === '005504034',
    JSON.stringify({ legs: legsOf(named), asked }));
  trains = false;
  asked = [];
  const noTrain = await Lib.cityRailJourney('', hass, agyag, szhely);
  check('with no train to another town’s station there is no journey, not a bus ride to the wrong town',
    noTrain === null && asked.length === 1, JSON.stringify({ legs: legsOf(noTrain), asked }));

  const gyirmot = { id: 'volan-gyor:1', name: 'Győr (Gyirmót), Ménfői út' };
  const gyor = { id: 'BKK_005504209', name: 'Győr', lat: 47.6807, lon: 17.634 };
  asked = [];
  const gyorNone = await Lib.cityRailJourney('', hass, gyirmot, gyor);
  trains = true;
  asked = [];
  const gyorRail = await Lib.cityRailJourney('', hass, gyirmot, gyor);
  check('"Győr" does not end at Győr-Szabadhegy 2.2 km off: the train takes it on, or there is no journey',
    gyorNone === null && !!gyorRail && last(gyorRail).label === 'IC 900'
    && asked.length === 1 && asked[0].from === 'BKK_005504200' && asked[0].to === '005504209',
    JSON.stringify({ none: legsOf(gyorNone), rail: legsOf(gyorRail), asked }));
  asked = [];
  const atHub = await Lib.cityRailJourney('', hass, gyirmot,
    { id: 'BKK_005504200', name: 'Győr-Szabadhegy', lat: 47.6725, lon: 17.6605 });
  check('a hub by the destination’s own place is it: the bus ends there, no train',
    !!atHub && last(atHub).label === '37' && last(atHub).to === 'Győr, Szabadhegy, vasútállomás' && asked.length === 0,
    JSON.stringify({ legs: legsOf(atHub), asked }));
  Object.assign(Lib, keep);
}

/* A replan from the journey map redraws the card's own journey box, and an
   OSM way origin (way/…) is a place: no destinations are looked up for it. */
{
  const keep = {
    planJourney: Lib.planJourney,
    fillJourneyShapes: Lib.fillJourneyShapes,
    journeyMapSegments: Lib.journeyMapSegments,
    reachableDests: Lib.reachableDests,
  };
  const layer = () => ({ addTo() { return this; } });
  const L = {
    map: () => ({
      setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {},
    }),
    tileLayer: () => layer(),
    polyline: () => layer(),
    marker: () => layer(),
    divIcon: (o) => o,
    latLngBounds: (pts) => ({ pts }),
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  pc._ensureLeaflet = async () => L;
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_F00001', stopName: 'A', destKey: 'b', destName: 'B', destStopId: 'BKK_F00002' });
  pc._rows = [];
  const stale = { durationMin: 11, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label: 'OLD7', color: '009EE3', minutes: 11, from: 'A', to: 'B' }] };
  const fresh = { durationMin: 23, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label: 'NEW9', color: '009EE3', minutes: 23, from: 'A', to: 'B', shape: [[47.5, 19.0], [47.52, 19.05]] }] };
  pc._journey = stale;
  pc._paint();
  await settleTicks();
  const box = () => (pc.shadowRoot.querySelector('.journey-plan') || {}).textContent || '';
  const before = box();
  let replans = 0;
  Lib.planJourney = async () => { replans += 1; return fresh; };
  Lib.fillJourneyShapes = async () => {};
  Lib.journeyMapSegments = (j) => (j && j.legs.some((l) => l.shape)
    ? [{ walk: false, color: '#009EE3', shape: j.legs[0].shape, from: 'A', to: 'B' }] : []);
  await pc._openJourneyMap();
  await settleTicks();
  check('a replan from the journey map redraws the card’s journey box',
    replans === 1 && pc._journey === fresh && /OLD7/.test(before) && /NEW9/.test(box()) && !/OLD7/.test(box()),
    JSON.stringify({ replans, before, after: box() }));
  pc._closeVehicleMap();

  delete pc._loadDests;
  let looked = 0;
  Lib.reachableDests = async () => { looked += 1; return { dests: [{ key: 'x', name: 'X' }], destRoutes: {} }; };
  pc._fillKey = async () => {};
  pc._config = Object.assign({}, pc._config, { stopId: 'way/[123456]', stopName: 'Some square', stopKind: '' });
  await pc._loadDests();
  check('an OSM way origin is a place: its destinations are not looked up',
    looked === 0 && pc._dests.length === 0, JSON.stringify({ looked, dests: pc._dests.length }));
  pc._config = Object.assign({}, pc._config, { stopId: 'BKK_F00001', stopName: 'A' });
  await pc._loadDests();
  check('a stop origin still looks its destinations up', looked === 1 && pc._dests.length === 1, String(looked));
  Object.assign(Lib, keep);
  pc.remove();
}


/* ---- Seventh review: planner and card library ---- */

/* A swap while the journey map's replan is in flight: the old pair's plan
   must not replace the new pair's journey, paint, or open a map. */
{
  const keep = {
    planJourney: Lib.planJourney,
    fillJourneyShapes: Lib.fillJourneyShapes,
    journeyMapSegments: Lib.journeyMapSegments,
  };
  const layer = () => ({ addTo() { return this; } });
  const L = {
    map: () => ({
      setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {},
    }),
    tileLayer: () => layer(),
    polyline: () => layer(),
    marker: () => layer(),
    divIcon: (o) => o,
    latLngBounds: (pts) => ({ pts }),
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  pc._ensureLeaflet = async () => L;
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_F00001', stopName: 'A', destKey: 'b', destName: 'B', destStopId: 'BKK_F00002' });
  pc._rows = [];
  const mk = (label) => ({ durationMin: 10, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label, color: '009EE3', minutes: 10, from: 'x', to: 'y' }] });
  pc._journey = mk('NOSHAPE7');
  pc._paint();
  await settleTicks();
  Lib.fillJourneyShapes = async () => {};
  Lib.journeyMapSegments = () => [];
  let release = () => {};
  const calls = [];
  Lib.planJourney = (_k, o, d) => {
    calls.push(o.name + '->' + d.name);
    if (o.name === 'A') return new Promise((r) => { release = () => r(mk('OLD_A_TO_B')); });
    return Promise.resolve(mk('NEW_B_TO_A'));
  };
  const opening = pc._openJourneyMap();
  await settleTicks();
  pc._swapPlan();
  pc._considerJourney();
  await settleTicks();
  const box = () => (pc.shadowRoot.querySelector('.journey-plan') || {}).textContent || '';
  const mid = box();
  release();
  await opening;
  await settleTicks();
  const after = box();
  check('a swap during the journey map’s replan keeps the new pair’s plan: no old box, no old map',
    /NEW_B_TO_A/.test(mid) && /NEW_B_TO_A/.test(after) && !/OLD_A_TO_B/.test(after)
    && pc._journey && pc._journey.legs[0].label === 'NEW_B_TO_A' && !pc._mapOverlay && pc._openingMap === '',
    JSON.stringify({ calls, mid, after, label: pc._journey && pc._journey.legs[0].label, overlay: !!pc._mapOverlay }));
  pc._closeVehicleMap();
  Object.assign(Lib, keep);
  pc.remove();
}

/* A city feed's destination with a place takes only a station by that
   place: Szeged's station 4.8 km from "Kiskundorozsma vasútállomás" is no
   train to it. */
{
  const keep = { cityIndex: Lib.cityIndex, searchStops: Lib.searchStops, elviraBetween: Lib.elviraBetween };
  const day = Lib.volanDay(0);
  const m = day.mins;
  const idx = {
    c: [['1111111', '20000101', '20991231']],
    ops: [{ i: 0, id: 'hodmezovasarhely', name: 'Hódmezővásárhely — helyi' }, { i: 1, id: 'szeged', name: 'Szeged — SZKT' }],
    byNum: new Map(),
    byKey: new Map(),
    stops: [
      ['hodmezovasarhely:1', 'Kossuth tér', 46.418, 20.33, 0],
      ['hodmezovasarhely:2', 'Népkert vasútállomás', 46.40532, 20.31473, 0],
      ['szeged:9', 'Kiskundorozsma vasútállomás', 46.27058, 20.08917, 1],
    ].map(([id, name, lat, lon, op], i) => ({ i, op, id, name, key: Lib.norm(name), fold: Lib.fold(name), lat, lon })),
    t: [[0, '12', [0, 1], [m + 10, m + 20]]],
  };
  Lib.cityIndex = async () => idx;
  const stations = [
    { id: 'BKK_005517939', name: 'Szeged', lat: 46.2405, lon: 20.1424 },
    { id: 'BKK_005517921', name: 'Szeged-Rókus', lat: 46.2655, lon: 20.1270 },
    { id: 'BKK_005517962', name: 'Kiskundorozsma', lat: 46.2707, lon: 20.0890 },
    { id: 'BKK_005517525', name: 'Hódmezővásárhely-Népkert', lat: 46.4054, lon: 20.3150 },
  ];
  /* FUTÁR-like: every word of the query is in the name. */
  const allWords = async (_k, q) => {
    const words = Lib.fold(q).split(' ').filter((w) => w.length >= 3 && w !== 'vasutallomas');
    return stations.filter((st) => words.every((w) => Lib.fold(st.name).includes(w)));
  };
  let asked = [];
  Lib.elviraBetween = async (_h, from, to) => {
    asked.push(to);
    return [{ label: 'S 100', dep: day.base + (m + 40) * 60, travel: 25, head: 'Szeged', destHit: true }];
  };
  const origin = { id: 'hodmezovasarhely:1', name: 'Kossuth tér' };
  const dest = { id: 'szeged:9', name: 'Kiskundorozsma vasútállomás', lat: 46.27058, lon: 20.08917 };
  const legsOf = (j) => j && j.legs.map((l) => (l.walk ? 'walk' : l.label) + '>' + l.to);
  /* Only the town's stations in the answers (the hub's own name finds
     nothing): no train, not Szeged's. */
  Lib.searchStops = async (k, q) => (await allWords(k, q)).filter((st) => /^Szeged/.test(st.name));
  const far = await Lib.cityRailJourney('k', { states: {} }, origin, dest);
  /* The same place with its own station in the answer: that one. */
  asked = [];
  Lib.searchStops = async () => stations;
  const near = await Lib.cityRailJourney('k', { states: {} }, origin, dest);
  check('a destination’s town station km off its place is not its train (Kiskundorozsma, not Szeged)',
    far === null, JSON.stringify(legsOf(far)));
  check('a station by the destination’s place still is',
    !!near && asked.length > 0 && asked.every((to) => to === '005517962'),
    JSON.stringify({ legs: legsOf(near), asked }));
  /* FUTÁR's search wants every word: "Szeged Kiskundorozsma vasútállomás"
     and "Szeged" never name Kiskundorozsma. The hub's own name does. */
  asked = [];
  const queries = [];
  Lib.searchStops = async (k, q) => { queries.push(q); return allWords(k, q); };
  const byOwnName = await Lib.cityRailJourney('k', { states: {} }, origin, dest);
  check('a destination station named for its own place is found by that name (Kiskundorozsma vasútállomás)',
    !!byOwnName && asked.length > 0 && asked.every((to) => to === '005517962')
    && queries.indexOf('Kiskundorozsma vasútállomás') >= 0,
    JSON.stringify({ legs: legsOf(byOwnName), asked, queries }));
  Object.assign(Lib, keep);
}

/* A sensor train with another number is another train, whatever its time
   and route: 2620 does not take 2621's platform, position or trip. */
{
  const dep = Math.floor(Date.now() / 1000) + 600;
  const row = { trainNumber: '2620', label: 'S70', sched: dep, dep };
  const other = { trainNumber: '2621', attime: Lib.hm(dep), routeid: 'S70', platform: '3', lat: 47.5, lon: 19.1 };
  const own = Object.assign({}, other, { trainNumber: '2620', platform: '5' });
  const bare = Object.assign({}, other, { trainNumber: '', platform: '6' });
  check('a sensor train with another number does not match a numbered row',
    Lib.matchHassVehicle([other], row) === null, JSON.stringify(Lib.matchHassVehicle([other], row)));
  check('its own number, or none, by time and route still matches',
    Lib.matchHassVehicle([other, own], row) === own && Lib.matchHassVehicle([bare], row) === bare,
    JSON.stringify([Lib.matchHassVehicle([other, own], row), Lib.matchHassVehicle([bare], row)]));
}

/* A FUTÁR error is no answer: the station code is looked for again, not
   kept as "none" for ten minutes. */
{
  const keep = { fetch: Lib.fetch, isMavStop: Lib.isMavStop };
  const calls = [];
  let fail = true;
  Lib.fetch = async (_k, _path, params) => {
    calls.push(params && params.query);
    if (fail) throw new Error('BKK timeout');
    return { data: { references: { stops: { BKK_005517228: { id: 'BKK_005517228', name: 'Szeged', type: 'RAIL', vehicleType: 'RAIL' } } } } };
  };
  Lib.isMavStop = () => true;
  const c1 = await Lib.elviraResolveCode('KEY', 'szeged:7123', 'Szegedx7');
  fail = false;
  calls.length = 0;
  const c2 = await Lib.elviraResolveCode('KEY', 'szeged:7123', 'Szegedx7');
  check('an ELVIRA code search that failed is asked again once FUTÁR answers',
    c1 === '' && c2 === '005517228' && calls.length > 0, JSON.stringify({ c1, c2, calls }));
  /* A real "no such station" is still kept. */
  Lib.fetch = async (_k, _path, params) => { calls.push(params && params.query); return { data: { references: { stops: {} } } }; };
  await Lib.elviraResolveCode('KEY', 'szeged:7124', 'Nowherex7');
  calls.length = 0;
  await Lib.elviraResolveCode('KEY', 'szeged:7124', 'Nowherex7');
  check('a station FUTÁR does not know is still kept as none', calls.length === 0, JSON.stringify(calls));
  Object.assign(Lib, keep);
}

/* At the shortest poll (20 s) every poll gets a new board, not every
   second one the previous. */
{
  const keep = { fetch: Lib.fetch, now: Date.now };
  let fetches = 0;
  let now = keep.now();
  const base = now;
  Date.now = () => now;
  Lib.fetch = async () => { fetches += 1; now += 800; return { n: fetches }; };
  const seen = [];
  try {
    for (let i = 0; i < 6; i++) {
      now = base + i * 20000;
      const d = await Lib.departureBoard('', 'BKK_F7TTL', 60);
      seen.push(d.n);
    }
  } finally {
    Date.now = keep.now;
    Lib.fetch = keep.fetch;
  }
  check('a 20 s poll gets a new departure board every time', seen.join(',') === '1,2,3,4,5,6' && fetches === 6,
    seen.join(','));
}

/* A case-only alias is the same FUTÁR search again; and a failed alias
   request does not take back what the typed name found. */
{
  const keep = { fetch: Lib.fetch, isMavStop: Lib.isMavStop };
  check('aliases differ from the name by more than case',
    Lib.aliasQueries('Deák').length === 0 && Lib.aliasQueries('Szeged').length === 0
    && Lib.aliasQueries('Keleti pályaudvar').join('|') === 'Budapest-Keleti|keleti'
    && Lib.aliasQueries('Szeged vasútállomás').join('|') === 'szeged',
    JSON.stringify([Lib.aliasQueries('Deák'), Lib.aliasQueries('Szeged'), Lib.aliasQueries('Keleti pályaudvar'),
      Lib.aliasQueries('Szeged vasútállomás')]));
  const asked = [];
  Lib.fetch = async (_k, _path, params) => {
    asked.push(params.query);
    if (asked.length > 1) throw Object.assign(new Error('rate limited'), { code: 'futar_rate_limited' });
    return { data: { references: { stops: { BKK_005510017: { id: 'BKK_005510017', name: 'Budapest-Keleti', type: 'RAIL', vehicleType: 'RAIL' } } } } };
  };
  Lib.isMavStop = () => true;
  let hits = null;
  let err = '';
  try {
    hits = await Lib.searchStops('k', 'Keleti pályaudvar', 'mav');
  } catch (e) {
    err = String(e && e.message);
  }
  check('a failed alias search keeps the typed name’s hits',
    asked.length === 2 && !!hits && hits.some((h) => h.id === 'BKK_005510017'),
    JSON.stringify({ asked, hits: hits && hits.map((h) => h.id), err }));
  asked.length = 0;
  Lib.fetch = async () => { asked.push(1); throw new Error('BKK timeout'); };
  err = '';
  try {
    await Lib.searchStops('k', 'Keleti pályaudvar', 'mav');
  } catch (e) {
    err = String(e && e.message);
  }
  check('a failed search for the typed name itself is still an error', /timeout/.test(err) && asked.length === 1, err);
  Object.assign(Lib, keep);
}

/* An accented capital is part of the word: the TISZA train is not one
   heading for Tiszaújváros. */
check('a named train does not match a headsign it only begins (Tisza, Tiszaújváros)',
  !Lib.matchEmmaVehicle({ label: 'Tisza' }, { trip: { tripShortName: '', tripHeadsign: 'Tiszaújváros' } })
  && Lib.matchEmmaVehicle({ label: 'Tisza' }, { trip: { tripShortName: '', tripHeadsign: 'Tisza Budapest-Keleti' } })
  && Lib.matchEmmaVehicle({ label: 'Hernád' }, { trip: { tripShortName: '', tripHeadsign: 'Hernád (Kassa)' } })
  && !Lib.matchEmmaVehicle({ label: 'Balaton' }, { trip: { tripHeadsign: 'Balatonfüred' } }));

/* A sibling card's key whose lovelace/config read failed once is looked for
   again later, not never (the card stayed on "no API key"). */
{
  const realNow = Date.now;
  let offset = 0;
  Date.now = () => realNow() + offset;
  try {
    let reads = 0;
    let down = true;
    const dashboard = { views: [{ cards: [{ type: 'custom:hungarian-transport-card', apiKey: 'sibling-key7' }] }] };
    const conn = {
      sendMessagePromise: async (msg) => {
        if (msg.type !== 'lovelace/config') return {};
        reads += 1;
        if (down) throw new Error('connection lost');
        return dashboard;
      },
    };
    const hass = { states: {}, language: 'en', connection: conn };
    const kc = document.createElement('hungarian-transport-card');
    kc._reloadSafe = () => {};
    document.body.appendChild(kc);
    kc.setConfig({ language: 'en' });
    kc.hass = hass;
    await settleTicks();
    const first = kc._apiKey();
    const readsFirst = reads;
    kc.hass = Object.assign({}, hass);
    await settleTicks();
    const readsSoon = reads;
    down = false;
    offset = 61 * 1000;
    kc.hass = Object.assign({}, hass);
    await settleTicks();
    check('a dashboard key read that failed is tried again later, not on every state change',
      first === '' && readsFirst > 0 && readsSoon === readsFirst && kc._apiKey() === 'sibling-key7',
      JSON.stringify({ first, readsFirst, readsSoon, reads, key: kc._apiKey() }));
    kc.remove();
  } finally {
    Date.now = realNow;
    Lib._hass = null;
  }
}

/* ---- Eighth review: cards, planner and card library ---- */

/* A FUTÁR IC whose only number is its trip id (BKK_8624_67, really IC 862)
   still takes the sensor's train 862: platform, seat marks and position
   before ELVIRA answers, or when it does not. */
{
  const dep = Math.floor(Date.now() / 1000) + 600;
  const row = { tripId: 'BKK_8624_67', label: 'IC', sched: dep, dep, trainNumber: '' };
  const sameTrip = { tripId: 'BKK_8624_67', trainNumber: '862', attime: Lib.hm(dep), routeid: 'IC', platform: '7', booking: true };
  const byTime = { trainNumber: '862', attime: Lib.hm(dep), routeid: 'IC', platform: '8' };
  const otherTime = { trainNumber: '862', attime: Lib.hm(dep + 3600), routeid: 'IC', platform: '9' };
  const rows = [Object.assign({}, row)];
  Lib.enrichFromHass({ states: { 'sensor.eighth_ic': { attributes: { vehicles: [sameTrip] } } } }, rows);
  check('a sensor train with the trip id’s shorter number is the same train (BKK_8624_67 is IC 862)',
    Lib.matchHassVehicle([sameTrip], row) === sameTrip && Lib.matchHassVehicle([byTime], row) === byTime
    && rows[0].platform === '7' && rows[0].booking === true,
    JSON.stringify({ trip: Lib.matchHassVehicle([sameTrip], row), time: Lib.matchHassVehicle([byTime], row), row: rows[0] }));
  /* The shorter reading alone is no match: a 862 an hour off is not it. */
  const numbered = { trainNumber: '2620', label: 'S70', tripId: 'BKK_26201_9', sched: dep, dep };
  const sameTripOtherNum = { tripId: 'BKK_26201_9', trainNumber: '2621', attime: Lib.hm(dep), routeid: 'S70' };
  check('the shorter number needs the time; the same trip id is never another train',
    Lib.matchHassVehicle([otherTime], row) === null
    && Lib.matchHassVehicle([sameTripOtherNum], numbered) === sameTripOtherNum,
    JSON.stringify([Lib.matchHassVehicle([otherTime], row), Lib.matchHassVehicle([sameTripOtherNum], numbered)]));
}

/* A hub with a place takes a station by name only near it: a tram to
   Hódmezővásárhely's station is no 6-minute walk from Szeged's, 20 km off.
   Its own name finds its own station. */
{
  const keep = { cityIndex: Lib.cityIndex, searchStops: Lib.searchStops, elviraBetween: Lib.elviraBetween };
  const day = Lib.volanDay(0);
  const m = day.mins;
  const idx = {
    c: [['1111111', '20000101', '20991231']],
    ops: [{ i: 0, id: 'szeged', name: 'Szeged — SZKT' }],
    byNum: new Map(),
    byKey: new Map(),
    stops: [
      ['szeged:31', 'Hősök tere', 46.4214, 20.3297, 0],
      ['szeged:32', 'Hódmezővásárhely vasútállomás', 46.4170, 20.3360, 0],
    ].map(([id, name, lat, lon, op], i) => ({ i, op, id, name, key: Lib.norm(name), fold: Lib.fold(name), lat, lon })),
    t: [[0, '20', [0, 1], [m + 10, m + 16]]],
  };
  Lib.cityIndex = async () => idx;
  const town = [
    { id: 'BKK_005517939', name: 'Szeged', lat: 46.2405, lon: 20.1424 },
    { id: 'BKK_005517921', name: 'Szeged-Rókus', lat: 46.2655, lon: 20.1270 },
  ];
  const own = { id: 'BKK_005517526', name: 'Hódmezővásárhely', lat: 46.4176, lon: 20.3368 };
  const search = (stations) => async (_k, q) => {
    const words = Lib.fold(q).split(' ').filter((w) => w.length >= 3 && w !== 'vasutallomas');
    return stations.filter((st) => words.every((w) => Lib.fold(st.name).includes(w)));
  };
  let asked = [];
  Lib.elviraBetween = async (_h, from, to) => {
    asked.push(from + '>' + to);
    return [{ label: 'IC', dep: day.base + (m + 40) * 60, travel: 150, head: 'Budapest-Nyugati', destHit: true }];
  };
  const origin = { id: 'szeged:31', name: 'Hősök tere' };
  const dest = { id: 'BKK_005510009', name: 'Budapest-Nyugati' };
  const legsOf = (j) => j && j.legs.map((l) => (l.walk ? 'walk' : l.label) + '>' + l.to);
  Lib.searchStops = search(town);
  const far = await Lib.cityRailJourney('k', { states: {} }, origin, dest);
  const hub = { hubName: 'Hódmezővásárhely vasútállomás', hubFold: Lib.fold('Hódmezővásárhely vasútállomás'), hubLat: 46.4170, hubLon: 20.3360 };
  const farStation = await Lib.railStationAtHub('k', 'Szeged', hub);
  check('a hub’s station by the town’s name is not one 20 km off (Hódmezővásárhely, not Szeged)',
    far === null && farStation === null,
    JSON.stringify({ legs: legsOf(far), station: farStation && farStation.name }));
  asked = [];
  Lib.searchStops = search(town.concat([own]));
  const near = await Lib.cityRailJourney('k', { states: {} }, origin, dest);
  const walk = near && near.legs.find((l) => l.stationWalk);
  check('the hub’s own name finds the station by it',
    !!walk && walk.to === 'Hódmezővásárhely' && asked.length > 0 && asked.every((a) => a.startsWith('BKK_005517526>')),
    JSON.stringify({ legs: legsOf(near), asked }));
  Object.assign(Lib, keep);
}

/* A reload of the same pair (a key showing up, back on the view, a new time
   window) while the journey map's replan runs is not a new pair: the map
   still opens with the replanned journey. */
{
  const keep = {
    planJourney: Lib.planJourney,
    fillJourneyShapes: Lib.fillJourneyShapes,
    journeyMapSegments: Lib.journeyMapSegments,
  };
  const layer = () => ({ addTo() { return this; } });
  const L = {
    map: () => ({
      setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {},
    }),
    tileLayer: () => layer(),
    polyline: () => layer(),
    marker: () => layer(),
    divIcon: (o) => o,
    latLngBounds: (pts) => ({ pts }),
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  pc._reloadSafe = () => {};
  pc._loadDests = async () => {};
  pc._ensureLeaflet = async () => L;
  document.body.appendChild(pc);
  pc.setConfig({ language: 'hu', stopId: 'BKK_F00001', stopName: 'A', destKey: 'b', destName: 'B', destStopId: 'BKK_F00002' });
  await settleTicks();
  const mk = (label) => ({ durationMin: 10, transfers: 0, walkMin: 0, waitMin: 0, legs: [{ walk: false, label, color: '009EE3', minutes: 10, from: 'x', to: 'y' }] });
  pc._journey = mk('NOSHAPE8');
  Lib.fillJourneyShapes = async () => {};
  Lib.journeyMapSegments = (j) => (j && j.legs[0].label === 'REPLANNED8'
    ? [{ walk: false, color: '#000', shape: [[47.5, 19], [47.6, 19.1]] }] : []);
  let release = () => {};
  Lib.planJourney = () => new Promise((r) => { release = () => r(mk('REPLANNED8')); });
  const notices = [];
  pc._showMapNotice = (msg) => notices.push(msg);
  const opening = pc._openJourneyMap();
  await settleTicks();
  /* What every hop-card _reload does first. */
  pc._gen += 1;
  release();
  await opening;
  await settleTicks();
  check('a same-pair reload during the journey map’s replan still opens the map with the replanned journey',
    !!pc._mapOverlay && pc._journey && pc._journey.legs[0].label === 'REPLANNED8' && !notices.length,
    JSON.stringify({ overlay: !!pc._mapOverlay, opening: pc._openingMap, notices, label: pc._journey && pc._journey.legs[0].label }));
  pc._closeVehicleMap();
  Object.assign(Lib, keep);
  pc.remove();
}

/* "Now" mode: a failed journey plan over an empty board does not leave the
   planner (which does not poll) on "Loading…". */
{
  const keep = {
    departures: Lib.departures, planJourney: Lib.planJourney, fillTransitousIfEmpty: Lib.fillTransitousIfEmpty,
  };
  let calls = 0;
  Lib.departures = async () => [];
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  Lib.planJourney = async () => {
    calls += 1;
    await new Promise((r) => setTimeout(r, 20));
    throw new Error('plan failed');
  };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  pc.hass = { language: 'hu', states: {}, services: {}, callWS: async () => ({}), callService: async () => ({}) };
  pc.setConfig({ mode: 'volan', stopId: 'volan_1', stopName: 'A', destKey: 'b', destName: 'B', destStopId: 'volan_2' });
  await new Promise((r) => setTimeout(r, 150));
  const text = (pc.shadowRoot.textContent || '').replace(/\s+/g, ' ');
  check('a failed journey plan over an empty planner board does not stay on "Loading…"',
    calls > 0 && !pc._journeyLoading && !text.includes('Betöltés'),
    JSON.stringify({ calls, loading: pc._journeyLoading, text: text.slice(0, 160) }));
  Object.assign(Lib, keep);
  pc.remove();
}

/* Destinations loaded before the integration's FUTÁR service showed up
   (HA starting, an entry reload) load again once it does: the BKK ones. */
{
  const keep = {
    reachableDests: Lib.reachableDests, departures: Lib.departures, planJourney: Lib.planJourney,
    apiKeyFromDashboard: Lib.apiKeyFromDashboard,
  };
  const calls = [];
  Lib.reachableDests = async () => {
    calls.push(Lib.integrationHasKey(Lib._hass) ? 'futar' : 'none');
    return { dests: [], destRoutes: {} };
  };
  Lib.departures = async () => [];
  Lib.planJourney = async () => null;
  Lib.apiKeyFromDashboard = async () => '';
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  const base = { language: 'hu', states: {}, callWS: async () => ({}), callService: async () => ({}) };
  pc.setConfig({ stopId: 'BKK_F00001', stopName: 'A' });
  pc.hass = Object.assign({}, base, { services: {} });
  await new Promise((r) => setTimeout(r, 40));
  const futar = { hungarian_transport: { futar: {} } };
  pc.hass = Object.assign({}, base, { services: futar });
  await new Promise((r) => setTimeout(r, 40));
  pc.hass = Object.assign({}, base, { services: futar });
  await new Promise((r) => setTimeout(r, 40));
  check('planner destinations loaded before the FUTÁR service load again once, when it shows up',
    calls.length === 2 && calls[0] === 'none' && calls[1] === 'futar', JSON.stringify(calls));
  Object.assign(Lib, keep);
  pc.remove();
  Lib._hass = null;
}

/* The editor: an integration gone for a moment (an entry reload) and back
   probes its key again, not taken as refused with the key field left up. */
{
  const keep = { probeApiKey: Lib.probeApiKey, apiKeyFromDashboard: Lib.apiKeyFromDashboard };
  let probes = 0;
  Lib.probeApiKey = async () => { probes += 1; return { ok: true }; };
  Lib.apiKeyFromDashboard = async () => '';
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  const withSvc = { language: 'hu', states: {}, services: { hungarian_transport: { futar: {} } }, callWS: async () => ({}), callService: async () => ({}) };
  const noSvc = { language: 'hu', states: {}, services: {}, callWS: async () => ({}), callService: async () => ({}) };
  const cfg = { type: 'custom:hungarian-transport-card', mode: 'bkk', stopId: 'BKK_F00001', stopName: 'A', destKey: 'b', destName: 'B' };
  ed.hass = withSvc;
  ed.setConfig(cfg);
  await settleTicks();
  const first = { ok: ed._keyOk, probes };
  ed.hass = noSvc;
  ed.setConfig(Object.assign({}, cfg, { name: 'x' }));
  await settleTicks();
  const gone = ed._keyOk;
  ed.hass = withSvc;
  ed.setConfig(Object.assign({}, cfg, { name: 'xy' }));
  await settleTicks();
  const wrap = ed._el('apiKeyWrap');
  check('the editor probes the integration again when it comes back, and hides the key field',
    first.ok === true && first.probes === 1 && gone === false && ed._keyOk === true && probes === 2
    && !!wrap && wrap.style.display === 'none',
    JSON.stringify({ first, gone, ok: ed._keyOk, probes, display: wrap && wrap.style.display }));
  Object.assign(Lib, keep);
  ed.remove();
}

/* Two FUTÁR trips with the same line in the same minute are two rows, each
   with its own trip id and headsign. */
{
  const at = Math.floor(Date.now() / 1000) + 600;
  const a = { tripId: 'BKK_A1', label: '7', head: 'Bosnyák tér', dep: at, sched: at, color: '#009EE3', vehicle: 'bus' };
  const b = { tripId: 'BKK_B2', label: '7', head: 'Albertfalva', dep: at, sched: at, color: '#009EE3', vehicle: 'bus' };
  const out = Lib.mergeVolanRows([a, b], [], 12);
  const pairs = out.map((r) => r.tripId + ' ' + r.head).sort();
  check('two FUTÁR trips of one line in the same minute stay two rows with their own headsigns',
    out.length === 2 && pairs[0] === 'BKK_A1 Bosnyák tér' && pairs[1] === 'BKK_B2 Albertfalva',
    JSON.stringify(pairs));
}

/* A data edit with a borrowed sibling key: setConfig reloads once; the next
   hass update finding the same key again does not reload a second time. */
{
  const dashboard = { views: [{ cards: [{ type: 'custom:hungarian-transport-card', apiKey: 'sibling-key8' }] }] };
  const conn = { sendMessagePromise: async (msg) => (msg.type === 'lovelace/config' ? dashboard : {}) };
  const hass = { states: {}, language: 'en', connection: conn, services: {} };
  const kc = document.createElement('hungarian-transport-card');
  let reloads = 0;
  kc._reload = async function () { reloads += 1; this._reloadActive = false; };
  document.body.appendChild(kc);
  kc.setConfig({ language: 'en', stopId: 'BKK_F1', stopName: 'A', destKey: 'b', destName: 'B' });
  kc.hass = hass;
  await settleTicks(10);
  const borrowed = kc._apiKey();
  reloads = 0;
  kc.setConfig({ language: 'en', stopId: 'BKK_F2', stopName: 'A2', destKey: 'b', destName: 'B' });
  await settleTicks(10);
  const afterEdit = reloads;
  kc.hass = Object.assign({}, hass);
  await settleTicks(10);
  check('a data edit with a borrowed key reloads once, not again on the next hass update',
    borrowed === 'sibling-key8' && afterEdit === 1 && reloads === 1,
    JSON.stringify({ borrowed, afterEdit, reloads }));
  kc.remove();
  Lib._hass = null;
}

/* The editor hands the planner a new pair (every stop pick is a setConfig):
   the old pair's itinerary must not stand under the new title while the new
   board is still loading, and a journey map still opening for the old pair
   must not plan or paint it. */
{
  const keep = { dep: Lib.departures, plan: Lib.planJourney, fill: Lib.fillTransitousIfEmpty, shapes: Lib.fillJourneyShapes };
  const pc = document.createElement('hungarian-transit-stop-card-plan');
  document.body.appendChild(pc);
  pc.hass = { states: {}, language: 'hu', connection: null, services: {} };
  let release = () => {};
  Lib.departures = () => new Promise((res) => { release = res; });
  Lib.planJourney = async () => null;
  Lib.fillTransitousIfEmpty = async (rows) => rows;
  pc._loadDests = async () => {};
  pc.setConfig({ language: 'hu', stopId: 'BKK_A', stopName: 'Alpha', destKey: 'beta', destName: 'Beta', destStopId: 'BKK_B' });
  await settleTicks();
  const ride = { walk: false, minutes: 10, meters: 0, label: 'OLDLINE', headsign: 'Beta', from: 'Alpha', to: 'Beta', color: '', text: '' };
  pc._journey = { durationMin: 10, transfers: 0, walkMin: 0, waitMin: 0, legs: [ride] };
  pc._journeyKey = 'old';
  pc._paint();
  const shownBefore = pc.shadowRoot.innerHTML.includes('OLDLINE');
  const gen0 = pc._pairGen;
  pc.setConfig({ language: 'hu', stopId: 'BKK_C', stopName: 'Gamma', destKey: 'delta', destName: 'Delta', destStopId: 'BKK_D' });
  await settleTicks();
  check('a new pair from the editor drops the old itinerary at once',
    shownBefore && !pc.shadowRoot.innerHTML.includes('OLDLINE') && pc._journey === null && pc._pairGen === gen0 + 1,
    JSON.stringify({ shownBefore, journey: !!pc._journey, gen: pc._pairGen - gen0 }));
  const gen1 = pc._pairGen;
  pc.setConfig({ language: 'hu', name: 'Only a title', stopId: 'BKK_C', stopName: 'Gamma', destKey: 'delta', destName: 'Delta', destStopId: 'BKK_D' });
  await settleTicks();
  check('a title edit keeps the plan state', pc._pairGen === gen1, String(pc._pairGen - gen1));
  release([]);
  await settleTicks();

  /* "Mikor" changed while the journey map was still loading its shapes: the
     plan it was drawing is gone, so no map opens (one for the new plan can). */
  pc._journey = { durationMin: 10, transfers: 0, walkMin: 0, waitMin: 0, legs: [ride] };
  const shapes = deferred();
  Lib.fillJourneyShapes = () => shapes.p;
  pc._ensureLeaflet = async () => { throw new Error('must not get this far'); };
  const opening = pc._openJourneyMap();
  pc._applyWhen({ mode: 'depart', at: Date.now() + 3600 * 1000 });
  shapes.resolve();
  await opening;
  await settleTicks();
  check('a "Mikor" change while the journey map opens leaves no map of a dropped plan',
    !document.querySelector('.ht-map-overlay') && pc._openingMap === '' && pc._journey === null,
    JSON.stringify({ overlay: !!document.querySelector('.ht-map-overlay'), opening: pc._openingMap }));
  release([]);
  await settleTicks();
  pc.remove();
  Object.assign(Lib, { departures: keep.dep, planJourney: keep.plan, fillTransitousIfEmpty: keep.fill, fillJourneyShapes: keep.shapes });
}

/* The editor's destination pick: FUTÁR keys every town's bus station as
   "aut all"; saved as destKey, it matched the first bus station on any trip.
   The town stays in the key, the routes come from the list's own key. */
{
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._loadDests = async () => {};
  let last = null;
  ed.addEventListener('config-changed', (ev) => { last = ev.detail.config; });
  ed.setConfig({ language: 'hu', volan: true, stopId: 'BKK_NEP', stopName: 'Népliget' });
  ed._destRoutes = { 'aut all': ['BKK_1205'] };
  ed._pickStop({ id: 'BKK_GY', key: 'aut all', name: 'Győr, autóbusz-állomás' }, 'dest');
  check('the editor keeps the town in a bus-station destination key, with the list key’s routes',
    last && last.destKey === Lib.norm('Győr, autóbusz-állomás') && last.destKey !== 'aut all'
    && JSON.stringify(last.routeIds) === '["BKK_1205"]',
    JSON.stringify(last));
  check('that key no longer matches another town’s bus station',
    !Lib.nameEq('Tatabánya, autóbusz-állomás', last.destKey) && Lib.nameEq('Győr, autóbusz-állomás', last.destKey),
    last.destKey);
  ed.remove();
}

/* A failed destination load is not retried on every keystroke in the name field. */
{
  const keep = Lib.reachableDests;
  let calls = 0;
  Lib.reachableDests = async () => { calls += 1; throw new Error('FUTÁR down'); };
  const ed = document.createElement('hungarian-transport-card-editor');
  document.body.appendChild(ed);
  ed.hass = { states: {}, language: 'hu' };
  ed._probeKey = () => {};
  ed._canLoadDests = () => true;
  ed.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr' });
  await settleTicks();
  const first = calls;
  ed.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'M' });
  ed.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Győr', name: 'Mu' });
  await settleTicks();
  const afterTyping = calls;
  ed.setConfig({ language: 'hu', volan: true, stopId: 'volan_2', stopName: 'Pápa', name: 'Mu' });
  await settleTicks();
  check('a failed destination load is tried once per stop, not once per keystroke',
    first === 1 && afterTyping === 1 && calls === 2, JSON.stringify({ first, afterTyping, calls }));
  ed.remove();
  Lib.reachableDests = keep;
}

/* A MOTIS bus leg without a route colour is not painted in the rail blue. */
{
  const j = Lib.motisJourney({ startTime: '2026-10-19T06:00:00Z', legs: [
    { mode: 'BUS', routeShortName: '7', from: { name: 'A' }, to: { name: 'B' }, startTime: '2026-10-19T06:05:00Z', endTime: '2026-10-19T06:25:00Z', duration: 1200 },
    { mode: 'RAIL', routeShortName: '905', from: { name: 'B' }, to: { name: 'C' }, startTime: '2026-10-19T06:30:00Z', endTime: '2026-10-19T07:25:00Z', duration: 3300 },
  ] });
  check('a bus leg without a colour keeps none, a train leg gets the rail default',
    j && j.legs.length === 2 && j.legs[0].color === '' && j.legs[1].color.toLowerCase() === '4477aa',
    JSON.stringify(j && j.legs.map((l) => l.color)));
}

/* The timetable position: between the last stop left and the next, along
   the shape; standing at a stop while dwelling; none before the first
   departure or from the last arrival on. */
{
  const shape = [[47.50, 19.00], [47.50, 19.10], [47.50, 19.20]];
  const timed = [
    { pt: [47.50, 19.00], arr: 1000, dep: 1000 },
    { pt: [47.50, 19.10], arr: 1600, dep: 1660 },
    { pt: [47.50, 19.20], arr: 2260, dep: 2260 },
  ];
  const mid = Lib.estimateShapePosition(shape, timed, 1300);
  const dwell = Lib.estimateShapePosition(shape, timed, 1630);
  const later = Lib.estimateShapePosition(shape, timed, 1960);
  check('the timetable position is half way along the first run at half its time',
    !!mid && Math.abs(mid.pt[1] - 19.05) < 0.002 && mid.from === 0 && mid.to === 1, JSON.stringify(mid));
  check('while dwelling the vehicle stands at the stop', !!dwell && dwell.pt[1] === 19.10, JSON.stringify(dwell));
  check('the second run is measured from its own departure',
    !!later && Math.abs(later.pt[1] - 19.15) < 0.002 && later.from === 1, JSON.stringify(later));
  const waiting = Lib.estimateShapePosition(shape, timed, 999);
  check('before the first departure the vehicle waits at its first stop; none from the last arrival on',
    !!waiting && waiting.waiting === true && waiting.pt[1] === 19.00 && Lib.estimateShapePosition(shape, timed, 2260) === null,
    JSON.stringify(waiting));
  /* A stop the shape does not pass (another line's geometry): the straight
     line between the stops, not a point on the far shape. */
  const off = Lib.estimateShapePosition([[46.0, 18.0], [46.0, 18.3]], timed, 1300);
  check('stops off the shape fall back to the line between them',
    !!off && Math.abs(off.pt[0] - 47.5) < 1e-9 && Math.abs(off.pt[1] - 19.05) < 1e-9, JSON.stringify(off));
  /* A shape drawn the other way round still gives a position. */
  const rev = Lib.estimateShapePosition(shape.slice().reverse(), timed, 1300);
  check('a reversed shape still gives the position between the stops', !!rev && Math.abs(rev.pt[1] - 19.05) < 0.002, JSON.stringify(rev));
  check('GTFS geometry carries the trip minutes and day base for the estimate',
    JSON.stringify(Lib.gtfsTimedStops({ stops: [[47.5, 19.0], null, [47.5, 19.2]], mins: [600, 605, 620], base: 1000 }))
      === JSON.stringify([{ pt: [47.5, 19.0], arr: 37000, dep: 37000 }, { pt: [47.5, 19.2], arr: 38200, dep: 38200 }]));
}

/* A row without a reported position gets the timetable's, as a dashed
   marker with the footer saying so; a poll moves it, and a reported
   position replaces it. */
{
  const made = [];
  const layer = () => ({ addTo() { return this; }, setLatLng(p) { this.pt = p; }, setIcon(i) { this.html = i.html; }, redraw() {} });
  const fakeMap = { setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {} };
  const fakeL = {
    map: () => fakeMap, tileLayer: () => layer(), divIcon: (o) => o,
    marker: (pt, o) => { const m = Object.assign(layer(), { pt, html: o.icon.html }); made.push(m); return m; },
    polyline: () => layer(), latLngBounds: () => ({ extend() {} }),
  };
  const prevL = window.L;
  window.L = fakeL;
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  /* The rows are set by hand below: no board load may replace them. */
  c._reloadSafe = () => {};
  c.setConfig({ language: 'hu', mav: true, stopId: 'BKK_005501024', stopName: 'Kelenföld', destKey: 'szekesfehervar', destName: 'Székesfehérvár' });
  const now = Math.floor(Date.now() / 1000);
  c._loadRowMapShape = async () => ({ shape: [[47.47, 19.02], [47.30, 18.60], [47.19, 18.41]], board: [47.47, 19.02], alight: [47.19, 18.41] });
  const asked = [];
  c._rowTimedStops = async (row) => {
    asked.push(row.tripId);
    return [
      { pt: [47.47, 19.02], arr: now - 600, dep: now - 600 },
      { pt: [47.30, 18.60], arr: now + 600, dep: now + 600 },
      { pt: [47.19, 18.41], arr: now + 1800, dep: now + 1800 },
    ];
  };
  const row = { label: 'IC', headsign: 'Szombathely', tripId: 'BKK_9051_1', trainNumber: '905', type: 'RAIL', icon: 'mdi:train', hasGps: false, lat: null, lon: null };
  c._rows = [Lib.asRow(Object.assign({ dep: now - 600, sched: now - 600, head: 'Szombathely', rawType: 'RAIL' }, row), 'Székesfehérvár', 'hu')];
  await c._openVehicleMap(row);
  const dot = made.find((m) => m.html.includes('ht-veh-dot'));
  const foot = document.querySelector('.ht-map-overlay .ht-map-foot');
  check('a train without GPS is drawn where the timetable puts it, with a dashed marker',
    asked.length === 1 && !!dot && dot.html.includes('ht-veh-dot-est') && !dot.html.includes('ht-veh-dot-live')
    && Math.abs(dot.pt[0] - 47.385) < 0.01 && Math.abs(dot.pt[1] - 18.81) < 0.02,
    JSON.stringify({ asked, pt: dot && dot.pt, html: dot && dot.html.slice(0, 60) }));
  check('the footer says the position is estimated from the timetable',
    !!foot && foot.textContent.includes('Becsült pozíció (menetrend)') && !foot.textContent.includes('Élő pozíció'),
    foot && foot.textContent);
  /* A poll: still no GPS, the marker stays (and follows the timetable). */
  c._syncOpenMap();
  check('a poll without GPS keeps the dashed marker', c._mapMarker === dot && dot.html.includes('ht-veh-dot-est'));
  /* GPS shows up: the live marker replaces the estimate for good. */
  c._rows = [Object.assign({}, c._rows[0], { hasGps: true, lat: 47.40, lon: 18.85 })];
  c._syncOpenMap();
  check('a reported position replaces the estimate',
    c._mapMarker === dot && dot.html.includes('ht-veh-dot-live') && dot.pt[0] === 47.40 && c._mapEstimate === null
    && foot.textContent.includes('Élő pozíció'),
    JSON.stringify({ html: dot.html.slice(0, 60), pt: dot.pt, est: c._mapEstimate }));
  c._closeVehicleMap();
  check('closing the map drops the estimate', c._mapEstimate === null && !c._mapMarker);
  /* A GTFS coach row: its index geometry carries the times, no FUTÁR call. */
  made.length = 0;
  const geo = { stops: [[47.47, 19.08], [47.00, 19.30]], from: 0, to: 1, mins: [Math.round((now - 300 - 60) / 60), Math.round((now + 900 - 60) / 60)], base: 60 };
  const coach = Lib.asRow({ tripId: 'gtfs:1205:20261026:600:1', label: '1205', head: 'Kecskemét', vehicle: 'coach', dep: now - 300, sched: now - 300, gtfs: geo }, 'Kecskemét', 'hu');
  c.setConfig({ language: 'hu', volan: true, stopId: 'volan_1', stopName: 'Népliget', destKey: 'kecskemet', destName: 'Kecskemét' });
  c._loadRowMapShape = async () => ({ shape: geo.stops, board: geo.stops[0], alight: geo.stops[1] });
  delete c._rowTimedStops;
  c._rows = [coach];
  await c._openVehicleMap(coach);
  const coachDot = made.find((m) => m.html.includes('ht-veh-dot'));
  check('a Volán coach without GPS is placed by its GTFS times',
    !!coachDot && coachDot.html.includes('ht-veh-dot-est') && coachDot.pt[0] < 47.47 && coachDot.pt[0] > 47.00,
    JSON.stringify(coachDot && coachDot.pt));
  c._closeVehicleMap();
  c.remove();
  window.L = prevL;
  globalThis.requestAnimationFrame = prevRaf;
}

/* The timetable position on the shapes that trip it up: an out-and-back
   line (the stop is passed twice), a hop clipped short of a stop, a stop
   beside the track, and FUTÁR's half-predicted times. */
{
  /* A→B→C and back on a lane 11 m north; the poles are on the outbound lane. */
  const back = 0.0001;
  const outAndBack = [[47.50, 19.00], [47.50, 19.10], [47.50, 19.20], [47.50 + back, 19.10], [47.50 + back, 19.00]];
  const T = 10000;
  const trip = [
    { pt: [47.50, 19.00], arr: T, dep: T }, { pt: [47.50, 19.10], arr: T + 100, dep: T + 100 },
    { pt: [47.50, 19.20], arr: T + 200, dep: T + 200 }, { pt: [47.50, 19.10], arr: T + 300, dep: T + 300 },
    { pt: [47.50, 19.00], arr: T + 400, dep: T + 400 },
  ];
  const outbound = Lib.estimateShapePosition(outAndBack, trip, T + 50);
  const returning = Lib.estimateShapePosition(outAndBack, trip, T + 350);
  check('on an out-and-back line the outbound run is drawn on the outbound lane',
    !!outbound && Math.abs(outbound.pt[1] - 19.05) < 0.002 && Math.abs(outbound.pt[0] - 47.50) < 0.00002, JSON.stringify(outbound));
  check('and the return run on the return lane, not across the whole line',
    !!returning && Math.abs(returning.pt[1] - 19.05) < 0.002 && Math.abs(returning.pt[0] - (47.50 + back)) < 0.00002, JSON.stringify(returning));
  /* The hop's shape starts at 19.10; the stop before it is 750 m west. */
  const clipped = [[47.50, 19.10], [47.50, 19.20]];
  const edge = Lib.estimateShapePosition(clipped, [{ pt: [47.50, 19.09], arr: T, dep: T }, { pt: [47.50, 19.101], arr: T + 100, dep: T + 100 }], T + 50);
  check('a stop outside the clipped hop is not clamped onto its end: the straight line between the stops',
    !!edge && Math.abs(edge.pt[1] - 19.0955) < 0.0002, JSON.stringify(edge));
  /* Stations sit 100 m beside the track: while dwelling the marker stays on the track. */
  const beside = [{ pt: [47.5009, 19.00], arr: T, dep: T }, { pt: [47.5009, 19.10], arr: T + 100, dep: T + 160 }, { pt: [47.5009, 19.20], arr: T + 260, dep: T + 260 }];
  const dwelling = Lib.estimateShapePosition([[47.50, 19.00], [47.50, 19.10], [47.50, 19.20]], beside, T + 130);
  check('a dwelling vehicle stands on the track beside its station, not on the station point',
    !!dwelling && Math.abs(dwelling.pt[0] - 47.50) < 0.00002 && Math.abs(dwelling.pt[1] - 19.10) < 0.0002, JSON.stringify(dwelling));
  /* FUTÁR: a predicted arrival with a scheduled departure before it. */
  const keepTD = Lib.tripDetails;
  Lib.tripDetails = async () => ({
    sts: [
      { stopId: 'S1', departureTime: T, arrivalTime: T },
      { stopId: 'S2', arrivalTime: T + 100, departureTime: T + 120, predictedArrivalTime: T + 300 },
      { stopId: 'S3', arrivalTime: T + 400, departureTime: T + 400 },
    ],
    stops: { S1: { lat: 47.5, lon: 19.0, name: 'A' }, S2: { lat: 47.5, lon: 19.1, name: 'B' }, S3: { lat: 47.5, lon: 19.2, name: 'C' } },
  });
  const timed = await Lib.tripTimedStops('k', {}, 'BKK_T', { id: 'S2', name: 'B' });
  check('a stop with only a predicted arrival takes it for its departure too, and the boarding stop is marked',
    timed.length === 3 && timed[1].arr === T + 300 && timed[1].dep === T + 300 && timed[1].origin === true && !timed[0].origin,
    JSON.stringify(timed));
  Lib.tripDetails = keepTD;
}

/* On the card: the trip details are cached for the session, so the row's
   departure of this poll shifts the times; a row that left the board keeps
   its marker moving; a train known only from Transitous and ELVIRA is placed
   between the boarding and alighting stops. */
{
  const made = [];
  const layer = () => ({ addTo() { return this; }, setLatLng(p) { this.pt = p; }, setIcon(i) { this.html = i.html; }, redraw() {} });
  const fakeMap = { setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {}, remove() {}, on() {} };
  const fakeL = {
    map: () => fakeMap, tileLayer: () => layer(), divIcon: (o) => o,
    marker: (pt, o) => { const m = Object.assign(layer(), { pt, html: o.icon.html }); made.push(m); return m; },
    polyline: () => layer(), latLngBounds: () => ({ extend() {} }),
  };
  const prevL = window.L;
  window.L = fakeL;
  const prevRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  const now = Math.floor(Date.now() / 1000);
  const c = document.createElement('hungarian-transport-card');
  document.body.appendChild(c);
  c._reloadSafe = () => {};
  c.setConfig({ language: 'hu', mav: true, stopId: 'BKK_005501024', stopName: 'Kelenföld', destKey: 'szekesfehervar', destName: 'Székesfehérvár' });
  c._hasFutar = () => true;
  const shape = [[47.47, 19.02], [47.30, 18.60], [47.19, 18.41]];
  c._loadRowMapShape = async () => ({ shape, board: shape[0], alight: shape[2] });
  const keepTTS = Lib.tripTimedStops;
  Lib.tripTimedStops = async () => [
    { pt: shape[0], arr: now - 600, dep: now - 600, origin: true },
    { pt: shape[1], arr: now + 600, dep: now + 600 },
    { pt: shape[2], arr: now + 1800, dep: now + 1800 },
  ];
  /* Cached details say it left 10 min ago; the row says 5 min ago (5 min late). */
  const row = Lib.asRow({ tripId: 'BKK_9051_1', label: 'IC', head: 'Szombathely', rawType: 'RAIL', dep: now - 300, sched: now - 600, trainNumber: '905' }, 'Székesfehérvár', 'hu');
  const shifted = await c._rowTimedStops(row);
  check('a delay grown since the trip details were read shifts every stop by the row\u2019s departure',
    shifted.length === 3 && shifted[0].dep === now - 300 && shifted[1].arr === now + 900, JSON.stringify(shifted.map((s) => s.arr - now)));
  c._rows = [row];
  await c._openVehicleMap(row);
  const dot = made.find((m) => m.html.includes('ht-veh-dot'));
  check('the dashed marker sits a quarter of the way into the first run (5 of 20 minutes)',
    !!dot && dot.html.includes('ht-veh-dot-est') && dot.pt[0] < 47.47 && dot.pt[0] > 47.40, JSON.stringify(dot && dot.pt));
  /* The row leaves the board a minute after departure: the marker keeps running. */
  c._rows = [];
  c._syncOpenMap();
  await settleTicks();
  check('a row gone from the board keeps its dashed marker', c._mapMarker === dot && dot.html.includes('ht-veh-dot-est') && !!c._mapEstimate);
  c._closeVehicleMap();
  /* A train that has not left its first station yet: the marker waits there, and the footer says so. */
  made.length = 0;
  Lib.tripTimedStops = async () => [
    { pt: shape[0], arr: now + 600, dep: now + 600, origin: true },
    { pt: shape[1], arr: now + 1800, dep: now + 1800 },
    { pt: shape[2], arr: now + 3000, dep: now + 3000 },
  ];
  const later = Lib.asRow({ tripId: 'BKK_9051_1', label: 'IC', head: 'Szombathely', rawType: 'RAIL', dep: now + 600, sched: now + 600, trainNumber: '905' }, 'Székesfehérvár', 'hu');
  c._rows = [later];
  await c._openVehicleMap(later);
  const waitDot = made.find((m) => m.html.includes('ht-veh-dot'));
  const waitFoot = document.querySelector('.ht-map-overlay .ht-map-foot');
  check('a train not yet departed waits at its first station with a dashed marker and "Vár az indulásra"',
    !!waitDot && waitDot.html.includes('ht-veh-dot-est') && Math.abs(waitDot.pt[0] - shape[0][0]) < 0.0005
    && !!waitFoot && waitFoot.textContent.includes('Vár az indulásra (menetrend)'),
    JSON.stringify({ pt: waitDot && waitDot.pt, foot: waitFoot && waitFoot.textContent }));
  c._closeVehicleMap();
  Lib.tripTimedStops = keepTTS;
  /* ELVIRA-only: a Transitous host row with no stop list, but a departure and a travel time. */
  made.length = 0;
  const motis = Lib.asRow({ tripId: 'motis:abc', label: 'IC', head: 'Szombathely', rawType: 'RAIL', dep: now - 300, sched: now - 300, trainNumber: '905', travel: 20 }, 'Székesfehérvár', 'hu');
  c._rows = [motis];
  await c._openVehicleMap(motis);
  const motisDot = made.find((m) => m.html.includes('ht-veh-dot'));
  const foot = document.querySelector('.ht-map-overlay .ht-map-foot');
  check('a train known only from Transitous and ELVIRA is placed between its boarding and alighting stops',
    !!motisDot && motisDot.html.includes('ht-veh-dot-est') && motisDot.pt[0] < 47.47 && motisDot.pt[0] > 47.19
    && !!foot && foot.textContent.includes('Becsült pozíció (menetrend)'),
    JSON.stringify({ pt: motisDot && motisDot.pt, foot: foot && foot.textContent }));
  c._closeVehicleMap();
  c.remove();
  window.L = prevL;
  globalThis.requestAnimationFrame = prevRaf;
}

process.exit(failed ? 1 : 0);

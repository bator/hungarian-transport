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

await import('./hungarian-transport.js');

const src = await readFile('./hungarian-transport.js', 'utf8');

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
];
card._paint();

const out = html();
check('renders one row per departure', (out.match(/<tr>/g) || []).length === 2);
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
  withErr.includes('boom') && (withErr.match(/<tr>/g) || []).length === 2);

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
check('editor has look-ahead chips', chips.length === 7, String(chips.length));
let emitted = null;
editor.addEventListener('config-changed', (ev) => { emitted = ev.detail.config.minutesAfter; });
const chip180 = editor.querySelector('#minutesAfterChips .chip[data-min="180"]');
if (chip180) chip180.click();
check('chip click emits minutesAfter', emitted === 180, String(emitted));
minSel.value = '60';
minSel.dispatchEvent(new Event('change', { bubbles: true }));
check('select change emits minutesAfter', emitted === 60, String(emitted));

process.exit(failed ? 1 : 0);

/* Minimal DOM stubs so the card module can be imported outside a browser. */
class FakeEl {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this._text = '';
  }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); }
  addEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getElementById() { return null; }
  attachShadow() { this.shadowRoot = new FakeEl('shadow'); return this.shadowRoot; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._text = v; }
  get textContent() { return this._text; }
  set innerHTML(v) { this._html = v; }
  get innerHTML() { return this._html || ''; }
}

const registry = new Map();
globalThis.customElements = {
  get: (n) => registry.get(n),
  define: (n, c) => registry.set(n, c),
  whenDefined: () => Promise.resolve(),
};
globalThis.HTMLElement = class HTMLElement extends FakeEl {
  constructor() { super('x'); }
};
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  querySelectorAll: () => [],
  activeElement: null,
};
globalThis.window = globalThis;
Object.defineProperty(globalThis, 'navigator', {
  value: { language: 'en-GB' },
  configurable: true,
});

await import('./hungarian-transport.js');

const src = await (await import('node:fs/promises')).readFile('./hungarian-transport.js', 'utf8');

/* Compare the two dictionaries by re-evaluating just the I18N literal. */
const start = src.indexOf('const I18N = {');
const end = src.indexOf('\n};', start) + 3;
const I18N = eval(src.slice(start, end).replace('const I18N =', '(') .replace(/;$/, ')'));

const flat = (obj, prefix = '') => Object.entries(obj).flatMap(([k, v]) => (
  (v && typeof v === 'object' && !(v instanceof Function))
    ? flat(v, `${prefix}${k}.`)
    : [`${prefix}${k}`]
));

const hu = flat(I18N.hu).sort();
const en = flat(I18N.en).sort();
const missingEn = hu.filter((k) => !en.includes(k));
const extraEn = en.filter((k) => !hu.includes(k));

let failed = false;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failed = true;
};

check('dictionaries have identical key sets', missingEn.length === 0 && extraEn.length === 0,
  missingEn.length || extraEn.length ? `missing in en: ${missingEn}; extra in en: ${extraEn}` : `${hu.length} keys`);
check('hu mode hint mentions vonat for MAV', I18N.hu.mode.mav.hint.includes('vonat'));
check('en mode hint is English', I18N.en.mode.mav.hint === 'Pick an origin and a destination station. Every train between them is listed.',
  I18N.en.mode.mav.hint);
check('en name label', I18N.en.nameLabel === 'Name (optional)', I18N.en.nameLabel);
check('platform label differs per language',
  I18N.hu.platformShort(3) === 'vág.3' && I18N.en.platformShort(3) === 'pl.3');
check('minutes formatter', I18N.hu.minutes(7) === '7 perc' && I18N.en.minutes(7) === '7 min');

check('card element registered', !!registry.get('bkk-stop-card-r3'));
check('planner element registered', !!registry.get('bkk-stop-card-plan'));
check('editor element registered', !!registry.get('bkk-stop-card-r3-editor'));
check('card advertised to picker', (globalThis.customCards || []).length === 2,
  JSON.stringify((globalThis.customCards || []).map((c) => c.type)));

const Card = registry.get('bkk-stop-card-r3');
const stub = Card.getStubConfig();
check('stub config carries language', stub.language === 'auto', JSON.stringify(stub));

/* Built-in renderer: the card must draw its own rows when bkk-stop-card-r2 is
   absent, which is the case for a plain HACS install. */
const card = new Card();
card._config = { stopId: 'BKK_1', destKey: 'keleti', destName: 'Keleti' };
card._vehicles = [];

check('does not claim r2 when r2 is unregistered', card._useR2() === false);

const row = card._rowHtml({
  in: '4', type: 'TRAM', routeid: '59B', headsign: 'Sz\u00e9ll K\u00e1lm\u00e1n t\u00e9r',
  predicted_attime: '14:35', attime: '14:32', delay: 180,
  wheelchair: 'True', bikesAllowed: true, color: 'FFD800', textcolor: '000000',
  travelMin: 12,
}, 'en');

check('row renders the route badge', row.includes('59B') && row.includes('#FFD800'));
check('row picks the tram icon', row.includes('mdi:tram'), row.match(/mdi:[a-z-]+/g)?.join(','));
check('row shows the countdown in the chosen language', row.includes('4 min'));
check('row marks a delay', row.includes('late') && row.includes('+3'));
check('row shows accessibility icons', row.includes('mdi:wheelchair-accessibility') && row.includes('mdi:bicycle'));
check('row shows travel time', row.includes('12 min'));

const hostEl = new FakeEl('div');
card._builtin = hostEl;

card._paintBuiltin();
check('empty result reports no departures', hostEl.innerHTML.includes('No upcoming departure.'),
  hostEl.innerHTML.trim().slice(0, 60));

card._config = { language: 'hu' };
card._paintBuiltin();
check('unconfigured card prompts for stops in Hungarian',
  hostEl.innerHTML.includes('Add meg a forr\u00e1s \u00e9s a c\u00e9l meg\u00e1ll\u00f3t.'),
  hostEl.innerHTML.trim().slice(0, 60));

card._errorText = 'BKK HTTP 401';
card._paintBuiltin();
check('error is rendered instead of rows',
  hostEl.innerHTML.includes('msg err') && hostEl.innerHTML.includes('BKK HTTP 401'));

card._errorText = '';
card._vehicles = [{ in: '0', type: 'BUS', routeid: '7', headsign: '<script>x</script>' }];
card._paintBuiltin();
check('headsign is HTML-escaped', !hostEl.innerHTML.includes('<script>')
  && hostEl.innerHTML.includes('&lt;script&gt;'));
check('zero minutes renders as "most" in Hungarian', hostEl.innerHTML.includes('most'));

card._config = { renderer: 'builtin' };
check('renderer: builtin opts out of r2', card._useR2() === false);

process.exit(failed ? 1 : 0);

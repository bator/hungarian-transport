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

check('card element registered', !!registry.get('hungarian-transport-card'));
check('planner element registered', !!registry.get('hungarian-transit-stop-card-plan'));
check('planner alias still registered', !!registry.get('bkk-stop-card-plan'));
check('editor element registered', !!registry.get('hungarian-transport-card-editor'));
check('r3 alias still registered', !!registry.get('bkk-stop-card-r3'));
check('r3 editor alias still registered', !!registry.get('bkk-stop-card-r3-editor'));
check('card advertised to picker', (globalThis.customCards || []).some((c) => c.type === 'hungarian-transport-card'),
  JSON.stringify((globalThis.customCards || []).map((c) => c.type)));

const stub = registry.get('hungarian-transport-card').getStubConfig();
check('stub config carries language', stub.language === 'auto', JSON.stringify(stub));
check('stub config carries minutesAfter', stub.minutesAfter === 180, JSON.stringify(stub));

process.exit(failed ? 1 : 0);

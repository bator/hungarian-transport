/* Boot order inside Home Assistant: the integration imports the card next to
   HA's app bundle, and the app swaps window.customElements for a scoped
   registry polyfill. The cards must end up in the registry HA reads. */
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><home-assistant></home-assistant></body></html>', {
  url: 'http://localhost:8123/lovelace/0',
  pretendToBeVisual: true,
});
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'navigator',
  'CSSStyleDeclaration', 'Node', 'Event', 'CustomEvent']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true });
}
const native = dom.window.customElements;
globalThis.customElements = native;

let failed = false;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failed = true;
};

await import('./dist/hungarian-transport.js?boot');
check('cards wait while Home Assistant has not booted',
  !native.get('hungarian-transport-card') && !native.get('hungarian-transit-stop-card-plan'));

/* What the polyfill does, as far as the cards can tell: a new global
   registry whose get() only knows its own definitions. */
const own = new Map();
const shim = {
  define(name, cls) {
    if (own.has(name)) throw new Error(`${name} already defined`);
    own.set(name, cls);
    if (!native.get(name)) native.define(name, cls);
  },
  get: (name) => own.get(name),
  whenDefined: (name) => native.whenDefined(name),
};
globalThis.customElements = shim;
shim.define('home-assistant', class extends dom.window.HTMLElement {});
await new Promise((r) => setTimeout(r, 0));
check('cards register in the registry Home Assistant uses after the swap',
  !!shim.get('hungarian-transport-card') && !!shim.get('hungarian-transit-stop-card-plan')
  && !!shim.get('bkk-stop-card-r3') && !!shim.get('hungarian-transport-card-editor'),
  JSON.stringify([...own.keys()]));
check('picker entries are still advertised',
  (dom.window.customCards || globalThis.window.customCards || []).some((c) => c.type === 'hungarian-transport-card'));

process.exit(failed ? 1 : 0);

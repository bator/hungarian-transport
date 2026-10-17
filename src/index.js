/* Hungarian transport Lovelace cards. Built into dist/hungarian-transport.js
   with `npm run build`; edit these sources, not the bundle. */
import './lib/names.js';
import './lib/futar.js';
import './lib/transitous.js';
import './lib/journey.js';
import './lib/geo.js';
import './lib/dashboard-key.js';
import './lib/gtfs.js';
import './lib/services.js';
import './lib/departures.js';
import './lib/vehicles.js';
import { BKK_PLANNER_TAG, BKK_PLANNER_TAG_ALIAS, CARD_VERSION } from './config.js';
import { BKKHopCardEditor } from './editor.js';
import { BKKHopCard } from './hop-card.js';
import { BkkLib } from './lib/core.js';
import { BKKPlannerCard } from './planner-card.js';
import { BKKPlannerCardEditor } from './planner-editor.js';
import { BKK_HOP_EDITOR, BKK_HOP_EDITOR_ALIAS, BKK_HOP_TAG, BKK_HOP_TAG_ALIAS, BKK_PLANNER_EDITOR, BKK_PLANNER_EDITOR_ALIAS } from './tags.js';



/* `bkk-stop-card-r3` and its editor stay registered so dashboards written
   before the rename keep working. Aliases get their own subclass: a class
   can back only one tag per registry. */
const ELEMENTS = [
  [BKK_HOP_EDITOR, BKKHopCardEditor],
  [BKK_HOP_EDITOR_ALIAS, class extends BKKHopCardEditor {}],
  [BKK_PLANNER_EDITOR, BKKPlannerCardEditor],
  [BKK_PLANNER_EDITOR_ALIAS, class extends BKKPlannerCardEditor {}],
  [BKK_HOP_TAG, BKKHopCard],
  [BKK_HOP_TAG_ALIAS, class extends BKKHopCard {}],
  [BKK_PLANNER_TAG, BKKPlannerCard],
  [BKK_PLANNER_TAG_ALIAS, class extends BKKPlannerCard {}],
];

/* Looked up at call time on purpose: the registry may have been replaced. */
function registerElements() {
  ELEMENTS.forEach(([tag, cls]) => {
    if (customElements.get(tag)) return;
    try {
      customElements.define(tag, cls);
    } catch (err) {
      console.warn(`Hungarian transport: could not register <${tag}>`, err);
    }
  });
}

/* Home Assistant's app bundle installs a scoped custom element registry
   polyfill that replaces window.customElements. The integration loads this
   module next to that bundle at boot, so it can run first; a tag defined in
   the native registry before the swap is invisible to the polyfill's get(),
   and every card then shows "Custom element doesn't exist" (Configuration
   error) until the next lucky reload. HA defines its own root element only
   after the swap, so wait for that one. Loaded later (a Lovelace resource,
   or outside HA), register at once. */
const haRoot = typeof document !== 'undefined' && document.querySelector
  ? document.querySelector('home-assistant, hc-main')
  : null;
if (haRoot && !customElements.get(haRoot.localName)) {
  customElements.whenDefined(haRoot.localName).then(registerElements, registerElements);
} else {
  registerElements();
}

console.info(`%c HUNGARIAN-TRANSPORT %c ${CARD_VERSION} `,
  'color:#fff;background:#0f6fc6;font-weight:700',
  'color:#0f6fc6;background:#fff;font-weight:700');

globalThis.HungarianTransportLib = BkkLib;

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === BKK_HOP_TAG)) {
  window.customCards.push({
    type: BKK_HOP_TAG,
    name: 'Hungarian transport',
    description: 'BKK, M\u00c1V, Vol\u00e1n and local-city departures between two stops',
    preview: false,
  });
}
if (!window.customCards.some((c) => c.type === BKK_PLANNER_TAG)) {
  window.customCards.push({
    type: BKK_PLANNER_TAG,
    name: 'Hungarian transit stop planner',
    description: 'Pick two stops; every BKK, Vol\u00e1n and M\u00c1V service between them is listed',
    preview: false,
  });
}

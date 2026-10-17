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
   before the rename keep working. */
if (!customElements.get(BKK_HOP_EDITOR)) {
  customElements.define(BKK_HOP_EDITOR, BKKHopCardEditor);
}
if (!customElements.get(BKK_HOP_EDITOR_ALIAS)) {
  customElements.define(BKK_HOP_EDITOR_ALIAS, class extends BKKHopCardEditor {});
}
if (!customElements.get(BKK_PLANNER_EDITOR)) {
  customElements.define(BKK_PLANNER_EDITOR, BKKPlannerCardEditor);
}
if (!customElements.get(BKK_PLANNER_EDITOR_ALIAS)) {
  customElements.define(BKK_PLANNER_EDITOR_ALIAS, class extends BKKPlannerCardEditor {});
}
if (!customElements.get(BKK_HOP_TAG)) {
  customElements.define(BKK_HOP_TAG, BKKHopCard);
}
if (!customElements.get(BKK_HOP_TAG_ALIAS)) {
  customElements.define(BKK_HOP_TAG_ALIAS, class extends BKKHopCard {});
}
if (!customElements.get(BKK_PLANNER_TAG)) {
  customElements.define(BKK_PLANNER_TAG, BKKPlannerCard);
}
if (!customElements.get(BKK_PLANNER_TAG_ALIAS)) {
  customElements.define(BKK_PLANNER_TAG_ALIAS, class extends BKKPlannerCard {});
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

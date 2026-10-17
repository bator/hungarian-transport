import { BkkLib } from '../lib/core.js';
import { OWN_CARD_TYPES } from '../tags.js';

Object.assign(BkkLib, {
  /* Only a key that sits on one of our own cards may be reused. A dashboard can
     hold apiKey values belonging to entirely unrelated cards, and those must
     never end up in a request to BKK. */
  findApiKey(node, acc) {
    if (!acc) acc = [];
    if (!node || typeof node !== 'object') return acc;
    if (Array.isArray(node)) {
      node.forEach((x) => BkkLib.findApiKey(x, acc));
      return acc;
    }
    const type = String(node.type || '').replace(/^custom:/, '');
    if (typeof node.apiKey === 'string' && node.apiKey && OWN_CARD_TYPES.indexOf(type) >= 0) {
      acc.push(node.apiKey);
    }
    Object.keys(node).forEach((k) => {
      if (k === 'apiKey') return;
      BkkLib.findApiKey(node[k], acc);
    });
    return acc;
  },
  /* A dashboard usually holds several of these cards. Reuse the key already
     configured on a sibling card instead of asking for it again. Only the
     default dashboard and the one being viewed are inspected. */
  async apiKeyFromDashboard(hass) {
    if (!hass || !hass.connection) return '';
    const paths = [null];
    if (hass.panelUrl && paths.indexOf(hass.panelUrl) < 0) paths.push(hass.panelUrl);
    for (let i = 0; i < paths.length; i++) {
      try {
        const msg = { type: 'lovelace/config' };
        if (paths[i]) msg.url_path = paths[i];
        const cfg = await hass.connection.sendMessagePromise(msg);
        const keys = BkkLib.findApiKey(cfg);
        if (keys.length) return keys[0];
      } catch (_e) { /* not a Lovelace dashboard, or no access */ }
    }
    return '';
  },
});

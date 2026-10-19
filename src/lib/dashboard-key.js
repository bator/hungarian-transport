import { BkkLib } from '../lib/core.js';
import { OWN_CARD_TYPES } from '../tags.js';

Object.assign(BkkLib, {
  /* DEPRECATED card-level key (review J-3, 2026-10-19). A key in the card YAML
     is sent to BKK as a query parameter (futar.js), and any logged-in Home
     Assistant user, admin or not, can read it with the lovelace/config call
     used below. It keeps working; the recommended place is the integration
     (the hungarian_transport.futar service), whose key never leaves Home
     Assistant. The card says so once per page, without the key itself. */
  _warnedCardKey: false,
  warnCardApiKey(cfg) {
    if (BkkLib._warnedCardKey || !cfg || typeof cfg.apiKey !== 'string' || !cfg.apiKey.trim()) return false;
    BkkLib._warnedCardKey = true;
    try {
      console.warn('Hungarian transport: the card-level apiKey is deprecated. Every logged-in Home Assistant '
        + 'user can read it from the dashboard config, and it is sent to BKK in the request URL. Enter the key '
        + 'in the Hungarian transport integration instead and remove apiKey from the card.');
    } catch (_e) { /* no console */ }
    return true;
  },
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
     default dashboard and the one being viewed are inspected.
     Editors and planners ask on every hass update or keystroke while no key
     is found, and each ask is one or two full lovelace/config reads: a found
     key is kept for the page, a miss for a minute, concurrent asks share one. */
  _dashKeys: new WeakMap(),
  apiKeyFromDashboard(hass) {
    if (!hass || !hass.connection) return Promise.resolve('');
    /* Per connection (a new login is a new one) and per dashboard. */
    let byPanel = BkkLib._dashKeys.get(hass.connection);
    if (!byPanel) {
      byPanel = new Map();
      BkkLib._dashKeys.set(hass.connection, byPanel);
    }
    const memo = String(hass.panelUrl || '');
    const hit = byPanel.get(memo);
    if (hit && (hit.p || hit.key || Date.now() - hit.ts < 60000)) return hit.p || Promise.resolve(hit.key);
    const p = BkkLib._readDashboardKey(hass).then((key) => {
      byPanel.set(memo, { key, ts: Date.now() });
      return key;
    }, () => {
      byPanel.delete(memo);
      return '';
    });
    byPanel.set(memo, { p, key: '', ts: Date.now() });
    return p;
  },
  async _readDashboardKey(hass) {
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

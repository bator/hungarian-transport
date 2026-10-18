import { BkkLib } from '../lib/core.js';

/* The planner's origin suggestions are the stops this Home Assistant user
   picks, not a fixed list. They live in the user's own frontend storage
   (frontend/get_user_data), so every device of that user sees the same,
   with a localStorage copy for when the websocket call is not available. */
const HISTORY_KEY = 'hungarian_transport_planner_stops';
const HISTORY_KEEP = 24;
/* A stop used often long ago fades: its count halves every 30 days. */
const HISTORY_HALF_LIFE_DAYS = 30;

const localRead = () => {
  try {
    return JSON.parse(globalThis.localStorage.getItem(HISTORY_KEY) || '[]');
  } catch (_e) {
    return [];
  }
};

const localWrite = (list) => {
  try {
    globalThis.localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  } catch (_e) { /* private window or storage off: the HA copy still holds it */ }
};

Object.assign(BkkLib, {
  STOP_HISTORY_KEY: HISTORY_KEY,
  _stopHistory: null,
  stopHistoryScore(entry, now) {
    const ageDays = Math.max(0, ((now || Date.now()) - (Number(entry && entry.t) || 0)) / 86400000);
    return (Number(entry && entry.n) || 1) * Math.pow(0.5, ageDays / HISTORY_HALF_LIFE_DAYS);
  },
  _cleanStopHistory(list) {
    const out = [];
    const seen = new Set();
    (Array.isArray(list) ? list : []).forEach((e) => {
      if (!e || !e.id || !e.name) return;
      const id = String(e.id);
      if (seen.has(id)) return;
      seen.add(id);
      const row = { id, name: String(e.name), n: Math.max(1, Number(e.n) || 1), t: Number(e.t) || 0 };
      if (Number.isFinite(Number(e.lat)) && Number.isFinite(Number(e.lon)) && e.lat !== null && e.lon !== null) {
        row.lat = Number(e.lat);
        row.lon = Number(e.lon);
      }
      if (e.kind) row.kind = String(e.kind);
      if (e.label) row.label = String(e.label);
      out.push(row);
    });
    return out;
  },
  /* One read per connection; a new login reads again. */
  loadStopHistory(hass) {
    const conn = (hass && hass.connection) || null;
    const memo = BkkLib._stopHistory;
    if (memo && memo.conn === conn) return memo.p;
    const p = (async () => {
      let list = null;
      if (hass && typeof hass.callWS === 'function') {
        try {
          const res = await hass.callWS({ type: 'frontend/get_user_data', key: HISTORY_KEY });
          list = res && res.value;
        } catch (_e) {
          list = null;
        }
      }
      if (!Array.isArray(list)) list = localRead();
      return BkkLib._cleanStopHistory(list);
    })();
    BkkLib._stopHistory = { conn, p };
    return p;
  },
  /* Count a pick (an origin, a destination or a suggestion), keep the most
     used, and store the list for this user. */
  async recordStop(hass, stop) {
    if (!stop || !stop.id || !stop.name) return BkkLib.loadStopHistory(hass);
    const list = (await BkkLib.loadStopHistory(hass)).map((e) => Object.assign({}, e));
    const now = Date.now();
    const id = String(stop.id);
    let entry = list.find((e) => e.id === id);
    if (!entry) {
      entry = { id, name: String(stop.name), n: 0, t: 0 };
      list.push(entry);
    }
    entry.n += 1;
    entry.t = now;
    entry.name = String(stop.name);
    if (Number.isFinite(Number(stop.lat)) && Number.isFinite(Number(stop.lon)) && stop.lat !== null && stop.lon !== null) {
      entry.lat = Number(stop.lat);
      entry.lon = Number(stop.lon);
    }
    if (stop.kind) entry.kind = String(stop.kind);
    if (stop.label) entry.label = String(stop.label);
    const next = BkkLib._cleanStopHistory(list
      .sort((a, b) => BkkLib.stopHistoryScore(b, now) - BkkLib.stopHistoryScore(a, now))
      .slice(0, HISTORY_KEEP));
    BkkLib._stopHistory = { conn: (hass && hass.connection) || null, p: Promise.resolve(next) };
    localWrite(next);
    if (hass && typeof hass.callWS === 'function') {
      try {
        await hass.callWS({ type: 'frontend/set_user_data', key: HISTORY_KEY, value: next });
      } catch (_e) { /* the local copy still has it */ }
    }
    return next;
  },
  /* The most used stops first, without the one already picked. */
  frequentStops(list, opts) {
    opts = opts || {};
    const now = opts.now || Date.now();
    const skip = String(opts.excludeId || '');
    return (list || [])
      .filter((e) => e && e.id && e.id !== skip)
      .slice()
      .sort((a, b) => BkkLib.stopHistoryScore(b, now) - BkkLib.stopHistoryScore(a, now))
      .slice(0, opts.limit || 6);
  },
});

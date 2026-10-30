import { BkkLib } from '../lib/core.js';

/* The planner's origin suggestions are the stops this Home Assistant user
   picks, not a fixed list. They live in the user's own frontend storage
   (frontend/get_user_data), so every device of that user sees the same,
   with a localStorage copy for when the websocket call is not available. */
const HISTORY_KEY = 'hungarian_transport_planner_stops';
const HISTORY_KEEP = 24;
/* A stop used often long ago fades: its count halves every 30 days. */
const HISTORY_HALF_LIFE_DAYS = 30;
/* A new pick counts 1 against stops used for months: up to this many of the
   last week's picks keep a place, or the list would never take a new stop. */
const HISTORY_RECENT_DAYS = 7;
const HISTORY_RECENT_SLOTS = 8;

/* localStorage is per browser profile, while the history is per Home
   Assistant user: two users of one tablet must not see each other's stops.
   Without a known user there is no browser copy at all; the bare key older
   versions shared between users is never read, and goes on the next write. */
const localKey = (hass) => {
  const uid = hass && hass.user && hass.user.id;
  return uid ? `${HISTORY_KEY}:${uid}` : '';
};

const localRead = (hass) => {
  const key = localKey(hass);
  if (!key) return [];
  try {
    return JSON.parse(globalThis.localStorage.getItem(key) || '[]');
  } catch (_e) {
    return [];
  }
};

const localWrite = (hass, list) => {
  const key = localKey(hass);
  if (!key) return;
  try {
    globalThis.localStorage.setItem(key, JSON.stringify(list));
    globalThis.localStorage.removeItem(HISTORY_KEY);
  } catch (_e) { /* private window or storage off: the HA copy still holds it */ }
};

Object.assign(BkkLib, {
  STOP_HISTORY_KEY: HISTORY_KEY,
  stopHistoryLocalKey: localKey,
  _stopHistory: null,
  /* Writes queue up behind each other: two picks in one tick (an origin and
     a destination) must both land in the list. */
  _stopHistoryWrite: Promise.resolve(),
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
  /* The server's list for this user; null when it cannot be read (no
     websocket, or the call failed), which is not the same as empty. */
  async _readServerHistory(hass) {
    if (!hass || typeof hass.callWS !== 'function') return null;
    try {
      const res = await hass.callWS({ type: 'frontend/get_user_data', key: HISTORY_KEY });
      /* null is an answer: this user has no history yet. */
      return Array.isArray(res && res.value) ? res.value : [];
    } catch (_e) {
      return null;
    }
  },
  /* Two copies of one history (the server's and this browser's, or two
     devices'): every stop of either, each with its higher count and its
     later use. */
  _mergeStopHistory(a, b) {
    const byId = new Map();
    BkkLib._cleanStopHistory(a).concat(BkkLib._cleanStopHistory(b)).forEach((e) => {
      const prev = byId.get(e.id);
      if (!prev) {
        byId.set(e.id, e);
        return;
      }
      const newer = e.t > prev.t ? e : prev;
      byId.set(e.id, Object.assign({}, prev, newer, { n: Math.max(prev.n, e.n), t: Math.max(prev.t, e.t) }));
    });
    return Array.from(byId.values());
  },
  /* The most used stops first, at most HISTORY_KEEP of them; the latest
     picks of the past week stay even when their count is still low. */
  _rankStopHistory(list, now) {
    const at = now || Date.now();
    const ranked = BkkLib._cleanStopHistory(list.slice()
      .sort((a, b) => BkkLib.stopHistoryScore(b, at) - BkkLib.stopHistoryScore(a, at)));
    if (ranked.length <= HISTORY_KEEP) return ranked;
    const recent = new Set(ranked
      .filter((e) => at - e.t <= HISTORY_RECENT_DAYS * 86400000)
      .sort((a, b) => b.t - a.t)
      .slice(0, HISTORY_RECENT_SLOTS));
    let room = HISTORY_KEEP - recent.size;
    return ranked.filter((e) => {
      if (recent.has(e)) return true;
      if (room <= 0) return false;
      room -= 1;
      return true;
    });
  },
  /* Whether `local` has a stop the server's list lacks, or a later or more
     counted use of one: picks made while the server could not be read. */
  _stopHistoryAhead(local, server) {
    const byId = new Map(server.map((e) => [e.id, e]));
    return local.some((e) => {
      const s = byId.get(e.id);
      return !s || e.n > s.n || e.t > s.t;
    });
  },
  /* Picks kept only in this browser go to the server: read again right
     before writing (behind any write in flight), so nothing newer is lost. */
  _pushLocalStopHistory(hass) {
    const run = BkkLib._stopHistoryWrite.then(async () => {
      const server = await BkkLib._readServerHistory(hass);
      if (server === null) return;
      const cleanServer = BkkLib._cleanStopHistory(server);
      const local = BkkLib._cleanStopHistory(localRead(hass));
      if (!BkkLib._stopHistoryAhead(local, cleanServer)) return;
      const next = BkkLib._rankStopHistory(BkkLib._mergeStopHistory(cleanServer, local), Date.now());
      localWrite(hass, next);
      await hass.callWS({ type: 'frontend/set_user_data', key: HISTORY_KEY, value: next });
    });
    BkkLib._stopHistoryWrite = run.catch(() => { /* the next write still runs */ });
    return BkkLib._stopHistoryWrite;
  },
  /* One read per connection; a new login reads again. A failed read shows
     the browser's copy but is not kept: the next look asks again. A good
     read shows the server's list with this browser's picks merged in, and
     sends those picks up when the server lacks them. */
  loadStopHistory(hass) {
    const conn = (hass && hass.connection) || null;
    const memo = BkkLib._stopHistory;
    if (memo && memo.conn === conn) return memo.p;
    const p = (async () => {
      const server = await BkkLib._readServerHistory(hass);
      const local = BkkLib._cleanStopHistory(localRead(hass));
      if (server === null) {
        if (BkkLib._stopHistory && BkkLib._stopHistory.p === p) BkkLib._stopHistory = null;
        return local;
      }
      const cleanServer = BkkLib._cleanStopHistory(server);
      if (!BkkLib._stopHistoryAhead(local, cleanServer)) return cleanServer;
      BkkLib._pushLocalStopHistory(hass);
      return BkkLib._rankStopHistory(BkkLib._mergeStopHistory(cleanServer, local), Date.now());
    })();
    BkkLib._stopHistory = { conn, p };
    return p;
  },
  /* Whether this connection's list is held (read from the server, or
     being read): false after a failed read, so the caller asks again. */
  stopHistoryKept(hass) {
    const memo = BkkLib._stopHistory;
    return !!memo && memo.conn === ((hass && hass.connection) || null);
  },
  /* Count a pick (an origin, a destination or a suggestion), keep the most
     used, and store the list for this user. */
  recordStop(hass, stop) {
    if (!stop || !stop.id || !stop.name) return BkkLib.loadStopHistory(hass);
    const run = BkkLib._stopHistoryWrite.then(() => BkkLib._recordStopNow(hass, stop));
    BkkLib._stopHistoryWrite = run.catch(() => { /* the next write still runs */ });
    return run;
  },
  /* The server's list is read again right before each write, so another
     device's picks since this page loaded are kept, not overwritten. When
     it cannot be read, only this browser's copy is written: a list that
     may be stale must not replace the server's. */
  async _recordStopNow(hass, stop) {
    const server = await BkkLib._readServerHistory(hass);
    const memo = BkkLib._stopHistory;
    const conn = (hass && hass.connection) || null;
    const seen = memo && memo.conn === conn ? await memo.p.catch(() => []) : [];
    const list = BkkLib._mergeStopHistory(server || [], BkkLib._mergeStopHistory(seen, localRead(hass)));
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
    const next = BkkLib._rankStopHistory(list, now);
    /* Kept for this connection only when the server's list was read: a
       list built without it would hide the other devices' picks for good. */
    BkkLib._stopHistory = server !== null ? { conn, p: Promise.resolve(next) } : null;
    localWrite(hass, next);
    if (server !== null) {
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

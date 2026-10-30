import { BKK_API, MIN_REFRESH_S } from '../config.js';
import { codedError } from '../i18n.js';
import { BkkLib } from '../lib/core.js';

/* A departure board is shared between cards for this long: well under the
   shortest poll, and timed from the request, so a card polling at the floor
   always gets a new one (at exactly the floor every second poll was the
   previous board). */
const BOARD_TTL_MS = Math.max(1, MIN_REFRESH_S - 5) * 1000;
/* An older board stands in for a refresh that the integration's own rate
   limit refused (futar_rate_limited): not an outage, so no banner, and the
   departures it lists still leave on time. Kept no longer than this. */
const BOARD_STALE_MS = 5 * 60 * 1000;

Object.assign(BkkLib, {
  _mem: {},
  _inflight: {},
  _gateActive: 0,
  _gateCards: 0,
  _gateWaiters: [],
  _gateFgWaiters: [],
  _boards: new Map(),
  _boardInflight: new Map(),
  /* One slot pair per card on the dashboard, between 4 and 24. */
  _gateLimit() {
    const cards = Math.max(1, BkkLib._gateCards || 1);
    return Math.min(24, Math.max(4, cards * 2));
  },
  /* Foreground calls are the departure boards. They jump ahead of trip-details
     so one card finishing does not hold up the other cards on the dashboard. */
  async _gate(foreground) {
    const busy = BkkLib._gateActive >= BkkLib._gateLimit();
    const fgWaiting = BkkLib._gateFgWaiters.length > 0;
    if (!busy && (foreground || !fgWaiting)) {
      BkkLib._gateActive += 1;
      return;
    }
    await new Promise((resolve) => {
      (foreground ? BkkLib._gateFgWaiters : BkkLib._gateWaiters).push(resolve);
    });
  },
  _ungate() {
    const next = BkkLib._gateFgWaiters.shift() || BkkLib._gateWaiters.shift();
    if (next) next();
    else BkkLib._gateActive = Math.max(0, BkkLib._gateActive - 1);
  },
  _cached(cache, key) {
    if (BkkLib._mem[key]) return BkkLib._mem[key];
    if (cache && cache[key]) {
      BkkLib._mem[key] = cache[key];
      return cache[key];
    }
    return undefined;
  },
  _store(cache, key, value) {
    BkkLib.cachePut(BkkLib._mem, key, value);
    if (cache && cache !== BkkLib._mem) BkkLib.cachePut(cache, key, value);
    return value;
  },
  /* Latest hass object from any card or editor, for integration FUTÁR calls. */
  _hass: null,
  integrationHasKey(hass) {
    const h = hass || BkkLib._hass;
    return !!(h && h.services && h.services.hungarian_transport
      && h.services.hungarian_transport.futar);
  },
  /* The only key the browser ever holds is one the user put on a card. With
     no such key, FUTÁR goes through the integration when it holds the key,
     and a key borrowed from a sibling card is only the last resort. */
  cardApiKey(cfg, hass, borrowedKey) {
    const own = String((cfg && cfg.apiKey) || '').trim();
    if (own) return own;
    if (BkkLib.integrationHasKey(hass)) return '';
    return String(borrowedKey || '').trim();
  },
  /* FUTÁR is reachable with this card key, or through the integration. */
  hasFutar(apiKey, hass) {
    return !!String(apiKey || '').trim() || BkkLib.integrationHasKey(hass);
  },
  /* The service's error as a translated banner: a rejected key, the
     integration's own request limit (the card tries again), or an outage
     with its detail. The code stays for departureBoard's fallback and the
     editor's key probe. */
  _integrationError(code, detail) {
    const why = String(detail || code);
    let err;
    if (code === 'futar_auth') err = codedError('errApiKeyBad');
    else if (code === 'futar_rate_limited') err = codedError('errFutarRateLimited');
    else err = codedError('errFutarDown', why);
    err.code = String(code);
    return err;
  },
  async _fetchViaIntegration(path, params) {
    const hass = BkkLib._hass;
    if (!BkkLib.integrationHasKey(hass)) throw codedError('errNoApiKey');
    const serviceData = { path: String(path), params: params || {} };
    let timer = null;
    const timeout = new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('BKK timeout')), 20000);
    });
    let res;
    try {
      const call = BkkLib.callServiceResponse(hass, 'hungarian_transport', 'futar', serviceData);
      res = await Promise.race([call, timeout]);
    } catch (err) {
      const msg = err && (err.message || err.code);
      throw new Error(msg && /^BKK /.test(String(msg)) ? String(msg) : `BKK ${msg || err}`);
    } finally {
      if (timer) clearTimeout(timer);
    }
    const payload = BkkLib.servicePayload(res);
    /* Expected outages come back as {data: null, error, detail} instead of a
       service error, so polling cards do not log an ERROR line each time. */
    if (payload && payload.error) throw BkkLib._integrationError(payload.error, payload.detail);
    const data = payload && payload.data;
    if (!data || typeof data !== 'object') throw new Error('BKK empty response');
    if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
    return data;
  },
  /* With a card key the browser calls FUTÁR directly; without one, the
     integration's hungarian_transport.futar service does, with its own key.
     The card key path is DEPRECATED (review J-3): the key goes into the URL
     below and is readable from the dashboard config by every Home Assistant
     user. It still works; see BkkLib.warnCardApiKey. */
  async fetch(apiKey, path, params, foreground) {
    if (!apiKey) {
      if (!BkkLib.integrationHasKey()) throw codedError('errNoApiKey');
      await BkkLib._gate(!!foreground);
      try {
        return await BkkLib._fetchViaIntegration(path, params);
      } finally {
        BkkLib._ungate();
      }
    }
    await BkkLib._gate(!!foreground);
    const q = new URLSearchParams(Object.assign({
      key: apiKey, version: '4', appVersion: 'apiary-1.0',
    }, params));
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 20000) : null;
    try {
      let res;
      try {
        res = await fetch(`${BKK_API}/${path}?${q}`, { signal: ctrl ? ctrl.signal : undefined });
      } catch (err) {
        throw new Error(err && err.name === 'AbortError' ? 'BKK timeout' : `BKK ${err}`);
      }
      if (res.status === 401 || res.status === 403) {
        const err = codedError('errApiKeyBad');
        err.code = 'futar_auth';
        throw err;
      }
      /* The same code as the integration's answer: the board falls back on
         its last copy (departureBoard), whichever path was refused. */
      if (res.status === 429) {
        const err = codedError('errFutarRateLimited');
        err.code = 'futar_rate_limited';
        throw err;
      }
      if (!res.ok) throw codedError('errFutarDown', `HTTP ${res.status}`);
      const data = await res.json();
      if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
      return data;
    } finally {
      if (timer) clearTimeout(timer);
      BkkLib._ungate();
    }
  },
  /* Same stop, horizon and FUTÁR access is one request, shared by every card
     that needs it. A card with its own (revoked) key must not be served the
     board another card fetched with a working one, nor the other way round. */
  departureBoard(apiKey, stopId, horizon) {
    const via = String(apiKey || '').trim();
    const key = (via ? 'key:' + via : 'integration') + '|' + String(stopId) + '|' + String(horizon);
    const hit = BkkLib._boards.get(key);
    if (hit && Date.now() - hit.ts < BOARD_TTL_MS) return Promise.resolve(hit.data);
    const inflight = BkkLib._boardInflight.get(key);
    if (inflight) return inflight;
    const asked = Date.now();
    const run = BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
      stopId: String(stopId),
      minutesAfter: String(horizon),
      minutesBefore: '0',
      onlyDepartures: 'true',
      includeReferences: 'true',
      includeVehicleFromTrip: 'true',
    }, true).then((data) => {
      /* Every stop and horizon a card ever showed would stay otherwise: a
         planner tried on a dozen origins kept a dozen full boards. */
      const now = Date.now();
      BkkLib._boards.forEach((v, k) => { if (now - v.ts >= BOARD_STALE_MS) BkkLib._boards.delete(k); });
      BkkLib._boards.set(key, { ts: asked, data });
      return data;
    }, (err) => {
      if (err && err.code === 'futar_rate_limited' && hit && Date.now() - hit.ts < BOARD_STALE_MS) return hit.data;
      throw err;
    }).finally(() => {
      BkkLib._boardInflight.delete(key);
    });
    BkkLib._boardInflight.set(key, run);
    return run;
  },
  boardSpanMin(data, nowSec) {
    const times = ((((data || {}).data || {}).entry) || {}).stopTimes || [];
    let last = 0;
    times.forEach((st) => {
      const dep = st.predictedDepartureTime || st.departureTime || 0;
      if (dep > last) last = dep;
    });
    if (!last) return { count: times.length, spanMin: 0 };
    return { count: times.length, spanMin: (last - nowSec) / 60 };
  },
  planPlaceVertex(name, id) {
    const label = String(name || '').replace(/::/g, ' ').trim();
    const raw = String(id || '');
    const m = raw.match(/^BKK_(.+)$/i);
    if (m && label) return `${label}::BKK:${m[1]}`;
    return '';
  },
  async planPlace(apiKey, stop) {
    const name = (stop && stop.name) || '';
    const id = (stop && stop.id) || '';
    const vertex = BkkLib.planPlaceVertex(name, id);
    if (vertex) return vertex;
    const pt = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon);
    if (name && pt) return `${name}::${pt[0]},${pt[1]}`;
    if (!BkkLib.hasFutar(apiKey) || !name) return '';
    try {
      const data = await BkkLib.fetch(apiKey, 'search.json', { query: name }, true);
      const stops = (((data.data || {}).references) || {}).stops || {};
      const want = BkkLib.fold(name);
      let best = null;
      Object.values(stops).forEach((s) => {
        if (!s || s.lat == null || s.lon == null) return;
        if (id && s.id === id) best = s;
        else if (!best && BkkLib.fold(s.name) === want) best = s;
      });
      if (!best) return '';
      return `${String(name).replace(/::/g, ' ')}::${best.lat},${best.lon}`;
    } catch (_e) {
      return '';
    }
  },
});

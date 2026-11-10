import { DEFAULT_MINUTES_AFTER } from '../config.js';
import { BkkLib } from '../lib/core.js';

/* What the integration's elvira_between may take (elvira.py): each MÁV board
   gives up after ELVIRA_TIMEOUT (30 s), and one call asks its boards in at
   most two rounds, one after the other (tomorrow's destination board after
   today's, around midnight). The card waits for that and a little more:
   cutting a slow but whole answer off showed "did not answer" over it. If
   the integration ever asks a third round, or a longer board, this has to
   grow with it. */
const ELVIRA_BOARD_TIMEOUT_MS = 30000;
const ELVIRA_BOARD_ROUNDS = 2;

Object.assign(BkkLib, {
  /* The integration registers elvira_between; without it (an older or
     unloaded integration) every poll was one failing call_service. */
  hasElvira(hass) {
    const domain = hass && hass.services && hass.services.hungarian_transport;
    return !!(domain && typeof domain === 'object' && domain.elvira_between);
  },
  /* A failed call (a websocket that dropped, HA restarting) is asked again
     on the next poll; only calls that keep failing leave ELVIRA alone this
     long. The outage is logged once, not on every poll of every MÁV card. */
  _elviraRetryMs: 3 * 60000,
  _elviraFailLimit: 3,
  _elviraFails: 0,
  /* The limit counts failed poll rounds, not calls: every MÁV card (and the
     planner) fails in the same moment when the websocket drops, and that is
     one round. A card polls every 20 s at the most, so failures closer than
     this to the last one counted are the same round. */
  _elviraFailRoundMs: 15000,
  _elviraLastFailAt: 0,
  _elviraDownUntil: 0,
  _elviraWarned: false,
  _elviraLastCode: '',
  _elviraLastErr: '',
  /* A call that never answers ends here, not with the websocket: past the
     integration's own worst case, so only a call it no longer works on is
     cut off. */
  _elviraTimeoutMs: ELVIRA_BOARD_ROUNDS * ELVIRA_BOARD_TIMEOUT_MS + 10000,
  /* The service schema's own limit on dest_name. */
  _elviraDestNameMax: 100,
  /* The trains between two stations, as {rows, error, detail}. error is ''
     for a full answer, else what went wrong: the integration's code
     (elvira_unavailable, elvira_rate_limited), a destination board that did
     not arrive (dest_error: the rows are there, their destHit is not sure),
     a call that failed or timed out, not_loaded, no_code for a stop with
     no station code, or code_search_failed for one whose FUTÁR station
     search failed this time (detail is its name). A MÁV card tells these
     from an empty board; elviraBetween is only the rows. */
  async elviraBoard(hass, originId, destId, minutesAfter, destName, opts) {
    opts = opts || {};
    const none = (error, detail) => ({ rows: [], error: error || '', detail: detail || '' });
    if (!BkkLib.hasElvira(hass)) return none();
    if (Date.now() < BkkLib._elviraDownUntil) return none(BkkLib._elviraLastCode, BkkLib._elviraLastErr);
    let origin = BkkLib.elviraStationCode(originId);
    let dest = BkkLib.elviraStationCode(destId);
    if (!origin) origin = await this.elviraResolveCode(opts.apiKey, originId, opts.originName || '');
    if (!dest) dest = await this.elviraResolveCode(opts.apiKey, destId, destName || '');
    /* A station search that failed (null: FUTÁR timed out or rate-limited)
       is no "no station code": the card is set up right, and the next
       poll searches again. */
    if (origin === null || dest === null) {
      return none('code_search_failed', origin === null ? (opts.originName || originId) : (destName || destId));
    }
    if (!origin || !dest) {
      return none('no_code', !origin ? (opts.originName || originId) : (destName || destId));
    }
    if (origin === dest) return none();
    const serviceData = {
      origin_code: origin,
      dest_code: dest,
      minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
      /* The service rejects a longer one as an invalid call. */
      dest_name: String(destName || '').slice(0, BkkLib._elviraDestNameMax),
    };
    /* A planned time starts the window there (the planner's timed search);
       one already past is the service's own "from now". */
    const at = Math.floor(Number(opts.at));
    if (Number.isFinite(at) && at > Math.floor(Date.now() / 1000)) serviceData.at = at;
    const pair = `${origin}>${dest}`;
    let res;
    try {
      res = await BkkLib._elviraCall(hass, serviceData);
    } catch (err) {
      const detail = BkkLib.elviraErrText(err);
      /* The entry unloaded (disabled, reloading): its services stay listed. */
      let code = 'elvira_call_failed';
      if (err && err.translation_key === 'not_loaded') code = 'not_loaded';
      else if (err && err.code === 'elvira_timeout') code = 'elvira_timeout';
      /* A call that timed out went through a websocket that is up, to an
         integration still at work on it: that one is no strike towards
         pausing ELVIRA for every card. A dropped websocket rejects instead. */
      if (code !== 'elvira_timeout') {
        const now = Date.now();
        if (now - BkkLib._elviraLastFailAt >= BkkLib._elviraFailRoundMs) {
          BkkLib._elviraFails += 1;
          BkkLib._elviraLastFailAt = now;
        }
        BkkLib._elviraLastCode = code;
        BkkLib._elviraLastErr = detail;
        if (BkkLib._elviraFails >= BkkLib._elviraFailLimit) {
          BkkLib._elviraDownUntil = now + BkkLib._elviraRetryMs;
        }
      }
      if (!BkkLib._elviraWarned) {
        BkkLib._elviraWarned = true;
        console.warn('MÁV ELVIRA reservation data unavailable, retrying:', err);
      }
      return none(code, detail);
    }
    BkkLib._elviraFails = 0;
    BkkLib._elviraLastFailAt = 0;
    BkkLib._elviraWarned = false;
    if (!res) return none();
    /* ELVIRA itself being down is {departures: [], error, detail}, not a
       service error: the integration answered, so only this pair's
       outage is noted and no backoff applies to the other pairs. */
    const payload = BkkLib.servicePayload(res);
    /* The integration's own request limit, not MÁV being down: no rows
       this poll, no outage warning, and the pair's outage state stays. */
    if (payload && payload.error === 'elvira_rate_limited') return none(payload.error, payload.detail);
    if (payload && payload.error) {
      BkkLib._elviraDown(pair, `${payload.error}${payload.detail ? ` (${payload.detail})` : ''}`);
      return none(String(payload.error), payload.detail);
    }
    BkkLib._elviraOutages.delete(pair);
    const rows = BkkLib.elviraRowsFromResult(res);
    const list = Array.isArray(rows) ? rows : [];
    if (payload && payload.dest_error) {
      return { rows: list, error: String(payload.dest_error), detail: `${destName || dest}: ${payload.dest_error}` };
    }
    return { rows: list, error: '', detail: '' };
  },
  async elviraBetween(hass, originId, destId, minutesAfter, destName, opts) {
    return (await this.elviraBoard(hass, originId, destId, minutesAfter, destName, opts)).rows;
  },
  /* A failed call's reason, as text for the banner. home-assistant-js-
     websocket does not reject with an Error: a call the dropped socket cut
     off is {error: {code, message}}, one sent while it was down the bare
     ERR_CONNECTION_LOST (3). Neither may show as "[object Object]" or "3". */
  elviraErrText(err) {
    const inner = err && typeof err === 'object' && err.error && typeof err.error === 'object' ? err.error : null;
    const why = err && typeof err === 'object'
      ? [err.message, inner && inner.message, err.code, inner && inner.code]
        .find((v) => (typeof v === 'string' && v.trim()) || Number.isFinite(v))
      : err;
    if (why == null || why === '') return '';
    if (typeof why === 'number') return why === 3 ? 'Connection lost' : `websocket error ${why}`;
    return String(why);
  },
  _elviraCall(hass, serviceData) {
    let timer = null;
    const timeout = new Promise((_resolve, reject) => {
      timer = setTimeout(() => {
        const err = new Error('elvira_between did not answer in time');
        err.code = 'elvira_timeout';
        reject(err);
      }, BkkLib._elviraTimeoutMs);
    });
    const call = Promise.resolve(BkkLib.callServiceResponse(hass, 'hungarian_transport', 'elvira_between', serviceData));
    return Promise.race([call, timeout]).finally(() => clearTimeout(timer));
  },
  /* Cards poll every 15–45 s: warn when a station pair's ELVIRA outage
     starts, not on every poll; its next good answer ends the outage. */
  _elviraOutages: new Set(),
  _elviraDown(pair, reason) {
    if (BkkLib._elviraOutages.has(pair)) return;
    BkkLib._elviraOutages.add(pair);
    console.warn('MÁV ELVIRA reservation data unavailable:', reason);
  },
  coachVehiclesFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res.vehicles)) return res.vehicles;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.vehicles)) return payload.vehicles;
    return [];
  },
  _coachPosInflight: null,
  _coachPosCache: null,
  async coachPositions(hass) {
    if (!BkkLib.hasCompanionService(hass, 'coach_positions')) return [];
    const now = Date.now();
    const hit = BkkLib._coachPosCache;
    if (hit && now - hit.ts < 20000) return hit.vehicles;
    if (BkkLib._coachPosInflight) return BkkLib._coachPosInflight;
    /* A call that failed returns null, not []: "no vehicles" would wipe
       the positions carried over from the last poll (applyCoachGps). */
    const run = (async () => {
      try {
        const res = await BkkLib.hassService(hass, 'coach_positions', {});
        if (!res) return null;
        const vehicles = BkkLib.coachVehiclesFromResult(res);
        const list = Array.isArray(vehicles) ? vehicles : [];
        BkkLib._coachPosCache = { ts: Date.now(), vehicles: list };
        return list;
      } catch (err) {
        return null;
      } finally {
        BkkLib._coachPosInflight = null;
      }
    })();
    BkkLib._coachPosInflight = run;
    return run;
  },
  async coachShape(hass, route, headsign) {
    if (!route || !BkkLib.hasCompanionService(hass, 'coach_shape')) return '';
    const serviceData = { route: String(route), headsign: String(headsign || '') };
    try {
      const res = await BkkLib.hassService(hass, 'coach_shape', serviceData);
      if (!res) return '';
      const payload = (res && (res.response || res.service_response)) || res || {};
      return String(payload.points || '');
    } catch (err) {
      return '';
    }
  },
  servicePayload(res) {
    if (!res || typeof res !== 'object') return {};
    if (Array.isArray(res.shape) || Array.isArray(res.points) || Array.isArray(res.departures)) return res;
    const nested = res.response || res.service_response || res.result;
    if (nested && nested !== res) return BkkLib.servicePayload(nested);
    return res;
  },
  /* Call a service that returns a response. The websocket message is the one
     path that carries return_response on every frontend version; callService
     is only the fallback when there is no connection. Resolves to null when
     neither exists. */
  async callServiceResponse(hass, domain, service, serviceData) {
    if (!hass) return null;
    if (hass.connection && typeof hass.connection.sendMessagePromise === 'function') {
      return hass.connection.sendMessagePromise({
        type: 'call_service',
        domain: String(domain),
        service: String(service),
        service_data: serviceData || {},
        return_response: true,
      });
    }
    if (typeof hass.callService === 'function') {
      return hass.callService(domain, service, serviceData || {}, undefined, false, true);
    }
    return null;
  },
  /* The bkk_stop companion integration is optional. Without it every poll
     with GTFS or MOTIS rows would make a failing call_service round trip, so
     its services are only called when hass lists them. */
  hasCompanionService(hass, service) {
    const domain = hass && hass.services && hass.services.bkk_stop;
    return !!(domain && typeof domain === 'object' && domain[service]);
  },
  /* Companion bkk_stop services (coach GPS and shapes). Resolves to null
     when the companion is not installed or lacks the service. */
  hassService(hass, service, serviceData) {
    if (!BkkLib.hasCompanionService(hass, service)) return Promise.resolve(null);
    return BkkLib.callServiceResponse(hass, 'bkk_stop', service, serviceData);
  },
  /* FUTÁR trip-details.json already ships the street polyline
     (8E Kelenföld-bound: ~1000 points, Újpalota → Kelenföld).
     bkk_stop has no trip_shape service, so the map must decode this
     itself instead of waiting on Home Assistant. */
  async futarTripShape(apiKey, cache, tripId) {
    if (!BkkLib.hasFutar(apiKey) || !BkkLib.isFutarTripId(tripId)) return [];
    try {
      const packed = await BkkLib.tripDetails(apiKey, cache, tripId);
      return BkkLib.validMapShape(BkkLib.decodePolyline(packed && packed.polyline));
    } catch (_e) {
      return [];
    }
  },
  /* Bus/tram/metro/trolley/coach maps want the published line, not the
     Transitous hop-cut. combineHopShapes drops a FUTÁR line that starts
     far from the board (8E at Újpalota vs a Keleti hop). */
  pickPublishedLineShape(motisShape, futarShape, row, coversHead) {
    const motis = BkkLib.validMapShape(motisShape);
    const futar = BkkLib.validMapShape(futarShape);
    if (BkkLib.keepsFullLineShape(row) && !BkkLib.isTrainRow(row) && futar.length >= 8) {
      return futar;
    }
    return BkkLib.combineHopShapes(motis, futar, coversHead, []);
  },
});

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
  /* A call that never answers ends here, not with the websocket: past the
     integration's own worst case, so only a call it no longer works on is
     cut off. */
  _elviraTimeoutMs: ELVIRA_BOARD_ROUNDS * ELVIRA_BOARD_TIMEOUT_MS + 10000,
  /* The rows of elviraBoard (board.js): the trains between two stations. */
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

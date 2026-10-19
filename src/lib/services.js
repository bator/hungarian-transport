import { DEFAULT_MINUTES_AFTER } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  elviraRowsFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res)) return res;
    if (Array.isArray(res.departures)) return res.departures;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.departures)) return payload.departures;
    if (Array.isArray(payload)) return payload;
    return [];
  },
  /* The integration registers elvira_between; without it (an older or
     unloaded integration) every poll was one failing call_service. */
  hasElvira(hass) {
    const domain = hass && hass.services && hass.services.hungarian_transport;
    return !!(domain && typeof domain === 'object' && domain.elvira_between);
  },
  /* After a failed call ELVIRA is left alone this long, and the outage is
     logged once, not on every poll of every MÁV card. */
  _elviraRetryMs: 3 * 60000,
  _elviraDownUntil: 0,
  _elviraWarned: false,
  async elviraBetween(hass, originId, destId, minutesAfter, destName, opts) {
    opts = opts || {};
    if (!BkkLib.hasElvira(hass) || Date.now() < BkkLib._elviraDownUntil) return [];
    let origin = BkkLib.elviraStationCode(originId);
    let dest = BkkLib.elviraStationCode(destId);
    if (!origin) origin = await this.elviraResolveCode(opts.apiKey, originId, opts.originName || '');
    if (!dest) dest = await this.elviraResolveCode(opts.apiKey, destId, destName || '');
    if (!origin || !dest || origin === dest) {
      return [];
    }
    const serviceData = {
      origin_code: origin,
      dest_code: dest,
      minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
      dest_name: destName || '',
    };
    const pair = `${origin}>${dest}`;
    try {
      const res = await BkkLib.callServiceResponse(hass, 'hungarian_transport', 'elvira_between', serviceData);
      BkkLib._elviraWarned = false;
      if (!res) return [];
      /* ELVIRA itself being down is {departures: [], error, detail}, not a
         service error: the integration answered, so only this pair's
         outage is noted and no backoff applies to the other pairs. */
      const payload = BkkLib.servicePayload(res);
      if (payload && payload.error) {
        BkkLib._elviraDown(pair, `${payload.error}${payload.detail ? ` (${payload.detail})` : ''}`);
        return [];
      }
      BkkLib._elviraOutages.delete(pair);
      const rows = BkkLib.elviraRowsFromResult(res);
      return Array.isArray(rows) ? rows : [];
    } catch (err) {
      BkkLib._elviraDownUntil = Date.now() + BkkLib._elviraRetryMs;
      if (!BkkLib._elviraWarned) {
        BkkLib._elviraWarned = true;
        console.warn('MÁV ELVIRA reservation data unavailable, retrying in a few minutes:', err);
      }
      return [];
    }
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
    const run = (async () => {
      try {
        const res = await BkkLib.hassService(hass, 'coach_positions', {});
        if (!res) return [];
        const vehicles = BkkLib.coachVehiclesFromResult(res);
        const list = Array.isArray(vehicles) ? vehicles : [];
        BkkLib._coachPosCache = { ts: Date.now(), vehicles: list };
        return list;
      } catch (err) {
        return [];
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

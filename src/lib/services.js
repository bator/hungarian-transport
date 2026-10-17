import { DEFAULT_MINUTES_AFTER, INTEGRATION_KEY } from '../config.js';
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
  async elviraBetween(hass, originId, destId, minutesAfter, destName, opts) {
    opts = opts || {};
    let origin = BkkLib.elviraStationCode(originId);
    let dest = BkkLib.elviraStationCode(destId);
    if (!origin) origin = await BkkLib.elviraResolveCode(opts.apiKey, originId, opts.originName || '');
    if (!dest) dest = await BkkLib.elviraResolveCode(opts.apiKey, destId, destName || '');
    if (!hass || !origin || !dest || origin === dest) {
      return [];
    }
    const serviceData = {
      origin_code: origin,
      dest_code: dest,
      minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
      dest_name: destName || '',
    };
    try {
      let res;
      if (typeof hass.callService === 'function') {
        try {
          res = await hass.callService('hungarian_transport', 'elvira_between', serviceData, undefined, false, true);
        } catch (err) {
          if (!hass.connection) throw err;
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'hungarian_transport',
            service: 'elvira_between',
            service_data: serviceData,
            return_response: true,
          });
        }
      } else if (hass.connection) {
        res = await hass.connection.sendMessagePromise({
          type: 'call_service',
          domain: 'hungarian_transport',
          service: 'elvira_between',
          service_data: serviceData,
          return_response: true,
        });
      } else {
        return [];
      }
      const rows = BkkLib.elviraRowsFromResult(res);
      return Array.isArray(rows) ? rows : [];
    } catch (err) {
      console.warn('MÁV ELVIRA reservation data unavailable:', err);
      return [];
    }
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
    if (!hass) return [];
    const now = Date.now();
    const hit = BkkLib._coachPosCache;
    if (hit && now - hit.ts < 20000) return hit.vehicles;
    if (BkkLib._coachPosInflight) return BkkLib._coachPosInflight;
    const run = (async () => {
      try {
        let res;
        if (typeof hass.callService === 'function') {
          try {
            res = await hass.callService('bkk_stop', 'coach_positions', {}, undefined, false, true);
          } catch (err) {
            if (!hass.connection) throw err;
            res = await hass.connection.sendMessagePromise({
              type: 'call_service',
              domain: 'bkk_stop',
              service: 'coach_positions',
              service_data: {},
              return_response: true,
            });
          }
        } else if (hass.connection) {
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'bkk_stop',
            service: 'coach_positions',
            service_data: {},
            return_response: true,
          });
        } else {
          return [];
        }
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
    if (!hass || !route) return '';
    const serviceData = { route: String(route), headsign: String(headsign || '') };
    try {
      let res;
      if (typeof hass.callService === 'function') {
        try {
          res = await hass.callService('bkk_stop', 'coach_shape', serviceData, undefined, false, true);
        } catch (err) {
          if (!hass.connection) throw err;
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'bkk_stop',
            service: 'coach_shape',
            service_data: serviceData,
            return_response: true,
          });
        }
      } else if (hass.connection) {
        res = await hass.connection.sendMessagePromise({
          type: 'call_service',
          domain: 'bkk_stop',
          service: 'coach_shape',
          service_data: serviceData,
          return_response: true,
        });
      } else {
        return '';
      }
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
  async hassService(hass, service, serviceData) {
    if (!hass) return null;
    if (hass.connection && typeof hass.connection.sendMessagePromise === 'function') {
      return hass.connection.sendMessagePromise({
        type: 'call_service',
        domain: 'bkk_stop',
        service: String(service),
        service_data: serviceData || {},
        return_response: true,
      });
    }
    if (typeof hass.callService === 'function') {
      return hass.callService('bkk_stop', service, serviceData, undefined, false, true);
    }
    return null;
  },
  async hassTripShape(hass, apiKey, tripId) {
    if (!hass || !BkkLib.isFutarTripId(tripId) || !apiKey || apiKey === INTEGRATION_KEY) return [];
    const serviceData = { trip_id: String(tripId), api_key: String(apiKey) };
    try {
      const res = await BkkLib.hassService(hass, 'trip_shape', serviceData);
      const payload = BkkLib.servicePayload(res);
      return BkkLib.validMapShape(payload.shape || payload.points || []);
    } catch (_e) {
      return [];
    }
  },
  /* FUTÁR trip-details.json already ships the street polyline
     (8E Kelenföld-bound: ~1000 points, Újpalota → Kelenföld).
     bkk_stop has no trip_shape service, so the map must decode this
     itself instead of waiting on Home Assistant. */
  async futarTripShape(apiKey, cache, tripId) {
    if (!apiKey || !BkkLib.isFutarTripId(tripId)) return [];
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

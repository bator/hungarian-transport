import { BKK_RAIL_BADGE, PLATFORM_IN_HEAD, PLATFORM_STRIP, vehicleIcon } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  trainNumberFromTripId(tripId) {
    const s = String(tripId || '');
    /* BKK_90681_106 is train 9068 (trailing 1 on a 5-digit body).
       West/south IC trips are BKK_8661_47 for train 866, BKK_8741_47 for 874.
       Suburban 4xxx ids (BKK_4541_108) must keep all four digits.
       No train number has more than five digits: BKK_1792439004037 is an
       ad-hoc FUTÁR trip (a millisecond timestamp), not a train. */
    const long = s.match(/^BKK_([89]\d{3})1(?:_|$)/);
    if (long) return long[1];
    const ic = s.match(/^BKK_([89]\d{2})1(?:_|$)/);
    if (ic) return ic[1];
    const m = s.match(/^BKK_(\d{1,5})(?:_|$)/);
    return m ? m[1] : '';
  },
  /* The other reading of a 4–5 digit id body: the train number plus one
     digit, where the rule above kept every digit (BKK_8624_67 is IC 862,
     BKK_2461_311 is EC 246). '' when both readings agree. Ambiguous on its
     own: mergeVolanRows tries it only for long-distance rows, within its
     train-number window, against a number from another source. */
  trainNumberLooseFromTripId(tripId) {
    const m = String(tripId || '').match(/^BKK_(\d{4,5})(?:_|$)/);
    if (!m) return '';
    const loose = m[1].slice(0, -1);
    return loose === BkkLib.trainNumberFromTripId(tripId) ? '' : loose;
  },
  isFutarTripId(tripId) {
    const t = String(tripId || '');
    return !!t && !/^(elvira:|gtfs:|motis:)/.test(t);
  },
  emmaTrainNumber(text) {
    const s = String(text || '').trim();
    const lead = s.match(/^(\d+)/);
    if (lead) return lead[1].replace(/^0+/, '') || lead[1];
    const mid = s.match(/\b(\d{3,5})\b/);
    return mid ? (mid[1].replace(/^0+/, '') || mid[1]) : '';
  },
  /* What a vehicle is matched on, worked out once per vehicle rather than
     once per row and vehicle pair. */
  _emmaVehicleKey(vehicle) {
    const trip = vehicle.trip || {};
    return {
      n: BkkLib.emmaTrainNumber(trip.tripShortName || vehicle.label || ''),
      blob: [
        trip.tripShortName, trip.tripHeadsign,
        (trip.route || {}).longName, (trip.route || {}).shortName, vehicle.label,
      ].join(' ').toUpperCase(),
    };
  },
  /* A row's train number, and its named label as a whole-word pattern
     (BALATON, not Balatonfüred): one RegExp per row. */
  _emmaRowKey(row) {
    const name = String(row.label || '').trim().toUpperCase();
    const named = !!name && name.length >= 3 && !BkkLib.genericRailLabel(name);
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return {
      num: BkkLib.rowTrainNumber(row),
      re: named ? new RegExp('(?:^|[^A-Z0-9])' + esc + '(?:[^A-Z0-9]|$)') : null,
    };
  },
  _emmaKeysMatch(rk, vk) {
    if (rk.num && vk.n && rk.num === vk.n) return true;
    if (!rk.re || !rk.re.test(vk.blob)) return false;
    return !rk.num || !vk.n;
  },
  matchEmmaVehicle(row, vehicle) {
    if (!row || !vehicle) return false;
    return BkkLib._emmaKeysMatch(BkkLib._emmaRowKey(row), BkkLib._emmaVehicleKey(vehicle));
  },
  _emmaKeyed(vehicles) {
    return (Array.isArray(vehicles) ? vehicles : [])
      .filter(Boolean)
      .map((v) => ({ v, k: BkkLib._emmaVehicleKey(v) }));
  },
  _applyEmmaKeyed(list, keyed) {
    if (!keyed.length) return list;
    list.forEach((row) => {
      if (!row) return;
      const rk = BkkLib._emmaRowKey(row);
      for (let i = 0; i < keyed.length; i++) {
        const v = keyed[i].v;
        if (!BkkLib._emmaKeysMatch(rk, keyed[i].k)) continue;
        const gps = BkkLib.mapGpsLatLon(v.lat, v.lon);
        if (!gps) continue;
        row.lat = gps[0];
        row.lon = gps[1];
        if (v.heading != null) row.heading = v.heading;
        if (v.speed != null) row.speed = v.speed;
        break;
      }
    });
    return list;
  },
  applyEmmaPositions(rows, vehicles) {
    const list = Array.isArray(rows) ? rows : [];
    return BkkLib._applyEmmaKeyed(list, BkkLib._emmaKeyed(vehicles));
  },
  coachRouteKeys(route) {
    const raw = String(route || '').trim().toUpperCase();
    const keys = [];
    const add = (value) => {
      const key = String(value || '');
      if (key && keys.indexOf(key) < 0) keys.push(key);
    };
    add(raw);
    const body = raw.indexOf('SZ') === 0 ? raw.slice(2) : raw;
    add(body);
    const stripped = body.replace(/^0+/, '');
    add(stripped);
    if (stripped) add('SZ' + stripped);
    return keys;
  },
  applyCoachGps(rows, vehicles, nowSec) {
    const list = Array.isArray(rows) ? rows : [];
    const vehs = Array.isArray(vehicles) ? vehicles : [];
    const byRoute = new Map();
    const fold = (value) => String(value || '').toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '');
    vehs.forEach((vehicle) => {
      if (!vehicle) return;
      if (!BkkLib.mapGpsLatLon(vehicle.lat, vehicle.lon)) return;
      BkkLib.coachRouteKeys(vehicle.route).forEach((key) => {
        if (!byRoute.has(key)) byRoute.set(key, []);
        byRoute.get(key).push(vehicle);
      });
    });
    const now = Number.isFinite(Number(nowSec)) ? Number(nowSec) : Math.floor(Date.now() / 1000);
    const rowHead = (row) => fold(row.headsign || row.head);
    const vehicleClassOk = (row, vehicle) => {
      const route = String(vehicle.route || '').trim().toUpperCase();
      const kind = String(row.vehicle || '').toLowerCase();
      if (route.indexOf('SZ') === 0) return kind === 'bus';
      if (/^\d{4}$/.test(route)) return kind === 'coach';
      return true;
    };
    const pairOk = (row, vehicle) => {
      if (!vehicleClassOk(row, vehicle)) return false;
      const head = rowHead(row);
      const vehicleHead = fold(vehicle.head);
      if (head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0)) return true;
      if (!vehicleHead) {
        const dep = Number(row.dep || row.sched || 0);
        return dep > 0 && Math.abs(dep - now) <= 20 * 60;
      }
      return false;
    };
    const groups = new Map();
    list.forEach((row, idx) => {
      if (!row || !/^(gtfs:|motis:)/.test(String(row.tripId || ''))) return;
      if (BkkLib.mapGpsLatLon(row.lat, row.lon)) return;
      let key = '';
      BkkLib.coachRouteKeys(row.label).some((candidate) => {
        if (!byRoute.has(candidate)) return false;
        key = candidate;
        return true;
      });
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(idx);
    });
    groups.forEach((indexes, key) => {
      const uniq = [];
      const seen = new Set();
      byRoute.get(key).forEach((vehicle) => {
        const id = String(vehicle.tripId || (vehicle.lat + ',' + vehicle.lon));
        if (seen.has(id)) return;
        seen.add(id);
        uniq.push(vehicle);
      });
      const pairs = [];
      indexes.forEach((idx) => {
        uniq.forEach((vehicle, vi) => {
          if (!pairOk(list[idx], vehicle)) return;
          const dep = Number(list[idx].dep || list[idx].sched || 0);
          const head = rowHead(list[idx]);
          const vehicleHead = fold(vehicle.head);
          const headMatch = !!(head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0));
          const distance = Math.abs((dep || now) - now);
          pairs.push({ idx: idx, vi: vi, score: (headMatch ? 0 : 1) * 1e12 + distance });
        });
      });
      pairs.sort((a, b) => a.score - b.score);
      const usedRow = new Set();
      const usedVehicle = new Set();
      pairs.forEach((pair) => {
        if (usedRow.has(pair.idx) || usedVehicle.has(pair.vi)) return;
        usedRow.add(pair.idx);
        usedVehicle.add(pair.vi);
        const gps = BkkLib.mapGpsLatLon(uniq[pair.vi].lat, uniq[pair.vi].lon);
        if (!gps) return;
        list[pair.idx].lat = gps[0];
        list[pair.idx].lon = gps[1];
      });
    });
    return list;
  },
  async attachCoachGps(hass, rows) {
    const list = Array.isArray(rows) ? rows : [];
    const needs = list.some((row) => row
      && /^(gtfs:|motis:)/.test(String(row.tripId || ''))
      && !BkkLib.mapGpsLatLon(row.lat, row.lon));
    if (!hass || !needs) return list;
    return BkkLib.applyCoachGps(list, await BkkLib.coachPositions(hass));
  },
  keepFutarCandidate(rt, selected, mode, destKey) {
    if (!selected || !selected.size || mode === 'volan') return true;
    if (destKey && (mode === 'bkk' || mode === 'mav' || mode === 'all')) return true;
    const rid = (rt && rt.id) || '';
    return selected.has(rid);
  },
  genericRailLabel(label) {
    return /^(IC|EC|IR|EX|EN|RJX|S|SZ)$/i.test(String(label || '').trim());
  },
  suburbanLineLabel(label) {
    return /^[SGZ]\d/i.test(String(label || '').trim());
  },
  /* True when the row's visible jelzés is a MÁV train number (16907) or
     generic EX/EN plus a long number. These stay on the hop; merge them
     onto a branded suburban/named row when they are the same departure. */
  numberedRailBadge(row) {
    if (!row || !BkkLib.isTrainRow(row)) return false;
    const label = String(row.label || '').trim();
    if (/^\d+$/.test(label)) return true;
    if (/^(EX|EN)$/i.test(label) && /^\d{4,}$/.test(BkkLib.rowTrainNumber(row))) return true;
    return false;
  },
  platformFromText(text) {
    const m = String(text || '').match(PLATFORM_IN_HEAD);
    return m ? m[1] : '';
  },
  stripPlatform(text) {
    return String(text || '').replace(PLATFORM_STRIP, '').replace(/\s+/g, ' ').trim();
  },
  /* Home Assistant hands over a new states object on every change and the
     same one in between, so one scan of all states serves every paint of
     every card until something changes. The list is shared: read only. */
  _hassVehicles: new WeakMap(),
  collectHassVehicles(hass) {
    const states = hass && hass.states;
    if (!states || typeof states !== 'object') return [];
    const hit = BkkLib._hassVehicles.get(states);
    if (hit) return hit.all;
    const out = [];
    Object.keys(states).forEach((eid) => {
      if (eid.indexOf('sensor.') !== 0) return;
      const vehs = ((states[eid] || {}).attributes || {}).vehicles;
      if (!Array.isArray(vehs)) return;
      vehs.forEach((v) => { if (v) out.push(v); });
    });
    BkkLib._hassVehicles.set(states, { all: out, emma: null });
    return out;
  },
  /* The EMMA (non-FUTÁR) vehicles of the same scan, keyed for matching. */
  _hassEmmaKeyed(hass, pool) {
    const memo = hass && hass.states && BkkLib._hassVehicles.get(hass.states);
    if (memo && memo.all === pool && memo.emma) return memo.emma;
    const keyed = BkkLib._emmaKeyed(pool.filter((v) => !BkkLib.isFutarTripId(v && v.tripId)).map((v) => ({
      lat: v.lat,
      lon: v.lon,
      heading: v.heading,
      speed: v.speed,
      label: v.label || v.trainNumber,
      trip: {
        tripShortName: String(
          (v.trip && (v.trip.tripShortName || v.trip.shortName))
          || v.trainNumber || v.shortName || v.label || '',
        ),
      },
    })));
    if (memo && memo.all === pool) memo.emma = keyed;
    return keyed;
  },
  matchHassVehicle(pool, row) {
    const trip = String(row.tripId || '');
    const num = String(row.trainNumber || BkkLib.trainNumberFromTripId(row.tripId) || '');
    const hm = BkkLib.hm(row.sched || row.dep);
    const label = String(row.label || '').toUpperCase();
    let best = null;
    let bestScore = 0;
    pool.forEach((v) => {
      let s = 0;
      if (trip && v.tripId && String(v.tripId) === trip) s += 5;
      if (num && v.trainNumber && String(v.trainNumber) === num) s += 3;
      if (hm && v.attime && String(v.attime) === hm) s += 2;
      const vr = String(v.routeid || '').toUpperCase();
      if (label && vr && label === vr) s += 1;
      if (s > bestScore) {
        bestScore = s;
        best = v;
      }
    });
    if (!best) return null;
    if (bestScore >= 5) return best;
    if (num && bestScore >= 3) return best;
    if (hm && label && bestScore >= 3) return best;
    return null;
  },
  /* BKK has no live track or IC seat-reservation flag. Those live on the
     bkk_stop sensors (ELVIRA). Copy them onto the same departure. */
  enrichFromHass(hass, rows) {
    const pool = BkkLib.collectHassVehicles(hass);
    if (!pool.length || !rows || !rows.length) return rows;
    rows.forEach((row) => {
      const v = BkkLib.matchHassVehicle(pool, row);
      if (!v) return;
      if (!row.platform && v.platform) row.platform = String(v.platform);
      if (!row.platform) {
        const fromHead = BkkLib.platformFromText(v.headsign);
        if (fromHead) row.platform = fromHead;
      }
      if (v.booking) row.booking = true;
      if (!row.trainNumber && v.trainNumber) row.trainNumber = String(v.trainNumber);
      if (v.tripId && BkkLib.isFutarTripId(v.tripId) && !BkkLib.isFutarTripId(row.tripId)) {
        row.tripId = String(v.tripId);
      }
      if (v.bikesAllowed === true || v.bikesallowed === true) row.bikesAllowed = true;
      const incoming = BkkLib.mapGpsLatLon(v.lat, v.lon);
      if (!BkkLib.mapGpsLatLon(row.lat, row.lon) && incoming) {
        row.lat = incoming[0];
        row.lon = incoming[1];
      }
    });
    BkkLib._applyEmmaKeyed(rows, BkkLib._hassEmmaKeyed(hass, pool));
    return rows;
  },
  applyRailBadge(row) {
    if (!row) return row;
    let key = String(row.label || '').trim().toUpperCase().normalize('NFC');
    if (key === 'S') {
      row.label = 'Sz';
      key = 'SZ';
    }
    let badge = BKK_RAIL_BADGE[key];
    if (!badge && BkkLib.isTrainRow(row) && key
        && !BkkLib.suburbanLineLabel(key)
        && !BkkLib.genericRailLabel(key)
        && !/^\d+$/.test(key)) {
      badge = BKK_RAIL_BADGE.IC;
    }
    if (!badge) return row;
    const cur = String(row.color || '').replace('#', '').toLowerCase();
    if (!cur || cur === '4477aa') {
      row.color = '#' + badge[0];
      row.text = '#' + badge[1];
    }
    return row;
  },

  /* One departure in the shape the card renders: display strings plus the raw
     epoch seconds, so the countdown can tick without refetching. */
  asRow(r, destName, lang) {
    BkkLib.applyRailBadge(r);
    const dep = r.dep;
    let type = String(r.rawType || r.vehicle || 'BUS').toUpperCase();
    if (type.indexOf('SUBURBAN') >= 0 || type === 'TRAIN' || type === 'RAILWAY') type = 'RAIL';
    let head = String(r.head || destName || '').trim();
    const plat = r.platform ? String(r.platform) : BkkLib.platformFromText(head);
    head = BkkLib.stripPlatform(head);
    const lat = Number(r.lat);
    const lon = Number(r.lon);
    const gps = BkkLib.mapGpsLatLon(lat, lon);
    const hasGps = !!gps;
    const shape = Array.isArray(r.shape) ? r.shape : undefined;
    return {
      type: type,
      icon: vehicleIcon(type),
      label: r.label,
      headsign: head,
      tripId: r.tripId || '',
      depTs: dep || 0,
      schedTs: r.sched || dep || 0,
      attime: BkkLib.hm(r.sched || dep),
      predicted_attime: BkkLib.hm(dep),
      wheelchair: !!r.wheelchair,
      bikesAllowed: !!r.bikesAllowed,
      color: String(r.color || '').replace('#', ''),
      textcolor: String(r.text || r.textcolor || '').replace('#', ''),
      platform: plat || '',
      trainNumber: r.trainNumber || '',
      delay: Number(r.delay || 0),
      booking: !!r.booking,
      nationalPass: !!r.nationalPass,
      reservationFreeSection: !!r.reservationFreeSection,
      travelMin: r.travel,
      lat: hasGps ? gps[0] : null,
      lon: hasGps ? gps[1] : null,
      hasGps: hasGps,
      shape: shape,
      gtfs: r.gtfs || null,
      vehicle: r.vehicle || '',
      vehicleId: r.vehicleId || '',
      licensePlate: r.licensePlate || '',
      model: r.model || '',
      vehicleStatus: r.vehicleStatus || '',
      stopDistancePercent: r.stopDistancePercent,
    };
  },
});

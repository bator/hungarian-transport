import { cityIndexUrl, clampMinutesAfter, departureStillDue, mavIndexUrl, maxDepartureRows, tzDow, tzEpochSec, tzParts, tzShiftYmd, volanIndexUrl } from '../config.js';
import { codedError } from '../i18n.js';
import { BkkLib } from '../lib/core.js';
import { INDEX_SCHEMA } from '../tags.js';

Object.assign(BkkLib, {
  /* Cached per resolved URL, so two cards pointing at different index files do
     not serve each other's data. The URL carries a daily cache-buster, which
     also expires the entry on a dashboard left open overnight. */
  _idx: new Map(),
  _indexCached(url, httpKey, gunzipKey) {
    const cache = BkkLib._idx;
    const hit = cache.get(url);
    if (hit) return hit;
    const p = BkkLib._loadGtfsIndex(url, httpKey, gunzipKey).catch((err) => {
      cache.delete(url);
      throw err;
    });
    cache.set(url, p);
    while (cache.size > 8) cache.delete(cache.keys().next().value);
    return p;
  },
  volanIndex() {
    return BkkLib._indexCached(volanIndexUrl(), 'errVolanHttp', 'errVolanGunzip');
  },
  cityIndex() {
    return BkkLib._indexCached(cityIndexUrl(), 'errCityHttp', 'errCityGunzip');
  },
  _mavP: new Map(),
  mavIndex() {
    const url = mavIndexUrl();
    const cache = BkkLib._mavP;
    const hit = cache.get(url);
    if (hit) return hit;
    const p = BkkLib._loadMavIndex(url).then((idx) => {
      const empty = !idx || !Array.isArray(idx.p) || idx.p.length < 1;
      if (empty) cache.delete(url);
      return idx && typeof idx === 'object' ? idx : { n: {}, l: {}, p: [] };
    }).catch(() => {
      cache.delete(url);
      return { n: {}, l: {}, p: [] };
    });
    cache.set(url, p);
    while (cache.size > 4) cache.delete(cache.keys().next().value);
    return p;
  },
  async _loadMavIndex(url) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 45000) : null;
    let res;
    try {
      res = await fetch(url, { cache: 'no-cache', signal: ctrl ? ctrl.signal : undefined });
    } catch (_err) {
      return { n: {}, l: {}, p: [] };
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!res.ok) return { n: {}, l: {}, p: [] };
    const buf = await res.arrayBuffer();
    const u8 = new Uint8Array(buf);
    let text = '';
    if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
      if (typeof DecompressionStream !== 'function') return { n: {}, l: {}, p: [] };
      const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
      text = await new Response(stream).text();
    } else {
      text = new TextDecoder().decode(u8);
    }
    let idx;
    try {
      idx = JSON.parse(text);
    } catch (_e) {
      return { n: {}, l: {}, p: [] };
    }
    if (!idx || typeof idx !== 'object' || !idx.n || !Array.isArray(idx.p)) {
      return { n: {}, l: {}, p: [] };
    }
    return {
      n: idx.n && typeof idx.n === 'object' ? idx.n : {},
      l: idx.l && typeof idx.l === 'object' ? idx.l : {},
      p: idx.p,
    };
  },
  mavShapeIndexKey(row, idx) {
    const num = BkkLib.rowTrainNumber(row);
    const label = String((row && (row.label || row.route || '')) || '').trim().toUpperCase();
    if (!idx) return null;
    /* BKK suburban trip ids (G43 19707) collide with unrelated MÁV train
       numbers. Branded G/S/Z lines always use the line polyline. */
    const line = (label.match(/^[SGZ]\d{2}/) || [])[0];
    if (BkkLib.suburbanLineLabel(label) && line && idx.l && idx.l[line] != null) {
      return { kind: 'l', key: line, ix: idx.l[line] };
    }
    if (num && idx.n && idx.n[num] != null) {
      return { kind: 'n', key: num, ix: idx.n[num] };
    }
    if (label && idx.l && idx.l[label] != null) {
      return { kind: 'l', key: label, ix: idx.l[label] };
    }
    return null;
  },
  async mavShapeForRow(row) {
    try {
      const idx = await BkkLib.mavIndex();
      if (!idx || !Array.isArray(idx.p)) return [];
      const hit = BkkLib.mavShapeIndexKey(row, idx);
      if (!hit) return [];
      const encoded = idx.p[hit.ix];
      if (encoded == null || encoded === '') return [];
      return BkkLib.validMapShape(BkkLib.decodePolyline(encoded));
    } catch (_e) {
      return [];
    }
  },
  async _loadGtfsIndex(url, httpKey, gunzipKey) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 45000) : null;
    let res;
    /* no-cache revalidates: Home Assistant answers 304 for an unchanged index,
       so a dashboard reload does not download megabytes again, while a rebuilt
       index (new ETag) is fetched at once. */
    try {
      res = await fetch(url, { cache: 'no-cache', signal: ctrl ? ctrl.signal : undefined });
    } catch (_err) {
      throw codedError('errIndexFetch');
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!res.ok) throw codedError(httpKey, res.status);
    const buf = await res.arrayBuffer();
    const u8 = new Uint8Array(buf);
    let text = '';
    if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
      if (typeof DecompressionStream !== 'function') throw codedError(gunzipKey);
      const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
      text = await new Response(stream).text();
    } else {
      text = new TextDecoder().decode(u8);
    }
    let idx;
    try {
      idx = JSON.parse(text);
    } catch (_e) {
      /* Usually an HTML error page served in place of the index. */
      throw codedError('errIndexParse');
    }
    if (!idx || typeof idx !== 'object' || !Array.isArray(idx.s) || !Array.isArray(idx.t)) {
      throw codedError('errIndexParse');
    }
    if (idx.v && Number(idx.v) > INDEX_SCHEMA) throw codedError('errIndexSchema', idx.v);
    idx.stops = (idx.s || []).map((row, i) => ({
      i,
      id: row[0],
      name: row[1],
      num: String(row[2] || ''),
      op: row.length > 3 ? Number(row[3]) : 0,
      key: BkkLib.norm(row[1]),
      fold: BkkLib.fold(row[1]),
    }));
    idx.ops = (idx.o || []).map((row, i) => ({ i, id: row[0], name: row[1] }));
    idx.byNum = new Map();
    idx.byKey = new Map();
    idx.stops.forEach((st) => {
      const nk = st.op + ':' + st.num;
      const kk = st.op + ':' + st.key;
      if (st.num && !idx.byNum.has(nk)) idx.byNum.set(nk, st);
      if (st.key && !idx.byKey.has(kk)) idx.byKey.set(kk, st);
    });
    return idx;
  },
  opIx(idx, city) {
    if (!idx || !city) return -1;
    const hit = (idx.ops || []).find((o) => o.id === city);
    return hit ? hit.i : -1;
  },
  cityStops(idx, opIx) {
    if (opIx < 0) return idx.stops || [];
    return (idx.stops || []).filter((s) => s.op === opIx);
  },
  /* One GTFS service day. `mins` is the earliest departure minute still worth
     looking at: for yesterday's service day that is now + 24h, which is how
     after-midnight (24:xx) trips stay visible. */
  volanDay(offset) {
    const off = offset || 0;
    const now = tzParts(new Date());
    const ymd = tzShiftYmd(now.ymd, off);
    let mins = 0;
    if (off === 0) mins = now.mins;
    else if (off < 0) mins = now.mins - off * 1440;
    return {
      ymd,
      dow: tzDow(ymd),
      mins,
      midnight: tzEpochSec(ymd, 0),
    };
  },
  volanServiceOk(idx, si, ymd, dow) {
    const c = (idx.c || [])[si];
    if (!c) return false;
    const days = String(c[0] || '0000000');
    const start = String(c[1] || '');
    const end = String(c[2] || '');
    let ok = ymd >= start && ymd <= end && days.charAt(dow) === '1';
    const add = (idx.a || [])[si] || [];
    const rem = (idx.r || [])[si] || [];
    if (add.some((x) => String(x) === ymd)) ok = true;
    if (rem.some((x) => String(x) === ymd)) ok = false;
    return ok;
  },
  cityOpId(idx, stop) {
    if (!idx || !stop) return '';
    const id = String(stop.id || '');
    const hit = (idx.stops || []).find((s) => s.id === id);
    if (hit && idx.ops && idx.ops[hit.op]) return idx.ops[hit.op].id;
    const colon = id.indexOf(':');
    const prefix = colon > 0 ? id.slice(0, colon) : '';
    const op = (idx.ops || []).find((o) => o.id === prefix);
    return op ? op.id : '';
  },
  cityStopIndexes(idx, stop, opIx) {
    const origin = BkkLib.volanMatchStop(idx, stop, opIx);
    if (!origin) return [];
    const pool = BkkLib.cityStops(idx, opIx);
    const out = [];
    pool.forEach((s) => {
      if (s.fold === origin.fold && s.op === origin.op) out.push(s.i);
    });
    return out.length ? out : [origin.i];
  },
  volanMatchStop(idx, stop, opIx) {
    if (!idx || !stop) return null;
    const op = (opIx == null) ? 0 : opIx;
    const pool = opIx == null ? idx.stops : BkkLib.cityStops(idx, op);
    const num = BkkLib.stopNum(stop.id);
    if (num && idx.byNum.has(op + ':' + num)) {
      const hit = idx.byNum.get(op + ':' + num);
      if (opIx == null || hit.op === op) return hit;
    }
    const key = BkkLib.norm(stop.name || '');
    if (key && idx.byKey.has(op + ':' + key)) return idx.byKey.get(op + ':' + key);
    const f = BkkLib.fold(stop.name || '');
    if (!f || f.length < 4) return null;
    const exact = pool.find((s) => s.fold === f);
    if (exact) return exact;
    const hits = pool.filter((s) => s.fold.indexOf(f) >= 0);
    if (!hits.length) return null;
    hits.sort((a, b) => {
      const ah = /autobusz-allomas|palyaudvar/.test(a.fold) ? 0 : 1;
      const bh = /autobusz-allomas|palyaudvar/.test(b.fold) ? 0 : 1;
      if (ah !== bh) return ah - bh;
      return a.fold.length - b.fold.length;
    });
    return hits[0];
  },
  volanDestHit(st, destKey, destFold) {
    if (!st) return false;
    if (destKey && (st.key === destKey || st.fold === destFold)) return true;
    const fk = destFold || BkkLib.fold(destKey || '');
    if (fk && fk.length >= 4 && st.fold.indexOf(fk) >= 0) return true;
    return false;
  },
  async volanSearchStops(q) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsSearchStops(idx, q, null);
  },
  async volanRoutesAtStop(stop) {
    const idx = await BkkLib.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, stop, null);
    if (!origin) return [];
    const seen = new Set();
    const out = [];
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      const route = trips[i][1];
      if (!route || seen.has(route)) continue;
      const ps = trips[i][2] || [];
      if (ps.indexOf(origin.i) < 0) continue;
      seen.add(route);
      out.push({
        id: 'gtfs:' + route,
        label: String(route),
        color: '#F9AB13',
        text: '#000000',
        type: 'COACH',
        source: 'gtfs',
      });
    }
    out.sort((a, b) => a.label.localeCompare(b.label, 'hu', { numeric: true }));
    return out;
  },
  async volanRemainingNames(stop, routeLabel) {
    const idx = await BkkLib.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, stop, null);
    if (!origin || !routeLabel) return [];
    const want = String(routeLabel);
    const byKey = new Map();
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      if (String(trips[i][1]) !== want) continue;
      const ps = trips[i][2] || [];
      const oidx = ps.indexOf(origin.i);
      if (oidx < 0) continue;
      for (let j = oidx + 1; j < ps.length; j++) {
        const st = idx.stops[ps[j]];
        if (!st || !st.key || byKey.has(st.key)) continue;
        byKey.set(st.key, { key: st.key, name: st.name, id: st.id });
      }
    }
    return Array.from(byKey.values());
  },
  citySearchAll(idx, q) {
    const f = BkkLib.fold(q);
    if (!f || !idx) return [];
    const best = new Map();
    (idx.stops || []).forEach((s) => {
      const fold = s.fold || BkkLib.fold(s.name);
      if (!fold || fold.indexOf(f) < 0) return;
      const op = (idx.ops || [])[s.op];
      if (!op) return;
      const prev = best.get(op.id);
      const start = fold.startsWith(f) ? 0 : 1;
      if (!prev || start < prev.start || (start === prev.start && s.name.localeCompare(prev.stop.name, 'hu') < 0)) {
        best.set(op.id, { stop: s, start: start });
      }
    });
    return Array.from(best.values()).map((row) => row.stop);
  },
  async citySearchStops(q, city) {
    if (!city) return [];
    const idx = await BkkLib.cityIndex();
    return BkkLib.gtfsSearchStops(idx, q, BkkLib.opIx(idx, city));
  },
  gtfsSearchStops(idx, q, opIx) {
    const f = BkkLib.fold(q);
    if (!f) return [];
    const pool = opIx == null || opIx < 0 ? idx.stops : BkkLib.cityStops(idx, opIx);
    const hits = pool.filter((s) => s.fold.indexOf(f) >= 0);
    hits.sort((a, b) => {
      const ah = /autobusz-allomas|palyaudvar/.test(a.fold) ? 0 : 1;
      const bh = /autobusz-allomas|palyaudvar/.test(b.fold) ? 0 : 1;
      if (ah !== bh) return ah - bh;
      const as = a.fold.startsWith(f) ? 0 : 1;
      const bs = b.fold.startsWith(f) ? 0 : 1;
      if (as !== bs) return as - bs;
      return a.name.localeCompare(b.name, 'hu');
    });
    return hits.slice(0, 20).map((s) => ({
      id: s.id,
      name: s.name,
      label: s.name,
      type: 'BUS',
    }));
  },
  async volanReachableDests(stop) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsReachableDests(idx, stop, null);
  },
  async cityReachableDests(stop, city) {
    if (!city) return { dests: [], destRoutes: {} };
    const idx = await BkkLib.cityIndex();
    return BkkLib.gtfsReachableDests(idx, stop, BkkLib.opIx(idx, city));
  },
  gtfsReachableDests(idx, stop, opIx) {
    const origin = BkkLib.volanMatchStop(idx, stop, opIx == null ? null : opIx);
    if (!origin) return { dests: [], destRoutes: {} };
    const indexes = opIx == null
      ? [origin.i]
      : BkkLib.cityStopIndexes(idx, stop, opIx);
    const byKey = new Map();
    const destRoutes = {};
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      const t = trips[i];
      const route = t[1];
      const ps = t[2] || [];
      const oidx = ps.findIndex((p) => indexes.indexOf(p) >= 0);
      if (oidx < 0) continue;
      for (let j = oidx + 1; j < ps.length; j++) {
        const st = idx.stops[ps[j]];
        if (!st || !st.key) continue;
        if (opIx != null && st.op !== opIx) continue;
        if (!byKey.has(st.key)) byKey.set(st.key, { key: st.key, name: st.name, id: st.id });
        if (!destRoutes[st.key]) destRoutes[st.key] = [];
        if (destRoutes[st.key].indexOf(route) < 0) destRoutes[st.key].push(route);
      }
    }
    const dests = Array.from(byKey.values());
    dests.sort((a, b) => a.name.localeCompare(b.name, 'hu'));
    return { dests, destRoutes };
  },
  async volanDepartures(stopId, dest, originName, minutesAfter) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, null, '#F9AB13', '#000000', 'coach', 'COACH', minutesAfter);
  },
  async cityDepartures(stopId, dest, originName, city, minutesAfter) {
    if (!city) return [];
    const idx = await BkkLib.cityIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, BkkLib.opIx(idx, city), '#0F6FC6', '#ffffff', 'bus', 'BUS', minutesAfter);
  },
  gtfsDepartures(idx, stopId, dest, originName, opIx, color, text, vehicle, rawType, minutesAfter) {
    const origin = BkkLib.volanMatchStop(idx, { id: stopId, name: originName || '' }, opIx == null ? null : opIx);
    if (!origin) return [];
    const indexes = opIx == null
      ? [origin.i]
      : BkkLib.cityStopIndexes(idx, { id: stopId, name: originName || '' }, opIx);
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold((dest && dest.name) || destKey || '');
    const horizon = clampMinutesAfter(minutesAfter);
    const rowCap = maxDepartureRows(horizon);
    const rows = [];
    const trips = idx.t || [];
    const todayMins = BkkLib.volanDay(0).mins;
    /* -1 catches yesterday's 24:xx trips, which are still tonight's buses. */
    const dayOffsets = [-1, 0, 1];
    for (let n = 0; n < dayOffsets.length; n++) {
      const day = BkkLib.volanDay(dayOffsets[n]);
      let earliest = day.mins - 1;
      let latest = day.mins + horizon;
      if (dayOffsets[n] > 0) {
        const remain = horizon - (1440 - todayMins);
        if (remain <= 0) continue;
        earliest = -1;
        latest = remain;
      }
      for (let i = 0; i < trips.length; i++) {
        const t = trips[i];
        if (!BkkLib.volanServiceOk(idx, t[0], day.ymd, day.dow)) continue;
        const route = t[1];
        const ps = t[2] || [];
        const ms = t[3] || [];
        const oidx = ps.findIndex((p) => indexes.indexOf(p) >= 0);
        if (oidx < 0) continue;
        const depMin = ms[oidx];
        if (depMin == null || depMin < earliest) continue;
        if (depMin > latest) continue;
        let didx = -1;
        if (destKey || destFold) {
          for (let j = oidx + 1; j < ps.length; j++) {
            if (BkkLib.volanDestHit(idx.stops[ps[j]], destKey, destFold)) {
              didx = j;
              break;
            }
          }
          if (didx < 0) continue;
        }
        const destStop = didx >= 0 ? idx.stops[ps[didx]] : idx.stops[ps[ps.length - 1]];
        const arrMin = didx >= 0 ? ms[didx] : null;
        const travel = (arrMin != null && arrMin >= depMin && arrMin - depMin <= 1440) ? (arrMin - depMin) : null;
        const depTs = day.midnight + depMin * 60;
        if (!departureStillDue(depTs)) continue;
        rows.push({
          tripId: 'gtfs:' + route + ':' + day.ymd + ':' + depMin + ':' + origin.i,
          label: route,
          color: color,
          text: text,
          vehicle: vehicle,
          rawType: rawType,
          dep: depTs,
          sched: depTs,
          delay: 0,
          head: (dest && dest.name) || (destStop && destStop.name) || '',
          platform: '',
          wheelchair: false,
          bikesAllowed: false,
          trainNumber: '',
          travel,
        });
      }
    }
    rows.sort((a, b) => a.dep - b.dep);
    const uniq = [];
    const seen = new Set();
    rows.forEach((row) => {
      const k = row.label + '|' + row.sched;
      if (seen.has(k)) return;
      seen.add(k);
      uniq.push(row);
    });
    return uniq.slice(0, rowCap);
  },
  mergeDestIndex(a, b) {
    const byKey = new Map();
    const destRoutes = {};
    const add = (idx) => {
      (idx && idx.dests || []).forEach((d) => {
        if (!d || !d.key) return;
        if (!byKey.has(d.key)) byKey.set(d.key, d);
        if (!destRoutes[d.key]) destRoutes[d.key] = [];
        ((idx.destRoutes && idx.destRoutes[d.key]) || d.routes || []).forEach((rid) => {
          if (rid && destRoutes[d.key].indexOf(rid) < 0) destRoutes[d.key].push(rid);
        });
      });
    };
    add(a);
    add(b);
    const dests = Array.from(byKey.values());
    dests.sort((x, y) => x.name.localeCompare(y.name, 'hu'));
    return { dests, destRoutes };
  },
  rowTrainNumber(row) {
    const direct = String((row && row.trainNumber) || '').trim();
    if (direct) return direct.replace(/^0+/, '') || direct;
    return BkkLib.trainNumberFromTripId(row && row.tripId);
  },
  mergeVolanRows(gtfs, otp, limit, opts) {
    const out = [];
    const byTime = new Map();
    const byNum = new Map();
    const trainNumberMatchWindowMin = 5;
    const generic = (c) => {
      const x = String(c || '').replace('#', '').toLowerCase();
      return !x || x === '4477aa';
    };
    const timeOf = (row) => `${String(row.label || '').toUpperCase()}|${Math.round(Number(row.sched || row.dep || 0) / 60)}`;
    const overlay = (prev, row) => {
      if (!prev || !row) return;
      if (generic(prev.color) && row.color && !generic(row.color)) {
        prev.color = row.color;
        if (row.text) prev.text = row.text;
      }
      const prevLab = String(prev.label || '');
      const rowLab = String(row.label || '');
      const brandedLab = (lab) => {
        const s = String(lab || '').trim();
        return !!(s && !BkkLib.genericRailLabel(s) && !/^\d+$/.test(s));
      };
      if (!brandedLab(prevLab) && brandedLab(rowLab)) {
        prev.label = rowLab;
      }
      const prevHasGps = !!BkkLib.mapGpsLatLon(prev.lat, prev.lon);
      const rowHasGps = !!BkkLib.mapGpsLatLon(row.lat, row.lon);
      const rowElvira = /^elvira:/i.test(String(row.tripId || ''));
      if (rowHasGps && (!prevHasGps || rowElvira)) {
        prev.lat = Number(row.lat);
        prev.lon = Number(row.lon);
      }
      const tid = String(row.tripId || '');
      if (BkkLib.isFutarTripId(tid)) prev.tripId = tid;
      else if (tid && !/^(elvira:|gtfs:)/.test(tid) && !prevHasGps) prev.tripId = tid;
      if ((!Array.isArray(prev.shape) || prev.shape.length < 2)
          && Array.isArray(row.shape) && row.shape.length >= 2) {
        prev.shape = row.shape;
      }
      ['vehicleId', 'licensePlate', 'model', 'vehicleStatus', 'stopDistancePercent'].forEach((k) => {
        if ((prev[k] == null || prev[k] === '') && row[k] != null && row[k] !== '') prev[k] = row[k];
      });
      if (!prev.platform && row.platform) prev.platform = String(row.platform);
      if (row.booking) prev.booking = true;
      if (!prev.trainNumber && row.trainNumber) prev.trainNumber = String(row.trainNumber);
      if (prev.travel == null && row.travel != null) prev.travel = row.travel;
      if (row.wheelchair) prev.wheelchair = true;
      if (row.bikesAllowed) prev.bikesAllowed = true;
    };
    const minuteOf = (row) => Math.round(Number((row && (row.sched || row.dep)) || 0) / 60);
    const numberKey = (number) => {
      const raw = String(number || '').trim();
      return BkkLib.emmaTrainNumber(raw) || raw.replace(/^0+/, '') || raw;
    };
    const registerNumber = (number, index) => {
      const key = numberKey(number);
      if (!key) return;
      const rows = byNum.get(key) || [];
      if (rows.indexOf(index) < 0) rows.push(index);
      byNum.set(key, rows);
    };
    const findNumberMatch = (number, minute) => {
      const rows = byNum.get(numberKey(number)) || [];
      if (!minute || !rows.length) return -1;
      let best = -1;
      let distance = trainNumberMatchWindowMin + 1;
      rows.forEach((index) => {
        const delta = Math.abs(minuteOf(out[index]) - minute);
        if (delta < distance) {
          best = index;
          distance = delta;
        }
      });
      return distance <= trainNumberMatchWindowMin ? best : -1;
    };
    const brandedHost = (row) => BkkLib.isTrainRow(row)
      && !BkkLib.numberedRailBadge(row)
      && (BkkLib.suburbanLineLabel(row && row.label)
        || (!BkkLib.genericRailLabel(row && row.label) && String((row && row.label) || '').trim()));
    const add = (row, skipUnmatched) => {
      if (!row || !departureStillDue(row.dep || row.sched)) return;
      const n = BkkLib.rowTrainNumber(row);
      const min = minuteOf(row);
      const numberMatch = n ? findNumberMatch(n, min) : -1;
      if (numberMatch >= 0) {
        overlay(out[numberMatch], row);
        return;
      }
      if (BkkLib.numberedRailBadge(row) && min) {
        const host = out.findIndex((p) => brandedHost(p) && minuteOf(p) === min);
        if (host >= 0) {
          overlay(out[host], row);
          registerNumber(n, host);
          return;
        }
      } else if (brandedHost(row) && min) {
        const numbered = out.findIndex((p) => BkkLib.numberedRailBadge(p) && minuteOf(p) === min);
        if (numbered >= 0) {
          overlay(out[numbered], row);
          registerNumber(n, numbered);
          return;
        }
      }
      /* Named IC (GÖCSEJ) vs generic FUTÁR IC at the same minute, when the
         train number on one side did not parse. Suburban G43/Z30 stay out.
         overlayOnly must not use this: GEMENC at the same minute would steal
         a FUTÁR IC and copy booking. Spec is trainNumber (+ minute). */
      if (!skipUnmatched && BkkLib.isTrainRow(row) && min) {
        const namedIc = (r) => BkkLib.isTrainRow(r)
          && !BkkLib.suburbanLineLabel(r && r.label)
          && !BkkLib.genericRailLabel(r && r.label)
          && !/^\d+$/.test(String((r && r.label) || '').trim());
        const genericIc = (r) => BkkLib.isTrainRow(r) && BkkLib.genericRailLabel(r && r.label);
        let pair = -1;
        if (namedIc(row)) {
          pair = out.findIndex((p) => genericIc(p) && minuteOf(p) === min);
        } else if (genericIc(row)) {
          pair = out.findIndex((p) => namedIc(p) && minuteOf(p) === min);
        }
        if (pair >= 0) {
          overlay(out[pair], row);
          const pn = BkkLib.rowTrainNumber(out[pair]) || n;
          registerNumber(pn, pair);
          registerNumber(n, pair);
          return;
        }
      }
      /* overlayOnly must not pair by label+minute: two ICs at the same
         minute would copy booking onto the other FUTÁR train. Spec is
         trainNumber (byNum) plus branded/numbered pairing above. */
      const tk = timeOf(row);
      if (!skipUnmatched && byTime.has(tk)) {
        overlay(out[byTime.get(tk)], row);
        return;
      }
      if (skipUnmatched) return;
      const idx = out.length;
      out.push(row);
      byTime.set(tk, idx);
      registerNumber(n, idx);
    };
    const overlayOnly = !!(opts && opts.overlayOnly);
    const hosts = overlayOnly ? (gtfs || []) : (otp || []);
    const extra = overlayOnly ? (otp || []) : (gtfs || []);
    hosts.forEach((row) => add(row, false));
    extra.forEach((row) => add(row, overlayOnly));
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    const cap = Number(limit);
    return out.slice(0, (Number.isFinite(cap) && cap > 0) ? cap : 12);
  },
});

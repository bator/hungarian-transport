import { cityIndexUrl, clampMinutesAfter, departureStillDue, mavIndexUrl, maxDepartureRows, tzDow, tzEpochSec, tzParts, tzShiftYmd, volanIndexUrl } from '../config.js';
import { codedError } from '../i18n.js';
import { BkkLib } from '../lib/core.js';
import { INDEX_SCHEMA } from '../tags.js';

/* A destination stop of a trip lies no farther than this from the picked
   destination's own coordinates. */
const DEST_NEAR_M = 1500;

Object.assign(BkkLib, {
  /* Cached per resolved URL, so two cards pointing at different index files do
     not serve each other's data. The URL carries a daily cache-buster, which
     also expires the entry on a dashboard left open overnight; the day before's
     copy of the same file is dropped then, not kept for days (each decoded
     index is tens of MB on a wall tablet). */
  _idx: new Map(),
  _indexBase(url) {
    return String(url).replace(/([?&])v=\d{8}(&|$)/, (_m, lead, tail) => (tail ? lead : '')).replace(/[?&]$/, '');
  },
  _cacheIndex(cache, url, p, max) {
    const base = BkkLib._indexBase(url);
    Array.from(cache.keys()).forEach((k) => {
      if (k !== url && BkkLib._indexBase(k) === base) cache.delete(k);
    });
    cache.set(url, p);
    while (cache.size > max) cache.delete(cache.keys().next().value);
  },
  _indexCached(url, httpKey, gunzipKey) {
    const cache = BkkLib._idx;
    const hit = cache.get(url);
    if (hit) return hit;
    const p = BkkLib._loadGtfsIndex(url, httpKey, gunzipKey).catch((err) => {
      cache.delete(url);
      throw err;
    });
    BkkLib._cacheIndex(cache, url, p, 8);
    return p;
  },
  /* One card's view of the library: the same functions, with that card's own
     index URLs (volanIndex / cityIndex / mavIndex from its config). The lookups
     reach the indexes through `this`, so a card's override follows its own
     calls across every await and never leaks to another card (review J-2).
     A card without overrides gets the library itself. */
  withIndexes(cfg) {
    const c = cfg || {};
    const ix = { volan: String(c.volanIndex || ''), city: String(c.cityIndex || ''), mav: String(c.mavIndex || '') };
    if (!ix.volan && !ix.city && !ix.mav) return BkkLib;
    return Object.create(BkkLib, { _ix: { value: ix } });
  },
  volanIndex() {
    return BkkLib._indexCached(volanIndexUrl(this && this._ix && this._ix.volan), 'errVolanHttp', 'errVolanGunzip');
  },
  cityIndex() {
    return BkkLib._indexCached(cityIndexUrl(this && this._ix && this._ix.city), 'errCityHttp', 'errCityGunzip');
  },
  _mavP: new Map(),
  /* When the index came back empty or failed: url -> the time of that answer.
     Opening a train map must not wait on a stalled server again for a while. */
  _mavEmptyAt: new Map(),
  _mavEmptyRetryMs: 10 * 60 * 1000,
  mavIndex() {
    const url = mavIndexUrl(this && this._ix && this._ix.mav);
    const cache = BkkLib._mavP;
    const hit = cache.get(url);
    if (hit) {
      const emptyAt = BkkLib._mavEmptyAt.get(url);
      if (!emptyAt || Date.now() - emptyAt < BkkLib._mavEmptyRetryMs) return hit;
      cache.delete(url);
      BkkLib._mavEmptyAt.delete(url);
    }
    const empty = () => ({ n: {}, l: {}, p: [] });
    const p = BkkLib._loadMavIndex(url).then((idx) => {
      const ok = idx && typeof idx === 'object' && Array.isArray(idx.p) && idx.p.length > 0;
      if (ok) BkkLib._mavEmptyAt.delete(url);
      else BkkLib._mavEmptyAt.set(url, Date.now());
      return idx && typeof idx === 'object' ? idx : empty();
    }).catch(() => {
      BkkLib._mavEmptyAt.set(url, Date.now());
      return empty();
    });
    BkkLib._cacheIndex(cache, url, p, 4);
    return p;
  },
  /* Headers and body under one timeout: a body that stalls after its
     headers must not leave an index promise pending until the page reloads.
     no-cache revalidates: Home Assistant answers 304 for an unchanged index,
     so a dashboard reload does not download megabytes again, while a rebuilt
     index (new ETag) is fetched at once. Resolves { status, bytes }, with
     bytes null on an HTTP error; rejects on a network error or timeout. */
  async _fetchIndexBytes(url) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), BkkLib._indexTimeoutMs) : null;
    try {
      const res = await fetch(url, { cache: 'no-cache', signal: ctrl ? ctrl.signal : undefined });
      if (!res.ok) return { status: res.status, bytes: null };
      return { status: res.status, bytes: new Uint8Array(await res.arrayBuffer()) };
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  _indexTimeoutMs: 45000,
  /* Index text, gunzipped when gzip; null when this browser cannot gunzip. */
  async _indexText(u8) {
    if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
      if (typeof DecompressionStream !== 'function') return null;
      const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'));
      return new Response(stream).text();
    }
    return new TextDecoder().decode(u8);
  },
  async _loadMavIndex(url) {
    let got;
    try {
      got = await BkkLib._fetchIndexBytes(url);
    } catch (_err) {
      return { n: {}, l: {}, p: [] };
    }
    if (!got.bytes) return { n: {}, l: {}, p: [] };
    const text = await BkkLib._indexText(got.bytes);
    if (text == null) return { n: {}, l: {}, p: [] };
    let idx;
    try {
      idx = JSON.parse(text);
    } catch (_e) {
      return { n: {}, l: {}, p: [] };
    }
    if (!idx || typeof idx !== 'object' || !idx.n || !Array.isArray(idx.p)) {
      return { n: {}, l: {}, p: [] };
    }
    /* Schema 2 needs the station table: variants name their stations by
       index into st, and the map pins sit on those coordinates. */
    return {
      v: Number(idx.v) || 1,
      feed: String(idx.feed || ''),
      st: Array.isArray(idx.st) ? idx.st : [],
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
  /* Schema 2 keeps every stop pattern of a train (936 sometimes runs on to
     Szombathely) with its stations: take the longest one that serves the
     card's two stations in order, else one serving the boarding station.
     Pins go on that pattern's own station coordinates. */
  pickMavVariant(variants, stations, boardName, alightName) {
    const same = (i, name) => !!name && BkkLib.nameEq(((stations || [])[i] || [])[0] || '', name);
    let firstBoard = null;
    for (const variant of variants || []) {
      const ids = (variant && variant[1]) || [];
      const b = ids.findIndex((i) => same(i, boardName));
      if (b < 0) continue;
      const a = ids.findIndex((i, k) => k > b && same(i, alightName));
      if (a > b) return { variant, board: b, alight: a };
      if (!firstBoard) firstBoard = { variant, board: b, alight: -1 };
    }
    if (firstBoard) return firstBoard;
    return variants && variants[0] ? { variant: variants[0], board: -1, alight: -1 } : null;
  },
  async mavRouteForRow(row, boardName, alightName) {
    try {
      const idx = await this.mavIndex();
      if (!idx || !Array.isArray(idx.p)) return null;
      const hit = BkkLib.mavShapeIndexKey(row, idx);
      if (!hit) return null;
      if (!Array.isArray(hit.ix)) {
        const shape = BkkLib.validMapShape(BkkLib.decodePolyline(idx.p[hit.ix] || ''));
        return shape.length >= 2 ? { shape, board: null, alight: null } : null;
      }
      const pick = BkkLib.pickMavVariant(hit.ix, idx.st, boardName, alightName);
      if (!pick) return null;
      const shape = BkkLib.validMapShape(BkkLib.decodePolyline(idx.p[pick.variant[0]] || ''));
      if (shape.length < 2) return null;
      const at = (pos) => {
        const st = pos >= 0 ? (idx.st || [])[pick.variant[1][pos]] : null;
        return st ? BkkLib.mapGpsLatLon(st[1] / 1e5, st[2] / 1e5) : null;
      };
      return { shape, board: at(pick.board), alight: at(pick.alight) };
    } catch (_e) {
      return null;
    }
  },
  async _loadGtfsIndex(url, httpKey, gunzipKey) {
    let got;
    try {
      got = await BkkLib._fetchIndexBytes(url);
    } catch (_err) {
      throw codedError('errIndexFetch');
    }
    if (!got.bytes) throw codedError(httpKey, got.status);
    const text = await BkkLib._indexText(got.bytes);
    if (text == null) throw codedError(gunzipKey);
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
    /* Schema 29: "g" holds lat, lon per stop in 1e-5 degrees (0, 0 = unknown). */
    const g = Array.isArray(idx.g) && idx.g.length === 2 * idx.s.length ? idx.g : null;
    idx.stops = (idx.s || []).map((row, i) => ({
      i,
      id: row[0],
      name: row[1],
      num: String(row[2] || ''),
      op: row.length > 3 ? Number(row[3]) : 0,
      key: BkkLib.norm(row[1]),
      fold: BkkLib.fold(row[1]),
      lat: g && (g[2 * i] || g[2 * i + 1]) ? g[2 * i] / 1e5 : null,
      lon: g && (g[2 * i] || g[2 * i + 1]) ? g[2 * i + 1] / 1e5 : null,
    }));
    /* The raw rows are in idx.stops now; a wall tablet keeps the index for
       the page, so the copy goes. */
    idx.g = null;
    idx.s = null;
    /* The shapes folder ("sh") sits next to the index file. */
    idx.url = url;
    idx.ops = (idx.o || []).map((row, i) => ({ i, id: row[0], name: row[1] }));
    idx.byNum = new Map();
    idx.byKey = new Map();
    idx.byId = new Map();
    idx.stops.forEach((st) => {
      const nk = st.op + ':' + st.num;
      const kk = st.op + ':' + st.key;
      if (st.num && !idx.byNum.has(nk)) idx.byNum.set(nk, st);
      if (st.key && !idx.byKey.has(kk)) idx.byKey.set(kk, st);
      if (st.id && !idx.byId.has(st.id)) idx.byId.set(st.id, st);
    });
    return idx;
  },
  gtfsStopById(idx, id) {
    if (!idx || !id) return null;
    const key = String(id);
    if (idx.byId) return idx.byId.get(key) || null;
    return (idx.stops || []).find((s) => s.id === key) || null;
  },
  /* The town of a city feed's operator ("Székesfehérvár — helyi" →
     Székesfehérvár); '' for the Volán index, whose names carry the town. */
  gtfsOpTown(idx, opIx) {
    const op = idx && opIx != null ? (idx.ops || [])[opIx] : null;
    return op ? String(op.name || '').split(' \u2014 ')[0].trim() : '';
  },
  /* A stop of an index as a picker hit: with its coordinates and its town,
     so planning starts from that stop and not from whatever a geocoder
     finds under the bare name (Tatabánya "Fő tér" in Budapest). */
  gtfsHit(idx, s, extra) {
    const hit = Object.assign({ id: s.id, name: s.name }, extra || {});
    if (BkkLib.finiteLatLon(s.lat, s.lon)) {
      hit.lat = s.lat;
      hit.lon = s.lon;
    }
    const area = idx && idx.ops && idx.ops.length ? BkkLib.gtfsOpTown(idx, s.op) : '';
    if (area) hit.area = area;
    return hit;
  },
  /* Picked stops that came without coordinates (a city search hit, an
     older history entry, a YAML origin) get them from the index they
     belong to. Anything else passes through. */
  async withGtfsCoords(stops) {
    const list = Array.isArray(stops) ? stops : [];
    const need = (s) => s && s.id && !BkkLib.finiteLatLon(s.lat, s.lon);
    const cityish = (s) => need(s) && BkkLib.stopIdFamily(s) === 'city';
    const volanish = (s) => need(s) && /^(?:hkir_|volan_)/i.test(String(s.id));
    const load = async (want, get) => {
      if (!list.some(want)) return null;
      try { return await get(); } catch (_e) { return null; }
    };
    const [city, volan] = await Promise.all([
      load(cityish, () => this.cityIndex()),
      load(volanish, () => this.volanIndex()),
    ]);
    return list.map((s) => {
      const idx = cityish(s) ? city : (volanish(s) ? volan : null);
      const hit = idx ? BkkLib.gtfsStopById(idx, s.id) : null;
      if (!hit || !BkkLib.finiteLatLon(hit.lat, hit.lon)) return s;
      const out = Object.assign({}, s, { lat: hit.lat, lon: hit.lon });
      const area = idx.ops && idx.ops.length ? BkkLib.gtfsOpTown(idx, hit.op) : '';
      if (area && !out.area) out.area = area;
      return out;
    });
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
  /* One GTFS service day. GTFS times count from noon minus 12 h, not from
     midnight: on the two DST days midnight is an hour off that base, and
     every departure of the day would shift by an hour. `base` is that moment
     in epoch seconds and `mins` is now in the day's own GTFS minutes: about
     now + 24h for yesterday (its 24:xx trips are tonight's), negative for
     tomorrow. */
  volanDay(offset, nowMs) {
    const off = offset || 0;
    const at = nowMs == null ? Date.now() : nowMs;
    const ymd = tzShiftYmd(tzParts(new Date(at)).ymd, off);
    const base = BkkLib.gtfsDayBase(ymd);
    return {
      ymd,
      dow: tzDow(ymd),
      mins: Math.floor((at / 1000 - base) / 60),
      base,
    };
  },
  gtfsDayBase(ymd) {
    return tzEpochSec(ymd, 720) - 12 * 3600;
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
    const hit = BkkLib.gtfsStopById(idx, id);
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
  /* Folded town names of an index: the "Town, " part of its stop names
     (the Volán feed names every stop so) and its operators' towns. */
  _gtfsTowns: new WeakMap(),
  gtfsTowns(idx) {
    if (!idx || typeof idx !== 'object') return new Set();
    let towns = BkkLib._gtfsTowns.get(idx);
    if (towns) return towns;
    towns = new Set();
    (idx.stops || []).forEach((st) => {
      const split = BkkLib.splitCityPrefix(st && st.name);
      if (split) towns.add(BkkLib.fold(split.city));
    });
    (idx.ops || []).forEach((op) => {
      const town = BkkLib.fold(BkkLib.gtfsOpTown(idx, op.i));
      if (town) towns.add(town);
    });
    BkkLib._gtfsTowns.set(idx, towns);
    return towns;
  },
  /* Whether a stop of a trip is the destination. A town ("Tata", "Eger")
     is any stop of that town, as a whole word: "Tata, vasútállomás" but not
     "Tatabánya", nor "Egerszalók". Any other name has to be that place:
     "Kórház" is not "Kórház tér" nor "Megyei Kórház", and when both name a
     town it is the same one. With the destination's coordinates, a stop of
     that name in another town (11 towns have a "Kossuth tér") is too far.
     opts: { town, lat, lon }, and the destination's place key and town
     (`place`, `townOf`) when the caller has them worked out already. */
  volanDestHit(st, destKey, destFold, opts) {
    if (!st) return false;
    opts = opts || {};
    const fk = destFold || BkkLib.fold(destKey || '');
    if (opts.town) {
      if (destKey && (st.key === destKey || st.fold === fk)) return true;
      return !!fk && fk.length >= 4 && (' ' + st.fold + ' ').indexOf(' ' + fk + ' ') >= 0;
    }
    const want = BkkLib.mapGpsLatLon(opts.lat, opts.lon);
    const here = BkkLib.mapGpsLatLon(st.lat, st.lon);
    if (want && here && BkkLib.haversineMeters(want, here) > DEST_NEAR_M) return false;
    if (destKey && (st.key === destKey || st.fold === fk)) return true;
    const name = destKey || fk;
    const place = opts.place != null ? opts.place : BkkLib.placeNameKey({ name });
    const mine = BkkLib._stopPlace(st);
    if (!place || place !== mine.place) return false;
    const town = opts.townOf != null ? opts.townOf : BkkLib._townOf(name);
    return !(town && mine.town && town !== mine.town);
  },
  /* An index stop's place key and town, worked out once per stop. */
  _stopPlaces: new WeakMap(),
  _stopPlace(st) {
    let hit = BkkLib._stopPlaces.get(st);
    if (!hit) {
      const name = st.name || st.key || '';
      hit = { place: BkkLib.placeNameKey({ name }), town: BkkLib._townOf(name) };
      BkkLib._stopPlaces.set(st, hit);
    }
    return hit;
  },
  _townOf(name) {
    const split = BkkLib.splitCityPrefix(name);
    return split ? BkkLib.fold(split.city) : '';
  },
  async volanSearchStops(q) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsSearchStops(idx, q, null);
  },
  async volanRoutesAtStop(stop) {
    const idx = await this.volanIndex();
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
    const idx = await this.volanIndex();
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
    const idx = await this.cityIndex();
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
    return hits.slice(0, 20).map((s) => BkkLib.gtfsHit(idx, s, { label: s.name, type: 'BUS' }));
  },
  async volanReachableDests(stop) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsReachableDests(idx, stop, null);
  },
  async cityReachableDests(stop, city) {
    if (!city) return { dests: [], destRoutes: {} };
    const idx = await this.cityIndex();
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
        if (!byKey.has(st.key)) byKey.set(st.key, BkkLib.gtfsHit(idx, st, { key: st.key }));
        if (!destRoutes[st.key]) destRoutes[st.key] = [];
        if (destRoutes[st.key].indexOf(route) < 0) destRoutes[st.key].push(route);
      }
    }
    const dests = Array.from(byKey.values());
    dests.sort((a, b) => a.name.localeCompare(b.name, 'hu'));
    return { dests, destRoutes };
  },
  async volanDepartures(stopId, dest, originName, minutesAfter) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, null, '#F9AB13', '#000000', 'coach', 'COACH', minutesAfter);
  },
  async cityDepartures(stopId, dest, originName, city, minutesAfter) {
    if (!city) return [];
    const idx = await this.cityIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, BkkLib.opIx(idx, city), '#0F6FC6', '#ffffff', 'bus', 'BUS', minutesAfter);
  },
  /* GTFS route_type (basic and extended codes) to the card's vehicle kind.
     Buses keep the caller's default: coach for Volán, bus for city feeds. */
  gtfsRouteKind(routeType, vehicle, rawType) {
    const rt = Number(routeType);
    if (!Number.isFinite(rt)) return { vehicle, rawType };
    if (rt === 0 || (rt >= 900 && rt < 1000)) return { vehicle: 'tram', rawType: 'TRAM' };
    if (rt === 1 || (rt >= 400 && rt < 500)) return { vehicle: 'subway', rawType: 'SUBWAY' };
    if (rt === 2 || (rt >= 100 && rt < 200) || rt === 300) return { vehicle: 'rail', rawType: 'RAIL' };
    if (rt === 4 || (rt >= 1000 && rt < 1100) || rt === 1200) return { vehicle: 'ferry', rawType: 'FERRY' };
    if (rt === 11 || rt === 800) return { vehicle: 'trolleybus', rawType: 'TROLLEYBUS' };
    return { vehicle, rawType };
  },
  /* Where a GTFS trip runs: its stops' coordinates and a reference into the
     sharded shapes folder (trip row element 5), carried on the departure row
     so the map needs neither the index nor Transitous. */
  gtfsTripGeo(idx, t, from, to) {
    const ps = (t && t[2]) || [];
    const stops = ps.map((p) => {
      const st = idx.stops[p];
      return st && st.lat != null ? [st.lat, st.lon] : null;
    });
    const ref = Number(t && t[5]);
    const sh = idx.sh;
    return {
      shape: sh && sh.dir && sh.n > 0 && Number.isInteger(ref) && ref >= 0
        ? { base: idx.url, dir: sh.dir, n: sh.n, id: ref }
        : null,
      stops,
      from,
      to: to >= 0 ? to : ps.length - 1,
    };
  },
  _shapeShards: new Map(),
  async _fetchJsonGz(url, timeoutMs) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs || 20000) : null;
    try {
      const res = await fetch(url, { signal: ctrl ? ctrl.signal : undefined });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      const u8 = new Uint8Array(buf);
      if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
        if (typeof DecompressionStream !== 'function') throw new Error('no gzip');
        const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
        return JSON.parse(await new Response(stream).text());
      }
      return JSON.parse(new TextDecoder().decode(u8));
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  /* One trip's shapes.txt polyline. Shard id % n holds it at id / n; the
     folder name carries a content hash, so the browser cache can keep it. */
  async gtfsShape(ref) {
    if (!ref || !ref.dir || !(ref.n > 0) || !(ref.id >= 0)) return [];
    const k = ref.id % ref.n;
    const url = new URL(`${ref.dir}/${String(k).padStart(2, '0')}.json.gz`, ref.base).href;
    let p = BkkLib._shapeShards.get(url);
    if (!p) {
      p = BkkLib._fetchJsonGz(url).catch((err) => {
        BkkLib._shapeShards.delete(url);
        throw err;
      });
      BkkLib._shapeShards.set(url, p);
      while (BkkLib._shapeShards.size > 16) BkkLib._shapeShards.delete(BkkLib._shapeShards.keys().next().value);
    }
    const shard = await p;
    const enc = shard && Array.isArray(shard.p) ? shard.p[Math.floor(ref.id / ref.n)] : '';
    return enc ? BkkLib.validMapShape(BkkLib.decodePolyline(enc)) : [];
  },
  /* GTFS geometry for a departure row: the shapes.txt route (the whole line,
     or the hop for kinds the card clips), the boarding and alighting stops,
     and the stop-to-stop line as a last resort. Null for non-GTFS rows. */
  async gtfsRowShape(row) {
    const geo = row && row.gtfs;
    if (!geo) return null;
    const pts = Array.isArray(geo.stops) ? geo.stops : [];
    const board = pts[geo.from] || null;
    const dest = pts[geo.to] || null;
    let shape = [];
    try {
      shape = await BkkLib.gtfsShape(geo.shape);
    } catch (_e) {
      shape = [];
    }
    if (shape.length >= 2 && !BkkLib.keepsFullLineShape(row) && board && dest) {
      const hop = BkkLib.clipShapeToBoardDest(shape, board, dest);
      if (hop.length >= 2) shape = hop;
    }
    return { shape, board, dest, stopLine: BkkLib.validMapShape(pts.filter(Boolean)) };
  },
  /* City name of a local operator ("Székesfehérvár — helyi" → Székesfehérvár),
     so Transitous stop lookups stay inside that city. */
  async stopArea(cfg) {
    if (BkkLib.mode(cfg || {}) !== 'helyi' || !cfg.city) return '';
    try {
      const idx = await this.cityIndex();
      const op = (idx.ops || []).find((o) => o.id === cfg.city);
      return op ? String(op.name || '').split(' \u2014 ')[0].trim() : '';
    } catch (_e) {
      return '';
    }
  },
  gtfsDepartures(idx, stopId, dest, originName, opIx, color, text, vehicle, rawType, minutesAfter) {
    const origin = BkkLib.volanMatchStop(idx, { id: stopId, name: originName || '' }, opIx == null ? null : opIx);
    if (!origin) return [];
    const indexes = opIx == null
      ? [origin.i]
      : BkkLib.cityStopIndexes(idx, { id: stopId, name: originName || '' }, opIx);
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold((dest && dest.name) || destKey || '');
    const destName = destKey || destFold;
    const destOpts = {
      town: !!destFold && !BkkLib.splitCityPrefix((dest && dest.name) || destKey || '')
        && BkkLib.gtfsTowns(idx).has(destFold),
      lat: dest && dest.lat,
      lon: dest && dest.lon,
      place: BkkLib.placeNameKey({ name: destName }),
      townOf: BkkLib._townOf(destName),
    };
    const horizon = clampMinutesAfter(minutesAfter);
    const rowCap = maxDepartureRows(horizon);
    const rows = [];
    const trips = idx.t || [];
    /* -1 catches yesterday's 24:xx trips, which are still tonight's buses;
       +1 is tomorrow morning when the look-ahead crosses its base. */
    const dayOffsets = [-1, 0, 1];
    for (let n = 0; n < dayOffsets.length; n++) {
      const day = BkkLib.volanDay(dayOffsets[n]);
      const earliest = day.mins - 1;
      const latest = day.mins + horizon;
      if (latest < 0) continue;
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
            if (BkkLib.volanDestHit(idx.stops[ps[j]], destKey, destFold, destOpts)) {
              didx = j;
              break;
            }
          }
          if (didx < 0) continue;
        }
        const destStop = didx >= 0 ? idx.stops[ps[didx]] : idx.stops[ps[ps.length - 1]];
        const arrMin = didx >= 0 ? ms[didx] : null;
        const travel = (arrMin != null && arrMin >= depMin && arrMin - depMin <= 1440) ? (arrMin - depMin) : null;
        const depTs = day.base + depMin * 60;
        if (!departureStillDue(depTs)) continue;
        const kind = BkkLib.gtfsRouteKind(t[4], vehicle, rawType);
        rows.push({
          gtfs: BkkLib.gtfsTripGeo(idx, t, oidx, didx),
          tripId: 'gtfs:' + route + ':' + day.ymd + ':' + depMin + ':' + origin.i,
          label: route,
          color: color,
          text: text,
          vehicle: kind.vehicle,
          rawType: kind.rawType,
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
    const byLoose = new Map();
    const trainNumberMatchWindowMin = 5;
    const generic = (c) => {
      const x = String(c || '').replace('#', '').toLowerCase();
      return !x || x === '4477aa';
    };
    const timeOf = (row) => `${String(row.label || '').toUpperCase()}|${Math.round(Number(row.sched || row.dep || 0) / 60)}`;
    const overlay = (prev, row) => {
      if (!prev || !row) return;
      const prevNum = String(prev.trainNumber || '').trim();
      const prevNumFromId = !!prevNum && BkkLib.isFutarTripId(prev.tripId)
        && prevNum === BkkLib.trainNumberFromTripId(prev.tripId);
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
      if (row.nationalPass) prev.nationalPass = true;
      if (row.reservationFreeSection) prev.reservationFreeSection = true;
      /* ELVIRA's number is the train's own; one read off a FUTÁR trip id
         may carry a digit too many (BKK_8624_67 is IC 862). */
      if (row.trainNumber && (!prevNum || (rowElvira && prevNumFromId))) {
        prev.trainNumber = String(row.trainNumber);
      }
      if (prev.travel == null && row.travel != null) prev.travel = row.travel;
      if (row.wheelchair) prev.wheelchair = true;
      if (row.bikesAllowed) prev.bikesAllowed = true;
    };
    const minuteOf = (row) => Math.round(Number((row && (row.sched || row.dep)) || 0) / 60);
    const numberKey = (number) => {
      const raw = String(number || '').trim();
      return BkkLib.emmaTrainNumber(raw) || raw.replace(/^0+/, '') || raw;
    };
    const registerNumber = (number, index, map = byNum) => {
      const key = numberKey(number);
      if (!key) return;
      const rows = map.get(key) || [];
      if (rows.indexOf(index) < 0) rows.push(index);
      map.set(key, rows);
    };
    const findNumberMatch = (number, minute, map = byNum) => {
      const rows = map.get(numberKey(number)) || [];
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
    /* A FUTÁR IC/EC whose only number is its trip id is also filed under
       the id less its last digit (BKK_8624_67: 862), for a number of the
       train's own (ELVIRA) to find within the same window. Suburban and Sz
       ids stay whole: BKK_4541_108 is train 4541, not 454. */
    const looseNumber = (row) => {
      if (!BkkLib.isTrainRow(row)) return '';
      if (!/^(IC|EC|IR|RJX)$/i.test(String(row.label || '').trim())) return '';
      const fromId = BkkLib.trainNumberFromTripId(row.tripId);
      const direct = String(row.trainNumber || '').trim();
      if (!fromId || (direct && direct !== fromId)) return '';
      return BkkLib.trainNumberLooseFromTripId(row.tripId);
    };
    const ownNumber = (row) => BkkLib.isTrainRow(row)
      && !!String(row.trainNumber || '').trim()
      && !BkkLib.isFutarTripId(row.tripId)
      && !BkkLib.suburbanLineLabel(row.label);
    /* Pairing by minute (branded/numbered, named IC, label) is for one
       departure seen twice. Two rows with train numbers of their own that
       differ are two trains: ELVIRA lists the whole station, so G44 4711 to
       Budapest-Déli leaves Kelenföld in the same minute as 8738 to Siófok. */
    const numbersOf = (row) => [BkkLib.rowTrainNumber(row), looseNumber(row)]
      .map(numberKey).filter(Boolean);
    const otherTrain = (a, b) => {
      const x = numbersOf(a);
      const y = numbersOf(b);
      return !!x.length && !!y.length && !x.some((n) => y.indexOf(n) >= 0);
    };
    const brandedHost = (row) => BkkLib.isTrainRow(row)
      && !BkkLib.numberedRailBadge(row)
      && (BkkLib.suburbanLineLabel(row && row.label)
        || (!BkkLib.genericRailLabel(row && row.label) && String((row && row.label) || '').trim()));
    const add = (row, skipUnmatched) => {
      if (!row || !departureStillDue(row.dep || row.sched)) return;
      const n = BkkLib.rowTrainNumber(row);
      const min = minuteOf(row);
      let numberMatch = n ? findNumberMatch(n, min) : -1;
      if (numberMatch < 0 && n && ownNumber(row)) numberMatch = findNumberMatch(n, min, byLoose);
      if (numberMatch >= 0) {
        overlay(out[numberMatch], row);
        return;
      }
      if (BkkLib.numberedRailBadge(row) && min) {
        const host = out.findIndex((p) => brandedHost(p) && minuteOf(p) === min && !otherTrain(p, row));
        if (host >= 0) {
          overlay(out[host], row);
          registerNumber(n, host);
          return;
        }
      } else if (brandedHost(row) && min) {
        const numbered = out.findIndex((p) => BkkLib.numberedRailBadge(p) && minuteOf(p) === min
          && !otherTrain(p, row));
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
          pair = out.findIndex((p) => genericIc(p) && minuteOf(p) === min && !otherTrain(p, row));
        } else if (genericIc(row)) {
          pair = out.findIndex((p) => namedIc(p) && minuteOf(p) === min && !otherTrain(p, row));
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
      if (!skipUnmatched && byTime.has(tk) && !otherTrain(out[byTime.get(tk)], row)) {
        overlay(out[byTime.get(tk)], row);
        return;
      }
      if (skipUnmatched) return;
      const idx = out.length;
      out.push(row);
      byTime.set(tk, idx);
      registerNumber(n, idx);
      registerNumber(looseNumber(row), idx, byLoose);
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

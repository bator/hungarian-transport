import { TRANSITOUS, clampMinutesAfter, departureStillDue, maxDepartureRows } from '../config.js';
import { t } from '../i18n.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  /* FUTÁR plan-trip times are epoch milliseconds. Values below 1e11 are seconds. */
  epochMs(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return 0;
    return n >= 1e11 ? n : n * 1000;
  },
  isoMs(value) {
    if (value == null || value === '') return 0;
    if (typeof value === 'number') return BkkLib.epochMs(value);
    const n = Date.parse(String(value));
    return Number.isFinite(n) && n > 0 ? n : 0;
  },
  async transitousFetch(path, params) {
    const q = new URLSearchParams();
    Object.keys(params || {}).forEach((key) => {
      const val = params[key];
      if (val == null || val === '') return;
      q.set(key, String(val));
    });
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 10000) : null;
    const base = String(TRANSITOUS).replace(/\/$/, '');
    const rel = String(path || '').indexOf('/') === 0 ? String(path) : ('/' + String(path || ''));
    try {
      const res = await fetch(`${base}${rel}?${q}`, {
        cache: 'no-store',
        signal: ctrl ? ctrl.signal : undefined,
        headers: {
          Accept: 'application/json',
        },
      });
      if (!res.ok) throw new Error(`Transitous HTTP ${res.status}`);
      /* Awaited here, so the timeout also covers the body. */
      return await res.json();
    } catch (err) {
      if (err && err.name === 'AbortError') throw new Error('Transitous timeout');
      throw err;
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  bkkTransitousStopIds(id) {
    const raw = String(id || '').trim();
    if (!/^BKK_/i.test(raw) || /^BKK_0055/i.test(raw)) return [];
    const body = raw.replace(/^BKK_/i, '');
    const ids = ['hu-bkk_' + body];
    if (/^CSF/i.test(body) && body.length > 3) ids.push('hu-bkk_F' + body.slice(3));
    return ids;
  },
  transitousFeedWant(id) {
    const s = String(id || '');
    if (/^BKK_0055/i.test(s)) return 'hu-mav_';
    if (/^BKK_/i.test(s)) return 'hu-bkk_';
    if (/^(volan_|AREA_CS|hkir_)/i.test(s)) return 'hu-volanbusz_';
    if (/^miskolc:/i.test(s)) return 'hu-mvk_';
    if (s.indexOf(':') > 0) return 'hu-';
    return '';
  },
  /* A stop does not move: every poll of an empty board geocoded both ends
     again. Answers stay for the page; a failed lookup is asked again, and
     an empty answer after ten minutes (a geocoder that had no data yet). */
  _geocodeCache: new Map(),
  _geocodeCacheMax: 200,
  _geocodeEmptyAt: new Map(),
  _geocodeEmptyRetryMs: 10 * 60 * 1000,
  transitousGeocode(text, opts) {
    const q = String(text || '').trim();
    if (!q) return Promise.resolve([]);
    const params = { text: q, language: 'hu' };
    const typ = opts && opts.type;
    if (typ === 'all') {
      /* omit type: STOP, ADDRESS and PLACE together */
    } else {
      params.type = typ || 'STOP';
    }
    const key = `${params.type || 'all'}|${q}`;
    const cache = BkkLib._geocodeCache;
    const empties = BkkLib._geocodeEmptyAt;
    const hit = cache.get(key);
    const emptyAt = empties.get(key);
    if (hit && !(emptyAt && Date.now() - emptyAt >= BkkLib._geocodeEmptyRetryMs)) return hit;
    if (hit) cache.delete(key);
    empties.delete(key);
    const run = BkkLib.transitousFetch('/api/v1/geocode', params)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        if (!list.length && cache.get(key) === run) empties.set(key, Date.now());
        return list;
      });
    run.catch(() => { if (cache.get(key) === run) cache.delete(key); });
    if (cache.size >= BkkLib._geocodeCacheMax) {
      const oldest = cache.keys().next().value;
      cache.delete(oldest);
      empties.delete(oldest);
    }
    cache.set(key, run);
    return run;
  },
  transitousInHungary(hit) {
    if (!hit) return false;
    if (/^hu-/i.test(String(hit.id || ''))) return true;
    if ((hit.areas || []).some((a) => /magyarorsz/i.test((a && a.name) || ''))) return true;
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
    return lat >= 45.7 && lat <= 48.65 && lon >= 16.05 && lon <= 22.95;
  },
  isPlaceStop(stop) {
    if (!stop) return false;
    if (/^(geo:|node\/|way\/)/i.test(String(stop.id || stop.stopId || ''))) return true;
    const kind = String(stop.kind || stop.stopKind || '');
    return kind === 'address' || kind === 'place';
  },
  transitousHitToStop(hit) {
    if (!hit || !hit.name) return null;
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    const areas = hit.areas || [];
    const city = (areas.find((a) => Number(a.adminLevel) === 8) || areas.find((a) => a.unique) || {}).name || '';
    const kindRaw = String(hit.type || '').toUpperCase();
    const kind = kindRaw === 'ADDRESS' ? 'address' : kindRaw === 'PLACE' ? 'place' : 'stop';
    let id = String(hit.id || '');
    if (!id && Number.isFinite(lat) && Number.isFinite(lon)) {
      id = 'geo:' + lat.toFixed(6) + ',' + lon.toFixed(6);
    }
    if (!id) return null;
    return {
      id,
      name: hit.name,
      label: city ? (hit.name + ' (' + city + ')') : hit.name,
      lat: Number.isFinite(lat) ? lat : undefined,
      lon: Number.isFinite(lon) ? lon : undefined,
      kind,
    };
  },
  plannerHitLabel(s, lang) {
    const tag = s && s.kind === 'address' ? t(lang, 'plannerAddress')
      : s && s.kind === 'place' ? t(lang, 'plannerPlace') : '';
    const base = (s && (s.label || s.name)) || '';
    return tag ? (base + ' \u00b7 ' + tag) : base;
  },
  async searchStopsTransitous(q, opts) {
    const hits = await BkkLib.transitousGeocode(q, opts);
    return (hits || [])
      .filter((hit) => BkkLib.transitousInHungary(hit))
      .map((hit) => BkkLib.transitousHitToStop(hit))
      .filter((s) => s && s.id)
      .slice(0, 20);
  },
  capPlannerHits(hits, n) {
    const limit = n || 24;
    const volan = [];
    const rest = [];
    (hits || []).forEach((s) => {
      if (!s || !s.id) return;
      if (/^(?:volan_|hkir_|AREA_CS)/i.test(String(s.id))) volan.push(s);
      else rest.push(s);
    });
    const out = [];
    const max = Math.max(volan.length, rest.length);
    for (let i = 0; i < max && out.length < limit; i++) {
      if (i < rest.length) out.push(rest[i]);
      if (out.length >= limit) break;
      if (i < volan.length) out.push(volan[i]);
    }
    return out;
  },
  async searchPlannerStops(apiKey, q) {
    const byId = new Map();
    try {
      const motis = await BkkLib.searchStopsTransitous(q, { type: 'all' });
      motis.forEach((s) => {
        if (!s || !s.id || /^hu-volanbusz_/i.test(s.id)) return;
        const mapped = String(s.id).replace(/^hu-bkk_/i, 'BKK_');
        const row = mapped !== s.id ? Object.assign({}, s, { id: mapped }) : s;
        if (byId.has(row.id)) return;
        byId.set(row.id, row);
      });
      await this.addTransitousVolanStops(byId, q, motis);
    } catch (_e) { /* GTFS / BKK still fill the list */ }
    try {
      (await this.searchStops(apiKey, q, 'all', '', { skipTransitousVolan: true })).forEach((s) => {
        if (!s || !s.id || byId.has(s.id)) return;
        byId.set(s.id, s);
      });
    } catch (_e) { /* Transitous hits are enough when the index is missing */ }
    /* City and Volán index hits get their coordinates first: the collapse
       tells two towns' same-named stops apart by them, and a picked stop
       is planned from where it is. */
    const placed = await this.withGtfsCoords(Array.from(byId.values()));
    const collapsed = BkkLib.collapseSameNameStops(placed);
    return BkkLib.capPlannerHits(collapsed, 24);
  },
  pickTransitousHit(hits, stop, kind) {
    const list = Array.isArray(hits) ? hits : [];
    if (!list.length) return null;
    const want = BkkLib.transitousFeedWant(stop && stop.id);
    const wantName = (stop && stop.name) || '';
    kind = String(kind || '').toLowerCase();
    /* A bare street name ("Béla utca") exists in many towns. When the stop's
       city is known and some hit lies in it, hits elsewhere are out. */
    const area = BkkLib.fold((stop && stop.area) || '');
    const inArea = (hit) => !!area && (hit.areas || []).some((a) => BkkLib.fold(a && a.name) === area);
    const areaHits = area ? list.filter((hit) => hit && inArea(hit)) : [];
    const busWanted = /aut[o\u00f3]busz|aut\.?\s*\u00e1ll/i.test(wantName);
    const near = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon);
    let best = null;
    let bestScore = -1;
    list.forEach((hit) => {
      if (!hit) return;
      if (areaHits.length && !inArea(hit)) return;
      const hid = String(hit.id || '');
      const hname = hit.name || '';
      let score = 0;
      const railWant = want === 'hu-mav_' || kind === 'rail' || kind === 'train';
      if (railWant && (/railway/i.test(hid) || hid.indexOf('hu-mav_') === 0)) score += 8;
      else if (want && hid.indexOf(want) === 0) score += 5;
      else if (railWant && hid.indexOf('hu-bkk_') === 0) score += 2;
      else if (/^hu-/.test(hid)) score += 1;
      if (/railway/i.test(hid) && railWant) score += 2;
      const wantTok = BkkLib.foldTokens(wantName);
      const hitTok = BkkLib.foldTokens(hname);
      const missing = wantTok.filter((tok) => !hitTok.some((y) => (
        y === tok || y.indexOf(tok) >= 0 || tok.indexOf(y) >= 0
      )));
      if (missing.length) score -= 4 * missing.length;
      if (BkkLib.foldTokenMatch(wantName, hname)) score += 3;
      if (BkkLib.fold(wantName) && BkkLib.fold(hname) === BkkLib.fold(wantName)) score += 2;
      if (!busWanted && /aut[o\u00f3]busz|aut\.?\s*\u00e1ll/i.test(hname)) score -= 4;
      /* Known stop coordinates (GTFS index) settle same-named stops. */
      const hpt = near && BkkLib.mapGpsLatLon(hit.lat, hit.lon);
      if (hpt) {
        const d = BkkLib.haversineMeters(near, hpt);
        if (d <= 400) score += 6;
        else if (d > 5000) score -= 6;
      }
      if (score > bestScore) {
        bestScore = score;
        best = hit;
      }
    });
    if (bestScore >= 3) return best;
    return (areaHits.length ? areaHits[0] : list[0]) || null;
  },
  transitousCoord(hit) {
    if (!hit) return '';
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return lat + ',' + lon;
    return String(hit.id || '');
  },
  async transitousPlace(stop, kind) {
    if (!stop) return '';
    /* A stop without coordinates (lat: null) is not at 0,0. */
    const pt = BkkLib.mapGpsLatLon(stop.lat, stop.lon);
    if (pt) return pt[0] + ',' + pt[1];
    const name = String(stop.name || '').trim();
    if (!name) return String(stop.id || '');
    try {
      const hits = await BkkLib.transitousGeocode(name);
      return BkkLib.transitousCoord(BkkLib.pickTransitousHit(hits, stop, kind));
    } catch (_e) {
      return '';
    }
  },
  async transitousStopRef(stop, kind) {
    const name = String((stop && (stop.name || stop.key)) || '').trim();
    const fromStop = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon)
      || BkkLib.knownStopCoords(stop);
    const mapped = BkkLib.bkkTransitousStopIds(stop && stop.id);
    for (let i = 0; i < mapped.length; i += 1) {
      try {
        const data = await BkkLib.transitousFetch('/api/v5/stoptimes', {
          stopId: mapped[i],
          n: '1',
        });
        const place = data && data.place;
        const pt = place && BkkLib.mapGpsLatLon(place.lat, place.lon);
        if (!pt) continue;
        const pname = String((place && place.name) || '').trim();
        if (name && pname
            && !BkkLib.foldTokenMatch(name, pname)
            && BkkLib.fold(name) !== BkkLib.fold(pname)) {
          continue;
        }
        return {
          id: mapped[i],
          name: pname || name,
          lat: pt[0],
          lon: pt[1],
        };
      } catch (_e) { /* parent CSF ids 404; child F ids usually work */ }
    }
    const queries = [];
    const area = String((stop && stop.area) || '').trim();
    if (name && area && BkkLib.fold(name).indexOf(BkkLib.fold(area)) < 0) queries.push(`${name} ${area}`);
    if (name) queries.push(name);
    let stripped = name;
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.'].forEach((sfx) => {
      const low = stripped.toLowerCase();
      if (low.endsWith(sfx)) stripped = stripped.slice(0, -sfx.length).trim();
    });
    if (stripped && stripped !== name) queries.push(stripped);
    const hits = [];
    const seen = {};
    for (let i = 0; i < queries.length; i += 1) {
      try {
        const found = await BkkLib.transitousGeocode(queries[i]);
        (found || []).forEach((hit) => {
          const id = hit && hit.id;
          if (!id || seen[id]) return;
          seen[id] = true;
          hits.push(hit);
        });
      } catch (_e) { /* next query */ }
    }
    let hit = BkkLib.pickTransitousHit(hits, stop, kind);
    if ((kind === 'rail' || kind === 'train') && hit && !/railway|hu-mav_/i.test(String(hit.id || ''))) {
      const rail = hits.find((h) => /railway|hu-mav_/i.test(String((h && h.id) || '')));
      if (rail) hit = rail;
    }
    const board = BkkLib.mapGpsLatLon(hit && hit.lat, hit && hit.lon) || fromStop;
    return {
      id: (hit && hit.id) || '',
      name: (hit && hit.name) || name,
      lat: board ? board[0] : null,
      lon: board ? board[1] : null,
    };
  },
  motisLegLabel(leg) {
    const short = String((leg && leg.routeShortName) || '').trim();
    if (short) return short;
    const display = String((leg && leg.displayName) || '').trim();
    if (display) return display;
    return String((leg && leg.tripShortName) || '').trim();
  },
  motisLegType(mode) {
    const m = String(mode || '').toUpperCase();
    if (m.indexOf('SUBURBAN') >= 0) return 'SUBURBAN';
    if (/RAIL|TRAIN/.test(m)) return 'RAIL';
    if (m.indexOf('TRAM') >= 0) return 'TRAM';
    if (m.indexOf('SUBWAY') >= 0 || m.indexOf('METRO') >= 0) return 'SUBWAY';
    if (m.indexOf('TROLLEY') >= 0) return 'TROLLEYBUS';
    if (m === 'COACH') return 'COACH';
    return 'BUS';
  },
  motisMatchesMode(leg, mode) {
    if (mode === 'all' || mode === 'helyi') return true;
    const m = String((leg && leg.mode) || '').toUpperCase();
    if (m === 'WALK') return false;
    const id = String((leg && (leg.tripId || leg.routeId)) || '');
    return BkkLib.routeMatchesMode({
      type: BkkLib.motisLegType(m),
      id: /volan/i.test(id) ? ('volan_' + id) : id,
      agencyId: id,
    }, mode || 'all');
  },
  rowsFromMotis(payload, dest, mode, horizon, parseOpts) {
    const its = (payload && payload.itineraries) || [];
    parseOpts = parseOpts || {};
    const now = Number(parseOpts.now) > 0
      ? Number(parseOpts.now)
      : Math.floor(Date.now() / 1000);
    const h = clampMinutesAfter(horizon);
    const latest = now + h * 60;
    const destName = (dest && (dest.name || dest.key)) || '';
    const out = [];
    its.forEach((it) => {
      if (Number(it.transfers || 0) > 0) return;
      const ride = (it.legs || []).filter((leg) => String(leg.mode || '').toUpperCase() !== 'WALK');
      if (ride.length !== 1) return;
      const leg = ride[0];
      if (!BkkLib.motisMatchesMode(leg, mode || 'all')) return;
      const startMs = BkkLib.isoMs(leg.startTime || it.startTime);
      const dep = startMs ? Math.round(startMs / 1000) : 0;
      if (!dep || dep > latest + 60) return;
      if (!parseOpts.keepPast && !departureStillDue(dep, now)) return;
      const endMs = BkkLib.isoMs(leg.endTime || it.endTime);
      const sec = Number(leg.duration);
      const fromSec = Number.isFinite(sec) && sec > 0
        ? sec
        : (startMs && endMs ? Math.round((endMs - startMs) / 1000) : 0);
      const travel = fromSec <= 0 ? null : Math.max(1, Math.round(fromSec / 60));
      const rawType = BkkLib.motisLegType(leg.mode);
      const label = BkkLib.motisLegLabel(leg);
      const color = String(leg.routeColor || '').replace(/#/g, '') || (rawType === 'RAIL' ? '4477AA' : '009EE3');
      const text = String(leg.routeTextColor || '').replace(/#/g, '') || 'ffffff';
      const tripRaw = String(leg.tripId || '').trim();
      const tripId = 'motis:' + (tripRaw || (dep + ':' + label));
      const trainNumber = /RAIL|SUBURBAN|TRAIN/.test(rawType)
        ? BkkLib.emmaTrainNumber(leg.tripShortName || label)
        : '';
      const fromStop = leg.from || {};
      const plat = String(
        fromStop.scheduledTrack || fromStop.track || fromStop.platform || '',
      ).trim();
      const row = {
        tripId,
        motisTripId: tripRaw,
        label,
        color: '#' + color,
        text: '#' + text,
        vehicle: BkkLib.vehicleKind({ type: rawType, id: tripRaw }),
        rawType,
        dep,
        sched: dep,
        delay: 0,
        head: String(leg.headsign || destName || ''),
        platform: plat,
        wheelchair: false,
        bikesAllowed: false,
        booking: !!(leg.mayRequireBooking || fromStop.mayRequireBooking || leg.booking),
        trainNumber,
        travel,
        shape: BkkLib.decodePolyline(leg.legGeometry),
      };
      BkkLib.applyRailBadge(row);
      out.push(row);
    });
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    return out;
  },
  async transitousDepartures(origin, dest, opts) {
    opts = opts || {};
    if (!dest || !(dest.key || dest.name || dest.id)) return [];
    const [fromPlace, toPlace] = await Promise.all([
      BkkLib.transitousPlace(origin),
      BkkLib.transitousPlace(dest),
    ]);
    if (!fromPlace || !toPlace) return [];
    const horizon = clampMinutesAfter(opts.minutesAfter);
    const anchor = Number(opts.anchorDep || 0);
    try {
      const params = {
        fromPlace,
        toPlace,
        timetableView: 'true',
        maxTransfers: '0',
        numItineraries: String(maxDepartureRows(horizon)),
        searchWindow: String(horizon * 60),
      };
      if (opts.time) params.time = String(opts.time);
      else if (anchor > 0) params.time = new Date(Math.max(0, anchor - 120) * 1000).toISOString();
      const data = await BkkLib.transitousFetch('/api/v5/plan', params);
      const parseOpts = anchor > 0 ? { now: anchor, keepPast: true } : {};
      return BkkLib.rowsFromMotis(data, dest, opts.mode || 'all', horizon, parseOpts);
    } catch (_e) {
      return [];
    }
  },
  isTrainRow(row) {
    return BkkLib.vehicleBucket(row) === 'rail';
  },
  /* Hop maps draw the whole published line (street or track), not a
     stop-to-stop chord. Bus/tram/metro/trolley/coach/rail all keep it
     (8E: Újpalota → Kelenföld, 2B: Jászai → Pacsirtatelep, G43: the
     pfaedle rails). Leaflet canvas at GPS zoom clips that; SVG does not. */
  keepsFullLineShape(row) {
    if (BkkLib.isTrainRow(row)) return true;
    const kind = BkkLib.vehicleBucket(row);
    return kind === 'tram' || kind === 'subway' || kind === 'trolley'
      || kind === 'bus' || kind === 'coach';
  },
  vehicleBucket(row) {
    if (!row) return '';
    if (typeof row === 'string') {
      const s = row.toLowerCase();
      if (/rail|train|suburban/.test(s)) return 'rail';
      if (/tram/.test(s)) return 'tram';
      if (/subway|metro/.test(s)) return 'subway';
      if (/trolley/.test(s)) return 'trolley';
      if (/ferry|boat/.test(s)) return 'ferry';
      if (/coach/.test(s)) return 'coach';
      if (/bus/.test(s)) return 'bus';
      return s;
    }
    const v = String(row.vehicle || '').toLowerCase();
    const typ = String(row.rawType || row.type || row.mode || '').toUpperCase();
    if (v === 'rail' || v === 'train' || /RAIL|SUBURBAN|TRAIN/.test(typ)) return 'rail';
    if (/^elvira:/i.test(String(row.tripId || ''))) return 'rail';
    if (v === 'tram' || typ.indexOf('TRAM') >= 0) return 'tram';
    if (v === 'subway' || v === 'metro' || /SUBWAY|METRO/.test(typ)) return 'subway';
    if (v === 'trolley' || v === 'trolleybus' || typ.indexOf('TROLLEY') >= 0) return 'trolley';
    if (v === 'ferry' || typ.indexOf('FERRY') >= 0) return 'ferry';
    if (v === 'coach' || typ === 'COACH' || /^gtfs:/i.test(String(row.tripId || ''))) return 'coach';
    if (v === 'bus' || typ === 'BUS') return 'bus';
    if (BkkLib.rowTrainNumber(row) && !/bus|tram|subway|trolley|coach/i.test(v)) return 'rail';
    return v || 'bus';
  },
  sameVehicleBucket(a, b) {
    const x = BkkLib.vehicleBucket(a);
    const y = BkkLib.vehicleBucket(b);
    if (!x || !y) return true;
    if (x === y) return true;
    return (x === 'bus' && y === 'coach') || (x === 'coach' && y === 'bus');
  },
  motisTripIdRaw(row) {
    const raw = String((row && (row.motisTripId || row.tripId)) || '').replace(/^motis:/i, '').trim();
    if (!raw || /^\d+:/.test(raw)) return '';
    return raw;
  },
  namedTrainAliasMatch(a, b) {
    const aliases = { ADRIA: ['AGRAM'], AGRAM: ['ADRIA'] };
    const tokens = (s) => String(s || '').toUpperCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .split(/[^A-Z]+/)
      .filter((t) => t.length >= 4 && !BkkLib.genericRailLabel(t));
    const ta = tokens(a);
    const tb = tokens(b);
    for (let i = 0; i < ta.length; i += 1) {
      const al = aliases[ta[i]] || [];
      for (let j = 0; j < al.length; j += 1) {
        if (tb.indexOf(al[j]) >= 0) return true;
      }
    }
    return false;
  },
  motisLabelMatch(a, b) {
    const la = String(a || '').trim().toUpperCase();
    const lb = String(b || '').trim().toUpperCase();
    if (!la || !lb) return false;
    if (la === lb) return true;
    if (lb.indexOf(la + ' ') === 0 || la.indexOf(lb + ' ') === 0) return true;
    if (BkkLib.genericRailLabel(la) || BkkLib.genericRailLabel(lb)) return false;
    const fa = BkkLib.fold(la);
    const fb = BkkLib.fold(lb);
    if (!fa || !fb) return false;
    if (fa === fb) return true;
    const esc = fa.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp('(?:^|[^a-z0-9])' + esc + '(?:[^a-z0-9]|$)').test(fb);
  },
  /* True when the Transitous departure lists where it goes (next stops or
     a terminus) and neither the hop's destination nor the row's headsign
     is among them. Unknown stops decide nothing. */
  motisTripElsewhere(m, row, dest) {
    const known = ((m && m.nextNames) || []).some(Boolean) || !!(m && m.tripTo);
    if (!known) return false;
    const wantDest = !!(dest && (dest.name || dest.key));
    const head = String((row && (row.head || row.headsign || row.tripTo)) || '').trim();
    if (!wantDest && !head) return false;
    if (wantDest && BkkLib.destOnMotisRow(m, dest)) return false;
    if (head && BkkLib.destOnMotisRow(m, { name: head, key: head })) return false;
    return true;
  },
  destOnMotisRow(m, dest) {
    const want = BkkLib.fold((dest && (dest.name || dest.key)) || '');
    if (!want) return false;
    const names = [];
    (m && m.nextNames || []).forEach((n) => names.push(n));
    if (m && m.tripTo) names.push(m.tripTo);
    return names.some((n) => {
      if (!n) return false;
      return BkkLib.fold(n) === want || BkkLib.foldTokenMatch(want, n);
    });
  },
  rowsFromMotisStopTimes(payload) {
    const sts = (payload && payload.stopTimes) || [];
    return sts.map((st) => {
      const place = (st && st.place) || {};
      const startMs = BkkLib.isoMs(place.departure || place.scheduledDeparture);
      const rawType = BkkLib.motisLegType(st.mode);
      const label = String(st.displayName || st.routeShortName || st.tripShortName || '').trim();
      const tripRaw = String((st && st.tripId) || '').trim();
      const destName = (st.tripTo && st.tripTo.name) || '';
      return {
        tripId: tripRaw ? ('motis:' + tripRaw) : '',
        motisTripId: tripRaw,
        label,
        vehicle: BkkLib.vehicleKind({ type: rawType, id: tripRaw }),
        rawType,
        dep: startMs ? Math.round(startMs / 1000) : 0,
        sched: startMs ? Math.round(startMs / 1000) : 0,
        head: String(st.headsign || destName || ''),
        tripTo: destName,
        nextNames: ((st.nextStops || []).map((p) => p && p.name).filter(Boolean)),
        trainNumber: /RAIL|SUBURBAN|TRAIN/.test(rawType)
          ? BkkLib.emmaTrainNumber(st.tripShortName || label)
          : '',
        shape: [],
      };
    }).filter((row) => row.motisTripId);
  },
  pickMotisVehicle(motis, row, dest) {
    const list = Array.isArray(motis) ? motis : [];
    const num = BkkLib.rowTrainNumber(row);
    const label = String((row && row.label) || '').trim().toUpperCase();
    const dep = Number((row && (row.dep || row.sched || row.depTs)) || 0);
    /* A row with a real line label (2, 26, 5020, S40) only takes a vehicle of
       that line: drawing another line's route is worse than drawing none.
       Generic rail labels (IC, S) and unlabelled rows may fall back on time. */
    const wantsId = !!(num || (label && !BkkLib.genericRailLabel(label)));
    let best = null;
    let bestScore = -1e9;
    const usable = (m) => {
      if (!m) return false;
      if (!BkkLib.sameVehicleBucket(row, m)) return false;
      const shape = BkkLib.validMapShape(m.shape);
      return shape.length >= 2 || !!BkkLib.motisTripIdRaw(m);
    };
    list.forEach((m) => {
      if (!usable(m)) return;
      const mNum = BkkLib.rowTrainNumber(m);
      const mLabel = String((m && m.label) || '').trim().toUpperCase();
      const sameNum = !!(num && mNum && num === mNum);
      const sameLabel = BkkLib.motisLabelMatch(label, mLabel);
      const sameAlias = !!(label && (
        BkkLib.namedTrainAliasMatch(label, mLabel)
        || BkkLib.namedTrainAliasMatch(label, String((m && m.head) || ''))
      ));
      const destOn = BkkLib.destOnMotisRow(m, dest);
      const dt = dep ? Math.abs(Number(m.dep || 0) - dep) : 0;
      if (dep && dt > 45 * 60 && !sameNum) return;
      const identity = sameNum || sameLabel || sameAlias;
      if (!identity && wantsId) return;
      /* A train whose listed stops reach neither the alighting station nor
         the row's own headsign is another train, whatever its number says:
         the number FUTÁR's trip id implies (BKK_87141 → 8714) can be the
         Siófok train's while the row runs Ajka → Budapest-Déli. */
      if (BkkLib.isTrainRow(row) && BkkLib.motisTripElsewhere(m, row, dest)) return;
      const destNear = destOn && (!dep || dt <= 12 * 60);
      if (!identity && !destNear) return;
      const score = (sameNum ? 1000 : 0)
        + (sameLabel ? 400 : 0)
        + (sameAlias ? 250 : 0)
        + (destNear ? 50 : 0)
        - Math.min(dt, 1800) / 60;
      if (score > bestScore) {
        best = m;
        bestScore = score;
      }
    });
    if (!best && dep) {
      const near = list.filter((m) => usable(m) && Math.abs(Number(m.dep || 0) - dep) <= 8 * 60);
      const toward = dest ? near.filter((m) => BkkLib.destOnMotisRow(m, dest)) : near;
      const pool = toward.length ? toward : near;
      if (pool.length === 1) {
        const dt0 = Math.abs(Number(pool[0].dep || 0) - dep);
        /* Trains may carry a different number in MOTIS than in FUTÁR, so a
           lone train due within 90 s is still the same run. */
        if (!wantsId || (BkkLib.isTrainRow(row) && dt0 <= 90)) {
          best = pool[0];
        }
      } else if (pool.length > 1 && !wantsId) {
        pool.sort((a, b) => Math.abs(Number(a.dep || 0) - dep) - Math.abs(Number(b.dep || 0) - dep));
        const gap = Math.abs(Number(pool[1].dep || 0) - dep) - Math.abs(Number(pool[0].dep || 0) - dep);
        if (gap >= 60 || toward.length === 1) {
          best = pool[0];
        }
      }
    }
    return best;
  },
  /* A trip's route does not change: the board, its map and the planner
     ask for it once. A failed fetch is asked again. */
  _motisTrips: new Map(),
  _motisTripsMax: 32,
  motisTripShape(tripId) {
    const raw = String(tripId || '').replace(/^motis:/i, '').trim();
    if (!raw || /^\d+:/.test(raw)) return Promise.resolve([]);
    const cache = BkkLib._motisTrips;
    /* Each caller gets its own list to cut or extend. */
    const hit = cache.get(raw);
    if (hit) return hit.then((pts) => pts.slice());
    const run = BkkLib.transitousFetch('/api/v5/trip', {
      tripId: raw,
      joinInterlinedLegs: 'true',
    }).then((data) => {
      const legs = (data && data.legs) || [];
      const pts = [];
      legs.forEach((leg) => {
        if (String((leg && leg.mode) || '').toUpperCase() === 'WALK') return;
        BkkLib.validMapShape(BkkLib.decodePolyline(leg && leg.legGeometry)).forEach((pt) => {
          const last = pts[pts.length - 1];
          if (last && last[0] === pt[0] && last[1] === pt[1]) return;
          pts.push(pt);
        });
      });
      return pts;
    });
    run.catch(() => { if (cache.get(raw) === run) cache.delete(raw); });
    if (cache.size >= BkkLib._motisTripsMax) cache.delete(cache.keys().next().value);
    cache.set(raw, run);
    return run.then((pts) => pts.slice());
  },
  async motisShapeForHop(row, origin, dest, opts) {
    opts = Object.assign({}, opts || {});
    const anchor = Number((row && (row.dep || row.sched || row.depTs)) || 0);
    if (anchor > 0) {
      opts.anchorDep = anchor;
      if (!opts.time) opts.time = new Date((anchor - 120) * 1000).toISOString();
    }
    const kind = BkkLib.vehicleBucket(row);
    let board = BkkLib.mapGpsLatLon(origin && origin.lat, origin && origin.lon)
      || BkkLib.knownStopCoords(origin);
    let hit = null;
    let source = '';
    let hopShape = [];
    let fullShape = [];
    try {
      const ref = await BkkLib.transitousStopRef(origin, kind);
      if (ref && BkkLib.mapGpsLatLon(ref.lat, ref.lon)) {
        board = BkkLib.mapGpsLatLon(ref.lat, ref.lon);
      }
      if (ref && ref.id) {
        const data = await BkkLib.transitousFetch('/api/v5/stoptimes', {
          stopId: ref.id,
          time: opts.time || undefined,
          n: '40',
          fetchStops: 'true',
        });
        if (!board && data && data.place) {
          board = BkkLib.mapGpsLatLon(data.place.lat, data.place.lon);
        }
        const sts = BkkLib.rowsFromMotisStopTimes(data);
        hit = BkkLib.pickMotisVehicle(sts, row, dest);
        if (hit) source = 'stoptimes';
      }
    } catch (_e) { /* hop plan is the fallback */ }
    if (!hit) {
      const motis = await BkkLib.transitousDepartures(origin, dest, opts);
      hit = BkkLib.pickMotisVehicle(motis, row, dest);
      hopShape = BkkLib.validMapShape(hit && hit.shape);
      if (hit) source = 'plan';
    } else {
      hopShape = BkkLib.validMapShape(hit.shape);
    }
    const rawId = BkkLib.motisTripIdRaw(hit);
    if (rawId) {
      try {
        fullShape = await BkkLib.motisTripShape(rawId);
      } catch (_e) {
        fullShape = [];
      }
    }
    /* Prefer the full trip polyline (tracks). hopShape from a MOTIS plan is
       often just the stop-to-stop hop cut. */
    let shape = fullShape.length >= 2 ? fullShape : hopShape;
    let destPt = BkkLib.mapGpsLatLon(dest && dest.lat, dest && dest.lon)
      || BkkLib.knownStopCoords(dest);
    if (!BkkLib.keepsFullLineShape(row)) {
      if (!destPt && dest && (dest.name || dest.key || dest.id)) {
        try {
          const destRef = await BkkLib.transitousStopRef(dest, kind);
          destPt = destRef && BkkLib.mapGpsLatLon(destRef.lat, destRef.lon);
        } catch (_e) {
          destPt = null;
        }
      }
      const clipped = BkkLib.clipShapeToBoardDest(shape, board, destPt);
      if (clipped.length >= 2) shape = clipped;
    }
    return {
      shape: shape,
      board: board,
      dest: destPt,
      source: source,
      hit: hit,
      coversHead: BkkLib.motisCoversHead(hit, row),
    };
  },
  /* An empty board (a city line at night) stays empty for a while: after a
     fill that found nothing (or failed), the same pair is not asked again
     for ten minutes, instead of a plan on every poll. */
  _transitousEmptyAt: new Map(),
  _transitousEmptyRetryMs: 10 * 60 * 1000,
  _transitousFillKey(origin, dest, opts) {
    const o = origin || {};
    const d = dest || {};
    return [o.id || '', o.name || '', d.id || '', d.key || '', d.name || '',
      (opts && opts.mode) || 'all', clampMinutesAfter(opts && opts.minutesAfter)].join('|');
  },
  async fillTransitousIfEmpty(rows, origin, dest, opts) {
    const list = Array.isArray(rows) ? rows : [];
    if (list.length) return list;
    const key = BkkLib._transitousFillKey(origin, dest, opts);
    const empties = BkkLib._transitousEmptyAt;
    const now = Date.now();
    if (now - (empties.get(key) || 0) < BkkLib._transitousEmptyRetryMs) return list;
    empties.forEach((ts, k) => { if (now - ts >= BkkLib._transitousEmptyRetryMs) empties.delete(k); });
    try {
      const extra = await BkkLib.transitousDepartures(origin, dest, opts);
      if (extra.length) empties.delete(key);
      else empties.set(key, Date.now());
      return BkkLib.mergeVolanRows(extra, list, maxDepartureRows(opts && opts.minutesAfter));
    } catch (_e) {
      return list;
    }
  },
  /* MOTIS v5: itinerary duration is seconds; leg duration is seconds;
     startTime/endTime are ISO-8601. */
  journeyFromMotis(payload) {
    const its = (payload && payload.itineraries) || [];
    if (!its.length) return null;
    return BkkLib.motisJourney(BkkLib.bestItinerary(its, (it) => Date.parse(it.endTime || '')));
  },
  /* Every itinerary of a MOTIS plan, in the order Transitous returns them
     (by departure, or by arrival for arriveBy). */
  journeysFromMotis(payload) {
    const its = (payload && payload.itineraries) || [];
    return its.map((it) => BkkLib.motisJourney(it)).filter(Boolean);
  },
  motisJourney(best) {
    if (!best) return null;
    const legs = [];
    const tripStart = BkkLib.isoMs(best.startTime);
    let prevEnd = tripStart;
    (best.legs || []).forEach((leg) => {
      const walk = String(leg.mode || '').toUpperCase() === 'WALK';
      const startMs = BkkLib.isoMs(leg.startTime);
      const endMs = BkkLib.isoMs(leg.endTime);
      const sec = Number(leg.duration);
      const fromSec = Number.isFinite(sec) && sec > 0
        ? sec
        : (startMs && endMs ? Math.round((endMs - startMs) / 1000) : 0);
      let minutes = fromSec <= 0 ? 0 : Math.max(1, Math.round(fromSec / 60));
      let meters = Math.max(0, Math.round(Number(leg.distance || 0)));
      if (!meters && walk) meters = minutes * 80;
      if (minutes <= 0) {
        if (!walk || meters < 25) return;
        minutes = 1;
      }
      let waitMin = 0;
      if (!walk && startMs && prevEnd) {
        waitMin = Math.round((startMs - prevEnd) / 60000);
        if (waitMin < 1) waitMin = 0;
      }
      if (endMs) prevEnd = endMs;
      else if (startMs) prevEnd = startMs;
      const from = leg.from || {};
      const to = leg.to || {};
      const fromLat = Number(from.lat);
      const fromLon = Number(from.lon);
      const toLat = Number(to.lat);
      const toLon = Number(to.lon);
      const row = {
        walk,
        startMs: startMs || undefined,
        endMs: endMs || undefined,
        minutes,
        meters,
        label: walk ? '' : BkkLib.motisLegLabel(leg),
        headsign: String(leg.headsign || ''),
        color: String(leg.routeColor || '').replace(/#/g, ''),
        text: String(leg.routeTextColor || '').replace(/#/g, ''),
        from: String(from.name || ''),
        to: String(to.name || ''),
        waitMin,
        tripId: String(leg.tripId || ''),
        shape: BkkLib.decodePolyline(leg.legGeometry),
        fromLat: Number.isFinite(fromLat) ? fromLat : undefined,
        fromLon: Number.isFinite(fromLon) ? fromLon : undefined,
        toLat: Number.isFinite(toLat) ? toLat : undefined,
        toLon: Number.isFinite(toLon) ? toLon : undefined,
      };
      if (!walk) {
        /* The rail default stands in for a missing colour on trains only; a
           bus or tram without one keeps "" and the card's own default. */
        const rail = /RAIL|SUBURBAN/.test(BkkLib.motisLegType(leg.mode));
        const badge = BkkLib.applyRailBadge({
          label: row.label,
          color: row.color || (rail ? '4477AA' : ''),
        });
        row.color = String(badge.color || row.color).replace(/#/g, '');
        if (badge.text) row.text = String(badge.text).replace(/#/g, '');
      }
      legs.push(row);
    });
    if (!legs.length) return null;
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    const walkMin = legs.reduce((sum, leg) => sum + (leg.walk ? leg.minutes : 0), 0);
    return {
      durationMin: Math.max(1, Math.round(Number(best.duration || 0) / 60)),
      walkMin,
      waitMin,
      transfers: Math.max(0, Number(best.transfers || 0)),
      startMs: BkkLib.isoMs(best.startTime) || undefined,
      endMs: BkkLib.isoMs(best.endTime) || undefined,
      legs,
    };
  },
  /* MOTIS plan between two places. opts.time (epoch ms) plans for that
     moment instead of now; opts.arriveBy makes it the arrival time. */
  async transitousPlan(origin, dest, opts) {
    opts = opts || {};
    const places = await Promise.all([
      BkkLib.transitousPlace(origin),
      BkkLib.transitousPlace(dest),
    ]);
    const fromPlace = places[0];
    const toPlace = places[1];
    if (!fromPlace || !toPlace) return null;
    const planParams = {
      fromPlace,
      toPlace,
      numItineraries: String(opts.numItineraries || 12),
      searchWindow: String(clampMinutesAfter(opts.minutesAfter) * 60),
    };
    if (Number(opts.time) > 0) planParams.time = new Date(Number(opts.time)).toISOString();
    if (opts.arriveBy) planParams.arriveBy = 'true';
    return BkkLib.transitousFetch('/api/v5/plan', planParams);
  },
  async transitousJourney(origin, dest, opts) {
    try {
      const data = await BkkLib.transitousPlan(origin, dest, opts);
      const journey = BkkLib.journeyFromMotis(data);
      if (journey && journey.legs.some((leg) => !leg.walk)) return journey;
      return null;
    } catch (_e) {
      return null;
    }
  },
  async transitousJourneys(origin, dest, opts) {
    try {
      const data = await BkkLib.transitousPlan(origin, dest, opts);
      return BkkLib.journeysFromMotis(data).filter((j) => j.legs.some((leg) => !leg.walk));
    } catch (_e) {
      return [];
    }
  },
  /* Direct board rows hide the transfer block unless the itinerary is a
     real transfer that arrives sooner than the fastest listed ride. */
  journeyBeatsBoard(journey, rows) {
    if (!journey || !Array.isArray(journey.legs)) return false;
    const rides = journey.legs.filter((leg) => !leg.walk);
    if (rides.length < 2) return false;
    const dur = Number(journey.durationMin);
    if (!Number.isFinite(dur) || dur <= 0) return false;
    const travels = (Array.isArray(rows) ? rows : []).map((row) => Number(
      row && (row.travelMin != null ? row.travelMin : row.travel)
    )).filter((n) => Number.isFinite(n) && n > 0);
    if (!travels.length) return true;
    return dur < Math.min.apply(null, travels);
  },
});

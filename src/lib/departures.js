import { DEPART_MINUTES_AFTER, clampMinutesAfter, departureStillDue, maxDepartureRows } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  async addTransitousVolanStops(byId, q, motisHits) {
    const motis = motisHits || await BkkLib.searchStopsTransitous(q);
    let idx = null;
    try {
      idx = await this.volanIndex();
    } catch (_e) {
      idx = null;
    }
    motis.forEach((s) => {
      if (!s || !s.id || !/^hu-volanbusz_/i.test(s.id)) return;
      let id = s.id;
      let name = s.name || '';
      if (idx) {
        const hit = BkkLib.volanMatchStop(idx, { id: s.id, name: s.name }, null);
        if (hit && hit.id) {
          id = hit.id;
          name = hit.name || name;
        }
      }
      if (!id || byId.has(id)) return;
      const num = BkkLib.stopNum(id) || BkkLib.stopNum(s.id);
      if (num) {
        const clash = Array.from(byId.values()).find((x) => BkkLib.stopNum(x.id) === num);
        if (clash) return;
      }
      byId.set(id, {
        id,
        name,
        label: name,
        lat: s.lat,
        lon: s.lon,
      });
    });
  },
  async searchStops(apiKey, q, mode, city, opts) {
    const byId = new Map();
    if (mode === 'volan' || mode === 'all') {
      try {
        (await this.volanSearchStops(q)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
      try {
        if (!(opts && opts.skipTransitousVolan)) {
          await this.addTransitousVolanStops(byId, q);
        }
      } catch (_e) { /* Transitous names are extra coverage */ }
    }
    if (mode === 'all') {
      try {
        const idx = await this.cityIndex();
        const seenFold = new Set();
        BkkLib.citySearchAll(idx, q).forEach((s) => {
          const opId = BkkLib.cityOpId(idx, s);
          const op = (idx.ops || []).find((o) => o.id === opId);
          const city = op ? String(op.name).split(' — ')[0] : '';
          const fold = BkkLib.fold(s.name) + '|' + opId;
          if (!opId || seenFold.has(fold)) return;
          seenFold.add(fold);
          byId.set(s.id, {
            id: s.id,
            name: s.name,
            label: city ? (s.name + ' (' + city + ')') : s.name,
            city: true,
          });
        });
      } catch (_e) { /* city index optional */ }
    }
    if (mode === 'helyi') {
      try {
        (await this.citySearchStops(q, city)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
      return BkkLib.groupStops(Array.from(byId.values())).slice(0, 20);
    }
    if (BkkLib.hasFutar(apiKey)) {
      const queries = [q].concat(BkkLib.aliasQueries(q));
      for (let i = 0; i < queries.length; i++) {
        let data;
        try {
          data = await BkkLib.fetch(apiKey, 'search.json', { query: queries[i] }, true);
        } catch (err) {
          /* An alias (a rate limit on the second request, say) does not
             take back what the typed name found already. */
          if (!byId.size || (i === 0 && mode !== 'volan' && mode !== 'all')) throw err;
          break;
        }
        const stops = ((((data || {}).data || {}).references) || {}).stops || {};
        Object.values(stops).forEach((s) => {
          if (!s || !s.id || !s.name) return;
          if (/^STOP_/i.test(String(s.id))) return;
          if (mode === 'mav' && !BkkLib.isMavStop(s)) return;
          if (mode === 'volan' && !BkkLib.isVolanStop(s)) return;
          if (mode === 'bkk' && !BkkLib.isBkkStop(s)) return;
          const num = BkkLib.stopNum(s.id);
          if ((mode === 'volan' || mode === 'all') && num && BkkLib.isVolanStop(s)) {
            const clash = Array.from(byId.values()).find((x) => BkkLib.stopNum(x.id) === num);
            if (clash) return;
          }
          byId.set(s.id, s);
        });
      }
    }
    let hits;
    if (mode === 'all') {
      const mav = [];
      const volan = [];
      const city = [];
      const rest = [];
      Array.from(byId.values()).forEach((s) => {
        if (s.city) city.push(s);
        else if (BkkLib.isMavStop(s)) mav.push(s);
        else if (BkkLib.isVolanStop(s)) volan.push(s);
        else rest.push(s);
      });
      const groups = [
        BkkLib.groupStops(rest),
        BkkLib.groupStops(mav),
        BkkLib.groupStops(volan),
        city,
      ];
      hits = [];
      const max = Math.max(groups[0].length, groups[1].length, groups[2].length, groups[3].length);
      for (let i = 0; i < max; i++) {
        groups.forEach((g) => { if (g[i]) hits.push(g[i]); });
      }
    } else {
      hits = BkkLib.groupStops(Array.from(byId.values()));
    }
    hits = hits.map((h) => {
      if (h.city && h.label) return h;
      let label = BkkLib.labelAliased(q, h);
      if (mode === 'all') {
        if (BkkLib.isMavStop(h)) label += ' (M\u00c1V)';
        else if (BkkLib.isVolanStop(h)) label += ' (Vol\u00e1n)';
      }
      return Object.assign({}, h, { label: label });
    });
    const foldedQ = BkkLib.fold(q);
    hits.sort((a, b) => {
      const as = (foldedQ.includes('arpad hid') && BkkLib.fold(a.name).includes('goncz arpad')) ? 0 : 1;
      const bs = (foldedQ.includes('arpad hid') && BkkLib.fold(b.name).includes('goncz arpad')) ? 0 : 1;
      if (as !== bs) return as - bs;
      return (a.label || a.name).localeCompare(b.label || b.name, 'hu');
    });
    if (mode === 'all') hits = BkkLib.collapseSameNameStops(hits);
    const cities = hits.filter((h) => h.city).slice(0, 40);
    const other = hits.filter((h) => !h.city).slice(0, 20);
    return cities.concat(other);
  },
  async remainingNames(apiKey, cache, stop, routeId) {
    const { variants, stops } = await BkkLib.loadRoutePattern(apiKey, cache, routeId);
    const names = [];
    const seen = new Set();
    variants.forEach((v) => {
      const idx = (v.ids || []).findIndex((id) => (
        BkkLib.stopMatchesOrigin(Object.assign({ id }, stops[id] || {}), stop)
      ));
      if (idx < 0) return;
      v.ids.slice(idx + 1).forEach((id) => {
        const raw = (stops[id] || {}).name;
        const n = BkkLib.placeNameKey({ name: raw }) || BkkLib.stationKey(raw) || BkkLib.norm(raw);
        if (!raw || !n || seen.has(n)) return;
        seen.add(n);
        names.push({ key: n, name: raw, id });
      });
    });
    return names;
  },
  originIndex(sts, stops, origin) {
    if (!origin) return 0;
    const oid = origin.id || origin;
    const on = origin.name || '';
    const originObj = { id: oid, name: on };
    let idx = (sts || []).findIndex((s) => (
      BkkLib.stopMatchesOrigin(Object.assign({ id: s.stopId }, stops[s.stopId] || {}), originObj)
    ));
    return idx;
  },
  stopMatchesOrigin(stop, origin) {
    if (!stop || !origin) return false;
    const oid = origin.id || origin;
    const oname = origin.name || '';
    if (stop.id && BkkLib.sameStop(stop.id, oid)) return true;
    if (stop.parentStationId && BkkLib.sameStop(stop.parentStationId, oid)) return true;
    if (oname && BkkLib.nameEq(stop.name, oname)) return true;
    return false;
  },
  /* Parent stop-areas (BKK_CSF…) cap live departures at ~60 rows, which is
     only ~15 min at Keleti. Query the child poles that actually serve the
     selected routes so minutesAfter can fill. */
  async loadRoutePattern(apiKey, cache, routeId) {
    const hit = BkkLib._cached(cache, routeId);
    if (hit && hit.variants) return hit;
    if (BkkLib._inflight[routeId]) return BkkLib._inflight[routeId];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, 'route-details.json', {
          routeId,
          includeReferences: 'true',
        }, true);
        const entry = (data.data || {}).entry || {};
        const packed = {
          variants: (entry.variants || []).map((v) => ({
            name: v.name,
            ids: v.stopIds || [],
          })),
          stops: ((data.data || {}).references || {}).stops || {},
        };
        return BkkLib._store(cache, routeId, packed);
      } finally {
        delete BkkLib._inflight[routeId];
      }
    })();
    BkkLib._inflight[routeId] = p;
    return p;
  },
  polesFromPatterns(patterns, origin, destKey) {
    const poles = new Set();
    (patterns || []).forEach((pat) => {
      if (!pat) return;
      const variants = pat.variants || [];
      const stops = pat.stops || {};
      variants.forEach((v) => {
        const ids = v.ids || [];
        const oidx = ids.findIndex((id) => (
          BkkLib.stopMatchesOrigin(Object.assign({ id }, stops[id] || {}), origin)
        ));
        if (oidx < 0) return;
        if (destKey) {
          const hits = ids.slice(oidx + 1).some((id) => (
            BkkLib.nameEq((stops[id] || {}).name, destKey)
          ));
          if (!hits) return;
        }
        poles.add(ids[oidx]);
      });
    });
    return Array.from(poles);
  },
  /* Poles of the loaded route patterns that belong to the stop area parentId. */
  childPolesOf(cache, routeIds, parentId) {
    const kids = new Set();
    (routeIds || []).forEach((rid) => {
      const stops = (BkkLib._cached(cache, rid) || {}).stops || {};
      Object.values(stops).forEach((st) => {
        if (st && st.id && st.parentStationId && BkkLib.sameStop(st.parentStationId, parentId)) kids.add(st.id);
      });
    });
    return kids;
  },
  async originPoles(apiKey, cache, origin, routeIds, destKey) {
    const routes = (routeIds || []).filter(Boolean);
    const patterns = await Promise.all(routes.map(async (rid) => {
      try {
        return await BkkLib.loadRoutePattern(apiKey, cache, rid);
      } catch (_e) {
        return null;
      }
    }));
    const poles = new Set(BkkLib.polesFromPatterns(patterns, origin, destKey || ''));
    if (!destKey) {
      if (!poles.size && origin && BkkLib.isStopArea({ id: origin.id })) {
        (await BkkLib.childStopIds(apiKey, cache, origin.id)).forEach((id) => poles.add(id));
      }
      if (!poles.size && origin && origin.id) poles.add(origin.id);
    }
    return Array.from(poles);
  },
  async childStopIds(apiKey, cache, parentId) {
    const key = 'children:' + parentId;
    const hit = BkkLib._cached(cache, key);
    if (Array.isArray(hit)) return hit;
    if (BkkLib._inflight[key]) return BkkLib._inflight[key];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, 'schedule-for-stop.json', {
          stopId: parentId,
          includeReferences: 'true',
        }, true);
        const stops = ((data.data || {}).references || {}).stops || {};
        const ids = Object.values(stops)
          .filter((s) => s && s.id && s.parentStationId === parentId)
          .filter((s) => !/^STOP_/i.test(s.id) && !BkkLib.isMavStop(s) && !BkkLib.isVolanStop(s))
          .map((s) => s.id);
        return BkkLib._store(cache, key, ids);
      } finally {
        delete BkkLib._inflight[key];
      }
    })();
    BkkLib._inflight[key] = p;
    return p;
  },
  /* A dashboard left open for a day would otherwise accumulate every trip it
     ever looked at, each with its full stop list. Oldest entries go first. */
  cachePut(cache, key, value) {
    if (!cache) return value;
    const keys = Object.keys(cache);
    if (keys.length >= 400) {
      keys.slice(0, keys.length - 399).forEach((k) => { delete cache[k]; });
    }
    cache[key] = value;
    return value;
  },
  async tripDetails(apiKey, cache, tripId) {
    const key = 'trip:' + tripId;
    const hit = BkkLib._cached(cache, key);
    if (hit) return hit;
    if (BkkLib._inflight[key]) return BkkLib._inflight[key];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, 'trip-details.json', { tripId });
        const packed = {
          sts: (((data.data || {}).entry) || {}).stopTimes || [],
          stops: (((data.data || {}).references) || {}).stops || {},
          polyline: ((((data.data || {}).entry) || {}).polyline || {}).points || '',
          vehicle: (((data.data || {}).entry) || {}).vehicle || null,
          nowSec: BkkLib.unixSec(data.currentTime),
        };
        return BkkLib._store(cache, key, packed);
      } finally {
        delete BkkLib._inflight[key];
      }
    })();
    BkkLib._inflight[key] = p;
    return p;
  },
  tripReaches(details, origin, destKey) {
    const { sts, stops } = details;
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return false;
    return sts.slice(oidx + 1).some((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
  },
  async tripGoesTo(apiKey, cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    try {
      return BkkLib.tripReaches(await BkkLib.tripDetails(apiKey, cache, tripId), origin, destKey);
    } catch (_e) {
      return false;
    }
  },
  /* What tripGoesTo would answer from the cached trip details alone:
     true / false, or undefined when the trip was never loaded. */
  knownTripGoesTo(cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    const hit = BkkLib._cached(cache, 'trip:' + tripId);
    return hit ? BkkLib.tripReaches(hit, origin, destKey) : undefined;
  },
  /* The trip's own stops for the map: where the rider boards, and the first
     later stop named like the card's destination. Exact, and on the route. */
  async tripHopStops(apiKey, cache, tripId, origin, destKey) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return null;
    const at = (s) => {
      const stop = (s && stops[s.stopId]) || {};
      return BkkLib.mapGpsLatLon(stop.lat, stop.lon);
    };
    const hit = destKey
      ? sts.slice(oidx + 1).find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey))
      : null;
    return { board: at(sts[oidx]), alight: hit ? at(hit) : null };
  },
  /* The trip's stops with their (predicted, else scheduled) times, in
     running order, for the timetable position of a vehicle without GPS. */
  async tripTimedStops(apiKey, cache, tripId, origin) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = origin ? BkkLib.originIndex(sts, stops, origin) : -1;
    const out = [];
    (sts || []).forEach((s, i) => {
      const stop = (s && stops[s.stopId]) || {};
      const pt = BkkLib.mapGpsLatLon(stop.lat, stop.lon);
      /* One pair per stop: the predicted one when FUTÁR gives either half of
         it (a late arrival with a scheduled departure before it would put
         the vehicle at the next stop), else the scheduled one. */
      const pa = Number(s.predictedArrivalTime);
      const pd = Number(s.predictedDepartureTime);
      const predicted = (pa > 0) || (pd > 0);
      const arr = predicted ? (pa > 0 ? pa : pd) : Number(s.arrivalTime || s.departureTime);
      const dep = predicted ? (pd > 0 ? pd : pa) : Number(s.departureTime || s.arrivalTime);
      if (!pt || !Number.isFinite(arr) || arr <= 0) return;
      const entry = { pt, arr, dep: Number.isFinite(dep) && dep > 0 ? dep : arr };
      /* The card's boarding stop, marked: its row's departure is this poll's. */
      if (i === oidx) entry.origin = true;
      out.push(entry);
    });
    return out;
  },
  async tripRemainingNames(apiKey, cache, tripId, origin) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return [];
    const names = [];
    const seen = new Set();
    sts.slice(oidx + 1).forEach((s) => {
      const raw = (stops[s.stopId] || {}).name;
      const n = BkkLib.placeNameKey({ name: raw }) || BkkLib.stationKey(raw) || BkkLib.norm(raw);
      if (!raw || !n || seen.has(n)) return;
      seen.add(n);
      names.push({ key: n, name: raw, id: s.stopId });
    });
    return names;
  },
  async reachableDests(apiKey, cache, stop, mode, city) {
    if (mode === 'helyi') {
      return this.cityReachableDests(stop, city);
    }
    let out = { dests: [], destRoutes: {} };
    try {
      const data = await BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
        stopId: stop.id,
        minutesAfter: DEPART_MINUTES_AFTER,
        minutesBefore: '0',
        onlyDepartures: 'true',
        includeReferences: 'true',
      }, true);
      const refs = (data.data || {}).references || {};
      const routes = refs.routes || {};
      const trips = refs.trips || {};
      const times = ((data.data || {}).entry || {}).stopTimes || [];
      const seenTrip = new Set();
      const seenRoute = new Set();
      const tripJobs = [];
      const routeIds = [];
      for (let i = 0; i < times.length; i++) {
        const st = times[i];
        const trip = trips[st.tripId] || {};
        const rid = trip.routeId;
        const rt = routes[rid] || { id: rid, type: '' };
        if (!BkkLib.routeMatchesMode(rt, mode)) continue;
        if (rid && !seenRoute.has(rid)) {
          seenRoute.add(rid);
          routeIds.push(rid);
        }
        if (st.tripId && !seenTrip.has(st.tripId) && tripJobs.length < 40) {
          seenTrip.add(st.tripId);
          tripJobs.push({ tripId: st.tripId, routeId: rid });
        }
      }
      const fromTrips = await Promise.all(tripJobs.map(async (job) => {
        try {
          const names = await BkkLib.tripRemainingNames(apiKey, cache, job.tripId, stop);
          return { routeId: job.routeId, names };
        } catch (_e) {
          return { routeId: job.routeId, names: [] };
        }
      }));
      const fromRoutes = await Promise.all(routeIds.slice(0, 32).map(async (rid) => {
        try {
          const names = await BkkLib.remainingNames(apiKey, cache, stop, rid);
          return { routeId: rid, names };
        } catch (_e) {
          return { routeId: rid, names: [] };
        }
      }));
      const byKey = new Map();
      const destRoutes = {};
      fromTrips.concat(fromRoutes).forEach(({ routeId, names }) => {
        (names || []).forEach((n) => {
          if (!n || !n.key) return;
          const prev = byKey.get(n.key);
          if (!prev) byKey.set(n.key, n);
          else if (BkkLib.isMetroSuffixName(prev.name) && !BkkLib.isMetroSuffixName(n.name)) {
            byKey.set(n.key, n);
          }
          if (!destRoutes[n.key]) destRoutes[n.key] = [];
          if (routeId && destRoutes[n.key].indexOf(routeId) < 0) destRoutes[n.key].push(routeId);
        });
      });
      const dests = Array.from(byKey.values());
      dests.sort((a, b) => a.name.localeCompare(b.name, 'hu'));
      out = { dests, destRoutes };
    } catch (err) {
      if (mode !== 'volan' && mode !== 'all') throw err;
    }
    if (mode === 'volan' || mode === 'all') {
      try {
        out = BkkLib.mergeDestIndex(await this.volanReachableDests(stop), out);
      } catch (_e) { /* GTFS index optional */ }
    }
    if (mode === 'all') {
      try {
        const idx = await this.cityIndex();
        const cityId = BkkLib.cityOpId(idx, stop);
        if (cityId) out = BkkLib.mergeDestIndex(await this.cityReachableDests(stop, cityId), out);
      } catch (_e) { /* city index optional */ }
    }
    return out;
  },
  async travelMin(apiKey, tripId, destKey, dep, origin, cache) {
    if (!tripId || !destKey || /^(gtfs:|elvira:|motis:)/.test(String(tripId))) return null;
    try {
      const { sts, stops } = await BkkLib.tripDetails(apiKey, cache || {}, tripId);
      const oidx = BkkLib.originIndex(sts, stops, origin);
      const start = oidx >= 0 ? oidx + 1 : 0;
      const dest = sts.slice(start).find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
      if (!dest) return null;
      /* Trip details are cached for the session, so their predictions age
         while `dep` is always fresh: a bus that ran late since would lose
         that delay from its ride. Both ends come from the same snapshot
         instead, predicted with predicted and timetable with timetable. */
      const o = oidx >= 0 ? sts[oidx] : null;
      const oPred = o && (o.predictedDepartureTime || o.predictedArrivalTime);
      const oPlan = o && (o.departureTime || o.arrivalTime);
      let from = dep;
      let arr = dest.predictedArrivalTime || dest.arrivalTime || dest.departureTime;
      if (oPred && dest.predictedArrivalTime) {
        from = oPred;
        arr = dest.predictedArrivalTime;
      } else if (oPlan && (dest.arrivalTime || dest.departureTime)) {
        from = oPlan;
        arr = dest.arrivalTime || dest.departureTime;
      }
      if (!arr || !from) return null;
      const mins = Math.round((arr - from) / 60);
      return (mins >= 1 && mins <= 1440) ? mins : null;
    } catch (_e) {
      return null;
    }
  },
  async departures(apiKey, stopId, routeIds, dest, opts) {
    opts = opts || {};
    const mode = opts.mode || (opts.mav ? 'mav' : 'bkk');
    const horizon = clampMinutesAfter(opts.minutesAfter);
    if (mode === 'helyi') {
      let cityRows = await this.cityDepartures(stopId, dest, opts.originName, opts.city, horizon);
      cityRows = await BkkLib.fillTransitousIfEmpty(
        cityRows,
        /* With the stop's coordinates: by name alone "Kossuth tér" is any town's. */
        { id: stopId, name: opts.originName || '', lat: opts.originLat, lon: opts.originLon },
        dest,
        opts,
      );
      if (opts.deferGps) return cityRows;
      return BkkLib.attachCoachGps(opts.hass, cityRows);
    }
    const cache = opts.cache || {};
    const origin = { id: stopId, name: opts.originName || '' };
    const selected = new Set(routeIds || []);
    const destKey = dest && dest.key;
    const maxRows = maxDepartureRows(horizon);
    const now = Math.floor(Date.now() / 1000);
    const latest = now + horizon * 60;
    let rows = [];
    /* ELVIRA's trains and an empty board's Transitous fill are the card's
       own steps (hop-card _reload), not this one's. */
    const queryFutar = mode === 'bkk' || mode === 'mav' || /^BKK_/i.test(String(stopId || ''));
    const futarTask = queryFutar ? (async () => {
    let candidates = [];
    try {
      let poles = [];
      const poleRoutes = Array.from(selected);
      if (poleRoutes.length) {
        try {
          poles = await BkkLib.originPoles(apiKey, cache, origin, poleRoutes, destKey || '');
        } catch (_e) {
          poles = [];
        }
      }
      /* Saved routes are what ran when the card was set up (at night only
         the night bus): with a destination they only add a hub's far poles.
         The stop's own board is still read, and a capped one discovers the
         rest below, so a day line on another pole is not left out. Its own
         child poles are on that board already: read only when it is capped. */
      let near = [];
      if (!poles.length) {
        poles = [stopId];
      } else if (destKey) {
        const kids = BkkLib.childPolesOf(cache, poleRoutes, stopId);
        const saved = poles.filter((p) => p !== stopId);
        near = saved.filter((p) => kids.has(p));
        poles = [stopId].concat(saved.filter((p) => !kids.has(p)));
      }
      /* One pole failing still leaves the others' rows. Every board failing
         (a revoked key, an outage, the integration erroring) is not "no
         departures": the first error goes up so the card shows it. */
      let boardErr = null;
      const loadBoards = (ids) => Promise.all(ids.map(async (pid) => {
        try {
          return await BkkLib.departureBoard(apiKey, pid, horizon);
        } catch (err) {
          if (!boardErr) boardErr = err;
          return null;
        }
      }));
      let payloads = await loadBoards(poles);
      /* The parent's board stood in for its child poles: when it failed,
         their own boards are read instead of dropping their rows. */
      if (near.length && !payloads[0]) {
        poles = poles.concat(near);
        payloads = payloads.concat(await loadBoards(near));
        near = [];
      }
      if (boardErr && payloads.every((data) => !data)) throw boardErr;
      const usedParentOnly = poles[0] === stopId && (poles.length === 1 || !!destKey);
      const span = payloads.slice(0, usedParentOnly ? 1 : payloads.length).reduce((best, data) => {
        const cur = BkkLib.boardSpanMin(data, now);
        return {
          count: best.count + cur.count,
          spanMin: Math.max(best.spanMin, cur.spanMin),
        };
      }, { count: 0, spanMin: 0 });
      /* A failed parent board (its child poles stood in) has no span: it is
         not "capped", or every outage or rate limit set off discovery's
         extra route and board requests. */
      const capped = usedParentOnly && !!payloads[0]
        && (span.count >= 60 || span.spanMin + 2 < horizon);
      if (capped) {
        const discovered = [];
        payloads.forEach((data) => {
          const refs = ((data && data.data || {}).references) || {};
          const entry = ((data && data.data || {}).entry) || {};
          const trips = refs.trips || {};
          const routes = refs.routes || {};
          const ids = new Set(entry.routeIds || []);
          (entry.stopTimes || []).forEach((st) => {
            const rid = (trips[st.tripId] || {}).routeId;
            if (rid) ids.add(rid);
          });
          ids.forEach((rid) => {
            const rt = routes[rid] || {};
            if (!BkkLib.routeMatchesMode(rt, mode)) return;
            if (!BkkLib.keepFutarCandidate(rt, selected, mode, destKey)) return;
            if (discovered.indexOf(rid) < 0) discovered.push(rid);
          });
        });
        let extra = [];
        if (discovered.length) {
          try {
            extra = await BkkLib.originPoles(apiKey, cache, origin, discovered, destKey || '');
          } catch (_e) {
            extra = [];
          }
        }
        const fresh = Array.from(new Set(near.concat(extra))).filter((id) => poles.indexOf(id) < 0);
        if (fresh.length) {
          poles = poles.concat(fresh);
          payloads = payloads.concat(await loadBoards(fresh));
        }
      }
      const seenTrip = new Set();
      payloads.forEach((data) => {
        if (!data) return;
        const refs = (data.data || {}).references || {};
        const routes = refs.routes || {};
        const trips = refs.trips || {};
        const entry = (data.data || {}).entry || {};
        const times = entry.stopTimes || [];
        const stops = refs.stops || {};
        times.forEach((st) => {
          if (!st.tripId || seenTrip.has(st.tripId)) return;
          const trip = trips[st.tripId] || {};
          const rid = trip.routeId;
          const rt = routes[rid] || {};
          if (!BkkLib.routeMatchesMode(rt, mode)) return;
          if (!BkkLib.keepFutarCandidate(rt, selected, mode, destKey)) return;
          const dep = st.predictedDepartureTime || st.departureTime;
          if (dep && dep > latest + 60) return;
          if (dep && !departureStillDue(dep, now)) return;
          seenTrip.add(st.tripId);
          const fallbackColor = mode === 'volan' ? 'F9AB13' : '4477aa';
          const fallbackText = mode === 'volan' ? '000000' : 'ffffff';
          const veh = st.vehicle || {};
          const loc = BkkLib.vehicleLoc(veh);
          const vehicle = BkkLib.vehicleKind(rt);
          candidates.push({
            tripId: st.tripId,
            label: rt.iconDisplayText || rt.shortName || '?',
            color: '#' + String(rt.color || fallbackColor).replace('#', ''),
            text: '#' + String(rt.textColor || fallbackText).replace('#', ''),
            vehicle,
            rawType: String(rt.type || 'BUS').toUpperCase(),
            dep: dep,
            sched: st.departureTime,
            delay: (st.predictedDepartureTime && st.departureTime)
              ? (st.predictedDepartureTime - st.departureTime) : 0,
            head: trip.tripHeadsign || st.stopHeadsign || '',
            /* A single pole's board leaves stopId off its stop times (the
               entry names the pole), so its rows need the entry's stopId to
               show the same stand code as the parent station's board. */
            platform: (stops[st.stopId || entry.stopId] || {}).platformCode || '',
            wheelchair: trip.wheelchairAccessible === 1
              || trip.wheelchairAccessible === true
              || st.wheelchairAccessible === true,
            bikesAllowed: trip.bikesAllowed === 1 || trip.bikesAllowed === true,
            /* Only trains have a train number. A bus on a numeric ad-hoc
               trip id (BKK_1792439004037) must not show one. */
            trainNumber: vehicle === 'rail'
              ? (trip.shortName || trip.tripShortName
                || BkkLib.trainNumberFromTripId(st.tripId) || '')
              : '',
            booking: !!st.mayRequireBooking,
            lat: loc.lat,
            lon: loc.lon,
            vehicleId: veh.vehicleId || '',
            licensePlate: veh.licensePlate || '',
            model: veh.model || '',
            vehicleStatus: veh.status || '',
            stopDistancePercent: veh.stopDistancePercent,
          });
        });
      });
      candidates.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    } catch (err) {
      if (mode !== 'volan' && mode !== 'all') throw err;
    }
    let sure = [];
    let maybe = [];
    /* A matching headsign is the destination itself, so those rows skip the
       per-trip request. Through-routes still need trip-details, but that
       check must not hold the first timetable paint. */
    const headHitsDest = (row) => !!(dest && (
      BkkLib.nameEq(row.head, dest.name) || BkkLib.nameEq(row.head, dest.key)
    ));
    if (destKey && candidates.length) {
      /* Trips already checked on an earlier poll are answered from the
         cache, so a through trip stays in the first paint instead of
         dropping out until the slowest new trip's details arrive. */
      candidates.forEach((row) => {
        const known = headHitsDest(row) || BkkLib.knownTripGoesTo(cache, row.tripId, origin, destKey);
        if (known === true) sure.push(row);
        else if (known === undefined) maybe.push(row);
      });
      sure = sure.slice(0, maxRows);
    } else {
      sure = candidates.slice(0, maxRows);
    }
    const asFutar = (list) => list.map((row) => Object.assign({ travel: null }, row));
    const early = asFutar(sure);
    /* A full list of direct rows still leaves room for a through trip that
       leaves before the last of them: check those, then keep the earliest. */
    const sureFull = sure.length >= maxRows;
    const lastSure = sureFull ? (sure[sure.length - 1].dep || 0) : Infinity;
    const pool = maybe.filter((row) => (row.dep || 0) <= lastSure);
    if (destKey && pool.length && typeof opts.armThroughCheck === 'function') {
      opts.armThroughCheck(() => {
        const pending = (async () => {
          const picked = sure.slice();
          let found = 0;
          for (let i = 0; i < pool.length && (sureFull ? found < maxRows : picked.length < maxRows); i += 16) {
            const chunk = pool.slice(i, i + 16);
            const flags = await Promise.all(chunk.map((row) => (
              BkkLib.tripGoesTo(apiKey, cache, row.tripId, origin, destKey)
            )));
            flags.forEach((ok, j) => {
              if (!ok) return;
              picked.push(chunk[j]);
              found += 1;
            });
          }
          picked.sort((a, b) => (a.dep || 0) - (b.dep || 0));
          const full = asFutar(picked.slice(0, maxRows));
          if (typeof opts.onFutarUpdate === 'function') opts.onFutarUpdate(full);
        })();
        pending.catch(() => {
          if (typeof opts.onFutarUpdate === 'function') opts.onFutarUpdate(asFutar(sure));
        });
      });
    }
    return early;
    })() : Promise.resolve([]);
    const gtfsTask = (async () => {
      let extraVolan = [];
      let extraCity = [];
      if (mode === 'volan' || mode === 'all') {
        try {
          extraVolan = await this.volanDepartures(stopId, dest, opts.originName, horizon);
          if (mode === 'all' && selected.size) {
            extraVolan = extraVolan.filter((row) => selected.has('gtfs:' + row.label));
          }
        } catch (err) {
          if (mode !== 'all') throw err;
        }
      }
      if (mode === 'all') {
        try {
          const idx = await this.cityIndex();
          const cityId = BkkLib.cityOpId(idx, { id: stopId, name: opts.originName || '' });
          if (cityId) {
            extraCity = await this.cityDepartures(stopId, dest, opts.originName, cityId, horizon);
          }
        } catch (_e) { /* city index optional */ }
      }
      if ((extraVolan.length || extraCity.length) && typeof opts.onPartial === 'function') {
        let partial = BkkLib.mergeVolanRows(extraVolan, rows, maxRows);
        partial = BkkLib.mergeVolanRows(extraCity, partial, maxRows);
        opts.onPartial(partial);
      }
      return { extraVolan, extraCity };
    })();
    const [futar, gtfs] = await Promise.all([futarTask, gtfsTask]);
    rows = BkkLib.mergeVolanRows(futar, rows, maxRows);
    rows = BkkLib.mergeVolanRows(gtfs.extraVolan, rows, maxRows);
    rows = BkkLib.mergeVolanRows(gtfs.extraCity, rows, maxRows);
    if (opts.deferGps) return rows;
    return BkkLib.attachCoachGps(opts.hass, rows);
  },

});

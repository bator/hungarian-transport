import { clampMinutesAfter, pad2, tzFields } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  /* FUTÁR plan-trip: itinerary duration and walkTime are seconds.
     Each leg duration is milliseconds. */
  journeyFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    if (!its.length) return null;
    let best = its[0];
    its.forEach((it) => {
      if (Number(it.duration || 1e15) < Number(best.duration || 1e15)) best = it;
    });
    return BkkLib.futarJourney(best);
  },
  journeysFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    return its.map((it) => BkkLib.futarJourney(it)).filter(Boolean);
  },
  futarJourney(best) {
    if (!best) return null;
    const legs = [];
    const tripStart = BkkLib.epochMs(best.startTime);
    let prevEnd = tripStart;
    (best.legs || []).forEach((leg) => {
      const sec = Math.round(Number(leg.duration || 0) / 1000);
      const walk = String(leg.mode || '').toUpperCase() === 'WALK';
      const startMs = BkkLib.epochMs(leg.startTime);
      const endMs = BkkLib.epochMs(leg.endTime);
      let waitMin = 0;
      if (!walk && startMs && prevEnd) {
        waitMin = Math.round((startMs - prevEnd) / 60000);
        if (waitMin < 1) waitMin = 0;
      }
      if (endMs) prevEnd = endMs;
      else if (startMs) prevEnd = startMs;
      let minutes = sec <= 0 ? 0 : Math.max(1, Math.round(sec / 60));
      let meters = Math.max(0, Math.round(Number(leg.distance || 0)));
      if (!meters && walk) meters = minutes * 80;
      if (minutes <= 0) {
        if (!walk || meters < 25) return;
        minutes = 1;
      }
      const from = leg.from || {};
      const to = leg.to || {};
      const fromLat = Number(from.lat);
      const fromLon = Number(from.lon);
      const toLat = Number(to.lat);
      const toLon = Number(to.lon);
      legs.push({
        walk,
        startMs: startMs || undefined,
        endMs: endMs || undefined,
        minutes,
        meters,
        label: String(leg.routeShortName || ''),
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
      });
    });
    if (!legs.length) return null;
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    return {
      durationMin: Math.max(1, Math.round(Number(best.duration || 0) / 60)),
      walkMin: Math.max(0, Math.round(Number(best.walkTime || 0) / 60)),
      waitMin,
      transfers: Math.max(0, Number(best.transfers || 0)),
      startMs: BkkLib.epochMs(best.startTime) || undefined,
      endMs: BkkLib.epochMs(best.endTime) || undefined,
      legs,
    };
  },
  /* MOTIS and FUTÁR name coordinate endpoints START and END. */
  nameJourneyEnds(journey, origin, dest) {
    if (!journey || !Array.isArray(journey.legs)) return journey;
    const from = String((origin && origin.name) || '').trim();
    const to = String((dest && dest.name) || '').trim();
    journey.legs.forEach((leg) => {
      if (from && /^start$/i.test(String(leg.from || '').trim())) leg.from = from;
      if (to && /^end$/i.test(String(leg.to || '').trim())) leg.to = to;
    });
    return journey;
  },
  /* FUTÁR plan-trip parameters for a chosen moment (Budapest wall clock). */
  futarPlanTime(opts) {
    const ms = Number(opts && opts.time);
    if (!(ms > 0)) return {};
    const f = tzFields(new Date(ms));
    const out = {
      date: `${f.y}-${pad2(f.mo)}-${pad2(f.d)}`,
      time: `${pad2(f.h)}:${pad2(f.mi)}`,
    };
    if (opts.arriveBy) out.arriveBy = 'true';
    return out;
  },
  /* Keep only itineraries nobody beats: drop one when another leaves no
     earlier and arrives no later (a 22:39 → 06:52 night wait next to a
     01:56 → 04:33). Ties keep the one with fewer transfers. */
  dropDominatedJourneys(list) {
    const items = (Array.isArray(list) ? list : []).filter(Boolean);
    return items.filter((a) => {
      if (!a.startMs || !a.endMs) return true;
      return !items.some((b) => {
        if (b === a || !b.startMs || !b.endMs) return false;
        if (b.startMs < a.startMs || b.endMs > a.endMs) return false;
        if (b.startMs > a.startMs || b.endMs < a.endMs) return true;
        return (b.transfers || 0) < (a.transfers || 0);
      });
    });
  },
  /* Several itineraries for the planner's chosen time: Transitous first,
     FUTÁR plan-trip when it has none. */
  async planJourneys(apiKey, origin, dest, hass, opts) {
    opts = opts || {};
    let list = await BkkLib.transitousJourneys(origin, dest, opts);
    if (!list.length && BkkLib.hasFutar(apiKey)) {
      const fromPlace = await BkkLib.planPlace(apiKey, origin);
      const toPlace = await BkkLib.planPlace(apiKey, dest);
      if (fromPlace && toPlace) {
        try {
          const data = await BkkLib.fetch(apiKey, 'plan-trip.json', Object.assign({
            fromPlace,
            toPlace,
            mode: 'TRANSIT,WALK',
            numItineraries: '6',
          }, BkkLib.futarPlanTime(opts)), true);
          list = BkkLib.journeysFromPlan(data).filter((j) => j.legs.some((leg) => !leg.walk));
        } catch (_e) {
          list = [];
        }
      }
    }
    list.forEach((j) => BkkLib.nameJourneyEnds(j, origin, dest));
    list = BkkLib.dropDominatedJourneys(list);
    const at = Number(opts.time) || Date.now();
    const key = opts.arriveBy ? 'endMs' : 'startMs';
    list.sort((a, b) => (a[key] || at) - (b[key] || at));
    if (opts.arriveBy) {
      /* Latest arrivals before the requested time first. */
      list.reverse();
    }
    return list.slice(0, opts.max || 6);
  },
  async planJourney(apiKey, origin, dest, hass, opts) {
    opts = opts || {};
    const motis = await BkkLib.transitousJourney(origin, dest, opts);
    if (motis && motis.legs.some((leg) => !leg.walk)) return BkkLib.nameJourneyEnds(motis, origin, dest);
    let futar = null;
    if (BkkLib.hasFutar(apiKey)) {
      const fromPlace = await BkkLib.planPlace(apiKey, origin);
      const toPlace = await BkkLib.planPlace(apiKey, dest);
      if (fromPlace && toPlace) {
        try {
          const horizon = clampMinutesAfter(opts.minutesAfter);
          const data = await BkkLib.fetch(apiKey, 'plan-trip.json', {
            fromPlace,
            toPlace,
            mode: 'TRANSIT,WALK',
            numItineraries: String(Math.min(12, Math.max(5, Math.round(horizon / 40)))),
            searchWindow: String(horizon * 60),
          }, true);
          futar = BkkLib.journeyFromPlan(data);
        } catch (_e) {
          futar = null;
        }
      }
    }
    if (futar && futar.legs.some((leg) => !leg.walk)) return BkkLib.nameJourneyEnds(futar, origin, dest);
    const viaCity = await BkkLib.cityRailJourney(apiKey, hass, origin, dest);
    return viaCity || futar;
  },
  /* Local GTFS has no planner API. Ride the city feed to a station, walk
     across, then take the next train. Same-name stops are a 3 minute walk;
     the station bay to the platform is 6 minutes at 80 m/min. */
  cityHubPath(idx, originStop) {
    if (!idx || !originStop) return null;
    const stops = idx.stops || [];
    const op = originStop.op;
    const day = BkkLib.volanDay(0);
    const ride = new Map();
    const trips = idx.t || [];
    for (let n = 0; n < trips.length; n++) {
      const t = trips[n];
      const ps = t[2] || [];
      const ms = t[3] || [];
      if (ps.length < 2) continue;
      const first = stops[ps[0]];
      if (!first || first.op !== op) continue;
      if (!BkkLib.volanServiceOk(idx, t[0], day.ymd, day.dow)) continue;
      const span = Math.min(ps.length, 28);
      for (let i = 0; i < span; i++) {
        for (let j = i + 1; j < span; j++) {
          const mins = ms[j] - ms[i];
          if (mins < 1 || mins > 150) continue;
          const key = ps[i] + '|' + ps[j];
          const prev = ride.get(key);
          if (!prev || mins < prev.ride) {
            ride.set(key, { from: ps[i], to: ps[j], route: String(t[1]), ride: mins });
          }
        }
      }
    }
    const rideAt = new Map();
    ride.forEach((edge) => {
      if (!rideAt.has(edge.from)) rideAt.set(edge.from, []);
      rideAt.get(edge.from).push(edge);
    });
    const walkAt = new Map();
    const byFold = new Map();
    stops.forEach((st) => {
      if (!st || st.op !== op || !st.fold) return;
      if (!byFold.has(st.fold)) byFold.set(st.fold, []);
      byFold.get(st.fold).push(st.i);
    });
    byFold.forEach((group) => {
      if (group.length < 2 || group.length > 8) return;
      group.forEach((from) => {
        group.forEach((to) => {
          if (from === to) return;
          if (!walkAt.has(from)) walkAt.set(from, []);
          walkAt.get(from).push(to);
        });
      });
    });
    const hub = (st) => BkkLib.isRailHubName(st && st.name);
    const starts = [];
    stops.forEach((st) => {
      if (st && st.op === op && st.fold === originStop.fold) starts.push(st.i);
    });
    if (!starts.length) starts.push(originStop.i);
    const board = (at) => {
      const ids = [at];
      (walkAt.get(at) || []).forEach((to) => { if (ids.indexOf(to) < 0) ids.push(to); });
      return ids;
    };
    let bestHub = null;
    const consider = (state) => {
      const here = stops[state.at];
      if (hub(here) && state.cost > 0 && (!bestHub || state.cost < bestHub.cost)) bestHub = state;
    };
    starts.forEach((startAt) => {
      board(startAt).forEach((bay) => {
        const walked = bay === startAt ? 0 : 3;
        const base = {
          at: bay, rides: 0, cost: walked, prev: walked ? {
            at: startAt, prev: null, walk: true, minutes: 3,
            from: stops[startAt] && stops[startAt].name,
            to: stops[bay] && stops[bay].name,
          } : null,
        };
        (rideAt.get(bay) || []).forEach((edge) => {
          const first = {
            at: edge.to, rides: 1, cost: base.cost + edge.ride, prev: base.prev || base,
            walk: false, route: edge.route, minutes: edge.ride,
            fromI: edge.from, toI: edge.to,
            from: stops[bay] && stops[bay].name,
            to: stops[edge.to] && stops[edge.to].name,
          };
          consider(first);
          board(edge.to).forEach((mid) => {
            const midWalk = mid === edge.to ? 0 : 3;
            (rideAt.get(mid) || []).forEach((edge2) => {
              if (edge2.route === edge.route && edge2.to === edge.to) return;
              const secondPrev = {
                at: mid, rides: 1, cost: first.cost + midWalk, prev: first,
                walk: midWalk > 0, minutes: midWalk,
                from: stops[edge.to] && stops[edge.to].name,
                to: stops[mid] && stops[mid].name,
              };
              const second = {
                at: edge2.to, rides: 2, cost: first.cost + midWalk + edge2.ride,
                prev: midWalk ? secondPrev : first,
                walk: false, route: edge2.route, minutes: edge2.ride,
                fromI: edge2.from, toI: edge2.to,
                from: stops[mid] && stops[mid].name,
                to: stops[edge2.to] && stops[edge2.to].name,
              };
              consider(second);
            });
          });
        });
      });
    });
    if (!bestHub) return null;
    const edges = [];
    for (let step = bestHub; step && step.prev; step = step.prev) {
      edges.push({
        walk: !!step.walk,
        route: step.route || '',
        minutes: step.minutes,
        fromI: step.fromI,
        toI: step.toI,
        from: step.from || '',
        to: step.to || '',
      });
    }
    edges.reverse();
    return {
      minutes: bestHub.cost,
      hubName: stops[bestHub.at] && stops[bestHub.at].name,
      hubFold: stops[bestHub.at] && stops[bestHub.at].fold,
      edges,
    };
  },
  async cityRailJourney(apiKey, hass, origin, dest) {
    if (!BkkLib.isRailDest(dest)) return null;
    let idx;
    try { idx = await BkkLib.cityIndex(); } catch (_e) { return null; }
    if (!idx || !idx.stops) return null;
    let originStop = (idx.stops || []).find((st) => st && st.id === origin.id);
    if (!originStop) {
      originStop = BkkLib.volanMatchStop(idx, { id: origin.id, name: origin.name || '' }, null);
    }
    if (!originStop || originStop.op == null) return null;
    const path = BkkLib.cityHubPath(idx, originStop);
    if (!path || !path.edges.length) return null;
    const op = (idx.ops || [])[originStop.op];
    const cityName = op ? String(op.name).split(' — ')[0] : '';
    const destFold = BkkLib.fold((dest && dest.name) || '');
    const cityTok = new Set(BkkLib.foldTokens(cityName));
    const destTok = BkkLib.foldTokens(destFold);
    const hubTok = BkkLib.foldTokens(path.hubFold);
    const destIsHub = !!(destFold && path.hubFold && (
      destFold === path.hubFold
      || (BkkLib.foldTokenMatch(destFold, path.hubFold)
        && destTok.every((tok) => hubTok.indexOf(tok) >= 0 || cityTok.has(tok)))
    ));
    let ready = Math.floor(Date.now() / 1000);
    const legs = [];
    for (let i = 0; i < path.edges.length; i++) {
      const edge = path.edges[i];
      if (edge.walk) {
        const startMs = ready * 1000;
        ready += edge.minutes * 60;
        legs.push({
          walk: true, minutes: edge.minutes, meters: edge.minutes * 80,
          label: '', headsign: '', color: '', text: '',
          from: edge.from, to: edge.to, waitMin: 0,
          startMs, endMs: ready * 1000,
        });
        continue;
      }
      const timed = BkkLib.nextCityRide(idx, edge.fromI, edge.toI, edge.route, ready);
      if (!timed) return null;
      const waitMin = Math.round((timed.dep - ready) / 60);
      legs.push({
        walk: false,
        minutes: Math.max(1, Math.round((timed.arr - timed.dep) / 60)),
        meters: 0,
        label: edge.route,
        headsign: edge.to,
        color: '0F6FC6',
        text: 'FFFFFF',
        from: edge.from,
        to: edge.to,
        waitMin: waitMin >= 1 ? waitMin : 0,
        startMs: timed.dep * 1000,
        endMs: timed.arr * 1000,
      });
      ready = timed.arr;
    }
    if (!destIsHub) {
      const rail = await BkkLib.railAfterCity(apiKey, hass, cityName, path, dest, ready);
      if (!rail) return null;
      legs.push(rail.walk);
      legs.push(rail.train);
      ready = rail.ready;
    }
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    const walkMin = legs.reduce((sum, leg) => sum + (leg.walk ? leg.minutes : 0), 0);
    const start = BkkLib.finiteLatLon(origin && origin.lat, origin && origin.lon);
    const end = BkkLib.finiteLatLon(dest && dest.lat, dest && dest.lon);
    if (start && legs[0] && legs[0].fromLat == null) {
      legs[0].fromLat = start[0];
      legs[0].fromLon = start[1];
    }
    if (end && legs.length) {
      const last = legs[legs.length - 1];
      if (last.toLat == null) {
        last.toLat = end[0];
        last.toLon = end[1];
      }
    }
    return {
      durationMin: Math.max(1, Math.round((ready - Math.floor(Date.now() / 1000)) / 60)),
      walkMin,
      waitMin,
      transfers: Math.max(0, legs.filter((leg) => !leg.walk).length - 1),
      legs,
    };
  },
  nextCityRide(idx, fromI, toI, route, readySec) {
    let best = null;
    for (let off = 0; off < 2; off++) {
      const day = BkkLib.volanDay(off);
      const trips = idx.t || [];
      for (let n = 0; n < trips.length; n++) {
        const t = trips[n];
        if (String(t[1]) !== route) continue;
        if (!BkkLib.volanServiceOk(idx, t[0], day.ymd, day.dow)) continue;
        const ps = t[2] || [];
        const ms = t[3] || [];
        const o = ps.indexOf(fromI);
        const d = ps.indexOf(toI);
        if (o < 0 || d <= o) continue;
        const dep = day.base + ms[o] * 60;
        if (dep < readySec - 30) continue;
        const arr = day.base + ms[d] * 60;
        if (!best || dep < best.dep) best = { dep, arr };
      }
      if (best) break;
    }
    return best;
  },
  async railAfterCity(apiKey, hass, cityName, path, dest, readySec) {
    if (!path || !path.hubName) return null;
    const query = (cityName ? cityName + ' ' : '') + (path.hubName || '');
    let station = null;
    try {
      const hits = await BkkLib.searchStops(apiKey, query, 'mav');
      const want = path.hubFold || path.hubName || '';
      station = (hits || []).find((hit) => BkkLib.foldTokenMatch(want, hit.name || '')) || null;
    } catch (_e) {
      station = null;
    }
    if (!station || !station.id) return null;
    const walkStart = readySec;
    const walkEnd = readySec + 6 * 60;
    const walk = {
      walk: true, stationWalk: true, minutes: 6, meters: 480, label: '', headsign: '',
      color: '', text: '', from: path.hubName || '', to: station.name || '',
      waitMin: 0, startMs: walkStart * 1000, endMs: walkEnd * 1000,
    };
    let train = null;
    if (hass && dest && dest.id) {
      try {
        const rows = await BkkLib.elviraBetween(
          hass, station.id, dest.id, 480, dest.name || '', { apiKey, originName: station.name || '' },
        );
        const next = (rows || []).filter((row) => row.destHit !== false && Number(row.dep) >= walkEnd - 30)
          .sort((a, b) => a.dep - b.dep)[0];
        if (next && next.dep) {
          const rideMin = Number(next.travel) > 0 ? Number(next.travel) : 0;
          const arr = rideMin ? next.dep + rideMin * 60 : next.dep;
          const waitMin = Math.round((next.dep - walkEnd) / 60);
          const badge = BkkLib.applyRailBadge({
            label: String(next.label || next.trainNumber || ''),
            color: '4477AA',
          });
          train = {
            walk: false,
            minutes: rideMin || 1,
            meters: 0,
            label: String(next.label || next.trainNumber || ''),
            headsign: dest.name || '',
            color: String(badge.color || '4477AA').replace('#', ''),
            text: String(badge.text || 'FFFFFF').replace('#', ''),
            from: station.name || '',
            to: dest.name || '',
            waitMin: waitMin >= 1 ? waitMin : 0,
            startMs: next.dep * 1000,
            endMs: arr * 1000,
          };
        }
      } catch (_e) { /* no train → whole city→rail journey is null */ }
    }
    if (!train) return null;
    return { walk, train, ready: Math.round(train.endMs / 1000) };
  },
  async probeApiKey(apiKey) {
    if (!BkkLib.hasFutar(apiKey)) return { ok: false, code: 'errNoApiKey' };
    try {
      await BkkLib.fetch(apiKey, 'search.json', { query: '.' }, true);
      return { ok: true };
    } catch (err) {
      const msg = String(err && err.message ? err.message : err);
      if (/HTTP 401|HTTP 403/.test(msg)) return { ok: false, code: 'errApiKeyBad' };
      if (err && err.i18nKey === 'errNoApiKey') return { ok: false, code: 'errNoApiKey' };
      return { ok: false, code: 'errApiKeyProbe' };
    }
  },
});

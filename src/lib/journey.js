import { clampMinutesAfter, pad2, tzDow, tzFields } from '../config.js';
import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  /* FUTÁR plan-trip: itinerary duration and walkTime are seconds.
     Each leg duration is milliseconds. */
  journeyFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    if (!its.length) return null;
    return BkkLib.futarJourney(BkkLib.bestItinerary(its, (it) => Number(it.endTime)));
  },
  /* The itinerary that arrives first; among equal arrivals the shorter ride,
     then fewer transfers. The search window spans hours: the shortest
     duration alone picked a trip leaving almost three hours later. */
  bestItinerary(its, endMs) {
    const arrive = (it) => {
      const v = endMs(it);
      return Number.isFinite(v) && v > 0 ? v : Infinity;
    };
    const rides = (it) => (it.legs || []).filter((leg) => {
      const mode = String(leg.mode || '').toUpperCase();
      return mode && mode !== 'WALK';
    }).length;
    let best = its[0];
    its.forEach((it) => {
      const a = arrive(it);
      const b = arrive(best);
      if (a !== b) {
        if (a < b) best = it;
        return;
      }
      const da = Number(it.duration || 1e15);
      const db = Number(best.duration || 1e15);
      if (da !== db) {
        if (da < db) best = it;
        return;
      }
      if (rides(it) < rides(best)) best = it;
    });
    return best;
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
    [origin, dest] = await this.withGtfsCoords([origin, dest]);
    let list = await BkkLib.transitousJourneys(origin, dest, opts);
    if (!list.length && BkkLib.hasFutar(apiKey)) {
      const [fromPlace, toPlace] = await Promise.all([
        BkkLib.planPlace(apiKey, origin),
        BkkLib.planPlace(apiKey, dest),
      ]);
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
    [origin, dest] = await this.withGtfsCoords([origin, dest]);
    const motis = await BkkLib.transitousJourney(origin, dest, opts);
    if (motis && motis.legs.some((leg) => !leg.walk)) return BkkLib.nameJourneyEnds(motis, origin, dest);
    let futar = null;
    if (BkkLib.hasFutar(apiKey)) {
      const [fromPlace, toPlace] = await Promise.all([
        BkkLib.planPlace(apiKey, origin),
        BkkLib.planPlace(apiKey, dest),
      ]);
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
    const viaCity = await this.cityRailJourney(apiKey, hass, origin, dest);
    return viaCity || futar;
  },
  /* One operator's ride and walk edges for a service day: the fastest
     ride between any two stops of a trip (up to 28 stops in), with every
     route that serves the pair, and walks between same-named stops. Built
     once per day, not per question. */
  _cityHubGraphs: new WeakMap(),
  _cityHubGraph(idx, op, ymd) {
    let byIdx = BkkLib._cityHubGraphs.get(idx);
    if (!byIdx) {
      byIdx = new Map();
      BkkLib._cityHubGraphs.set(idx, byIdx);
    }
    const memoKey = op + '|' + ymd;
    const hit = byIdx.get(memoKey);
    if (hit) return hit;
    const stops = idx.stops || [];
    const dow = tzDow(ymd);
    const ride = new Map();
    const trips = idx.t || [];
    for (let n = 0; n < trips.length; n++) {
      const t = trips[n];
      const ps = t[2] || [];
      const ms = t[3] || [];
      if (ps.length < 2) continue;
      const first = stops[ps[0]];
      if (!first || first.op !== op) continue;
      if (!BkkLib.volanServiceOk(idx, t[0], ymd, dow)) continue;
      const span = Math.min(ps.length, 28);
      for (let i = 0; i < span; i++) {
        for (let j = i + 1; j < span; j++) {
          const mins = ms[j] - ms[i];
          if (mins < 1 || mins > 150) continue;
          const key = ps[i] + '|' + ps[j];
          const route = String(t[1]);
          const prev = ride.get(key);
          if (!prev) {
            ride.set(key, { from: ps[i], to: ps[j], route, routes: [route], ride: mins });
            continue;
          }
          /* The fastest line may run once a day: the others are kept, and
             the ride is timed on whichever comes first. */
          if (prev.routes.indexOf(route) < 0) prev.routes.push(route);
          if (mins < prev.ride) {
            prev.ride = mins;
            prev.route = route;
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
    const graph = { rideAt, walkAt };
    /* Yesterday's graph is not asked for again. */
    Array.from(byIdx.keys()).forEach((k) => { if (k.split('|')[1] !== ymd) byIdx.delete(k); });
    byIdx.set(memoKey, graph);
    return graph;
  },
  /* Local GTFS has no planner API. Ride the city feed to a station, walk
     across, then take the next train. Same-name stops are a 3 minute walk;
     the station bay to the platform is 6 minutes at 80 m/min. The paths
     come cheapest first by ride time alone; the timetable picks among them. */
  cityHubPaths(idx, originStop, limit = 24) {
    if (!idx || !originStop) return [];
    const stops = idx.stops || [];
    const op = originStop.op;
    const { rideAt, walkAt } = BkkLib._cityHubGraph(idx, op, BkkLib.volanDay(0).ymd);
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
    const hubs = [];
    const consider = (state) => {
      const here = stops[state.at];
      if (hub(here) && state.cost > 0) hubs.push(state);
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
            walk: false, route: edge.route, routes: edge.routes, minutes: edge.ride,
            fromI: edge.from, toI: edge.to,
            from: stops[bay] && stops[bay].name,
            to: stops[edge.to] && stops[edge.to].name,
          };
          consider(first);
          board(edge.to).forEach((mid) => {
            const midWalk = mid === edge.to ? 0 : 3;
            (rideAt.get(mid) || []).forEach((edge2) => {
              if (edge2.to === edge.to) return;
              const secondPrev = {
                at: mid, rides: 1, cost: first.cost + midWalk, prev: first,
                walk: midWalk > 0, minutes: midWalk, fromI: edge.to, toI: mid,
                from: stops[edge.to] && stops[edge.to].name,
                to: stops[mid] && stops[mid].name,
              };
              const second = {
                at: edge2.to, rides: 2, cost: first.cost + midWalk + edge2.ride,
                prev: midWalk ? secondPrev : first,
                walk: false, route: edge2.route, routes: edge2.routes, minutes: edge2.ride,
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
    /* Stable: of equal ride times the one found first (fewer rides) leads. */
    return hubs.sort((a, b) => a.cost - b.cost).slice(0, limit).map((state) => {
      const edges = [];
      for (let step = state; step && step.prev; step = step.prev) {
        edges.push({
          walk: !!step.walk,
          route: step.route || '',
          routes: step.walk ? [] : (step.routes || [step.route || '']).slice(),
          minutes: step.minutes,
          fromI: step.fromI,
          toI: step.toI,
          from: step.from || '',
          to: step.to || '',
        });
      }
      edges.reverse();
      const hubStop = stops[state.at];
      const hubPt = hubStop ? BkkLib.finiteLatLon(hubStop.lat, hubStop.lon) : null;
      return {
        minutes: state.cost,
        hubName: hubStop && hubStop.name,
        hubFold: hubStop && hubStop.fold,
        hubLat: hubPt ? hubPt[0] : undefined,
        hubLon: hubPt ? hubPt[1] : undefined,
        edges,
      };
    });
  },
  cityHubPath(idx, originStop) {
    return BkkLib.cityHubPaths(idx, originStop, 1)[0] || null;
  },
  /* Only from a city feed's own stop: a BKK or Volán origin matched by
     number or name lands on some other town's stop (BKK_F01020 "Kossuth
     Lajos utca" was a Miskolc one). */
  async cityRailJourney(apiKey, hass, origin, dest) {
    if (!BkkLib.isRailDest(dest) || BkkLib.stopIdFamily(origin) !== 'city') return null;
    let idx;
    try { idx = await this.cityIndex(); } catch (_e) { return null; }
    if (!idx || !idx.stops) return null;
    let originStop = BkkLib.gtfsStopById(idx, origin.id);
    if (!originStop) {
      /* A rebuilt index renumbered the stop: its name in that city. */
      const opIx = BkkLib.opIx(idx, String(origin.id).split(':')[0]);
      if (opIx >= 0) originStop = BkkLib.volanMatchStop(idx, { id: '', name: origin.name || '' }, opIx);
    }
    if (!originStop || originStop.op == null) return null;
    const paths = BkkLib.cityHubPaths(idx, originStop).filter((p) => p.edges.length);
    if (!paths.length) return null;
    const op = (idx.ops || [])[originStop.op];
    const cityName = op ? String(op.name).split(' — ')[0] : '';
    /* The destination is the station the city ride ends at: by place, or
       by name, when every word of it is the town's or the hub's (a hub
       called just "Vasútállomás" has no word of its own). */
    const destFold = BkkLib.fold((dest && dest.name) || '');
    const cityTok = new Set(BkkLib.foldTokens(cityName));
    const destTok = BkkLib.foldTokens(destFold);
    const destPt = BkkLib.finiteLatLon(dest && dest.lat, dest && dest.lon);
    const isDestHub = (path) => {
      const hubTok = BkkLib.foldTokens(path.hubFold);
      const hubPt = BkkLib.finiteLatLon(path.hubLat, path.hubLon);
      return !!(destFold && path.hubFold && (
        destFold === path.hubFold
        || (hubPt && destPt && BkkLib.haversineMeters(hubPt, destPt) <= 800)
        || (destTok.length && destTok.every((tok) => hubTok.indexOf(tok) >= 0 || cityTok.has(tok)))
      ));
    };
    /* A path to the destination's own station needs no train: only those
       count when there is one. */
    const toDest = paths.filter(isDestHub);
    const destIsHub = toDest.length > 0;
    const startSec = Math.floor(Date.now() / 1000);
    const rides = new Map();
    const nextRide = (edge, readySec) => {
      const key = edge.fromI + '|' + edge.toI + '|' + edge.routes.join(',') + '|' + readySec;
      if (!rides.has(key)) rides.set(key, BkkLib.nextCityRide(idx, edge.fromI, edge.toI, edge.routes, readySec));
      return rides.get(key);
    };
    const timeCityPath = (path) => {
      let ready = startSec;
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
            fromI: edge.fromI, toI: edge.toI,
          });
          continue;
        }
        const timed = nextRide(edge, ready);
        if (!timed) return null;
        const waitMin = Math.round((timed.dep - ready) / 60);
        /* The trip's own end, as on the bus: not the stop this ride gets off. */
        const tripStops = (timed.trip && timed.trip[2]) || [];
        const terminus = idx.stops[tripStops[tripStops.length - 1]];
        legs.push({
          walk: false,
          minutes: Math.max(1, Math.round((timed.arr - timed.dep) / 60)),
          meters: 0,
          label: timed.trip ? String(timed.trip[1]) : edge.route,
          headsign: (terminus && terminus.name) || edge.to,
          color: '0F6FC6',
          text: 'FFFFFF',
          from: edge.from,
          to: edge.to,
          waitMin: waitMin >= 1 ? waitMin : 0,
          startMs: timed.dep * 1000,
          endMs: timed.arr * 1000,
          /* The trip's stops and shapes.txt route, so the map draws the ride
             along its streets instead of a line between two names. */
          gtfs: timed.trip ? BkkLib.gtfsTripGeo(idx, timed.trip, timed.o, timed.d) : null,
          fromI: edge.fromI,
          toI: edge.toI,
        });
        ready = timed.arr;
      }
      return { path, legs, ready };
    };
    /* The earliest arrival at the station wins, waits included: the
       fastest line by ride time may not run again until tomorrow. A path's
       ride time is a floor for its arrival, so a path is not timed once it
       cannot beat the best one to its station. */
    const bestAt = new Map();
    const tried = destIsHub ? toDest : paths;
    let first = null;
    for (let i = 0; i < tried.length; i++) {
      const floor = startSec + tried[i].minutes * 60;
      /* The destination's own station: only the overall best counts. */
      if (destIsHub && first && floor >= first.ready) break;
      const key = tried[i].hubFold || tried[i].hubName || '';
      const held = bestAt.get(key);
      if (held && floor >= held.ready) continue;
      const timed = timeCityPath(tried[i]);
      if (!timed) continue;
      if (!held || timed.ready < held.ready) bestAt.set(key, timed);
      if (!first || timed.ready < first.ready) first = timed;
    }
    if (!first) return null;
    let best = first;
    if (!destIsHub) {
      /* The train decides: a station reached first may have none to the
         destination, or a later one. The three stations the city reaches
         first are asked, and the earliest arrival at the destination wins. */
      const hubs = Array.from(bestAt.values()).sort((a, b) => a.ready - b.ready).slice(0, 3);
      const rails = await Promise.all(hubs.map((city) => this.railAfterCity(apiKey, hass, cityName, city.path, dest, city.ready)
        .catch(() => null)));
      best = null;
      let railBest = null;
      rails.forEach((rail, n) => {
        if (rail && (!railBest || rail.ready < railBest.ready)) {
          railBest = rail;
          best = hubs[n];
        }
      });
      if (!best) return null;
      best.legs.push(railBest.walk);
      best.legs.push(railBest.train);
      best.ready = railBest.ready;
    }
    const { legs, ready } = best;
    /* The journey starts when it has to: walks before the first ride end
       as that bus leaves, and waiting for it is not part of the trip. */
    const firstRide = legs.findIndex((leg) => !leg.walk);
    if (firstRide >= 0) {
      let at = legs[firstRide].startMs;
      for (let i = firstRide - 1; i >= 0; i--) {
        legs[i].endMs = at;
        legs[i].startMs = at - legs[i].minutes * 60000;
        at = legs[i].startMs;
      }
      legs[firstRide].waitMin = 0;
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
    /* Every other city leg ends at a stop of the index: its coordinates
       place the leg (the origin and destination above win at the ends). */
    const stopAt = (i) => {
      const st = Number.isInteger(i) ? idx.stops[i] : null;
      return st ? BkkLib.finiteLatLon(st.lat, st.lon) : null;
    };
    legs.forEach((leg) => {
      const a = stopAt(leg.fromI);
      const b = stopAt(leg.toI);
      if (a && leg.fromLat == null) { leg.fromLat = a[0]; leg.fromLon = a[1]; }
      if (b && leg.toLat == null) { leg.toLat = b[0]; leg.toLon = b[1]; }
      delete leg.fromI;
      delete leg.toI;
    });
    const startMs = (legs[0] && legs[0].startMs) || startSec * 1000;
    const endMs = ready * 1000;
    return {
      durationMin: Math.max(1, Math.round((endMs - startMs) / 60000)),
      walkMin,
      waitMin,
      transfers: Math.max(0, legs.filter((leg) => !leg.walk).length - 1),
      startMs,
      endMs,
      legs,
    };
  },
  /* The ride that gets there first on any of `routes` (one route or a
     list): a slow line leaving now beats a fast one leaving at dawn. */
  /* An index's trips by route, so a ride is looked up among its own
     line's trips, not the whole feed's, for every path and day. */
  _cityTripsByRoute: new WeakMap(),
  cityTripsOf(idx, routes) {
    let byRoute = BkkLib._cityTripsByRoute.get(idx);
    if (!byRoute) {
      byRoute = new Map();
      (idx.t || []).forEach((t) => {
        const key = String(t[1]);
        if (!byRoute.has(key)) byRoute.set(key, []);
        byRoute.get(key).push(t);
      });
      BkkLib._cityTripsByRoute.set(idx, byRoute);
    }
    const out = [];
    new Set([].concat(routes).map(String)).forEach((route) => {
      (byRoute.get(route) || []).forEach((t) => out.push(t));
    });
    return out;
  },
  nextCityRide(idx, fromI, toI, routes, readySec) {
    const trips = BkkLib.cityTripsOf(idx, routes);
    let best = null;
    /* -1: after midnight, yesterday's 24:xx trips are tonight's rides. */
    for (let off = -1; off < 2; off++) {
      const day = BkkLib.volanDay(off);
      for (let n = 0; n < trips.length; n++) {
        const t = trips[n];
        if (!BkkLib.volanServiceOk(idx, t[0], day.ymd, day.dow)) continue;
        const ps = t[2] || [];
        const ms = t[3] || [];
        const o = ps.indexOf(fromI);
        const d = ps.indexOf(toI);
        if (o < 0 || d <= o) continue;
        const dep = day.base + ms[o] * 60;
        if (dep < readySec - 30) continue;
        const arr = day.base + ms[d] * 60;
        if (!best || arr < best.arr || (arr === best.arr && dep > best.dep)) best = { dep, arr, trip: t, o, d };
      }
      /* Yesterday's late trip can still lose to an earlier one today. */
      if (best && off >= 0) break;
    }
    return best;
  },
  /* The railway station at a city feed's hub, from FUTÁR's MÁV stops: the
     one by the hub stop, else by name. Hubs called just "Vasútállomás"
     (Székesfehérvár, Szombathely, Kaposvár …) or "Főpályaudvar" (Pécs)
     share no word with the station ("Székesfehérvár", "Pécs"), so the town
     names it then; a town with two stations goes by the hub's own word
     (Miskolc "Gömöri pályaudvar" is Miskolc-Gömöri, not Miskolc-Tiszai). */
  async railStationAtHub(apiKey, cityName, path) {
    const hubPt = BkkLib.finiteLatLon(path.hubLat, path.hubLon);
    const want = path.hubFold || path.hubName || '';
    const cityTok = BkkLib.foldTokens(cityName);
    const queries = [(cityName ? cityName + ' ' : '') + (path.hubName || '')];
    if (cityName && queries.indexOf(cityName) < 0) queries.push(cityName);
    for (let i = 0; i < queries.length; i++) {
      let hits = [];
      try {
        hits = (await this.searchStops(apiKey, queries[i], 'mav')) || [];
      } catch (_e) {
        hits = [];
      }
      const near = hubPt ? hits
        .map((hit) => ({ hit, pt: BkkLib.finiteLatLon(hit.lat, hit.lon) }))
        .filter((c) => c.pt && BkkLib.haversineMeters(hubPt, c.pt) <= 1500)
        .sort((x, y) => BkkLib.haversineMeters(hubPt, x.pt) - BkkLib.haversineMeters(hubPt, y.pt))[0] : null;
      if (near) return near.hit;
      const byHub = hits.find((hit) => BkkLib.foldTokenMatch(want, hit.name || ''));
      if (byHub) return byHub;
      const extra = (hit) => BkkLib.foldTokens(hit.name || '').filter((tok) => cityTok.indexOf(tok) < 0).length;
      const byTown = cityTok.length ? hits
        .filter((hit) => BkkLib.foldTokens(hit.name || '').some((tok) => cityTok.indexOf(tok) >= 0))
        .sort((x, y) => extra(x) - extra(y))[0] : null;
      if (byTown) return byTown;
    }
    return null;
  },
  async railAfterCity(apiKey, hass, cityName, path, dest, readySec) {
    if (!path || !path.hubName) return null;
    const station = await this.railStationAtHub(apiKey, cityName, path);
    if (!station || !station.id) return null;
    const walkStart = readySec;
    const walkEnd = readySec + 6 * 60;
    const walk = {
      walk: true, stationWalk: true, minutes: 6, meters: 480, label: '', headsign: '',
      color: '', text: '', from: path.hubName || '', to: station.name || '',
      waitMin: 0, startMs: walkStart * 1000, endMs: walkEnd * 1000,
    };
    /* The station's own place ends the walk and starts the train: without
       it the map has only one end of each and draws neither. */
    const stationPt = BkkLib.finiteLatLon(station.lat, station.lon);
    if (stationPt) {
      walk.toLat = stationPt[0];
      walk.toLon = stationPt[1];
    }
    let train = null;
    if (hass && dest && dest.id) {
      try {
        /* With the station code resolved here, ELVIRA is not handed the
           destination's name, and heads each train with its own terminus. */
        const destCode = await this.elviraResolveCode(apiKey, dest.id, dest.name || '');
        const rows = destCode ? await this.elviraBetween(
          hass, station.id, destCode, 480, '', { apiKey, originName: station.name || '' },
        ) : [];
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
            headsign: String(next.head || dest.name || ''),
            color: String(badge.color || '4477AA').replace('#', ''),
            text: String(badge.text || 'FFFFFF').replace('#', ''),
            from: station.name || '',
            to: dest.name || '',
            waitMin: waitMin >= 1 ? waitMin : 0,
            startMs: next.dep * 1000,
            endMs: arr * 1000,
          };
          if (stationPt) {
            train.fromLat = stationPt[0];
            train.fromLon = stationPt[1];
          }
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
      if ((err && err.code === 'futar_auth') || /HTTP 401|HTTP 403/.test(msg)) {
        return { ok: false, code: 'errApiKeyBad' };
      }
      if (err && err.i18nKey === 'errNoApiKey') return { ok: false, code: 'errNoApiKey' };
      return { ok: false, code: 'errApiKeyProbe' };
    }
  },
});

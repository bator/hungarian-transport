import { BkkLib } from '../lib/core.js';

/* GTFS-Realtime delays and vehicle positions from the integration's
   `hungarian_transport.realtime` service (realtime.py): Volán, the city
   networks with a public feed, and MÁV trains. The integration fetches each
   feed at most once a minute and only while a card asks; the card asks once
   per poll, and only for rows that have no live data of their own (FUTÁR,
   ELVIRA, the bkk_stop coach positions keep their place). */

/* What one card asks for at once; the service refuses more. */
const RT_MAX_TRIPS = 40;
const RT_MAX_STOPS = 120;
/* Stops before the boarding stop sent along: a stop without an update of
   its own takes the nearest earlier one's delay. */
const RT_STOPS_BEFORE = 20;
/* Cards on one dashboard asking about the same feed and trips within this
   long share one call; a card polls every 15–45 s. */
const RT_CACHE_MS = 25000;
/* A failed call (no integration answer at all) is not repeated for this long. */
const RT_RETRY_MS = 5 * 60000;
/* A delay the feed reported keeps its row on the board after its timetable
   time, and is shown from the first paint of the next poll on, until the
   feed has not confirmed it for this long. */
const RT_KNOWN_MS = 3 * 60000;
const RT_LATE_MAX_MIN = 60;

Object.assign(BkkLib, {
  hasRealtime(hass) {
    const domain = hass && hass.services && hass.services.hungarian_transport;
    return !!(domain && typeof domain === 'object' && domain.realtime);
  },
  _rtCache: new Map(),
  _rtInflight: new Map(),
  _rtDownUntil: 0,
  _rtWarned: false,
  /* Feeds the integration has no realtime data for: not asked again until
     the page reloads. */
  _rtNoFeed: new Set(),
  _rtKnown: new Map(),
  rtKnownDelay(tripId, nowMs) {
    const hit = BkkLib._rtKnown.get(String(tripId || ''));
    if (!hit) return 0;
    if ((nowMs == null ? Date.now() : nowMs) - hit.at > RT_KNOWN_MS) {
      BkkLib._rtKnown.delete(String(tripId));
      return 0;
    }
    return hit.delay;
  },
  /* How many minutes past their timetable time gtfsDepartures still looks
     at trips: the largest delay a feed reported lately. */
  rtLateWindowMin(nowMs) {
    const now = nowMs == null ? Date.now() : nowMs;
    let most = 0;
    BkkLib._rtKnown.forEach((hit, key) => {
      if (now - hit.at > RT_KNOWN_MS) {
        BkkLib._rtKnown.delete(key);
        return;
      }
      if (hit.delay > most) most = hit.delay;
    });
    return Math.min(RT_LATE_MAX_MIN, Math.ceil(most / 60));
  },
  _rtRemember(tripId, delay, nowMs) {
    const key = String(tripId || '');
    if (!key) return;
    BkkLib._rtKnown.set(key, { delay, at: nowMs == null ? Date.now() : nowMs });
    while (BkkLib._rtKnown.size > 500) BkkLib._rtKnown.delete(BkkLib._rtKnown.keys().next().value);
  },
  /* The trip a GTFS departure row is, as gtfsDepartures saw it in the index:
     the index's trip_id (trip element 6, when the index has it), its route
     and first time, and its stops up to the alighting stop, without the
     city index's operator prefix. */
  rtGtfsDescriptor(feed, idx, t, oidx, didx, ymd) {
    if (!feed || !t) return null;
    const ps = t[2] || [];
    const ms = t[3] || [];
    const hi = didx >= 0 && didx - oidx < RT_MAX_STOPS - RT_STOPS_BEFORE ? didx : oidx;
    const lo = Math.max(0, oidx - RT_STOPS_BEFORE);
    const pre = feed + ':';
    const stops = [];
    for (let i = lo; i <= hi && stops.length < RT_MAX_STOPS; i++) {
      const st = idx.stops[ps[i]];
      const id = String((st && st.id) || '');
      stops.push(id.indexOf(pre) === 0 ? id.slice(pre.length) : id);
    }
    const out = {
      feed,
      route: String(t[1] || ''),
      start: Number.isFinite(Number(ms[0])) ? Number(ms[0]) : null,
      date: String(ymd || ''),
      stops,
      from: oidx - lo,
    };
    if (typeof t[6] === 'string' && t[6]) out.trip = t[6];
    if (hi === didx && didx >= 0) out.to = didx - lo;
    return out;
  },
  /* The service request for one row, or null for a row with nothing to
     look up. Train rows (mav mode) are looked up by train number. */
  rtRequest(row, ctx) {
    if (!row || !row.tripId) return null;
    const rt = row.rt;
    if (rt && rt.feed) {
      const req = { key: String(row.tripId).slice(0, 96) };
      if (rt.trip) req.trip_id = String(rt.trip).slice(0, 128);
      if (rt.route) req.route = String(rt.route).slice(0, 32);
      if (Number.isInteger(rt.start) && rt.start >= 0 && rt.start <= 2880) req.start = rt.start;
      if (/^\d{8}$/.test(rt.date || '')) req.date = rt.date;
      const stops = (rt.stops || []).filter((s) => s && String(s).length <= 64).slice(0, RT_MAX_STOPS);
      if (stops.length === (rt.stops || []).length && stops.length) {
        req.stops = stops.map(String);
        if (Number.isInteger(rt.from) && rt.from < stops.length) req.from = rt.from;
        if (Number.isInteger(rt.to) && rt.to < stops.length) req.to = rt.to;
      }
      return { feed: rt.feed, req };
    }
    const mode = ctx && ctx.mode;
    if ((mode !== 'mav' && mode !== 'all') || !BkkLib.isTrainRow(row)) return null;
    const num = String(BkkLib.rowTrainNumber(row) || '').replace(/^0+/, '');
    if (!/^\d{1,6}$/.test(num)) return null;
    const req = { key: ('t:' + num + ':' + String(row.tripId)).slice(0, 96), train: num };
    const from = BkkLib.elviraStationCode(ctx.stopId);
    const to = BkkLib.elviraStationCode(ctx.destStopId);
    if (from) {
      req.stops = to && to !== from ? [from, to] : [from];
      req.from = 0;
      if (req.stops.length > 1) req.to = 1;
    }
    return { feed: 'mav', req };
  },
  rtTripsFromResult(res) {
    const payload = BkkLib.servicePayload(res);
    if (!payload || typeof payload !== 'object') return null;
    if (payload.error === 'rt_no_feed') return { noFeed: true, trips: {} };
    const trips = payload.trips;
    return { trips: trips && typeof trips === 'object' ? trips : {} };
  },
  /* One service call per feed and set of trips, shared by the cards that
     ask the same within RT_CACHE_MS. Null when the call failed. */
  async realtimeFetch(hass, feed, trips) {
    if (!BkkLib.hasRealtime(hass) || BkkLib._rtNoFeed.has(feed)) return null;
    if (Date.now() < BkkLib._rtDownUntil) return null;
    const sig = feed + '|' + trips.map((t) => t.key).join('|');
    const hit = BkkLib._rtCache.get(sig);
    if (hit && Date.now() - hit.at < RT_CACHE_MS) return hit.out;
    const running = BkkLib._rtInflight.get(sig);
    if (running) return running;
    const run = (async () => {
      try {
        const res = await BkkLib.callServiceResponse(hass, 'hungarian_transport', 'realtime', { feed, trips });
        BkkLib._rtWarned = false;
        const out = BkkLib.rtTripsFromResult(res);
        if (out && out.noFeed) BkkLib._rtNoFeed.add(feed);
        BkkLib._rtCache.set(sig, { at: Date.now(), out });
        while (BkkLib._rtCache.size > 32) BkkLib._rtCache.delete(BkkLib._rtCache.keys().next().value);
        return out;
      } catch (err) {
        BkkLib._rtDownUntil = Date.now() + RT_RETRY_MS;
        if (!BkkLib._rtWarned) {
          BkkLib._rtWarned = true;
          console.warn('Live delays and positions (hungarian_transport.realtime) unavailable, retrying in a few minutes:', err);
        }
        return null;
      } finally {
        BkkLib._rtInflight.delete(sig);
      }
    })();
    BkkLib._rtInflight.set(sig, run);
    return run;
  },
  /* FUTÁR's own prediction or vehicle, or an ELVIRA delay: the row's live
     data, which the feed does not replace. A delay the feed gave on an
     earlier poll (rtDelay) is its own to update. */
  _rtMayDelay(row) {
    if (row.rtDelay) return true;
    if (Number(row.delay || 0) !== 0) return false;
    return !(BkkLib.isFutarTripId(row.tripId) && BkkLib.mapGpsLatLon(row.lat, row.lon));
  },
  _rtMayPosition(row) {
    return !!(row.rtPos || row.gpsCarried || !BkkLib.mapGpsLatLon(row.lat, row.lon));
  },
  /* Copy one answer onto its row. True when something changed. */
  applyRealtimeRow(row, hit, nowSec) {
    if (!row || !hit || typeof hit !== 'object') return false;
    let changed = false;
    const now = nowSec == null ? Math.floor(Date.now() / 1000) : nowSec;
    const delay = Number(hit.delay);
    if (hit.delay != null && Number.isFinite(delay) && BkkLib._rtMayDelay(row)) {
      const sched = Number(row.sched || row.dep || 0);
      const at = Number(hit.time);
      if (sched > 0) {
        row.dep = hit.time != null && Number.isFinite(at) && at > 0 && !hit.approx ? at : sched + delay;
        row.delay = row.dep - sched;
      }
      row.rtDelay = true;
      row.rtApprox = !!hit.approx;
      BkkLib._rtRemember(row.tripId, row.delay, now * 1000);
      const destDelay = Number(hit.dest_delay);
      if (row.travel != null && hit.dest_delay != null && Number.isFinite(destDelay) && !hit.approx) {
        const travel = Math.round(Number(row.travel) + (destDelay - delay) / 60);
        if (travel > 0) row.travel = travel;
      }
      changed = true;
    }
    const v = hit.vehicle;
    const gps = v && BkkLib.mapGpsLatLon(v.lat, v.lon);
    if (gps && BkkLib._rtMayPosition(row)) {
      row.lat = gps[0];
      row.lon = gps[1];
      const ts = Number(v.timestamp);
      row.gpsAt = Number.isFinite(ts) && ts > 0 ? Math.min(ts, now) : now;
      delete row.gpsCarried;
      if (v.bearing != null && Number.isFinite(Number(v.bearing))) row.heading = Number(v.bearing);
      if (v.speed != null && Number.isFinite(Number(v.speed))) row.speed = Number(v.speed);
      row.rtPos = true;
      changed = true;
    }
    return changed;
  },
  /* Look up the rows' trips and copy delays and positions onto them.
     Resolves to true when a row changed (the caller repaints). */
  async attachRealtime(hass, rows, ctx) {
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length || !BkkLib.hasRealtime(hass)) return false;
    const byFeed = new Map();
    list.forEach((row) => {
      const ask = BkkLib.rtRequest(row, ctx || {});
      if (!ask || BkkLib._rtNoFeed.has(ask.feed)) return;
      let group = byFeed.get(ask.feed);
      if (!group) byFeed.set(ask.feed, group = { trips: [], rows: new Map() });
      if (group.trips.length >= RT_MAX_TRIPS || group.rows.has(ask.req.key)) return;
      group.trips.push(ask.req);
      group.rows.set(ask.req.key, row);
    });
    if (!byFeed.size) return false;
    let changed = false;
    await Promise.all(Array.from(byFeed.entries()).map(async ([feed, group]) => {
      const out = await BkkLib.realtimeFetch(hass, feed, group.trips);
      if (!out) return;
      const nowSec = Math.floor(Date.now() / 1000);
      group.rows.forEach((row, key) => {
        if (BkkLib.applyRealtimeRow(row, out.trips[key], nowSec)) changed = true;
      });
    }));
    return changed;
  },
});

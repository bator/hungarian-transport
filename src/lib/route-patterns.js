import { tzParts } from '../config.js';

/* Route patterns (GLOSSARY.md) the browser keeps: the page's memory, the
   localStorage copy between page loads, and which pattern may still prove
   a trip. A dashboard opened again does not ask route-details for every
   route of every card. Storage, clock, timers and the page's pagehide come
   in through deps, so a test runs it without a browser. */

/* A route pattern rules a trip out without its trip-details only while it
   is this young and from the same day: a timetable change, a weekend or
   construction variant is in the next one. Finding poles takes any age. */
export const PATTERN_PROOF_MS = 3 * 3600000;
export const ROUTE_PATTERNS_KEY = 'hungarian_transport_route_patterns';
/* Only the newest few are kept, and only what the cards read of a stop. */
export const ROUTE_PATTERNS_KEEP = 120;
/* The patterns fetched meanwhile go back in one write, not one read and
   write of the whole list per route. */
export const ROUTE_PATTERNS_WRITE_MS = 2000;

/* deps: storage() the Storage (or null; one that throws is a private
   window or storage turned off), now() the clock in ms, setTimer(fn, ms)
   and clearTimer(t), and events, whose pagehide flushes a pending write. */
export function routePatternStore(deps) {
  const d = Object.assign({
    storage: () => globalThis.localStorage,
    now: () => Date.now(),
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (t) => clearTimeout(t),
    events: globalThis,
  }, deps);
  let memo = null;
  let timer = null;
  const readDisk = () => {
    const all = JSON.parse(d.storage().getItem(ROUTE_PATTERNS_KEY) || '{}');
    return all && typeof all === 'object' ? all : {};
  };
  /* Read once per page. */
  const stored = () => {
    if (memo) return memo;
    try {
      memo = readDisk();
    } catch (_e) {
      memo = {};
    }
    return memo;
  };
  const fresh = (pat, nowMs) => {
    if (!pat || !pat.variants || typeof pat.at !== 'number') return false;
    const now = nowMs === undefined ? d.now() : nowMs;
    /* "The same day" is Budapest's, as everywhere the timetable is read,
       not the browser's own time zone. */
    return now - pat.at < PATTERN_PROOF_MS
      && tzParts(new Date(pat.at)).ymd === tzParts(new Date(now)).ymd;
  };
  const write = () => {
    timer = null;
    try {
      /* Another tab may have written since this page read the list: its
         patterns stay, the newer copy of a route wins. */
      const all = stored();
      let disk = {};
      try {
        disk = readDisk();
      } catch (_e) { /* unreadable: this page's list alone */ }
      Object.keys(disk).forEach((k) => {
        if (disk[k] && (!all[k] || disk[k].at > all[k].at)) all[k] = disk[k];
      });
      const out = {};
      Object.keys(all).filter((k) => fresh(all[k]))
        .sort((a, b) => all[b].at - all[a].at)
        .slice(0, ROUTE_PATTERNS_KEEP)
        .forEach((k) => { out[k] = all[k]; });
      memo = out;
      d.storage().setItem(ROUTE_PATTERNS_KEY, JSON.stringify(out));
    } catch (_e) { /* private window, storage off or full: the page's memo still holds them */ }
  };
  const store = {
    /* Whether a pattern may still prove a trip (nowMs: the clock's now). */
    fresh,
    /* An earlier page load's (or this page's) copy of a route's pattern
       while it may still prove a trip, else null. */
    kept(routeId) {
      const pat = stored()[routeId];
      return fresh(pat) && Array.isArray(pat.variants) && pat.stops && typeof pat.stops === 'object'
        ? pat : null;
    },
    /* A pattern route-details just gave ({variants, stops}), as of now:
       kept with only the stops' id, name and parent station. Returns it
       whole, with its time (at). */
    put(routeId, pattern) {
      const at = d.now();
      const stops = {};
      Object.keys(pattern.stops || {}).forEach((id) => {
        const st = pattern.stops[id] || {};
        stops[id] = { id: st.id || id, name: st.name || '', parentStationId: st.parentStationId || '' };
      });
      stored()[routeId] = { variants: pattern.variants, stops, at };
      if (!timer) timer = d.setTimer(write, ROUTE_PATTERNS_WRITE_MS);
      return { variants: pattern.variants, stops: pattern.stops, at };
    },
    /* The pending write now (a page about to go). */
    flush() {
      if (!timer) return;
      d.clearTimer(timer);
      write();
    },
  };
  /* A tab closed within the write delay still keeps what it fetched. */
  if (d.events && typeof d.events.addEventListener === 'function') {
    d.events.addEventListener('pagehide', () => store.flush());
  }
  return store;
}

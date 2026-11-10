import { BkkLib, register } from '../lib/core.js';
import { generation } from './generation.js';

/* After this long the "now" plan is stale: the planner plans again on its
   countdown tick, or when it is shown again. */
export const NOW_PLAN_STALE_MS = 5 * 60 * 1000;

/* A chosen time's search window, in minutes. */
const TIMED_WINDOW_MIN = 120;

const NOW = { mode: 'now', at: 0 };

/* The journey plan (GLOSSARY.md) of the planner's stop pair: the plan for
   now, made again on every reload of the board, or a chosen time's
   itineraries. Which answer is current lives here: a reload or a new pair
   makes the running one stale, and the "now" plan on screen stays there
   while the same pair plans again, until the new one lands. The planning
   comes in through an adapter (journeyPlanSources below), so a test runs
   it on a fake planner and clock, without a card.
   deps: planner {now(pair) -> journey or null, at(pair, when) -> [journey],
   shapes(journey): the route shapes filled in place}, now() the clock in
   ms, and changed(part) once something shows differently: 'card' for the
   whole card, 'journey' for the journey box only.
   A pair is {origin, dest, horizon}: two stops {id, name, lat, lon} and the
   look-ahead in minutes. A when is {mode: now | depart | arrive, at: ms}. */
export function journeyPlan(deps) {
  /* Answers of the running plan: every reload and every new pair. */
  const runs = generation();
  /* A new stop pair only: a journey map opening for the old one stops. */
  const pairs = generation();
  let when = NOW;
  /* This run's plan; for a chosen time, the open itinerary. */
  let own = null;
  /* The "now" plan of the last run, shown until this run's lands. */
  let held = null;
  let list = [];
  let open = 0;
  /* What the running or landed plan is for (pair, look-ahead, when). */
  let key = '';
  let loading = false;
  let done = false;
  let failed = false;
  let loadedAt = 0;
  const timed = () => when.mode !== 'now' && when.at > 0;
  const shown = () => own || (timed() ? null : held);

  const planAt = (pair, token) => {
    list = [];
    own = null;
    open = 0;
    deps.changed('journey');
    deps.planner.at(pair, when).then((found) => {
      if (runs.stale(token)) return;
      loading = false;
      done = true;
      list = found || [];
      failed = false;
      open = 0;
      own = list[0] || null;
      deps.changed('card');
    }).catch(() => {
      if (runs.stale(token)) return;
      /* A search that failed (Transitous or FUTÁR down) is not "no route
         for this time": the view says to try again. Nor is it done: the
         next reload asks again instead of keeping the failure. */
      loading = false;
      done = false;
      key = '';
      failed = true;
      list = [];
      deps.changed('card');
    });
  };

  const planNow = (pair, token) => {
    deps.planner.now(pair).then(async (journey) => {
      if (runs.stale(token)) return;
      loading = false;
      held = null;
      own = journey;
      deps.changed('card');
      if (!journey) return;
      try {
        await deps.planner.shapes(journey);
      } catch (_e) { /* draw the endpoint fallback */ }
      if (runs.stale(token)) return;
      own = journey;
      deps.changed('journey');
    }).catch(() => {
      if (runs.stale(token)) return;
      loading = false;
      /* The held plan is the one this replaced: a stale one is no answer. */
      held = null;
      /* The planner does not poll: a board left on "Loading…" would stay. */
      deps.changed('card');
    });
  };

  return {
    get when() { return when; },
    /* A chosen time, not now. */
    get timed() { return timed(); },
    /* The journey on screen: for now this run's plan or the last one's,
       for a chosen time the open itinerary (null: all folded). */
    get shown() { return shown(); },
    /* A chosen time's itineraries. */
    get list() { return list; },
    get openIndex() { return open; },
    get loading() { return loading; },
    /* The last chosen-time search failed (try again, not "no route"). */
    get failed() { return failed; },
    /* The "now" plan is old enough to plan again. */
    get stale() {
      return !timed() && loadedAt > 0 && deps.now() - loadedAt > NOW_PLAN_STALE_MS;
    },

    /* A new stop pair (picked, swapped, reset, or from the editor): what was
       planned, and every answer or map still coming, is the old pair's. */
    reset() {
      runs.bump();
      pairs.bump();
      own = null;
      held = null;
      list = [];
      open = 0;
      key = '';
      loading = false;
      done = false;
    },

    /* Most, Indulás, Érkezés or another time. Most once more plans now
       again with the plan on screen kept until the new one lands; a chosen
       time has other itineraries. */
    setWhen(next) {
      const keep = !timed() && next.mode === 'now' ? shown() : null;
      when = next;
      key = '';
      own = null;
      held = keep;
      list = [];
      done = false;
      loading = false;
    },

    /* A reload starts: an answer still coming is for the last run. For now
       the plan on screen is held over until this run's lands. */
    begin() {
      loadedAt = deps.now();
      runs.bump();
      key = '';
      if (timed()) {
        held = null;
        return;
      }
      if (own) held = own;
      own = null;
      loading = false;
    },

    /* Plan for the pair, unless that plan is running or already here. */
    consider(pair) {
      const isTimed = timed();
      const at = isTimed ? `${when.mode}@${when.at}` : 'now';
      const next = [pair.origin.id, pair.dest.id || '', pair.dest.name, pair.horizon, at].join('|');
      if (!pair.origin.id || !pair.dest.name) return;
      if (key === next && (loading || own || (isTimed && done))) return;
      key = next;
      loading = true;
      done = false;
      const token = runs.token();
      if (isTimed) planAt(pair, token);
      else planNow(pair, token);
    },

    /* A chosen time's itinerary i opens (-1: all folded). */
    openItinerary(i) {
      open = i;
      own = list[i] || null;
    },

    /* A plan for now made outside the run (the journey map's own) is the
       one on screen, held over too while a reload plans again. */
    adopt(journey) {
      own = journey;
      if (held) held = journey;
    },

    /* The stop pair a task started on; samePair(token) is false once a new
       pair came (reset). */
    pairToken() { return pairs.token(); },
    samePair(token) { return !pairs.stale(token); },

    /* Back on the view: plan again when nothing is on screen or "now" is
       stale. A chosen time does not go stale, nor do its itineraries when
       all are folded or none was found. */
    wantsReload(boardHasRows) {
      const planned = timed() && (list.length || done);
      const idle = !(planned || shown() || loading || boardHasRows);
      return idle || this.stale;
    },
  };
}

register({
  journeyPlan,
  /* The production planner of a card's journey plan: Transitous, FUTÁR
     and the timetables (journey.js) with the card's index URLs, FUTÁR key
     and Home Assistant, each read when a plan starts. */
  journeyPlanSources(card) {
    const stops = (pair) => [pair.origin, pair.dest];
    return {
      now: (pair) => BkkLib.withIndexes(card.cfg()).planJourney(
        card.apiKey(), ...stops(pair), card.hass(), { minutesAfter: pair.horizon }),
      at: (pair, when) => BkkLib.withIndexes(card.cfg()).planJourneys(
        card.apiKey(), ...stops(pair), card.hass(), {
          time: when.at,
          arriveBy: when.mode === 'arrive',
          minutesAfter: TIMED_WINDOW_MIN,
        }),
      shapes: (journey) => BkkLib.fillJourneyShapes(journey),
    };
  },
});

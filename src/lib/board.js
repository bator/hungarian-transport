import { DEFAULT_MINUTES_AFTER, clampMinutesAfter, departureStillDue, maxDepartureRows } from '../config.js';
import { codedError } from '../i18n.js';
import { BkkLib } from '../lib/core.js';

/* The departure board (GLOSSARY.md): which sources a stop pair's board
   reads, how their rows merge, what a failed source leaves on screen, and
   the enrichment (coach GPS, realtime, ride time) every poll adds. The
   sources come in through adapters (boardSources below), so a test runs a
   poll on fakes and a fixed clock, without a card or Home Assistant. */

/* A failed call (a websocket that dropped, HA restarting) is asked again
   on the next poll; only calls that keep failing leave ELVIRA alone this
   long. The same wait keeps a MÁV card on FUTÁR while the integration
   answers not_loaded. */
const ELVIRA_RETRY_MS = 3 * 60000;
const ELVIRA_FAIL_LIMIT = 3;
/* The limit counts failed poll rounds, not calls: every MÁV card (and the
   planner) fails in the same moment when the websocket drops, and that is
   one round. A card polls every 20 s at the most, so failures closer than
   this to the last one counted are the same round. */
const ELVIRA_FAIL_ROUND_MS = 15000;
/* The service schema's own limit on dest_name. */
const ELVIRA_DEST_NAME_MAX = 100;

/* ELVIRA through the integration's elvira_between, as {rows, error,
   detail}. error is '' for a full answer, else what went wrong: the
   integration's code (elvira_unavailable, elvira_rate_limited), a
   destination board that did not arrive (dest_error: the rows are there,
   their destHit is not sure), a call that failed or timed out, not_loaded,
   no_code for a stop with no station code, or code_search_failed for one
   whose FUTÁR station search failed this time (detail is its name).
   The failure streak is the source's own and shared by everyone who asks
   (every MÁV card and the planner): calls that keep failing pause ELVIRA
   for all of them, and an outage is logged once, not on every poll.
   deps: call(hass, serviceData) is the elvira_between call, now() the
   clock in ms, warn(...) the log. lib is whose station search resolves a
   stop without a code (the card's own index URLs). */
export function elviraSource(deps) {
  const st = {
    fails: 0, lastFailAt: 0, downUntil: 0, warned: false, lastCode: '', lastErr: '',
    /* Station pairs whose outage was logged; their next good answer ends it. */
    outages: new Set(),
  };
  const none = (error, detail) => ({ rows: [], error: error || '', detail: detail || '' });
  const down = (pair, reason) => {
    if (st.outages.has(pair)) return;
    st.outages.add(pair);
    deps.warn('MÁV ELVIRA reservation data unavailable:', reason);
  };
  return {
    async board(lib, hass, originId, destId, minutesAfter, destName, opts) {
      opts = opts || {};
      if (!BkkLib.hasElvira(hass)) return none();
      if (deps.now() < st.downUntil) return none(st.lastCode, st.lastErr);
      let origin = BkkLib.elviraStationCode(originId);
      let dest = BkkLib.elviraStationCode(destId);
      if (!origin) origin = await lib.elviraResolveCode(opts.apiKey, originId, opts.originName || '');
      if (!dest) dest = await lib.elviraResolveCode(opts.apiKey, destId, destName || '');
      /* A station search that failed (null: FUTÁR timed out or rate-limited)
         is no "no station code": the card is set up right, and the next
         poll searches again. */
      if (origin === null || dest === null) {
        return none('code_search_failed', origin === null ? (opts.originName || originId) : (destName || destId));
      }
      if (!origin || !dest) {
        return none('no_code', !origin ? (opts.originName || originId) : (destName || destId));
      }
      if (origin === dest) return none();
      const serviceData = {
        origin_code: origin,
        dest_code: dest,
        minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
        /* The service rejects a longer one as an invalid call. */
        dest_name: String(destName || '').slice(0, ELVIRA_DEST_NAME_MAX),
      };
      /* A planned time starts the window there (the planner's timed search);
         one already past is the service's own "from now". */
      const at = Math.floor(Number(opts.at));
      if (Number.isFinite(at) && at > Math.floor(deps.now() / 1000)) serviceData.at = at;
      const pair = `${origin}>${dest}`;
      let res;
      try {
        res = await deps.call(hass, serviceData);
      } catch (err) {
        const detail = BkkLib.elviraErrText(err);
        /* The entry unloaded (disabled, reloading): its services stay listed. */
        let code = 'elvira_call_failed';
        if (err && err.translation_key === 'not_loaded') code = 'not_loaded';
        else if (err && err.code === 'elvira_timeout') code = 'elvira_timeout';
        /* A call that timed out went through a websocket that is up, to an
           integration still at work on it: that one is no strike towards
           pausing ELVIRA for every card. A dropped websocket rejects instead. */
        if (code !== 'elvira_timeout') {
          const now = deps.now();
          if (now - st.lastFailAt >= ELVIRA_FAIL_ROUND_MS) {
            st.fails += 1;
            st.lastFailAt = now;
          }
          st.lastCode = code;
          st.lastErr = detail;
          if (st.fails >= ELVIRA_FAIL_LIMIT) st.downUntil = now + ELVIRA_RETRY_MS;
        }
        if (!st.warned) {
          st.warned = true;
          deps.warn('MÁV ELVIRA reservation data unavailable, retrying:', err);
        }
        return none(code, detail);
      }
      st.fails = 0;
      st.lastFailAt = 0;
      st.warned = false;
      if (!res) return none();
      /* ELVIRA itself being down is {departures: [], error, detail}, not a
         service error: the integration answered, so only this pair's
         outage is noted and no pause applies to the other pairs. */
      const payload = BkkLib.servicePayload(res);
      /* The integration's own request limit, not MÁV being down: no rows
         this poll, no outage warning, and the pair's outage state stays. */
      if (payload && payload.error === 'elvira_rate_limited') return none(payload.error, payload.detail);
      if (payload && payload.error) {
        down(pair, `${payload.error}${payload.detail ? ` (${payload.detail})` : ''}`);
        return none(String(payload.error), payload.detail);
      }
      st.outages.delete(pair);
      const rows = BkkLib.elviraRowsFromResult(res);
      const list = Array.isArray(rows) ? rows : [];
      if (payload && payload.dest_error) {
        return { rows: list, error: String(payload.dest_error), detail: `${destName || dest}: ${payload.dest_error}` };
      }
      return { rows: list, error: '', detail: '' };
    },
  };
}

/* The one ELVIRA source of the page. Looked up at call time, so a test
   that replaces callServiceResponse or console.warn reaches it too. */
const sharedElvira = elviraSource({
  call: (hass, serviceData) => BkkLib._elviraCall(hass, serviceData),
  now: () => Date.now(),
  warn: (...args) => console.warn(...args),
});

/* Why a MÁV board is not ELVIRA's whole answer: the banner. Only a stop
   FUTÁR has no station for asks for the editor; a station search that
   failed is FUTÁR's outage, for one poll. */
function elviraBanner(answer) {
  if (answer.error === 'no_code') return codedError('errElviraNoCode', answer.detail);
  if (answer.error === 'code_search_failed') return codedError('errFutarDown', answer.detail);
  return codedError('errElviraDown', answer.detail || answer.error);
}

/* One poll of a stop pair's departure board. The board on screen is the
   caller's; this reads it (poll.board()) and sends every change as a whole
   snapshot to poll.show(), in order:
     place     the origin is a place, not a stop: no rows, the journey only
     partial   the GTFS rows, before FUTÁR answers
     listed    the timetables answered (not painted: the journey starts over)
     board     the merged rows (FUTÁR, GTFS, ELVIRA), or a MÁV board
     waiting   nothing to list until the through-check answers (the spinner)
     filled    the Transitous fill of an empty board
     settled   the rows are this poll's (not painted: the journey may plan)
     gps, realtime   coach positions and live delays on the first rows
     done      the last snapshot: every row with its GPS and ride time
     failing, failed   the poll threw: the banner, and a fill of an empty board
   A snapshot is {phase, rows, banner, loading, filled, quiet}: quiet ones
   are not painted. Once poll.current() is false (a newer reload) nothing
   more is shown or fetched.
   poll: cfg (the card's config), mode, hass() and apiKey() (read when
   used: both change during a poll), tripCache, state (the card's memory
   across polls: {viaFutarUntil}), board(), show(snapshot), current(), and
   sources (boardSources(cfg) when left out). */
async function runBoard(poll, src, knownElvira) {
  const live = poll.current;
  if (!live()) return;
  const cfg = poll.cfg;
  const mode = poll.mode;
  const hass = () => poll.hass();
  const apiKey = () => poll.apiKey();
  const shown = () => poll.board();
  const hasFutar = () => BkkLib.hasFutar(apiKey(), hass());
  const nowSec = () => Math.floor(src.now() / 1000);
  const show = (phase, changes) => {
    const cur = shown();
    poll.show(Object.assign({
      phase, rows: cur.rows, banner: cur.banner, loading: cur.loading, filled: cur.filled, quiet: false,
    }, changes));
  };
  const dest = {
    key: cfg.destKey,
    name: cfg.destName,
    id: cfg.destStopId || '',
    lat: cfg.destLat,
    lon: cfg.destLon,
  };
  const origin = {
    id: cfg.stopId,
    name: cfg.stopName || '',
    lat: cfg.stopLat,
    lon: cfg.stopLon,
  };
  /* Every poll builds new row objects: a trip's ride time and coach GPS
     from the last list stay on it until this run fills them again, so
     they do not blink out while ELVIRA or the GPS lookup answers. The
     coach position is marked (gpsCarried) and keeps its fix time (gpsAt):
     the lookup replaces or drops it, a failing lookup drops it once the
     fix is two minutes old, and a sensor or EMMA position
     (enrichFromHass) replaces it and clears the mark. FUTÁR rows bring
     their own vehicle position on every poll. */
  const carryOver = (list) => {
    const prev = new Map();
    (shown().rows || []).forEach((row) => {
      if (row && row.tripId) prev.set(String(row.tripId), row);
    });
    if (!prev.size) return;
    list.forEach((row) => {
      const old = row && row.tripId ? prev.get(String(row.tripId)) : null;
      if (!old || old === row) return;
      if (row.travel == null && old.travel != null) {
        row.travel = old.travelSched != null ? old.travelSched : old.travel;
      }
      if (['gtfs', 'motis'].includes(BkkLib.rowSource(row)) && !BkkLib.mapGpsLatLon(row.lat, row.lon)) {
        const gps = BkkLib.mapGpsLatLon(old.lat, old.lon);
        if (gps) {
          row.lat = gps[0];
          row.lon = gps[1];
          row.gpsCarried = true;
          /* The fix's own time travels with it: a position carried poll
             after poll ages out (applyCoachGps) instead of staying live. */
          row.gpsAt = Number(old.gpsAt) > 0 ? Number(old.gpsAt) : nowSec();
        }
      }
    });
  };
  /* A new list of rows: what the last one knew about them, and the
     positions Home Assistant's sensors give. */
  const listed = (phase, list, changes) => {
    if (!live()) return;
    const merged = list || [];
    merged.forEach((row) => BkkLib.applyRailBadge(row));
    carryOver(merged);
    src.sensors(hass(), merged);
    show(phase, Object.assign({ rows: merged, filled: false, banner: '', loading: false }, changes));
  };
  /* A MÁV board ELVIRA did not refresh (an outage, the integration's rate
     limit, a failed or timed-out call, a destination board that did not
     arrive) keeps the trains still due from the last one, beside those
     this answer did list, like a failed FUTÁR poll keeps its rows. */
  const keepMavRows = (fresh) => {
    const cur = shown();
    if (cur.filled) return fresh;
    const have = new Set(fresh.map((row) => String(row.tripId || '')));
    const now = nowSec();
    const kept = (cur.rows || [])
      .filter((row) => BkkLib.rowSource(row) === 'elvira' && !have.has(String(row.tripId))
        && departureStillDue(row.dep, now))
      .map((row) => Object.assign({}, row));
    return fresh.concat(kept).sort((a, b) => (a.dep || 0) - (b.dep || 0));
  };
  /* A key-less MÁV card got here with elvira_between listed (_reload).
     It drops out of hass.services for a moment while HA restarts: that
     poll stays on the MÁV board, as one ELVIRA did not answer, not on
     FUTÁR the card has no key for ("no API key" over its trains). */
  const elviraGone = mode === 'mav' && !BkkLib.hasElvira(hass()) && !hasFutar();
  const mavBoard = mode === 'mav' && (BkkLib.hasElvira(hass()) || elviraGone)
    && !(src.now() < poll.state.viaFutarUntil && hasFutar());
  try {
    if (BkkLib.isPlaceStop({ id: cfg.stopId, kind: cfg.stopKind })) {
      show('place', { rows: [], banner: '', loading: false });
      return;
    }
    const wantElvira = (mode === 'mav' || mode === 'all') && BkkLib.hasElvira(hass());
    /* knownElvira: the ELVIRA answer this poll already has (the not_loaded
       switch to FUTÁR below), not asked for a second time. */
    const elviraP = knownElvira ? Promise.resolve(knownElvira) : elviraGone
      ? Promise.resolve({ rows: [], error: 'not_loaded', detail: '' })
      : wantElvira
      ? src.elvira(
        hass(),
        cfg.stopId,
        cfg.destStopId || '',
        /* The service takes 15..480 and rejects the call otherwise. */
        clampMinutesAfter(cfg.minutesAfter),
        cfg.destName || '',
        { apiKey: apiKey(), originName: cfg.stopName || '' },
      ).catch((err) => ({ rows: [], error: 'elvira_call_failed', detail: BkkLib.elviraErrText(err) }))
      : Promise.resolve({ rows: [], error: '', detail: '' });
    const cap = maxDepartureRows(cfg.minutesAfter);
    let earlyRows = [];
    let futarFull = null;
    let elviraRows = [];
    /* Why this poll's MÁV board is not ELVIRA's whole answer: the banner. */
    let mavErr = null;
    /* Beside FUTÁR's rows ELVIRA only overlays, except a train the
       destination's board confirms that FUTÁR lacks (IC TÓPART at :37
       from Székesfehérvár): that one is listed as on a MÁV card. */
    const addElvira = (row) => BkkLib.elviraBoardRows([row], nowSec())[0] || null;
    const publish = () => {
      if (!live()) return;
      /* An empty board goes on to the Transitous fill below, not to
         "no departures" first. */
      if (mavBoard) {
        if (elviraRows.length) listed('board', elviraRows, { banner: mavErr || '' });
        return;
      }
      let list = futarFull
        ? BkkLib.mergeRows(earlyRows, futarFull, cap)
        : earlyRows.slice();
      list = BkkLib.overlayRows(list, elviraRows, cap, addElvira);
      /* Through trips (the destination mid-route) come only from the
         through-check: an empty list before it answers is not "no
         departures". The first load keeps its spinner, a poll its rows. */
      if (!list.length && startThrough && !futarFull) {
        /* FUTÁR answers again: the last run's banner goes. Its
           Transitous fill-ins are not this board's rows (back to the
           spinner); FUTÁR rows it kept stay until the check answers. */
        const cur = shown();
        if (cur.banner) {
          const rows = cur.filled ? [] : cur.rows;
          /* Nothing on the board (the failed run left none): the
             spinner, not "no departures", until the check answers. */
          show('waiting', {
            rows, banner: '', filled: false, loading: (rows || []).length ? cur.loading : true,
          });
        }
        return;
      }
      listed('board', list);
    };
    let startThrough = null;
    let throughResolve = () => {};
    const throughWait = new Promise((resolve) => { throughResolve = resolve; });
    const rows = await src.timetables(
      apiKey(), cfg.stopId, cfg.routeIds, dest,
      {
        originName: cfg.stopName || '',
        originLat: cfg.stopLat,
        originLon: cfg.stopLon,
        cache: poll.tripCache,
        mode,
        city: cfg.city || '',
        minutesAfter: cfg.minutesAfter,
        deferTravel: true,
        deferGps: true,
        hass: hass(),
        skipFutar: mavBoard,
        /* The GTFS rows come before FUTÁR answers: the last run's FUTÁR
           rows stay beside them until then, instead of vanishing. */
        onPartial: (list) => {
          if (!live()) return;
          const cur = shown();
          const kept = (cur.filled ? [] : (cur.rows || []))
            .filter((row) => BkkLib.rowSource(row) === 'futar')
            .map((row) => Object.assign({}, row));
          listed('partial', kept.length ? BkkLib.mergeRows(kept, list, cap) : list);
        },
        /* departures() hands its through-check over instead of starting
           it: it runs once the first rows are painted (below). */
        armThroughCheck: (fn) => { startThrough = fn; },
        onFutarUpdate: (full) => {
          throughResolve();
          if (!live()) return;
          futarFull = full || [];
          publish();
        },
      },
    );
    if (!live()) return;
    show('listed', { quiet: true });
    earlyRows = rows || [];
    if (mavBoard) {
      const answer = await elviraP;
      if (!live()) return;
      if (answer.error === 'not_loaded' && hasFutar()) {
        /* The entry is unloaded (disabled, reloading) and its services
           stay listed: a card with a key reads FUTÁR until it is back. */
        poll.state.viaFutarUntil = src.now() + ELVIRA_RETRY_MS;
        return runBoard(poll, src, answer);
      }
      elviraRows = BkkLib.elviraBoardRows(answer.rows, nowSec());
      if (answer.error) {
        mavErr = elviraBanner(answer);
        elviraRows = keepMavRows(elviraRows);
      }
      elviraRows = elviraRows.slice(0, cap);
    }
    publish();
    /* The open map moves once a poll, at the end, on rows that have
       their GPS: an earlier pass saw a coach without it as one that lost
       it, and three passes raced each other on the estimate's times. */
    const firstRows = shown().rows;
    /* Only a FUTÁR trip has trip details to time the ride by. */
    const needsTravel = (row) => row.travel == null && BkkLib.rowSource(row) === 'futar';
    const fillTravel = (list) => Promise.all((list || []).map(async (row) => {
      if (!needsTravel(row)) return;
      row.travel = await src.rideTime(apiKey(), row.tripId, cfg.destKey, row.dep, origin, poll.tripCache);
    }));
    /* Live delays and positions from the GTFS-Realtime feeds, after the
       coach GPS so a bkk_stop position keeps its place (realtime.js). */
    const rtCtx = { mode, stopId: cfg.stopId, destStopId: cfg.destStopId || '' };
    const gpsTravel = Promise.all([
      src.coachGps(hass(), firstRows).then(async (withGps) => {
        /* A newer list (the through-check) must not be replaced by this
           older one; it gets its GPS on the last pass below. */
        if (!live() || shown().rows !== firstRows) return;
        show('gps', { rows: withGps });
        const rt = await src.realtime(hass(), withGps, rtCtx);
        if (!rt || !live() || shown().rows !== withGps) return;
        show('realtime', { rows: withGps });
      }),
      fillTravel(firstRows),
    ]);
    if (startThrough) {
      startThrough();
    } else {
      throughResolve();
    }
    if (!mavBoard) {
      const answer = await elviraP;
      if (!live()) return;
      elviraRows = answer.rows;
      /* ELVIRA answers again: the MÁV card goes back to its board. */
      if (!answer.error && mode === 'mav') poll.state.viaFutarUntil = 0;
      publish();
    }
    await throughWait;
    if (!live()) return;
    /* The board's own rows, without the trains ELVIRA added: one train
       the destination confirms is no reason to skip the fill, which
       brings the buses and trams beside it (the planner's mode all). */
    const hosts = mavBoard ? elviraRows
      : (futarFull ? BkkLib.mergeRows(earlyRows, futarFull, cap) : earlyRows.slice());
    /* A city board is filled from Transitous inside departures() already:
       a second fill here only repeated the same lookups on every poll. */
    if (!hosts.length && mode !== 'helyi') {
      const filled = await src.transitous(hosts, origin, dest, {
        mode,
        minutesAfter: cfg.minutesAfter,
      });
      if (!live()) return;
      const merged = BkkLib.overlayRows(filled, elviraRows, cap, addElvira);
      earlyRows = merged;
      /* Transitous rows, not this board's own: a failed poll replaces
         them instead of keeping them as if FUTÁR had sent them. */
      listed('filled', merged, { banner: mavErr || '', filled: filled.length > 0 });
    }
    show('settled', { quiet: true });
    await gpsTravel;
    if (!live()) return;
    /* Rows the through-check added (the destination mid-route) are new
       objects: their ride time and coach GPS are filled here. Trip
       details are cached by then, so this costs no request. */
    let last = shown().rows;
    if (last !== firstRows) {
      const latest = last;
      const [withGps] = await Promise.all([
        src.coachGps(hass(), latest),
        fillTravel(latest),
      ]);
      if (!live() || shown().rows !== latest) return;
      await src.realtime(hass(), withGps, rtCtx);
      if (!live() || shown().rows !== latest) return;
      last = withGps;
    }
    show('done', { rows: last });
  } catch (err) {
    if (!live()) return;
    show('failing', { loading: false, quiet: true });
    /* FUTÁR failing (a revoked key, an outage) used to look like an
       empty board, which Transitous then filled. It still fills it,
       now under the error banner instead of in silence. A poll that
       fails keeps the FUTÁR rows it had: only an empty board, or one
       Transitous filled before, is filled again. Rows that have all
       left by now count as an empty board: an outage longer than the
       kept rows must not leave only the banner. */
    const now = nowSec();
    const cur = shown();
    const keepRows = !cur.filled
      && (cur.rows || []).some((row) => row && departureStillDue(row.dep, now));
    if ((mode === 'bkk' || mode === 'mav') && !keepRows) {
      let filled = [];
      try {
        filled = await src.transitous([], origin, dest, {
          mode,
          minutesAfter: cfg.minutesAfter,
        });
      } catch (_e) {
        filled = [];
      }
      if (!live()) return;
      if (filled.length) {
        listed('failed', filled, { banner: err || '', filled: true });
        return;
      }
    }
    show('failed', { banner: err || '' });
  }
}

Object.assign(BkkLib, {
  /* The production adapters of a card's board: its index URLs for the
     timetables and the station search (withIndexes), Home Assistant for
     ELVIRA, the coach and realtime lookups and the sensors. */
  boardSources(cfg) {
    const lib = BkkLib.withIndexes(cfg);
    return {
      now: () => Date.now(),
      /* FUTÁR and the GTFS indexes (departures.js). */
      timetables: (key, stopId, routeIds, dest, opts) => lib.departures(key, stopId, routeIds, dest, opts),
      elvira: (hass, originId, destId, minutesAfter, destName, opts) => (
        lib.elviraBoard(hass, originId, destId, minutesAfter, destName, opts)),
      transitous: (rows, origin, dest, opts) => BkkLib.fillTransitousIfEmpty(rows, origin, dest, opts),
      coachGps: (hass, rows) => BkkLib.attachCoachGps(hass, rows),
      realtime: (hass, rows, ctx) => BkkLib.attachRealtime(hass, rows, ctx),
      rideTime: (key, tripId, destKey, dep, origin, cache) => BkkLib.travelMin(key, tripId, destKey, dep, origin, cache),
      sensors: (hass, rows) => BkkLib.enrichFromHass(hass, rows),
    };
  },
  /* One poll of a departure board (runBoard above). */
  nextBoard(poll) {
    return runBoard(poll, poll.sources || BkkLib.boardSources(poll.cfg));
  },
  /* The trains between two stations (elviraSource above), through the
     page's one ELVIRA source; elviraBetween is only the rows. */
  elviraBoard(hass, originId, destId, minutesAfter, destName, opts) {
    return sharedElvira.board(this || BkkLib, hass, originId, destId, minutesAfter, destName, opts);
  },
});

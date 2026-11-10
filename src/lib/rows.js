import { departureStillDue, vehicleIcon } from '../config.js';
import { BkkLib, register } from '../lib/core.js';

/* A departure row is one departure towards the destination, whichever
   source listed it (GLOSSARY.md). Its `source` says whose trip it is:
   'futar', 'gtfs' (a Volán or city timetable), 'elvira' (MÁV's boards,
   elvira-rows.js) or 'motis' (Transitous). A trip id keeps its source's
   prefix (FUTÁR's has none): it is a cache, route pattern and realtime
   key, and decisions read `source` instead. */
const SOURCES = new Set(['futar', 'gtfs', 'elvira', 'motis']);
const SOURCE_PREFIX = /^(gtfs|elvira|motis):/;

/* Lists hosts, then pairs each extra row with the host it is the same
   departure as (overlay) or lists it too. overlayOnly: an extra row that
   pairs with no host is dropped, or added as addUnmatched maps it. Host
   rows are filled in place. */
function combine(hosts, extra, limit, overlayOnly, addUnmatched) {
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
    const prevNumFromId = !!prevNum && BkkLib.rowSource(prev) === 'futar'
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
    const rowSource = BkkLib.rowSource(row);
    const rowElvira = rowSource === 'elvira';
    if (rowHasGps && (!prevHasGps || rowElvira)) {
      prev.lat = Number(row.lat);
      prev.lon = Number(row.lon);
    }
    /* FUTÁR's trip id goes onto the host (its trip details, vehicle and
       realtime match), Transitous' onto one without GPS; the source goes
       with it, since it names whose trip the row is. */
    const tid = String(row.tripId || '');
    if (tid && (rowSource === 'futar' || (rowSource === 'motis' && !prevHasGps))) {
      prev.tripId = tid;
      prev.source = rowSource;
    }
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
    && BkkLib.rowSource(row) !== 'futar'
    && !BkkLib.suburbanLineLabel(row.label);
  /* Pairing by minute (branded/numbered, named IC, label) is for one
     departure seen twice. Two rows with train numbers of their own that
     differ are two trains: ELVIRA lists the whole station, so G44 4711 to
     Budapest-Déli leaves Kelenföld in the same minute as 8738 to Siófok. */
  const numbersOf = (row) => [BkkLib.rowTrainNumber(row), looseNumber(row)]
    .map(numberKey).filter(Boolean);
  /* Two FUTÁR trips are two departures, whatever their label and minute:
     paired, one row would carry one's trip id under the other's headsign. */
  const otherTrain = (a, b) => {
    const ta = String((a && a.tripId) || '');
    const tb = String((b && b.tripId) || '');
    if (ta !== tb && BkkLib.rowSource(a) === 'futar' && BkkLib.rowSource(b) === 'futar') return true;
    const x = numbersOf(a);
    const y = numbersOf(b);
    return !!x.length && !!y.length && !x.some((n) => y.indexOf(n) >= 0);
  };
  const brandedHost = (row) => BkkLib.isTrainRow(row)
    && !BkkLib.numberedRailBadge(row)
    && (BkkLib.suburbanLineLabel(row && row.label)
      || (!BkkLib.genericRailLabel(row && row.label) && String((row && row.label) || '').trim()));
  /* The kind a row's train is: ELVIRA's railKind, FUTÁR's label. FUTÁR's
     bare S is a személyvonat and ELVIRA's S a sebesvonat, so S, Sz, SV
     and Gy are one kind here; a number for a label is no kind at all. */
  const railKindOf = (row) => {
    const elvira = BkkLib.rowSource(row) === 'elvira';
    const k = String((elvira && row.railKind) || (row && row.label) || '').trim().toUpperCase().normalize('NFC');
    if (!k || /^\d+$/.test(k)) return '';
    return /^(S|SZ|SV|GY)$/.test(k) ? 'S' : k;
  };
  const terminusOf = (row) => String((BkkLib.rowSource(row) === 'elvira' ? row.end : row.head) || '').trim();
  /* An ELVIRA train the card would add stands for a host row with no
     number to pair by (an ad-hoc FUTÁR trip) only when both leave in the
     same minute, of the same kind, for the same terminus. */
  const sameTrain = (p, row) => BkkLib.isTrainRow(p) && minuteOf(p) === minuteOf(row)
    && !!railKindOf(p) && railKindOf(p) === railKindOf(row)
    && !!terminusOf(p) && BkkLib.nameEq(terminusOf(p), terminusOf(row))
    && !otherTrain(p, row);
  /* skipUnmatched: an overlay-only row, dropped when nothing pairs it.
     asNew: the row added in its place when nothing does; it pairs as
     strictly as an overlay-only row, plus sameTrain. */
  const add = (row, skipUnmatched, asNew) => {
    const strict = skipUnmatched || !!asNew;
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
    if (!strict && BkkLib.isTrainRow(row) && min) {
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
    if (!strict && byTime.has(tk) && !otherTrain(out[byTime.get(tk)], row)) {
      overlay(out[byTime.get(tk)], row);
      return;
    }
    if (skipUnmatched) return;
    if (asNew && BkkLib.isTrainRow(row) && min) {
      const same = out.findIndex((p) => sameTrain(p, row));
      if (same >= 0) {
        overlay(out[same], row);
        registerNumber(n, same);
        return;
      }
    }
    const idx = out.length;
    out.push(asNew || row);
    byTime.set(tk, idx);
    registerNumber(n, idx);
    registerNumber(looseNumber(row), idx, byLoose);
  };
  hosts.forEach((row) => add(row, false));
  extra.forEach((row) => {
    const own = addUnmatched ? addUnmatched(row) : null;
    add(row, overlayOnly && !own, own);
  });
  out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
  const cap = Number(limit);
  return out.slice(0, (Number.isFinite(cap) && cap > 0) ? cap : 12);
}

register({
  /* The source of a trip id, by its prefix: '' for none. */
  tripIdSource(tripId) {
    const t = String(tripId || '');
    if (!t) return '';
    const m = t.match(SOURCE_PREFIX);
    return m ? m[1] : 'futar';
  },
  /* A trip id FUTÁR's own API knows (trip details, vehicles). */
  isFutarTripId(tripId) {
    return BkkLib.tripIdSource(tripId) === 'futar';
  },
  /* Every producer sets `source`; an object built elsewhere (a vehicle, a
     leg, a test's row) is read off its trip id. A coach position's own
     `source` ('volan') is not a row's. */
  rowSource(row) {
    if (!row) return '';
    return SOURCES.has(row.source) ? row.source : BkkLib.tripIdSource(row.tripId);
  },
  rowTrainNumber(row) {
    const direct = String((row && row.trainNumber) || '').trim();
    if (direct) return direct.replace(/^0+/, '') || direct;
    return BkkLib.trainNumberFromTripId(row && row.tripId);
  },
  /* One list from two sources' rows: the hosts', then each extra row on the
     host it is the same departure as (its colour, platform, booking, GPS,
     FUTÁR trip id fill the host's gaps), or as a row of its own. Sorted by
     departure, at most limit rows (12 without one). */
  mergeRows(hosts, extra, limit) {
    return combine(hosts || [], extra || [], limit, false, null);
  },
  /* The hosts with what an overlay source knows about the same departures
     (ELVIRA's booking and platform on FUTÁR's trains). An overlay row pairs
     only by train number, or numbered with branded (not by label and
     minute: GEMENC would land on a FUTÁR IC). One that pairs with no host
     is dropped, unless addUnmatched maps it to a row of its own (an ELVIRA
     train the destination's board confirms): that one also pairs as the
     same train (minute, kind, terminus) or is added. */
  overlayRows(hosts, overlay, limit, addUnmatched) {
    return combine(hosts || [], overlay || [], limit, true,
      typeof addUnmatched === 'function' ? addUnmatched : null);
  },
  /* One departure in the shape the card renders: display strings plus the raw
     epoch seconds, so the countdown can tick without refetching. */
  asRow(r, destName, lang) {
    BkkLib.applyRailBadge(r);
    const dep = r.dep;
    let type = String(r.rawType || r.vehicle || 'BUS').toUpperCase();
    if (type.indexOf('SUBURBAN') >= 0 || type === 'TRAIN' || type === 'RAILWAY') type = 'RAIL';
    let head = String(r.head || destName || '').trim();
    const plat = r.platform ? String(r.platform) : BkkLib.platformFromText(head);
    head = BkkLib.stripPlatform(head);
    const lat = Number(r.lat);
    const lon = Number(r.lon);
    const gps = BkkLib.mapGpsLatLon(lat, lon);
    const hasGps = !!gps;
    const shape = Array.isArray(r.shape) ? r.shape : undefined;
    return {
      type: type,
      icon: vehicleIcon(type),
      label: r.label,
      headsign: head,
      tripId: r.tripId || '',
      source: BkkLib.rowSource(r),
      depTs: dep || 0,
      schedTs: r.sched || dep || 0,
      attime: BkkLib.hm(r.sched || dep),
      predicted_attime: BkkLib.hm(dep),
      wheelchair: !!r.wheelchair,
      bikesAllowed: !!r.bikesAllowed,
      color: String(r.color || '').replace('#', ''),
      textcolor: String(r.text || r.textcolor || '').replace('#', ''),
      platform: plat || '',
      trainNumber: r.trainNumber || '',
      delay: Number(r.delay || 0),
      booking: !!r.booking,
      nationalPass: !!r.nationalPass,
      reservationFreeSection: !!r.reservationFreeSection,
      travelMin: r.travel,
      lat: hasGps ? gps[0] : null,
      lon: hasGps ? gps[1] : null,
      hasGps: hasGps,
      shape: shape,
      gtfs: r.gtfs || null,
      vehicle: r.vehicle || '',
      vehicleId: r.vehicleId || '',
      licensePlate: r.licensePlate || '',
      model: r.model || '',
      vehicleStatus: r.vehicleStatus || '',
      stopDistancePercent: r.stopDistancePercent,
    };
  },
});

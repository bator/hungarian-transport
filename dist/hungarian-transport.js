const CARD_VERSION = '1.4.4-rev.9';

const BKK_PLANNER_TAG = 'hungarian-transit-stop-card-plan';
const BKK_PLANNER_TAG_ALIAS = 'bkk-stop-card-plan';
const BKK_API = 'https://go.bkk.hu/api/query/v1/ws/otp/api/where';
/* Default departure look-ahead. Override per card with `minutesAfter`.
   The Kelenfold-Szekesfehervar hop lists trains past 2h, so 180 is the
   floor that still fills travelMin on those rows. */
const DEFAULT_MINUTES_AFTER = 180;
const MIN_MINUTES_AFTER = 15;
const MAX_MINUTES_AFTER = 480;
const MINUTES_AFTER_PRESETS = [30, 60, 90, 120, 180, 240, 360, 480];
const DEPART_MINUTES_AFTER = String(DEFAULT_MINUTES_AFTER);

/* A departure stays on the card until a minute after it was due.
   Anything older is a bus that already left. */
const DEPARTURE_GRACE_SEC = 60;

function departureStillDue(dep, nowSec) {
  const ts = Number(dep);
  if (!Number.isFinite(ts) || ts <= 0) return true;
  return ts >= (nowSec || Math.floor(Date.now() / 1000)) - DEPARTURE_GRACE_SEC;
}

function clampMinutesAfter(value) {
  if (value == null || value === '') return DEFAULT_MINUTES_AFTER;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MINUTES_AFTER;
  return Math.min(MAX_MINUTES_AFTER, Math.max(MIN_MINUTES_AFTER, Math.round(n)));
}

function maxDepartureRows(horizon) {
  const h = clampMinutesAfter(horizon);
  const cap = h >= 480 ? 80 : 40;
  return Math.min(cap, Math.max(12, Math.round(h / 2.5)));
}
const PLATFORM_IN_HEAD = /(?:\u00b7\s*)?(?:v\u00e1g|pl)\.(\S+)/i;
const PLATFORM_STRIP = /\s*(?:\u00b7\s*)?(?:v\u00e1g|pl)\.\S+/gi;

/* Shortcut chips in the editor: national hubs only. Override them per card with
   `favorites: [{ id, name }]` if your own stops are elsewhere. */
const BKK_FAVORITES = [
  { id: 'BKK_CSF01131', name: 'Keleti p\u00e1lyaudvar' },
  { id: 'BKK_056216', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
];
const MAV_FAVORITES = [
  { id: 'BKK_005510017', name: 'Budapest-Keleti' },
  { id: 'BKK_005501024', name: 'Budapest-Kelenf\u00f6ld' },
  { id: 'BKK_005503269', name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r' },
];
const VOLAN_FAVORITES = [
  { id: 'hkir_773521', name: 'Budapest, N\u00e9pliget aut\u00f3busz-p\u00e1lyaudvar' },
  { id: 'volan_773538_99', name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
];
const FAVORITES = BKK_FAVORITES.concat(VOLAN_FAVORITES, MAV_FAVORITES);
const DEFAULT_FAVORITES = {
  bkk: BKK_FAVORITES,
  mav: MAV_FAVORITES,
  volan: VOLAN_FAVORITES,
  helyi: [],
  all: FAVORITES,
};

const VEHICLE_ICONS = {
  BUS: 'mdi:bus',
  TRAM: 'mdi:tram',
  TROLLEYBUS: 'mdi:bus-electric',
  SUBWAY: 'mdi:subway-variant',
  METRO: 'mdi:subway-variant',
  RAIL: 'mdi:train',
  TRAIN: 'mdi:train',
  FERRY: 'mdi:ferry',
};

function vehicleIcon(type) {
  const key = String(type || '').toUpperCase();
  if (VEHICLE_ICONS[key]) return VEHICLE_ICONS[key];
  if (key.indexOf('RAIL') >= 0 || key.indexOf('TRAIN') >= 0) return 'mdi:train';
  if (key.indexOf('TRAM') >= 0) return 'mdi:tram';
  if (key.indexOf('TROLLEY') >= 0) return 'mdi:bus-electric';
  if (key.indexOf('SUBWAY') >= 0 || key.indexOf('METRO') >= 0) return 'mdi:subway-variant';
  return 'mdi:bus';
}

/* BKK GO rail-line badge colours. ELVIRA rows have no route color (always
   #4477aa); FUTÁR is often empty on a MÁV hop, so the label is the source. */
const BKK_RAIL_BADGE = {
  G43: ['AACD46', 'FFFFFF'],
  Z30: ['FFCD28', '3C3C3C'],
  S10: ['00AFF0', 'FFFFFF'],
  S12: ['00AFF0', 'FFFFFF'],
  S30: ['00AFF0', 'FFFFFF'],
  S36: ['00AFF0', 'FFFFFF'],
  S40: ['00AFF0', 'FFFFFF'],
  IC: ['2E5EA8', 'FFFFFF'],
  EC: ['2E5EA8', 'FFFFFF'],
  S: ['2E5EA8', 'FFFFFF'],
};

/* Assets live next to this module, so the card works from /hacsfiles/, /local/
   or wherever the Lovelace resource points. Requires `type: module`. */
const ASSET_BASE = new URL('.', import.meta.url).href;
let VOLAN_INDEX_OVERRIDE = '';
let CITY_INDEX_OVERRIDE = '';
let leafletPromise = null;

function setVolanIndexUrl(url) {
  VOLAN_INDEX_OVERRIDE = String(url || '');
}

function setCityIndexUrl(url) {
  CITY_INDEX_OVERRIDE = String(url || '');
}

/* Hungarian timetables are published in Budapest wall-clock time. Deriving the
   service day and the minute offsets from the browser's zone breaks the card
   for anyone whose Home Assistant is set to a different timezone. */
const TZ = 'Europe/Budapest';
const TZ_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: TZ,
  hour12: false,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});
const pad2 = (n) => String(n).padStart(2, '0');

function tzFields(date) {
  const out = {};
  TZ_PARTS.formatToParts(date).forEach((p) => {
    if (p.type !== 'literal') out[p.type] = p.value;
  });
  return {
    y: Number(out.year),
    mo: Number(out.month),
    d: Number(out.day),
    h: Number(out.hour) % 24,
    mi: Number(out.minute),
  };
}

function tzParts(date) {
  const f = tzFields(date);
  return { ymd: `${f.y}${pad2(f.mo)}${pad2(f.d)}`, mins: f.h * 60 + f.mi };
}

/* Epoch seconds for a Budapest wall-clock moment given as YYYYMMDD plus minutes
   from midnight. GTFS uses 24:xx and beyond for after-midnight trips, so mins
   may exceed 1440. Two passes settle the DST offset. */
function tzEpochSec(ymd, mins) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const target = Date.UTC(y, mo - 1, d) + (mins || 0) * 60000;
  let utc = target;
  for (let i = 0; i < 2; i++) {
    const f = tzFields(new Date(utc));
    utc += target - Date.UTC(f.y, f.mo - 1, f.d, f.h, f.mi);
  }
  return Math.round(utc / 1000);
}

function tzShiftYmd(ymd, days) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const t = new Date(Date.UTC(y, mo - 1, d + (days || 0)));
  return `${t.getUTCFullYear()}${pad2(t.getUTCMonth() + 1)}${pad2(t.getUTCDate())}`;
}

/* GTFS calendar.txt starts the week on Monday. */
function tzDow(ymd) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const js = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  return js === 0 ? 6 : js - 1;
}

function dayBuster(base) {
  return `${base}${base.includes('?') ? '&' : '?'}v=${tzParts(new Date()).ymd}`;
}

function volanIndexUrl() {
  const base = VOLAN_INDEX_OVERRIDE || new URL('volan-index.json.gz', ASSET_BASE).href;
  return dayBuster(base);
}

function cityIndexUrl() {
  const base = CITY_INDEX_OVERRIDE || new URL('city-index.json.gz', ASSET_BASE).href;
  return dayBuster(base);
}

const I18N = {
  hu: {
    langAuto: 'Automatikus (Home Assistant nyelve)',
    langHu: 'Magyar',
    langEn: 'English',
    langLabel: 'Nyelv',
    apiKeyLabel: 'BKK API kulcs',
    apiKeyPlaceholder: 'UUID a BKK Open Dat\u00e1b\u00f3l',
    apiKeyHint: 'Ingyenes kulcs, egyszer\u0171 regisztr\u00e1ci\u00f3 ut\u00e1n:',
    apiKeyOk: 'A BKK API kulcs rendben van.',
    apiKeyChange: 'M\u00f3dos\u00edt\u00e1s',
    nameLabel: 'N\u00e9v (opcion\u00e1lis)',
    namePlaceholder: 'Hungarian transport',
    destLoading: 'El\u00e9rhet\u0151 c\u00e9lok bet\u00f6lt\u00e9se...',
    destEmptySuggest: (list) => 'Nincs k\u00f6zvetlen j\u00e1rat erre a c\u00e9lra. El\u00e9rhet\u0151 c\u00e9lok: ' + list + '.',
    platformShort: (p) => `v\u00e1g.${p}`,
    tipPlatform: (p) => `V\u00e1g\u00e1ny ${p}`,
    minutes: (n) => `${n} perc`,
    now: 'most',
    cardNoDepartures: 'Nincs k\u00f6zelg\u0151 indul\u00e1s.',
    cardPickStops: 'V\u00e1lassz forr\u00e1s \u00e9s c\u00e9l meg\u00e1ll\u00f3t a k\u00e1rtya be\u00e1ll\u00edt\u00e1saiban.',
    cardLoading: 'Bet\u00f6lt\u00e9s\u2026',
    tipWheelchair: 'Akad\u00e1lymentes, alacsonypadl\u00f3s j\u00e1rm\u0171',
    tipBike: 'Ker\u00e9kp\u00e1r sz\u00e1ll\u00edthat\u00f3',
    tipBooking: 'Helyjegy v\u00e1lt\u00e1sa k\u00f6telez\u0151',
    tipDelay: (n) => `${n} perc k\u00e9s\u00e9s`,
    tipEarly: (n) => `${n} perccel kor\u00e1bban indul`,
    tipScheduled: (hm) => `Menetrend szerint ${hm}`,
    tipTravel: (n) => `Menetid\u0151 a c\u00e9lig: ${n} perc`,
    tipTrain: (nr) => `Vonatsz\u00e1m: ${nr}`,
    mapOpenTip: 'T\u00e9rk\u00e9p: j\u00e1rm\u0171 az \u00fatvonalon',
    mapClose: 'Bez\u00e1r\u00e1s',
    mapLoadingRoute: '\u00datvonal bet\u00f6lt\u00e9se\u2026',
    mapNoData: 'Ehhez a j\u00e1rathoz jelenleg nincs \u00e9l\u0151 poz\u00edci\u00f3 vagy \u00fatvonal.',
    mapLeafletFail: 'A t\u00e9rk\u00e9p bet\u00f6lt\u00e9se nem siker\u00fclt.',
    mapNoGeometry: 'A BKK nem adott vissza \u00fatvonal-geometri\u00e1t ehhez a j\u00e1rathoz.',
    mapRouteFail: (msg) => `\u00datvonal nem t\u00f6lthet\u0151: ${msg}`,
    mapNothingToShow: 'Nincs megjelen\u00edthet\u0151 \u00fatvonal ehhez a j\u00e1rathoz.',
    mapFullRoute: 'Teljes \u00fatvonal',
    mapLivePos: '\u00c9l\u0151 poz\u00edci\u00f3',
    mapEstPos: 'Becs\u00fclt poz\u00edci\u00f3 (menetrend)',
    mapNoGps: 'Nincs \u00e9l\u0151 GPS',
    mapUntilStop: (n) => `Meg\u00e1ll\u00f3ig: ${n}%`,
    mapExpected: (hm) => `V\u00e1rhat\u00f3: ${hm}`,
    mapExpectedVs: (pair) => `V\u00e1rhat\u00f3: ${pair[0]} (menetrend ${pair[1]})`,
    mapFootFallback: 'J\u00e1rm\u0171 a t\u00e9rk\u00e9pen',
    mapLiveDot: '\u00c9l\u0151 poz\u00edci\u00f3',
    mapEstDot: 'Becs\u00fclt poz\u00edci\u00f3',
    mapTypeBus: 'Busz',
    mapTypeTram: 'Villamos',
    mapTypeTrolley: 'Trolibusz',
    mapTypeSubway: 'Metr\u00f3',
    mapTypeRail: 'Vonat',
    mapTypeVehicle: 'J\u00e1rm\u0171',
    errNoApiKey: 'Hi\u00e1nyzik az apiKey a k\u00e1rtya be\u00e1ll\u00edt\u00e1s\u00e1b\u00f3l.',
    errApiKeyBad: 'A BKK API kulcs \u00e9rv\u00e9nytelen (401/403).',
    errApiKeyProbe: 'A BKK API kulcsot nem siker\u00fclt ellen\u0151rizni.',
    errVolanHttp: (status) => `Vol\u00e1n menetrend HTTP ${status}`,
    errVolanGunzip: 'A b\u00f6ng\u00e9sz\u0151 nem tudja kicsomagolni a Vol\u00e1n menetrendet.',
    errCityHttp: (status) => `Helyi menetrend HTTP ${status}`,
    errCityGunzip: 'A b\u00f6ng\u00e9sz\u0151 nem tudja kicsomagolni a helyi menetrendet.',
    errIndexFetch: 'A menetrend-index nem t\u00f6lt\u0151d\u00f6tt le (id\u0151t\u00fall\u00e9p\u00e9s vagy h\u00e1l\u00f3zati hiba).',
    errIndexParse: 'A let\u00f6lt\u00f6tt menetrend-index olvashatatlan.',
    errIndexSchema: (v) => `A menetrend-index \u00fajabb (v${v}), mint amit ez a k\u00e1rtya ismer. Friss\u00edtsd a k\u00e1rty\u00e1t.`,
    cityLabel: 'V\u00e1ros',
    cityPlaceholder: 'V\u00e1lassz v\u00e1rost',
    minutesAfterLabel: 'Menetrend el\u0151re (perc)',
    minutesAfterHint: 'H\u00e1ny percet n\u00e9zzen el\u0151re az indul\u00e1sokb\u00f3l. 30\u2013480 perc, alap\u00e9rtelmez\u00e9s 180.',
    plannerTitle: 'Tervez\u0151',
    plannerStep1: 'Forr\u00e1s',
    plannerStep2: 'C\u00e9l',
    plannerStep3: 'Meddig',
    plannerSwap: 'Csere',
    plannerDepartures: 'Indul\u00e1sok',
    plannerSearchPlaceholder: 'Utca, meg\u00e1ll\u00f3, aut\u00f3busz-\u00e1llom\u00e1s...',
    plannerDestPlaceholder: 'C\u00e9l meg\u00e1ll\u00f3 keres\u00e9se...',
    plannerReset: '\u00daj',
    plannerMultiHint: 'T\u00f6bbet is v\u00e1laszthatsz. Ekkor csak a k\u00f6z\u00f6s meg\u00e1ll\u00f3k maradnak.',
    plannerPickVehicleFirst: 'El\u0151bb v\u00e1lassz j\u00e1rm\u0171vet',
    plannerPickAll: 'V\u00e1lassz forr\u00e1s \u00e9s c\u00e9l meg\u00e1ll\u00f3t.',
    plannerLoading: 'Bet\u00f6lt\u00e9s...',
    plannerNoSharedStop: 'Nincs k\u00f6z\u00f6s meg\u00e1ll\u00f3',
    plannerNoSharedLater: 'Ezeknek a j\u00e1ratoknak nincs k\u00f6z\u00f6s k\u00e9s\u0151bbi meg\u00e1ll\u00f3ja.',
    plannerJourneyTitle: '\u00c1tsz\u00e1ll\u00e1ssal',
    plannerJourneySummary: (parts) => `${parts[0]} perc \u00b7 ${parts[1]} \u00e1tsz\u00e1ll\u00e1s \u00b7 ${parts[2]} perc gyalogl\u00e1s`,
    plannerWalk: (parts) => `Gyalogl\u00e1s ${parts[0]} perc, ${parts[1]} m`,
    plannerRide: (min) => `${min} perc`,
    plannerPickDest: 'V\u00e1lassz c\u00e9lt...',
    plannerNoRoutes: 'Nincs indul\u00f3 j\u00e1rat ebben a meg\u00e1ll\u00f3ban.',
    plannerNoDepartures: 'Nincs k\u00f6zelg\u0151 indul\u00e1s a v\u00e1lasztott j\u00e1ratokkal.',
    colArrival: '\u00e9rkez\u00e9s',
    colExpected: 'v\u00e1rhat\u00f3',
    colTravel: 'menetid\u0151',
    mode: {
      bkk: {
        stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
        dest: 'C\u00e9l meg\u00e1ll\u00f3',
        hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l meg\u00e1ll\u00f3t. Az \u00f6sszes BKK j\u00e1rat megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s, pl. Bosny\u00e1k t\u00e9r, \u00c1rp\u00e1d h\u00edd',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 meg\u00e1ll\u00f3k k\u00f6z\u00f6tt',
        empty: 'Nincs BKK meg\u00e1ll\u00f3 erre a keres\u00e9sre.',
        destEmpty: 'Err\u0151l a meg\u00e1ll\u00f3r\u00f3l nincs j\u00e1rat erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
        destNone: 'Nincs el\u00e9rhet\u0151 BKK c\u00e9l ebb\u0151l a forr\u00e1sb\u00f3l.',
      },
      all: {
        stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
        dest: 'C\u00e9l meg\u00e1ll\u00f3',
        hint: 'V\u00e1lassz forr\u00e1s \u00e9s c\u00e9l meg\u00e1ll\u00f3t. Minden BKK, Vol\u00e1n \u00e9s M\u00c1V j\u00e1rat megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Utca, meg\u00e1ll\u00f3, aut\u00f3busz-\u00e1llom\u00e1s...',
        destPh: 'C\u00e9l meg\u00e1ll\u00f3 keres\u00e9se...',
        empty: 'Nincs meg\u00e1ll\u00f3 erre a keres\u00e9sre.',
        destEmpty: 'Nincs j\u00e1rat ebb\u0151l a meg\u00e1ll\u00f3b\u00f3l oda.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
        destNone: 'Ebb\u0151l a forr\u00e1sb\u00f3l nincs k\u00f6zvetlen c\u00e9l.',
      },
      mav: {
        stop: 'Forr\u00e1s \u00e1llom\u00e1s',
        dest: 'C\u00e9l \u00e1llom\u00e1s',
        hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l \u00e1llom\u00e1st. Az \u00f6sszes vonat megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s, pl. Kelenf\u00f6ld, Sz\u00e9kesfeh\u00e9rv\u00e1r',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 \u00e1llom\u00e1sok k\u00f6z\u00f6tt',
        empty: 'Nincs M\u00c1V \u00e1llom\u00e1s erre a keres\u00e9sre.',
        destEmpty: 'Err\u0151l az \u00e1llom\u00e1sr\u00f3l nincs vonat erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s \u00e1llom\u00e1st.',
        destNone: 'Nincs el\u00e9rhet\u0151 M\u00c1V c\u00e9l\u00e1llom\u00e1s ebb\u0151l a forr\u00e1sb\u00f3l.',
      },
      volan: {
        stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
        dest: 'C\u00e9l meg\u00e1ll\u00f3',
        hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l Vol\u00e1n meg\u00e1ll\u00f3t. Az \u00f6sszes busz megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s, pl. N\u00e9pliget, Sz\u00e9kesfeh\u00e9rv\u00e1r aut\u00f3busz-\u00e1llom\u00e1s',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 Vol\u00e1n meg\u00e1ll\u00f3k k\u00f6z\u00f6tt',
        empty: 'Nincs Vol\u00e1n meg\u00e1ll\u00f3 erre a keres\u00e9sre.',
        destEmpty: 'Err\u0151l a meg\u00e1ll\u00f3r\u00f3l nincs k\u00f6zvetlen Vol\u00e1n erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
        destNone: 'Nincs el\u00e9rhet\u0151 Vol\u00e1n c\u00e9l ebb\u0151l a forr\u00e1sb\u00f3l.',
      },
      helyi: {
        stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
        dest: 'C\u00e9l meg\u00e1ll\u00f3',
        hint: 'V\u00e1lassz v\u00e1rost, majd a forr\u00e1s \u00e9s a c\u00e9l meg\u00e1ll\u00f3t. A helyi j\u00e1ratok jelennek meg k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s a v\u00e1ros meg\u00e1ll\u00f3i k\u00f6z\u00f6tt',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 meg\u00e1ll\u00f3k k\u00f6z\u00f6tt',
        empty: 'Nincs meg\u00e1ll\u00f3 erre a keres\u00e9sre ebben a v\u00e1rosban.',
        destEmpty: 'Err\u0151l a meg\u00e1ll\u00f3r\u00f3l nincs k\u00f6zvetlen j\u00e1rat erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
        destNone: 'Nincs el\u00e9rhet\u0151 c\u00e9l ebb\u0151l a forr\u00e1sb\u00f3l.',
      },
    },
  },
  en: {
    langAuto: 'Automatic (Home Assistant language)',
    langHu: 'Magyar',
    langEn: 'English',
    langLabel: 'Language',
    apiKeyLabel: 'BKK API key',
    apiKeyPlaceholder: 'UUID from BKK Open Data',
    apiKeyHint: 'Free key, after a quick sign-up:',
    apiKeyOk: 'The BKK API key is working.',
    apiKeyChange: 'Change',
    nameLabel: 'Name (optional)',
    namePlaceholder: 'Hungarian transport',
    destLoading: 'Loading reachable destinations...',
    destEmptySuggest: (list) => 'No direct service to that destination. Reachable destinations: ' + list + '.',
    platformShort: (p) => `pl.${p}`,
    tipPlatform: (p) => `Platform ${p}`,
    minutes: (n) => `${n} min`,
    now: 'now',
    cardNoDepartures: 'No upcoming departures.',
    cardPickStops: 'Pick an origin and a destination stop in the card settings.',
    cardLoading: 'Loading\u2026',
    tipWheelchair: 'Wheelchair accessible, low-floor vehicle',
    tipBike: 'Bikes allowed',
    tipBooking: 'Seat reservation required',
    tipDelay: (n) => `${n} min late`,
    tipEarly: (n) => `${n} min early`,
    tipScheduled: (hm) => `Scheduled for ${hm}`,
    tipTravel: (n) => `Travel time to destination: ${n} min`,
    tipTrain: (nr) => `Train number: ${nr}`,
    mapOpenTip: 'Map: vehicle on its route',
    mapClose: 'Close',
    mapLoadingRoute: 'Loading route\u2026',
    mapNoData: 'No live position or route is available for this trip.',
    mapLeafletFail: 'Could not load the map.',
    mapNoGeometry: 'BKK did not return a route shape for this trip.',
    mapRouteFail: (msg) => `Could not load the route: ${msg}`,
    mapNothingToShow: 'Nothing to show on the map for this trip.',
    mapFullRoute: 'Full route',
    mapLivePos: 'Live position',
    mapEstPos: 'Estimated position (timetable)',
    mapNoGps: 'No live GPS',
    mapUntilStop: (n) => `To stop: ${n}%`,
    mapExpected: (hm) => `Due: ${hm}`,
    mapExpectedVs: (pair) => `Due: ${pair[0]} (scheduled ${pair[1]})`,
    mapFootFallback: 'Vehicle on the map',
    mapLiveDot: 'Live position',
    mapEstDot: 'Estimated position',
    mapTypeBus: 'Bus',
    mapTypeTram: 'Tram',
    mapTypeTrolley: 'Trolleybus',
    mapTypeSubway: 'Metro',
    mapTypeRail: 'Train',
    mapTypeVehicle: 'Vehicle',
    errNoApiKey: 'The apiKey is missing from the card configuration.',
    errApiKeyBad: 'The BKK API key is invalid (401/403).',
    errApiKeyProbe: 'Could not verify the BKK API key.',
    errVolanHttp: (status) => `Vol\u00e1n timetable HTTP ${status}`,
    errVolanGunzip: 'This browser cannot decompress the Vol\u00e1n timetable.',
    errCityHttp: (status) => `Local timetable HTTP ${status}`,
    errCityGunzip: 'This browser cannot decompress the local timetable.',
    errIndexFetch: 'Could not download the timetable index (timeout or network error).',
    errIndexParse: 'The downloaded timetable index is unreadable.',
    errIndexSchema: (v) => `The timetable index is newer (v${v}) than this card understands. Please update the card.`,
    cityLabel: 'City',
    cityPlaceholder: 'Pick a city',
    minutesAfterLabel: 'Look-ahead (minutes)',
    minutesAfterHint: 'How far ahead to list departures. 30\u2013480 minutes, default 180.',
    plannerTitle: 'Planner',
    plannerStep1: 'Origin',
    plannerStep2: 'Destination',
    plannerStep3: 'Look-ahead',
    plannerSwap: 'Swap',
    plannerDepartures: 'Departures',
    plannerSearchPlaceholder: 'Street, stop, coach station...',
    plannerDestPlaceholder: 'Search destination stop...',
    plannerReset: 'Reset',
    plannerMultiHint: 'You can pick several. Only stops shared by all of them are kept.',
    plannerPickVehicleFirst: 'Pick a vehicle first',
    plannerPickAll: 'Pick an origin and a destination stop.',
    plannerLoading: 'Loading...',
    plannerNoSharedStop: 'No shared stop',
    plannerNoSharedLater: 'These routes share no later stop.',
    plannerJourneyTitle: 'With a transfer',
    plannerJourneySummary: (parts) => `${parts[0]} min \u00b7 ${parts[1]} transfer \u00b7 ${parts[2]} min walking`,
    plannerWalk: (parts) => `Walk ${parts[0]} min, ${parts[1]} m`,
    plannerRide: (min) => `${min} min`,
    plannerPickDest: 'Pick a destination...',
    plannerNoRoutes: 'No departures from this stop.',
    plannerNoDepartures: 'No upcoming departure on the selected routes.',
    colArrival: 'in',
    colExpected: 'expected',
    colTravel: 'travel',
    mode: {
      bkk: {
        stop: 'Origin stop',
        dest: 'Destination stop',
        hint: 'Pick an origin and a destination stop. Every BKK service between them is listed.',
        originPh: 'Search, e.g. Bosny\u00e1k t\u00e9r, \u00c1rp\u00e1d h\u00edd',
        destPh: 'Search among the stops reachable from the origin',
        empty: 'No BKK stop matches this search.',
        destEmpty: 'No service from this stop to that destination.',
        destNeedOrigin: 'Pick an origin stop first.',
        destNone: 'No BKK destination is reachable from this origin.',
      },
      all: {
        stop: 'Origin stop',
        dest: 'Destination stop',
        hint: 'Pick an origin and a destination stop. Every BKK, Vol\u00e1n and M\u00c1V service between them is listed.',
        originPh: 'Street, stop, coach station...',
        destPh: 'Search destination stop...',
        empty: 'No stop matches this search.',
        destEmpty: 'No service from this stop to that destination.',
        destNeedOrigin: 'Pick an origin stop first.',
        destNone: 'No destination is reachable from this origin.',
      },
      mav: {
        stop: 'Origin station',
        dest: 'Destination station',
        hint: 'Pick an origin and a destination station. Every train between them is listed.',
        originPh: 'Search, e.g. Kelenf\u00f6ld, Sz\u00e9kesfeh\u00e9rv\u00e1r',
        destPh: 'Search among the stations reachable from the origin',
        empty: 'No M\u00c1V station matches this search.',
        destEmpty: 'No train from this station to that destination.',
        destNeedOrigin: 'Pick an origin station first.',
        destNone: 'No M\u00c1V destination is reachable from this origin.',
      },
      volan: {
        stop: 'Origin stop',
        dest: 'Destination stop',
        hint: 'Pick an origin and a destination Vol\u00e1n stop. Every coach between them is listed.',
        originPh: 'Search, e.g. N\u00e9pliget, Sz\u00e9kesfeh\u00e9rv\u00e1r aut\u00f3busz-\u00e1llom\u00e1s',
        destPh: 'Search among the Vol\u00e1n stops reachable from the origin',
        empty: 'No Vol\u00e1n stop matches this search.',
        destEmpty: 'No direct coach from this stop to that destination.',
        destNeedOrigin: 'Pick an origin stop first.',
        destNone: 'No Vol\u00e1n destination is reachable from this origin.',
      },
      helyi: {
        stop: 'Origin stop',
        dest: 'Destination stop',
        hint: 'Pick a city, then an origin and a destination. Local services between them are listed.',
        originPh: 'Search stops in this city',
        destPh: 'Search among the stops reachable from the origin',
        empty: 'No stop in this city matches the search.',
        destEmpty: 'No direct service from this stop to that destination.',
        destNeedOrigin: 'Pick an origin stop first.',
        destNone: 'No destination is reachable from this origin.',
      },
    },
  },
};

/* Errors thrown deep in BkkLib carry a translation key so the display site can
   render them in the language the card is configured for. */
function codedError(key, arg) {
  const err = new Error(String(I18N.hu[key] instanceof Function ? I18N.hu[key](arg) : I18N.hu[key]));
  err.i18nKey = key;
  err.i18nArg = arg;
  return err;
}

function resolveLang(pref, hass) {
  if (pref === 'hu' || pref === 'en') return pref;
  const raw = (hass && hass.language)
    || (typeof navigator !== 'undefined' ? navigator.language : '')
    || 'hu';
  return String(raw).toLowerCase().startsWith('hu') ? 'hu' : 'en';
}

function t(lang, key, arg) {
  const dict = I18N[lang] || I18N.hu;
  const value = dict[key] !== undefined ? dict[key] : I18N.hu[key];
  return typeof value === 'function' ? value(arg) : value;
}

function modeCopy(lang, mode) {
  const dict = I18N[lang] || I18N.hu;
  return dict.mode[mode] || dict.mode.bkk;
}

function errText(lang, err) {
  if (err && err.i18nKey) return t(lang, err.i18nKey, err.i18nArg);
  return (err && err.message) ? err.message : String(err);
}

const BKK_HOP_TAG = 'hungarian-transport-card';
const BKK_HOP_EDITOR = 'hungarian-transport-card-editor';
const BKK_HOP_TAG_ALIAS = 'bkk-stop-card-r3';
const BKK_HOP_EDITOR_ALIAS = 'bkk-stop-card-r3-editor';
const BKK_PLANNER_EDITOR = 'hungarian-transit-stop-card-plan-editor';
const BKK_PLANNER_EDITOR_ALIAS = 'bkk-stop-card-plan-editor';
const OWN_CARD_TYPES = [BKK_HOP_TAG, BKK_HOP_TAG_ALIAS, BKK_PLANNER_TAG, BKK_PLANNER_TAG_ALIAS];
/* Highest compact-index layout this card understands; see scripts/gtfs_compact.py. */
const INDEX_SCHEMA = 27;

const BkkLib = {
  esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  },
  norm(name) {
    let t = String(name || '').normalize('NFC');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/[\u00A0\u202F]/g, ' ');
    t = t.trim().toLowerCase();
    t = t.replace(/[\u2013\u2014]/g, '-');
    t = t.replace(/\s+/g, ' ');
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.'].forEach((sfx) => {
      if (t.endsWith(sfx)) t = t.slice(0, -sfx.length).trim();
    });
    t = t.replace(/[,.;:\s]+$/g, '').trim();
    return t;
  },
  /* "Keleti pályaudvar M" (street) and "Keleti pályaudvar" (metro) are one place. */
  stationKey(name) {
    let t = String(name || '').normalize('NFC');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/[\u00A0\u202F]/g, ' ');
    t = t.trim().toLowerCase();
    t = t.replace(/[\u2013\u2014]/g, '-');
    t = t.replace(/\s+/g, ' ');
    if (/\sm$/i.test(t)) t = t.replace(/\sm$/i, '').trim();
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.'].forEach((sfx) => {
      if (t.endsWith(sfx)) t = t.slice(0, -sfx.length).trim();
    });
    t = t.replace(/[,.;:\s]+$/g, '').trim();
    return t;
  },
  nameEq(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    const na = BkkLib.norm(a);
    const nb = BkkLib.norm(b);
    if (na && nb && na === nb) return true;
    const sa = BkkLib.stationKey(a);
    const sb = BkkLib.stationKey(b);
    return !!(sa && sb && sa === sb);
  },
  fold(s) {
    return BkkLib.norm(s)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  },
  isStopArea(stop) {
    if (!stop) return false;
    if (stop.locationType === 1) return true;
    return /_CS/i.test(String(stop.id || ''));
  },
  groupStops(stops) {
    const byName = new Map();
    (stops || []).forEach((s) => {
      if (!s || !s.id || !s.name) return;
      const key = BkkLib.stationKey(s.name) || BkkLib.norm(s.name);
      if (!key) return;
      if (!byName.has(key)) byName.set(key, []);
      byName.get(key).push(s);
    });
    const hits = [];
    byName.forEach((group) => {
      const mavParent = group.find((s) => /^BKK_0055[0-9]+$/.test(String(s.id || '')));
      const volanArea = group.find((s) => (
        /^AREA_CS/i.test(String(s.id || '')) ||
        (/^volan_/i.test(String(s.id || '')) && s.locationType === 1)
      ));
      const area = mavParent || volanArea || group.find((s) => BkkLib.isStopArea(s));
      const withParent = group.find((s) => s.parentStationId);
      const named = group.find((s) => !BkkLib.isMetroSuffixName(s.name)) || group[0];
      const pick = area || withParent || named;
      hits.push({
        id: area ? area.id : (pick.parentStationId || pick.id),
        name: named.name,
      });
    });
    return hits;
  },
  destHitsForQuery(dests, q, extras) {
    const f = BkkLib.fold(q || '');
    const out = [];
    const seen = new Map();
    const add = (d) => {
      if (!d || !d.name) return;
      const key = d.key || BkkLib.stationKey(d.name) || BkkLib.norm(d.name);
      if (!key) return;
      const id = d.id || d.stopId || '';
      const prev = seen.get(key);
      if (prev) {
        if (BkkLib.elviraStationCode(id) && !BkkLib.elviraStationCode(prev.id)) prev.id = id;
        return;
      }
      const row = { key: key, name: d.name, id: id };
      seen.set(key, row);
      out.push(row);
    };
    (extras || []).forEach(add);
    (dests || []).forEach((d) => {
      if (f) {
        const dn = BkkLib.fold(d.name);
        if (dn.indexOf(f) < 0 && f.indexOf(dn) < 0) return;
      }
      add(d);
    });
    return out;
  },
  isMavRoute(rt) {
    const t = String((rt && rt.type) || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return false;
    return t === 'RAIL' || t === 'RAILWAY' || t === 'TRAIN';
  },
  isVolanRoute(rt) {
    if (!rt) return false;
    if (String(rt.type || '').toUpperCase() === 'COACH') return true;
    if (/^volan_/i.test(String(rt.id || ''))) return true;
    return /volan/i.test(String(rt.agencyId || ''));
  },
  isMavStop(stop) {
    if (!stop) return false;
    const t = String(stop.type || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return false;
    if (t === 'RAIL' || t === 'RAILWAY' || t === 'TRAIN') return true;
    return /^BKK_0055/.test(String(stop.id || ''));
  },
  isVolanStop(stop) {
    if (!stop) return false;
    const id = String(stop.id || '');
    if (/^volan_/i.test(id) || /^AREA_CS/i.test(id) || /^hkir_/i.test(id)) return true;
    if (String(stop.type || '').toUpperCase() === 'COACH') return true;
    return false;
  },
  /* BKK street stops next to a metro are named "… M". The hop card and
     commute sensors use the non-M stop (the metro / parent name). */
  isMetroSuffixName(name) {
    const n = String(name || '').normalize('NFC').replace(/\s+/g, ' ').trim();
    return /\sM$/i.test(n);
  },
  isBkkStop(stop) {
    if (!stop || BkkLib.isMavStop(stop) || BkkLib.isVolanStop(stop)) return false;
    const id = String(stop.id || '');
    if (/^AREA_/i.test(id) || /^STOP_/i.test(id)) return false;
    const t = String(stop.type || '').toUpperCase();
    if (t === 'COACH') return false;
    if (t === 'BUS' || t === 'TRAM' || t === 'SUBWAY' || t === 'TROLLEYBUS' ||
        t === 'FERRY' || t.indexOf('SUBURBAN') >= 0) return true;
    if (!/^BKK_/i.test(id)) return false;
    if (stop.locationType === 1) return true;
    return !t;
  },
  mode(cfg) {
    if (cfg && cfg.volan) return 'volan';
    if (cfg && cfg.helyi) return 'helyi';
    if (cfg && cfg.mav) return 'mav';
    return 'bkk';
  },
  routeMatchesMode(rt, mode) {
    if (mode === 'mav') return BkkLib.isMavRoute(rt);
    if (mode === 'volan') return BkkLib.isVolanRoute(rt);
    if (mode === 'all') return true;
    return !BkkLib.isMavRoute(rt) && !BkkLib.isVolanRoute(rt);
  },
  stopNum(id) {
    const s = String(id || '');
    const m = s.match(/^(?:volan_|AREA_CS|hkir_)(\d+)/i);
    if (m) return m[1];
    const n = s.match(/(\d{5,})/);
    return n ? n[1] : '';
  },
  elviraStationCode(id) {
    const m = String(id || '').match(/(\d{9})/);
    return m ? m[1] : '';
  },
  /* BKK street/metro parents (CSF…) sit next to MÁV stations but have no
     9-digit ELVIRA code. Map the well-known names; searchStops fills the rest. */
  elviraCodeFromName(name) {
    const f = BkkLib.fold(name);
    if (f === 'keleti' || f === 'budapest keleti') return '005510017';
    if (f === 'kelenfold' || f === 'budapest kelenfold') return '005501024';
    return '';
  },
  async elviraResolveCode(apiKey, id, name) {
    const direct = BkkLib.elviraStationCode(id);
    if (direct) return direct;
    const mapped = BkkLib.elviraCodeFromName(name);
    if (mapped) return mapped;
    const n = String(name || '').trim();
    if (!apiKey || !n) return '';
    const queries = [n];
    const key = BkkLib.stationKey(n);
    if (key && key !== n) queries.push(key);
    BkkLib.aliasQueries(n).forEach((q) => queries.push(q));
    const seen = new Set();
    for (let i = 0; i < queries.length; i++) {
      const q = queries[i];
      if (!q || seen.has(q)) continue;
      seen.add(q);
      try {
        const hits = await BkkLib.searchStops(apiKey, q, 'mav');
        for (let j = 0; j < hits.length; j++) {
          const c = BkkLib.elviraStationCode(hits[j].id);
          if (c) {
            return c;
          }
        }
      } catch (_e) { /* search is a fallback */ }
    }
    return '';
  },
  sameStop(id, originId) {
    if (!id || !originId) return false;
    if (id === originId) return true;
    const a = String(id);
    const b = String(originId);
    const va = BkkLib.stopNum(a);
    const vb = BkkLib.stopNum(b);
    if (va && vb && (/^(?:volan_|AREA_CS|hkir_)/i.test(a) || /^(?:volan_|AREA_CS|hkir_)/i.test(b))) {
      return va === vb;
    }
    const base = (s) => s.replace(/^BKK_/, '').split('_')[0];
    if (/^volan_/i.test(a) || /^volan_/i.test(b) || /^AREA_/i.test(a) || /^AREA_/i.test(b) || /^hkir_/i.test(a) || /^hkir_/i.test(b)) {
      return false;
    }
    return base(a) === base(b);
  },
  aliasQueries(q) {
    const f = BkkLib.fold(q);
    const extra = [];
    if (f.includes('arpad hid')) extra.push('Goncz Arpad varoskozpont');
    if (f.includes('moszkva')) extra.push('Szell Kalman ter');
    if (f === 'keleti' || f.indexOf('keleti ') === 0) extra.push('Budapest-Keleti');
    if (f === 'kelenfold' || f.indexOf('kelenfold ') === 0) extra.push('Budapest-Kelenfold');
    const key = BkkLib.stationKey(q);
    if (key && key !== q && extra.indexOf(key) < 0) extra.push(key);
    return extra;
  },
  labelAliased(q, hit) {
    const f = BkkLib.fold(q);
    const n = BkkLib.fold(hit.name);
    if (f.includes('arpad hid') && n === 'goncz arpad varoskozpont') {
      return 'G\u00f6ncz \u00c1rp\u00e1d v\u00e1rosk\u00f6zpont (M3, \u00c1rp\u00e1d h\u00edd)';
    }
    return hit.name;
  },
  _mem: {},
  _inflight: {},
  _gateActive: 0,
  _gateCards: 0,
  _gateWaiters: [],
  _gateFgWaiters: [],
  _boards: new Map(),
  _boardInflight: new Map(),
  /* One slot pair per card on the dashboard, between 4 and 24. */
  _gateLimit() {
    const cards = Math.max(1, BkkLib._gateCards || 1);
    return Math.min(24, Math.max(4, cards * 2));
  },
  /* Foreground calls are the departure boards. They jump ahead of trip-details
     so one card finishing does not hold up the other cards on the dashboard. */
  async _gate(foreground) {
    const busy = BkkLib._gateActive >= BkkLib._gateLimit();
    const fgWaiting = BkkLib._gateFgWaiters.length > 0;
    if (!busy && (foreground || !fgWaiting)) {
      BkkLib._gateActive += 1;
      return;
    }
    await new Promise((resolve) => {
      (foreground ? BkkLib._gateFgWaiters : BkkLib._gateWaiters).push(resolve);
    });
  },
  _ungate() {
    const next = BkkLib._gateFgWaiters.shift() || BkkLib._gateWaiters.shift();
    if (next) next();
    else BkkLib._gateActive = Math.max(0, BkkLib._gateActive - 1);
  },
  _cached(cache, key) {
    if (BkkLib._mem[key]) return BkkLib._mem[key];
    if (cache && cache[key]) {
      BkkLib._mem[key] = cache[key];
      return cache[key];
    }
    return undefined;
  },
  _store(cache, key, value) {
    BkkLib.cachePut(BkkLib._mem, key, value);
    if (cache && cache !== BkkLib._mem) BkkLib.cachePut(cache, key, value);
    return value;
  },
  async fetch(apiKey, path, params, foreground) {
    if (!apiKey) throw codedError('errNoApiKey');
    await BkkLib._gate(!!foreground);
    const q = new URLSearchParams(Object.assign({
      key: apiKey, version: '4', appVersion: 'apiary-1.0',
    }, params));
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 20000) : null;
    try {
      let res;
      try {
        res = await fetch(`${BKK_API}/${path}?${q}`, { signal: ctrl ? ctrl.signal : undefined });
      } catch (err) {
        throw new Error(err && err.name === 'AbortError' ? 'BKK timeout' : `BKK ${err}`);
      }
      if (!res.ok) throw new Error(`BKK HTTP ${res.status}`);
      const data = await res.json();
      if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
      return data;
    } finally {
      if (timer) clearTimeout(timer);
      BkkLib._ungate();
    }
  },
  /* Same stop and horizon is one request, shared by every card that needs it. */
  departureBoard(apiKey, stopId, horizon) {
    const key = String(stopId) + '|' + String(horizon);
    const hit = BkkLib._boards.get(key);
    if (hit && Date.now() - hit.ts < 20000) return Promise.resolve(hit.data);
    const inflight = BkkLib._boardInflight.get(key);
    if (inflight) return inflight;
    const run = BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
      stopId: String(stopId),
      minutesAfter: String(horizon),
      minutesBefore: '0',
      onlyDepartures: 'true',
      includeReferences: 'true',
      includeVehicleFromTrip: 'true',
    }, true).then((data) => {
      BkkLib._boards.set(key, { ts: Date.now(), data });
      return data;
    }).finally(() => {
      BkkLib._boardInflight.delete(key);
    });
    BkkLib._boardInflight.set(key, run);
    return run;
  },
  boardSpanMin(data, nowSec) {
    const times = ((((data || {}).data || {}).entry) || {}).stopTimes || [];
    let last = 0;
    times.forEach((st) => {
      const dep = st.predictedDepartureTime || st.departureTime || 0;
      if (dep > last) last = dep;
    });
    if (!last) return { count: times.length, spanMin: 0 };
    return { count: times.length, spanMin: (last - nowSec) / 60 };
  },
  planPlaceVertex(name, id) {
    const label = String(name || '').replace(/::/g, ' ').trim();
    const raw = String(id || '');
    const m = raw.match(/^BKK_(.+)$/i);
    if (m && label) return `${label}::BKK:${m[1]}`;
    return '';
  },
  async planPlace(apiKey, stop) {
    const name = (stop && stop.name) || '';
    const id = (stop && stop.id) || '';
    const vertex = BkkLib.planPlaceVertex(name, id);
    if (vertex) return vertex;
    const lat = Number(stop && stop.lat);
    const lon = Number(stop && stop.lon);
    if (name && Number.isFinite(lat) && Number.isFinite(lon)) return `${name}::${lat},${lon}`;
    if (!apiKey || !name) return '';
    try {
      const data = await BkkLib.fetch(apiKey, 'search.json', { query: name }, true);
      const stops = (((data.data || {}).references) || {}).stops || {};
      const want = BkkLib.fold(name);
      let best = null;
      Object.values(stops).forEach((s) => {
        if (!s || s.lat == null || s.lon == null) return;
        if (id && s.id === id) best = s;
        else if (!best && BkkLib.fold(s.name) === want) best = s;
      });
      if (!best) return '';
      return `${String(name).replace(/::/g, ' ')}::${best.lat},${best.lon}`;
    } catch (_e) {
      return '';
    }
  },
  /* FUTÁR plan-trip: itinerary duration and walkTime are seconds.
     Each leg duration is milliseconds. */
  journeyFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    if (!its.length) return null;
    let best = its[0];
    its.forEach((it) => {
      if (Number(it.duration || 1e15) < Number(best.duration || 1e15)) best = it;
    });
    const legs = (best.legs || []).map((leg) => {
      const sec = Math.round(Number(leg.duration || 0) / 1000);
      const walk = String(leg.mode || '').toUpperCase() === 'WALK';
      return {
        walk,
        minutes: sec <= 0 ? 0 : Math.max(1, Math.round(sec / 60)),
        meters: Math.max(0, Math.round(Number(leg.distance || 0))),
        label: String(leg.routeShortName || ''),
        headsign: String(leg.headsign || ''),
        color: String(leg.routeColor || '').replace(/#/g, ''),
        text: String(leg.routeTextColor || '').replace(/#/g, ''),
        from: String((leg.from || {}).name || ''),
        to: String((leg.to || {}).name || ''),
      };
    }).filter((leg) => leg.minutes > 0);
    if (!legs.length) return null;
    return {
      durationMin: Math.max(1, Math.round(Number(best.duration || 0) / 60)),
      walkMin: Math.max(0, Math.round(Number(best.walkTime || 0) / 60)),
      transfers: Math.max(0, Number(best.transfers || 0)),
      legs,
    };
  },
  async planJourney(apiKey, origin, dest) {
    if (!apiKey) return null;
    const fromPlace = await BkkLib.planPlace(apiKey, origin);
    const toPlace = await BkkLib.planPlace(apiKey, dest);
    if (!fromPlace || !toPlace) return null;
    try {
      const data = await BkkLib.fetch(apiKey, 'plan-trip.json', {
        fromPlace,
        toPlace,
        mode: 'TRANSIT,WALK',
        numItineraries: '5',
      }, true);
      return BkkLib.journeyFromPlan(data);
    } catch (_e) {
      return null;
    }
  },
  async probeApiKey(apiKey) {
    if (!apiKey) return { ok: false, code: 'errNoApiKey' };
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
  hm(ts) {
    if (!ts) return '';
    const f = tzFields(new Date(ts * 1000));
    return `${pad2(f.h)}:${pad2(f.mi)}`;
  },
  vehicleKind(rt) {
    if (BkkLib.isMavRoute(rt)) return 'rail';
    const t = String((rt && rt.type) || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return 'rail';
    if (t.indexOf('TRAM') >= 0) return 'tram';
    if (t.indexOf('SUBWAY') >= 0 || t.indexOf('METRO') >= 0) return 'subway';
    if (t.indexOf('TROLLEY') >= 0) return 'trolleybus';
    if (t === 'COACH' || BkkLib.isVolanRoute(rt)) return 'coach';
    return 'bus';
  },
  vehicleLoc(veh) {
    if (!veh || typeof veh !== 'object') return { lat: NaN, lon: NaN };
    const loc = veh.location || {};
    const lat = Number(loc.lat != null ? loc.lat : veh.latitude);
    const lon = Number(loc.lon != null ? loc.lon : (veh.longitude != null ? veh.longitude : veh.lng));
    return { lat: lat, lon: lon };
  },
  unixSec(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return Date.now() / 1000;
    return n > 1e12 ? n / 1000 : n;
  },
  decodePolyline(encoded) {
    if (encoded && typeof encoded === 'object') encoded = encoded.points || '';
    if (!encoded || typeof encoded !== 'string') return [];
    const coords = [];
    let index = 0;
    let lat = 0;
    let lng = 0;
    try {
      while (index < encoded.length) {
        for (let xy = 0; xy < 2; xy++) {
          let result = 0;
          let shift = 0;
          let byte;
          do {
            byte = encoded.charCodeAt(index++) - 63;
            result |= (byte & 0x1f) << shift;
            shift += 5;
          } while (byte >= 0x20);
          const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
          if (xy === 0) lat += delta;
          else lng += delta;
        }
        coords.push([lat / 1e5, lng / 1e5]);
      }
    } catch (_e) {
      return [];
    }
    if (coords.length <= 400) return coords;
    const out = [];
    const step = (coords.length - 1) / 399;
    for (let i = 0; i < 400; i++) {
      const idx = Math.round(i * step);
      const pt = coords[Math.min(idx, coords.length - 1)];
      if (!out.length || out[out.length - 1][0] !== pt[0] || out[out.length - 1][1] !== pt[1]) {
        out.push(pt);
      }
    }
    const last = coords[coords.length - 1];
    if (out[out.length - 1][0] !== last[0] || out[out.length - 1][1] !== last[1]) out.push(last);
    return out;
  },
  haversineMeters(a, b) {
    const toRad = (d) => (d * Math.PI) / 180;
    const R = 6371000;
    const dLat = toRad(b[0] - a[0]);
    const dLon = toRad(b[1] - a[1]);
    const lat1 = toRad(a[0]);
    const lat2 = toRad(b[0]);
    const h = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  },
  pointAlongShape(shape, targetMeters) {
    if (!Array.isArray(shape) || shape.length < 1) return null;
    if (shape.length === 1 || !(targetMeters > 0)) return shape[0];
    const cum = [0];
    for (let i = 1; i < shape.length; i++) {
      cum.push(cum[i - 1] + BkkLib.haversineMeters(shape[i - 1], shape[i]));
    }
    const total = cum[cum.length - 1];
    if (!(total > 0)) return shape[0];
    const tval = Math.max(0, Math.min(total, Number(targetMeters) || 0));
    let i = 1;
    while (i < cum.length && cum[i] < tval) i += 1;
    const i1 = Math.min(i, shape.length - 1);
    const i0 = Math.max(0, i1 - 1);
    const seg = cum[i1] - cum[i0];
    if (!(seg > 0)) return shape[i1];
    const f = (tval - cum[i0]) / seg;
    return [
      shape[i0][0] + (shape[i1][0] - shape[i0][0]) * f,
      shape[i0][1] + (shape[i1][1] - shape[i0][1]) * f,
    ];
  },
  estimatePositionFromSchedule(shape, stopTimes, nowSec) {
    if (!Array.isArray(shape) || shape.length < 2 || !Array.isArray(stopTimes) || stopTimes.length < 1) {
      return null;
    }
    const stops = stopTimes.map((st) => {
      const tval = st.predictedDepartureTime != null ? st.predictedDepartureTime
        : (st.predictedArrivalTime != null ? st.predictedArrivalTime
          : (st.departureTime != null ? st.departureTime : st.arrivalTime));
      const dist = st.shapeDistTraveled != null ? Number(st.shapeDistTraveled) : NaN;
      return {
        t: tval != null ? Number(tval) : NaN,
        dist: Number.isFinite(dist) ? dist : NaN,
      };
    }).filter((s) => Number.isFinite(s.t)).sort((a, b) => a.t - b.t);
    if (!stops.length) return null;
    const now = Number(nowSec);
    if (!Number.isFinite(now)) return null;
    let targetDist;
    if (now <= stops[0].t) {
      targetDist = Number.isFinite(stops[0].dist) ? stops[0].dist : 0;
    } else if (now >= stops[stops.length - 1].t) {
      const last = stops[stops.length - 1];
      if (Number.isFinite(last.dist)) targetDist = last.dist;
      else return shape[shape.length - 1];
    } else {
      let i = 0;
      while (i < stops.length - 1 && stops[i + 1].t < now) i += 1;
      const a = stops[i];
      const b = stops[i + 1];
      const span = b.t - a.t;
      const f = span > 0 ? (now - a.t) / span : 0;
      if (Number.isFinite(a.dist) && Number.isFinite(b.dist)) {
        targetDist = a.dist + (b.dist - a.dist) * f;
      } else {
        const i0 = Math.floor((i / Math.max(1, stops.length - 1)) * (shape.length - 1));
        const i1 = Math.floor(((i + 1) / Math.max(1, stops.length - 1)) * (shape.length - 1));
        const p0 = shape[Math.max(0, Math.min(shape.length - 1, i0))];
        const p1 = shape[Math.max(0, Math.min(shape.length - 1, i1))];
        return [
          p0[0] + (p1[0] - p0[0]) * f,
          p0[1] + (p1[1] - p0[1]) * f,
        ];
      }
    }
    const maxOfficial = Math.max(
      ...stops.map((s) => (Number.isFinite(s.dist) ? s.dist : 0)),
      targetDist || 0,
    );
    let polyLen = 0;
    for (let i = 1; i < shape.length; i++) {
      polyLen += BkkLib.haversineMeters(shape[i - 1], shape[i]);
    }
    const scaled = maxOfficial > 0 ? (targetDist / maxOfficial) * polyLen : targetDist;
    return BkkLib.pointAlongShape(shape, scaled);
  },
  /* Only a key that sits on one of our own cards may be reused. A dashboard can
     hold apiKey values belonging to entirely unrelated cards, and those must
     never end up in a request to BKK. */
  findApiKey(node, acc) {
    if (!acc) acc = [];
    if (!node || typeof node !== 'object') return acc;
    if (Array.isArray(node)) {
      node.forEach((x) => BkkLib.findApiKey(x, acc));
      return acc;
    }
    const type = String(node.type || '').replace(/^custom:/, '');
    if (typeof node.apiKey === 'string' && node.apiKey && OWN_CARD_TYPES.indexOf(type) >= 0) {
      acc.push(node.apiKey);
    }
    Object.keys(node).forEach((k) => {
      if (k === 'apiKey') return;
      BkkLib.findApiKey(node[k], acc);
    });
    return acc;
  },
  /* A dashboard usually holds several of these cards. Reuse the key already
     configured on a sibling card instead of asking for it again. Only the
     default dashboard and the one being viewed are inspected. */
  async apiKeyFromDashboard(hass) {
    if (!hass || !hass.connection) return '';
    const paths = [null];
    if (hass.panelUrl && paths.indexOf(hass.panelUrl) < 0) paths.push(hass.panelUrl);
    for (let i = 0; i < paths.length; i++) {
      try {
        const msg = { type: 'lovelace/config' };
        if (paths[i]) msg.url_path = paths[i];
        const cfg = await hass.connection.sendMessagePromise(msg);
        const keys = BkkLib.findApiKey(cfg);
        if (keys.length) return keys[0];
      } catch (_e) { /* not a Lovelace dashboard, or no access */ }
    }
    return '';
  },
  /* Cached per resolved URL, so two cards pointing at different index files do
     not serve each other's data. The URL carries a daily cache-buster, which
     also expires the entry on a dashboard left open overnight. */
  _idx: new Map(),
  _indexCached(url, httpKey, gunzipKey) {
    const cache = BkkLib._idx;
    const hit = cache.get(url);
    if (hit) return hit;
    const p = BkkLib._loadGtfsIndex(url, httpKey, gunzipKey).catch((err) => {
      cache.delete(url);
      throw err;
    });
    cache.set(url, p);
    while (cache.size > 4) cache.delete(cache.keys().next().value);
    return p;
  },
  volanIndex() {
    return BkkLib._indexCached(volanIndexUrl(), 'errVolanHttp', 'errVolanGunzip');
  },
  cityIndex() {
    return BkkLib._indexCached(cityIndexUrl(), 'errCityHttp', 'errCityGunzip');
  },
  async _loadGtfsIndex(url, httpKey, gunzipKey) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 45000) : null;
    let res;
    try {
      res = await fetch(url, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
    } catch (_err) {
      throw codedError('errIndexFetch');
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!res.ok) throw codedError(httpKey, res.status);
    const buf = await res.arrayBuffer();
    const u8 = new Uint8Array(buf);
    let text = '';
    if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
      if (typeof DecompressionStream !== 'function') throw codedError(gunzipKey);
      const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
      text = await new Response(stream).text();
    } else {
      text = new TextDecoder().decode(u8);
    }
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
    idx.stops = (idx.s || []).map((row, i) => ({
      i,
      id: row[0],
      name: row[1],
      num: String(row[2] || ''),
      op: row.length > 3 ? Number(row[3]) : 0,
      key: BkkLib.norm(row[1]),
      fold: BkkLib.fold(row[1]),
    }));
    idx.ops = (idx.o || []).map((row, i) => ({ i, id: row[0], name: row[1] }));
    idx.byNum = new Map();
    idx.byKey = new Map();
    idx.stops.forEach((st) => {
      const nk = st.op + ':' + st.num;
      const kk = st.op + ':' + st.key;
      if (st.num && !idx.byNum.has(nk)) idx.byNum.set(nk, st);
      if (st.key && !idx.byKey.has(kk)) idx.byKey.set(kk, st);
    });
    return idx;
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
  /* One GTFS service day. `mins` is the earliest departure minute still worth
     looking at: for yesterday's service day that is now + 24h, which is how
     after-midnight (24:xx) trips stay visible. */
  volanDay(offset) {
    const off = offset || 0;
    const now = tzParts(new Date());
    const ymd = tzShiftYmd(now.ymd, off);
    let mins = 0;
    if (off === 0) mins = now.mins;
    else if (off < 0) mins = now.mins - off * 1440;
    return {
      ymd,
      dow: tzDow(ymd),
      mins,
      midnight: tzEpochSec(ymd, 0),
    };
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
    const hit = (idx.stops || []).find((s) => s.id === id);
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
  volanDestHit(st, destKey, destFold) {
    if (!st) return false;
    if (destKey && (st.key === destKey || st.fold === destFold)) return true;
    const fk = destFold || BkkLib.fold(destKey || '');
    if (fk && fk.length >= 4 && st.fold.indexOf(fk) >= 0) return true;
    return false;
  },
  async volanSearchStops(q) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsSearchStops(idx, q, null);
  },
  async volanRoutesAtStop(stop) {
    const idx = await BkkLib.volanIndex();
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
    const idx = await BkkLib.volanIndex();
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
    const idx = await BkkLib.cityIndex();
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
    return hits.slice(0, 20).map((s) => ({
      id: s.id,
      name: s.name,
      label: s.name,
      type: 'BUS',
    }));
  },
  async volanReachableDests(stop) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsReachableDests(idx, stop, null);
  },
  async cityReachableDests(stop, city) {
    if (!city) return { dests: [], destRoutes: {} };
    const idx = await BkkLib.cityIndex();
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
        if (!byKey.has(st.key)) byKey.set(st.key, { key: st.key, name: st.name, id: st.id });
        if (!destRoutes[st.key]) destRoutes[st.key] = [];
        if (destRoutes[st.key].indexOf(route) < 0) destRoutes[st.key].push(route);
      }
    }
    const dests = Array.from(byKey.values());
    dests.sort((a, b) => a.name.localeCompare(b.name, 'hu'));
    return { dests, destRoutes };
  },
  async volanDepartures(stopId, dest, originName, minutesAfter) {
    const idx = await BkkLib.volanIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, null, '#F9AB13', '#000000', 'coach', 'COACH', minutesAfter);
  },
  async cityDepartures(stopId, dest, originName, city, minutesAfter) {
    if (!city) return [];
    const idx = await BkkLib.cityIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, BkkLib.opIx(idx, city), '#0F6FC6', '#ffffff', 'bus', 'BUS', minutesAfter);
  },
  gtfsDepartures(idx, stopId, dest, originName, opIx, color, text, vehicle, rawType, minutesAfter) {
    const origin = BkkLib.volanMatchStop(idx, { id: stopId, name: originName || '' }, opIx == null ? null : opIx);
    if (!origin) return [];
    const indexes = opIx == null
      ? [origin.i]
      : BkkLib.cityStopIndexes(idx, { id: stopId, name: originName || '' }, opIx);
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold((dest && dest.name) || destKey || '');
    const horizon = clampMinutesAfter(minutesAfter);
    const rowCap = maxDepartureRows(horizon);
    const rows = [];
    const trips = idx.t || [];
    const todayMins = BkkLib.volanDay(0).mins;
    /* -1 catches yesterday's 24:xx trips, which are still tonight's buses. */
    const dayOffsets = [-1, 0, 1];
    for (let n = 0; n < dayOffsets.length; n++) {
      const day = BkkLib.volanDay(dayOffsets[n]);
      let earliest = day.mins - 1;
      let latest = day.mins + horizon;
      if (dayOffsets[n] > 0) {
        const remain = horizon - (1440 - todayMins);
        if (remain <= 0) continue;
        earliest = -1;
        latest = remain;
      }
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
            if (BkkLib.volanDestHit(idx.stops[ps[j]], destKey, destFold)) {
              didx = j;
              break;
            }
          }
          if (didx < 0) continue;
        }
        const destStop = didx >= 0 ? idx.stops[ps[didx]] : idx.stops[ps[ps.length - 1]];
        const arrMin = didx >= 0 ? ms[didx] : null;
        const travel = (arrMin != null && arrMin >= depMin && arrMin - depMin <= 1440) ? (arrMin - depMin) : null;
        const depTs = day.midnight + depMin * 60;
        if (!departureStillDue(depTs)) continue;
        rows.push({
          tripId: 'gtfs:' + route + ':' + day.ymd + ':' + depMin + ':' + origin.i,
          label: route,
          color: color,
          text: text,
          vehicle: vehicle,
          rawType: rawType,
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
  mergeVolanRows(gtfs, otp, limit) {
    const out = [];
    const byTime = new Map();
    const byNum = new Map();
    const generic = (c) => {
      const x = String(c || '').replace('#', '').toLowerCase();
      return !x || x === '4477aa';
    };
    const timeOf = (row) => `${String(row.label || '').toUpperCase()}|${Math.round(Number(row.sched || row.dep || 0) / 60)}`;
    const overlay = (prev, row) => {
      if (!prev || !row) return;
      if (generic(prev.color) && row.color && !generic(row.color)) {
        prev.color = row.color;
        if (row.text) prev.text = row.text;
      }
      const prevHasGps = Number.isFinite(Number(prev.lat)) && Number.isFinite(Number(prev.lon));
      if (!prevHasGps) {
        const lat = Number(row.lat);
        const lon = Number(row.lon);
        if (Number.isFinite(lat) && Number.isFinite(lon)) {
          prev.lat = lat;
          prev.lon = lon;
        }
      }
      const tid = String(row.tripId || '');
      if (tid && !/^(elvira:|gtfs:)/.test(tid) && !prevHasGps) prev.tripId = tid;
      ['vehicleId', 'licensePlate', 'model', 'vehicleStatus', 'stopDistancePercent'].forEach((k) => {
        if ((prev[k] == null || prev[k] === '') && row[k] != null && row[k] !== '') prev[k] = row[k];
      });
    };
    const add = (row) => {
      if (!row || !departureStillDue(row.dep || row.sched)) return;
      const n = BkkLib.rowTrainNumber(row);
      if (n && byNum.has(n)) {
        overlay(out[byNum.get(n)], row);
        return;
      }
      const tk = timeOf(row);
      if (byTime.has(tk)) {
        overlay(out[byTime.get(tk)], row);
        return;
      }
      const idx = out.length;
      out.push(row);
      byTime.set(tk, idx);
      if (n) byNum.set(n, idx);
    };
    (otp || []).forEach(add);
    (gtfs || []).forEach(add);
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    const cap = Number(limit);
    return out.slice(0, (Number.isFinite(cap) && cap > 0) ? cap : 12);
  },
  elviraRowsFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res)) return res;
    if (Array.isArray(res.departures)) return res.departures;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.departures)) return payload.departures;
    if (Array.isArray(payload)) return payload;
    return [];
  },
  async elviraBetween(hass, originId, destId, minutesAfter, destName, opts) {
    opts = opts || {};
    let origin = BkkLib.elviraStationCode(originId);
    let dest = BkkLib.elviraStationCode(destId);
    if (!origin) origin = await BkkLib.elviraResolveCode(opts.apiKey, originId, opts.originName || '');
    if (!dest) dest = await BkkLib.elviraResolveCode(opts.apiKey, destId, destName || '');
    if (!hass || !origin || !dest || origin === dest) {
      return [];
    }
    const serviceData = {
      origin_code: origin,
      dest_code: dest,
      minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
      dest_name: destName || '',
    };
    try {
      let res;
      if (typeof hass.callService === 'function') {
        try {
          res = await hass.callService('bkk_stop', 'elvira_between', serviceData, undefined, false, true);
        } catch (err) {
          if (!hass.connection) throw err;
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'bkk_stop',
            service: 'elvira_between',
            service_data: serviceData,
            return_response: true,
          });
        }
      } else if (hass.connection) {
        res = await hass.connection.sendMessagePromise({
          type: 'call_service',
          domain: 'bkk_stop',
          service: 'elvira_between',
          service_data: serviceData,
          return_response: true,
        });
      } else {
        return [];
      }
      const rows = BkkLib.elviraRowsFromResult(res);
      return Array.isArray(rows) ? rows : [];
    } catch (err) {
      return [];
    }
  },
  coachVehiclesFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res.vehicles)) return res.vehicles;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.vehicles)) return payload.vehicles;
    return [];
  },
  _coachPosInflight: null,
  _coachPosCache: null,
  async coachPositions(hass) {
    if (!hass) return [];
    const now = Date.now();
    const hit = BkkLib._coachPosCache;
    if (hit && now - hit.ts < 20000) return hit.vehicles;
    if (BkkLib._coachPosInflight) return BkkLib._coachPosInflight;
    const run = (async () => {
      try {
        let res;
        if (typeof hass.callService === 'function') {
          try {
            res = await hass.callService('bkk_stop', 'coach_positions', {}, undefined, false, true);
          } catch (err) {
            if (!hass.connection) throw err;
            res = await hass.connection.sendMessagePromise({
              type: 'call_service',
              domain: 'bkk_stop',
              service: 'coach_positions',
              service_data: {},
              return_response: true,
            });
          }
        } else if (hass.connection) {
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'bkk_stop',
            service: 'coach_positions',
            service_data: {},
            return_response: true,
          });
        } else {
          return [];
        }
        const vehicles = BkkLib.coachVehiclesFromResult(res);
        const list = Array.isArray(vehicles) ? vehicles : [];
        BkkLib._coachPosCache = { ts: Date.now(), vehicles: list };
        return list;
      } catch (err) {
        return [];
      } finally {
        BkkLib._coachPosInflight = null;
      }
    })();
    BkkLib._coachPosInflight = run;
    return run;
  },
  async coachShape(hass, route, headsign) {
    if (!hass || !route) return '';
    const serviceData = { route: String(route), headsign: String(headsign || '') };
    try {
      let res;
      if (typeof hass.callService === 'function') {
        try {
          res = await hass.callService('bkk_stop', 'coach_shape', serviceData, undefined, false, true);
        } catch (err) {
          if (!hass.connection) throw err;
          res = await hass.connection.sendMessagePromise({
            type: 'call_service',
            domain: 'bkk_stop',
            service: 'coach_shape',
            service_data: serviceData,
            return_response: true,
          });
        }
      } else if (hass.connection) {
        res = await hass.connection.sendMessagePromise({
          type: 'call_service',
          domain: 'bkk_stop',
          service: 'coach_shape',
          service_data: serviceData,
          return_response: true,
        });
      } else {
        return '';
      }
      const payload = (res && (res.response || res.service_response)) || res || {};
      return String(payload.points || '');
    } catch (err) {
      return '';
    }
  },
  async searchStops(apiKey, q, mode, city) {
    const byId = new Map();
    if (mode === 'volan' || mode === 'all') {
      try {
        (await BkkLib.volanSearchStops(q)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
    }
    if (mode === 'all') {
      try {
        const idx = await BkkLib.cityIndex();
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
        (await BkkLib.citySearchStops(q, city)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
      return BkkLib.groupStops(Array.from(byId.values())).slice(0, 20);
    }
    if (apiKey) {
      try {
        const queries = [q].concat(BkkLib.aliasQueries(q));
        for (let i = 0; i < queries.length; i++) {
          const data = await BkkLib.fetch(apiKey, 'search.json', { query: queries[i] }, true);
          const stops = (((data.data || {}).references) || {}).stops || {};
          Object.values(stops).forEach((s) => {
            if (!s || !s.id || !s.name) return;
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
      } catch (err) {
        if ((mode !== 'volan' && mode !== 'all') || !byId.size) throw err;
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
        const n = BkkLib.stationKey(raw) || BkkLib.norm(raw);
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
  async tripGoesTo(apiKey, cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    try {
      const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
      const oidx = BkkLib.originIndex(sts, stops, origin);
      if (oidx < 0) return false;
      return sts.slice(oidx + 1).some((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
    } catch (_e) {
      return false;
    }
  },
  async tripRemainingNames(apiKey, cache, tripId, origin) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return [];
    const names = [];
    const seen = new Set();
    sts.slice(oidx + 1).forEach((s) => {
      const raw = (stops[s.stopId] || {}).name;
      const n = BkkLib.stationKey(raw) || BkkLib.norm(raw);
      if (!raw || !n || seen.has(n)) return;
      seen.add(n);
      names.push({ key: n, name: raw, id: s.stopId });
    });
    return names;
  },
  async reachableDests(apiKey, cache, stop, mode, city) {
    if (mode === 'helyi') {
      return BkkLib.cityReachableDests(stop, city);
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
        out = BkkLib.mergeDestIndex(await BkkLib.volanReachableDests(stop), out);
      } catch (_e) { /* GTFS index optional */ }
    }
    if (mode === 'all') {
      try {
        const idx = await BkkLib.cityIndex();
        const cityId = BkkLib.cityOpId(idx, stop);
        if (cityId) out = BkkLib.mergeDestIndex(await BkkLib.cityReachableDests(stop, cityId), out);
      } catch (_e) { /* city index optional */ }
    }
    return out;
  },
  async travelMin(apiKey, tripId, destKey, dep, origin, cache) {
    if (!tripId || !destKey || String(tripId).indexOf('gtfs:') === 0
        || String(tripId).indexOf('elvira:') === 0) return null;
    try {
      const { sts, stops } = await BkkLib.tripDetails(apiKey, cache || {}, tripId);
      const oidx = BkkLib.originIndex(sts, stops, origin);
      const start = oidx >= 0 ? oidx + 1 : 0;
      const dest = sts.slice(start).find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
      if (!dest) return null;
      const arr = dest.predictedArrivalTime || dest.arrivalTime || dest.departureTime;
      if (!arr || !dep) return null;
      const mins = Math.round((arr - dep) / 60);
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
      const cityRows = await BkkLib.cityDepartures(stopId, dest, opts.originName, opts.city, horizon);
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
    if ((mode === 'mav' || mode === 'all') && opts.hass && !opts.skipElvira) {
      try {
        const destId = (dest && dest.id) || opts.destStopId || '';
        const extra = await BkkLib.elviraBetween(
          opts.hass,
          stopId,
          destId,
          horizon,
          (dest && dest.name) || '',
          { apiKey: apiKey, originName: opts.originName || '' },
        );
        rows = BkkLib.mergeVolanRows(rows, extra, maxRows);
      } catch (_e) { /* ELVIRA optional; FUTÁR/GTFS rows still show */ }
    }
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
      if (!poles.length) poles = [stopId];
      const loadBoards = (ids) => Promise.all(ids.map(async (pid) => {
        try {
          return await BkkLib.departureBoard(apiKey, pid, horizon);
        } catch (_e) {
          return null;
        }
      }));
      let payloads = await loadBoards(poles);
      const usedParentOnly = poles.length === 1 && poles[0] === stopId;
      const span = payloads.reduce((best, data) => {
        const cur = BkkLib.boardSpanMin(data, now);
        return {
          count: best.count + cur.count,
          spanMin: Math.max(best.spanMin, cur.spanMin),
        };
      }, { count: 0, spanMin: 0 });
      const capped = usedParentOnly && (span.count >= 60 || span.spanMin + 2 < horizon);
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
        if (discovered.length) {
          let extra = [];
          try {
            extra = await BkkLib.originPoles(apiKey, cache, origin, discovered, destKey || '');
          } catch (_e) {
            extra = [];
          }
          const fresh = extra.filter((id) => poles.indexOf(id) < 0);
          if (fresh.length) {
            poles = poles.concat(fresh);
            payloads = payloads.concat(await loadBoards(fresh));
          }
        }
      }
      const seenTrip = new Set();
      payloads.forEach((data) => {
        if (!data) return;
        const refs = (data.data || {}).references || {};
        const routes = refs.routes || {};
        const trips = refs.trips || {};
        const times = ((data.data || {}).entry || {}).stopTimes || [];
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
          candidates.push({
            tripId: st.tripId,
            label: rt.iconDisplayText || rt.shortName || '?',
            color: '#' + String(rt.color || fallbackColor).replace('#', ''),
            text: '#' + String(rt.textColor || fallbackText).replace('#', ''),
            vehicle: BkkLib.vehicleKind(rt),
            rawType: String(rt.type || 'BUS').toUpperCase(),
            dep: dep,
            sched: st.departureTime,
            delay: (st.predictedDepartureTime && st.departureTime)
              ? (st.predictedDepartureTime - st.departureTime) : 0,
            head: trip.tripHeadsign || st.stopHeadsign || '',
            platform: (stops[st.stopId] || {}).platformCode || '',
            wheelchair: trip.wheelchairAccessible === 1
              || trip.wheelchairAccessible === true
              || st.wheelchairAccessible === true,
            bikesAllowed: trip.bikesAllowed === 1 || trip.bikesAllowed === true,
            trainNumber: trip.shortName || trip.tripShortName
              || BkkLib.trainNumberFromTripId(st.tripId) || '',
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
      candidates.forEach((row) => {
        if (headHitsDest(row)) sure.push(row);
        else maybe.push(row);
      });
      sure = sure.slice(0, maxRows);
    } else {
      sure = candidates.slice(0, maxRows);
    }
    const asFutar = (list) => list.map((row) => Object.assign({ travel: null }, row));
    const early = asFutar(sure);
    if (destKey && maybe.length && sure.length < maxRows && typeof opts.armThroughCheck === 'function') {
      opts.armThroughCheck(() => {
        const pending = (async () => {
          const picked = sure.slice();
          for (let i = 0; i < maybe.length && picked.length < maxRows; i += 16) {
            const chunk = maybe.slice(i, i + 16);
            const flags = await Promise.all(chunk.map((row) => (
              BkkLib.tripGoesTo(apiKey, cache, row.tripId, origin, destKey)
            )));
            flags.forEach((ok, j) => { if (ok) picked.push(chunk[j]); });
          }
          const full = asFutar(picked.slice(0, maxRows));
          if (typeof opts.onFutarUpdate === 'function') opts.onFutarUpdate(full);
        })();
        pending.catch(() => {});
      });
    }
    return early;
    })() : Promise.resolve([]);
    const gtfsTask = (async () => {
      let extraVolan = [];
      let extraCity = [];
      if (mode === 'volan' || mode === 'all') {
        try {
          extraVolan = await BkkLib.volanDepartures(stopId, dest, opts.originName, horizon);
          if (mode === 'all' && selected.size) {
            extraVolan = extraVolan.filter((row) => selected.has('gtfs:' + row.label));
          }
        } catch (err) {
          if (mode !== 'all') throw err;
        }
      }
      if (mode === 'all') {
        try {
          const idx = await BkkLib.cityIndex();
          const cityId = BkkLib.cityOpId(idx, { id: stopId, name: opts.originName || '' });
          if (cityId) {
            extraCity = await BkkLib.cityDepartures(stopId, dest, opts.originName, cityId, horizon);
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

  trainNumberFromTripId(tripId) {
    const m = String(tripId || '').match(/^BKK_(\d+)(?:_|$)/);
    return m ? m[1] : '';
  },
  isFutarTripId(tripId) {
    const t = String(tripId || '');
    return !!t && !/^(elvira:|gtfs:)/.test(t);
  },
  emmaTrainNumber(text) {
    const m = String(text || '').trim().match(/^(\d+)/);
    if (!m) return '';
    return m[1].replace(/^0+/, '') || m[1];
  },
  matchEmmaVehicle(row, vehicle) {
    if (!row || !vehicle) return false;
    const num = BkkLib.rowTrainNumber(row);
    const trip = vehicle.trip || {};
    const n = BkkLib.emmaTrainNumber(trip.tripShortName || vehicle.label || '');
    if (num && n && num === n) return true;
    const name = String(row.label || '').trim().toUpperCase();
    if (!name || name.length < 3 || BkkLib.genericRailLabel(name)) return false;
    const blob = [
      trip.tripShortName, trip.tripHeadsign,
      (trip.route || {}).longName, (trip.route || {}).shortName, vehicle.label,
    ].join(' ').toUpperCase();
    if (blob.indexOf(name) < 0) return false;
    return !num || !n || num === n;
  },
  applyEmmaPositions(rows, vehicles) {
    const list = Array.isArray(rows) ? rows : [];
    const vehs = Array.isArray(vehicles) ? vehicles : [];
    list.forEach((row) => {
      if (!row) return;
      if (Number.isFinite(Number(row.lat)) && Number.isFinite(Number(row.lon))) return;
      for (let i = 0; i < vehs.length; i++) {
        const v = vehs[i];
        if (!BkkLib.matchEmmaVehicle(row, v)) continue;
        const lat = Number(v.lat);
        const lon = Number(v.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        row.lat = lat;
        row.lon = lon;
        if (v.heading != null) row.heading = v.heading;
        if (v.speed != null) row.speed = v.speed;
        break;
      }
    });
    return list;
  },
  needsFutarMapResolve(row) {
    const lat = Number(row && row.lat);
    const lon = Number(row && row.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return false;
    return !BkkLib.isFutarTripId((row && row.tripId) || '');
  },
  coachRouteKeys(route) {
    const raw = String(route || '').trim().toUpperCase();
    const keys = [];
    const add = (value) => {
      const key = String(value || '');
      if (key && keys.indexOf(key) < 0) keys.push(key);
    };
    add(raw);
    const body = raw.indexOf('SZ') === 0 ? raw.slice(2) : raw;
    add(body);
    const stripped = body.replace(/^0+/, '');
    add(stripped);
    if (stripped) add('SZ' + stripped);
    return keys;
  },
  applyCoachGps(rows, vehicles, nowSec) {
    const list = Array.isArray(rows) ? rows : [];
    const vehs = Array.isArray(vehicles) ? vehicles : [];
    const byRoute = new Map();
    const fold = (value) => String(value || '').toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '');
    vehs.forEach((vehicle) => {
      if (!vehicle) return;
      const lat = Number(vehicle.lat);
      const lon = Number(vehicle.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      BkkLib.coachRouteKeys(vehicle.route).forEach((key) => {
        if (!byRoute.has(key)) byRoute.set(key, []);
        byRoute.get(key).push(vehicle);
      });
    });
    const now = Number.isFinite(Number(nowSec)) ? Number(nowSec) : Math.floor(Date.now() / 1000);
    const rowHead = (row) => fold(row.headsign || row.head);
    const vehicleClassOk = (row, vehicle) => {
      const route = String(vehicle.route || '').trim().toUpperCase();
      const kind = String(row.vehicle || '').toLowerCase();
      if (route.indexOf('SZ') === 0) return kind === 'bus';
      if (/^\d{4}$/.test(route)) return kind === 'coach';
      return true;
    };
    const pairOk = (row, vehicle) => {
      if (!vehicleClassOk(row, vehicle)) return false;
      const head = rowHead(row);
      const vehicleHead = fold(vehicle.head);
      if (head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0)) return true;
      if (!vehicleHead) {
        const dep = Number(row.dep || row.sched || 0);
        return dep > 0 && Math.abs(dep - now) <= 20 * 60;
      }
      return false;
    };
    const groups = new Map();
    list.forEach((row, idx) => {
      if (!row || !/^gtfs:/.test(String(row.tripId || ''))) return;
      if (Number.isFinite(Number(row.lat)) && Number.isFinite(Number(row.lon))) return;
      let key = '';
      BkkLib.coachRouteKeys(row.label).some((candidate) => {
        if (!byRoute.has(candidate)) return false;
        key = candidate;
        return true;
      });
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(idx);
    });
    groups.forEach((indexes, key) => {
      const uniq = [];
      const seen = new Set();
      byRoute.get(key).forEach((vehicle) => {
        const id = String(vehicle.tripId || (vehicle.lat + ',' + vehicle.lon));
        if (seen.has(id)) return;
        seen.add(id);
        uniq.push(vehicle);
      });
      const pairs = [];
      indexes.forEach((idx) => {
        uniq.forEach((vehicle, vi) => {
          if (!pairOk(list[idx], vehicle)) return;
          const dep = Number(list[idx].dep || list[idx].sched || 0);
          const head = rowHead(list[idx]);
          const vehicleHead = fold(vehicle.head);
          const headMatch = !!(head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0));
          const distance = Math.abs((dep || now) - now);
          pairs.push({ idx: idx, vi: vi, score: (headMatch ? 0 : 1) * 1e12 + distance });
        });
      });
      pairs.sort((a, b) => a.score - b.score);
      const usedRow = new Set();
      const usedVehicle = new Set();
      pairs.forEach((pair) => {
        if (usedRow.has(pair.idx) || usedVehicle.has(pair.vi)) return;
        usedRow.add(pair.idx);
        usedVehicle.add(pair.vi);
        list[pair.idx].lat = Number(uniq[pair.vi].lat);
        list[pair.idx].lon = Number(uniq[pair.vi].lon);
      });
    });
    return list;
  },
  async attachCoachGps(hass, rows) {
    const list = Array.isArray(rows) ? rows : [];
    const needs = list.some((row) => row
      && /^gtfs:/.test(String(row.tripId || ''))
      && !(Number.isFinite(Number(row.lat)) && Number.isFinite(Number(row.lon))));
    if (!hass || !needs) return list;
    return BkkLib.applyCoachGps(list, await BkkLib.coachPositions(hass));
  },
  keepFutarCandidate(rt, selected, mode, destKey) {
    if (!selected || !selected.size || mode === 'volan') return true;
    if (destKey && (mode === 'bkk' || mode === 'mav' || mode === 'all')) return true;
    const rid = (rt && rt.id) || '';
    return selected.has(rid);
  },
  rowTimeKey(row) {
    const label = String((row && row.label) || '').toUpperCase();
    const min = Math.round(Number((row && (row.sched || row.dep)) || 0) / 60);
    return label + '|' + min;
  },
  genericRailLabel(label) {
    return /^(IC|EC|IR|EX|EN|RJX|S|SZ)$/i.test(String(label || '').trim());
  },
  matchFutarStopTime(row, st, trip, rt) {
    if (!row || !st || !st.tripId) return false;
    const num = BkkLib.rowTrainNumber(row);
    const n = String((trip && (trip.shortName || trip.tripShortName)) || '').trim().replace(/^0+/, '')
      || BkkLib.trainNumberFromTripId(st.tripId);
    if (num && n && num === n) return true;
    const rowMin = Math.round(Number(row.sched || row.dep || 0) / 60);
    const stMin = Math.round(Number(
      st.departureTime || st.predictedDepartureTime || st.arrivalTime || st.predictedArrivalTime || 0,
    ) / 60);
    if (!rowMin || !stMin || Math.abs(rowMin - stMin) > 1) return false;
    const rl = String(row.label || '').toUpperCase();
    const fl = String((rt && (rt.iconDisplayText || rt.shortName)) || '').toUpperCase();
    if (rl && fl && rl === fl) return true;
    if (rl && fl && BkkLib.genericRailLabel(rl) !== BkkLib.genericRailLabel(fl)
        && (BkkLib.genericRailLabel(rl) || BkkLib.genericRailLabel(fl))) {
      return true;
    }
    return false;
  },
  async futarTripIdForRow(apiKey, cache, row, stopId, extraStopId) {
    const tid = String((row && row.tripId) || '');
    if (BkkLib.isFutarTripId(tid)) return tid;
    const num = BkkLib.rowTrainNumber(row);
    const wantKey = BkkLib.rowTimeKey(row);
    if (!apiKey || (!num && !wantKey)) return '';
    const stops = [];
    [stopId, extraStopId].forEach((id) => {
      if (id && stops.indexOf(id) < 0) stops.push(id);
    });
    if (!stops.length) return '';
    const key = 'futarTrip:' + stops.join(',') + ':' + (num || wantKey);
    const hit = BkkLib._cached(cache, key);
    if (typeof hit === 'string') return hit;
    try {
      for (let s = 0; s < stops.length; s++) {
        const data = await BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
          stopId: stops[s],
          minutesAfter: '360',
          minutesBefore: '180',
          includeReferences: 'true',
          includeVehicleFromTrip: 'true',
        });
        const refs = ((data.data || {}).references) || {};
        const trips = refs.trips || {};
        const routes = refs.routes || {};
        const times = (((data.data || {}).entry) || {}).stopTimes || [];
        for (let i = 0; i < times.length; i++) {
          const st = times[i];
          const trip = trips[(st && st.tripId) || ''] || {};
          const rt = routes[trip.routeId] || {};
          if (BkkLib.matchFutarStopTime(row, st, trip, rt)) {
            return BkkLib._store(cache, key, st.tripId);
          }
        }
      }
      return BkkLib._store(cache, key, '');
    } catch (_e) {
      return '';
    }
  },
  platformFromText(text) {
    const m = String(text || '').match(PLATFORM_IN_HEAD);
    return m ? m[1] : '';
  },
  stripPlatform(text) {
    return String(text || '').replace(PLATFORM_STRIP, '').replace(/\s+/g, ' ').trim();
  },
  collectHassVehicles(hass) {
    const out = [];
    const states = (hass && hass.states) || {};
    Object.keys(states).forEach((eid) => {
      if (eid.indexOf('sensor.') !== 0) return;
      const vehs = (states[eid].attributes || {}).vehicles;
      if (!Array.isArray(vehs)) return;
      vehs.forEach((v) => { if (v) out.push(v); });
    });
    return out;
  },
  matchHassVehicle(pool, row) {
    const trip = String(row.tripId || '');
    const num = String(row.trainNumber || BkkLib.trainNumberFromTripId(row.tripId) || '');
    const hm = BkkLib.hm(row.sched || row.dep);
    const label = String(row.label || '').toUpperCase();
    let best = null;
    let bestScore = 0;
    pool.forEach((v) => {
      let s = 0;
      if (trip && v.tripId && String(v.tripId) === trip) s += 5;
      if (num && v.trainNumber && String(v.trainNumber) === num) s += 3;
      if (hm && v.attime && String(v.attime) === hm) s += 2;
      const vr = String(v.routeid || '').toUpperCase();
      if (label && vr && label === vr) s += 1;
      if (s > bestScore) {
        bestScore = s;
        best = v;
      }
    });
    if (!best) return null;
    if (bestScore >= 5) return best;
    if (num && bestScore >= 3) return best;
    if (hm && label && bestScore >= 3) return best;
    return null;
  },
  /* BKK has no live track or IC seat-reservation flag. Those live on the
     bkk_stop sensors (ELVIRA). Copy them onto the same departure. */
  enrichFromHass(hass, rows) {
    const pool = BkkLib.collectHassVehicles(hass);
    if (!pool.length || !rows || !rows.length) return rows;
    rows.forEach((row) => {
      const v = BkkLib.matchHassVehicle(pool, row);
      if (!v) return;
      if (!row.platform && v.platform) row.platform = String(v.platform);
      if (!row.platform) {
        const fromHead = BkkLib.platformFromText(v.headsign);
        if (fromHead) row.platform = fromHead;
      }
      if (v.booking) row.booking = true;
      if (!row.trainNumber && v.trainNumber) row.trainNumber = String(v.trainNumber);
      if (v.bikesAllowed === true || v.bikesallowed === true) row.bikesAllowed = true;
      if (!Number.isFinite(Number(row.lat)) && v.lat != null) {
        row.lat = Number(v.lat);
        row.lon = Number(v.lon);
      }
    });
    return rows;
  },
  applyRailBadge(row) {
    if (!row) return row;
    const badge = BKK_RAIL_BADGE[String(row.label || '').toUpperCase()];
    if (!badge) return row;
    const cur = String(row.color || '').replace('#', '').toLowerCase();
    if (!cur || cur === '4477aa') {
      row.color = '#' + badge[0];
      row.text = '#' + badge[1];
    }
    return row;
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
    const hasGps = Number.isFinite(lat) && Number.isFinite(lon);
    const gtfs = /^(gtfs:|elvira:)/.test(String(r.tripId || ''));
    return {
      type: type,
      icon: vehicleIcon(type),
      label: r.label,
      headsign: head,
      tripId: r.tripId || '',
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
      travelMin: r.travel,
      lat: hasGps ? lat : null,
      lon: hasGps ? lon : null,
      hasGps: hasGps,
      hasLocation: hasGps || (!gtfs && !!r.tripId),
      vehicle: r.vehicle || '',
      vehicleId: r.vehicleId || '',
      licensePlate: r.licensePlate || '',
      model: r.model || '',
      vehicleStatus: r.vehicleStatus || '',
      stopDistancePercent: r.stopDistancePercent,
    };
  },
};

class BKKHopCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._poll = null;
    this._tick = null;
    this._hass = null;
    this._tripCache = {};
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    this._loading = false;
    this._painted = false;
    this._gen = 0;
    this._keyTried = false;
    this._reloadActive = false;
    this._mapOverlay = null;
    this._map = null;
    this._mapMarker = null;
    this._mapRoute = null;
    this._openMapKey = null;
    this._onMapKey = null;
    this._mapClicksBound = false;
    this._openingMap = '';
  }

  static async getConfigElement() {
    if (!customElements.get(BKK_HOP_EDITOR)) await customElements.whenDefined(BKK_HOP_EDITOR);
    return document.createElement(BKK_HOP_EDITOR);
  }

  static getStubConfig() {
    return {
      name: '',
      stopId: '',
      stopName: '',
      routeIds: [],
      destKey: '',
      destName: '',
      mav: false,
      volan: false,
      helyi: false,
      city: '',
      language: 'auto',
      minutesAfter: DEFAULT_MINUTES_AFTER,
    };
  }

  _lang() {
    return resolveLang((this._config || {}).language, this._hass);
  }

  setConfig(config) {
    if (!this.shadowRoot) {
      try { this.attachShadow({ mode: 'open' }); } catch (_e) { /* already attached */ }
    }
    this._holdGate();
    this._config = Object.assign({ routeIds: [] }, config || {});
    this._tripCache = {};
    this._err = '';
    this._keyTried = false;
    try {
      this._render();
    } catch (err) {
      this._showErr(err);
      return;
    }
    this._reloadSafe();
  }

  /* _reload is async, so its rejections need an owner. Mark in-flight
     synchronously: Home Assistant often setConfig()s then attaches in the
     same turn, and connectedCallback must not start a second fetch. */
  _reloadSafe() {
    this._reloadActive = true;
    Promise.resolve().then(() => this._reload()).catch((err) => this._showErr(err));
  }

  set hass(hass) {
    const first = !this._hass;
    const langBefore = this._hass ? this._lang() : '';
    this._hass = hass;
    if (this._lang() !== langBefore) this._paint();
    if (first && this._rawRows && this._rawRows.length) {
      BkkLib.enrichFromHass(this._hass, this._rawRows);
      const cfg = this._config || {};
      this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
      this._paint();
    }
    this._maybeFillKey();
  }

  /* hass is set on every state change, so looking for a sibling card's key has
     to happen at most once, and never in the key-less modes. */
  _maybeFillKey() {
    const mode = BkkLib.mode(this._config || {});
    if (mode === 'volan' || mode === 'helyi') return;
    if (this._config.apiKey || this._keyTried) return;
    this._keyTried = true;
    this._fillKey().catch(() => { /* dashboard config not readable */ });
  }

  getCardSize() {
    const rows = Math.max(1, (this._rows || []).length);
    return Math.max(1, Math.round((44 + rows * 30) / 50));
  }

  /* Home Assistant re-parents cards while editing a dashboard, so the timers
     have to come back after a detach. */
  _holdGate() {
    if (this._gateHeld) return;
    this._gateHeld = true;
    BkkLib._gateCards += 1;
  }

  connectedCallback() {
    this._holdGate();
    if (!this._painted) return;
    this._startTick();
    this._bindMapClicks();
    if (!this._poll && !this._reloadActive) this._reloadSafe();
  }

  disconnectedCallback() {
    if (this._gateHeld) {
      this._gateHeld = false;
      BkkLib._gateCards = Math.max(0, BkkLib._gateCards - 1);
    }
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    if (this._tick) { clearInterval(this._tick); this._tick = null; }
  }

  /* Keep the rows on screen and put the failure in a banner above them: a
     single failed refresh should not wipe a timetable that is still useful. */
  _showErr(err) {
    this._err = errText(this._lang(), err);
    this._paint();
  }

  async _fillKey() {
    const key = await BkkLib.apiKeyFromDashboard(this._hass);
    if (!key || this._config.apiKey) return;
    this._config = Object.assign({}, this._config, { apiKey: key });
    this._reloadSafe();
  }

  _header() {
    const cfg = this._config || {};
    const ready = cfg.stopId && cfg.destKey;
    if (cfg.name) return cfg.name;
    if (ready) return `${cfg.stopName || ''} \u2192 ${cfg.destName || ''}`;
    return 'Hungarian transport';
  }

  _render() {
    if (!this._rows) this._rows = [];
    if (!this._tripCache) this._tripCache = {};
    const root = this.shadowRoot;
    if (!root) return;
    if (this._painted) { this._paint(); return; }
    while (root.firstChild) root.removeChild(root.firstChild);
    const style = document.createElement('style');
    style.textContent = `
      :host { display: block; pointer-events: auto; }
      ha-card { overflow: visible; pointer-events: auto; }
      .wrap { padding: 10px 12px 11px; }
      .head { font-weight: 700; margin-bottom: 7px; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
      .err { font-size: 12px; color: var(--error-color, #e66); margin-bottom: 6px; }
      .msg { font-size: 12px; color: var(--secondary-text-color); }
      .body { pointer-events: auto; }
      table { width: 100%; border-collapse: collapse; font-size: 13px; pointer-events: auto; }
      tbody tr, tr.row-clickable { cursor: pointer; }
      tr.row-clickable:hover td { background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
      tr + tr td { border-top: 1px solid var(--divider-color); }
      td { padding: 6px 3px; vertical-align: middle; }
      td.icon { width: 20px; color: var(--secondary-text-color); }
      td.icon ha-icon, td.mapbtn ha-icon { --mdc-icon-size: 18px; display: block; pointer-events: none; }
      td.mapbtn { width: 28px; color: var(--secondary-text-color); }
      td.mapbtn .map-open {
        appearance: none; border: 0; background: transparent; color: inherit;
        padding: 2px; margin: 0; cursor: pointer; display: flex;
        align-items: center; justify-content: center; pointer-events: auto;
      }
      td.route { width: 1%; white-space: nowrap; }
      .badge { display: inline-block; min-width: 2.4em; padding: 2px 6px;
        border-radius: 6px; font-weight: 800; font-size: 12px; line-height: 1.35;
        text-align: center; }
      td.dest { max-width: 0; overflow: hidden; white-space: nowrap;
        text-overflow: ellipsis; }
      .num { margin-left: 5px; font-size: 11px; color: var(--secondary-text-color); }
      td.flags { width: 1%; white-space: nowrap; }
      td.when { width: 1%; white-space: nowrap; text-align: right;
        font-variant-numeric: tabular-nums; }
      .at { font-weight: 700; }
      .at.late { color: var(--error-color, #e66); }
      .at.early { color: var(--success-color, #2e9e4f); }
      .sched { margin-left: 5px; font-size: 11px; text-decoration: line-through;
        color: var(--secondary-text-color); }
      td.in { width: 1%; white-space: nowrap; text-align: right; font-weight: 700;
        font-variant-numeric: tabular-nums; }
      .travel { display: block; font-size: 11px; font-weight: 400;
        color: var(--secondary-text-color); }
      .pictos { display: inline-flex; gap: 4px; align-items: center; }
      .picto { position: relative; font-size: 11px; line-height: 1.2; cursor: help; }
      .picto.booking { color: #c0392b; font-weight: 800; font-size: 11px; }
      .plat {
        display: inline-block; margin-right: 4px; padding: 0 5px; border-radius: 2px;
        background: #2E5EA8; color: #fff; font-size: 11px; font-weight: 700;
        line-height: 1.45; white-space: nowrap;
      }
      .picto::after {
        content: attr(data-tip); position: absolute; left: 50%; top: calc(100% + 7px);
        transform: translateX(-50%); background: var(--primary-text-color, #222);
        color: var(--card-background-color, #fff); padding: 4px 7px; border-radius: 6px;
        font-size: 11px; font-weight: 500; white-space: nowrap; pointer-events: none;
        opacity: 0; visibility: hidden; transition: opacity 0.12s; z-index: 30;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
      }
      .picto::before {
        content: ''; position: absolute; left: 50%; top: calc(100% + 2px);
        transform: translateX(-50%); border: 5px solid transparent;
        border-bottom-color: var(--primary-text-color, #222); pointer-events: none;
        opacity: 0; visibility: hidden; transition: opacity 0.12s; z-index: 30;
      }
      .picto:hover::after, .picto:hover::before,
      .picto:focus-visible::after, .picto:focus-visible::before {
        opacity: 1; visibility: visible;
      }
    `;
    const card = document.createElement('ha-card');
    const wrap = document.createElement('div');
    wrap.className = 'wrap';
    this._elHead = document.createElement('div');
    this._elHead.className = 'head';
    this._elErr = document.createElement('div');
    this._elErr.className = 'err';
    this._elBody = document.createElement('div');
    this._elBody.className = 'body';
    wrap.appendChild(this._elHead);
    wrap.appendChild(this._elErr);
    wrap.appendChild(this._elBody);
    card.appendChild(wrap);
    root.appendChild(style);
    root.appendChild(card);
    this._painted = true;
    this._bindMapClicks();
    this._paint();
    this._startTick();
  }

  /* The countdown is derived from the epoch seconds, so it stays honest
     between refreshes without hitting the API again. */
  _startTick() {
    if (this._tick) clearInterval(this._tick);
    this._tick = setInterval(() => {
      if ((this._rows || []).length) this._paint();
    }, 15000);
  }

  _paint() {
    if (!this._painted || !this._elBody) return;
    const lang = this._lang();
    const cfg = this._config || {};
    this._elHead.textContent = this._header();
    this._elErr.textContent = this._err || '';
    this._elErr.style.display = this._err ? '' : 'none';
    const nowSec = Math.floor(Date.now() / 1000);
    const allRows = this._rows || [];
    const rows = allRows.filter((r) => departureStillDue(r.depTs, nowSec));
    if (rows.length) {
      this._elBody.innerHTML = `<table><tbody>${
        rows.map((r, i) => this._rowHtml(r, lang, i)).join('')
      }</tbody></table>`;
      return;
    }
    let msg = '';
    if (!cfg.stopId || !cfg.destKey) msg = t(lang, 'cardPickStops');
    else if (this._loading) msg = t(lang, 'cardLoading');
    else if (!this._err) msg = t(lang, 'cardNoDepartures');
    this._elBody.innerHTML = msg ? `<div class="msg">${BkkLib.esc(msg)}</div>` : '';
  }

  _badgeStyle(row) {
    const hex = (v, fallback) => {
      const s = String(v || '').replace('#', '');
      return /^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(s) ? '#' + s : fallback;
    };
    const bg = hex(row.color, 'var(--primary-color)');
    const fg = hex(row.textcolor, '#fff');
    return `background:${bg};color:${fg}`;
  }

  _picto(glyph, tip, cls) {
    const extra = cls ? ` ${cls}` : '';
    return `<span class="picto${extra}" tabindex="0" role="img" aria-label="${BkkLib.esc(tip)}"`
      + ` data-tip="${BkkLib.esc(tip)}">${BkkLib.esc(glyph)}</span>`;
  }

  _countdown(ts, lang) {
    if (!ts) return '';
    const mins = Math.round((ts * 1000 - Date.now()) / 60000);
    return mins <= 0 ? t(lang, 'now') : t(lang, 'minutes', mins);
  }

  _rowHtml(r, lang, idx) {
    const esc = BkkLib.esc;
    const delayMin = Math.round(Number(r.delay || 0) / 60);
    let atClass = 'at';
    let sched = '';
    let atTip = '';
    if (delayMin >= 1) {
      atClass += ' late';
      atTip = t(lang, 'tipDelay', delayMin);
      if (r.attime && r.attime !== r.predicted_attime) sched = r.attime;
    } else if (delayMin <= -1) {
      atClass += ' early';
      atTip = t(lang, 'tipEarly', Math.abs(delayMin));
      if (r.attime && r.attime !== r.predicted_attime) sched = r.attime;
    } else if (r.attime) {
      atTip = t(lang, 'tipScheduled', r.attime);
    }
    const pictos = [];
    if (r.wheelchair) pictos.push(this._picto('\u267f', t(lang, 'tipWheelchair')));
    if (r.bikesAllowed) pictos.push(this._picto('\ud83d\udeb2', t(lang, 'tipBike')));
    if (r.booking) pictos.push(this._picto('H', t(lang, 'tipBooking'), 'booking'));
    const plat = r.platform
      ? `<span class="plat" title="${esc(t(lang, 'tipPlatform', r.platform))}">${
        esc(r.platform)}</span>`
      : '';
    const travel = r.travelMin
      ? `<span class="travel" title="${esc(t(lang, 'tipTravel', r.travelMin))}">${
        esc(t(lang, 'minutes', r.travelMin))}</span>`
      : '';
    const num = r.trainNumber
      ? `<span class="num" title="${esc(t(lang, 'tipTrain', r.trainNumber))}">${
        esc(r.trainNumber)}</span>`
      : '';
    const tip = esc(t(lang, 'mapOpenTip'));
    const clickable = ` class="row-clickable" data-row-index="${idx}" title="${tip}"`;
    const mapBtn = `<td class="mapbtn"><button type="button" class="map-open" aria-label="${tip}" title="${tip}"><ha-icon icon="mdi:map-marker-outline"></ha-icon></button></td>`;
    return `<tr${clickable}>
      <td class="icon"><ha-icon icon="${esc(r.icon || 'mdi:bus')}"></ha-icon></td>
      <td class="route"><span class="badge" style="${esc(this._badgeStyle(r))}">${
      esc(r.label || '?')}</span></td>
      <td class="dest" title="${esc(r.headsign || '')}">${esc(r.headsign || '')}${num}</td>
      <td class="flags">${plat}${pictos.length ? `<span class="pictos">${pictos.join('')}</span>` : ''}</td>
      ${mapBtn}
      <td class="when"><span class="${atClass}" title="${esc(atTip)}">${
      esc(r.predicted_attime || r.attime || '')}</span>${
      sched ? `<span class="sched">${esc(sched)}</span>` : ''}</td>
      <td class="in">${esc(this._countdown(r.depTs, lang))}${travel}</td>
    </tr>`;
  }

  _bindMapClicks() {
    if (this._mapClicksBound) return;
    if (!this._elBody) return;
    this._mapClicksBound = true;
    const fn = (ev) => this._onRowClick(ev);
    this._elBody.addEventListener('click', fn);
    this.addEventListener('click', fn, true);
  }

  _rowFromClick(ev) {
    const path = (typeof ev.composedPath === 'function') ? ev.composedPath() : [];
    let tr = null;
    for (let i = 0; i < path.length; i++) {
      const n = path[i];
      if (n && n.nodeType === 1 && n.getAttribute && n.getAttribute('data-row-index') != null
        && (n.tagName === 'TR' || (n.classList && n.classList.contains('row-clickable')))) {
        tr = n;
        break;
      }
    }
    if (!tr) {
      const start = ev.target;
      if (start && start.closest) tr = start.closest('tr[data-row-index]');
    }
    if (!tr) return null;
    const idx = Number(tr.getAttribute('data-row-index'));
    return (this._rows || [])[idx] || null;
  }

  _onRowClick(ev) {
    const row = this._rowFromClick(ev);
    if (!row) return;
    ev.preventDefault();
    ev.stopPropagation();
    this._openVehicleMap(row);
  }

  _mapTypeLabel(kind, lang) {
    const k = String(kind || '').toLowerCase();
    if (k === 'tram') return t(lang, 'mapTypeTram');
    if (k === 'trolleybus' || k === 'trolley') return t(lang, 'mapTypeTrolley');
    if (k === 'subway' || k === 'metro') return t(lang, 'mapTypeSubway');
    if (k === 'rail' || k === 'train' || k === 'coach') return t(lang, 'mapTypeRail');
    if (k === 'bus') return t(lang, 'mapTypeBus');
    return t(lang, 'mapTypeVehicle');
  }

  _mapGlyph(kind) {
    const k = String(kind || '').toLowerCase();
    if (k === 'tram') return '\ud83d\ude8a';
    if (k === 'trolleybus' || k === 'trolley') return '\ud83d\ude8e';
    if (k === 'subway' || k === 'metro') return '\ud83d\ude87';
    if (k === 'rail' || k === 'train' || k === 'coach') return '\ud83d\ude86';
    if (k === 'bus') return '\ud83d\ude8c';
    return '\ud83d\udccd';
  }

  _ensureLeaflet() {
    const isLeaflet = (L) => !!(L && typeof L.map === 'function' && typeof L.tileLayer === 'function' && typeof L.divIcon === 'function');
    if (typeof window !== 'undefined' && isLeaflet(window.L)) return Promise.resolve(window.L);
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise((resolve, reject) => {
      const cssId = 'hungarian-transport-leaflet-css';
      if (typeof document !== 'undefined' && !document.getElementById(cssId)) {
        const link = document.createElement('link');
        link.id = cssId;
        link.rel = 'stylesheet';
        link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
        document.head.appendChild(link);
      }
      if (typeof window !== 'undefined' && isLeaflet(window.L)) {
        resolve(window.L);
        return;
      }
      const existing = typeof document !== 'undefined'
        ? document.getElementById('hungarian-transport-leaflet-js') : null;
      if (existing) {
        existing.addEventListener('load', () => {
          if (isLeaflet(window.L)) resolve(window.L);
          else reject(new Error('leaflet'));
        });
        existing.addEventListener('error', reject);
        return;
      }
      const script = document.createElement('script');
      script.id = 'hungarian-transport-leaflet-js';
      script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      script.onload = () => {
        if (isLeaflet(window.L)) resolve(window.L);
        else reject(new Error('leaflet'));
      };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return leafletPromise;
  }

  _closeVehicleMap() {
    if (this._onMapKey && typeof document !== 'undefined') {
      document.removeEventListener('keydown', this._onMapKey);
      this._onMapKey = null;
    }
    if (this._mapRoute && this._map) {
      try { this._map.removeLayer(this._mapRoute); } catch (_e) { /* ignore */ }
      this._mapRoute = null;
    }
    if (this._map) {
      try { this._map.remove(); } catch (_e) { /* ignore */ }
      this._map = null;
      this._mapMarker = null;
    }
    if (this._mapOverlay && this._mapOverlay.parentNode) {
      this._mapOverlay.parentNode.removeChild(this._mapOverlay);
    }
    this._mapOverlay = null;
    this._openMapKey = null;
    this._openingMap = '';
  }

  _mapKey(row) {
    return `${row.label || ''}|${row.headsign || ''}|${row.vehicleId || row.tripId || ''}`;
  }

  _dotIcon(row, live, lang) {
    const hex = String(row.color || '').replace('#', '');
    const color = /^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(hex) ? '#' + hex : '#2E5EA8';
    const pulse = live ? 'ht-veh-dot-live' : 'ht-veh-dot-est';
    const tip = live ? t(lang, 'mapLiveDot') : t(lang, 'mapEstDot');
    return `<div class="ht-veh-dot ${pulse}" style="--bg:${BkkLib.esc(color)};" title="${BkkLib.esc(tip)}">`
      + `<span class="ht-veh-dot-core"></span></div>`;
  }

  _mapFootText(row, hasShape, hasGps, estimated, lang) {
    const bits = [];
    if (hasShape) bits.push(t(lang, 'mapFullRoute'));
    if (hasGps) bits.push(t(lang, 'mapLivePos'));
    else if (estimated) bits.push(t(lang, 'mapEstPos'));
    else if (hasShape) bits.push(t(lang, 'mapNoGps'));
    if (row.stopDistancePercent != null && row.stopDistancePercent !== '') {
      bits.push(t(lang, 'mapUntilStop', row.stopDistancePercent));
    }
    if (row.vehicleStatus) bits.push(String(row.vehicleStatus));
    if (row.licensePlate) bits.push(String(row.licensePlate));
    if (row.predicted_attime && row.attime && row.predicted_attime !== row.attime) {
      bits.push(t(lang, 'mapExpectedVs', [row.predicted_attime, row.attime]));
    } else if (row.predicted_attime || row.attime) {
      bits.push(t(lang, 'mapExpected', row.predicted_attime || row.attime));
    }
    return bits.join(' \u00b7 ') || t(lang, 'mapFootFallback');
  }

  _showMapNotice(message) {
    this._closeVehicleMap();
    const overlay = document.createElement('div');
    overlay.className = 'ht-map-overlay';
    overlay.innerHTML = `
      <style>
        .ht-map-overlay{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:16px}
        .ht-map-notice{background:var(--card-background-color,#fff);padding:16px 18px;border-radius:10px;max-width:360px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.3);color:var(--primary-text-color,#222)}
        .ht-map-notice button{margin-top:12px;border:0;background:var(--primary-color,#03a9f4);color:#fff;padding:8px 12px;border-radius:6px;cursor:pointer}
      </style>
      <div class="ht-map-notice" role="dialog">${BkkLib.esc(message)}<br><button type="button">OK</button></div>
    `;
    document.body.appendChild(overlay);
    this._mapOverlay = overlay;
    const close = () => this._closeVehicleMap();
    overlay.querySelector('button').addEventListener('click', close);
    overlay.addEventListener('click', (ev) => { if (ev.target === overlay) close(); });
  }

  async _openVehicleMap(row) {
    const lang = this._lang();
    const cfg = this._config || {};
    const key = this._mapKey(row);
    if (this._openingMap === key) return;
    if (this._mapOverlay && this._openMapKey === key) return;
    this._closeVehicleMap();
    this._openingMap = key;
    let lat = row.lat != null ? Number(row.lat) : NaN;
    let lon = row.lon != null ? Number(row.lon) : NaN;
    let hasGps = Number.isFinite(lat) && Number.isFinite(lon);
    let shape = [];
    let hasShape = false;
    let positionEstimated = false;
    let tripId = row.tripId || '';
    if (!hasGps && !tripId) {
      this._showMapNotice(t(lang, 'mapNoData'));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'ht-map-overlay';
    const sub = this._mapTypeLabel(row.vehicle || row.type, lang)
      + (row.model ? ' \u00b7 ' + row.model : '');
    overlay.innerHTML = `
      <style>
        .ht-map-overlay{position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
        .ht-map-panel{width:min(720px,100%);height:min(70vh,560px);background:var(--card-background-color,#fff);border-radius:28px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 12px 40px rgba(0,0,0,.35);color:var(--primary-text-color,#222)}
        .ht-map-head{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--divider-color,rgba(0,0,0,.08));font:600 14px/1.3 system-ui,sans-serif}
        .ht-map-head .meta{flex:1;min-width:0}
        .ht-map-head .sub{font-weight:400;font-size:12px;opacity:.75;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ht-map-close{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;padding:4px 8px;opacity:.7;color:inherit}
        .ht-map-canvas{flex:1;height:420px;min-height:420px}
        .ht-map-foot{padding:8px 12px;font:12px/1.35 system-ui,sans-serif;opacity:.85;border-top:1px solid var(--divider-color,rgba(0,0,0,.08))}
        .ht-veh-dot{width:22px;height:22px;transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;filter:drop-shadow(0 1px 3px rgba(0,0,0,.45))}
        .ht-veh-dot-core{width:14px;height:14px;border-radius:50%;background:var(--bg,#2E5EA8);border:2.5px solid #fff;box-sizing:border-box}
        .ht-veh-dot-live .ht-veh-dot-core{width:16px;height:16px;box-shadow:0 0 0 3px color-mix(in srgb, var(--bg,#2E5EA8) 35%, transparent)}
        .ht-veh-dot-est .ht-veh-dot-core{border-style:dashed;opacity:.95}
      </style>
      <div class="ht-map-panel" role="dialog" aria-modal="true">
        <div class="ht-map-head">
          <div class="meta">
            <div>${BkkLib.esc(this._mapGlyph(row.vehicle || row.type))} ${BkkLib.esc(row.label || '')} \u2192 ${BkkLib.esc(row.headsign || '')}</div>
            <div class="sub">${BkkLib.esc(sub)}</div>
          </div>
          <button type="button" class="ht-map-close" aria-label="${BkkLib.esc(t(lang, 'mapClose'))}">\u00d7</button>
        </div>
        <div class="ht-map-canvas"></div>
        <div class="ht-map-foot">${BkkLib.esc(t(lang, 'mapLoadingRoute'))}</div>
      </div>
    `;
    (document.documentElement || document.body).appendChild(overlay);
    this._mapOverlay = overlay;
    this._openMapKey = this._mapKey(row);
    this._onMapKey = (ev) => {
      if (ev.key === 'Escape') this._closeVehicleMap();
    };
    document.addEventListener('keydown', this._onMapKey);
    overlay.querySelector('.ht-map-close').addEventListener('click', () => this._closeVehicleMap());
    overlay.addEventListener('click', (ev) => {
      if (ev.target === overlay) this._closeVehicleMap();
    });
    const canvas = overlay.querySelector('.ht-map-canvas');
    const foot = overlay.querySelector('.ht-map-foot');
    const startLat = hasGps ? lat : 47.5;
    const startLon = hasGps ? lon : 19.05;
    let map;
    try {
      map = L.map(canvas, { scrollWheelZoom: true }).setView([startLat, startLon], hasGps ? 12 : 10);
      /* Carto Voyager raster now watermarks every tile without an API key.
         OSM France is a keyed-less street map that still works with Leaflet. */
      L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
        maxZoom: 20,
        subdomains: 'abc',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://www.openstreetmap.fr/">OpenStreetMap France</a>',
      }).addTo(map);
    } catch (_e) {
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    this._map = map;
    const resizeMap = () => { try { map.invalidateSize(); } catch (_e) { /* ignore */ } };
    requestAnimationFrame(() => { resizeMap(); requestAnimationFrame(resizeMap); });
    const drawMarker = () => {
      if (!this._map || this._map !== map) return;
      const hereLat = Number(lat);
      const hereLon = Number(lon);
      if (!Number.isFinite(hereLat) || !Number.isFinite(hereLon)) return;
      if (this._mapMarker) {
        try { this._map.removeLayer(this._mapMarker); } catch (_e) { /* ignore */ }
      }
      const icon = L.divIcon({
        className: '',
        html: this._dotIcon(row, hasGps && !positionEstimated, lang),
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      this._mapMarker = L.marker([hereLat, hereLon], { icon: icon, zIndexOffset: 600 }).addTo(map);
    };
    if (hasGps) {
      drawMarker();
      foot.textContent = this._mapFootText(row, false, true, false, lang);
      map.setView([lat, lon], 13);
    }
    let details = null;
    if (!hasShape && /^gtfs:/i.test(String(row.tripId || '')) && this._hass) {
      try {
        const points = await BkkLib.coachShape(this._hass, row.label, row.headsign || '');
        shape = BkkLib.decodePolyline(points);
        hasShape = shape.length >= 2;
      } catch (_e) { /* timetable row still shows the live dot */ }
    }
    if (!hasShape && cfg.apiKey) {
      let geomTrip = BkkLib.isFutarTripId(tripId) ? tripId : '';
      if (!geomTrip) {
        try {
          let extraStop = cfg.destStopId || '';
          if (!extraStop && (cfg.destName || cfg.destKey)) {
            const code = await BkkLib.elviraResolveCode(
              cfg.apiKey, '', cfg.destName || cfg.destKey,
            );
            if (code) extraStop = /^BKK_/i.test(code) ? code : ('BKK_' + code);
          }
          geomTrip = await BkkLib.futarTripIdForRow(
            cfg.apiKey, this._tripCache || {}, row, cfg.stopId, extraStop,
          ) || '';
        } catch (_e) { /* keep the live dot without a line */ }
      }
      if (BkkLib.isFutarTripId(geomTrip)) {
        try {
          details = await BkkLib.tripDetails(cfg.apiKey, this._tripCache || {}, geomTrip);
          shape = BkkLib.decodePolyline((details && details.polyline) || '');
          hasShape = shape.length >= 2;
          if (!hasShape) foot.textContent = t(lang, 'mapNoGeometry');
          if (!hasGps && details && details.vehicle) {
            const loc = BkkLib.vehicleLoc(details.vehicle);
            if (Number.isFinite(loc.lat) && Number.isFinite(loc.lon)) {
              lat = loc.lat;
              lon = loc.lon;
              hasGps = true;
            }
          }
        } catch (err) {
          foot.textContent = t(lang, 'mapRouteFail', err && err.message ? err.message : err);
        }
      }
    }
    if (!this._map || this._map !== map || !this._mapOverlay) {
      this._openingMap = '';
      return;
    }
    if (!hasGps && hasShape && details) {
      const est = BkkLib.estimatePositionFromSchedule(shape, details.sts, details.nowSec);
      if (est && Number.isFinite(est[0]) && Number.isFinite(est[1])) {
        lat = est[0];
        lon = est[1];
        positionEstimated = true;
      }
    }
    const hasPosition = Number.isFinite(lat) && Number.isFinite(lon);
    const hex = String(row.color || '').replace('#', '');
    const routeColor = /^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(hex) ? '#' + hex : '#2E5EA8';
    if (hasShape) {
      L.polyline(shape, {
        color: '#1a1a1a', weight: 7, opacity: 0.45,
        lineJoin: 'round', lineCap: 'round', interactive: false,
      }).addTo(map);
      this._mapRoute = L.polyline(shape, {
        color: routeColor, weight: 5, opacity: 0.98,
        lineJoin: 'round', lineCap: 'round',
      }).addTo(map);
    }
    if (hasPosition && !this._mapMarker) drawMarker();
    foot.textContent = this._mapFootText(row, hasShape, hasGps, positionEstimated, lang);
    setTimeout(() => {
      map.invalidateSize();
      if (hasShape) {
        const bounds = L.latLngBounds(shape);
        if (hasPosition) bounds.extend([lat, lon]);
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 13 });
      } else if (hasPosition) {
        map.setView([lat, lon], 13);
      } else {
        foot.textContent = t(lang, 'mapNothingToShow');
      }
    }, 80);
    this._openingMap = '';
  }

  _syncOpenMap() {
    if (!this._map || !this._openMapKey || !this._mapMarker) return;
    const row = (this._rows || []).find((r) => this._mapKey(r) === this._openMapKey);
    if (!row || !row.hasGps || row.lat == null || row.lon == null) return;
    const lat = Number(row.lat);
    const lon = Number(row.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const L = typeof window !== 'undefined' ? window.L : null;
    this._mapMarker.setLatLng([lat, lon]);
    if (L) {
      this._mapMarker.setIcon(L.divIcon({
        className: '',
        html: this._dotIcon(row, true, this._lang()),
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      }));
    }
    const foot = this._mapOverlay && this._mapOverlay.querySelector('.ht-map-foot');
    if (foot) foot.textContent = this._mapFootText(row, true, true, false, this._lang());
  }

  _departMode() {
    return BkkLib.mode(this._config || {});
  }

  async _reload() {
    /* Overlapping reloads (config edits, reconnects) must not leave an orphan
       interval behind, so every run is tagged with the generation that owns it. */
    const gen = ++this._gen;
    this._reloadActive = true;
    const cfg = this._config || {};
    if (cfg.volanIndex) setVolanIndexUrl(cfg.volanIndex);
    if (cfg.cityIndex) setCityIndexUrl(cfg.cityIndex);
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    const mode = this._departMode();
    const ready = cfg.stopId && cfg.destKey && (cfg.apiKey || mode === 'volan' || mode === 'helyi' || mode === 'all');
    if (!ready) {
      this._rows = [];
      this._loading = false;
      this._paint();
      if (gen === this._gen) this._reloadActive = false;
      return;
    }
    const run = async () => {
      if (gen !== this._gen) return;
      try {
        const wantElvira = (mode === 'mav' || mode === 'all') && this._hass;
        const elviraP = wantElvira
          ? BkkLib.elviraBetween(
            this._hass,
            cfg.stopId,
            cfg.destStopId || '',
            cfg.minutesAfter,
            cfg.destName || '',
            { apiKey: cfg.apiKey, originName: cfg.stopName || '' },
          ).catch(() => [])
          : Promise.resolve([]);
        const paintRows = (list) => {
          if (gen !== this._gen) return;
          const merged = list || [];
          merged.forEach((row) => BkkLib.applyRailBadge(row));
          this._rawRows = merged;
          BkkLib.enrichFromHass(this._hass, this._rawRows);
          this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
          this._err = '';
          this._loading = false;
          this._paint();
        };
        const cap = maxDepartureRows(cfg.minutesAfter);
        let earlyRows = [];
        let futarFull = null;
        let elviraRows = [];
        const publish = () => {
          if (gen !== this._gen) return;
          let list = futarFull
            ? BkkLib.mergeVolanRows(futarFull, earlyRows, cap)
            : earlyRows.slice();
          list = BkkLib.mergeVolanRows(elviraRows, list, cap);
          paintRows(list);
        };
        let startThrough = null;
        let elviraDone = !wantElvira;
        let throughDone = true;
        const considerJourney = () => {
          if (!elviraDone || !throughDone) return;
          if (typeof this._considerJourney === 'function') this._considerJourney();
        };
        const rows = await BkkLib.departures(
          cfg.apiKey, cfg.stopId, cfg.routeIds, {
            key: cfg.destKey,
            name: cfg.destName,
            id: cfg.destStopId || '',
          },
          {
            originName: cfg.stopName || '',
            cache: this._tripCache || {},
            mode: this._departMode(),
            city: cfg.city || '',
            minutesAfter: cfg.minutesAfter,
            deferTravel: true,
            deferGps: true,
            hass: this._hass,
            skipElvira: true,
            onPartial: paintRows,
            armThroughCheck: (fn) => { startThrough = fn; },
            onFutarUpdate: (full) => {
              if (gen !== this._gen) return;
              futarFull = full || [];
              publish();
              throughDone = true;
              considerJourney();
            },
          },
        );
        if (gen !== this._gen) return;
        this._journey = null;
        this._journeyKey = '';
        earlyRows = rows || [];
        publish();
        elviraP.then((list) => {
          if (gen !== this._gen) return;
          elviraRows = list || [];
          publish();
          elviraDone = true;
          considerJourney();
        });
        if (startThrough) {
          throughDone = false;
          startThrough();
        }
        considerJourney();
        this._syncOpenMap();
        const origin = { id: cfg.stopId, name: cfg.stopName || '' };
        await Promise.all([
          BkkLib.attachCoachGps(this._hass, this._rawRows).then((withGps) => {
            if (gen !== this._gen) return;
            this._rawRows = withGps;
            this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
            this._paint();
            this._syncOpenMap();
          }),
          Promise.all(this._rawRows.map(async (row) => {
            if (row.travel != null || /^(gtfs:|elvira:)/.test(String(row.tripId || ''))) return;
            row.travel = await BkkLib.travelMin(
              cfg.apiKey, row.tripId, cfg.destKey, row.dep, origin, this._tripCache || {},
            );
          })),
        ]);
        if (gen !== this._gen) return;
        this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
        this._paint();
        this._syncOpenMap();
      } catch (err) {
        if (gen !== this._gen) return;
        this._loading = false;
        this._showErr(err);
      }
    };
    this._loading = !(this._rows || []).length;
    this._paint();
    try {
      await run();
      if (!this.isConnected || gen !== this._gen) return;
      const every = Math.max(15, Number(cfg.refresh || 45)) * 1000;
      this._poll = setInterval(run, every);
    } finally {
      if (gen === this._gen) this._reloadActive = false;
    }
  }
}


class BKKPlannerCard extends BKKHopCard {
  constructor() {
    super();
    this._searchTimer = null;
    this._destTimer = null;
    this._searchSeq = 0;
    this._destSeq = 0;
    this._destSearchSeq = 0;
    this._dests = [];
    this._destRoutes = {};
    this._destHits = [];
    this._destsLoading = false;
    this._pickersMounted = false;
  }

  static async getConfigElement() {
    if (!customElements.get(BKK_PLANNER_EDITOR)) {
      await customElements.whenDefined(BKK_PLANNER_EDITOR);
    }
    return document.createElement(BKK_PLANNER_EDITOR);
  }

  static getStubConfig() {
    return {
      name: '',
      language: 'auto',
      minutesAfter: DEFAULT_MINUTES_AFTER,
      stopId: '',
      stopName: '',
      destKey: '',
      destName: '',
      destStopId: '',
      routeIds: [],
    };
  }

  getCardSize() {
    return Math.max(6, super.getCardSize() + 3);
  }

  _departMode() {
    return 'all';
  }

  _considerJourney() {
    if (this._journeyLoading) return;
    const nowSec = Math.floor(Date.now() / 1000);
    const visible = (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
    if (visible.length) {
      if (this._journey) {
        this._journey = null;
        this._paint();
      }
      return;
    }
    const cfg = this._config || {};
    if (!cfg.apiKey || !cfg.stopId || !cfg.destName) return;
    const key = [cfg.stopId, cfg.destStopId || '', cfg.destName].join('|');
    if (this._journeyKey === key) return;
    const gen = this._gen;
    this._journeyKey = key;
    this._journeyLoading = true;
    BkkLib.planJourney(
      cfg.apiKey,
      { id: cfg.stopId, name: cfg.stopName || '' },
      { id: cfg.destStopId || '', name: cfg.destName || '' },
    ).then((journey) => {
      this._journeyLoading = false;
      if (gen !== this._gen) return;
      this._journey = journey;
      this._paint();
    }).catch(() => {
      this._journeyLoading = false;
    });
  }

  _paint() {
    super._paint();
    this._paintJourney();
  }

  _paintJourney() {
    if (!this._elBody) return;
    const prev = this._elBody.querySelector('.journey-plan');
    if (prev) prev.remove();
    const journey = this._journey;
    if (!journey || !journey.legs || !journey.legs.length) return;
    const nowSec = Math.floor(Date.now() / 1000);
    const visible = (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
    if (visible.length) return;
    const lang = this._lang();
    const esc = BkkLib.esc;
    const legs = journey.legs.map((leg) => {
      if (leg.walk) {
        return `<div class="j-leg j-walk"><span class="j-mode">${esc(t(lang, 'plannerWalk', [leg.minutes, leg.meters]))}</span>`
          + `<span class="j-where">${esc(leg.from)} \u2192 ${esc(leg.to)}</span></div>`;
      }
      const ride = leg.label
        ? `<span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label)}</span>`
        : '';
      return `<div class="j-leg">${ride}<span class="j-where">${esc(leg.headsign || leg.to)}</span>`
        + `<span class="j-min">${esc(t(lang, 'plannerRide', leg.minutes))}</span></div>`;
    }).join('');
    const box = document.createElement('div');
    box.className = 'journey-plan';
    box.innerHTML = `<div class="j-title">${esc(t(lang, 'plannerJourneyTitle'))}</div>`
      + `<div class="j-sum">${esc(t(lang, 'plannerJourneySummary', [journey.durationMin, journey.transfers, journey.walkMin]))}</div>`
      + legs;
    this._elBody.appendChild(box);
  }

  _header() {
    const cfg = this._config || {};
    if (cfg.stopId && cfg.destKey) {
      return `${cfg.stopName || ''} \u2192 ${cfg.destName || ''}`;
    }
    return cfg.name || t(this._lang(), 'plannerTitle');
  }

  _render() {
    const first = !this._painted;
    super._render();
    const card = this.shadowRoot && this.shadowRoot.querySelector('ha-card');
    if (card) card.classList.add('is-planner');
    if (first) this._mountPickers();
    else this._syncPickerLabels();
    this._ensureDepartureCaption();
  }

  _ensureDepartureCaption() {
    if (!this.shadowRoot || !this._elBody || this.shadowRoot.getElementById('pdeps')) return;
    const cap = document.createElement('div');
    cap.id = 'pdeps';
    cap.className = 'deps-label';
    cap.textContent = t(this._lang(), 'plannerDepartures');
    this._elBody.parentNode.insertBefore(cap, this._elBody);
  }

  _planEl(id) {
    return this.shadowRoot ? this.shadowRoot.getElementById(id) : null;
  }

  _mountPickers() {
    if (this._pickersMounted || !this.shadowRoot) return;
    const wrap = this.shadowRoot.querySelector('.wrap');
    const head = this._elHead;
    if (!wrap || !head) return;
    const style = document.createElement('style');
    style.textContent = `
      ha-card.is-planner { overflow: visible; }
      ha-card.is-planner .wrap { padding: 8px 4px 12px; }
      ha-card.is-planner .head {
        font-size: 22px; font-weight: 700; letter-spacing: -0.02em;
        margin: 0; padding: 8px 12px 0; white-space: normal; line-height: 1.2;
      }
      ha-card.is-planner .deps-label {
        margin: 16px 12px 4px; font-size: 12px; font-weight: 700;
        letter-spacing: 0.04em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      ha-card.is-planner .body { padding: 0 4px; }
      ha-card.is-planner table { font-size: 15px; }
      ha-card.is-planner td { padding: 12px 6px; }
      ha-card.is-planner td.route .badge {
        min-width: 2.6em; text-align: center; border-radius: 8px;
        padding: 4px 8px; font-weight: 700;
      }
      .plan { margin: 8px 8px 0; }
      .journey {
        display: grid; grid-template-columns: 40px minmax(0, 1fr); column-gap: 8px;
        padding: 14px; border-radius: var(--ha-card-border-radius, 16px);
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .spine { display: flex; flex-direction: column; align-items: center; padding-top: 28px; }
      .spine .dot { width: 14px; height: 14px; border-radius: 50%; box-sizing: border-box; flex: 0 0 auto; }
      .spine .dot.from { background: var(--primary-color); }
      .spine .dot.to { background: var(--card-background-color, #fff); border: 3px solid var(--primary-color); }
      .spine .stem { width: 2px; flex: 1 1 16px; min-height: 16px; background: var(--divider-color); }
      .spine #pswap {
        margin: 8px 0; border: 0; cursor: pointer; font: inherit; font-size: 12px; font-weight: 700;
        border-radius: 999px; padding: 8px 10px;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .place + .place { margin-top: 14px; }
      .plan .label { font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
        color: var(--secondary-text-color); margin-bottom: 6px; }
      .plan input {
        width: 100%; box-sizing: border-box; font: inherit; font-size: 16px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 12px; padding: 14px 14px;
      }
      .plan input:focus { outline: 2px solid var(--primary-color); outline-offset: 1px; }
      .plan .horizon {
        display: flex; flex-direction: column; align-items: stretch; gap: 8px;
        margin-top: 12px; padding: 10px 12px; border-radius: 16px;
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .plan .horizon-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      .plan .horizon .label { margin: 0; }
      .plan .horizon .chips {
        flex: none; width: 100%; margin: 0; flex-wrap: wrap; overflow: visible;
      }
      .plan .horizon .chip { flex: 0 0 auto; }
      .plan #preset {
        border: 0; background: transparent; color: var(--primary-color);
        font: inherit; font-size: 14px; font-weight: 700; cursor: pointer; padding: 6px;
        flex: 0 0 auto;
      }
      .plan .suggest { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
      .plan .suggest.hidden { display: none; }
      .plan .suggest-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
      .plan .suggest-kind {
        flex: 0 0 48px; font-size: 12px; font-weight: 700; color: var(--secondary-text-color);
      }
      .plan .suggest-stops { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }
      .plan .suggest-stop {
        border: 0; border-radius: 12px; cursor: pointer;
        font: inherit; font-size: 14px; font-weight: 600; padding: 8px 12px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
      }
      .plan .suggest-row[data-kind="bkk"] .suggest-stop { box-shadow: inset 3px 0 0 var(--primary-color); }
      .plan .suggest-row[data-kind="volan"] .suggest-stop { box-shadow: inset 3px 0 0 var(--warning-color, #f0b429); }
      .plan .suggest-row[data-kind="mav"] .suggest-stop { box-shadow: inset 3px 0 0 var(--info-color, #03a9f4); }
      .plan .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
      .plan .chip {
        border: 1px solid var(--divider-color); background: transparent;
        color: var(--primary-text-color); border-radius: 999px;
        padding: 6px 10px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 700;
      }
      .plan .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
      .plan .list { max-height: 220px; overflow: auto; margin-top: 8px; }
      .plan .list:empty { display: none; }
      .plan .hit {
        display: block; width: 100%; text-align: left; font: inherit; font-size: 15px; font-weight: 650;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 0; border-radius: 12px; padding: 10px 12px; cursor: pointer; margin-top: 6px;
      }
      .plan .picked {
        display: none; margin-top: 8px; font-size: 16px; font-weight: 800; line-height: 1.25;
        padding: 8px 10px; border-radius: 12px; background: var(--card-background-color, #fff);
      }
      .plan .picked.show { display: block; }
      .journey-plan {
        margin: 8px 8px 12px; padding: 12px;
        border-radius: var(--ha-card-border-radius, 12px);
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .journey-plan .j-title { font-size: 12px; font-weight: 700; letter-spacing: 0.04em;
        text-transform: uppercase; color: var(--secondary-text-color); }
      .journey-plan .j-sum { margin: 4px 0 10px; font-size: 16px; font-weight: 700; }
      .journey-plan .j-leg { display: flex; align-items: baseline; gap: 8px;
        padding: 6px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
      .journey-plan .j-where { flex: 1; min-width: 0; }
      .journey-plan .j-min { font-weight: 700; }
      .journey-plan .j-badge {
        flex: 0 0 auto; border-radius: 8px; padding: 2px 8px; font-weight: 700;
      }
      .journey-plan .j-walk { color: var(--secondary-text-color); }
    `;
    this.shadowRoot.appendChild(style);
    const lang = this._lang();
    const plan = document.createElement('div');
    plan.className = 'plan';
    plan.innerHTML = `
      <div class="journey">
        <div class="spine">
          <span class="dot from"></span>
          <span class="stem"></span>
          <button id="pswap" type="button">${BkkLib.esc(t(lang, 'plannerSwap'))}</button>
          <span class="stem"></span>
          <span class="dot to"></span>
        </div>
        <div class="places">
          <div class="place">
            <div class="label" id="plab1">${BkkLib.esc(t(lang, 'plannerStep1'))}</div>
            <input id="pq" type="search" placeholder="${BkkLib.esc(t(lang, 'plannerSearchPlaceholder'))}">
            <div class="picked" id="pstop"></div>
            <div class="suggest" id="pfav"></div>
            <div class="list" id="phits"></div>
          </div>
          <div class="place">
            <div class="label" id="plab2">${BkkLib.esc(t(lang, 'plannerStep2'))}</div>
            <input id="pdq" type="search" placeholder="${BkkLib.esc(t(lang, 'plannerDestPlaceholder'))}">
            <div class="picked" id="pdest"></div>
            <div class="list" id="pdhits"></div>
          </div>
        </div>
      </div>
      <div class="horizon">
        <div class="horizon-bar">
          <div class="label" id="plab3">${BkkLib.esc(t(lang, 'plannerStep3'))}</div>
          <button id="preset" type="button">${BkkLib.esc(t(lang, 'plannerReset'))}</button>
        </div>
        <div class="chips" id="pmin"></div>
      </div>
    `;
    wrap.insertBefore(plan, head);
    this._pickersMounted = true;
    this._bindPickers();
    this._paintFav();
    this._syncPickerLabels();
    this._paintDestHits('');
  }

  _bindPickers() {
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    const reset = this._planEl('preset');
    const swap = this._planEl('pswap');
    if (q) {
      q.addEventListener('input', () => {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this._searchOrigin(q.value.trim()), 280);
      });
    }
    if (dq) {
      dq.addEventListener('input', () => {
        clearTimeout(this._destTimer);
        this._destTimer = setTimeout(() => this._searchDest(dq.value.trim()), 280);
      });
      dq.addEventListener('focus', () => this._searchDest(dq.value.trim()));
    }
    if (reset) reset.addEventListener('click', () => this._resetPlan());
    if (swap) swap.addEventListener('click', () => this._swapPlan());
    this._paintMinutesAfter();
  }

  _paintFav() {
    const box = this._planEl('pfav');
    if (!box) return;
    box.innerHTML = '';
    const shortName = (name) => String(name || '')
      .replace(/^Budapest,\s*/, '')
      .replace(/^Budapest-/, '')
      .replace(/\s+aut\u00f3busz-p\u00e1lyaudvar$/, '')
      .replace(/\s+vas\u00fat\u00e1llom\u00e1s$/, '')
      .replace(/\s+p\u00e1lyaudvar$/, '');
    [
      ['bkk', 'BKK', BKK_FAVORITES],
      ['volan', 'Vol\u00e1n', VOLAN_FAVORITES],
      ['mav', 'M\u00c1V', MAV_FAVORITES],
    ].forEach(([kind, title, list]) => {
      const row = document.createElement('div');
      row.className = 'suggest-row';
      row.setAttribute('data-kind', kind);
      const label = document.createElement('span');
      label.className = 'suggest-kind';
      label.textContent = title;
      const stops = document.createElement('span');
      stops.className = 'suggest-stops';
      list.forEach((fav) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'suggest-stop';
        button.textContent = shortName(fav.name);
        button.addEventListener('click', () => this._pickOrigin(fav));
        stops.appendChild(button);
      });
      row.appendChild(label);
      row.appendChild(stops);
      box.appendChild(row);
    });
  }

  _syncPickerLabels() {
    const cfg = this._config || {};
    const lang = this._lang();
    const stop = this._planEl('pstop');
    const dest = this._planEl('pdest');
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    if (stop) {
      stop.textContent = cfg.stopName || '';
      stop.classList.toggle('show', !!cfg.stopName);
    }
    const fav = this._planEl('pfav');
    if (fav) fav.classList.toggle('hidden', !!cfg.stopName);
    if (dest) {
      dest.textContent = cfg.destName || '';
      dest.classList.toggle('show', !!cfg.destName);
    }
    if (q && document.activeElement !== q) {
      q.placeholder = cfg.stopName || t(lang, 'plannerSearchPlaceholder');
    }
    if (dq && document.activeElement !== dq) {
      dq.placeholder = cfg.destName || t(lang, 'plannerDestPlaceholder');
    }
    const lab1 = this._planEl('plab1');
    const lab2 = this._planEl('plab2');
    if (lab1) lab1.textContent = t(lang, 'plannerStep1');
    if (lab2) lab2.textContent = t(lang, 'plannerStep2');
    const lab3 = this._planEl('plab3');
    if (lab3) lab3.textContent = t(lang, 'plannerStep3');
    const deps = this._planEl('pdeps');
    if (deps) deps.textContent = t(lang, 'plannerDepartures');
    const swap = this._planEl('pswap');
    const reset = this._planEl('preset');
    if (swap) swap.textContent = t(lang, 'plannerSwap');
    if (reset) reset.textContent = t(lang, 'plannerReset');
    this._paintMinutesAfter();
  }

  _paintMinutesAfter() {
    const box = this._planEl('pmin');
    if (!box) return;
    const current = clampMinutesAfter(this._config && this._config.minutesAfter);
    box.innerHTML = '';
    MINUTES_AFTER_PRESETS.forEach((n) => {
      const b = document.createElement('button');
      b.className = 'chip' + (n === current ? ' on' : '');
      b.type = 'button';
      b.textContent = String(n);
      b.addEventListener('click', () => {
        this._config = Object.assign({}, this._config, { minutesAfter: n });
        this._paintMinutesAfter();
        this._reloadSafe();
      });
      box.appendChild(b);
    });
  }

  _swapPlan() {
    const cfg = this._config || {};
    const newOriginId = cfg.destStopId;
    const newOriginName = cfg.destName;
    if (!cfg.stopId || !newOriginId || !newOriginName) return;
    this._config = Object.assign({}, cfg, {
      stopId: newOriginId,
      stopName: newOriginName,
      destKey: BkkLib.stationKey(cfg.stopName || ''),
      destName: cfg.stopName || '',
      destStopId: cfg.stopId,
      routeIds: [],
    });
    this._dests = [];
    this._destRoutes = {};
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    const hits = this._planEl('phits');
    if (q) q.value = newOriginName;
    if (dq) dq.value = cfg.stopName || '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._paint();
    this._loadDests().then(() => this._reloadSafe());
  }

  _resetPlan() {
    this._dests = [];
    this._destRoutes = {};
    this._config = Object.assign({}, this._config, {
      stopId: '',
      stopName: '',
      destKey: '',
      destName: '',
      destStopId: '',
      routeIds: [],
    });
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    const hits = this._planEl('phits');
    if (q) q.value = '';
    if (dq) dq.value = '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._paintDestHits('');
    this._paint();
  }

  async _searchOrigin(q) {
    const box = this._planEl('phits');
    if (!box) return;
    if (!q || q.length < 2) { box.innerHTML = ''; return; }
    const seq = ++this._searchSeq;
    try {
      this._err = '';
      if (!this._config.apiKey) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const hits = await BkkLib.searchStops(this._config.apiKey, q, 'all');
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), 'mode').bkk.empty) + '</span>';
        return;
      }
      box.innerHTML = hits.map((s) => (
        `<button class="hit" type="button" data-id="${BkkLib.esc(s.id)}">${BkkLib.esc(s.label || s.name)}</button>`
      )).join('');
      box.querySelectorAll('.hit').forEach((btn) => {
        btn.addEventListener('click', () => {
          const id = btn.getAttribute('data-id');
          const st = hits.find((h) => h.id === id);
          this._pickOrigin({ id: st.id, name: st.name, label: st.label });
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._showErr(err);
    }
  }

  async _pickOrigin(stop) {
    this._config = Object.assign({}, this._config, {
      stopId: stop.id,
      stopName: stop.name,
      destKey: '',
      destName: '',
      destStopId: '',
      routeIds: [],
    });
    this._dests = [];
    this._destRoutes = {};
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    const hits = this._planEl('phits');
    if (q) q.value = stop.label || stop.name;
    if (dq) dq.value = '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._paint();
    await this._loadDests();
  }

  async _loadDests() {
    const cfg = this._config || {};
    if (!cfg.stopId) return;
    const seq = ++this._destSeq;
    this._destsLoading = true;
    this._paintDestHits('');
    try {
      if (!cfg.apiKey) await this._fillKey();
      const out = await BkkLib.reachableDests(
        this._config.apiKey,
        this._tripCache || {},
        { id: cfg.stopId, name: cfg.stopName || '' },
        'all',
        '',
      );
      if (seq !== this._destSeq) return;
      this._dests = (out && out.dests) || [];
      this._destRoutes = (out && out.destRoutes) || {};
    } catch (err) {
      if (seq !== this._destSeq) return;
      this._showErr(err);
      this._dests = [];
      this._destRoutes = {};
    } finally {
      if (seq === this._destSeq) {
        this._destsLoading = false;
        this._searchDest((this._planEl('pdq') || {}).value || '');
      }
    }
  }

  _idsForDest(key) {
    const raw = this._destRoutes[key] || [];
    const out = [];
    raw.forEach((id) => {
      if (!id) return;
      out.push(id);
      const s = String(id);
      if (s.indexOf('gtfs:') !== 0 && !/^BKK_/i.test(s) && !/^volan_/i.test(s) && !/^hkir_/i.test(s)) {
        out.push('gtfs:' + s);
      }
    });
    return out;
  }

  async _searchDest(q) {
    const box = this._planEl('pdhits');
    if (!box) return;
    const lang = this._lang();
    const cfg = this._config || {};
    if (!cfg.stopId) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'plannerPickAll')) + '</span>';
      return;
    }
    if (!q || q.length < 2) {
      this._paintDestHits(q || '');
      return;
    }
    const seq = ++this._destSearchSeq;
    try {
      if (!this._config.apiKey) await this._fillKey();
      const extras = await BkkLib.searchStops(this._config.apiKey, q, 'all');
      if (seq !== this._destSearchSeq) return;
      const hits = BkkLib.destHitsForQuery(this._dests, q, extras);
      if (!hits.length && this._destsLoading) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'destLoading')) + '</span>';
        return;
      }
      this._renderDestHits(hits, t(lang, 'mode').bkk.destEmpty);
    } catch (err) {
      if (seq !== this._destSearchSeq) return;
      this._showErr(err);
    }
  }

  _renderDestHits(hits, emptyMsg) {
    const box = this._planEl('pdhits');
    if (!box) return;
    this._destHits = hits || [];
    if (!this._destHits.length) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(emptyMsg) + '</span>';
      return;
    }
    const shown = this._destHits.slice(0, 20);
    box.innerHTML = shown.map((d) => (
      `<button class="hit" type="button" data-key="${BkkLib.esc(d.key)}" data-id="${BkkLib.esc(d.id || '')}">${BkkLib.esc(d.name)}</button>`
    )).join('');
    box.querySelectorAll('.hit').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-key');
        const id = btn.getAttribute('data-id') || '';
        const d = (this._destHits || []).find((x) => x.key === key);
        if (d) this._pickDest(Object.assign({}, d, { id: d.id || id }));
      });
    });
  }

  _paintDestHits(q) {
    const box = this._planEl('pdhits');
    if (!box) return;
    const lang = this._lang();
    const cfg = this._config || {};
    if (!cfg.stopId) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'plannerPickAll')) + '</span>';
      return;
    }
    if (this._destsLoading) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'destLoading')) + '</span>';
      return;
    }
    if (!this._dests.length) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'mode').bkk.destNone) + '</span>';
      return;
    }
    const hits = BkkLib.destHitsForQuery(this._dests, q, []);
    this._renderDestHits(hits, t(lang, 'mode').bkk.destEmpty);
  }

  _pickDest(dest) {
    this._config = Object.assign({}, this._config, {
      destKey: dest.key,
      destName: dest.name,
      destStopId: dest.id || '',
      routeIds: this._idsForDest(dest.key),
    });
    const dq = this._planEl('pdq');
    const hits = this._planEl('pdhits');
    if (dq) dq.value = dest.name;
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._reloadSafe();
  }
}


class BKKHopCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = { routeIds: [] };
    this._built = false;
    this._searchTimer = null;
    this._destTimer = null;
    this._loadingKey = false;
    this._cache = {};
    this._dests = [];
    this._destRoutes = {};
    this._loadedStop = '';
    this._destsLoading = false;
    this._keyOk = null;
    this._probingKey = false;
    this._keyDirty = false;
    this._userEditedKey = false;
    this._keyReveal = false;
    this._searchSeq = 0;
    this._destSeq = 0;
    this._citySeq = 0;
  }

  disconnectedCallback() {
    if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
    if (this._destTimer) { clearTimeout(this._destTimer); this._destTimer = null; }
  }

  setConfig(config) {
    this._config = Object.assign({ routeIds: [] }, config || {});
    if (!this._cache) this._cache = {};
    if (!this._dests) this._dests = [];
    if (!this._destRoutes) this._destRoutes = {};
    if (this._config.volanIndex) setVolanIndexUrl(this._config.volanIndex);
    if (this._config.cityIndex) setCityIndexUrl(this._config.cityIndex);
    if (!this._built) this._build();
    this._syncLang();
    this._syncTexts();
    this._syncName();
    this._syncStopLabel();
    this._syncDestLabel();
    this._syncMinutesAfter();
    this._syncApiKey();
    this._syncMode();
    this._probeKey();
    if (this._config.stopId && (this._config.apiKey || this._mode() === 'volan' || this._mode() === 'helyi') && this._loadedStop !== this._config.stopId) {
      this._loadDests();
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    // "auto" resolves against hass.language, which is only known once hass lands.
    if (first && this._built && this._lang() !== resolveLang(this._config.language, null)) {
      this._syncTexts();
      this._syncMode();
    }
    if (!this._userEditedKey && !this._config.apiKey && !this._loadingKey) this._fillKey();
    else if (this._config.apiKey) this._probeKey();
  }

  _lang() { return resolveLang(this._config.language, this._hass); }

  _mode() { return BkkLib.mode(this._config); }

  _build() {
    this._built = true;
    this.innerHTML = `
      <style>
        .card-config { padding: 8px 0 4px; }
        .card-config .f { margin-bottom: 18px; }
        .card-config label {
          display: block; font-weight: 500; margin-bottom: 8px;
          color: var(--primary-text-color);
        }
        .card-config input, .card-config select {
          width: 100%; box-sizing: border-box; font: inherit;
          background: var(--mdc-text-field-fill-color, var(--secondary-background-color, rgba(128,128,128,.12)));
          color: var(--primary-text-color);
          border: 0; border-bottom: 1px solid var(--divider-color);
          border-radius: 4px 4px 0 0; padding: 12px 12px 10px;
        }
        .card-config .hint { font-size: 12px; color: var(--secondary-text-color); margin: 6px 0 0; }
        .card-config .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .card-config .chip.on {
          color: var(--text-primary-color, #fff);
          background: var(--primary-color);
          border-color: var(--primary-color);
        }
        .card-config .hit {
          display: block; width: 100%; text-align: left; font: inherit;
          background: transparent; color: var(--primary-text-color);
          border: 0; border-bottom: 1px solid var(--divider-color);
          padding: 10px 8px; cursor: pointer;
        }
        .card-config .hit:hover { background: var(--secondary-background-color); }
        .card-config .list { max-height: 200px; overflow: auto; }
        .card-config .picked { font-weight: 600; margin-top: 6px; }
        .card-config .err { color: var(--error-color, #e66); font-size: 12px; margin-top: 8px; }
        .card-config .switch {
          display: flex; align-items: center; gap: 10px; font-weight: 500;
        }
        .card-config .switch input { width: auto; margin: 0; }
        .card-config .row { display: flex; gap: 18px; flex-wrap: wrap; }
        .card-config .row .switch { margin-right: 8px; }
        .card-config a { color: var(--primary-color); }
      </style>
      <div class="card-config">
        <div class="f">
          <label id="lblLang"></label>
          <select id="lang">
            <option value="auto"></option>
            <option value="hu"></option>
            <option value="en"></option>
          </select>
        </div>
        <div class="f" id="apiKeyWrap">
          <label id="lblApiKey"></label>
          <input id="apiKey" type="text" autocomplete="off">
          <div class="hint"><span id="hintApiKey"></span>
            <a href="https://opendata.bkk.hu/data-sources" target="_blank" rel="noopener">opendata.bkk.hu/data-sources</a>
          </div>
        </div>
        <div class="f" id="apiKeyOkWrap" style="display:none">
          <div class="hint"><span id="apiKeyOkText"></span>
            <a href="#" id="apiKeyEdit"></a>
          </div>
        </div>
        <div class="f">
          <label id="lblName"></label>
          <input id="name" type="text" placeholder="Hungarian transport">
        </div>
        <div class="f">
          <div class="row" id="modeSwitches">
            <label class="switch"><input id="mav" type="checkbox"> M\u00c1V</label>
            <label class="switch"><input id="volan" type="checkbox"> Vol\u00e1n</label>
            <label class="switch"><input id="helyi" type="checkbox"> Helyi</label>
          </div>
          <div class="hint" id="modeHint"></div>
        </div>
        <div class="f" id="cityWrap" style="display:none">
          <label id="lblCity"></label>
          <select id="city"></select>
        </div>
        <div class="f">
          <label id="lblStop"></label>
          <input id="q" type="search">
          <div class="chips" id="fav"></div>
          <div class="picked" id="stopPicked"></div>
          <div class="list" id="hits"></div>
        </div>
        <div class="f">
          <label id="lblDest"></label>
          <input id="dq" type="search">
          <div class="picked" id="destPicked"></div>
          <div class="list" id="destHits"></div>
        </div>
        <div class="f">
          <label id="lblMinutesAfter"></label>
          <select id="minutesAfter"></select>
          <div class="chips" id="minutesAfterChips"></div>
          <div class="hint" id="hintMinutesAfter"></div>
        </div>
        <div class="err" id="err"></div>
      </div>
    `;
    this._el('lang').addEventListener('change', (ev) => {
      this._emit({ language: ev.target.value });
      this._syncTexts();
      this._syncMode();
      this._paintDestHits(this._el('dq') ? this._el('dq').value.trim() : '');
    });
    this._el('name').addEventListener('input', (ev) => {
      this._emit({ name: ev.target.value });
    });
    this._el('apiKey').addEventListener('input', () => {
      this._keyDirty = true;
      this._userEditedKey = true;
    });
    this._el('apiKey').addEventListener('change', (ev) => {
      this._keyDirty = false;
      this._userEditedKey = true;
      this._keyOk = null;
      this._probedKey = '';
      this._error('');
      this._emit({ apiKey: ev.target.value.trim() });
      this._probeKey();
    });
    this._el('apiKeyEdit').addEventListener('click', (ev) => {
      ev.preventDefault();
      this._keyReveal = true;
      this._syncApiKey();
      const el = this._el('apiKey');
      if (el) el.focus();
    });
    this._el('mav').addEventListener('change', () => this._onModeToggle('mav'));
    this._el('volan').addEventListener('change', () => this._onModeToggle('volan'));
    this._el('helyi').addEventListener('change', () => this._onModeToggle('helyi'));
    this._el('city').addEventListener('change', (ev) => {
      this._emit({
        city: ev.target.value,
        stopId: '',
        stopName: '',
        destKey: '',
        destName: '',
        routeIds: [],
      });
      this._dests = [];
      this._loadedStop = '';
      this._syncStopLabel();
      this._syncDestLabel();
    });
    this._el('q').addEventListener('input', () => {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this._search(this._el('q').value.trim(), 'origin'), 280);
    });
    this._el('dq').addEventListener('input', () => {
      clearTimeout(this._destTimer);
      this._destTimer = setTimeout(() => this._search(this._el('dq').value.trim(), 'dest'), 280);
    });
    this._el('dq').addEventListener('focus', () => {
      this._search(this._el('dq').value.trim(), 'dest');
    });
    this._el('minutesAfter').addEventListener('change', (ev) => {
      const next = clampMinutesAfter(ev.target.value);
      if (next === clampMinutesAfter(this._config.minutesAfter)) {
        this._syncMinutesAfter();
        return;
      }
      this._emit({ minutesAfter: next });
    });
    this._paintFav();
  }

  _el(id) { return this.querySelector('#' + id); }

  /* The editor lives in HA's shadow tree. document.activeElement is the host,
     so walking only shadowRoots never reaches these light-DOM inputs. */
  _focusedEl() {
    try {
      return this.querySelector(':focus');
    } catch (_e) {
      return null;
    }
  }

  _isEditing(el) {
    if (!el) return false;
    const focused = this._focusedEl();
    if (focused === el) return true;
    if (focused && el.contains && el.contains(focused)) return true;
    try {
      if (typeof el.matches === 'function' && el.matches(':focus, :focus-within')) return true;
    } catch (_e) { /* jsdom */ }
    return false;
  }

  _syncName() {
    const el = this._el('name');
    if (!el || this._isEditing(el)) return;
    el.value = this._config.name || '';
  }

  _syncStopLabel() {
    const el = this._el('stopPicked');
    if (el) el.textContent = this._config.stopName || '';
    const q = this._el('q');
    if (q && !this._isEditing(q) && this._config.stopName) {
      q.placeholder = this._config.stopName;
    }
  }

  _syncDestLabel() {
    const el = this._el('destPicked');
    if (el) el.textContent = this._config.destName || '';
    const q = this._el('dq');
    if (q && !this._isEditing(q) && this._config.destName) {
      q.placeholder = this._config.destName;
    }
  }

  _syncMinutesAfter() {
    const cur = clampMinutesAfter(this._config.minutesAfter);
    const sel = this._el('minutesAfter');
    if (sel) {
      const lang = this._lang();
      const values = MINUTES_AFTER_PRESETS.slice();
      if (values.indexOf(cur) < 0) values.push(cur);
      values.sort((a, b) => a - b);
      const sig = lang + ':' + values.join(',');
      if (sel.dataset.opts !== sig) {
        sel.innerHTML = values.map((n) => (
          `<option value="${n}">${BkkLib.esc(t(lang, 'minutes', n))}</option>`
        )).join('');
        sel.dataset.opts = sig;
      }
      if (!this._isEditing(sel)) sel.value = String(cur);
    }
    const box = this._el('minutesAfterChips');
    if (!box) return;
    const signature = String(cur);
    if (box.dataset.cur === signature && box.childElementCount) {
      Array.from(box.children).forEach((b) => {
        b.classList.toggle('on', Number(b.getAttribute('data-min')) === cur);
      });
      return;
    }
    box.innerHTML = '';
    box.dataset.cur = signature;
    MINUTES_AFTER_PRESETS.forEach((n) => {
      const b = document.createElement('button');
      b.className = 'chip' + (n === cur ? ' on' : '');
      b.type = 'button';
      b.setAttribute('data-min', String(n));
      b.textContent = String(n);
      b.addEventListener('click', () => {
        if (n === clampMinutesAfter(this._config.minutesAfter)) return;
        this._emit({ minutesAfter: n });
      });
      box.appendChild(b);
    });
  }

  _syncApiKey() {
    this._syncApiKeyVisibility();
    const wrap = this._el('apiKeyWrap');
    const hidden = wrap && wrap.style.display === 'none';
    const el = this._el('apiKey');
    if (!el || this._isEditing(el) || this._keyDirty) return;
    el.value = hidden ? '' : (this._config.apiKey || '');
  }

  /* A working key is noise in the form, but it still has to be replaceable
     without hand-editing YAML, hence the "change" link. */
  _syncApiKeyVisibility() {
    const wrap = this._el('apiKeyWrap');
    const okWrap = this._el('apiKeyOkWrap');
    if (!wrap) return;
    const mode = this._mode();
    if (mode === 'volan' || mode === 'helyi') {
      wrap.style.display = 'none';
      if (okWrap) okWrap.style.display = 'none';
      return;
    }
    const key = (this._config.apiKey || '').trim();
    const settled = !!key && this._keyOk === true && !this._keyReveal;
    wrap.style.display = settled ? 'none' : '';
    if (okWrap) okWrap.style.display = settled ? '' : 'none';
  }

  _modeCopy(mode) {
    return modeCopy(this._lang(), mode);
  }

  _syncLang() {
    const el = this._el('lang');
    if (!el) return;
    const lang = this._lang();
    el.options[0].textContent = t(lang, 'langAuto');
    el.options[1].textContent = t(lang, 'langHu');
    el.options[2].textContent = t(lang, 'langEn');
    const pref = (this._config.language === 'hu' || this._config.language === 'en')
      ? this._config.language
      : 'auto';
    if (!this._isEditing(el)) el.value = pref;
  }

  _syncTexts() {
    const lang = this._lang();
    this._syncLang();
    const set = (id, text) => {
      const el = this._el(id);
      if (el) el.textContent = text;
    };
    set('lblLang', t(lang, 'langLabel'));
    set('lblApiKey', t(lang, 'apiKeyLabel'));
    set('hintApiKey', t(lang, 'apiKeyHint'));
    set('apiKeyOkText', t(lang, 'apiKeyOk'));
    set('apiKeyEdit', t(lang, 'apiKeyChange'));
    set('lblName', t(lang, 'nameLabel'));
    set('lblCity', t(lang, 'cityLabel'));
    set('lblMinutesAfter', t(lang, 'minutesAfterLabel'));
    set('hintMinutesAfter', t(lang, 'minutesAfterHint'));
    this._syncMinutesAfter();
    const key = this._el('apiKey');
    if (key) key.placeholder = t(lang, 'apiKeyPlaceholder');
    const name = this._el('name');
    if (name) name.placeholder = t(lang, 'namePlaceholder');
  }

  _syncMode() {
    const mode = this._mode();
    const mav = this._el('mav');
    const volan = this._el('volan');
    const helyi = this._el('helyi');
    if (mav && !this._isEditing(mav)) mav.checked = mode === 'mav';
    if (volan && !this._isEditing(volan)) volan.checked = mode === 'volan';
    if (helyi && !this._isEditing(helyi)) helyi.checked = mode === 'helyi';
    const wrap = this._el('cityWrap');
    if (wrap) wrap.style.display = mode === 'helyi' ? '' : 'none';
    if (mode === 'helyi') this._fillCities();
    const copy = this._modeCopy(mode);
    const hint = this._el('modeHint');
    if (hint) hint.textContent = copy.hint;
    const lblStop = this._el('lblStop');
    if (lblStop) lblStop.textContent = copy.stop;
    const lblDest = this._el('lblDest');
    if (lblDest) lblDest.textContent = copy.dest;
    const q = this._el('q');
    if (q && !this._isEditing(q) && !this._config.stopName) q.placeholder = copy.originPh;
    const dq = this._el('dq');
    if (dq && !this._isEditing(dq) && !this._config.destName) dq.placeholder = copy.destPh;
    this._syncApiKeyVisibility();
    this._paintFav();
  }

  _paintFav() {
    const fav = this._el('fav');
    if (!fav) return;
    fav.innerHTML = '';
    const mode = this._mode();
    const custom = Array.isArray(this._config.favorites) ? this._config.favorites : null;
    const list = (custom || DEFAULT_FAVORITES[mode] || []).filter((f) => f && f.id && f.name);
    list.forEach((f) => {
      const b = document.createElement('button');
      b.className = 'chip';
      b.type = 'button';
      b.textContent = f.name.split(' / ')[0];
      b.addEventListener('click', () => this._pickStop(f, 'origin'));
      fav.appendChild(b);
    });
  }

  _onModeToggle(which) {
    const mavOn = !!(this._el('mav') && this._el('mav').checked);
    const volanOn = !!(this._el('volan') && this._el('volan').checked);
    const helyiOn = !!(this._el('helyi') && this._el('helyi').checked);
    let mav = mavOn;
    let volan = volanOn;
    let helyi = helyiOn;
    if (which === 'mav' && mav) { volan = false; helyi = false; }
    if (which === 'volan' && volan) { mav = false; helyi = false; }
    if (which === 'helyi' && helyi) { mav = false; volan = false; }
    if (this._el('mav')) this._el('mav').checked = mav;
    if (this._el('volan')) this._el('volan').checked = volan;
    if (this._el('helyi')) this._el('helyi').checked = helyi;
    const q = this._el('q');
    const dq = this._el('dq');
    if (q) q.value = '';
    if (dq) dq.value = '';
    const hits = this._el('hits');
    const destHits = this._el('destHits');
    if (hits) hits.innerHTML = '';
    if (destHits) destHits.innerHTML = '';
    this._dests = [];
    this._destRoutes = {};
    this._cache = {};
    this._loadedStop = '';
    this._emit({
      mav: mav,
      volan: volan,
      helyi: helyi,
      city: helyi ? (this._config.city || '') : '',
      routeIds: [],
      stopId: '',
      stopName: '',
      destKey: '',
      destName: '',
    });
    this._syncMode();
    this._syncStopLabel();
    this._syncDestLabel();
  }

  async _fillCities() {
    const sel = this._el('city');
    if (!sel || this._isEditing(sel)) return;
    const seq = ++this._citySeq;
    try {
      const idx = await BkkLib.cityIndex();
      if (seq !== this._citySeq) return;
      const ops = (idx.ops || []).slice().sort((a, b) => a.name.localeCompare(b.name, 'hu'));
      const cur = this._config.city || '';
      const signature = ops.map((o) => o.id).join('|');
      if (sel.dataset.ops === signature && sel.options.length > 1) {
        if (cur && sel.value !== cur) sel.value = cur;
        return;
      }
      const ph = t(this._lang(), 'cityPlaceholder');
      sel.innerHTML = `<option value="">${BkkLib.esc(ph)}</option>` + ops.map((o) => (
        `<option value="${BkkLib.esc(o.id)}">${BkkLib.esc(o.name)}</option>`
      )).join('');
      sel.dataset.ops = signature;
      if (cur) sel.value = cur;
    } catch (err) {
      if (seq !== this._citySeq) return;
      this._error(errText(this._lang(), err));
    }
  }

  _error(msg) {
    const el = this._el('err');
    if (el) el.textContent = msg || '';
  }

  _emit(patch) {
    const cfg = Object.assign({}, this._config, patch);
    this._config = cfg;
    this.dispatchEvent(new CustomEvent('config-changed', {
      bubbles: true,
      composed: true,
      detail: { config: cfg },
    }));
  }

  async _fillKey() {
    this._loadingKey = true;
    try {
      const key = await BkkLib.apiKeyFromDashboard(this._hass);
      if (key && !this._config.apiKey && !this._userEditedKey) {
        this._emit({ apiKey: key });
        await this._probeKey();
      }
    } finally {
      this._loadingKey = false;
    }
  }

  async _probeKey() {
    const mode = this._mode();
    if (mode === 'volan' || mode === 'helyi') {
      this._syncApiKey();
      return;
    }
    const key = (this._config.apiKey || '').trim();
    if (!key) {
      this._keyOk = false;
      this._syncApiKey();
      return;
    }
    if (this._probingKey) return;
    if (this._probedKey === key && this._keyOk != null) {
      this._syncApiKey();
      return;
    }
    this._probingKey = true;
    this._probedKey = key;
    try {
      const result = await BkkLib.probeApiKey(key);
      if ((this._config.apiKey || '').trim() !== key) return;
      this._keyOk = !!result.ok;
      this._syncApiKey();
      if (!result.ok && result.code) this._error(errText(this._lang(), codedError(result.code)));
      else if (result.ok) this._error('');
    } finally {
      this._probingKey = false;
      const latest = (this._config.apiKey || '').trim();
      if (latest && latest !== this._probedKey) this._probeKey();
    }
  }

  async _search(q, which) {
    const box = this._el(which === 'dest' ? 'destHits' : 'hits');
    if (!box) return;
    if (which === 'dest') {
      this._paintDestHits(q);
      return;
    }
    if (!q || q.length < 2) {
      box.innerHTML = '';
      return;
    }
    const seq = ++this._searchSeq;
    try {
      this._error('');
      if (!this._config.apiKey && !this._userEditedKey) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const mode = this._mode();
      const hits = await BkkLib.searchStops(this._config.apiKey, q, mode, this._config.city);
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(this._modeCopy(mode).empty) + '</span>';
        return;
      }
      box.innerHTML = hits.map((s) => (
        `<button class="hit" type="button" data-id="${BkkLib.esc(s.id)}">${BkkLib.esc(s.label || s.name)}</button>`
      )).join('');
      box.querySelectorAll('.hit').forEach((btn) => {
        btn.addEventListener('click', () => {
          const id = btn.getAttribute('data-id');
          const st = hits.find((h) => h.id === id);
          this._pickStop({ id: st.id, name: st.name, label: st.label }, which);
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._error(errText(this._lang(), err));
    }
  }

  /* Rank the reachable destinations by how much of the query they share, so an
     unreachable search still ends with something usable. */
  _suggestDests(f) {
    const dests = this._dests || [];
    if (!dests.length) return [];
    if (!f || f.length < 3) return dests.slice(0, 6);
    const grams = new Set();
    for (let i = 0; i + 3 <= f.length; i++) grams.add(f.slice(i, i + 3));
    const scored = dests.map((d) => {
      const fold = BkkLib.fold(d.name);
      let score = 0;
      grams.forEach((g) => { if (fold.indexOf(g) >= 0) score += 1; });
      return { d, score };
    }).filter((x) => x.score > 0);
    scored.sort((a, b) => b.score - a.score);
    const picked = scored.slice(0, 6).map((x) => x.d);
    return picked.length ? picked : dests.slice(0, 6);
  }

  _paintDestHits(q) {
    const box = this._el('destHits');
    if (!box) return;
    const copy = this._modeCopy(this._mode());
    if (!this._config.stopId) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(copy.destNeedOrigin) + '</span>';
      return;
    }
    if (this._destsLoading) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), 'destLoading')) + '</span>';
      return;
    }
    if (!this._dests.length) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(copy.destNone) + '</span>';
      return;
    }
    const f = BkkLib.fold(q || '');
    const hits = f
      ? this._dests.filter((d) => BkkLib.fold(d.name).indexOf(f) >= 0)
      : this._dests;
    if (!hits.length) {
      /* A stop can exist and still be unreachable from this origin, so name a
         few destinations that do have a direct service. */
      const alts = this._suggestDests(f);
      const msg = alts.length
        ? t(this._lang(), 'destEmptySuggest', alts.map((d) => d.name).join(', '))
        : copy.destEmpty;
      box.innerHTML = '<span class="hint">' + BkkLib.esc(msg) + '</span>';
      return;
    }
    const shown = hits.slice(0, 50);
    box.innerHTML = shown.map((s) => (
      `<button class="hit" type="button" data-key="${BkkLib.esc(s.key)}">${BkkLib.esc(s.name)}</button>`
    )).join('');
    box.querySelectorAll('.hit').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-key');
        const st = this._dests.find((d) => d.key === key);
        if (!st) return;
        this._pickStop({ id: st.id, name: st.name, key: st.key }, 'dest');
      });
    });
  }

  async _loadDests() {
    if (!this._config.stopId) return;
    if (!this._config.apiKey && this._mode() !== 'volan' && this._mode() !== 'helyi') return;
    const stopId = this._config.stopId;
    const seq = ++this._destSeq;
    this._destsLoading = true;
    const box = this._el('destHits');
    if (box) box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), 'destLoading')) + '</span>';
    try {
      this._error('');
      const idx = await BkkLib.reachableDests(
        this._config.apiKey,
        this._cache,
        { id: this._config.stopId, name: this._config.stopName },
        this._mode(),
        this._config.city,
      );
      if (seq !== this._destSeq || this._config.stopId !== stopId) return;
      this._loadedStop = stopId;
      this._dests = idx.dests || [];
      this._destRoutes = idx.destRoutes || {};
      if (this._config.destKey && this._destRoutes[this._config.destKey]) {
        const ids = this._destRoutes[this._config.destKey].slice();
        const cur = this._config.routeIds || [];
        const same = ids.length === cur.length && ids.every((id) => cur.indexOf(id) >= 0);
        if (!same) this._emit({ routeIds: ids });
      }
      const dq = this._el('dq');
      this._paintDestHits(dq ? dq.value.trim() : '');
    } catch (err) {
      if (seq !== this._destSeq) return;
      this._loadedStop = '';
      this._error(errText(this._lang(), err));
      if (box) box.innerHTML = '';
    } finally {
      if (seq === this._destSeq) this._destsLoading = false;
    }
  }

  _pickStop(stop, which) {
    if (which === 'dest') {
      const box = this._el('destHits');
      const dq = this._el('dq');
      if (box) box.innerHTML = '';
      if (dq) dq.value = stop.label || stop.name;
      const key = stop.key || BkkLib.norm(stop.name);
      const patch = {
        destKey: key,
        destName: stop.name,
        routeIds: (this._destRoutes[key] || []).slice(),
      };
      if (!this._config.name && this._config.stopName) {
        patch.name = `${this._config.stopName} \u2192 ${stop.name}`;
      }
      this._emit(patch);
      this._syncDestLabel();
      return;
    }
    const box = this._el('hits');
    const q = this._el('q');
    if (box) box.innerHTML = '';
    if (q) q.value = stop.label || stop.name;
    this._cache = {};
    this._dests = [];
    this._destRoutes = {};
    this._loadedStop = '';
    const patch = {
      stopId: stop.id,
      stopName: stop.name,
      routeIds: [],
      destKey: '',
      destName: '',
    };
    this._emit(patch);
    this._syncStopLabel();
    this._syncDestLabel();
    const dq = this._el('dq');
    if (dq) dq.value = '';
    /* setConfig, called after config-changed, loads destinations once. */
  }
}


class BKKPlannerCardEditor extends BKKHopCardEditor {
  _mode() { return 'all'; }

  setConfig(config) {
    super.setConfig(config);
    this._hideModeControls();
  }

  _hideModeControls() {
    const row = this._el('modeSwitches');
    if (row) row.style.display = 'none';
    const city = this._el('cityWrap');
    if (city) city.style.display = 'none';
  }

  _syncMode() {
    super._syncMode();
    this._hideModeControls();
    const hint = this._el('modeHint');
    if (hint) hint.textContent = t(this._lang(), 'plannerPickAll');
    const lblStop = this._el('lblStop');
    if (lblStop) lblStop.textContent = t(this._lang(), 'plannerStep1');
    const lblDest = this._el('lblDest');
    if (lblDest) lblDest.textContent = t(this._lang(), 'plannerStep2');
  }
}


/* `bkk-stop-card-r3` and its editor stay registered so dashboards written
   before the rename keep working. */
if (!customElements.get(BKK_HOP_EDITOR)) {
  customElements.define(BKK_HOP_EDITOR, BKKHopCardEditor);
}
if (!customElements.get(BKK_HOP_EDITOR_ALIAS)) {
  customElements.define(BKK_HOP_EDITOR_ALIAS, class extends BKKHopCardEditor {});
}
if (!customElements.get(BKK_PLANNER_EDITOR)) {
  customElements.define(BKK_PLANNER_EDITOR, BKKPlannerCardEditor);
}
if (!customElements.get(BKK_PLANNER_EDITOR_ALIAS)) {
  customElements.define(BKK_PLANNER_EDITOR_ALIAS, class extends BKKPlannerCardEditor {});
}
if (!customElements.get(BKK_HOP_TAG)) {
  customElements.define(BKK_HOP_TAG, BKKHopCard);
}
if (!customElements.get(BKK_HOP_TAG_ALIAS)) {
  customElements.define(BKK_HOP_TAG_ALIAS, class extends BKKHopCard {});
}
if (!customElements.get(BKK_PLANNER_TAG)) {
  customElements.define(BKK_PLANNER_TAG, BKKPlannerCard);
}
if (!customElements.get(BKK_PLANNER_TAG_ALIAS)) {
  customElements.define(BKK_PLANNER_TAG_ALIAS, class extends BKKPlannerCard {});
}

console.info(`%c HUNGARIAN-TRANSPORT %c ${CARD_VERSION} `,
  'color:#fff;background:#0f6fc6;font-weight:700',
  'color:#0f6fc6;background:#fff;font-weight:700');

globalThis.HungarianTransportLib = BkkLib;

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === BKK_HOP_TAG)) {
  window.customCards.push({
    type: BKK_HOP_TAG,
    name: 'Hungarian transport',
    description: 'BKK, M\u00c1V, Vol\u00e1n and local-city departures between two stops',
    preview: false,
  });
}
if (!window.customCards.some((c) => c.type === BKK_PLANNER_TAG)) {
  window.customCards.push({
    type: BKK_PLANNER_TAG,
    name: 'Hungarian transit stop planner',
    description: 'Pick two stops; every BKK, Vol\u00e1n and M\u00c1V service between them is listed',
    preview: false,
  });
}

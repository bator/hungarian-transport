const CARD_VERSION = '1.3.3-rev.1';

const BKK_PLANNER_TAG = 'bkk-stop-card-plan';
const BKK_API = 'https://go.bkk.hu/api/query/v1/ws/otp/api/where';
/* Default departure look-ahead. Override per card with `minutesAfter`.
   The Kelenfold-Szekesfehervar hop lists trains past 2h, so 180 is the
   floor that still fills travelMin on those rows. */
const DEFAULT_MINUTES_AFTER = 180;
const MIN_MINUTES_AFTER = 15;
const MAX_MINUTES_AFTER = 360;
const MINUTES_AFTER_PRESETS = [30, 60, 90, 120, 180, 240, 360];
const DEPART_MINUTES_AFTER = String(DEFAULT_MINUTES_AFTER);

function clampMinutesAfter(value) {
  if (value == null || value === '') return DEFAULT_MINUTES_AFTER;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MINUTES_AFTER;
  return Math.min(MAX_MINUTES_AFTER, Math.max(MIN_MINUTES_AFTER, Math.round(n)));
}

function maxDepartureRows(horizon) {
  return Math.min(40, Math.max(12, Math.round(clampMinutesAfter(horizon) / 2.5)));
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
const FAVORITES = BKK_FAVORITES.concat(MAV_FAVORITES);
const DEFAULT_FAVORITES = {
  bkk: BKK_FAVORITES,
  mav: MAV_FAVORITES,
  volan: VOLAN_FAVORITES,
  helyi: [],
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

/* Assets live next to this module, so the card works from /hacsfiles/, /local/
   or wherever the Lovelace resource points. Requires `type: module`. */
const ASSET_BASE = new URL('.', import.meta.url).href;
let VOLAN_INDEX_OVERRIDE = '';
let CITY_INDEX_OVERRIDE = '';

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
    minutesAfterHint: 'H\u00e1ny percet n\u00e9zzen el\u0151re az indul\u00e1sokb\u00f3l. 30\u2013360 perc, alap\u00e9rtelmez\u00e9s 180.',
    plannerTitle: 'BKK \u00fatvonal',
    plannerStep1: '1. Meg\u00e1ll\u00f3',
    plannerStep2: '2. J\u00e1rm\u0171',
    plannerStep3: '3. Meddig',
    plannerDepartures: 'Indul\u00e1sok',
    plannerSearchPlaceholder: 'Utca, meg\u00e1ll\u00f3...',
    plannerReset: '\u00daj',
    plannerMultiHint: 'T\u00f6bbet is v\u00e1laszthatsz. Ekkor csak a k\u00f6z\u00f6s meg\u00e1ll\u00f3k maradnak.',
    plannerPickVehicleFirst: 'El\u0151bb v\u00e1lassz j\u00e1rm\u0171vet',
    plannerPickAll: 'V\u00e1lassz meg\u00e1ll\u00f3t, j\u00e1rm\u0171vet \u00e9s c\u00e9lt.',
    plannerLoading: 'Bet\u00f6lt\u00e9s...',
    plannerNoSharedStop: 'Nincs k\u00f6z\u00f6s meg\u00e1ll\u00f3',
    plannerNoSharedLater: 'Ezeknek a j\u00e1ratoknak nincs k\u00f6z\u00f6s k\u00e9s\u0151bbi meg\u00e1ll\u00f3ja.',
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
    minutesAfterHint: 'How far ahead to list departures. 30\u2013360 minutes, default 180.',
    plannerTitle: 'BKK route',
    plannerStep1: '1. Stop',
    plannerStep2: '2. Vehicle',
    plannerStep3: '3. Destination',
    plannerDepartures: 'Departures',
    plannerSearchPlaceholder: 'Street, stop...',
    plannerReset: 'Reset',
    plannerMultiHint: 'You can pick several. Only stops shared by all of them are kept.',
    plannerPickVehicleFirst: 'Pick a vehicle first',
    plannerPickAll: 'Pick a stop, a vehicle and a destination.',
    plannerLoading: 'Loading...',
    plannerNoSharedStop: 'No shared stop',
    plannerNoSharedLater: 'These routes share no later stop.',
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

class BKKPlannerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._stop = null;
    this._routes = [];
    this._selected = new Set();
    this._dests = [];
    this._dest = null;
    this._rows = [];
    this._searchTimer = null;
    this._searchSeq = 0;
    this._poll = null;
    this._routeCache = {};
  }

  disconnectedCallback() {
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
  }

  static async getConfigElement() {
    return null;
  }

  static getStubConfig() {
    return { apiKey: '', name: '', language: 'auto', minutesAfter: DEFAULT_MINUTES_AFTER };
  }

  setConfig(config) {
    if (!this.shadowRoot) {
      try { this.attachShadow({ mode: 'open' }); } catch (_e) { /* already attached */ }
    }
    this._config = Object.assign({}, config || {});
    this._apiKey = this._config.apiKey || '';
    this._renderShell();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._config || !this._root) return;
    // "auto" resolves against hass.language; re-render only while nothing is picked.
    if (first && !this._stop && this._lang() !== resolveLang(this._config.language, null)) {
      this._renderShell();
    }
  }

  _lang() {
    return resolveLang((this._config || {}).language, this._hass);
  }

  _minutesAfter() {
    return clampMinutesAfter(this._config && this._config.minutesAfter);
  }

  getCardSize() {
    return 8;
  }

  _renderShell() {
    const root = this.shadowRoot;
    if (!root || !this._config) return;
    while (root.firstChild) root.removeChild(root.firstChild);
    const style = document.createElement('style');
    style.textContent = `
      :host { font-size: 13px; }
      ha-card { overflow: visible; }
      .wrap { padding: 10px 12px 12px; }
      .title { font-weight: 700; margin-bottom: 10px; }
      .step { margin-bottom: 12px; }
      .label { font-size: 11px; font-weight: 700; letter-spacing: 0.03em;
        color: var(--secondary-text-color); margin-bottom: 6px; text-transform: uppercase; }
      .hint { font-size: 11px; color: var(--secondary-text-color); margin: 4px 0 0; }
      input, select {
        width: 100%; box-sizing: border-box; font: inherit;
        background: var(--secondary-background-color, rgba(128,128,128,0.15));
        color: var(--primary-text-color); border: 1px solid var(--divider-color);
        border-radius: 8px; padding: 8px 10px;
      }
      .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
      .chip {
        border: 1px solid var(--divider-color); background: transparent;
        color: var(--primary-text-color); border-radius: 999px;
        padding: 4px 10px; cursor: pointer; font: inherit; font-size: 12px;
      }
      .chip.on { color: #fff; border-color: transparent; }
      .chip:disabled { opacity: 0.4; cursor: default; }
      .list { max-height: 180px; overflow: auto; margin-top: 6px; }
      .hit {
        display: block; width: 100%; text-align: left; font: inherit;
        background: transparent; color: var(--primary-text-color);
        border: 0; border-bottom: 1px solid var(--divider-color);
        padding: 8px 4px; cursor: pointer;
      }
      .hit:hover { background: var(--secondary-background-color); }
      .picked { font-weight: 700; margin-top: 4px; }
      table { width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 8px; }
      th { font-size: 10px; text-align: right; color: var(--secondary-text-color); padding: 4px; }
      th:first-child, th:nth-child(2), td:first-child, td:nth-child(2) { text-align: left; }
      td { padding: 6px 4px; font-variant-numeric: tabular-nums; }
      tbody tr:nth-child(odd) { background: var(--secondary-background-color); }
      .route { font-weight: 800; }
      .muted { color: var(--secondary-text-color); }
      .row { display: flex; gap: 8px; align-items: center; }
      .row button.clear {
        flex: 0 0 auto; border: 1px solid var(--divider-color);
        background: transparent; color: var(--primary-text-color);
        border-radius: 8px; padding: 8px 10px; cursor: pointer; font: inherit;
      }
      .err { color: var(--error-color, #e66); font-size: 12px; margin-top: 8px; }
    `;
    const card = document.createElement('ha-card');
    const wrap = document.createElement('div');
    wrap.className = 'wrap';
    const lang = this._lang();
    const tr = (key) => this._esc(t(lang, key));
    wrap.innerHTML = `
      <div class="title">${this._esc(this._config.name || t(lang, 'plannerTitle'))}</div>
      <div class="step">
        <div class="label">${tr('plannerStep1')}</div>
        <div class="row">
          <input id="q" type="search" placeholder="${tr('plannerSearchPlaceholder')}">
          <button class="clear" id="reset" type="button">${tr('plannerReset')}</button>
        </div>
        <div class="chips" id="fav"></div>
        <div class="picked" id="stopPicked"></div>
        <div class="list" id="hits"></div>
      </div>
      <div class="step">
        <div class="label">${tr('plannerStep2')}</div>
        <div class="hint">${tr('plannerMultiHint')}</div>
        <div class="chips" id="routes"></div>
      </div>
      <div class="step">
        <div class="label">${tr('plannerStep3')}</div>
        <select id="dest" disabled><option value="">${tr('plannerPickVehicleFirst')}</option></select>
      </div>
      <div class="step">
        <div class="label">${tr('plannerDepartures')}</div>
        <div id="table" class="muted">${tr('plannerPickAll')}</div>
      </div>
      <div class="err" id="err"></div>
    `;
    card.appendChild(wrap);
    root.appendChild(style);
    root.appendChild(card);
    this._root = root;
    this._bind();
    this._paintFav();
  }

  _bind() {
    const q = this._el('q');
    q.addEventListener('input', () => {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this._search(q.value.trim()), 280);
    });
    this._el('reset').addEventListener('click', () => this._reset());
    this._el('dest').addEventListener('change', (ev) => {
      const v = ev.target.value;
      this._dest = this._dests.find((d) => d.key === v) || null;
      this._loadDepartures();
    });
  }

  _el(id) { return this._root.getElementById(id); }

  _esc(s) {
    return BkkLib.esc(s);
  }

  _paintFav() {
    const box = this._el('fav');
    box.innerHTML = '';
    FAVORITES.forEach((f) => {
      const b = document.createElement('button');
      b.className = 'chip';
      b.type = 'button';
      b.textContent = f.name.split(' / ')[0];
      b.addEventListener('click', () => this._pickStop(f));
      box.appendChild(b);
    });
  }

  _reset() {
    this._stop = null;
    this._routes = [];
    this._selected = new Set();
    this._dests = [];
    this._dest = null;
    this._rows = [];
    this._el('q').value = '';
    this._el('hits').innerHTML = '';
    this._el('stopPicked').textContent = '';
    this._el('routes').innerHTML = '';
    const lang = this._lang();
    this._el('dest').innerHTML = `<option value="">${this._esc(t(lang, 'plannerPickVehicleFirst'))}</option>`;
    this._el('dest').disabled = true;
    this._el('table').textContent = t(lang, 'plannerPickAll');
    this._el('err').textContent = '';
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
  }

  _error(msg) { this._el('err').textContent = msg || ''; }

  async _bkk(path, params) {
    if (!this._apiKey) throw codedError('errNoApiKey');
    const q = new URLSearchParams(Object.assign({
      key: this._apiKey, version: '4', appVersion: 'apiary-1.0',
    }, params));
    const res = await fetch(`${BKK_API}/${path}?${q}`);
    if (!res.ok) throw new Error(`BKK HTTP ${res.status}`);
    const data = await res.json();
    if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
    return data;
  }

  _fold(s) {
    return this._norm(s)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  _aliasQueries(q) {
    const f = this._fold(q);
    const extra = [];
    if (f.includes('arpad hid')) extra.push('Goncz Arpad varoskozpont');
    if (f.includes('moszkva')) extra.push('Szell Kalman ter');
    return extra;
  }

  _labelAliased(q, hit) {
    const f = this._fold(q);
    const n = this._fold(hit.name);
    if (f.includes('arpad hid') && n === 'goncz arpad varoskozpont') {
      return 'G\u00f6ncz \u00c1rp\u00e1d v\u00e1rosk\u00f6zpont (M3, \u00c1rp\u00e1d h\u00edd)';
    }
    return hit.name;
  }

  async _search(q) {
    if (!q || q.length < 2) { this._el('hits').innerHTML = ''; return; }
    const seq = ++this._searchSeq;
    try {
      this._error('');
      const byId = new Map();
      const queries = [q].concat(this._aliasQueries(q));
      for (let i = 0; i < queries.length; i++) {
        const data = await this._bkk('search.json', { query: queries[i] });
        if (seq !== this._searchSeq) return;
        const stops = (((data.data || {}).references) || {}).stops || {};
        Object.values(stops).forEach((s) => {
          if (s && s.id && s.name) byId.set(s.id, s);
        });
      }
      if (seq !== this._searchSeq) return;
      let hits = BkkLib.groupStops(Array.from(byId.values()));
      hits = hits.map((h) => Object.assign({}, h, { label: this._labelAliased(q, h) }));
      const foldedQ = this._fold(q);
      hits.sort((a, b) => {
        const as = (foldedQ.includes('arpad hid') && this._fold(a.name).includes('goncz arpad')) ? 0 : 1;
        const bs = (foldedQ.includes('arpad hid') && this._fold(b.name).includes('goncz arpad')) ? 0 : 1;
        if (as !== bs) return as - bs;
        return (a.label || a.name).localeCompare(b.label || b.name, 'hu');
      });
      hits = hits.slice(0, 20);
      const box = this._el('hits');
      box.innerHTML = hits.map((s) => (
        `<button class="hit" type="button" data-id="${this._esc(s.id)}">${this._esc(s.label || s.name)}</button>`
      )).join('');
      box.querySelectorAll('.hit').forEach((btn) => {
        btn.addEventListener('click', () => {
          const id = btn.getAttribute('data-id');
          const st = hits.find((h) => h.id === id);
          this._pickStop({ id: st.id, name: st.name, label: st.label });
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._error(errText(this._lang(), err));
    }
  }

  async _pickStop(stop) {
    this._stop = stop;
    this._selected = new Set();
    this._dest = null;
    this._el('q').value = stop.label || stop.name;
    this._el('hits').innerHTML = '';
    this._el('stopPicked').textContent = stop.label || stop.name;
    this._el('routes').innerHTML = '<span class="muted">' + this._esc(t(this._lang(), 'plannerLoading')) + '</span>';
    this._el('dest').disabled = true;
    try {
      this._error('');
      await this._loadRoutes();
      this._paintRoutes();
      await this._refreshDests();
    } catch (err) {
      this._error(errText(this._lang(), err));
    }
  }

  async _loadRoutes() {
    const data = await this._bkk('arrivals-and-departures-for-stop.json', {
      stopId: this._stop.id,
      minutesAfter: String(this._minutesAfter()),
      minutesBefore: '0',
      onlyDepartures: 'true',
      includeReferences: 'true',
    });
    const refs = (data.data || {}).references || {};
    const routes = refs.routes || {};
    const entry = (data.data || {}).entry || {};
    const ids = entry.routeIds || Object.keys(routes);
    const list = [];
    ids.forEach((rid) => {
      const rt = routes[rid];
      if (!rt) return;
      const label = rt.iconDisplayText || rt.shortName;
      if (!label) return;
      list.push({
        id: rid,
        label,
        color: '#' + String(rt.color || '4477aa').replace('#', ''),
        text: '#' + String(rt.textColor || 'ffffff').replace('#', ''),
        type: rt.type || '',
      });
    });
    list.sort((a, b) => a.label.localeCompare(b.label, 'hu', { numeric: true }));
    this._routes = list;
    this._routeCache = {};
  }

  _paintRoutes() {
    const box = this._el('routes');
    box.innerHTML = '';
    if (!this._routes.length) {
      box.innerHTML = '<span class="muted">' + this._esc(t(this._lang(), 'plannerNoRoutes')) + '</span>';
      return;
    }
    this._routes.forEach((rt) => {
      const b = document.createElement('button');
      b.className = 'chip' + (this._selected.has(rt.id) ? ' on' : '');
      b.type = 'button';
      b.textContent = rt.label;
      if (this._selected.has(rt.id)) {
        b.style.background = rt.color;
        b.style.color = rt.text;
      }
      b.addEventListener('click', async () => {
        if (this._selected.has(rt.id)) this._selected.delete(rt.id);
        else this._selected.add(rt.id);
        this._paintRoutes();
        await this._refreshDests();
      });
      box.appendChild(b);
    });
  }

  _norm(name) {
    let t = String(name || '').normalize('NFC');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/[\u00A0\u202F]/g, ' ');
    t = t.trim().toLowerCase();
    t = t.replace(/[\u2013\u2014]/g, '-');
    t = t.replace(/\s+/g, ' ');
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.'].forEach((sfx) => {
      if (t.endsWith(sfx)) t = t.slice(0, -sfx.length).trim();
    });
    return t;
  }

  _isStopArea(stop) {
    if (!stop) return false;
    if (stop.locationType === 1) return true;
    return /_CS/i.test(String(stop.id || ''));
  }

  _groupStops(stops) {
    const byName = new Map();
    (stops || []).forEach((s) => {
      if (!s || !s.id || !s.name) return;
      const key = this._norm(s.name);
      if (!key) return;
      if (!byName.has(key)) byName.set(key, []);
      byName.get(key).push(s);
    });
    const hits = [];
    byName.forEach((group) => {
      const area = group.find((s) => this._isStopArea(s));
      const withParent = group.find((s) => s.parentStationId);
      const pick = area || withParent || group[0];
      hits.push({
        id: area ? area.id : (pick.parentStationId || pick.id),
        name: pick.name,
      });
    });
    return hits;
  }

  _uniqueByName(items) {
    const seen = new Set();
    const out = [];
    (items || []).forEach((d) => {
      const key = d.key || this._norm(d.name);
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push(d);
    });
    return out;
  }

  async _routeStops(routeId) {
    if (this._routeCache[routeId]) return this._routeCache[routeId];
    const data = await this._bkk('route-details.json', {
      routeId,
      includeReferences: 'true',
    });
    const entry = (data.data || {}).entry || {};
    const stops = ((data.data || {}).references || {}).stops || {};
    const variants = (entry.variants || []).map((v) => ({
      name: v.name,
      ids: v.stopIds || [],
    }));
    this._routeCache[routeId] = { variants, stops };
    return this._routeCache[routeId];
  }

  _sameStop(id, originId) {
    if (!id || !originId) return false;
    if (id === originId) return true;
    const base = (s) => String(s).replace(/^BKK_/, '').split('_')[0];
    return base(id) === base(originId);
  }

  async _remainingNames(routeId) {
    const { variants, stops } = await this._routeStops(routeId);
    const origin = this._stop.id;
    const names = [];
    const seen = new Set();
    variants.forEach((v) => {
      let idx = v.ids.findIndex((id) => this._sameStop(id, origin));
      if (idx < 0) {
        idx = v.ids.findIndex((id) => BkkLib.nameEq((stops[id] || {}).name, this._stop.name));
      }
      if (idx < 0) return;
      v.ids.slice(idx + 1).forEach((id) => {
        const raw = (stops[id] || {}).name;
        const n = BkkLib.stationKey(raw) || this._norm(raw);
        if (!raw || !n || seen.has(n)) return;
        seen.add(n);
        names.push({ key: n, name: raw, id });
      });
    });
    return names;
  }

  async _refreshDests() {
    const sel = this._el('dest');
    const lang = this._lang();
    const opt = (key) => `<option value="">${this._esc(t(lang, key))}</option>`;
    this._dest = null;
    if (!this._selected.size) {
      sel.innerHTML = opt('plannerPickVehicleFirst');
      sel.disabled = true;
      this._el('table').textContent = t(lang, 'plannerPickAll');
      return;
    }
    sel.innerHTML = opt('plannerLoading');
    const lists = [];
    for (const rid of this._selected) {
      lists.push(await this._remainingNames(rid));
    }
    let common = lists[0] || [];
    for (let i = 1; i < lists.length; i++) {
      const keys = new Set(lists[i].map((x) => x.key));
      common = common.filter((x) => keys.has(x.key));
    }
    this._dests = this._uniqueByName(common);
    common = this._dests;
    if (!common.length) {
      sel.innerHTML = opt('plannerNoSharedStop');
      sel.disabled = true;
      this._el('table').textContent = t(lang, 'plannerNoSharedLater');
      return;
    }
    sel.disabled = false;
    sel.innerHTML = opt('plannerPickDest') + common.map((d) => (
      `<option value="${this._esc(d.key)}">${this._esc(d.name)}</option>`
    )).join('');
  }

  async _loadDepartures() {
    if (!this._stop || !this._dest || !this._selected.size) return;
    this._el('table').innerHTML = '<span class="muted">' + this._esc(t(this._lang(), 'plannerLoading')) + '</span>';
    try {
      this._error('');
      const rows = await BkkLib.departures(
        this._apiKey,
        this._stop.id,
        Array.from(this._selected),
        this._dest,
        {
          originName: this._stop.name || '',
          cache: this._routeCache || {},
          mode: 'bkk',
          minutesAfter: this._minutesAfter(),
        },
      );
      this._rows = rows || [];
      this._paintTable();
    } catch (err) {
      this._error(errText(this._lang(), err));
    } finally {
      /* Keep polling after a failure, otherwise one hiccup freezes the card. */
      if (this._poll) clearInterval(this._poll);
      this._poll = setInterval(() => this._loadDepartures(), 45000);
    }
  }

  async _travelMin(tripId, dep) {
    if (!tripId || !this._dest) return null;
    try {
      const data = await this._bkk('trip-details.json', { tripId });
      const sts = (((data.data || {}).entry) || {}).stopTimes || [];
      const stops = (((data.data || {}).references) || {}).stops || {};
      const destN = this._dest.key;
      const dest = sts.find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destN));
      if (!dest) return null;
      const arr = dest.predictedArrivalTime || dest.arrivalTime || dest.departureTime;
      const from = dep;
      if (!arr || !from) return null;
      const mins = Math.round((arr - from) / 60);
      return (mins >= 1 && mins <= 1440) ? mins : null;
    } catch (_e) {
      return null;
    }
  }

  _hm(ts) {
    return BkkLib.hm(ts);
  }

  _inMin(ts) {
    if (!ts) return '';
    const lang = this._lang();
    const m = Math.round((ts * 1000 - Date.now()) / 60000);
    if (m < 0) return t(lang, 'now');
    return t(lang, 'minutes', m);
  }

  _paintTable() {
    const box = this._el('table');
    const lang = this._lang();
    if (!this._rows.length) {
      box.textContent = t(lang, 'plannerNoDepartures');
      return;
    }
    box.innerHTML = `
      <table>
        <thead><tr>
          <th></th><th></th>
          <th>${this._esc(t(lang, 'colArrival'))}</th>
          <th>${this._esc(t(lang, 'colExpected'))}</th>
          <th>${this._esc(t(lang, 'colTravel'))}</th>
        </tr></thead>
        <tbody>
          ${this._rows.map((r) => `
            <tr>
              <td class="route" style="color:${this._esc(r.color)}">${this._esc(r.label)}</td>
              <td>${this._esc(this._dest.name)}</td>
              <td>${this._esc(this._inMin(r.dep))}</td>
              <td>${this._esc(this._hm(r.dep))}</td>
              <td class="muted">${r.travel ? this._esc(t(lang, 'minutes', r.travel)) : ''}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }
}


const BKK_HOP_TAG = 'hungarian-transport-card';
const BKK_HOP_EDITOR = 'hungarian-transport-card-editor';
const BKK_HOP_TAG_ALIAS = 'bkk-stop-card-r3';
const BKK_HOP_EDITOR_ALIAS = 'bkk-stop-card-r3-editor';
const OWN_CARD_TYPES = [BKK_HOP_TAG, BKK_HOP_TAG_ALIAS, BKK_PLANNER_TAG];
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
    return !BkkLib.isMavRoute(rt) && !BkkLib.isVolanRoute(rt);
  },
  stopNum(id) {
    const s = String(id || '');
    const m = s.match(/^(?:volan_|AREA_CS|hkir_)(\d+)/i);
    if (m) return m[1];
    const n = s.match(/(\d{5,})/);
    return n ? n[1] : '';
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
  async fetch(apiKey, path, params) {
    if (!apiKey) throw codedError('errNoApiKey');
    const q = new URLSearchParams(Object.assign({
      key: apiKey, version: '4', appVersion: 'apiary-1.0',
    }, params));
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 20000) : null;
    let res;
    try {
      res = await fetch(`${BKK_API}/${path}?${q}`, { signal: ctrl ? ctrl.signal : undefined });
    } catch (err) {
      throw new Error(err && err.name === 'AbortError' ? 'BKK timeout' : `BKK ${err}`);
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!res.ok) throw new Error(`BKK HTTP ${res.status}`);
    const data = await res.json();
    if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
    return data;
  },
  async probeApiKey(apiKey) {
    if (!apiKey) return { ok: false, code: 'errNoApiKey' };
    try {
      await BkkLib.fetch(apiKey, 'search.json', { query: '.' });
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
    const byKey = new Map();
    const destRoutes = {};
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      const t = trips[i];
      const route = t[1];
      const ps = t[2] || [];
      const oidx = ps.indexOf(origin.i);
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
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold((dest && dest.name) || destKey || '');
    const horizon = clampMinutesAfter(minutesAfter);
    const rows = [];
    const trips = idx.t || [];
    /* -1 catches yesterday's 24:xx trips, which are still tonight's buses. */
    const dayOffsets = [-1, 0, 1];
    for (let n = 0; n < dayOffsets.length && rows.length < 12; n++) {
      const day = BkkLib.volanDay(dayOffsets[n]);
      for (let i = 0; i < trips.length; i++) {
        const t = trips[i];
        if (!BkkLib.volanServiceOk(idx, t[0], day.ymd, day.dow)) continue;
        const route = t[1];
        const ps = t[2] || [];
        const ms = t[3] || [];
        const oidx = ps.indexOf(origin.i);
        if (oidx < 0) continue;
        const depMin = ms[oidx];
        if (depMin == null || depMin < day.mins - 1) continue;
        if (depMin > day.mins + horizon) continue;
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
        rows.push({
          tripId: 'gtfs:' + route + ':' + day.ymd + ':' + depMin + ':' + origin.i,
          label: route,
          color: color,
          text: text,
          vehicle: vehicle,
          rawType: rawType,
          dep: day.midnight + depMin * 60,
          sched: day.midnight + depMin * 60,
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
    return uniq.slice(0, 12);
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
  mergeVolanRows(gtfs, otp) {
    const out = [];
    const seen = new Set();
    const keyOf = (row) => `${row.label}|${Math.round(Number(row.sched || row.dep || 0) / 60)}`;
    (otp || []).forEach((row) => {
      const k = keyOf(row);
      if (seen.has(k)) return;
      seen.add(k);
      out.push(row);
    });
    (gtfs || []).forEach((row) => {
      const k = keyOf(row);
      if (seen.has(k)) return;
      seen.add(k);
      out.push(row);
    });
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    return out.slice(0, 12);
  },
  async searchStops(apiKey, q, mode, city) {
    const byId = new Map();
    if (mode === 'volan') {
      try {
        (await BkkLib.volanSearchStops(q)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
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
          const data = await BkkLib.fetch(apiKey, 'search.json', { query: queries[i] });
          const stops = (((data.data || {}).references) || {}).stops || {};
          Object.values(stops).forEach((s) => {
            if (!s || !s.id || !s.name) return;
            if (mode === 'mav' && !BkkLib.isMavStop(s)) return;
            if (mode === 'volan' && !BkkLib.isVolanStop(s)) return;
            if (mode === 'bkk' && !BkkLib.isBkkStop(s)) return;
            const num = BkkLib.stopNum(s.id);
            if (mode === 'volan' && num) {
              const clash = Array.from(byId.values()).find((x) => BkkLib.stopNum(x.id) === num);
              if (clash) return;
            }
            byId.set(s.id, s);
          });
        }
      } catch (err) {
        if (mode !== 'volan' || !byId.size) throw err;
      }
    }
    let hits = BkkLib.groupStops(Array.from(byId.values()));
    hits = hits.map((h) => Object.assign({}, h, { label: BkkLib.labelAliased(q, h) }));
    const foldedQ = BkkLib.fold(q);
    hits.sort((a, b) => {
      const as = (foldedQ.includes('arpad hid') && BkkLib.fold(a.name).includes('goncz arpad')) ? 0 : 1;
      const bs = (foldedQ.includes('arpad hid') && BkkLib.fold(b.name).includes('goncz arpad')) ? 0 : 1;
      if (as !== bs) return as - bs;
      return (a.label || a.name).localeCompare(b.label || b.name, 'hu');
    });
    return hits.slice(0, 20);
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
    if (cache && cache[routeId] && cache[routeId].variants) return cache[routeId];
    const data = await BkkLib.fetch(apiKey, 'route-details.json', {
      routeId,
      includeReferences: 'true',
    });
    const entry = (data.data || {}).entry || {};
    const packed = {
      variants: (entry.variants || []).map((v) => ({
        name: v.name,
        ids: v.stopIds || [],
      })),
      stops: ((data.data || {}).references || {}).stops || {},
    };
    return BkkLib.cachePut(cache, routeId, packed);
  },
  async originPoles(apiKey, cache, origin, routeIds) {
    const poles = new Set();
    const routes = (routeIds || []).filter(Boolean).slice(0, 32);
    for (let i = 0; i < routes.length; i++) {
      try {
        const { variants, stops } = await BkkLib.loadRoutePattern(apiKey, cache, routes[i]);
        (variants || []).forEach((v) => {
          (v.ids || []).forEach((id) => {
            if (BkkLib.stopMatchesOrigin(Object.assign({ id }, stops[id] || {}), origin)) {
              poles.add(id);
            }
          });
        });
      } catch (_e) { /* skip a broken route */ }
    }
    if (!poles.size && origin && BkkLib.isStopArea({ id: origin.id })) {
      (await BkkLib.childStopIds(apiKey, cache, origin.id)).forEach((id) => poles.add(id));
    }
    if (!poles.size && origin && origin.id) poles.add(origin.id);
    return Array.from(poles).slice(0, 16);
  },
  async childStopIds(apiKey, cache, parentId) {
    const key = 'children:' + parentId;
    if (cache && Array.isArray(cache[key])) return cache[key];
    const data = await BkkLib.fetch(apiKey, 'schedule-for-stop.json', {
      stopId: parentId,
      includeReferences: 'true',
    });
    const stops = ((data.data || {}).references || {}).stops || {};
    const ids = Object.values(stops)
      .filter((s) => s && s.id && s.parentStationId === parentId)
      .filter((s) => !/^STOP_/i.test(s.id) && !BkkLib.isMavStop(s) && !BkkLib.isVolanStop(s))
      .map((s) => s.id);
    return BkkLib.cachePut(cache, key, ids);
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
    if (cache && cache[key]) return cache[key];
    const data = await BkkLib.fetch(apiKey, 'trip-details.json', { tripId });
    const packed = {
      sts: (((data.data || {}).entry) || {}).stopTimes || [],
      stops: (((data.data || {}).references) || {}).stops || {},
    };
    return BkkLib.cachePut(cache, key, packed);
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
      });
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
      if (mode !== 'volan') throw err;
    }
    if (mode === 'volan') {
      try {
        out = BkkLib.mergeDestIndex(await BkkLib.volanReachableDests(stop), out);
      } catch (_e) { /* GTFS index optional */ }
    }
    return out;
  },
  async travelMin(apiKey, tripId, destKey, dep, origin, cache) {
    if (!tripId || !destKey || String(tripId).indexOf('gtfs:') === 0) return null;
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
      return BkkLib.cityDepartures(stopId, dest, opts.originName, opts.city, horizon);
    }
    const cache = opts.cache || {};
    const origin = { id: stopId, name: opts.originName || '' };
    const selected = new Set(routeIds || []);
    const destKey = dest && dest.key;
    const maxRows = maxDepartureRows(horizon);
    const now = Math.floor(Date.now() / 1000);
    const latest = now + horizon * 60;
    let candidates = [];
    try {
      let poles = [];
      try {
        poles = await BkkLib.originPoles(apiKey, cache, origin, Array.from(selected));
      } catch (_e) {
        poles = [];
      }
      if (!poles.length) poles = [stopId];
      const payloads = await Promise.all(poles.map(async (pid) => {
        try {
          return await BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
            stopId: pid,
            minutesAfter: String(horizon),
            minutesBefore: '0',
            onlyDepartures: 'true',
            includeReferences: 'true',
            includeVehicleFromTrip: 'true',
          });
        } catch (_e) {
          return null;
        }
      }));
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
          if (selected.size && mode !== 'volan' && !selected.has(rid)) return;
          const dep = st.predictedDepartureTime || st.departureTime;
          if (dep && dep > latest + 60) return;
          seenTrip.add(st.tripId);
          const fallbackColor = mode === 'volan' ? 'F9AB13' : '4477aa';
          const fallbackText = mode === 'volan' ? '000000' : 'ffffff';
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
          });
        });
      });
      candidates.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    } catch (err) {
      if (mode !== 'volan') throw err;
    }
    let picked = candidates;
    if (destKey && candidates.length) {
      picked = [];
      for (let i = 0; i < candidates.length && picked.length < maxRows; i += 16) {
        const chunk = candidates.slice(i, i + 16);
        const flags = await Promise.all(chunk.map((row) => (
          BkkLib.tripGoesTo(apiKey, cache, row.tripId, origin, destKey)
        )));
        flags.forEach((ok, j) => { if (ok) picked.push(chunk[j]); });
      }
      picked = picked.slice(0, maxRows);
    } else {
      picked = candidates.slice(0, maxRows);
    }
    let rows = picked.map((row) => Object.assign({ travel: null }, row));
    await Promise.all(rows.map(async (row) => {
      if (row.travel != null || String(row.tripId || '').indexOf('gtfs:') === 0) return;
      row.travel = await BkkLib.travelMin(apiKey, row.tripId, destKey, row.dep, origin, cache);
    }));
    if (mode === 'volan') {
      try {
        const extra = await BkkLib.volanDepartures(stopId, dest, opts.originName, horizon);
        rows = BkkLib.mergeVolanRows(extra, rows);
      } catch (err) {
        if (!rows.length) throw err;
      }
    }
    return rows;
  },

  trainNumberFromTripId(tripId) {
    const m = String(tripId || '').match(/^BKK_(\d+)(?:_|$)/);
    return m ? m[1] : '';
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
    });
    return rows;
  },

  /* One departure in the shape the card renders: display strings plus the raw
     epoch seconds, so the countdown can tick without refetching. */
  asRow(r, destName, lang) {
    const dep = r.dep;
    let type = String(r.rawType || r.vehicle || 'BUS').toUpperCase();
    if (type.indexOf('SUBURBAN') >= 0 || type === 'TRAIN' || type === 'RAILWAY') type = 'RAIL';
    let head = String(r.head || destName || '').trim();
    const plat = r.platform ? String(r.platform) : BkkLib.platformFromText(head);
    head = BkkLib.stripPlatform(head);
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
      textcolor: String(r.text || '').replace('#', ''),
      platform: plat || '',
      trainNumber: r.trainNumber || '',
      delay: Number(r.delay || 0),
      booking: !!r.booking,
      travelMin: r.travel,
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

  /* _reload is async, so its rejections need an owner. */
  _reloadSafe() {
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
  connectedCallback() {
    if (!this._painted) return;
    this._startTick();
    if (!this._poll) this._reloadSafe();
  }

  disconnectedCallback() {
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
      :host { display: block; }
      ha-card { overflow: visible; }
      .wrap { padding: 10px 12px 11px; }
      .head { font-weight: 700; margin-bottom: 7px; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
      .err { font-size: 12px; color: var(--error-color, #e66); margin-bottom: 6px; }
      .msg { font-size: 12px; color: var(--secondary-text-color); }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      tr + tr td { border-top: 1px solid var(--divider-color); }
      td { padding: 6px 3px; vertical-align: middle; }
      td.icon { width: 20px; color: var(--secondary-text-color); }
      td.icon ha-icon { --mdc-icon-size: 18px; display: block; }
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
    const rows = this._rows || [];
    if (rows.length) {
      this._elBody.innerHTML = `<table><tbody>${
        rows.map((r) => this._rowHtml(r, lang)).join('')
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

  _rowHtml(r, lang) {
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
    return `<tr>
      <td class="icon"><ha-icon icon="${esc(r.icon || 'mdi:bus')}"></ha-icon></td>
      <td class="route"><span class="badge" style="${esc(this._badgeStyle(r))}">${
      esc(r.label || '?')}</span></td>
      <td class="dest" title="${esc(r.headsign || '')}">${esc(r.headsign || '')}${num}</td>
      <td class="flags">${plat}${pictos.length ? `<span class="pictos">${pictos.join('')}</span>` : ''}</td>
      <td class="when"><span class="${atClass}" title="${esc(atTip)}">${
      esc(r.predicted_attime || r.attime || '')}</span>${
      sched ? `<span class="sched">${esc(sched)}</span>` : ''}</td>
      <td class="in">${esc(this._countdown(r.depTs, lang))}${travel}</td>
    </tr>`;
  }

  async _reload() {
    /* Overlapping reloads (config edits, reconnects) must not leave an orphan
       interval behind, so every run is tagged with the generation that owns it. */
    const gen = ++this._gen;
    const cfg = this._config || {};
    if (cfg.volanIndex) setVolanIndexUrl(cfg.volanIndex);
    if (cfg.cityIndex) setCityIndexUrl(cfg.cityIndex);
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    const ready = cfg.stopId && cfg.destKey && (cfg.apiKey || BkkLib.mode(cfg) === 'volan' || BkkLib.mode(cfg) === 'helyi');
    if (!ready) {
      this._rows = [];
      this._loading = false;
      this._paint();
      return;
    }
    const run = async () => {
      if (gen !== this._gen) return;
      try {
        const rows = await BkkLib.departures(
          cfg.apiKey, cfg.stopId, cfg.routeIds, { key: cfg.destKey, name: cfg.destName },
          {
            originName: cfg.stopName || '',
            cache: this._tripCache || {},
            mode: BkkLib.mode(cfg),
            city: cfg.city || '',
            minutesAfter: cfg.minutesAfter,
          },
        );
        if (gen !== this._gen) return;
        this._rawRows = rows || [];
        BkkLib.enrichFromHass(this._hass, this._rawRows);
        const lang = this._lang();
        this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, lang));
        this._err = '';
        this._loading = false;
        this._paint();
      } catch (err) {
        if (gen !== this._gen) return;
        this._loading = false;
        this._showErr(err);
      }
    };
    this._loading = !(this._rows || []).length;
    this._paint();
    await run();
    if (!this.isConnected || gen !== this._gen) return;
    const every = Math.max(15, Number(cfg.refresh || 45)) * 1000;
    this._poll = setInterval(run, every);
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
          <div class="row">
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


/* `bkk-stop-card-r3` and its editor stay registered so dashboards written
   before the rename keep working. */
if (!customElements.get(BKK_HOP_EDITOR)) {
  customElements.define(BKK_HOP_EDITOR, BKKHopCardEditor);
}
if (!customElements.get(BKK_HOP_EDITOR_ALIAS)) {
  customElements.define(BKK_HOP_EDITOR_ALIAS, class extends BKKHopCardEditor {});
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

console.info(`%c HUNGARIAN-TRANSPORT %c ${CARD_VERSION} `,
  'color:#fff;background:#0f6fc6;font-weight:700',
  'color:#0f6fc6;background:#fff;font-weight:700');

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
    name: 'Hungarian transport planner',
    description: 'Stop \u2192 vehicle(s) \u2192 shared destination',
    preview: false,
  });
}

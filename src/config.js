
export const CARD_VERSION = '1.4.7-rev.3';

export const BKK_PLANNER_TAG = 'hungarian-transit-stop-card-plan';
export const BKK_PLANNER_TAG_ALIAS = 'bkk-stop-card-plan';
export const BKK_API = 'https://go.bkk.hu/api/query/v1/ws/otp/api/where';
export const TRANSITOUS = 'https://api.transitous.org';
/* Default departure look-ahead. Override per card with `minutesAfter`.
   The Kelenfold-Szekesfehervar hop lists trains past 2h, so 180 is the
   floor that still fills travelMin on those rows. */
export const DEFAULT_MINUTES_AFTER = 180;
export const MIN_MINUTES_AFTER = 15;
export const MAX_MINUTES_AFTER = 480;
export const MINUTES_AFTER_PRESETS = [30, 60, 90, 120, 180, 240, 360, 480];
export const DEPART_MINUTES_AFTER = String(DEFAULT_MINUTES_AFTER);

/* A departure stays on the card until a minute after it was due.
   Anything older is a bus that already left. */
export const DEPARTURE_GRACE_SEC = 60;

export function departureStillDue(dep, nowSec) {
  const ts = Number(dep);
  if (!Number.isFinite(ts) || ts <= 0) return true;
  return ts >= (nowSec || Math.floor(Date.now() / 1000)) - DEPARTURE_GRACE_SEC;
}

export function clampMinutesAfter(value) {
  if (value == null || value === '') return DEFAULT_MINUTES_AFTER;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MINUTES_AFTER;
  return Math.min(MAX_MINUTES_AFTER, Math.max(MIN_MINUTES_AFTER, Math.round(n)));
}

/* Keys that only change how the card looks: a change to them alone must not
   refetch the departures. Everything else selects or filters the data.
   Home Assistant hands its own layout keys (sections grid, visibility,
   card-mod) to setConfig too. */
export const PRESENTATION_KEYS = [
  'name', 'language', 'theme', 'type',
  'grid_options', 'view_layout', 'visibility', 'layout_options', 'card_mod',
];
const STRING_KEYS = ['name', 'stopId', 'stopName', 'destKey', 'destName', 'destStopId', 'city', 'apiKey', 'language', 'theme', 'stopKind', 'destKind'];

/* Hand-written YAML arrives as whatever the user typed: `routeIds: "9,109"`
   would otherwise be split into characters by new Set(). */
export function normalizeRouteIds(value) {
  if (Array.isArray(value)) return value.filter((v) => v != null && v !== '').map((v) => String(v).trim()).filter(Boolean);
  if (typeof value === 'string') return value.split(',').map((v) => v.trim()).filter(Boolean);
  return [];
}

export function normalizeCardConfig(config) {
  const cfg = Object.assign({}, config || {});
  cfg.routeIds = normalizeRouteIds(cfg.routeIds);
  STRING_KEYS.forEach((k) => {
    if (!(k in cfg) || typeof cfg[k] === 'string') return;
    cfg[k] = cfg[k] == null ? '' : String(cfg[k]);
  });
  if (cfg.minutesAfter != null && cfg.minutesAfter !== '') cfg.minutesAfter = clampMinutesAfter(cfg.minutesAfter);
  return cfg;
}

/* A stable text for the data-selecting part of a config, to tell a cosmetic
   edit (title, language) from one that needs a reload. */
export function configDataKey(config) {
  const cfg = config || {};
  return JSON.stringify(Object.keys(cfg).filter((k) => PRESENTATION_KEYS.indexOf(k) < 0).sort()
    .map((k) => [k, cfg[k]]));
}

/* Poll interval in seconds from the card's `refresh`. A value that is not a
   number ("30s") must not reach setInterval as NaN, which polls at once and
   forever. The floor is the shared departure board's lifetime (futar.js):
   polling faster would only read the same board again. */
export const DEFAULT_REFRESH_S = 45;
export const MIN_REFRESH_S = 20;
export function refreshSeconds(value) {
  if (value == null || value === '') return DEFAULT_REFRESH_S;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_REFRESH_S;
  return Math.min(3600, Math.max(MIN_REFRESH_S, Math.round(n)));
}

export function maxDepartureRows(horizon) {
  const h = clampMinutesAfter(horizon);
  const cap = h >= 480 ? 80 : 40;
  return Math.min(cap, Math.max(12, Math.round(h / 2.5)));
}
export const PLATFORM_IN_HEAD = /(?:\u00b7\s*)?(?:v\u00e1g|pl)\.(\S+)/i;
export const PLATFORM_STRIP = /\s*(?:\u00b7\s*)?(?:v\u00e1g|pl)\.\S+/gi;

/* Shortcut chips in the editor: national hubs only. Override them per card with
   `favorites: [{ id, name }]` if your own stops are elsewhere. */
export const BKK_FAVORITES = [
  { id: 'BKK_CSF01131', name: 'Keleti p\u00e1lyaudvar' },
  { id: 'BKK_CS007896', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
];
export const MAV_FAVORITES = [
  { id: 'BKK_005510017', name: 'Budapest-Keleti' },
  { id: 'BKK_005501024', name: 'Budapest-Kelenf\u00f6ld' },
  { id: 'BKK_005503269', name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r' },
];
export const VOLAN_FAVORITES = [
  { id: 'hkir_773521', name: 'Budapest, N\u00e9pliget aut\u00f3busz-p\u00e1lyaudvar' },
  { id: 'volan_773538_99', name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
];
export const FAVORITES = BKK_FAVORITES.concat(VOLAN_FAVORITES, MAV_FAVORITES);
export const DEFAULT_FAVORITES = {
  bkk: BKK_FAVORITES,
  mav: MAV_FAVORITES,
  volan: VOLAN_FAVORITES,
  helyi: [],
  all: FAVORITES,
};

export const VEHICLE_ICONS = {
  BUS: 'mdi:bus',
  TRAM: 'mdi:tram',
  TROLLEYBUS: 'mdi:bus-electric',
  SUBWAY: 'mdi:subway-variant',
  METRO: 'mdi:subway-variant',
  RAIL: 'mdi:train',
  TRAIN: 'mdi:train',
  FERRY: 'mdi:ferry',
};

export function vehicleIcon(type) {
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
export const BKK_RAIL_BADGE = {
  G43: ['AACD46', 'FFFFFF'],
  Z30: ['FFCD28', '3C3C3C'],
  S10: ['00AFF0', 'FFFFFF'],
  S12: ['00AFF0', 'FFFFFF'],
  S30: ['00AFF0', 'FFFFFF'],
  S36: ['00AFF0', 'FFFFFF'],
  S40: ['00AFF0', 'FFFFFF'],
  IC: ['2E5EA8', 'FFFFFF'],
  EC: ['2E5EA8', 'FFFFFF'],
  SZ: ['5C6B7A', 'FFFFFF'],
};

/* Assets live next to this module, so the card works from /hacsfiles/, /local/
   or wherever the Lovelace resource points. Requires `type: module`. */
export const ASSET_BASE = new URL('.', import.meta.url).href;
export let VOLAN_INDEX_OVERRIDE = '';
export let CITY_INDEX_OVERRIDE = '';
export let MAV_INDEX_OVERRIDE = '';

export function setVolanIndexUrl(url) {
  VOLAN_INDEX_OVERRIDE = String(url || '');
}

export function setCityIndexUrl(url) {
  CITY_INDEX_OVERRIDE = String(url || '');
}

export function setMavIndexUrl(url) {
  MAV_INDEX_OVERRIDE = String(url || '');
}

/* DEPRECATED, kept for callers outside this bundle: sets the module-wide
   fallback. The cards no longer use it: a module-wide setting read after an
   await could hand one card's index to another (review J-2, 2026-10-19).
   Each card now looks its indexes up through BkkLib.withIndexes(config),
   which carries that card's own volanIndex / cityIndex / mavIndex. */
export function applyIndexOverrides(cfg) {
  const c = cfg || {};
  setVolanIndexUrl(c.volanIndex);
  setCityIndexUrl(c.cityIndex);
  setMavIndexUrl(c.mavIndex);
}

/* Hungarian timetables are published in Budapest wall-clock time. Deriving the
   service day and the minute offsets from the browser's zone breaks the card
   for anyone whose Home Assistant is set to a different timezone. */
export const TZ = 'Europe/Budapest';
export const TZ_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: TZ,
  hour12: false,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});
export const pad2 = (n) => String(n).padStart(2, '0');

export function tzFields(date) {
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

export function tzParts(date) {
  const f = tzFields(date);
  return { ymd: `${f.y}${pad2(f.mo)}${pad2(f.d)}`, mins: f.h * 60 + f.mi };
}

/* Epoch seconds for a Budapest wall-clock moment given as YYYYMMDD plus minutes
   from midnight. GTFS uses 24:xx and beyond for after-midnight trips, so mins
   may exceed 1440. Two passes settle the DST offset. */
export function tzEpochSec(ymd, mins) {
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

export function tzShiftYmd(ymd, days) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const t = new Date(Date.UTC(y, mo - 1, d + (days || 0)));
  return `${t.getUTCFullYear()}${pad2(t.getUTCMonth() + 1)}${pad2(t.getUTCDate())}`;
}

/* GTFS calendar.txt starts the week on Monday. */
export function tzDow(ymd) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const js = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  return js === 0 ? 6 : js - 1;
}

export function dayBuster(base) {
  return `${base}${base.includes('?') ? '&' : '?'}v=${tzParts(new Date()).ymd}`;
}

/* `own` is the looking-up card's override (BkkLib.withIndexes); the module-wide
   one is only a fallback for callers outside the cards. */
export function volanIndexUrl(own) {
  const base = own || VOLAN_INDEX_OVERRIDE || new URL('volan-index.json.gz', ASSET_BASE).href;
  return dayBuster(base);
}

export function cityIndexUrl(own) {
  const base = own || CITY_INDEX_OVERRIDE || new URL('city-index.json.gz', ASSET_BASE).href;
  return dayBuster(base);
}

export function mavIndexUrl(own) {
  const base = own || MAV_INDEX_OVERRIDE || new URL('mav-index.json.gz', ASSET_BASE).href;
  return dayBuster(base);
}

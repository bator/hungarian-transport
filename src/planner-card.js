import { DEFAULT_MINUTES_AFTER, MINUTES_AFTER_PRESETS, clampMinutesAfter, departureStillDue, pad2, tzEpochSec, tzFields, tzShiftYmd } from './config.js';
import { BKKHopCard, NARROW_ROWS_PX } from './hop-card.js';
import { t } from './i18n.js';
import { BkkLib } from './lib/core.js';
import { BKK_PLANNER_EDITOR } from './tags.js';

/* After this long off screen the planner plans again on return. */
const PLANNER_STALE_MS = 5 * 60 * 1000;
/* After a failed stop-history read, the next one waits this long: hass
   changes many times a minute. */
const HISTORY_RETRY_MS = 60 * 1000;
/* Both journey maps: OpenStreetMap France tiles, credited as they ask. */
const MAP_TILES = 'https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png';
const MAP_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  + ' &copy; <a href="https://www.openstreetmap.fr/">OpenStreetMap France</a>';

export class BKKPlannerCard extends BKKHopCard {
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
    /* The origin whose reachable destinations are loaded (or loading). */
    this._destsFor = '';
    this._pickersMounted = false;
    /* Planning time lives on the card only: a saved time would be stale on
       the next visit. mode: now | depart | arrive; at: epoch ms. */
    this._when = { mode: 'now', at: 0 };
    this._journeys = [];
    this._openJourney = 0;
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

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 'auto', min_rows: 6 };
  }

  _departMode() {
    return 'all';
  }

  _shouldPoll() {
    return false;
  }

  /* Back on the view after a while, the departures and countdowns on screen
     are stale: plan again. A chosen time does not go stale, nor do its
     itineraries when all are folded or none was found. */
  _shouldReloadOnConnect() {
    const planned = this._timed() && ((this._journeys && this._journeys.length) || this._journeysDone);
    const idle = !(planned || this._journey || this._journeyLoading || (this._rows && this._rows.length));
    const stale = !this._timed() && this._loadedAt > 0 && Date.now() - this._loadedAt > PLANNER_STALE_MS;
    return idle || stale;
  }

  /* A new origin, destination or a swap: whatever the running reload or
     journey lookup still fetches belongs to the old pair and must not paint. */
  _invalidatePlan() {
    this._gen += 1;
    /* So do the searches typed or still running: their hits would paint
       under a field that was just picked or cleared. */
    if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
    if (this._destTimer) { clearTimeout(this._destTimer); this._destTimer = null; }
    this._searchSeq += 1;
    this._destSearchSeq += 1;
    this._reloadActive = false;
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    this._journey = null;
    this._journeys = [];
    this._journeyKey = '';
    this._journeyLoading = false;
    this._journeysDone = false;
    this._openJourney = 0;
  }

  _timed() {
    return this._when && this._when.mode !== 'now' && this._when.at > 0;
  }

  /* A chosen time plans itineraries for that moment. The live departure
     board stays for "now": FUTÁR, the GTFS indexes and ELVIRA all answer
     for the present. */
  async _reload() {
    this._loadedAt = Date.now();
    if (!this._timed()) return super._reload();
    const gen = ++this._gen;
    this._reloadActive = true;
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    this._loading = false;
    this._journeyKey = '';
    this._paint();
    this._considerJourney();
    if (gen === this._gen) this._reloadActive = false;
  }

  _considerJourney() {
    const cfg = this._config || {};
    const horizon = clampMinutesAfter(cfg.minutesAfter);
    const timed = this._timed();
    const when = timed ? `${this._when.mode}@${this._when.at}` : 'now';
    const key = [cfg.stopId, cfg.destStopId || '', cfg.destName, horizon, when].join('|');
    if (!cfg.stopId || !cfg.destName) return;
    if (this._journeyKey === key && (this._journeyLoading || this._journey || (timed && this._journeysDone))) return;
    const gen = this._gen;
    this._journeyKey = key;
    this._journeyLoading = true;
    this._journeysDone = false;
    const origin = { id: cfg.stopId, name: cfg.stopName || '', lat: cfg.stopLat, lon: cfg.stopLon };
    const dest = { id: cfg.destStopId || '', name: cfg.destName || '', lat: cfg.destLat, lon: cfg.destLon };
    if (timed) {
      this._journeys = [];
      this._journey = null;
      this._openJourney = 0;
      this._paintJourney();
      BkkLib.withIndexes(this._config).planJourneys(this._apiKey(), origin, dest, this._hass, {
        time: this._when.at,
        arriveBy: this._when.mode === 'arrive',
        minutesAfter: 120,
      }).then((list) => {
        if (gen !== this._gen) return;
        this._journeyLoading = false;
        this._journeysDone = true;
        this._journeys = list || [];
        this._openJourney = 0;
        this._journey = this._journeys[0] || null;
        this._paint();
      }).catch(() => {
        if (gen !== this._gen) return;
        this._journeyLoading = false;
        this._journeysDone = true;
        this._journeys = [];
        this._paint();
      });
      return;
    }
    BkkLib.withIndexes(this._config).planJourney(
      this._apiKey(),
      origin,
      dest,
      this._hass,
      { minutesAfter: horizon },
    ).then(async (journey) => {
      if (gen !== this._gen) return;
      this._journeyLoading = false;
      this._journey = journey;
      this._paint();
      if (!journey) return;
      try {
        await BkkLib.fillJourneyShapes(journey);
      } catch (_e) { /* draw the endpoint fallback */ }
      if (gen !== this._gen) return;
      this._journey = journey;
      this._paintJourney();
    }).catch(() => {
      if (gen !== this._gen) return;
      this._journeyLoading = false;
    });
  }

  set hass(hass) {
    super.hass = hass;
    if (!this._pickersMounted) return;
    /* The suggestions are this user's stops: they wait for hass to say who
       that is, and follow a new login. */
    if (this._historyDue((hass && hass.connection) || null)) this._paintFav();
    this._ensureDests();
  }

  /* Language from hass after the pickers were drawn: their labels, the
     time switch and the hints under the fields follow, not only the board. */
  _langChanged() {
    if (this._pickersMounted) this._syncPickerLabels();
    super._langChanged();
  }

  /* A hint under a search field keeps its i18n key, so a language change
     rewrites the same hint (a typed query's "no results" stays that). */
  _hintText(key) {
    const lang = this._lang();
    return key.indexOf('mode.') === 0 ? t(lang, 'mode').all[key.slice(5)] : t(lang, key);
  }

  _hint(box, key) {
    box.innerHTML = `<span class="hint" data-hint="${BkkLib.esc(key)}">${BkkLib.esc(this._hintText(key))}</span>`;
  }

  _repaintHints() {
    ['phits', 'pdhits'].forEach((id) => {
      const box = this._planEl(id);
      const span = box && !box.querySelector('.hit') ? box.querySelector('.hint[data-hint]') : null;
      if (span) span.textContent = this._hintText(span.getAttribute('data-hint'));
    });
  }

  _emptyBodyMessage(lang, cfg) {
    if (this._timed()) return '';
    /* The pickers above already ask for both stops. */
    if (!cfg.stopId || !cfg.destKey) return '';
    if (this._journey && this._journey.legs && this._journey.legs.length) return '';
    if (this._journeyLoading) return t(lang, 'cardLoading');
    return super._emptyBodyMessage(lang, cfg);
  }

  disconnectedCallback() {
    if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
    if (this._destTimer) { clearTimeout(this._destTimer); this._destTimer = null; }
    this._teardownPlannerInlineMap();
    super.disconnectedCallback();
  }

  connectedCallback() {
    super.connectedCallback();
    if (!this._painted || this._plannerMap) return;
    const host = this._journeyHost && this._journeyHost.querySelector('.journey-plan');
    if (host && host.querySelector('#pjmap')) this._mountPlannerInlineMap(host);
  }

  _teardownPlannerInlineMap() {
    const map = this._plannerMap;
    this._plannerMap = null;
    if (map) {
      try { map.remove(); } catch (_e) { /* ignore */ }
    }
  }

  _drawJourneyPolylines(L, map, segs, opts) {
    if (!Array.isArray(segs) || !segs.length) return [];
    opts = opts || {};
    const clickable = !!opts.interactive;
    const allPts = [];
    const lineBase = { lineJoin: 'round', lineCap: 'round', noClip: true };
    segs.forEach((seg) => {
      const shape = seg.shape;
      shape.forEach((pt) => allPts.push(pt));
      if (seg.walk) {
        L.polyline(shape, Object.assign({
          color: '#1a1a1a', weight: 5, opacity: 0.35, dashArray: '8 8', interactive: false,
        }, lineBase)).addTo(map);
        L.polyline(shape, Object.assign({
          color: '#888888', weight: 3, opacity: 0.95, dashArray: '8 8', interactive: clickable,
        }, lineBase)).addTo(map);
      } else {
        L.polyline(shape, Object.assign({
          color: '#1a1a1a', weight: 7, opacity: 0.45, interactive: false,
        }, lineBase)).addTo(map);
        L.polyline(shape, Object.assign({
          color: seg.color, weight: 5, opacity: 0.98, interactive: clickable,
        }, lineBase)).addTo(map);
      }
    });
    /* Boarding and alighting stop of every ride; names only on the big map,
       where there is room for them. */
    const lang = this._lang();
    BkkLib.journeyStopPins(segs).forEach((pin) => {
      L.marker(pin.pt, {
        icon: this._stopPinIcon(L, pin, lang, !!opts.labels),
        zIndexOffset: pin.kind === 'alight' ? 500 : 520,
        keyboard: false,
        interactive: clickable || !!opts.labels,
      }).addTo(map);
    });
    return allPts;
  }

  /* Refits once the layout settles. No animation: a zoom animation ends on
     a 250 ms timer of Leaflet's own that throws once the map is removed (an
     itinerary folded or swapped right away), and remove() cannot cancel it.
     A refit due after that skips the removed map. */
  _fitJourneyView(L, map, allPts, pad, alive) {
    if (!map || !allPts || allPts.length < 2) return;
    const bounds = L.latLngBounds(allPts);
    const apply = () => {
      if (!alive()) return;
      try {
        map.invalidateSize();
        map.fitBounds(bounds, { padding: [pad, pad], maxZoom: 14, animate: false });
      } catch (_e) { /* ignore */ }
    };
    apply();
    requestAnimationFrame(() => { apply(); requestAnimationFrame(apply); });
    setTimeout(apply, 200);
  }

  _fmtClock(ms) {
    return ms ? BkkLib.hm(Math.round(ms / 1000)) : '';
  }

  /* "ma 07:30", "holnap 07:30", or "h 10.20. 07:30" in Budapest time. */
  _whenText(ms) {
    const lang = this._lang();
    const f = tzFields(new Date(ms));
    const today = tzFields(new Date(Date.now()));
    /* Calendar tomorrow, not now + 24 h: on a DST day those differ. */
    const tomorrow = (() => {
      const ymd = tzShiftYmd(`${today.y}${pad2(today.mo)}${pad2(today.d)}`, 1);
      return { y: Number(ymd.slice(0, 4)), mo: Number(ymd.slice(4, 6)), d: Number(ymd.slice(6, 8)) };
    })();
    const clock = `${pad2(f.h)}:${pad2(f.mi)}`;
    const same = (a, b) => a.y === b.y && a.mo === b.mo && a.d === b.d;
    if (same(f, today)) return `${t(lang, 'plannerToday')} ${clock}`;
    if (same(f, tomorrow)) return `${t(lang, 'plannerTomorrow')} ${clock}`;
    const day = new Intl.DateTimeFormat(lang === 'hu' ? 'hu-HU' : 'en-GB', {
      timeZone: 'Europe/Budapest', weekday: 'short', month: 'numeric', day: 'numeric',
    }).format(new Date(ms));
    return `${day} ${clock}`;
  }

  _legChips(journey) {
    const esc = BkkLib.esc;
    return (journey.legs || []).map((leg) => {
      if (leg.walk) {
        return `<span class="chip-walk"><ha-icon icon="mdi:walk"></ha-icon>${esc(leg.minutes)}</span>`;
      }
      return `<span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label || '?')}</span>`;
    }).join('<span class="chip-sep">›</span>');
  }

  /* Departure → arrival as a vertical timeline in the line colours. */
  _legTimeline(journey) {
    const lang = this._lang();
    const esc = BkkLib.esc;
    const legs = journey.legs || [];
    const items = [];
    legs.forEach((leg) => {
      if (leg.walk) {
        const walkKey = leg.stationWalk ? 'plannerStationWalk' : 'plannerWalk';
        items.push(`<li class="tl-walk"><span class="tl-t">${esc(this._fmtClock(leg.startMs))}</span>`
          + `<div class="tl-b"><ha-icon icon="mdi:walk"></ha-icon>${esc(t(lang, walkKey, [leg.minutes, leg.meters]))}</div></li>`);
        return;
      }
      if (leg.waitMin >= 1) {
        items.push(`<li class="tl-wait"><span class="tl-t"></span>`
          + `<div class="tl-b">${esc(t(lang, 'plannerWait', leg.waitMin))}</div></li>`);
      }
      const hex = String(leg.color || '').replace('#', '');
      const tone = /^[0-9a-fA-F]{6}$/.test(hex) ? `#${hex}` : 'var(--primary-color)';
      items.push(`<li class="tl-ride" style="--c:${esc(tone)}">`
        + `<span class="tl-t">${esc(this._fmtClock(leg.startMs))}</span>`
        + `<div class="tl-b"><div class="tl-stop">${esc(leg.from || '')}</div>`
        + `<div class="tl-line"><span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label || '?')}</span>`
        + `<span class="tl-head">${esc(leg.headsign || leg.to || '')}</span>`
        + `<span class="tl-min">${esc(t(lang, 'plannerRide', leg.minutes))}</span></div>`
        + `<div class="tl-off">${esc(this._fmtClock(leg.endMs))} ${esc(leg.to || '')}</div></div></li>`);
    });
    const last = legs[legs.length - 1];
    if (last) {
      items.push(`<li class="tl-end"><span class="tl-t">${esc(this._fmtClock(journey.endMs || last.endMs))}</span>`
        + `<div class="tl-b"><div class="tl-stop">${esc(last.to || '')}</div></div></li>`);
    }
    return `<ol class="tl">${items.join('')}</ol>`;
  }

  /* One itinerary: a summary row, and when open its inline map, timeline
     and full-map button. */
  _itineraryHtml(journey, idx, open) {
    const lang = this._lang();
    const esc = BkkLib.esc;
    let times = '';
    if (journey.startMs && journey.endMs) {
      /* Name the day when the trip does not start on the asked-for day, and
         mark an arrival after midnight with +1. */
      const ref = this._timed() ? this._when.at : Date.now();
      const day = (ms) => {
        const f = tzFields(new Date(ms));
        return Date.UTC(f.y, f.mo - 1, f.d) / 86400000;
      };
      const start = day(journey.startMs) === day(ref)
        ? this._fmtClock(journey.startMs)
        : this._whenText(journey.startMs);
      const plus = day(journey.endMs) - day(journey.startMs);
      times = `<span class="itin-time">${esc(start)} \u2013 ${esc(this._fmtClock(journey.endMs))}`
        + `${plus > 0 ? `<sup>+${plus}</sup>` : ''}</span>`;
    }
    const meta = [t(lang, 'plannerTransfers', journey.transfers)];
    if (journey.walkMin) meta.push(t(lang, 'plannerWalkTotal', journey.walkMin));
    if (journey.waitMin) meta.push(t(lang, 'plannerWait', journey.waitMin));
    const body = open
      ? `<div class="itin-body"><div id="pjmap" class="j-map" hidden></div>${this._legTimeline(journey)}`
        + `<button type="button" id="pmap">${esc(t(lang, 'plannerShowMap'))}</button></div>`
      : '';
    return `<div class="itin${open ? ' open' : ''}">`
      + `<button type="button" class="itin-head" data-i="${idx}" aria-expanded="${open ? 'true' : 'false'}">`
      + `<span class="itin-top">${times}<span class="itin-dur">${esc(t(lang, 'plannerDuration', journey.durationMin))}</span></span>`
      + `<span class="itin-legs">${this._legChips(journey)}</span>`
      + `<span class="itin-meta">${esc(meta.join(' · '))}</span>`
      + `</button>${body}</div>`;
  }

  /* What the journey box shows: its class and markup, and whether its map
     button opens the board's first row. Null for nothing. */
  _journeyView() {
    const cfg = this._config || {};
    if (!cfg.stopId || !cfg.destName) return null;
    const lang = this._lang();
    const esc = BkkLib.esc;
    if (this._timed()) {
      const sub = this._when.mode === 'arrive'
        ? t(lang, 'plannerRoutesBy', this._whenText(this._when.at))
        : t(lang, 'plannerRoutesFrom', this._whenText(this._when.at));
      let inner = `<div class="j-title">${esc(t(lang, 'plannerRoutes'))}<span class="j-sub">${esc(sub)}</span></div>`;
      const list = this._journeys || [];
      if (this._journeyLoading) {
        inner += `<div class="j-msg">${esc(t(lang, 'plannerSearching'))}</div>`;
      } else if (!list.length) {
        inner += `<div class="j-msg">${esc(t(lang, 'plannerNoJourneys'))}</div>`;
      } else {
        inner += list.map((j, i) => this._itineraryHtml(j, i, i === this._openJourney)).join('');
      }
      return { className: 'journey-plan', html: inner, useBoard: false };
    }
    const visible = this._visibleRows();
    const journey = this._journey;
    const showJourney = !!(journey && journey.legs && journey.legs.length
      && (!visible.length || BkkLib.journeyBeatsBoard(journey, visible)));
    if (visible.length && !showJourney) {
      return {
        className: 'journey-plan j-map-only',
        html: `<button type="button" id="pmap">${esc(t(lang, 'plannerShowMap'))}</button>`,
        useBoard: true,
      };
    }
    if (!showJourney) return null;
    /* "With a transfer" only when there is one; a direct ride or a walk
       says so. */
    const rides = ((journey && journey.legs) || []).filter((leg) => !leg.walk).length;
    const titleKey = rides > 1 ? 'plannerJourneyTitle' : (rides === 1 ? 'plannerJourneyDirect' : 'plannerJourneyWalk');
    return {
      className: 'journey-plan',
      html: `<div class="j-title">${esc(t(lang, titleKey))}</div>` + this._itineraryHtml(journey, 0, true),
      useBoard: false,
    };
  }

  _visibleRows() {
    const nowSec = Math.floor(Date.now() / 1000);
    return (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
  }

  /* Redrawn only when what it shows changes: a board repaint (an error
     banner, a late hass) keeps the box and its inline map as they are. */
  _paintJourney() {
    const host = this._journeyHost;
    if (!host) return;
    const view = this._journeyView();
    const sig = view ? view.className + '\n' + view.html : '';
    if (sig === this._journeySig && this._journeySigOf === this._journey && host.childNodes.length === (view ? 1 : 0)) return;
    this._journeySig = sig;
    this._journeySigOf = this._journey;
    this._teardownPlannerInlineMap();
    host.innerHTML = '';
    if (!view) return;
    const box = document.createElement('div');
    box.className = view.className;
    box.innerHTML = view.html;
    host.appendChild(box);
    this._bindJourneyBox(box, view.useBoard);
  }

  _bindJourneyBox(box, useBoard) {
    const btn = box.querySelector('#pmap');
    if (btn) {
      btn.addEventListener('click', () => {
        if (!useBoard) return this._openJourneyMap();
        const first = this._visibleRows()[0];
        if (first) return this._openVehicleMap(first);
        this._showMapNotice(t(this._lang(), 'mapNoData'));
      });
    }
    box.querySelectorAll('.itin-head').forEach((head) => {
      head.addEventListener('click', () => {
        if (!this._timed()) return;
        const i = Number(head.getAttribute('data-i'));
        if (!Number.isInteger(i) || !this._journeys[i]) return;
        this._openJourney = this._openJourney === i ? -1 : i;
        this._journey = this._journeys[this._openJourney] || null;
        this._paintJourney();
      });
    });
    if (box.querySelector('#pjmap')) this._mountPlannerInlineMap(box);
  }

  async _mountPlannerInlineMap(host) {
    const el = host && host.querySelector('#pjmap');
    if (!el) return;
    try {
      const cfg = this._config || {};
      try {
        await BkkLib.fillJourneyShapes(this._journey);
      } catch (_e) { /* draw the endpoint fallback */ }
      if (!el.isConnected) return;
      const segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
      if (!segs.length) {
        el.hidden = true;
        return;
      }
      let L;
      try {
        L = await this._ensureLeaflet();
      } catch (_e) {
        el.hidden = true;
        return;
      }
      if (!el.isConnected) return;
      this._teardownPlannerInlineMap();
      el.hidden = false;
      const start = segs[0].shape[0];
      const map = L.map(el, {
        scrollWheelZoom: false,
        dragging: false,
        touchZoom: false,
        doubleClickZoom: false,
        boxZoom: false,
        keyboard: false,
        preferCanvas: false,
        attributionControl: true,
        zoomControl: false,
        zoomAnimation: false,
      }).setView(start, 8);
      this._plannerMap = map;
      L.tileLayer(MAP_TILES, { maxZoom: 20, subdomains: 'abc', attribution: MAP_ATTRIBUTION }).addTo(map);
      const allPts = this._drawJourneyPolylines(L, map, segs, { interactive: false });
      map.on('click', () => this._openJourneyMap());
      this._fitJourneyView(L, map, allPts, 16, () => this._plannerMap === map);
    } catch (_e) {
      this._teardownPlannerInlineMap();
      el.hidden = true;
    }
  }

  async _openJourneyMap() {
    const lang = this._lang();
    const cfg = this._config || {};
    if (this._openingMap === 'journey') return;
    if (this._mapOverlay && this._openMapKey === 'journey') return;
    this._closeVehicleMap();
    this._openingMap = 'journey';
    const seq = this._mapSeq;
    /* A swap or a new origin or destination while this open still waits
       (_invalidatePlan) makes it one for the old pair: its plan must not
       replace the new pair's journey, paint, or open a map. */
    const gen = this._gen;
    const stale = () => {
      if (this._mapStale(seq)) return true;
      if (gen === this._gen) return false;
      this._openingMap = '';
      return true;
    };
    try {
      await BkkLib.fillJourneyShapes(this._journey);
    } catch (_e) { /* draw the endpoint fallback */ }
    if (stale()) return;
    let segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
    /* Only "now" plans again for a map: a chosen time's open itinerary is
       one of several, and a plan for now would replace it with another. */
    if (!segs.length && cfg.stopId && cfg.destName && !this._timed()) {
      try {
        const journey = await BkkLib.withIndexes(this._config).planJourney(
          this._apiKey(),
          { id: cfg.stopId, name: cfg.stopName || '', lat: cfg.stopLat, lon: cfg.stopLon },
          { id: cfg.destStopId || '', name: cfg.destName || '', lat: cfg.destLat, lon: cfg.destLon },
          this._hass,
          { minutesAfter: clampMinutesAfter(cfg.minutesAfter) },
        );
        if (stale()) return;
        if (journey) this._journey = journey;
        try {
          await BkkLib.fillJourneyShapes(this._journey);
        } catch (_e) { /* draw the endpoint fallback */ }
        if (stale()) return;
        /* The card's own box shows the journey the map now draws, not the
           one this replan replaced. */
        if (journey) this._paintJourney();
        segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
      } catch (_e) { /* fall through to the no-geometry notice */ }
    }
    if (stale()) return;
    if (!segs.length) {
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapNoData'));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      if (stale()) return;
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    if (stale()) return;
    const overlay = document.createElement('div');
    overlay.className = 'ht-map-overlay';
    const title = `${cfg.stopName || ''} \u2192 ${cfg.destName || ''}`;
    const footText = t(lang, 'plannerJourneySummary', [
      this._journey && this._journey.durationMin,
      this._journey && this._journey.transfers,
      this._journey && this._journey.walkMin,
      (this._journey && this._journey.waitMin) || 0,
    ]);
    overlay.innerHTML = `
      <style>${this._mapChromeCss()}</style>
      <div class="ht-map-panel" role="dialog" aria-modal="true">
        <div class="ht-map-head">
          <div class="meta">${BkkLib.esc(title)}</div>
          <button type="button" class="ht-map-close" aria-label="${BkkLib.esc(t(lang, 'mapClose'))}">\u00d7</button>
        </div>
        <div class="ht-map-canvas"></div>
        <div class="ht-map-foot">${BkkLib.esc(footText)}</div>
      </div>
    `;
    (document.documentElement || document.body).appendChild(overlay);
    this._mapOverlay = overlay;
    this._openMapKey = 'journey';
    this._onMapKey = (ev) => {
      if (ev.key === 'Escape') this._closeVehicleMap();
    };
    document.addEventListener('keydown', this._onMapKey);
    overlay.querySelector('.ht-map-close').addEventListener('click', () => this._closeVehicleMap());
    overlay.addEventListener('click', (ev) => {
      if (ev.target === overlay) this._closeVehicleMap();
    });
    const canvas = overlay.querySelector('.ht-map-canvas');
    const start = segs[0].shape[0];
    let map;
    try {
      map = L.map(canvas, { scrollWheelZoom: true, preferCanvas: false }).setView(start, 8);
      L.tileLayer(MAP_TILES, { maxZoom: 20, subdomains: 'abc', attribution: MAP_ATTRIBUTION }).addTo(map);
    } catch (_e) {
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    this._map = map;
    const allPts = this._drawJourneyPolylines(L, map, segs, { labels: true });
    this._fitJourneyView(L, map, allPts, 36, () => this._map === map);
    this._openingMap = '';
  }

  /* The pickers name origin and destination already; the card keeps its
     own name (or "Tervező") as a title. */
  _header() {
    const cfg = this._config || {};
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
    this._ensureDests();
  }

  /* An origin from the YAML or the editor needs its reachable destinations
     too, not only one picked in the card. */
  _ensureDests() {
    const cfg = this._config || {};
    if (!this._pickersMounted || !this._hass || !cfg.stopId || this._destsFor === cfg.stopId) return;
    this._loadDests();
  }

  /* Itineraries sit between the pickers and the departure board. */
  _ensureDepartureCaption() {
    if (!this.shadowRoot || !this._elBody) return;
    if (!this._journeyHost || !this._journeyHost.isConnected) {
      this._journeyHost = document.createElement('div');
      this._journeyHost.className = 'pjourney';
      this._elBody.parentNode.insertBefore(this._journeyHost, this._elBody);
    }
    if (this.shadowRoot.getElementById('pdeps')) return;
    const cap = document.createElement('div');
    cap.id = 'pdeps';
    cap.className = 'deps-label';
    cap.textContent = t(this._lang(), 'plannerDepartures');
    this._elBody.parentNode.insertBefore(cap, this._elBody);
  }

  _paint() {
    super._paint();
    const timed = this._timed();
    const cap = this._planEl('pdeps');
    if (cap) cap.hidden = timed || !(this._rows || []).length;
    if (this._elBody) this._elBody.hidden = timed;
    this._paintJourney();
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
      ha-card.is-planner .wrap { padding: 14px 14px 12px; }
      ha-card.is-planner .head {
        font-size: 18px; font-weight: 700; letter-spacing: -0.01em; line-height: 1.25;
        margin: 0 2px 12px; padding: 0;
      }
      ha-card.is-planner .err { margin: 8px 2px 0; }
      ha-card.is-planner .deps-label, .journey-plan .j-title {
        display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
        margin: 16px 2px 6px; font-size: 12px; font-weight: 700;
        letter-spacing: 0.06em; text-transform: uppercase; color: var(--secondary-text-color);
      }
      ha-card.is-planner .deps-label[hidden], ha-card.is-planner .body[hidden] { display: none; }
      ha-card.is-planner .body { padding: 0; }
      ha-card.is-planner .msg { padding: 10px 2px; font-size: 14px; }
      ha-card.is-planner table { font-size: 15px; }
      ha-card.is-planner td { padding: 11px 5px; }
      ha-card.is-planner td.route .badge {
        min-width: 2.6em; text-align: center; border-radius: 8px;
        padding: 4px 8px; font-weight: 700;
      }
      @container ht-deps (max-width: ${NARROW_ROWS_PX}px) {
        ha-card.is-planner td { padding: 0; }
        ha-card.is-planner tr { padding: 9px 0; }
      }

      .plan { display: flex; flex-direction: column; gap: 10px; }
      /* Scoped to .plan: a bare .route / .when also matched the departure
         board's td.route and td.when and squeezed the headsign to a letter. */
      .plan .route {
        display: grid; grid-template-columns: 14px minmax(0, 1fr) 40px; column-gap: 10px;
        padding: 10px 8px 10px 12px; border-radius: 16px;
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .rail { display: flex; flex-direction: column; align-items: center; padding: 30px 0 18px; }
      .rail .dot { width: 12px; height: 12px; border-radius: 50%; box-sizing: border-box; flex: 0 0 auto; }
      .rail .dot.from { background: var(--primary-color); }
      .rail .dot.to { border: 3px solid var(--primary-color); background: var(--card-background-color, #fff); }
      .rail .line {
        flex: 1 1 auto; width: 0; margin: 4px 0;
        border-left: 2px dotted var(--secondary-text-color); opacity: .55;
      }
      .places { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
      .plan .label {
        font-size: 11px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
        color: var(--secondary-text-color); margin: 0 0 4px 2px;
      }
      .plan input, .plan select, .plan .picked {
        width: 100%; box-sizing: border-box; height: 40px; margin: 0;
        font: inherit; font-size: 15px; color: var(--primary-text-color);
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color); border-radius: 12px; padding: 0 12px;
      }
      .plan input:focus, .plan select:focus, .plan .picked:focus-visible,
      .seg button:focus-visible, #pswap:focus-visible, .itin-head:focus-visible {
        outline: 2px solid var(--primary-color); outline-offset: 1px;
      }
      .plan input.compact-hide { display: none; }
      .plan .picked {
        display: none; text-align: left; font-weight: 600; cursor: pointer;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-color: transparent;
      }
      .plan .picked.show { display: block; }
      #pswap {
        grid-column: 3; align-self: center; justify-self: center;
        width: 40px; height: 40px; border-radius: 50%; padding: 0; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        border: 1px solid var(--divider-color); background: var(--card-background-color, #fff);
        color: var(--primary-color);
      }
      #pswap svg { width: 22px; height: 22px; fill: currentColor; }
      #pswap:active { transform: rotate(180deg); transition: transform .2s; }

      .plan .when { display: flex; flex-direction: column; gap: 8px; }
      .seg {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px; padding: 3px;
        border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .seg button {
        border: 0; border-radius: 9px; padding: 8px 6px; cursor: pointer;
        font: inherit; font-size: 14px; font-weight: 600;
        background: transparent; color: var(--secondary-text-color);
      }
      .seg button.on {
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        box-shadow: 0 1px 3px rgba(0,0,0,.18);
      }
      .plan .horizon { display: flex; flex-direction: row; align-items: center; gap: 8px; }
      .plan .horizon .label { margin: 0; flex: 0 0 auto; }
      .plan .horizon select, .plan .horizon input { flex: 1 1 auto; min-width: 0; height: 38px; }
      .plan .horizon [hidden] { display: none; }
      .plan #preset {
        flex: 0 0 auto; border: 0; background: transparent; cursor: pointer; padding: 6px 4px;
        font: inherit; font-size: 13px; font-weight: 700; color: var(--primary-color);
      }

      .plan .suggest { margin-top: 6px; display: flex; flex-direction: row; flex-wrap: wrap; gap: 6px; }
      .plan .suggest.hidden, .plan .suggest:empty { display: none; }
      .plan .suggest-row, .plan .suggest-stops { display: contents; }
      .plan .suggest-kind { display: none; }
      .plan .suggest-stop {
        display: inline-flex; align-items: center; gap: 4px; cursor: pointer;
        border: 1px solid var(--divider-color); border-radius: 999px;
        font: inherit; font-size: 12px; font-weight: 600; padding: 4px 10px 4px 6px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
      }
      .plan .suggest-feed {
        display: inline-flex; align-items: center; justify-content: center;
        width: 16px; height: 16px; border-radius: 50%;
        font-size: 9px; font-weight: 800; line-height: 1;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .plan .suggest-list { display: contents; }
      .plan .list { max-height: 220px; overflow: auto; margin-top: 4px; }
      .plan .list:empty { display: none; }
      .plan .hit {
        display: block; width: 100%; text-align: left; cursor: pointer; margin-top: 4px;
        font: inherit; font-size: 14px; font-weight: 600; padding: 9px 12px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 10px;
      }
      .plan .hint { display: block; padding: 6px 4px; font-size: 13px; color: var(--secondary-text-color); }
      .plan #pdest.show ~ #pdhits .hint { display: none; }
      .itin-time sup { font-size: 11px; font-weight: 700; margin-left: 1px; color: var(--secondary-text-color); }

      .journey-plan .j-title { margin-top: 16px; }
      .journey-plan .j-sub { font-weight: 600; text-transform: none; letter-spacing: 0; }
      .journey-plan .j-msg { padding: 12px 2px; font-size: 14px; color: var(--secondary-text-color); }
      .journey-plan.j-map-only { margin: 12px 0 0; }
      .itin {
        border: 1px solid var(--divider-color); border-radius: 14px; overflow: hidden;
        background: var(--card-background-color, #fff);
      }
      .itin + .itin { margin-top: 8px; }
      .itin.open { border-color: var(--primary-color); }
      .itin-head {
        display: flex; flex-direction: column; gap: 6px; width: 100%; text-align: left;
        border: 0; background: transparent; color: inherit; font: inherit;
        padding: 10px 12px; cursor: pointer;
      }
      .itin-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
      .itin-time { font-size: 18px; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
      .itin-dur { font-size: 14px; font-weight: 700; white-space: nowrap; }
      .itin-legs { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
      .itin-meta { font-size: 12px; color: var(--secondary-text-color); }
      .j-badge {
        display: inline-block; flex: 0 0 auto; border-radius: 6px; padding: 2px 7px;
        font-size: 13px; font-weight: 700; line-height: 1.35; white-space: nowrap;
      }
      .chip-walk {
        display: inline-flex; align-items: center; gap: 1px; font-size: 12px; font-weight: 600;
        color: var(--secondary-text-color);
      }
      .chip-walk ha-icon, .tl ha-icon { --mdc-icon-size: 16px; }
      .chip-sep { color: var(--secondary-text-color); font-size: 14px; }
      .itin-body { padding: 0 12px 12px; }
      .journey-plan .j-map,
      .journey-plan .j-map.leaflet-container {
        height: 200px; min-height: 200px; margin: 0 0 12px; border-radius: 12px;
        overflow: hidden; position: relative; cursor: pointer;
        background: var(--secondary-background-color, #eee);
      }
      .journey-plan .j-map[hidden] { display: none; }
      .journey-plan .j-map.leaflet-container,
      .journey-plan .j-map .leaflet-container{width:100%;height:100%;min-height:200px;overflow:hidden}
      .journey-plan .j-map .leaflet-pane,.journey-plan .j-map .leaflet-tile,.journey-plan .j-map .leaflet-marker-icon,.journey-plan .j-map .leaflet-marker-shadow,.journey-plan .j-map .leaflet-tile-container,.journey-plan .j-map .leaflet-zoom-box,.journey-plan .j-map .leaflet-image-layer,.journey-plan .j-map .leaflet-layer{position:absolute;left:0;top:0}
      .journey-plan .j-map .leaflet-pane>svg,.journey-plan .j-map .leaflet-pane>canvas{position:absolute}
      .journey-plan .j-map svg,.journey-plan .j-map .leaflet-container svg{max-width:none!important;max-height:none!important}
      .journey-plan .j-map .leaflet-tile{max-width:none!important}
      .journey-plan .j-map .leaflet-tile-pane{z-index:200}
      .journey-plan .j-map .leaflet-overlay-pane{z-index:400}
      .journey-plan .j-map .leaflet-marker-pane{z-index:600}
      .journey-plan .j-map .leaflet-top,.journey-plan .j-map .leaflet-bottom{position:absolute;z-index:1000;pointer-events:none}
      .journey-plan .j-map .leaflet-top{top:0}
      .journey-plan .j-map .leaflet-bottom{bottom:0}
      .journey-plan .j-map .leaflet-left{left:0}
      .journey-plan .j-map .leaflet-right{right:0}
      .journey-plan .j-map .leaflet-control{position:relative;z-index:800;float:left;clear:both;pointer-events:auto}
      .journey-plan .j-map .leaflet-right .leaflet-control{float:right}
      .journey-plan .j-map .leaflet-control-attribution{margin:0;padding:0 5px;font-size:10px;line-height:1.4;color:#333;background:rgba(255,255,255,.72)}
      .journey-plan .j-map .leaflet-control-attribution a{color:#0078a8;text-decoration:none}
      .journey-plan .j-map .leaflet-attribution-flag{display:inline!important;vertical-align:baseline!important;width:1em;height:.6669em}
${this._stopPinCss()}
      .journey-plan #pmap {
        margin-top: 10px; border: 0; cursor: pointer; font: inherit; font-size: 13px; font-weight: 700;
        border-radius: 999px; padding: 8px 14px;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .journey-plan.j-map-only #pmap { margin: 0; }

      .tl { list-style: none; margin: 0; padding: 0; }
      .tl li { display: grid; grid-template-columns: 44px minmax(0, 1fr); column-gap: 10px; }
      .tl .tl-t {
        font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
        text-align: right; padding-top: 1px;
      }
      .tl .tl-b { position: relative; padding: 0 0 12px 14px; min-width: 0; font-size: 14px; }
      .tl .tl-b::before {
        content: ''; position: absolute; left: 0; top: 4px; bottom: -4px;
        border-left: 4px solid var(--c, var(--divider-color)); border-radius: 2px;
      }
      .tl .tl-walk .tl-b::before, .tl .tl-wait .tl-b::before {
        border-left: 3px dotted var(--secondary-text-color); left: 1px; opacity: .6;
      }
      .tl .tl-walk .tl-b, .tl .tl-wait .tl-b { color: var(--secondary-text-color); font-size: 13px; }
      .tl .tl-walk ha-icon { vertical-align: -3px; margin-right: 2px; }
      .tl .tl-end .tl-b::before {
        top: 3px; bottom: auto; width: 10px; height: 10px; left: -3px; border: 3px solid var(--primary-color);
        border-radius: 50%; box-sizing: border-box; background: var(--card-background-color, #fff);
      }
      .tl .tl-end .tl-b { padding-bottom: 0; }
      .tl .tl-stop { font-weight: 700; overflow-wrap: anywhere; }
      .tl .tl-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 4px 0 2px; }
      .tl .tl-head { flex: 1 1 auto; min-width: 0; color: var(--secondary-text-color); }
      .tl .tl-min { font-weight: 700; font-size: 13px; }
      .tl .tl-off { font-size: 12px; color: var(--secondary-text-color); }
    `;
    this.shadowRoot.appendChild(style);
    const lang = this._lang();
    const esc = BkkLib.esc;
    const plan = document.createElement('div');
    plan.className = 'plan';
    plan.innerHTML = `
      <div class="route journey">
        <div class="rail spine"><span class="dot from"></span><span class="line"></span><span class="dot to"></span></div>
        <div class="places">
          <div class="place">
            <div class="label" id="plab1">${esc(t(lang, 'plannerStep1'))}</div>
            <input id="pq" type="search" placeholder="${esc(t(lang, 'plannerSearchPlaceholder'))}">
            <button type="button" class="picked" id="pstop"></button>
            <div class="suggest" id="pfav"></div>
            <div class="list" id="phits"></div>
          </div>
          <div class="place">
            <div class="label" id="plab2">${esc(t(lang, 'plannerStep2'))}</div>
            <input id="pdq" type="search" placeholder="${esc(t(lang, 'plannerDestPlaceholder'))}">
            <button type="button" class="picked" id="pdest"></button>
            <div class="list" id="pdhits"></div>
          </div>
        </div>
        <button id="pswap" type="button" aria-label="${esc(t(lang, 'plannerSwap'))}" title="${esc(t(lang, 'plannerSwap'))}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9,3L5,7H8V14H10V7H13M16,17V10H14V17H11L15,21L19,17H16Z"/></svg>
        </button>
      </div>
      <div class="when">
        <div class="seg" role="radiogroup" id="pwhen" aria-label="${esc(t(lang, 'plannerWhen'))}">
          <button type="button" role="radio" data-when="now"></button>
          <button type="button" role="radio" data-when="depart"></button>
          <button type="button" role="radio" data-when="arrive"></button>
        </div>
        <div class="horizon">
          <div class="label" id="plab3">${esc(t(lang, 'plannerStep3'))}</div>
          <select id="pmin" aria-label="${esc(t(lang, 'plannerStep3'))}"></select>
          <input id="pat" type="datetime-local" step="300" hidden aria-label="${esc(t(lang, 'plannerWhen'))}">
          <button id="preset" type="button">${esc(t(lang, 'plannerReset'))}</button>
        </div>
      </div>
    `;
    wrap.insertBefore(plan, head.nextSibling);
    this._pickersMounted = true;
    this._bindPickers();
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
      q.addEventListener('blur', () => {
        if ((this._config || {}).stopName && !q.value.trim()) q.classList.add('compact-hide');
      });
    }
    if (dq) {
      dq.addEventListener('input', () => {
        clearTimeout(this._destTimer);
        this._destTimer = setTimeout(() => this._searchDest(dq.value.trim()), 280);
      });
      dq.addEventListener('focus', () => this._searchDest(dq.value.trim()));
      dq.addEventListener('blur', () => {
        if ((this._config || {}).destName && !dq.value.trim()) dq.classList.add('compact-hide');
      });
    }
    const stop = this._planEl('pstop');
    const dest = this._planEl('pdest');
    if (stop && q) {
      stop.addEventListener('click', () => {
        q.classList.remove('compact-hide');
        q.focus();
      });
    }
    if (dest && dq) {
      dest.addEventListener('click', () => {
        dq.classList.remove('compact-hide');
        dq.focus();
      });
    }
    if (reset) reset.addEventListener('click', () => this._resetPlan());
    if (swap) swap.addEventListener('click', () => this._swapPlan());
    const seg = this._planEl('pwhen');
    if (seg) {
      seg.querySelectorAll('button[data-when]').forEach((btn) => {
        btn.addEventListener('click', () => this._setWhen(btn.getAttribute('data-when')));
      });
    }
    const at = this._planEl('pat');
    if (at) {
      at.addEventListener('change', () => {
        const ms = this._parseWhenInput(at.value);
        if (!ms) return;
        this._applyWhen({ mode: this._when.mode === 'now' ? 'depart' : this._when.mode, at: ms });
      });
    }
    this._paintMinutesAfter();
  }

  /* Most: live board. Indulás / Érkezés: itineraries for the picked time,
     starting from the next five-minute mark. */
  _setWhen(mode) {
    if (mode !== 'depart' && mode !== 'arrive') {
      this._applyWhen({ mode: 'now', at: 0 });
      return;
    }
    let at = this._when.at;
    if (!(at > Date.now() - 3600000)) {
      const step = 5 * 60000;
      at = Math.ceil(Date.now() / step) * step;
    }
    this._applyWhen({ mode, at });
  }

  _applyWhen(when) {
    this._when = when;
    this._journeyKey = '';
    this._journey = null;
    this._journeys = [];
    this._journeysDone = false;
    this._journeyLoading = false;
    this._syncWhen();
    this._reloadSafe();
  }

  /* datetime-local values are Budapest wall clock, like every time here. */
  _parseWhenInput(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(value || ''));
    if (!m) return 0;
    return tzEpochSec(`${m[1]}${m[2]}${m[3]}`, Number(m[4]) * 60 + Number(m[5])) * 1000;
  }

  _whenInputValue(ms) {
    const f = tzFields(new Date(ms));
    return `${f.y}-${pad2(f.mo)}-${pad2(f.d)}T${pad2(f.h)}:${pad2(f.mi)}`;
  }

  _syncWhen() {
    const lang = this._lang();
    const timed = this._timed();
    const labels = { now: 'plannerNow', depart: 'plannerDepartAt', arrive: 'plannerArriveBy' };
    const seg = this._planEl('pwhen');
    if (seg) {
      seg.setAttribute('aria-label', t(lang, 'plannerWhen'));
      seg.querySelectorAll('button[data-when]').forEach((btn) => {
        const mode = btn.getAttribute('data-when');
        const on = mode === (this._when.mode || 'now');
        btn.textContent = t(lang, labels[mode]);
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-checked', on ? 'true' : 'false');
      });
    }
    const sel = this._planEl('pmin');
    const lab = this._planEl('plab3');
    const at = this._planEl('pat');
    if (sel) sel.hidden = timed;
    if (lab) lab.hidden = timed;
    if (at) {
      at.hidden = !timed;
      if (timed && this.shadowRoot.activeElement !== at) at.value = this._whenInputValue(this._when.at);
    }
  }

  /* A new login reads the suggestions at once; a failed read again only
     after HISTORY_RETRY_MS. */
  _historyDue(conn) {
    return this._historyFor !== conn || (this._historyRetryAt > 0 && Date.now() >= this._historyRetryAt);
  }

  /* The stops this user picks most, from their own Home Assistant storage
     (see stop-history.js); nothing until they have picked one. */
  _paintFav() {
    const box = this._planEl('pfav');
    if (!box) return;
    const cfg = this._config || {};
    /* Before hass there is no user to load suggestions for. */
    if (!this._hass) {
      box.innerHTML = '';
      return;
    }
    const conn = this._hass.connection || null;
    if (this._historyDue(conn)) {
      this._historyFor = conn;
      this._historyRetryAt = 0;
      const hass = this._hass;
      /* A failed read showed this browser's copy: a hass a minute later
         asks again. */
      const retry = () => { if (this._historyFor === conn) this._historyRetryAt = Date.now() + HISTORY_RETRY_MS; };
      BkkLib.loadStopHistory(hass).then((list) => {
        if (this._historyFor !== conn) return;
        this._historyList = list;
        this._paintFav();
        if (!BkkLib.stopHistoryKept(hass)) retry();
      }).catch(retry);
    }
    box.innerHTML = '';
    const shortName = (name) => String(name || '')
      .replace(/^Budapest,\s*/, '')
      .replace(/^Budapest-/, '')
      .replace(/\s+aut\u00f3busz-p\u00e1lyaudvar$/, '')
      .replace(/\s+vas\u00fat\u00e1llom\u00e1s$/, '')
      .replace(/\s+p\u00e1lyaudvar$/, '');
    const list = BkkLib.frequentStops(this._historyList || [], { excludeId: cfg.stopId, limit: 6 });
    if (!list.length) return;
    const marks = { mav: 'M', volan: 'V', bkk: 'B', city: 'H' };
    const row = document.createElement('div');
    row.className = 'suggest-row';
    const stops = document.createElement('span');
    stops.className = 'suggest-stops';
    list.forEach((fav) => {
      const mark = marks[BkkLib.stopIdFamily(fav)] || '';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'suggest-stop';
      button.title = fav.label || fav.name;
      if (mark) {
        const feed = document.createElement('span');
        feed.className = 'suggest-feed';
        feed.textContent = mark;
        button.appendChild(feed);
      }
      button.appendChild(document.createTextNode(shortName(fav.name)));
      button.addEventListener('click', () => this._pickOrigin(fav));
      stops.appendChild(button);
    });
    row.appendChild(stops);
    box.appendChild(row);
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
    const sel = this._planEl('pmin');
    if (sel) sel.setAttribute('aria-label', t(lang, 'plannerStep3'));
    const at = this._planEl('pat');
    if (at) at.setAttribute('aria-label', t(lang, 'plannerWhen'));
    if (dest) {
      dest.textContent = cfg.destName || '';
      dest.classList.toggle('show', !!cfg.destName);
    }
    if (q) {
      q.placeholder = t(lang, 'plannerSearchPlaceholder');
      const editing = this.shadowRoot.activeElement === q || !!(q.value || '').trim();
      q.classList.toggle('compact-hide', !!cfg.stopName && !editing);
    }
    if (dq) {
      dq.placeholder = t(lang, 'plannerDestPlaceholder');
      const editing = this.shadowRoot.activeElement === dq || !!(dq.value || '').trim();
      dq.classList.toggle('compact-hide', !!cfg.destName && !editing);
    }
    /* The suggestions follow the user's picks, so they are drawn again
       whenever the pickers change (a reset shows them again). */
    this._paintFav();
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
    if (swap) {
      swap.setAttribute('aria-label', t(lang, 'plannerSwap'));
      swap.setAttribute('title', t(lang, 'plannerSwap'));
    }
    if (reset) reset.textContent = t(lang, 'plannerReset');
    this._paintMinutesAfter();
    this._syncWhen();
    this._repaintHints();
  }

  _paintMinutesAfter() {
    const sel = this._planEl('pmin');
    if (!sel || sel.tagName !== 'SELECT') return;
    const current = clampMinutesAfter(this._config && this._config.minutesAfter);
    if (!sel.options.length) {
      MINUTES_AFTER_PRESETS.forEach((n) => {
        const o = document.createElement('option');
        o.value = String(n);
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => {
        const n = clampMinutesAfter(sel.value);
        this._config = Object.assign({}, this._config, { minutesAfter: n });
        this._reloadSafe();
      });
    }
    /* A configured look-ahead that is not a preset (45, 200) gets its own
       option, so the select does not show blank. */
    if (!Array.from(sel.options).some((o) => Number(o.value) === current)) {
      const o = document.createElement('option');
      o.value = String(current);
      const after = Array.from(sel.options).find((x) => Number(x.value) > current);
      sel.insertBefore(o, after || null);
    }
    const lang = this._lang();
    Array.from(sel.options).forEach((o) => { o.textContent = t(lang, 'plannerHorizon', Number(o.value)); });
    sel.value = String(current);
  }

  _swapPlan() {
    const cfg = this._config || {};
    const newOriginId = cfg.destStopId;
    const newOriginName = cfg.destName;
    if (!cfg.stopId || !newOriginId || !newOriginName) return;
    this._config = Object.assign({}, cfg, {
      stopId: newOriginId,
      stopName: newOriginName,
      stopLat: cfg.destLat,
      stopLon: cfg.destLon,
      stopKind: cfg.destKind || '',
      destKey: BkkLib.stationKey(cfg.stopName || ''),
      destName: cfg.stopName || '',
      destStopId: cfg.stopId,
      destLat: cfg.stopLat,
      destLon: cfg.stopLon,
      destKind: cfg.stopKind || '',
      routeIds: [],
    });
    this._invalidatePlan();
    this._dests = [];
    this._destRoutes = {};
    this._clearPickerInputs();
    this._paint();
    /* The swapped pair needs no route list (routeIds is empty): plan now,
       and load the new origin's destinations alongside. */
    this._reloadSafe();
    this._loadDests();
  }

  _resetPlan() {
    this._when = { mode: 'now', at: 0 };
    this._journeys = [];
    this._dests = [];
    this._destRoutes = {};
    this._destsFor = '';
    /* A destination load still running belongs to the cleared origin. */
    this._destSeq += 1;
    this._destsLoading = false;
    this._config = Object.assign({}, this._config, {
      stopId: '',
      stopName: '',
      stopKind: '',
      destKey: '',
      destName: '',
      destStopId: '',
      destKind: '',
      routeIds: [],
    });
    this._invalidatePlan();
    this._clearPickerInputs();
    this._paintDestHits('');
    this._paint();
  }

  /* After a pick, a swap or a reset: both fields empty, the origin's hits
     gone, the picked names on the buttons. */
  _clearPickerInputs() {
    const q = this._planEl('pq');
    const dq = this._planEl('pdq');
    const hits = this._planEl('phits');
    if (q) q.value = '';
    if (dq) dq.value = '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
  }

  async _searchOrigin(q) {
    const box = this._planEl('phits');
    if (!box) return;
    /* A cleared or too short query also cancels the search still running. */
    const seq = ++this._searchSeq;
    if (!q || q.length < 2) { box.innerHTML = ''; return; }
    try {
      this._err = '';
      if (!this._hasFutar()) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const hits = await BkkLib.withIndexes(this._config).searchPlannerStops(this._apiKey(), q);
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        this._hint(box, 'mode.empty');
        return;
      }
      box.innerHTML = hits.map((s) => (
        `<button class="hit" type="button" data-id="${BkkLib.esc(s.id)}">${BkkLib.esc(BkkLib.plannerHitLabel(s, this._lang()))}</button>`
      )).join('');
      box.querySelectorAll('.hit').forEach((btn) => {
        btn.addEventListener('click', () => {
          const id = btn.getAttribute('data-id');
          const st = hits.find((h) => h.id === id);
          if (st) this._pickOrigin(st);
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._showErr(err);
    }
  }

  /* Every picked stop counts towards the user's suggestions. */
  _rememberStop(stop) {
    const hass = this._hass;
    BkkLib.recordStop(hass, stop).then((list) => {
      this._historyList = list;
      this._paintFav();
      /* Written without the server's list: read it again a minute later. */
      if (!BkkLib.stopHistoryKept(hass)) this._historyRetryAt = Date.now() + HISTORY_RETRY_MS;
    }).catch(() => { /* suggestions only */ });
  }

  async _pickOrigin(stop) {
    this._rememberStop(stop);
    this._config = Object.assign({}, this._config, {
      stopId: stop.id,
      stopName: stop.name,
      stopLat: stop.lat,
      stopLon: stop.lon,
      stopKind: stop.kind || '',
      destKey: '',
      destName: '',
      destStopId: '',
      destLat: undefined,
      destLon: undefined,
      destKind: '',
      routeIds: [],
    });
    this._invalidatePlan();
    this._dests = [];
    this._destRoutes = {};
    this._clearPickerInputs();
    this._paint();
    await this._loadDests();
  }

  async _loadDests() {
    const cfg = this._config || {};
    if (!cfg.stopId) return;
    this._destsFor = cfg.stopId;
    const seq = ++this._destSeq;
    /* The last origin's destinations must not be offered while this
       one's load (an origin changed in the editor). */
    this._dests = [];
    this._destRoutes = {};
    this._destsLoading = true;
    this._paintDestHits('');
    try {
      if (!this._hasFutar()) await this._fillKey();
      const placeOrigin = /^(geo:|node\/|way\/)/i.test(String(cfg.stopId || ''))
        || cfg.stopKind === 'address' || cfg.stopKind === 'place';
      if (placeOrigin) {
        this._dests = [];
        this._destRoutes = {};
      } else {
        const out = await BkkLib.withIndexes(this._config).reachableDests(
          this._apiKey(),
          this._tripCache || {},
          { id: cfg.stopId, name: cfg.stopName || '' },
          'all',
          '',
        );
        if (seq !== this._destSeq) return;
        this._dests = (out && out.dests) || [];
        this._destRoutes = (out && out.destRoutes) || {};
      }
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

  _idsForDest(keys) {
    const raw = [];
    (Array.isArray(keys) ? keys : [keys]).forEach((key) => {
      (this._destRoutes[key] || []).forEach((id) => { if (raw.indexOf(id) < 0) raw.push(id); });
    });
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
    const cfg = this._config || {};
    if (!cfg.stopId) {
      this._hint(box, 'plannerPickAll');
      return;
    }
    const seq = ++this._destSearchSeq;
    if (!q || q.length < 2) {
      this._paintDestHits(q || '');
      return;
    }
    try {
      if (!this._hasFutar()) await this._fillKey();
      const extras = await BkkLib.withIndexes(this._config).searchPlannerStops(this._apiKey(), q);
      if (seq !== this._destSearchSeq) return;
      const hits = BkkLib.destHitsForQuery(this._dests, q, extras);
      if (!hits.length && this._destsLoading) {
        this._hint(box, 'destLoading');
        return;
      }
      this._renderDestHits(hits, 'mode.empty');
    } catch (err) {
      if (seq !== this._destSearchSeq) return;
      this._showErr(err);
    }
  }

  _renderDestHits(hits, emptyKey) {
    const box = this._planEl('pdhits');
    if (!box) return;
    this._destHits = hits || [];
    if (!this._destHits.length) {
      this._hint(box, emptyKey);
      return;
    }
    const shown = this._destHits.slice(0, 20);
    /* By position: two towns' "autóbusz-állomás" can share a key. */
    box.innerHTML = shown.map((d, i) => (
      `<button class="hit" type="button" data-ix="${i}" data-key="${BkkLib.esc(d.key)}" data-id="${BkkLib.esc(d.id || '')}">${BkkLib.esc(BkkLib.plannerHitLabel(d, this._lang()))}</button>`
    )).join('');
    box.querySelectorAll('.hit').forEach((btn) => {
      btn.addEventListener('click', () => {
        const d = shown[Number(btn.getAttribute('data-ix'))];
        if (d) this._pickDest(Object.assign({}, d));
      });
    });
  }

  _paintDestHits(q) {
    const box = this._planEl('pdhits');
    if (!box) return;
    const cfg = this._config || {};
    if (!cfg.stopId) {
      this._hint(box, 'plannerPickAll');
      return;
    }
    if (this._destsLoading) {
      this._hint(box, 'destLoading');
      return;
    }
    if (!this._dests.length) {
      this._hint(box, 'plannerTypeDest');
      return;
    }
    const hits = BkkLib.destHitsForQuery(this._dests, q, []);
    this._renderDestHits(hits, 'mode.destEmpty');
  }

  _pickDest(dest) {
    this._rememberStop(dest);
    /* A key without its town ("aut all") matches every town's bus station;
       a named town stays in the destination key. */
    const destKey = BkkLib.splitCityPrefix(dest.name || '') ? BkkLib.norm(dest.name) : dest.key;
    const routeKeys = dest.routeKeys && dest.routeKeys.length ? dest.routeKeys : [dest.key];
    this._config = Object.assign({}, this._config, {
      destKey,
      destName: dest.name,
      destStopId: dest.id || '',
      destLat: dest.lat,
      destLon: dest.lon,
      destKind: dest.kind || '',
      routeIds: this._idsForDest(routeKeys),
    });
    this._invalidatePlan();
    const dq = this._planEl('pdq');
    const hits = this._planEl('pdhits');
    if (dq) dq.value = '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._reloadSafe();
  }
}

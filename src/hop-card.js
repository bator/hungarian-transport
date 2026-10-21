import { ASSET_BASE, DEFAULT_MINUTES_AFTER, clampMinutesAfter, configDataKey, departureStillDue, maxDepartureRows, normalizeCardConfig, refreshSeconds } from './config.js';
import { codedError, errText, resolveLang, t } from './i18n.js';
import { BkkLib } from './lib/core.js';
import { BKK_HOP_EDITOR } from './tags.js';

let leafletPromise = null;

/* Departure board width (the card minus its padding) below which rows switch
   to the two-line phone layout. A 500 px desktop section column stays above it. */
export const NARROW_ROWS_PX = 440;

export class BKKHopCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._configDataKey = null;
    this._poll = null;
    this._tick = null;
    this._onVisibility = null;
    this._hass = null;
    this._tripCache = {};
    this._rows = [];
    this._rawRows = [];
    this._err = '';
    this._loading = false;
    /* The shown rows are a Transitous fill under an error banner. */
    this._rowsFilled = false;
    this._painted = false;
    this._gen = 0;
    this._keyTried = false;
    this._reloadActive = false;
    this._mapOverlay = null;
    this._map = null;
    this._mapMarker = null;
    this._mapBoardMarker = null;
    this._mapAlightMarker = null;
    this._mapRoute = null;
    this._openMapKey = null;
    this._onMapKey = null;
    this._mapClicksBound = false;
    this._openingMap = '';
    this._mapRef = null;
    this._mapState = null;
    this._mapL = null;
    this._mapReturnFocus = null;
    this._hiddenAt = 0;
    this._pollRun = null;
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
    /* Home Assistant builds a card (setConfig) before it attaches it, and
       some it never attaches. Only a card on the page takes a gate slot,
       ticks and fetches; connectedCallback starts all three. */
    if (this.isConnected) this._holdGate();
    const next = normalizeCardConfig(config);
    /* Against the config the card shows now, not the one last set: the
       planner's in-card picks change it, and re-applying the configured
       stops (a title edit in the editor preview) must bring them back. */
    const prevKey = this._configDataKey != null ? configDataKey(this._config) : null;
    const nextKey = configDataKey(next);
    this._config = next;
    this._configDataKey = nextKey;
    BkkLib.warnCardApiKey(next);
    /* Only the title or the language changed (the editor emits on every
       keystroke): redraw with the rows and timers it already has. */
    if (prevKey != null && prevKey === nextKey && this._painted) {
      try {
        this._render();
      } catch (err) {
        this._showErr(err);
      }
      return;
    }
    this._tripCache = {};
    this._err = '';
    this._keyTried = false;
    /* The rows on screen belong to the old pair. Kept, a first fetch for
       the new one that fails would leave them under the new title. */
    this._rows = [];
    this._rawRows = [];
    this._pollRun = null;
    try {
      this._render();
    } catch (err) {
      this._showErr(err);
      return;
    }
    if (this.isConnected) {
      this._reloadSafe();
      return;
    }
    /* A run still in flight from before a detach is for the old config. */
    this._gen += 1;
    this._reloadActive = false;
  }

  /* _reload is async, so its rejections need an owner. Mark in-flight
     synchronously: a detach and re-attach in the same turn (Home Assistant
     re-parenting a card) must not start a second fetch. */
  _reloadSafe() {
    this._reloadActive = true;
    Promise.resolve().then(() => this._reload()).catch((err) => this._showErr(err));
  }

  /* The card's own key, or a key borrowed from a sibling card while the
     integration has none. Empty when FUTÁR goes through the integration. */
  _apiKey() {
    return BkkLib.cardApiKey(this._config, this._hass, this._borrowedKey);
  }

  _hasFutar() {
    return BkkLib.hasFutar(this._apiKey(), this._hass);
  }

  set hass(hass) {
    const first = !this._hass;
    const langBefore = this._hass ? this._lang() : '';
    const viaBefore = this._viaIntegration;
    this._hass = hass;
    BkkLib._hass = hass;
    if (this._lang() !== langBefore) this._langChanged();
    /* The integration's futar service can register after the first paint
       (key added in the options flow): reload once it shows up. */
    this._viaIntegration = !(this._config || {}).apiKey && BkkLib.integrationHasKey(hass);
    if (this._viaIntegration && !viaBefore && this._painted && !this._reloadActive && this.isConnected) this._reloadSafe();
    if (first && this._rawRows && this._rawRows.length) {
      BkkLib.enrichFromHass(this._hass, this._rawRows);
      const cfg = this._config || {};
      this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
      this._paint();
    }
    this._maybeFillKey();
  }

  /* With `language: auto` Home Assistant calls setConfig before hass, so the
     first paint is in the browser's language: redraw in the user's. */
  _langChanged() {
    this._paint();
  }

  /* hass is set on every state change, so looking for a sibling card's key has
     to happen at most once, and never in the key-less modes. */
  _maybeFillKey() {
    const mode = BkkLib.mode(this._config || {});
    if (mode === 'volan' || mode === 'helyi') return;
    if (this._config.apiKey || this._keyTried || BkkLib.integrationHasKey(this._hass)) return;
    this._keyTried = true;
    this._fillKey().catch(() => { /* dashboard config not readable */ });
  }

  getCardSize() {
    const rows = Math.max(1, (this._rows || []).length);
    return Math.max(1, Math.round((44 + rows * 30) / 50));
  }

  /* Sections view: a departure board needs half a 12-column row at least to
     keep the headsign readable; its height follows the rows. */
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 'auto' };
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
    this._bindVisibility();
    if (!this._painted) return;
    this._startTick();
    this._bindMapClicks();
    if (!this._poll && !this._reloadActive && this._shouldReloadOnConnect()) this._reloadSafe();
  }

  _pageHidden() {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden';
  }

  _bindVisibility() {
    if (this._onVisibility || typeof document === 'undefined') return;
    this._onVisibility = () => this._visibilityChanged();
    document.addEventListener('visibilitychange', this._onVisibility);
  }

  _unbindVisibility() {
    if (!this._onVisibility || typeof document === 'undefined') return;
    document.removeEventListener('visibilitychange', this._onVisibility);
    this._onVisibility = null;
  }

  /* A background tab must not poll the APIs or repaint countdowns nobody
     sees. Back in front: the timers again, and a fresh run only after an
     absence longer than the poll interval. A quick tab switch would
     otherwise refetch everything each time; the poll picks up instead. */
  _visibilityChanged() {
    if (this._pageHidden()) {
      if (!this._hiddenAt) this._hiddenAt = Date.now();
      if (this._poll) { clearInterval(this._poll); this._poll = null; }
      if (this._tick) { clearInterval(this._tick); this._tick = null; }
      return;
    }
    const away = this._hiddenAt ? Date.now() - this._hiddenAt : Infinity;
    this._hiddenAt = 0;
    if (!this.isConnected || !this._painted) return;
    this._startTick();
    this._tickPaint();
    if (this._poll || this._reloadActive || !this._shouldReloadOnConnect()) return;
    if (away < refreshSeconds((this._config || {}).refresh) * 1000 && this._resumePoll()) return;
    this._reloadSafe();
  }

  /* The poll repeats the reload's own run. A run slower than the interval
     (a stalled ELVIRA call) skips the next tick instead of racing it: two
     runs could paint out of order. The busy flag lives with the run, so a
     poll picked up again after a tab switch still waits for it. */
  _armPoll(pollRun) {
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    this._pollRun = pollRun;
    const cfg = this._config || {};
    const every = refreshSeconds(cfg.refresh) * 1000;
    this._poll = setInterval(() => {
      if (pollRun.busy) return;
      pollRun.busy = true;
      pollRun.run().finally(() => { pollRun.busy = false; });
    }, every);
  }

  /* The poll of the current reload again, if there is one to pick up. */
  _resumePoll() {
    const p = this._pollRun;
    if (!p || p.gen !== this._gen || !this._shouldPoll()) return false;
    this._armPoll(p);
    return true;
  }

  _shouldPoll() {
    return true;
  }

  _shouldReloadOnConnect() {
    return true;
  }

  disconnectedCallback() {
    this._unbindVisibility();
    if (this._gateHeld) {
      this._gateHeld = false;
      BkkLib._gateCards = Math.max(0, BkkLib._gateCards - 1);
    }
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    if (this._tick) { clearInterval(this._tick); this._tick = null; }
    /* The overlay hangs off <html>, outside the card: navigating away (or
       the back gesture) must not leave it over the next view. */
    this._closeVehicleMap();
  }

  /* Keep the rows on screen and put the failure in a banner above them: a
     single failed refresh should not wipe a timetable that is still useful. */
  _showErr(err) {
    this._err = err || '';
    this._paint();
  }

  /* The error is kept as thrown and worded at paint, so a language change
     (hass arriving, the editor's switch) rewords the banner too. */
  _errMessage(lang) {
    return this._err ? errText(lang || this._lang(), this._err) : '';
  }

  /* Kept apart from the config: once the integration's futar service shows
     up (HA still starting, entry reloading), it wins over a borrowed key. */
  async _fillKey() {
    const key = await BkkLib.apiKeyFromDashboard(this._hass);
    if (!key || this._config.apiKey) return;
    this._borrowedKey = key;
    if (this.isConnected) this._reloadSafe();
  }

  _header() {
    const cfg = this._config || {};
    const ready = cfg.stopId && cfg.destKey;
    if (cfg.name) return cfg.name;
    if (ready) return `${cfg.stopName || ''} \u2192 ${cfg.destName || ''}`;
    return t(this._lang(), 'cardTitle');
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
      .body { pointer-events: auto; container: ht-deps / inline-size; }
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
      .badge.long { font-size: 10px; padding: 2px 5px; letter-spacing: -0.02em; }
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
      .picto.booking { color: #c0392b; font-weight: 800; font-size: 13px; }
      .picto.pass {
        padding: 0 3px; border-radius: 2px; background: #2e7d32; color: #fff;
        font-size: 10px; font-weight: 800; letter-spacing: 0.02em; line-height: 1.45;
      }
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
      /* On a phone the fixed columns (badge, pictograms, map pin, time,
         countdown) left the headsign a letter or two. Below ${NARROW_ROWS_PX}px each
         row becomes two lines on a shared grid: badge over vehicle icon, the
         headsign across the middle with pictograms and the map pin under it,
         and the time columns still aligned from row to row. */
      @container ht-deps (max-width: ${NARROW_ROWS_PX}px) {
        table { display: block; }
        tbody {
          display: grid; column-gap: 8px;
          grid-template-columns: auto minmax(0, 1fr) auto auto auto;
        }
        /* Without subgrid each row keeps the same tracks, just unaligned. */
        tr {
          display: grid; grid-column: 1 / -1;
          grid-template-columns: auto minmax(0, 1fr) auto auto auto;
          grid-template-columns: subgrid;
          grid-template-rows: auto auto; row-gap: 2px; align-items: center;
          padding: 6px 0;
        }
        tr + tr { border-top: 1px solid var(--divider-color); }
        tr + tr td { border-top: 0; }
        tr.row-clickable:hover td { background: none; }
        tr.row-clickable:hover { background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
        td { padding: 0; }
        td.icon, td.route, td.flags, td.mapbtn, td.when, td.in { width: auto; }
        td.route { grid-area: 1 / 1; justify-self: center; }
        td.icon { grid-area: 2 / 1; justify-self: center; }
        td.dest {
          grid-area: 1 / 2 / 2 / 4; min-width: 0; max-width: none;
          white-space: normal; overflow-wrap: break-word; line-height: 1.3;
          display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
        }
        .num { white-space: nowrap; }
        td.flags { grid-area: 2 / 2; min-width: 0; white-space: normal; }
        .pictos { flex-wrap: wrap; }
        td.mapbtn { grid-area: 2 / 3; }
        td.when { grid-area: 1 / 4 / 3 / 5; align-self: start; }
        td.in { grid-area: 1 / 5 / 3 / 6; align-self: start; }
        .sched { display: block; margin-left: 0; }
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
    this._tick = null;
    if (this._pageHidden() || !this.isConnected) return;
    this._tick = setInterval(() => this._tickPaint(), 15000);
  }

  /* Between refreshes only the countdowns change. Rewriting the whole table
     every 15 s dropped hover, keyboard focus and open tooltips, so update the
     text in place and repaint only when a departure falls off the list. */
  _tickPaint() {
    const shown = this._shownRows || [];
    if (!shown.length || !this._elBody) return;
    const nowSec = Math.floor(Date.now() / 1000);
    const due = (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
    if (due.length !== shown.length || due.some((r, i) => r !== shown[i])) {
      this._paint();
      return;
    }
    const lang = this._lang();
    this._elBody.querySelectorAll('tr[data-row-index]').forEach((tr) => {
      const row = shown[Number(tr.getAttribute('data-row-index'))];
      const cell = tr.querySelector('.cd');
      if (!row || !cell) return;
      const text = this._countdown(row.depTs, lang);
      if (cell.textContent !== text) cell.textContent = text;
    });
  }

  _paint() {
    if (!this._painted || !this._elBody) return;
    const lang = this._lang();
    const cfg = this._config || {};
    this._elHead.textContent = this._header();
    const errMsg = this._errMessage(lang);
    this._elErr.textContent = errMsg;
    this._elErr.style.display = errMsg ? '' : 'none';
    const nowSec = Math.floor(Date.now() / 1000);
    const allRows = this._rows || [];
    const rows = allRows.filter((r) => departureStillDue(r.depTs, nowSec));
    /* data-row-index points into this list, not into _rows: departed rows
       are filtered out, so the two indexes differ once a bus has left. */
    this._shownRows = rows;
    if (rows.length) {
      this._elBody.innerHTML = `<table><tbody>${
        rows.map((r, i) => this._rowHtml(r, lang, i)).join('')
      }</tbody></table>`;
      return;
    }
    const msg = this._emptyBodyMessage(lang, cfg);
    this._elBody.innerHTML = msg ? `<div class="msg">${BkkLib.esc(msg)}</div>` : '';
  }

  _emptyBodyMessage(lang, cfg) {
    if (!cfg.stopId || !cfg.destKey) return t(lang, 'cardPickStops');
    if (this._loading) return t(lang, 'cardLoading');
    if (this._err) return '';
    return t(lang, 'cardNoDepartures');
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
    if (r.booking) {
      const tip = r.reservationFreeSection
        ? `${t(lang, 'tipBooking')} \u00b7 ${t(lang, 'tipBookingFreeSection')}`
        : t(lang, 'tipBooking');
      pictos.push(this._picto('H', tip, 'booking'));
      /* Seat-reservation trains mostly still take the national pass; say so
         where ELVIRA lists it, since that is what pass holders look for. */
      if (r.nationalPass) pictos.push(this._picto('OB', t(lang, 'tipNationalPass'), 'pass'));
    }
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
      <td class="route"><span class="badge${String(r.label || '').length > 4 ? ' long' : ''}" style="${esc(this._badgeStyle(r))}">${
      esc(r.label || '?')}</span></td>
      <td class="dest" title="${esc(r.headsign || '')}">${esc(r.headsign || '')}${num}</td>
      <td class="flags">${plat}${pictos.length ? `<span class="pictos">${pictos.join('')}</span>` : ''}</td>
      ${mapBtn}
      <td class="when"><span class="${atClass}" title="${esc(atTip)}">${
      esc(r.predicted_attime || r.attime || '')}</span>${
      sched ? `<span class="sched">${esc(sched)}</span>` : ''}</td>
      <td class="in"><span class="cd">${esc(this._countdown(r.depTs, lang))}</span>${travel}</td>
    </tr>`;
  }

  _bindMapClicks() {
    if (this._mapClicksBound) return;
    if (!this._elBody) return;
    this._mapClicksBound = true;
    /* Capture on the host sees every click in the shadow tree first. */
    this.addEventListener('click', (ev) => this._onRowClick(ev), true);
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
    return (this._shownRows || [])[idx] || null;
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
    if (k === 'rail' || k === 'train') return t(lang, 'mapTypeRail');
    if (k === 'coach') return t(lang, 'mapTypeCoach');
    if (k === 'bus') return t(lang, 'mapTypeBus');
    if (k === 'ferry') return t(lang, 'mapTypeFerry');
    return t(lang, 'mapTypeVehicle');
  }

  _mapGlyph(kind) {
    const k = String(kind || '').toLowerCase();
    if (k === 'tram') return '\ud83d\ude8a';
    if (k === 'trolleybus' || k === 'trolley') return '\ud83d\ude8e';
    if (k === 'subway' || k === 'metro') return '\ud83d\ude87';
    if (k === 'rail' || k === 'train') return '\ud83d\ude86';
    if (k === 'bus' || k === 'coach') return '\ud83d\ude8c';
    if (k === 'ferry') return '\u26f4';
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
        link.href = new URL('leaflet/leaflet.css', ASSET_BASE).href;
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
      script.src = new URL('leaflet/leaflet.js', ASSET_BASE).href;
      script.onload = () => {
        if (isLeaflet(window.L)) resolve(window.L);
        else reject(new Error('leaflet'));
      };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    /* A failed load (offline for a moment) is tried again on the next open,
       not remembered until the page reloads. */
    leafletPromise.catch(() => {
      leafletPromise = null;
      const el = typeof document !== 'undefined' ? document.getElementById('hungarian-transport-leaflet-js') : null;
      if (el && !isLeaflet(window.L)) el.remove();
    });
    return leafletPromise;
  }

  /* Every close (and so every new open) bumps the map sequence. An open that
     is still awaiting Leaflet or a route checks it after each await, so a
     superseded open never adds a second overlay that nothing can close. */
  _closeVehicleMap() {
    this._mapSeq = (this._mapSeq || 0) + 1;
    const hadOverlay = !!this._mapOverlay;
    if (this._onMapKey && typeof document !== 'undefined') {
      document.removeEventListener('keydown', this._onMapKey);
      this._onMapKey = null;
    }
    if (this._mapRoute && this._map) {
      try { this._map.removeLayer(this._mapRoute); } catch (_e) { /* ignore */ }
      this._mapRoute = null;
    }
    if (this._mapRouteOutline && this._map) {
      try { this._map.removeLayer(this._mapRouteOutline); } catch (_e) { /* ignore */ }
      this._mapRouteOutline = null;
    }
    [this._mapBoardMarker, this._mapAlightMarker].forEach((marker) => {
      if (!marker || !this._map) return;
      try { this._map.removeLayer(marker); } catch (_e) { /* ignore */ }
    });
    this._mapBoardMarker = null;
    this._mapAlightMarker = null;
    (this._mapRouteLayers || []).forEach((layer) => {
      if (!this._map || !layer) return;
      try { this._map.removeLayer(layer); } catch (_e) { /* ignore */ }
    });
    this._mapRouteLayers = [];
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
    this._mapRef = null;
    this._mapState = null;
    this._mapL = null;
    const back = this._mapReturnFocus;
    this._mapReturnFocus = null;
    if (hadOverlay) this._restoreFocus(back);
  }

  _mapStale(seq) {
    return seq !== this._mapSeq || !this.isConnected;
  }

  _mapKey(row) {
    return `${row.label || ''}|${row.headsign || ''}|${row.vehicleId || row.tripId || ''}`;
  }

  /* What the open map shows, fixed when it opens. _mapKey is not: the
     vehicle id arrives with GPS, and an ELVIRA row takes the FUTÁR trip id,
     so a poll would no longer find the row. */
  _mapRefOf(row) {
    return {
      tripId: String((row && row.tripId) || ''),
      label: String((row && row.label) || ''),
      headsign: String((row && row.headsign) || ''),
      sched: Number((row && (row.schedTs || row.depTs)) || 0),
    };
  }

  _findMapRow(ref, rows) {
    if (!ref || !rows) return null;
    const byTrip = ref.tripId && rows.find((r) => String(r.tripId || '') === ref.tripId);
    if (byTrip) return byTrip;
    if (!ref.sched) return null;
    /* The same departure merged with another source keeps its time, and
       its line or its headsign. */
    return rows.find((r) => Number(r.schedTs || r.depTs || 0) === ref.sched
      && (String(r.label || '') === ref.label || String(r.headsign || '') === ref.headsign)) || null;
  }

  /* The focused element in this card's own tree (a row's map button, for a
     keyboard user), to come back to when the overlay closes. */
  _focusTarget() {
    const el = this.shadowRoot && this.shadowRoot.activeElement;
    return el ? { el, ref: null } : null;
  }

  /* Back where the overlay was opened from. A poll repaints the table, so
     the row's map button may be a new element by then: find that one. */
  _restoreFocus(back) {
    if (!back || !back.el) return;
    let el = back.el;
    if (!el.isConnected && back.ref && this._elBody) {
      const rows = this._shownRows || [];
      const idx = rows.indexOf(this._findMapRow(back.ref, rows));
      el = idx >= 0 ? this._elBody.querySelector(`tr[data-row-index="${idx}"] .map-open`) : null;
    }
    if (!el || !el.isConnected || typeof el.focus !== 'function') return;
    try { el.focus(); } catch (_e) { /* ignore */ }
  }

  /* Stops in the style of a metro map: boarding is a dot in the line's colour,
     alighting a ring, a transfer a neutral ring; the journey's start is a
     blue dot and its end a target. Boarding names sit right of the pin with
     the line's badge, alighting names left, so a transfer between two nearby
     stops shows both. */
  _stopPinIcon(L, pin, lang, withLabel) {
    const esc = BkkLib.esc;
    const word = {
      board: 'mapBoardStop', alight: 'mapAlightStop', transfer: 'mapTransferStop',
      start: 'mapStartPoint', end: 'mapEndPoint',
    }[pin.kind] || 'mapBoardStop';
    const tip = t(lang, word) + (pin.name ? ': ' + pin.name : '');
    const line = BkkLib.normMapHex(pin.color) || '#1a1a1a';
    const ink = BkkLib.normMapHex(pin.text) || BkkLib.mapTextOn(line);
    const badge = pin.label && pin.kind !== 'alight'
      ? `<b class="ht-stop-line">${esc(pin.label)}</b>` : '';
    const label = withLabel && pin.name
      ? `<span class="ht-stop-lbl">${badge}<span class="ht-stop-name">${esc(pin.name)}</span></span>` : '';
    return L.divIcon({
      className: 'ht-stop-pin-wrap',
      html: `<div class="ht-stop-pin ht-stop-${esc(pin.kind)}" style="--line:${esc(line)};--ink:${esc(ink)}"`
        + ` title="${esc(tip)}"><span class="ht-stop-mark"></span>${label}</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
  }

  /* The card's own map overlay lives outside the shadow root; the planner's
     inline map lives inside it. Both include these rules. */
  _stopPinCss() {
    return `
        .ht-stop-pin{position:relative;width:20px;height:20px;display:flex;align-items:center;justify-content:center}
        .ht-stop-mark{width:16px;height:16px;box-sizing:border-box;border-radius:50%;background:var(--line,#1a1a1a);border:3px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 1px 4px rgba(0,0,0,.4)}
        .ht-stop-alight .ht-stop-mark{background:#fff;border:4px solid var(--line,#1a1a1a)}
        .ht-stop-transfer .ht-stop-mark{width:18px;height:18px;background:#fff;border:4px solid #1a1a1a}
        .ht-stop-start .ht-stop-mark{width:14px;height:14px;background:#1a73e8;border:3px solid #fff;box-shadow:0 0 0 4px rgba(26,115,232,.25),0 1px 4px rgba(0,0,0,.4)}
        .ht-stop-end .ht-stop-mark{width:18px;height:18px;background:#1a1a1a;border:3px solid #fff;box-shadow:0 0 0 2px #1a1a1a,0 1px 5px rgba(0,0,0,.45)}
        .ht-stop-lbl{position:absolute;left:19px;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:5px;white-space:nowrap;max-width:220px;padding:3px 9px 3px 4px;border-radius:999px;background:#fff;color:#1f1f1f;font:600 12px/1.2 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3);pointer-events:none}
        .ht-stop-lbl:not(:has(.ht-stop-line)){padding-left:9px}
        .ht-stop-alight .ht-stop-lbl,.ht-stop-end .ht-stop-lbl{left:auto;right:19px}
        .ht-stop-name{overflow:hidden;text-overflow:ellipsis}
        .ht-stop-line{flex:none;min-width:14px;padding:1px 6px;border-radius:999px;background:var(--line,#1a1a1a);color:var(--ink,#fff);font:800 11px/1.25 system-ui,sans-serif;text-align:center}
    `;
  }

  /* The vehicle: a larger disc with its type icon (bus, tram, …), so it is
     not mistaken for the boarding stop's dot in the same colour. */
  _dotIcon(row, live, lang, colorHex) {
    const esc = BkkLib.esc;
    const color = BkkLib.normMapHex(colorHex || (row && row.color)) || '#2E5EA8';
    const ink = BkkLib.normMapHex(row && row.textcolor) || BkkLib.mapTextOn(color);
    const pulse = live ? 'ht-veh-dot-live' : 'ht-veh-dot-est';
    const tip = live ? t(lang, 'mapLiveDot') : t(lang, 'mapEstDot');
    const icon = String((row && row.icon) || 'mdi:bus');
    return `<div class="ht-veh-dot ${pulse}" style="--bg:${esc(color)};--ink:${esc(ink)}" title="${esc(tip)}">`
      + `<span class="ht-veh-dot-core"><ha-icon icon="${esc(icon)}"></ha-icon></span></div>`;
  }

  _mapChromeCss() {
    /* Leaflet CSS (shipped next to the card) can still fail to apply. Without pane
       positioning the SVG route is laid out below the tiles and clipped. */
    return `
        .ht-map-overlay{position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
        .ht-map-panel{width:min(720px,100%);height:min(70vh,560px);background:var(--card-background-color,#fff);border-radius:28px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 12px 40px rgba(0,0,0,.35);color:var(--primary-text-color,#222)}
        .ht-map-head{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--divider-color,rgba(0,0,0,.08));font:600 14px/1.3 system-ui,sans-serif}
        .ht-map-head .meta{flex:1;min-width:0}
        .ht-map-head .sub{font-weight:400;font-size:12px;opacity:.75;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ht-map-close{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;padding:4px 8px;opacity:.7;color:inherit}
        .ht-map-canvas{flex:1;height:420px;min-height:420px;position:relative}
        .ht-map-foot{padding:8px 12px;font:12px/1.35 system-ui,sans-serif;opacity:.85;border-top:1px solid var(--divider-color,rgba(0,0,0,.08))}
        .ht-veh-dot{width:32px;height:32px;display:flex;align-items:center;justify-content:center;filter:drop-shadow(0 1px 3px rgba(0,0,0,.45))}
        .ht-veh-dot-core{width:26px;height:26px;border-radius:50%;background:var(--bg,#2E5EA8);color:var(--ink,#fff);border:2.5px solid #fff;box-sizing:border-box;display:flex;align-items:center;justify-content:center}
        .ht-veh-dot-core ha-icon{--mdc-icon-size:15px;display:flex}
        .ht-veh-dot-live .ht-veh-dot-core{box-shadow:0 0 0 4px color-mix(in srgb, var(--bg,#2E5EA8) 35%, transparent)}
        .ht-veh-dot-est .ht-veh-dot-core{border-style:dashed;opacity:.95}
${this._stopPinCss()}
        .ht-map-canvas .leaflet-container{width:100%;height:100%;min-height:420px;overflow:hidden}
        .ht-map-canvas .leaflet-pane,.ht-map-canvas .leaflet-tile,.ht-map-canvas .leaflet-marker-icon,.ht-map-canvas .leaflet-marker-shadow,.ht-map-canvas .leaflet-tile-container,.ht-map-canvas .leaflet-zoom-box,.ht-map-canvas .leaflet-image-layer,.ht-map-canvas .leaflet-layer{position:absolute;left:0;top:0}
        .ht-map-canvas .leaflet-pane>svg,.ht-map-canvas .leaflet-pane>canvas{position:absolute}
        .ht-map-canvas .leaflet-container svg{max-width:none!important;max-height:none!important}
        .ht-map-canvas .leaflet-tile-pane{z-index:200}
        .ht-map-canvas .leaflet-overlay-pane{z-index:400}
        .ht-map-canvas .leaflet-marker-pane{z-index:600}
    `;
  }

  _mapFootText(row, hasShape, hasGps, estimated, lang, hasBoard, hasAlight) {
    const bits = [];
    if (hasShape) bits.push(t(lang, 'mapFullRoute'));
    if (hasBoard) bits.push('\u25cf ' + t(lang, 'mapBoardStop'));
    if (hasAlight) bits.push('\u25cb ' + t(lang, 'mapAlightStop'));
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
    const back = this._mapReturnFocus || this._focusTarget();
    this._closeVehicleMap();
    this._mapReturnFocus = back;
    const overlay = document.createElement('div');
    overlay.className = 'ht-map-overlay';
    overlay.innerHTML = `
      <style>
        .ht-map-overlay{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:16px}
        .ht-map-notice{background:var(--card-background-color,#fff);padding:16px 18px;border-radius:10px;max-width:360px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.3);color:var(--primary-text-color,#222)}
        .ht-map-notice button{margin-top:12px;border:0;background:var(--primary-color,#03a9f4);color:#fff;padding:8px 12px;border-radius:6px;cursor:pointer}
      </style>
      <div class="ht-map-notice" role="dialog" aria-modal="true">${BkkLib.esc(message)}<br><button type="button">${BkkLib.esc(t(this._lang(), 'mapOk'))}</button></div>
    `;
    document.body.appendChild(overlay);
    this._mapOverlay = overlay;
    const close = () => this._closeVehicleMap();
    const ok = overlay.querySelector('button');
    ok.addEventListener('click', close);
    overlay.addEventListener('click', (ev) => { if (ev.target === overlay) close(); });
    this._onMapKey = (ev) => {
      if (ev.key === 'Escape') close();
    };
    document.addEventListener('keydown', this._onMapKey);
    try { ok.focus(); } catch (_e) { /* ignore */ }
  }

  async _loadRowMapShape(row) {
    const cfg = this._config || {};
    let shape = BkkLib.validMapShape(row && row.shape);
    let board = BkkLib.mapGpsLatLon(cfg.stopLat, cfg.stopLon)
      || BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    /* MÁV nationwide rails from pfaedle OSM rails (not official 8 km chords
       on Székesfehérvár–Kelenföld, and not the Transitous hop cut). */
    if (BkkLib.isTrainRow(row)) {
      try {
        const mav = await BkkLib.withIndexes(cfg).mavRouteForRow(row, cfg.stopName || '', cfg.destName || cfg.destKey || '');
        if (mav && mav.shape.length >= 2) {
          /* The train's own stations, on its own route (MÁV GTFS). */
          const ownBoard = mav.board || board;
          const ownAlight = mav.alight || await this._rowAlightPoint(row, []);
          return {
            shape: mav.shape,
            board: BkkLib.pinOnRoute(ownBoard, [], mav.shape),
            alight: ownAlight && BkkLib.pinOnRoute(ownAlight, [], mav.shape),
          };
        }
      } catch (_e) { /* MOTIS / FUTÁR still fill */ }
    }
    /* GTFS rows (Volán, city feeds) carry their own stops and shapes.txt
       route: no network beyond one shape shard, and no guessing. */
    const gtfsGeo = await BkkLib.gtfsRowShape(row);
    if (gtfsGeo && gtfsGeo.board) board = gtfsGeo.board;
    if (gtfsGeo && gtfsGeo.shape.length >= 2) {
      return { shape: gtfsGeo.shape, board, alight: await this._rowAlightPoint(row, [gtfsGeo.dest]) };
    }
    const tripId = (row && row.tripId) || '';
    const canFutar = this._hasFutar() && (
      !/^motis:/i.test(String(tripId || '')) || !!BkkLib.rowTrainNumber(row)
    );
    let geomTrip = '';
    if (canFutar) {
      geomTrip = BkkLib.isFutarTripId(tripId) ? tripId : '';
      if (!geomTrip && this._hass) {
        const hit = BkkLib.matchHassVehicle(BkkLib.collectHassVehicles(this._hass), row);
        if (hit && BkkLib.isFutarTripId(hit.tripId)) geomTrip = hit.tripId;
      }
    }
    let futarShape = [];
    /* City buses/trams: decode FUTÁR trip-details polyline here.
       hass trip_shape does not exist on bkk_stop. */
    if (geomTrip && this._hasFutar() && !BkkLib.isTrainRow(row)) {
      try {
        futarShape = await BkkLib.futarTripShape(this._apiKey(), this._tripCache || {}, geomTrip);
      } catch (_e) { /* MOTIS hop still fills */ }
    }
    /* Same trip-details response (cached): the exact board and alight stops. */
    let futarHop = null;
    if (geomTrip && this._hasFutar() && cfg.destKey) {
      try {
        futarHop = await BkkLib.tripHopStops(this._apiKey(), this._tripCache || {}, geomTrip,
          { id: cfg.stopId, name: cfg.stopName }, cfg.destKey);
      } catch (_e) { /* Transitous and the config still place the stops */ }
    }
    const published = BkkLib.keepsFullLineShape(row) && !BkkLib.isTrainRow(row)
      && futarShape.length >= 8;
    let motisShape = [];
    let motisCoversHead = true;
    let motisMap = null;
    let motisBoardPt = null;
    const area = await BkkLib.withIndexes(cfg).stopArea(cfg);
    if (!published) {
      try {
        motisMap = await BkkLib.motisShapeForHop(
          row,
          {
            id: cfg.stopId,
            name: cfg.stopName,
            lat: cfg.stopLat != null ? cfg.stopLat : (gtfsGeo && gtfsGeo.board && gtfsGeo.board[0]),
            lon: cfg.stopLon != null ? cfg.stopLon : (gtfsGeo && gtfsGeo.board && gtfsGeo.board[1]),
            area,
          },
          {
            id: cfg.destStopId,
            name: cfg.destName,
            key: cfg.destKey,
            lat: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[0] : undefined,
            lon: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[1] : undefined,
            area,
          },
          { minutesAfter: cfg.minutesAfter, mode: 'all' },
        );
        motisShape = BkkLib.validMapShape(motisMap && motisMap.shape);
        motisCoversHead = !!(motisMap && motisMap.coversHead);
        if (motisMap && motisMap.board) {
          const motisBoard = BkkLib.mapGpsLatLon(motisMap.board[0], motisMap.board[1]);
          motisBoardPt = motisBoard;
          if (motisBoard) {
            const orig = board;
            if (!orig || BkkLib.haversineMeters(orig, motisBoard) <= 300) {
              board = motisBoard;
            }
          }
        }
      } catch (_e) { /* row shape / GPS still draw */ }
    }
    if (futarHop && futarHop.board && (!board || BkkLib.haversineMeters(board, futarHop.board) <= 300)) {
      board = futarHop.board;
    }
    if (!board) {
      board = BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    }
    if (!published && shape.length < 2 && /^gtfs:/i.test(String((row && row.tripId) || '')) && this._hass) {
      try {
        const points = await BkkLib.coachShape(this._hass, row.label, row.headsign || '');
        shape = BkkLib.validMapShape(BkkLib.decodePolyline(points));
      } catch (_e) { /* FUTAR / MOTIS still fill */ }
    }
    if (canFutar && BkkLib.isTrainRow(row) && BkkLib.isFutarTripId(geomTrip)) {
      /* When Transitous misses the train (BAKONY 905 from Szombathely),
         FUTÁR's own trip details still carry the whole train route, along
         the tracks. bkk_stop has no trip_shape service to ask instead. */
      if (futarShape.length < 2 && motisShape.length < 2 && this._hasFutar()) {
        try {
          futarShape = await BkkLib.futarTripShape(this._apiKey(), this._tripCache || {}, geomTrip);
        } catch (_e) { /* the stop pins still show */ }
      }
    }
    /* Direction-aware clip only when this mode does not keep the published line. */
    const clipForeignFutar = (pts) => {
      const valid = BkkLib.validMapShape(pts);
      if (BkkLib.keepsFullLineShape(row)) return valid;
      if (!board || valid.length < 2) return valid;
      const startGap = BkkLib.haversineMeters(valid[0], board);
      if (startGap <= 2500) return valid;
      return BkkLib.clipRideShape(
        valid,
        board,
        BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon)
          || BkkLib.knownStopCoords({
            id: cfg.destStopId, name: cfg.destName, key: cfg.destKey,
          }),
        row,
      );
    };
    if (!BkkLib.keepsFullLineShape(row)) {
      futarShape = clipForeignFutar(futarShape);
    }
    if (published) {
      shape = BkkLib.pickPublishedLineShape(motisShape, futarShape, row, motisCoversHead);
    } else {
      shape = BkkLib.combineHopShapes(
        motisShape,
        futarShape,
        motisCoversHead,
        BkkLib.isTrainRow(row) ? clipForeignFutar(row && row.shape) : (row && row.shape),
      );
      const motisGap = BkkLib.shapeGpsGapMeters(motisShape, row && row.lat, row && row.lon);
      const futarGap = BkkLib.shapeGpsGapMeters(futarShape, row && row.lat, row && row.lon);
      if (motisGap != null && futarGap != null && futarGap + 400 < motisGap && futarGap < 3000) {
        shape = futarShape;
      } else if (motisGap != null && motisGap > 4000 && futarShape.length >= 2
          && (futarGap == null || futarGap < motisGap)) {
        shape = futarShape;
      }
    }
    /* Published line stays whole (8E Újpalota→Kelenföld, not the hop). */
    const destPt = await this._rowAlightPoint(row, [futarHop && futarHop.alight, motisMap && motisMap.dest]);
    const clipped = BkkLib.clipRideShape(shape, board, destPt, row);
    if (clipped.length >= 2) shape = clipped;
    shape = BkkLib.validMapShape(shape);
    /* No shapes.txt route and nothing from Transitous: join the GTFS stops. */
    if (shape.length < 2 && gtfsGeo && gtfsGeo.stopLine.length >= 2) shape = gtfsGeo.stopLine;
    /* Every source came back empty: still show where the ride goes, as a
       line marked approximate, rather than no route at all. */
    let approx = false;
    if (shape.length < 2 && board && destPt && BkkLib.haversineMeters(board, destPt) > 30) {
      shape = [board, destPt];
      approx = true;
    }
    return {
      shape,
      approx,
      board: BkkLib.pinOnRoute(board, [futarHop && futarHop.board, motisBoardPt], shape),
      alight: destPt && BkkLib.pinOnRoute(destPt, [futarHop && futarHop.alight, motisMap && motisMap.dest], shape),
    };
  }

  /* Where the rider gets off: the trip's own stop (GTFS or FUTÁR) first, then
     Transitous's hop end, the configured stop, and a Transitous lookup.
     Cards without a destination have no alighting stop. */
  async _rowAlightPoint(row, candidates) {
    const cfg = this._config || {};
    if (!cfg.destKey && !cfg.destName && !cfg.destStopId) return null;
    const asPt = (p) => (Array.isArray(p) ? BkkLib.mapGpsLatLon(p[0], p[1]) : null);
    const known = (candidates || []).map(asPt).find(Boolean)
      || BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon)
      || BkkLib.knownStopCoords({ id: cfg.destStopId, name: cfg.destName, key: cfg.destKey });
    if (known || !cfg.destName) return known || null;
    try {
      const ref = await BkkLib.transitousStopRef({
        id: cfg.destStopId,
        name: cfg.destName,
        key: cfg.destKey,
        lat: cfg.destLat,
        lon: cfg.destLon,
        area: await BkkLib.withIndexes(cfg).stopArea(cfg),
      }, BkkLib.vehicleBucket(row));
      return (ref && BkkLib.mapGpsLatLon(ref.lat, ref.lon)) || null;
    } catch (_e) {
      return null;
    }
  }

  async _openVehicleMap(row) {
    const lang = this._lang();
    const cfg = this._config || {};
    const key = this._mapKey(row);
    if (this._openingMap === key) return;
    if (this._mapOverlay && this._openMapKey === key) return;
    const back = this._mapReturnFocus || this._focusTarget();
    this._closeVehicleMap();
    if (back && !back.ref) back.ref = this._mapRefOf(row);
    this._mapReturnFocus = back;
    this._openingMap = key;
    const seq = this._mapSeq;
    const gps0 = BkkLib.mapGpsLatLon(row.lat, row.lon);
    let lat = gps0 ? gps0[0] : NaN;
    let lon = gps0 ? gps0[1] : NaN;
    let hasGps = !!gps0;
    let shape = BkkLib.validMapShape(row.shape);
    let hasShape = shape.length >= 2;
    let positionEstimated = false;
    const tripId = row.tripId || '';
    if (!hasGps && !tripId && !hasShape) {
      this._showMapNotice(t(lang, 'mapNoData'));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      if (!this._mapStale(seq)) this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    if (this._mapStale(seq)) return;
    const overlay = document.createElement('div');
    overlay.className = 'ht-map-overlay';
    const sub = this._mapTypeLabel(row.vehicle || row.type, lang)
      + (row.model ? ' \u00b7 ' + row.model : '');
    overlay.innerHTML = `
      <style>${this._mapChromeCss()}</style>
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
    this._openMapKey = key;
    this._mapRef = this._mapRefOf(row);
    this._mapL = L;
    /* What the footer says, filled in as the open learns it; a poll's GPS
       update rewords it from the same state. */
    const st = {
      ready: false, hasShape: false, approx: false, hasGps, estimated: false,
      hasBoard: false, hasAlight: false, hasPosition: hasGps,
    };
    this._mapState = st;
    this._onMapKey = (ev) => {
      if (ev.key === 'Escape') this._closeVehicleMap();
    };
    document.addEventListener('keydown', this._onMapKey);
    const closeBtn = overlay.querySelector('.ht-map-close');
    closeBtn.addEventListener('click', () => this._closeVehicleMap());
    overlay.addEventListener('click', (ev) => {
      if (ev.target === overlay) this._closeVehicleMap();
    });
    try { closeBtn.focus(); } catch (_e) { /* ignore */ }
    const canvas = overlay.querySelector('.ht-map-canvas');
    const foot = overlay.querySelector('.ht-map-foot');
    const startLat = hasGps ? lat : 47.5;
    const startLon = hasGps ? lon : 19.05;
    let map;
    try {
      map = L.map(canvas, { scrollWheelZoom: true, preferCanvas: false }).setView([startLat, startLon], hasGps ? 12 : 8);
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
    this._mapRouteLayers = [];
    const resizeMap = () => {
      if (this._map !== map) return;
      try { map.invalidateSize(); } catch (_e) { /* ignore */ }
    };
    requestAnimationFrame(() => { resizeMap(); requestAnimationFrame(resizeMap); });
    const drawMarker = () => {
      if (!this._map || this._map !== map) return;
      const here = BkkLib.mapGpsLatLon(lat, lon);
      if (!here) return;
      if (this._mapMarker) {
        try { this._map.removeLayer(this._mapMarker); } catch (_e) { /* ignore */ }
      }
      const icon = L.divIcon({
        className: '',
        html: this._dotIcon(row, hasGps && !positionEstimated, lang),
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      });
      this._mapMarker = L.marker(here, { icon: icon, zIndexOffset: 600 }).addTo(map);
    };
    if (hasGps) {
      drawMarker();
      foot.textContent = this._mapFootText(row, false, true, false, lang);
    }
    let board = BkkLib.mapGpsLatLon(cfg.stopLat, cfg.stopLon)
      || BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    let alight = null;
    let approx = false;
    try {
      const got = await this._loadRowMapShape(row);
      shape = BkkLib.validMapShape(got.shape);
      hasShape = shape.length >= 2;
      approx = hasShape && !!got.approx;
      if (got.board) board = got.board;
      alight = got.alight || null;
    } catch (_e) { /* timetable row still shows the live dot */ }
    /* Same spot as the boarding stop (a loop line, a bad lookup): one pin. */
    if (alight && board && BkkLib.haversineMeters(board, alight) < 30) alight = null;
    if (this._mapStale(seq) || !this._map || this._map !== map || !this._mapOverlay) return;
    /* A poll may have placed or moved the vehicle while the route loaded. */
    if (st.hasGps && this._mapMarker && typeof this._mapMarker.getLatLng === 'function') {
      const at = this._mapMarker.getLatLng();
      const got = at && BkkLib.mapGpsLatLon(at.lat, at.lng);
      if (got) {
        lat = got[0];
        lon = got[1];
        hasGps = true;
      }
    }
    const pos = BkkLib.mapGpsLatLon(lat, lon);
    const hasPosition = !!pos;
    if (hasPosition) {
      lat = pos[0];
      lon = pos[1];
    }
    const routeColor = BkkLib.mapRouteColor(row, {});
    const lineBase = {
      lineJoin: 'round', lineCap: 'round', noClip: true, pane: 'overlayPane',
    };
    const applyView = () => {
      try { map.invalidateSize(); } catch (_e) { /* ignore */ }
      const points = [board, alight, hasPosition ? [lat, lon] : null].filter(Boolean);
      if (hasShape) {
        const bounds = L.latLngBounds(shape);
        points.forEach((pt) => bounds.extend(pt));
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 12, animate: false });
      } else if (points.length >= 2) {
        map.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 12, animate: false });
      } else if (points.length) {
        map.setView(points[0], 13, { animate: false });
      }
      (this._mapRouteLayers || []).forEach((layer) => {
        if (layer && typeof layer.redraw === 'function') {
          try { layer.redraw(); } catch (_e) { /* ignore */ }
        }
      });
    };
    if (hasShape) {
      const dash = approx ? { dashArray: '10 10' } : {};
      const outline = L.polyline(shape, Object.assign({
        color: '#1a1a1a', weight: 7, opacity: 0.45, interactive: false,
      }, lineBase, dash)).addTo(map);
      const line = L.polyline(shape, Object.assign({
        color: routeColor, weight: 5, opacity: 0.98,
      }, lineBase, dash)).addTo(map);
      this._mapRouteLayers.push(outline, line);
      this._mapRouteOutline = outline;
      this._mapRoute = line;
    }
    const stopPin = (pin) => L.marker(pin.pt, {
      icon: this._stopPinIcon(L, pin, lang, true), zIndexOffset: 500, keyboard: false,
    }).addTo(map);
    const line = { color: routeColor, label: row.label || '', text: row.textcolor || '' };
    if (alight) {
      this._mapAlightMarker = stopPin({ kind: 'alight', pt: alight, name: cfg.destName || '', color: routeColor });
    }
    if (board) this._mapBoardMarker = stopPin(Object.assign({ kind: 'board', pt: board, name: cfg.stopName || '' }, line));
    if (hasPosition && !this._mapMarker) drawMarker();
    applyView();
    Object.assign(st, {
      ready: true, hasShape, approx, hasGps, estimated: positionEstimated,
      hasBoard: !!board, hasAlight: !!alight, hasPosition,
    });
    foot.textContent = this._mapFoot(row, st, lang);
    /* Closed within these 80 ms, the map is gone: fitBounds on it throws. */
    setTimeout(() => { if (this._map === map) applyView(); }, 80);
    this._openingMap = '';
  }

  _mapFoot(row, st, lang) {
    if (!st.hasShape && !st.hasBoard && !st.hasPosition) return t(lang, 'mapNothingToShow');
    if (!st.hasShape) return t(lang, 'mapNoGeometry');
    const text = this._mapFootText(row, st.hasShape, st.hasGps, st.estimated, lang, st.hasBoard, st.hasAlight);
    return st.approx ? t(lang, 'mapApproxLine') + ' \u00b7 ' + text : text;
  }

  /* A poll moves the vehicle on the open map, and puts it there once GPS
     shows up for a row that had none when the map opened. */
  _syncOpenMap() {
    const st = this._mapState;
    if (!this._map || !this._openMapKey || !st) return;
    const row = this._findMapRow(this._mapRef, this._rows || []);
    if (!row || !row.hasGps) return;
    const gps = BkkLib.mapGpsLatLon(row.lat, row.lon);
    if (!gps) return;
    const lang = this._lang();
    const L = this._mapL || (typeof window !== 'undefined' ? window.L : null);
    const icon = L && typeof L.divIcon === 'function' ? L.divIcon({
      className: '',
      html: this._dotIcon(row, true, lang),
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    }) : null;
    if (this._mapMarker) {
      this._mapMarker.setLatLng(gps);
      if (icon) this._mapMarker.setIcon(icon);
    } else if (icon && typeof L.marker === 'function') {
      try {
        this._mapMarker = L.marker(gps, { icon, zIndexOffset: 600 }).addTo(this._map);
      } catch (_e) {
        return;
      }
    } else {
      return;
    }
    st.hasGps = true;
    st.hasPosition = true;
    st.estimated = false;
    if (!st.ready) return;
    const foot = this._mapOverlay && this._mapOverlay.querySelector('.ht-map-foot');
    if (foot) foot.textContent = this._mapFoot(row, st, lang);
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
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    this._pollRun = null;
    const mode = this._departMode();
    const ready = cfg.stopId && cfg.destKey && (this._hasFutar() || mode === 'volan' || mode === 'helyi' || mode === 'all');
    if (!ready) {
      this._rows = [];
      this._loading = false;
      /* Both stops are set, so the only thing missing is FUTÁR access (bkk
         or mav mode, no key on the card or in the integration): say that
         instead of "no departures". */
      if (cfg.stopId && cfg.destKey) this._err = codedError('errNoApiKey');
      this._paint();
      if (gen === this._gen) this._reloadActive = false;
      return;
    }
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
    const paintRows = (list) => {
      if (gen !== this._gen) return;
      const merged = list || [];
      merged.forEach((row) => BkkLib.applyRailBadge(row));
      this._rawRows = merged;
      BkkLib.enrichFromHass(this._hass, this._rawRows);
      this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
      this._rowsFilled = false;
      this._err = '';
      this._loading = false;
      this._paint();
    };
    const run = async () => {
      if (gen !== this._gen) return;
      /* This card's own index URLs, for every lookup of this run (J-2): never a module-wide setting. */
      const lib = BkkLib.withIndexes(cfg);
      try {
        if (BkkLib.isPlaceStop({ id: cfg.stopId, kind: cfg.stopKind })) {
          this._rows = [];
          this._rawRows = [];
          this._loading = false;
          this._err = '';
          this._journey = null;
          this._journeyKey = '';
          this._journeyLoading = false;
          this._paint();
          if (typeof this._considerJourney === 'function') this._considerJourney();
          return;
        }
        const wantElvira = (mode === 'mav' || mode === 'all') && BkkLib.hasElvira(this._hass);
        const elviraP = wantElvira
          ? lib.elviraBetween(
            this._hass,
            cfg.stopId,
            cfg.destStopId || '',
            /* The service takes 15..480 and rejects the call otherwise. */
            clampMinutesAfter(cfg.minutesAfter),
            cfg.destName || '',
            { apiKey: this._apiKey(), originName: cfg.stopName || '' },
          ).catch(() => [])
          : Promise.resolve([]);
        const cap = maxDepartureRows(cfg.minutesAfter);
        let earlyRows = [];
        let futarFull = null;
        let elviraRows = [];
        const publish = () => {
          if (gen !== this._gen) return;
          let list = futarFull
            ? BkkLib.mergeVolanRows(futarFull, earlyRows, cap)
            : earlyRows.slice();
          list = BkkLib.mergeVolanRows(list, elviraRows, cap, { overlayOnly: true });
          /* Through trips (the destination mid-route) come only from the
             through-check: an empty list before it answers is not "no
             departures". The first load keeps its spinner, a poll its rows. */
          if (!list.length && startThrough && !futarFull) {
            /* FUTÁR answers again: the last run's banner goes. Its
               Transitous fill-ins are not this board's rows (back to the
               spinner); FUTÁR rows it kept stay until the check answers. */
            if (this._err) {
              this._err = '';
              if (this._rowsFilled) {
                this._rows = [];
                this._rawRows = [];
                this._rowsFilled = false;
                this._loading = true;
              }
              this._paint();
            }
            return;
          }
          paintRows(list);
        };
        let startThrough = null;
        let throughResolve = () => {};
        const throughWait = new Promise((resolve) => { throughResolve = resolve; });
        const rows = await lib.departures(
          this._apiKey(), cfg.stopId, cfg.routeIds, dest,
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
              throughResolve();
              if (gen !== this._gen) return;
              futarFull = full || [];
              publish();
            },
          },
        );
        if (gen !== this._gen) return;
        this._journey = null;
        this._journeyKey = '';
        this._journeyLoading = false;
        earlyRows = rows || [];
        publish();
        this._syncOpenMap();
        const firstRows = this._rawRows;
        const needsTravel = (row) => row.travel == null && !/^(gtfs:|elvira:|motis:)/.test(String(row.tripId || ''));
        const fillTravel = (list) => Promise.all((list || []).map(async (row) => {
          if (!needsTravel(row)) return;
          row.travel = await BkkLib.travelMin(
            this._apiKey(), row.tripId, cfg.destKey, row.dep, origin, this._tripCache || {},
          );
        }));
        const gpsTravel = Promise.all([
          BkkLib.attachCoachGps(this._hass, firstRows).then((withGps) => {
            /* A newer list (the through-check) must not be replaced by this
               older one; it gets its GPS on the last pass below. */
            if (gen !== this._gen || this._rawRows !== firstRows) return;
            this._rawRows = withGps;
            this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
            this._paint();
            this._syncOpenMap();
          }),
          fillTravel(firstRows),
        ]);
        if (startThrough) {
          startThrough();
        } else {
          throughResolve();
        }
        elviraRows = await elviraP;
        if (gen !== this._gen) return;
        publish();
        await throughWait;
        if (gen !== this._gen) return;
        let merged = futarFull
          ? BkkLib.mergeVolanRows(futarFull, earlyRows, cap)
          : earlyRows.slice();
        merged = BkkLib.mergeVolanRows(merged, elviraRows, cap, { overlayOnly: true });
        /* A city board is filled from Transitous inside departures() already:
           a second fill here only repeated the same lookups on every poll. */
        if (!merged.length && mode !== 'helyi') {
          merged = await BkkLib.fillTransitousIfEmpty(merged, origin, dest, {
            mode: this._departMode(),
            minutesAfter: cfg.minutesAfter,
          });
          if (gen !== this._gen) return;
          merged = BkkLib.mergeVolanRows(merged, elviraRows, cap, { overlayOnly: true });
          earlyRows = merged;
          paintRows(merged);
        }
        if (typeof this._considerJourney === 'function') this._considerJourney();
        await gpsTravel;
        if (gen !== this._gen) return;
        /* Rows the through-check added (the destination mid-route) are new
           objects: their ride time and coach GPS are filled here. Trip
           details are cached by then, so this costs no request. */
        if (this._rawRows !== firstRows) {
          const latest = this._rawRows;
          const [withGps] = await Promise.all([
            BkkLib.attachCoachGps(this._hass, latest),
            fillTravel(latest),
          ]);
          if (gen !== this._gen || this._rawRows !== latest) return;
          this._rawRows = withGps;
        }
        this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
        this._paint();
        this._syncOpenMap();
      } catch (err) {
        if (gen !== this._gen) return;
        this._loading = false;
        /* FUTÁR failing (a revoked key, an outage) used to look like an
           empty board, which Transitous then filled. It still fills it,
           now under the error banner instead of in silence. */
        if (mode === 'bkk' || mode === 'mav') {
          let filled = [];
          try {
            filled = await BkkLib.fillTransitousIfEmpty([], origin, dest, {
              mode,
              minutesAfter: cfg.minutesAfter,
            });
          } catch (_e) {
            filled = [];
          }
          if (gen !== this._gen) return;
          if (filled.length) {
            paintRows(filled);
            this._rowsFilled = true;
          }
        }
        this._showErr(err);
      }
    };
    this._loading = !(this._rows || []).length;
    this._paint();
    try {
      await run();
      if (!this.isConnected || gen !== this._gen || !this._shouldPoll()) return;
      this._pollRun = { gen, run, busy: false };
      /* Hidden tab: the visibilitychange handler picks the poll up again. */
      if (this._pageHidden()) return;
      this._armPoll(this._pollRun);
    } finally {
      if (gen === this._gen) this._reloadActive = false;
    }
  }
}

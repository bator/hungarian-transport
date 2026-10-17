import { BKK_FAVORITES, DEFAULT_MINUTES_AFTER, MAV_FAVORITES, MINUTES_AFTER_PRESETS, VOLAN_FAVORITES, clampMinutesAfter, departureStillDue } from './config.js';
import { BKKHopCard } from './hop-card.js';
import { t } from './i18n.js';
import { BkkLib } from './lib/core.js';
import { BKK_PLANNER_EDITOR } from './tags.js';

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

  _shouldPoll() {
    return false;
  }

  _shouldReloadOnConnect() {
    return !(this._journey || this._journeyLoading || (this._rows && this._rows.length));
  }

  /* A full paint remounts the inline map. Leave the countdown frozen until
     the user changes origin, destination or the look-ahead. */
  _startTick() {}

  _considerJourney() {
    const cfg = this._config || {};
    const horizon = clampMinutesAfter(cfg.minutesAfter);
    const key = [cfg.stopId, cfg.destStopId || '', cfg.destName, horizon].join('|');
    if (!cfg.stopId || !cfg.destName) return;
    if (this._journeyKey === key && (this._journeyLoading || this._journey)) return;
    const gen = this._gen;
    this._journeyKey = key;
    this._journeyLoading = true;
    BkkLib.planJourney(
      this._apiKey(),
      { id: cfg.stopId, name: cfg.stopName || '', lat: cfg.stopLat, lon: cfg.stopLon },
      { id: cfg.destStopId || '', name: cfg.destName || '', lat: cfg.destLat, lon: cfg.destLon },
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

  _emptyBodyMessage(lang, cfg) {
    if (this._journey && this._journey.legs && this._journey.legs.length) return '';
    if (this._journeyLoading) return t(lang, 'cardLoading');
    return super._emptyBodyMessage(lang, cfg);
  }

  _paint() {
    this._teardownPlannerInlineMap();
    super._paint();
    this._paintJourney();
  }

  disconnectedCallback() {
    this._teardownPlannerInlineMap();
    super.disconnectedCallback();
  }

  connectedCallback() {
    super.connectedCallback();
    if (!this._painted || this._plannerMap) return;
    const host = this._elBody && this._elBody.querySelector('.journey-plan');
    if (host && host.querySelector('#pjmap')) this._mountPlannerInlineMap(host);
  }

  _teardownPlannerInlineMap() {
    if (this._plannerMap) {
      try { this._plannerMap.remove(); } catch (_e) { /* ignore */ }
      this._plannerMap = null;
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
    const pin = (pt, fill) => {
      if (!pt) return;
      L.circleMarker(pt, {
        radius: 7, color: '#fff', weight: 2, fillColor: fill, fillOpacity: 1,
      }).addTo(map);
    };
    pin(segs[0].shape[0], '#0F6FC6');
    for (let i = 0; i < segs.length - 1; i++) {
      const shape = segs[i].shape;
      pin(shape[shape.length - 1], '#555555');
    }
    const lastSeg = segs[segs.length - 1].shape;
    pin(lastSeg[lastSeg.length - 1], '#0F6FC6');
    return allPts;
  }

  _fitJourneyView(L, map, allPts, pad) {
    if (!map || !allPts || allPts.length < 2) return;
    const bounds = L.latLngBounds(allPts);
    const apply = () => {
      try {
        map.invalidateSize();
        map.fitBounds(bounds, { padding: [pad, pad], maxZoom: 14 });
      } catch (_e) { /* ignore */ }
    };
    apply();
    requestAnimationFrame(() => { apply(); requestAnimationFrame(apply); });
    setTimeout(apply, 200);
  }

  _paintJourney() {
    if (!this._elBody) return;
    this._teardownPlannerInlineMap();
    const prev = this._elBody.querySelector('.journey-plan');
    if (prev) prev.remove();
    const cfg = this._config || {};
    if (!cfg.stopId || !cfg.destName) return;
    const lang = this._lang();
    const esc = BkkLib.esc;
    const nowSec = Math.floor(Date.now() / 1000);
    const visible = (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
    const journey = this._journey;
    const mapBtn = `<button type="button" id="pmap">${esc(t(lang, 'plannerShowMap'))}</button>`;
    const bindMap = (box, useBoard) => {
      this._elBody.insertBefore(box, this._elBody.firstChild);
      const btn = box.querySelector('#pmap');
      if (btn) {
        btn.addEventListener('click', () => {
          if (!useBoard) return this._openJourneyMap();
          const first = visible[0];
          if (first) return this._openVehicleMap(first);
          this._showMapNotice(t(lang, 'mapNoData'));
        });
      }
    };
    const showJourney = !!(journey && journey.legs && journey.legs.length
      && (!visible.length || BkkLib.journeyBeatsBoard(journey, visible)));
    if (visible.length && !showJourney) {
      const box = document.createElement('div');
      box.className = 'journey-plan j-map-only';
      box.innerHTML = mapBtn;
      bindMap(box, true);
      return;
    }
    if (!showJourney) return;
    const legs = journey.legs.map((leg) => {
      if (leg.walk) {
        const walkKey = leg.stationWalk ? 'plannerStationWalk' : 'plannerWalk';
        return `<div class="j-leg j-walk"><span class="j-mode">${esc(t(lang, walkKey, [leg.minutes, leg.meters]))}</span>`
          + `<span class="j-where">${esc(leg.from)} \u2192 ${esc(leg.to)}</span></div>`;
      }
      const wait = leg.waitMin >= 1
        ? `<div class="j-leg j-wait">${esc(t(lang, 'plannerWait', leg.waitMin))}</div>`
        : '';
      const ride = leg.label
        ? `<span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label)}</span>`
        : '';
      return wait + `<div class="j-leg">${ride}<span class="j-where">${esc(leg.headsign || leg.to)}</span>`
        + `<span class="j-min">${esc(t(lang, 'plannerRide', leg.minutes))}</span></div>`;
    }).join('');
    const box = document.createElement('div');
    box.className = 'journey-plan';
    box.innerHTML = `<div class="j-title">${esc(t(lang, 'plannerJourneyTitle'))}</div>`
      + `<div class="j-sum">${esc(t(lang, 'plannerJourneySummary', [journey.durationMin, journey.transfers, journey.walkMin, journey.waitMin || 0]))}</div>`
      + `<div id="pjmap" class="j-map" hidden></div>`
      + mapBtn
      + legs;
    bindMap(box, false);
    this._mountPlannerInlineMap(box);
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
      }).setView(start, 8);
      this._plannerMap = map;
      L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
        maxZoom: 20,
        subdomains: 'abc',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);
      const allPts = this._drawJourneyPolylines(L, map, segs, { interactive: false });
      map.on('click', () => this._openJourneyMap());
      this._fitJourneyView(L, map, allPts, 16);
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
    try {
      await BkkLib.fillJourneyShapes(this._journey);
    } catch (_e) { /* draw the endpoint fallback */ }
    let segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
    if (!segs.length && cfg.stopId && cfg.destName) {
      try {
        const journey = await BkkLib.planJourney(
          this._apiKey(),
          { id: cfg.stopId, name: cfg.stopName || '', lat: cfg.stopLat, lon: cfg.stopLon },
          { id: cfg.destStopId || '', name: cfg.destName || '', lat: cfg.destLat, lon: cfg.destLon },
          this._hass,
          { minutesAfter: clampMinutesAfter(cfg.minutesAfter) },
        );
        if (journey) this._journey = journey;
        try {
          await BkkLib.fillJourneyShapes(this._journey);
        } catch (_e) { /* draw the endpoint fallback */ }
        segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
      } catch (_e) { /* fall through to the no-geometry notice */ }
    }
    if (!segs.length) {
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapNoData'));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
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
      L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
        maxZoom: 20,
        subdomains: 'abc',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://www.openstreetmap.fr/">OpenStreetMap France</a>',
      }).addTo(map);
    } catch (_e) {
      this._openingMap = '';
      this._showMapNotice(t(lang, 'mapLeafletFail'));
      return;
    }
    this._map = map;
    const allPts = this._drawJourneyPolylines(L, map, segs);
    this._fitJourneyView(L, map, allPts, 36);
    this._openingMap = '';
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
      ha-card.is-planner .wrap { padding: 4px 4px 8px; }
      ha-card.is-planner .head {
        font-size: 16px; font-weight: 700; letter-spacing: -0.02em;
        margin: 0; padding: 4px 10px 0; white-space: normal; line-height: 1.2;
      }
      ha-card.is-planner .deps-label {
        margin: 8px 10px 2px; font-size: 11px; font-weight: 700;
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
      .plan { margin: 4px 6px 0; }
      .journey {
        display: grid; grid-template-columns: 32px minmax(0, 1fr); column-gap: 6px;
        padding: 8px; border-radius: var(--ha-card-border-radius, 12px);
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .spine { display: flex; flex-direction: column; align-items: center; padding-top: 18px; }
      .spine .dot { width: 10px; height: 10px; border-radius: 50%; box-sizing: border-box; flex: 0 0 auto; }
      .spine .dot.from { background: var(--primary-color); }
      .spine .dot.to { background: var(--card-background-color, #fff); border: 2px solid var(--primary-color); }
      .spine .stem { width: 2px; flex: 1 1 8px; min-height: 8px; background: var(--divider-color); }
      .spine #pswap {
        margin: 4px 0; border: 0; cursor: pointer; font: inherit; font-size: 11px; font-weight: 700;
        border-radius: 999px; padding: 4px 6px;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .place + .place { margin-top: 8px; }
      .plan .label { font-size: 11px; font-weight: 600; letter-spacing: 0.02em;
        color: var(--secondary-text-color); margin-bottom: 3px; }
      .plan input {
        width: 100%; box-sizing: border-box; font: inherit; font-size: 14px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 8px; padding: 7px 10px;
      }
      .plan input:focus { outline: 2px solid var(--primary-color); outline-offset: 1px; }
      .plan input.compact-hide { display: none; }
      .plan .horizon {
        display: flex; flex-direction: row; align-items: center; gap: 8px;
        margin-top: 6px; padding: 0 2px;
      }
      .plan .horizon .label { margin: 0; flex: 0 0 auto; }
      .plan .horizon select {
        flex: 1 1 auto; min-width: 0; box-sizing: border-box;
        font: inherit; font-size: 13px; font-weight: 650;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 8px; padding: 5px 8px;
      }
      .plan #preset {
        border: 0; background: transparent; color: var(--primary-color);
        font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; padding: 4px 2px;
        flex: 0 0 auto;
      }
      .plan .suggest { margin-top: 4px; display: flex; flex-direction: row; flex-wrap: wrap; gap: 4px; }
      .plan .suggest.hidden { display: none; }
      .plan .suggest-row, .plan .suggest-stops { display: contents; }
      .plan .suggest-kind { display: none; }
      .plan .suggest-stop {
        display: inline-flex; align-items: center; gap: 3px;
        border: 0; border-radius: 6px; cursor: pointer;
        font: inherit; font-size: 11px; font-weight: 650; padding: 3px 7px 3px 5px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
      }
      .plan .suggest-feed {
        font-size: 9px; font-weight: 800; letter-spacing: 0.02em; line-height: 1;
        color: var(--primary-color);
      }
      .plan .suggest-row[data-kind="volan"] .suggest-feed { color: var(--warning-color, #f0b429); }
      .plan .suggest-row[data-kind="mav"] .suggest-feed { color: var(--info-color, #03a9f4); }
      .plan .suggest-row[data-kind="bkk"] .suggest-stop { box-shadow: inset 2px 0 0 var(--primary-color); }
      .plan .suggest-row[data-kind="volan"] .suggest-stop { box-shadow: inset 2px 0 0 var(--warning-color, #f0b429); }
      .plan .suggest-row[data-kind="mav"] .suggest-stop { box-shadow: inset 2px 0 0 var(--info-color, #03a9f4); }
      .plan .list { max-height: 180px; overflow: auto; margin-top: 4px; }
      .plan .list:empty { display: none; }
      .plan .hit {
        display: block; width: 100%; text-align: left; font: inherit; font-size: 14px; font-weight: 650;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 0; border-radius: 8px; padding: 7px 10px; cursor: pointer; margin-top: 4px;
      }
      .plan .picked {
        display: none; margin-top: 4px; font: inherit; font-size: 14px; font-weight: 700; line-height: 1.25;
        padding: 5px 8px; border: 0; border-radius: 8px; width: 100%; text-align: left;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        cursor: pointer;
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
      .journey-plan .j-map,
      .journey-plan .j-map.leaflet-container {
        height: 220px; min-height: 220px; margin: 0 0 10px; border-radius: 10px;
        overflow: hidden; position: relative; cursor: pointer;
        background: var(--card-background-color, #fff);
      }
      .journey-plan .j-map[hidden] { display: none; }
      .journey-plan .j-map.leaflet-container,
      .journey-plan .j-map .leaflet-container{width:100%;height:100%;min-height:220px;overflow:hidden}
      .journey-plan .j-map .leaflet-pane,.journey-plan .j-map .leaflet-tile,.journey-plan .j-map .leaflet-marker-icon,.journey-plan .j-map .leaflet-marker-shadow,.journey-plan .j-map .leaflet-tile-container,.journey-plan .j-map .leaflet-zoom-box,.journey-plan .j-map .leaflet-image-layer,.journey-plan .j-map .leaflet-layer{position:absolute;left:0;top:0}
      .journey-plan .j-map .leaflet-pane>svg,.journey-plan .j-map .leaflet-pane>canvas{position:absolute}
      .journey-plan .j-map svg,.journey-plan .j-map .leaflet-container svg{max-width:none!important;max-height:none!important}
      .journey-plan .j-map .leaflet-tile{max-width:none!important}
      .journey-plan .j-map .leaflet-tile-pane{z-index:200}
      .journey-plan .j-map .leaflet-overlay-pane{z-index:400}
      .journey-plan .j-map .leaflet-marker-pane{z-index:600}
      .journey-plan .j-map .leaflet-control-attribution{
        font-size:10px;background:rgba(255,255,255,.72);
      }
      .journey-plan #pmap {
        margin: 0 0 10px; border: 0; cursor: pointer; font: inherit; font-size: 13px; font-weight: 700;
        border-radius: 999px; padding: 8px 14px;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .journey-plan.j-map-only { margin: 8px 8px 0; padding: 8px 12px; }
      .journey-plan.j-map-only #pmap { margin: 0; }
      .journey-plan .j-leg { display: flex; align-items: baseline; gap: 8px;
        padding: 6px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
      .journey-plan .j-where { flex: 1; min-width: 0; }
      .journey-plan .j-min { font-weight: 700; }
      .journey-plan .j-badge {
        flex: 0 0 auto; border-radius: 8px; padding: 2px 8px; font-weight: 700;
      }
      .journey-plan .j-walk, .journey-plan .j-wait { color: var(--secondary-text-color); }
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
            <button type="button" class="picked" id="pstop"></button>
            <div class="suggest" id="pfav"></div>
            <div class="list" id="phits"></div>
          </div>
          <div class="place">
            <div class="label" id="plab2">${BkkLib.esc(t(lang, 'plannerStep2'))}</div>
            <input id="pdq" type="search" placeholder="${BkkLib.esc(t(lang, 'plannerDestPlaceholder'))}">
            <button type="button" class="picked" id="pdest"></button>
            <div class="list" id="pdhits"></div>
          </div>
        </div>
      </div>
      <div class="horizon">
        <div class="label" id="plab3">${BkkLib.esc(t(lang, 'plannerStep3'))}</div>
        <select id="pmin" aria-label="${BkkLib.esc(t(lang, 'plannerStep3'))}"></select>
        <button id="preset" type="button">${BkkLib.esc(t(lang, 'plannerReset'))}</button>
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
    const list = BkkLib.collapseSameNameStops(
      BKK_FAVORITES.concat(VOLAN_FAVORITES, MAV_FAVORITES),
    );
    const row = document.createElement('div');
    row.className = 'suggest-row';
    const stops = document.createElement('span');
    stops.className = 'suggest-stops';
    list.forEach((fav) => {
      const id = String(fav.id || '');
      const mark = /^BKK_0055/i.test(id) ? 'M'
        : /^(?:volan_|hkir_|AREA_CS)/i.test(id) ? 'V'
        : 'B';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'suggest-stop';
      const feed = document.createElement('span');
      feed.className = 'suggest-feed';
      feed.textContent = mark;
      button.appendChild(feed);
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
    if (dest) {
      dest.textContent = cfg.destName || '';
      dest.classList.toggle('show', !!cfg.destName);
    }
    if (q) {
      q.placeholder = t(lang, 'plannerSearchPlaceholder');
      const editing = document.activeElement === q || !!(q.value || '').trim();
      q.classList.toggle('compact-hide', !!cfg.stopName && !editing);
    }
    if (dq) {
      dq.placeholder = t(lang, 'plannerDestPlaceholder');
      const editing = document.activeElement === dq || !!(dq.value || '').trim();
      dq.classList.toggle('compact-hide', !!cfg.destName && !editing);
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
    const sel = this._planEl('pmin');
    if (!sel || sel.tagName !== 'SELECT') return;
    const current = clampMinutesAfter(this._config && this._config.minutesAfter);
    if (!sel.options.length) {
      MINUTES_AFTER_PRESETS.forEach((n) => {
        const o = document.createElement('option');
        o.value = String(n);
        o.textContent = String(n);
        sel.appendChild(o);
      });
      sel.addEventListener('change', () => {
        const n = clampMinutesAfter(sel.value);
        this._config = Object.assign({}, this._config, { minutesAfter: n });
        this._reloadSafe();
      });
    }
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
    this._dests = [];
    this._destRoutes = {};
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
    this._paint();
    this._loadDests().then(() => this._reloadSafe());
  }

  _resetPlan() {
    this._dests = [];
    this._destRoutes = {};
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
      if (!this._apiKey()) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const hits = await BkkLib.searchPlannerStops(this._apiKey(), q);
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), 'mode').all.empty) + '</span>';
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

  async _pickOrigin(stop) {
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
    this._dests = [];
    this._destRoutes = {};
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
      if (!this._apiKey()) await this._fillKey();
      const placeOrigin = /^(geo:|node\/)/i.test(String(cfg.stopId || ''))
        || cfg.stopKind === 'address' || cfg.stopKind === 'place';
      if (placeOrigin) {
        this._dests = [];
        this._destRoutes = {};
      } else {
        const out = await BkkLib.reachableDests(
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
      if (!this._apiKey()) await this._fillKey();
      const extras = await BkkLib.searchPlannerStops(this._apiKey(), q);
      if (seq !== this._destSearchSeq) return;
      const hits = BkkLib.destHitsForQuery(this._dests, q, extras);
      if (!hits.length && this._destsLoading) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'destLoading')) + '</span>';
        return;
      }
      this._renderDestHits(hits, t(lang, 'mode').all.empty);
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
      `<button class="hit" type="button" data-key="${BkkLib.esc(d.key)}" data-id="${BkkLib.esc(d.id || '')}">${BkkLib.esc(BkkLib.plannerHitLabel(d, this._lang()))}</button>`
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
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(lang, 'plannerTypeDest')) + '</span>';
      return;
    }
    const hits = BkkLib.destHitsForQuery(this._dests, q, []);
    this._renderDestHits(hits, t(lang, 'mode').all.destEmpty);
  }

  _pickDest(dest) {
    this._config = Object.assign({}, this._config, {
      destKey: dest.key,
      destName: dest.name,
      destStopId: dest.id || '',
      destLat: dest.lat,
      destLon: dest.lon,
      destKind: dest.kind || '',
      routeIds: this._idsForDest(dest.key),
    });
    const dq = this._planEl('pdq');
    const hits = this._planEl('pdhits');
    if (dq) dq.value = '';
    if (hits) hits.innerHTML = '';
    this._syncPickerLabels();
    this._reloadSafe();
  }
}

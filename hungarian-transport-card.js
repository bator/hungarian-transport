/* crusand Hungarian transport v29 */
(function defineNow() {
  const TAG = 'bkk-stop-card-r3';
  const ED = 'bkk-stop-card-r3-editor';
  if (!customElements.get(ED)) {
    class BkkStopCardR3Editor extends HTMLElement {
      constructor() {
        super();
        this._config = { routeIds: [] };
        this._dests = [];
        this._cache = {};
        this._built = false;
      }
      setConfig(config) { this._config = Object.assign({ routeIds: [] }, config || {}); }
      set hass(hass) { this._hass = hass; }
    }
    customElements.define(ED, BkkStopCardR3Editor);
  }
  if (!customElements.get(TAG)) {
    class BkkStopCardR3 extends HTMLElement {
      constructor() {
        super();
        try { if (!this.shadowRoot) this.attachShadow({ mode: 'open' }); } catch (_e) {}
        this._config = {};
      }
      static async getConfigElement() {
        if (!customElements.get(ED)) await customElements.whenDefined(ED);
        return document.createElement(ED);
      }
      static getStubConfig() {
        return { name: '', stopId: '', stopName: '', routeIds: [], destKey: '', destName: '', mav: false, volan: false };
      }
      setConfig(config) {
        this._config = Object.assign({ routeIds: [] }, config || {});
        if (typeof this._renderHop === 'function') {
          try { this._renderHop(); this._reloadHop(); } catch (err) {
            if (this.shadowRoot) {
              this.shadowRoot.innerHTML = '<ha-card><div style="padding:16px;color:var(--error-color)">' +
                String(err && err.message ? err.message : err) + '</div></ha-card>';
            }
          }
        } else if (this.shadowRoot) {
          this.shadowRoot.innerHTML = '<ha-card><div style="padding:16px">Hungarian transport</div></ha-card>';
        }
      }
      set hass(hass) {
        this._hass = hass;
        if (typeof this._onHass === 'function') this._onHass(hass);
      }
      getCardSize() { return 1; }
    }
    customElements.define(TAG, BkkStopCardR3);
  }
  console.info('[hungarian-transport] defined v29');
})();

/* hop + editor + shared BkkLib */
const BKK_PLANNER_TAG = 'bkk-stop-card-plan';
const BKK_API = 'https://go.bkk.hu/api/query/v1/ws/otp/api/where';

const BKK_FAVORITES = [
  { id: 'BKK_F02847', name: 'Miskolci utca / Cs\u00f6m\u00f6ri \u00fat' },
  { id: 'BKK_011754', name: 'R\u00e1kospatak utca / Cs\u00f6m\u00f6ri \u00fat' },
  { id: 'BKK_F01159', name: 'Keleti p\u00e1lyaudvar M' },
  { id: 'BKK_056233', name: 'Keleti p\u00e1lyaudvar' },
  { id: 'BKK_056216', name: 'Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
  { id: 'BKK_F03151', name: 'F\u0151 t\u00e9r' },
  { id: 'BKK_F02731', name: 'Zugl\u00f3 vas\u00fat\u00e1llom\u00e1s' },
  { id: 'BKK_F01506', name: 'Mester utca / K\u00f6nyves K\u00e1lm\u00e1n k\u00f6r\u00fat' },
  { id: 'BKK_F01428', name: 'Tim\u00f3t utca' },
];
const MAV_FAVORITES = [
  { id: 'BKK_005501024', name: 'Budapest-Kelenf\u00f6ld' },
  { id: 'BKK_005503269', name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r' },
  { id: 'BKK_005510017', name: 'Budapest-Keleti' },
  { id: 'BKK_005510090', name: 'Zugl\u00f3' },
];
const VOLAN_FAVORITES = [
  { id: 'hkir_773521', name: 'Budapest, N\u00e9pliget aut\u00f3busz-p\u00e1lyaudvar' },
  { id: 'volan_773538_99', name: 'Budapest, Kelenf\u00f6ld vas\u00fat\u00e1llom\u00e1s' },
  { id: 'AREA_CS665908_99', name: 'Sz\u00e9kesfeh\u00e9rv\u00e1r, aut\u00f3busz-\u00e1llom\u00e1s' },
  { id: 'AREA_CS665201_99', name: 'Duna\u00fajv\u00e1ros, aut\u00f3busz-\u00e1llom\u00e1s' },
  { id: 'volan_669685_99', name: 'Tatab\u00e1nya, aut\u00f3busz-\u00e1llom\u00e1s' },
];
function volanIndexUrl() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `/local/community/bkk-stop-card/volan-index.json.gz?v=${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
}
const FAVORITES = BKK_FAVORITES.concat(MAV_FAVORITES);

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
    this._poll = null;
    this._routeCache = {};
  }

  disconnectedCallback() {
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    if (this._searchTimer) { clearTimeout(this._searchTimer); this._searchTimer = null; }
  }

  setConfig(config) {
    if (!this.shadowRoot) {
      try { this.attachShadow({ mode: 'open' }); } catch (_e) { /* already attached */ }
    }
    this._config = Object.assign({}, config);
    this._apiKey = config.apiKey || '';
    this._renderShell();
  }

  set hass(_hass) {
    if (!this._root) this._renderShell();
  }

  getCardSize() {
    return 8;
  }

  _renderShell() {
    const root = this.shadowRoot;
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
    wrap.innerHTML = `
      <div class="title">${this._esc(this._config.name || 'BKK \u00fatvonal')}</div>
      <div class="step">
        <div class="label">1. Meg\u00e1ll\u00f3</div>
        <div class="row">
          <input id="q" type="search" placeholder="Utca, meg\u00e1ll\u00f3...">
          <button class="clear" id="reset" type="button">\u00daj</button>
        </div>
        <div class="chips" id="fav"></div>
        <div class="picked" id="stopPicked"></div>
        <div class="list" id="hits"></div>
      </div>
      <div class="step">
        <div class="label">2. J\u00e1rm\u0171</div>
        <div class="hint">T\u00f6bbet is v\u00e1laszthatsz. Ekkor csak a k\u00f6z\u00f6s meg\u00e1ll\u00f3k maradnak.</div>
        <div class="chips" id="routes"></div>
      </div>
      <div class="step">
        <div class="label">3. Meddig</div>
        <select id="dest" disabled><option value="">El\u0151bb v\u00e1lassz j\u00e1rm\u0171vet</option></select>
      </div>
      <div class="step">
        <div class="label">Indul\u00e1sok</div>
        <div id="table" class="muted">V\u00e1lassz meg\u00e1ll\u00f3t, j\u00e1rm\u0171vet \u00e9s c\u00e9lt.</div>
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
    return String(s || '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
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
    this._el('dest').innerHTML = '<option value="">El\u0151bb v\u00e1lassz j\u00e1rm\u0171vet</option>';
    this._el('dest').disabled = true;
    this._el('table').innerHTML = 'V\u00e1lassz meg\u00e1ll\u00f3t, j\u00e1rm\u0171vet \u00e9s c\u00e9lt.';
    this._el('err').textContent = '';
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
  }

  _error(msg) { this._el('err').textContent = msg || ''; }

  async _bkk(path, params) {
    if (!this._apiKey) throw new Error('Hi\u00e1nyzik az apiKey a k\u00e1rtya be\u00e1ll\u00edt\u00e1s\u00e1b\u00f3l.');
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
    try {
      this._error('');
      const byId = new Map();
      const queries = [q].concat(this._aliasQueries(q));
      for (let i = 0; i < queries.length; i++) {
        const data = await this._bkk('search.json', { query: queries[i] });
        const stops = (((data.data || {}).references) || {}).stops || {};
        Object.values(stops).forEach((s) => {
          if (s && s.id && s.name) byId.set(s.id, s);
        });
      }
      let hits = this._groupStops(Array.from(byId.values()));
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
      this._error(err.message || String(err));
    }
  }

  async _pickStop(stop) {
    this._stop = stop;
    this._selected = new Set();
    this._dest = null;
    this._el('q').value = stop.label || stop.name;
    this._el('hits').innerHTML = '';
    this._el('stopPicked').textContent = stop.label || stop.name;
    this._el('routes').innerHTML = '<span class="muted">Bet\u00f6lt\u00e9s...</span>';
    this._el('dest').disabled = true;
    try {
      this._error('');
      await this._loadRoutes();
      this._paintRoutes();
      await this._refreshDests();
    } catch (err) {
      this._error(err.message || String(err));
    }
  }

  async _loadRoutes() {
    const data = await this._bkk('arrivals-and-departures-for-stop.json', {
      stopId: this._stop.id,
      minutesAfter: '90',
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
      box.innerHTML = '<span class="muted">Nincs indul\u00f3 j\u00e1rat ebben a meg\u00e1ll\u00f3ban.</span>';
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
    const originN = this._norm(this._stop.name);
    const names = [];
    const seen = new Set();
    variants.forEach((v) => {
      let idx = v.ids.findIndex((id) => this._sameStop(id, origin));
      if (idx < 0) {
        idx = v.ids.findIndex((id) => this._norm((stops[id] || {}).name) === originN);
      }
      if (idx < 0) return;
      v.ids.slice(idx + 1).forEach((id) => {
        const raw = (stops[id] || {}).name;
        const n = this._norm(raw);
        if (!raw || !n || seen.has(n)) return;
        seen.add(n);
        names.push({ key: n, name: raw, id });
      });
    });
    return names;
  }

  async _refreshDests() {
    const sel = this._el('dest');
    this._dest = null;
    if (!this._selected.size) {
      sel.innerHTML = '<option value="">El\u0151bb v\u00e1lassz j\u00e1rm\u0171vet</option>';
      sel.disabled = true;
      this._el('table').innerHTML = 'V\u00e1lassz meg\u00e1ll\u00f3t, j\u00e1rm\u0171vet \u00e9s c\u00e9lt.';
      return;
    }
    sel.innerHTML = '<option value="">Bet\u00f6lt\u00e9s...</option>';
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
      sel.innerHTML = '<option value="">Nincs k\u00f6z\u00f6s meg\u00e1ll\u00f3</option>';
      sel.disabled = true;
      this._el('table').innerHTML = 'Ezeknek a j\u00e1ratoknak nincs k\u00f6z\u00f6s k\u00e9s\u0151bbi meg\u00e1ll\u00f3ja.';
      return;
    }
    sel.disabled = false;
    sel.innerHTML = '<option value="">V\u00e1lassz c\u00e9lt...</option>' + common.map((d) => (
      `<option value="${this._esc(d.key)}">${this._esc(d.name)}</option>`
    )).join('');
  }

  async _loadDepartures() {
    if (!this._stop || !this._dest || !this._selected.size) return;
    this._el('table').innerHTML = '<span class="muted">Bet\u00f6lt\u00e9s...</span>';
    try {
      this._error('');
      const data = await this._bkk('arrivals-and-departures-for-stop.json', {
        stopId: this._stop.id,
        minutesAfter: '90',
        minutesBefore: '0',
        onlyDepartures: 'true',
        includeReferences: 'true',
        includeVehicleFromTrip: 'true',
      });
      const refs = (data.data || {}).references || {};
      const routes = refs.routes || {};
      const trips = refs.trips || {};
      const times = ((data.data || {}).entry || {}).stopTimes || [];
      const rows = [];
      for (const st of times) {
        const trip = trips[st.tripId] || {};
        const rid = trip.routeId;
        if (!this._selected.has(rid)) continue;
        const rt = routes[rid] || {};
        const dep = st.predictedDepartureTime || st.departureTime;
        const sched = st.departureTime;
        rows.push({
          tripId: st.tripId,
          label: rt.iconDisplayText || rt.shortName || '?',
          color: '#' + String(rt.color || '4477aa').replace('#', ''),
          text: '#' + String(rt.textColor || 'ffffff').replace('#', ''),
          head: st.stopHeadsign || this._dest.name,
          dep,
          sched,
          travel: null,
        });
        if (rows.length >= 12) break;
      }
      await Promise.all(rows.slice(0, 8).map(async (row) => {
        row.travel = await this._travelMin(row.tripId, row.dep);
      }));
      this._rows = rows;
      this._paintTable();
      if (this._poll) clearInterval(this._poll);
      this._poll = setInterval(() => this._loadDepartures(), 45000);
    } catch (err) {
      this._error(err.message || String(err));
    }
  }

  async _travelMin(tripId, dep) {
    if (!tripId || !this._dest) return null;
    try {
      const data = await this._bkk('trip-details.json', { tripId });
      const sts = (((data.data || {}).entry) || {}).stopTimes || [];
      const stops = (((data.data || {}).references) || {}).stops || {};
      const destN = this._dest.key;
      const dest = sts.find((s) => this._norm((stops[s.stopId] || {}).name) === destN);
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
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  _inMin(ts) {
    if (!ts) return '';
    const m = Math.round((ts * 1000 - Date.now()) / 60000);
    if (m < 0) return 'most';
    return `${m} perc`;
  }

  _paintTable() {
    const box = this._el('table');
    if (!this._rows.length) {
      box.innerHTML = 'Nincs k\u00f6zelg\u0151 indul\u00e1s a v\u00e1lasztott j\u00e1ratokkal.';
      return;
    }
    box.innerHTML = `
      <table>
        <thead><tr>
          <th></th><th></th>
          <th>\u00e9rkez\u00e9s</th>
          <th>v\u00e1rhat\u00f3</th>
          <th>menetid\u0151</th>
        </tr></thead>
        <tbody>
          ${this._rows.map((r) => `
            <tr>
              <td class="route" style="color:${this._esc(r.color)}">${this._esc(r.label)}</td>
              <td>${this._esc(this._dest.name)}</td>
              <td>${this._esc(this._inMin(r.dep))}</td>
              <td>${this._esc(this._hm(r.dep))}</td>
              <td class="muted">${r.travel ? `${r.travel} perc` : ''}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }
}


const BKK_HOP_TAG = 'bkk-stop-card-r3';
const BKK_HOP_EDITOR = 'bkk-stop-card-r3-editor';

const BkkLib = {
  esc(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => (
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
      const key = BkkLib.norm(s.name);
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
      const pick = area || withParent || group[0];
      hits.push({
        id: area ? area.id : (pick.parentStationId || pick.id),
        name: pick.name,
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
    if (!apiKey) throw new Error('Hi\u00e1nyzik az apiKey a k\u00e1rtya be\u00e1ll\u00edt\u00e1s\u00e1b\u00f3l.');
    const q = new URLSearchParams(Object.assign({
      key: apiKey, version: '4', appVersion: 'apiary-1.0',
    }, params));
    const res = await fetch(`${BKK_API}/${path}?${q}`);
    if (!res.ok) throw new Error(`BKK HTTP ${res.status}`);
    const data = await res.json();
    if (data.status && data.status !== 'OK') throw new Error(`BKK ${data.status}`);
    return data;
  },
  hm(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
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
  findApiKey(node, acc) {
    if (!acc) acc = [];
    if (!node || typeof node !== 'object') return acc;
    if (Array.isArray(node)) {
      node.forEach((x) => BkkLib.findApiKey(x, acc));
      return acc;
    }
    if (typeof node.apiKey === 'string' && node.apiKey) acc.push(node.apiKey);
    Object.keys(node).forEach((k) => {
      if (k === 'apiKey') return;
      BkkLib.findApiKey(node[k], acc);
    });
    return acc;
  },
  async stealApiKey(hass) {
    if (!hass || !hass.connection) return '';
    const paths = [null, 'dashboard-banana', 'bkk-blanka'];
    for (let i = 0; i < paths.length; i++) {
      try {
        const msg = { type: 'lovelace/config' };
        if (paths[i]) msg.url_path = paths[i];
        const cfg = await hass.connection.sendMessagePromise(msg);
        const keys = BkkLib.findApiKey(cfg);
        if (keys.length) return keys[0];
      } catch (_e) { /* try next dashboard */ }
    }
    return '';
  },
  async volanIndex() {
    if (BkkLib._volanIdx) return BkkLib._volanIdx;
    if (BkkLib._volanIdxP) return BkkLib._volanIdxP;
    BkkLib._volanIdxP = (async () => {
      const res = await fetch(volanIndexUrl(), { cache: 'no-store' });
      if (!res.ok) throw new Error('Vol\u00e1n menetrend HTTP ' + res.status);
      const buf = await res.arrayBuffer();
      const u8 = new Uint8Array(buf);
      let text = '';
      if (u8.length >= 2 && u8[0] === 0x1f && u8[1] === 0x8b) {
        if (typeof DecompressionStream !== 'function') {
          throw new Error('A b\u00f6ng\u00e9sz\u0151 nem tudja kicsomagolni a Vol\u00e1n menetrendet.');
        }
        const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
        text = await new Response(stream).text();
      } else {
        text = new TextDecoder().decode(u8);
      }
      const idx = JSON.parse(text);
      idx.stops = (idx.s || []).map((row, i) => ({
        i,
        id: row[0],
        name: row[1],
        num: String(row[2] || ''),
        key: BkkLib.norm(row[1]),
        fold: BkkLib.fold(row[1]),
      }));
      idx.byNum = new Map();
      idx.byKey = new Map();
      idx.stops.forEach((st) => {
        if (st.num && !idx.byNum.has(st.num)) idx.byNum.set(st.num, st);
        if (st.key && !idx.byKey.has(st.key)) idx.byKey.set(st.key, st);
      });
      BkkLib._volanIdx = idx;
      return idx;
    })();
    try {
      return await BkkLib._volanIdxP;
    } catch (err) {
      BkkLib._volanIdxP = null;
      throw err;
    }
  },
  volanDay(offset) {
    const d = new Date();
    d.setDate(d.getDate() + (offset || 0));
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const js = d.getDay();
    const now = new Date();
    return {
      ymd: `${y}${mo}${day}`,
      dow: js === 0 ? 6 : js - 1,
      mins: offset ? 0 : (now.getHours() * 60 + now.getMinutes()),
      midnight: Math.floor(new Date(y, d.getMonth(), d.getDate()).getTime() / 1000),
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
  volanMatchStop(idx, stop) {
    if (!idx || !stop) return null;
    const num = BkkLib.stopNum(stop.id);
    if (num && idx.byNum.has(num)) return idx.byNum.get(num);
    const key = BkkLib.norm(stop.name || '');
    if (key && idx.byKey.has(key)) return idx.byKey.get(key);
    const f = BkkLib.fold(stop.name || '');
    if (!f || f.length < 4) return null;
    const exact = idx.stops.find((s) => s.fold === f);
    if (exact) return exact;
    const hits = idx.stops.filter((s) => s.fold.indexOf(f) >= 0);
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
    const f = BkkLib.fold(q);
    if (!f) return [];
    const hits = idx.stops.filter((s) => s.fold.indexOf(f) >= 0);
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
      type: 'COACH',
    }));
  },
  async volanReachableDests(stop) {
    const idx = await BkkLib.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, stop);
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
        if (!byKey.has(st.key)) byKey.set(st.key, { key: st.key, name: st.name, id: st.id });
        if (!destRoutes[st.key]) destRoutes[st.key] = [];
        if (destRoutes[st.key].indexOf(route) < 0) destRoutes[st.key].push(route);
      }
    }
    const dests = Array.from(byKey.values());
    dests.sort((a, b) => a.name.localeCompare(b.name, 'hu'));
    return { dests, destRoutes };
  },
  async volanDepartures(stopId, dest, originName) {
    const idx = await BkkLib.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, { id: stopId, name: originName || '' });
    if (!origin) return [];
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold((dest && dest.name) || destKey || '');
    const rows = [];
    const trips = idx.t || [];
    for (let dayOff = 0; dayOff <= 1 && rows.length < 12; dayOff++) {
      const day = BkkLib.volanDay(dayOff);
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
          color: '#F9AB13',
          text: '#000000',
          vehicle: 'coach',
          rawType: 'COACH',
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
  async searchStops(apiKey, q, mode) {
    const byId = new Map();
    if (mode === 'volan') {
      try {
        (await BkkLib.volanSearchStops(q)).forEach((s) => byId.set(s.id, s));
      } catch (_e) { /* GTFS index optional */ }
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
    if (!cache[routeId]) {
      const data = await BkkLib.fetch(apiKey, 'route-details.json', {
        routeId,
        includeReferences: 'true',
      });
      const entry = (data.data || {}).entry || {};
      const stops = ((data.data || {}).references || {}).stops || {};
      const variants = (entry.variants || []).map((v) => ({
        name: v.name,
        ids: v.stopIds || [],
      }));
      cache[routeId] = { variants, stops };
    }
    const { variants, stops } = cache[routeId];
    const origin = stop.id;
    const originN = BkkLib.norm(stop.name);
    const names = [];
    const seen = new Set();
    variants.forEach((v) => {
      let idx = v.ids.findIndex((id) => BkkLib.sameStop(id, origin));
      if (idx < 0) {
        idx = v.ids.findIndex((id) => BkkLib.norm((stops[id] || {}).name) === originN);
      }
      if (idx < 0) return;
      v.ids.slice(idx + 1).forEach((id) => {
        const raw = (stops[id] || {}).name;
        const n = BkkLib.norm(raw);
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
    const on = BkkLib.norm(origin.name || '');
    let idx = (sts || []).findIndex((s) => BkkLib.sameStop(s.stopId, oid));
    if (idx < 0 && on) {
      idx = (sts || []).findIndex((s) => BkkLib.norm((stops[s.stopId] || {}).name) === on);
    }
    return idx;
  },
  async tripDetails(apiKey, cache, tripId) {
    const key = 'trip:' + tripId;
    if (cache && cache[key]) return cache[key];
    const data = await BkkLib.fetch(apiKey, 'trip-details.json', { tripId });
    const packed = {
      sts: (((data.data || {}).entry) || {}).stopTimes || [],
      stops: (((data.data || {}).references) || {}).stops || {},
    };
    if (cache) cache[key] = packed;
    return packed;
  },
  async tripGoesTo(apiKey, cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    try {
      const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
      const oidx = BkkLib.originIndex(sts, stops, origin);
      if (oidx < 0) return false;
      return sts.slice(oidx + 1).some((s) => BkkLib.norm((stops[s.stopId] || {}).name) === destKey);
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
      const n = BkkLib.norm(raw);
      if (!raw || !n || seen.has(n)) return;
      seen.add(n);
      names.push({ key: n, name: raw, id: s.stopId });
    });
    return names;
  },
  async reachableDests(apiKey, cache, stop, mode) {
    let out = { dests: [], destRoutes: {} };
    try {
      const data = await BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
        stopId: stop.id,
        minutesAfter: '150',
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
        if (st.tripId && !seenTrip.has(st.tripId) && tripJobs.length < 24) {
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
      const fromRoutes = await Promise.all(routeIds.slice(0, 16).map(async (rid) => {
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
          if (!byKey.has(n.key)) byKey.set(n.key, n);
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
      const dest = sts.slice(start).find((s) => BkkLib.norm((stops[s.stopId] || {}).name) === destKey);
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
    const cache = opts.cache || {};
    const origin = { id: stopId, name: opts.originName || '' };
    const selected = new Set(routeIds || []);
    const wide = mode === 'mav' || mode === 'volan' || !selected.size;
    const destKey = dest && dest.key;
    let candidates = [];
    try {
      const data = await BkkLib.fetch(apiKey, 'arrivals-and-departures-for-stop.json', {
        stopId,
        minutesAfter: wide ? '150' : '90',
        minutesBefore: '0',
        onlyDepartures: 'true',
        includeReferences: 'true',
        includeVehicleFromTrip: 'true',
      });
      const refs = (data.data || {}).references || {};
      const routes = refs.routes || {};
      const trips = refs.trips || {};
      const times = ((data.data || {}).entry || {}).stopTimes || [];
      for (let i = 0; i < times.length && candidates.length < 28; i++) {
        const st = times[i];
        const trip = trips[st.tripId] || {};
        const rid = trip.routeId;
        const rt = routes[rid] || {};
        if (!BkkLib.routeMatchesMode(rt, mode)) continue;
        if (selected.size && mode !== 'volan' && !selected.has(rid)) continue;
        const stops = refs.stops || {};
        const fallbackColor = mode === 'volan' ? 'F9AB13' : '4477aa';
        const fallbackText = mode === 'volan' ? '000000' : 'ffffff';
        candidates.push({
          tripId: st.tripId,
          label: rt.iconDisplayText || rt.shortName || '?',
          color: '#' + String(rt.color || fallbackColor).replace('#', ''),
          text: '#' + String(rt.textColor || fallbackText).replace('#', ''),
          vehicle: BkkLib.vehicleKind(rt),
          rawType: String(rt.type || 'BUS').toUpperCase(),
          dep: st.predictedDepartureTime || st.departureTime,
          sched: st.departureTime,
          delay: (st.predictedDepartureTime && st.departureTime)
            ? (st.predictedDepartureTime - st.departureTime) : 0,
          head: trip.tripHeadsign || st.stopHeadsign || '',
          platform: (stops[st.stopId] || {}).platformCode || '',
          wheelchair: trip.wheelchairAccessible === 1 || trip.wheelchairAccessible === true,
          bikesAllowed: trip.bikesAllowed === 1 || trip.bikesAllowed === true,
          trainNumber: trip.shortName || trip.tripShortName || '',
        });
      }
    } catch (err) {
      if (mode !== 'volan') throw err;
    }
    let picked = candidates;
    if (destKey && candidates.length) {
      const flags = await Promise.all(candidates.map((row) => (
        BkkLib.tripGoesTo(apiKey, cache, row.tripId, origin, destKey)
      )));
      picked = candidates.filter((_, i) => flags[i]).slice(0, 12);
    }
    let rows = picked.slice(0, 12).map((row) => Object.assign({ travel: null }, row));
    await Promise.all(rows.slice(0, 8).map(async (row) => {
      if (row.travel != null || String(row.tripId || '').indexOf('gtfs:') === 0) return;
      row.travel = await BkkLib.travelMin(apiKey, row.tripId, destKey, row.dep, origin, cache);
    }));
    if (mode === 'volan') {
      try {
        const extra = await BkkLib.volanDepartures(stopId, dest, opts.originName);
        rows = BkkLib.mergeVolanRows(extra, rows);
      } catch (err) {
        if (!rows.length) throw err;
      }
    }
    return rows;
  },

  asSensorVehicle(r, destName) {
    const dep = r.dep;
    const mins = dep ? Math.max(0, Math.round((dep * 1000 - Date.now()) / 60000)) : 0;
    let type = String(r.rawType || r.vehicle || 'BUS').toUpperCase();
    if (type.indexOf('SUBURBAN') >= 0 || type === 'TRAIN' || type === 'RAILWAY') type = 'RAIL';
    const plat = r.platform ? String(r.platform) : '';
    let head = String(r.head || destName || '').trim();
    if (plat && !/(?:\u00b7\s*)?v\u00e1g\.\S+/i.test(head)) {
      head = head ? `${head} \u00b7 v\u00e1g.${plat}` : `v\u00e1g.${plat}`;
    }
    return {
      in: String(mins),
      type: type,
      routeid: r.label,
      headsign: head,
      tripId: r.tripId || '',
      attime: BkkLib.hm(r.sched || dep),
      predicted_attime: BkkLib.hm(dep),
      wheelchair: r.wheelchair ? 'True' : 'False',
      bikesAllowed: !!r.bikesAllowed,
      bikesallowed: !!r.bikesAllowed,
      color: String(r.color || '').replace('#', ''),
      textcolor: String(r.text || '').replace('#', ''),
      platform: plat,
      trainNumber: r.trainNumber || '',
      delay: Number(r.delay || 0),
      booking: !!r.booking,
      travelMin: r.travel,
    };
  },
  mergeSensorExtras(hass, vehicles) {
    const byTrip = {};
    const states = (hass && hass.states) || {};
    Object.keys(states).forEach((id) => {
      const vehs = (states[id].attributes || {}).vehicles;
      if (!Array.isArray(vehs)) return;
      vehs.forEach((v) => {
        if (v && v.tripId) byTrip[v.tripId] = v;
      });
    });
    return (vehicles || []).map((v) => {
      const extra = byTrip[v.tripId];
      if (!extra) return v;
      return Object.assign({}, extra, v, {
        in: v.in,
        attime: v.attime || extra.attime,
        predicted_attime: v.predicted_attime || extra.predicted_attime,
        type: extra.type || v.type,
        routeid: extra.routeid || v.routeid,
        headsign: extra.headsign || v.headsign,
      });
    });
  },
};

class BKKHopCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._poll = null;
    this._hass = null;
    this._tripCache = {};
    this._inner = null;
    this._vehicles = [];
    this._entityId = 'sensor.bkk_hop_' + Math.random().toString(36).slice(2, 10);
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
    };
  }

  setConfig(config) {
    if (!this.shadowRoot) {
      try { this.attachShadow({ mode: 'open' }); } catch (_e) { /* already attached */ }
    }
    this._config = Object.assign({ routeIds: [] }, config || {});
    this._tripCache = {};
    if (!this._entityId) {
      this._entityId = 'sensor.bkk_hop_' + Math.random().toString(36).slice(2, 10);
    }
    try {
      this._render();
      this._reload();
    } catch (err) {
      this._showErr(err);
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._pushHass();
    if (!this._config.apiKey) this._fillKey();
  }

  getCardSize() { return 1; }

  disconnectedCallback() {
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
  }

  _showErr(err) {
    const msg = err && err.message ? err.message : String(err);
    this._vehicles = [{
      in: '',
      type: 'RAIL',
      routeid: '?',
      headsign: msg,
      attime: '',
      predicted_attime: '',
      wheelchair: 'False',
      bikesAllowed: false,
      color: '888888',
      textcolor: 'FFFFFF',
    }];
    this._pushHass();
    const root = this.shadowRoot;
    if (root && !this._inner) {
      root.innerHTML = '<ha-card><div style="padding:12px;font-size:12px">' + BkkLib.esc(msg) + '</div></ha-card>';
    }
  }

  async _fillKey() {
    const key = await BkkLib.stealApiKey(this._hass);
    if (!key || this._config.apiKey) return;
    this._config = Object.assign({}, this._config, { apiKey: key });
    this._reload();
  }

  _header() {
    const cfg = this._config || {};
    const ready = cfg.stopId && cfg.destKey;
    if (cfg.name) return cfg.name;
    if (ready) return `${cfg.stopName || ''} \u2192 ${cfg.destName || ''}`;
    return 'Hungarian transport';
  }

  _fakeHass() {
    const hass = this._hass || { states: {} };
    const cfg = this._config || {};
    const fake = {
      entity_id: this._entityId,
      state: this._vehicles.length ? String(this._vehicles[0].in || 'ok') : 'unknown',
      attributes: {
        vehicles: this._vehicles,
        stationName: cfg.stopName || this._header(),
        alerts: [],
      },
    };
    const callService = (domain, service, data) => {
      if (domain === 'bkk_stop' && service === 'refresh') {
        this._reload();
        return;
      }
      if (typeof hass.callService === 'function') return hass.callService(domain, service, data);
    };
    return Object.assign({}, hass, {
      states: Object.assign({}, hass.states || {}, { [this._entityId]: fake }),
      callService: callService,
    });
  }

  _pushHass() {
    if (!this._inner) return;
    try {
      this._inner.hass = this._fakeHass();
    } catch (_e) { /* r2 not ready */ }
  }

  _mountR2() {
    const root = this.shadowRoot;
    if (this._inner) return true;
    if (!root) return false;
    const tag = 'bkk-stop-card-r2';
    if (!customElements.get(tag)) return false;
    while (root.firstChild) root.removeChild(root.firstChild);
    this._inner = document.createElement(tag);
    root.appendChild(this._inner);
    this._inner.setConfig({
      entity: this._entityId,
      layout_bpgo: true,
      name: this._header(),
    });
    this._pushHass();
    return true;
  }

  _render() {
    if (!this._entityId) {
      this._entityId = 'sensor.bkk_hop_' + Math.random().toString(36).slice(2, 10);
    }
    if (!this._vehicles) this._vehicles = [];
    if (!this._tripCache) this._tripCache = {};
    if (this._mountR2()) return;
    const root = this.shadowRoot;
    if (!root) return;
    if (!root.querySelector('.wait')) {
      while (root.firstChild) root.removeChild(root.firstChild);
      const card = document.createElement('ha-card');
      card.className = 'wait';
      card.innerHTML = '<div style="padding:12px;font-size:12px">Hungarian transport</div>';
      root.appendChild(card);
    }
    customElements.whenDefined('bkk-stop-card-r2').then(() => {
      if (!this.isConnected) return;
      this._inner = null;
      this._mountR2();
      this._pushHass();
    });
  }

  async _reload() {
    const cfg = this._config || {};
    if (this._poll) { clearInterval(this._poll); this._poll = null; }
    this._mountR2();
    const ready = cfg.stopId && cfg.destKey && (cfg.apiKey || BkkLib.mode(cfg) === 'volan');
    if (!ready) {
      this._vehicles = [];
      this._pushHass();
      return;
    }
    const run = async () => {
      try {
        const rows = await BkkLib.departures(
          cfg.apiKey, cfg.stopId, cfg.routeIds, { key: cfg.destKey, name: cfg.destName },
          {
            originName: cfg.stopName || '',
            cache: this._tripCache || {},
            mode: BkkLib.mode(cfg),
          },
        );
        let vehicles = (rows || []).map((r) => BkkLib.asSensorVehicle(r, cfg.destName));
        vehicles = BkkLib.mergeSensorExtras(this._hass, vehicles);
        this._vehicles = vehicles;
        if (!this._inner) this._mountR2();
        this._pushHass();
      } catch (err) {
        this._showErr(err);
      }
    };
    await run();
    this._poll = setInterval(run, 45000);
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
  }

  setConfig(config) {
    this._config = Object.assign({ routeIds: [] }, config || {});
    if (!this._cache) this._cache = {};
    if (!this._dests) this._dests = [];
    if (!this._destRoutes) this._destRoutes = {};
    if (!this._built) this._build();
    this._syncName();
    this._syncStopLabel();
    this._syncDestLabel();
    this._syncApiKey();
    this._syncMode();
    if (this._config.stopId && this._config.apiKey && this._loadedStop !== this._config.stopId) {
      this._loadDests();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config.apiKey && !this._loadingKey) this._fillKey();
  }

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
        .card-config .chip {
          border: 1px solid var(--divider-color); background: transparent;
          color: var(--primary-text-color); border-radius: 999px;
          padding: 4px 10px; cursor: pointer; font: inherit; font-size: 13px;
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
          <label>BKK API kulcs</label>
          <input id="apiKey" type="text" autocomplete="off" placeholder="UUID a BKK Open Dat\u00e1b\u00f3l">
          <div class="hint">Ingyenes kulcs, egyszer\u0171 regisztr\u00e1ci\u00f3 ut\u00e1n:
            <a href="https://opendata.bkk.hu/data-sources" target="_blank" rel="noopener">opendata.bkk.hu/data-sources</a>
          </div>
        </div>
        <div class="f">
          <label>N\u00e9v (opcion\u00e1lis)</label>
          <input id="name" type="text" placeholder="Hungarian transport">
        </div>
        <div class="f">
          <div class="row">
            <label class="switch"><input id="mav" type="checkbox"> M\u00c1V</label>
            <label class="switch"><input id="volan" type="checkbox"> Vol\u00e1n</label>
          </div>
          <div class="hint" id="modeHint">Add meg a forr\u00e1s \u00e9s a c\u00e9l meg\u00e1ll\u00f3t. Az \u00f6sszes j\u00e1rat megjelenik k\u00f6z\u00f6tt\u00fck.</div>
        </div>
        <div class="f">
          <label id="lblStop">Forr\u00e1s meg\u00e1ll\u00f3</label>
          <input id="q" type="search" placeholder="Keres\u00e9s, pl. Bosny\u00e1k t\u00e9r, \u00c1rp\u00e1d h\u00edd">
          <div class="chips" id="fav"></div>
          <div class="picked" id="stopPicked"></div>
          <div class="list" id="hits"></div>
        </div>
        <div class="f">
          <label id="lblDest">C\u00e9l meg\u00e1ll\u00f3</label>
          <input id="dq" type="search" placeholder="Keres\u00e9s, pl. Keleti p\u00e1lyaudvar">
          <div class="picked" id="destPicked"></div>
          <div class="list" id="destHits"></div>
        </div>
        <div class="err" id="err"></div>
      </div>
    `;
    this._el('name').addEventListener('input', (ev) => {
      this._emit({ name: ev.target.value });
    });
    this._el('apiKey').addEventListener('change', (ev) => {
      this._emit({ apiKey: ev.target.value.trim() });
    });
    this._el('mav').addEventListener('change', () => this._onModeToggle('mav'));
    this._el('volan').addEventListener('change', () => this._onModeToggle('volan'));
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
    this._paintFav();
  }

  _el(id) { return this.querySelector('#' + id); }

  _syncName() {
    const el = this._el('name');
    if (!el || document.activeElement === el) return;
    el.value = this._config.name || '';
  }

  _syncStopLabel() {
    const el = this._el('stopPicked');
    if (el) el.textContent = this._config.stopName || '';
    const q = this._el('q');
    if (q && document.activeElement !== q && this._config.stopName) {
      q.placeholder = this._config.stopName;
    }
  }

  _syncDestLabel() {
    const el = this._el('destPicked');
    if (el) el.textContent = this._config.destName || '';
    const q = this._el('dq');
    if (q && document.activeElement !== q && this._config.destName) {
      q.placeholder = this._config.destName;
    }
  }

  _syncApiKey() {
    const el = this._el('apiKey');
    if (!el || document.activeElement === el) return;
    el.value = this._config.apiKey || '';
  }

  _modeCopy(mode) {
    if (mode === 'mav') {
      return {
        stop: 'Forr\u00e1s \u00e1llom\u00e1s',
        dest: 'C\u00e9l \u00e1llom\u00e1s',
        hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l \u00e1llom\u00e1st. Az \u00f6sszes vonat megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s, pl. Kelenf\u00f6ld, Sz\u00e9kesfeh\u00e9rv\u00e1r',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 \u00e1llom\u00e1sok k\u00f6z\u00f6tt',
        empty: 'Nincs M\u00c1V \u00e1llom\u00e1s erre a keres\u00e9sre.',
        destEmpty: 'Err\u0151l az \u00e1llom\u00e1sr\u00f3l nincs vonat erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s \u00e1llom\u00e1st.',
        destNone: 'Nincs el\u00e9rhet\u0151 M\u00c1V c\u00e9l\u00e1llom\u00e1s ebb\u0151l a forr\u00e1sb\u00f3l.',
      };
    }
    if (mode === 'volan') {
      return {
        stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
        dest: 'C\u00e9l meg\u00e1ll\u00f3',
        hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l Vol\u00e1n meg\u00e1ll\u00f3t. Az \u00f6sszes busz megjelenik k\u00f6z\u00f6tt\u00fck.',
        originPh: 'Keres\u00e9s, pl. N\u00e9pliget, Sz\u00e9kesfeh\u00e9rv\u00e1r aut\u00f3busz-\u00e1llom\u00e1s',
        destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 Vol\u00e1n meg\u00e1ll\u00f3k k\u00f6z\u00f6tt',
        empty: 'Nincs Vol\u00e1n meg\u00e1ll\u00f3 erre a keres\u00e9sre.',
        destEmpty: 'Err\u0151l a meg\u00e1ll\u00f3r\u00f3l nincs k\u00f6zvetlen Vol\u00e1n erre a c\u00e9lra.',
        destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
        destNone: 'Nincs el\u00e9rhet\u0151 Vol\u00e1n c\u00e9l ebb\u0151l a forr\u00e1sb\u00f3l.',
      };
    }
    return {
      stop: 'Forr\u00e1s meg\u00e1ll\u00f3',
      dest: 'C\u00e9l meg\u00e1ll\u00f3',
      hint: 'Add meg a forr\u00e1s \u00e9s a c\u00e9l meg\u00e1ll\u00f3t. Az \u00f6sszes BKK j\u00e1rat megjelenik k\u00f6z\u00f6tt\u00fck.',
      originPh: 'Keres\u00e9s, pl. Bosny\u00e1k t\u00e9r, \u00c1rp\u00e1d h\u00edd',
      destPh: 'Keres\u00e9s a forr\u00e1sb\u00f3l el\u00e9rhet\u0151 meg\u00e1ll\u00f3k k\u00f6z\u00f6tt',
      empty: 'Nincs BKK meg\u00e1ll\u00f3 erre a keres\u00e9sre.',
      destEmpty: 'Err\u0151l a meg\u00e1ll\u00f3r\u00f3l nincs j\u00e1rat erre a c\u00e9lra.',
      destNeedOrigin: 'El\u0151bb v\u00e1lassz forr\u00e1s meg\u00e1ll\u00f3t.',
      destNone: 'Nincs el\u00e9rhet\u0151 BKK c\u00e9l ebb\u0151l a forr\u00e1sb\u00f3l.',
    };
  }

  _syncMode() {
    const mode = this._mode();
    const mav = this._el('mav');
    const volan = this._el('volan');
    if (mav && document.activeElement !== mav) mav.checked = mode === 'mav';
    if (volan && document.activeElement !== volan) volan.checked = mode === 'volan';
    const copy = this._modeCopy(mode);
    const hint = this._el('modeHint');
    if (hint) hint.textContent = copy.hint;
    const lblStop = this._el('lblStop');
    if (lblStop) lblStop.textContent = copy.stop;
    const lblDest = this._el('lblDest');
    if (lblDest) lblDest.textContent = copy.dest;
    const q = this._el('q');
    if (q && document.activeElement !== q && !this._config.stopName) q.placeholder = copy.originPh;
    const dq = this._el('dq');
    if (dq && document.activeElement !== dq && !this._config.destName) dq.placeholder = copy.destPh;
    this._paintFav();
  }

  _paintFav() {
    const fav = this._el('fav');
    if (!fav) return;
    fav.innerHTML = '';
    const mode = this._mode();
    const list = mode === 'mav' ? MAV_FAVORITES : (mode === 'volan' ? VOLAN_FAVORITES : BKK_FAVORITES);
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
    let mav = mavOn;
    let volan = volanOn;
    if (which === 'mav' && mav) volan = false;
    if (which === 'volan' && volan) mav = false;
    if (this._el('mav')) this._el('mav').checked = mav;
    if (this._el('volan')) this._el('volan').checked = volan;
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
      const key = await BkkLib.stealApiKey(this._hass);
      if (key && !this._config.apiKey) this._emit({ apiKey: key });
    } finally {
      this._loadingKey = false;
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
    try {
      this._error('');
      if (!this._config.apiKey) await this._fillKey();
      const mode = this._mode();
      const hits = await BkkLib.searchStops(this._config.apiKey, q, mode);
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
      this._error(err.message || String(err));
    }
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
      box.innerHTML = '<span class="hint">El\u00e9rhet\u0151 c\u00e9lok bet\u00f6lt\u00e9se...</span>';
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
      let msg = copy.destEmpty;
      if (this._mode() === 'volan' && f.includes('kelenfold')) {
        const bp = this._dests.filter((d) => /budapest/i.test(d.name)).slice(0, 6);
        if (bp.length) {
          msg = 'Nincs k\u00f6zvetlen Vol\u00e1n Kelenf\u00f6ldre. Budapesti c\u00e9lok: ' +
            bp.map((d) => d.name.replace(/^Budapest,\s*/i, '')).join(', ') + '.';
        }
      }
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
    if (!this._config.apiKey && this._mode() !== 'volan') return;
    const stopId = this._config.stopId;
    this._destsLoading = true;
    this._loadedStop = stopId;
    const box = this._el('destHits');
    if (box) box.innerHTML = '<span class="hint">El\u00e9rhet\u0151 c\u00e9lok bet\u00f6lt\u00e9se...</span>';
    try {
      this._error('');
      const idx = await BkkLib.reachableDests(
        this._config.apiKey,
        this._cache,
        { id: this._config.stopId, name: this._config.stopName },
        this._mode(),
      );
      if (this._config.stopId !== stopId) return;
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
      this._error(err.message || String(err));
      if (box) box.innerHTML = '';
    } finally {
      this._destsLoading = false;
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
    this._loadDests();
  }
}


(function attachHopImpl() {
  const Ctor = customElements.get(BKK_HOP_TAG);
  if (!Ctor) { console.error('[bkk-stop-card-r3] missing ctor'); return; }
  const srcProto = BKKHopCard.prototype;
  Object.getOwnPropertyNames(srcProto).forEach((name) => {
    if (name === 'constructor') return;
    const desc = Object.getOwnPropertyDescriptor(srcProto, name);
    if (!desc) return;
    if (name === 'setConfig') {
      Object.defineProperty(Ctor.prototype, '_renderHop', Object.getOwnPropertyDescriptor(srcProto, '_render') || {value: srcProto._render});
      Object.defineProperty(Ctor.prototype, '_reloadHop', Object.getOwnPropertyDescriptor(srcProto, '_reload') || {value: srcProto._reload});
      Object.defineProperty(Ctor.prototype, 'setConfig', {
        configurable: true,
        writable: true,
        value: function setConfig(config) {
          this._config = Object.assign({ routeIds: [] }, config || {});
          this._tripCache = {};
          if (!this.shadowRoot) {
            try { this.attachShadow({ mode: 'open' }); } catch (_e) {}
          }
          try {
            if (typeof this._render === 'function') { this._render(); this._reload(); }
            else if (typeof this._renderHop === 'function') { this._renderHop(); this._reloadHop(); }
          } catch (err) {
            if (this.shadowRoot) {
              this.shadowRoot.innerHTML = '<ha-card><div style="padding:16px">' + String(err) + '</div></ha-card>';
            }
          }
        },
      });
      return;
    }
    Object.defineProperty(Ctor.prototype, name, desc);
  });
  Ctor.getStubConfig = BKKHopCard.getStubConfig;
  Ctor.getConfigElement = async function getConfigElement() {
    if (!customElements.get(BKK_HOP_EDITOR)) {
      await customElements.whenDefined(BKK_HOP_EDITOR);
    }
    return document.createElement(BKK_HOP_EDITOR);
  };
  document.querySelectorAll(BKK_HOP_TAG).forEach((el) => {
    if (el._config && typeof el.setConfig === 'function') {
      try { el.setConfig(el._config); } catch (_e) {}
    }
  });

  const Ed = customElements.get(BKK_HOP_EDITOR);
  if (Ed && typeof BKKHopCardEditor !== 'undefined') {
    const src = BKKHopCardEditor.prototype;
    Object.getOwnPropertyNames(src).forEach((name) => {
      if (name === 'constructor') return;
      const desc = Object.getOwnPropertyDescriptor(src, name);
      if (desc) Object.defineProperty(Ed.prototype, name, desc);
    });
    document.querySelectorAll(BKK_HOP_EDITOR).forEach((el) => {
      if (el._config && typeof el.setConfig === 'function') {
        try { el.setConfig(el._config); } catch (_e) {}
      }
    });
  }
  console.info('[hungarian-transport] impl attached');
})();

if (!customElements.get(BKK_PLANNER_TAG)) {
  customElements.define(BKK_PLANNER_TAG, BKKPlannerCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === BKK_HOP_TAG)) {
  window.customCards.push({
    type: BKK_HOP_TAG,
    name: 'Hungarian transport',
    description: 'BKK, M\u00c1V and Vol\u00e1n departures between two stops',
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

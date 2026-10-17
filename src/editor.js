import { DEFAULT_FAVORITES, MINUTES_AFTER_PRESETS, clampMinutesAfter, setCityIndexUrl, setMavIndexUrl, setVolanIndexUrl } from './config.js';
import { codedError, errText, modeCopy, resolveLang, t } from './i18n.js';
import { BkkLib } from './lib/core.js';

export class BKKHopCardEditor extends HTMLElement {
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
    if (this._config.mavIndex) setMavIndexUrl(this._config.mavIndex);
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
    if (this._config.stopId && (this._hasFutar() || this._mode() === 'volan' || this._mode() === 'helyi') && this._loadedStop !== this._config.stopId) {
      this._loadDests();
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    BkkLib._hass = hass;
    // "auto" resolves against hass.language, which is only known once hass lands.
    if (first && this._built && this._lang() !== resolveLang(this._config.language, null)) {
      this._syncTexts();
      this._syncMode();
    }
    /* With the key in the integration, do not copy a sibling card's key into
       this card's config: keeping it out of dashboards is the point. */
    const viaIntegration = BkkLib.integrationHasKey(hass);
    if (!this._userEditedKey && !this._config.apiKey && !this._loadingKey && !viaIntegration) this._fillKey();
    else if (this._hasFutar()) this._probeKey();
  }

  _apiKey() {
    return BkkLib.cardApiKey(this._config, this._hass);
  }

  _hasFutar() {
    return BkkLib.hasFutar(this._apiKey(), this._hass);
  }

  /* What a probe result belongs to: this card's key, or the integration. */
  _keyAccess() {
    const key = this._apiKey();
    if (key) return `key:${key}`;
    return BkkLib.integrationHasKey(this._hass) ? 'integration' : '';
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
    const own = (this._config.apiKey || '').trim();
    const viaIntegration = !own && BkkLib.integrationHasKey(this._hass);
    /* A rejected integration key shows the field again, so a card key can
       be entered while the integration's key is fixed. */
    const settled = !this._keyReveal
      && ((viaIntegration && this._keyOk !== false) || (!!own && this._keyOk === true));
    wrap.style.display = settled ? 'none' : '';
    if (okWrap) okWrap.style.display = settled ? '' : 'none';
    const okText = this._el('apiKeyOkText');
    if (okText) okText.textContent = t(this._lang(), viaIntegration ? 'apiKeyIntegration' : 'apiKeyOk');
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
    const shown = (!custom && mode === 'all') ? BkkLib.collapseSameNameStops(list) : list;
    shown.forEach((f) => {
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
    const access = this._keyAccess();
    if (!access) {
      this._keyOk = false;
      this._syncApiKey();
      return;
    }
    if (this._probingKey) return;
    if (this._probedKey === access && this._keyOk != null) {
      this._syncApiKey();
      return;
    }
    this._probingKey = true;
    this._probedKey = access;
    try {
      const result = await BkkLib.probeApiKey(this._apiKey());
      if (this._keyAccess() !== access) return;
      this._keyOk = !!result.ok;
      this._syncApiKey();
      if (!result.ok && result.code) this._error(errText(this._lang(), codedError(result.code)));
      else if (result.ok) this._error('');
    } finally {
      this._probingKey = false;
      const latest = this._keyAccess();
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
      if (!this._hasFutar() && !this._userEditedKey) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const mode = this._mode();
      const hits = await BkkLib.searchStops(this._apiKey(), q, mode, this._config.city);
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
    if (!this._hasFutar() && this._mode() !== 'volan' && this._mode() !== 'helyi') return;
    const stopId = this._config.stopId;
    const seq = ++this._destSeq;
    this._destsLoading = true;
    const box = this._el('destHits');
    if (box) box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), 'destLoading')) + '</span>';
    try {
      this._error('');
      const idx = await BkkLib.reachableDests(
        this._apiKey(),
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

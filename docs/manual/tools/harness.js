import { MDI } from './mdi.js';

/* Minimal stand-ins for the two Home Assistant elements the cards use. */
class HaCard extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>:host{display:block;position:relative;box-sizing:border-box;
      background:var(--ha-card-background,var(--card-background-color));color:var(--primary-text-color);
      border-radius:var(--ha-card-border-radius,12px);border:1px solid var(--ha-card-border-color,#e0e0e0);
      font-size:14px;line-height:1.4;}</style><slot></slot>`;
  }
}
class HaIcon extends HTMLElement {
  static get observedAttributes() { return ['icon']; }
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._draw();
  }
  attributeChangedCallback() { this._draw(); }
  set icon(v) { this.setAttribute('icon', v); }
  get icon() { return this.getAttribute('icon'); }
  _draw() {
    const d = MDI[this.getAttribute('icon') || ''] || '';
    this.shadowRoot.innerHTML = `<style>:host{display:inline-flex;vertical-align:middle;
      width:var(--mdc-icon-size,24px);height:var(--mdc-icon-size,24px);}
      svg{width:100%;height:100%;fill:currentColor;display:block}</style>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"></path></svg>`;
  }
}
customElements.define('ha-card', HaCard);
customElements.define('ha-icon', HaIcon);

async function callSvc(msg) {
  const r = await fetch('/svc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domain: msg.domain, service: msg.service, service_data: msg.service_data || {} }),
  });
  const body = await r.json();
  if (!r.ok) {
    const err = new Error(body.error || `HTTP ${r.status}`);
    err.code = 'service_error';
    throw err;
  }
  return { context: { id: 'harness' }, response: body.response };
}

const userData = {};
window.__userData = userData;

export function makeHass(lang, opts = {}) {
  const connection = {
    sendMessagePromise: async (msg) => {
      if (msg.type === 'call_service') return callSvc(msg);
      if (msg.type === 'lovelace/config') return { views: [] };
      throw new Error(`unsupported ${msg.type}`);
    },
    subscribeEvents: async () => () => {},
    subscribeMessage: async () => () => {},
  };
  return {
    language: lang,
    locale: { language: lang },
    selectedLanguage: lang,
    states: {},
    services: {
      hungarian_transport: opts.noFutar
        ? { elvira_between: {}, rebuild_mav_index: {} }
        : { elvira_between: {}, futar: {}, rebuild_mav_index: {} },
    },
    user: { id: 'manual-demo-user', name: 'Demo', is_admin: true },
    panelUrl: 'lovelace',
    themes: { darkMode: document.documentElement.dataset.theme === 'dark' },
    connection,
    callWS: async (msg) => {
      if (msg.type === 'frontend/get_user_data') return { value: userData[msg.key] ?? null };
      if (msg.type === 'frontend/set_user_data') { userData[msg.key] = msg.value; return null; }
      throw new Error(`unsupported ${msg.type}`);
    },
    callService: async (domain, service, data) => callSvc({ domain, service, service_data: data }),
  };
}

const stage = () => document.getElementById('stage');

/* A card on the page background, the way a dashboard column shows it. */
window.__mountCard = (opts) => {
  const { tag, config, lang, width, id, noFutar } = opts;
  const shot = document.createElement('div');
  shot.className = 'shot';
  shot.id = id || 'shot';
  shot.style.width = `${width || 480}px`;
  const card = document.createElement(tag);
  card.setConfig(config);
  card.hass = makeHass(lang, { noFutar });
  shot.appendChild(card);
  stage().appendChild(shot);
  window.__card = card;
  return true;
};

/* The editor inside a card-configuration dialog with the live preview next
   to it, wired like Home Assistant: config-changed goes back to both. */
window.__mountEditor = (opts) => {
  const { tag, editorTag, config, lang, width, title, id, preview, noFutar } = opts;
  const dlg = document.createElement('div');
  dlg.className = 'dialog shot';
  dlg.id = id || 'shot';
  dlg.style.width = `${width || 1000}px`;
  dlg.innerHTML = `<h2></h2><div class="cols${preview === false ? ' single' : ''}"><div class="ed"></div>${
    preview === false ? '' : '<div class="preview"></div>'}</div>`;
  dlg.querySelector('h2').textContent = title || '';
  const hass = makeHass(lang, { noFutar });
  const editor = document.createElement(editorTag);
  editor.hass = hass;
  editor.setConfig(config);
  dlg.querySelector('.ed').appendChild(editor);
  let card = null;
  if (preview !== false) {
    card = document.createElement(tag);
    card.setConfig(config);
    card.hass = hass;
    dlg.querySelector('.preview').appendChild(card);
  }
  editor.addEventListener('config-changed', (ev) => {
    const cfg = ev.detail && ev.detail.config;
    if (!cfg) return;
    window.__lastConfig = cfg;
    editor.setConfig(cfg);
    if (card) card.setConfig(cfg);
  });
  stage().appendChild(dlg);
  window.__editor = editor;
  window.__card = card;
  return true;
};

window.__clear = () => { stage().innerHTML = ''; };
window.__setTheme = (theme) => { document.documentElement.dataset.theme = theme; };

const s = document.createElement('script');
s.type = 'module';
s.src = '/ht/hungarian-transport.js';
s.onload = () => { window.__ready = true; };
document.head.appendChild(s);

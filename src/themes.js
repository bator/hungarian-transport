/* Operator looks for the cards, taken from each operator's own passenger
   site (header, buttons, footer) on 2026-10-31. A theme tints the card's
   title band and colours the card's accents (buttons, focus rings, the open
   itinerary, the badge fallback); the card background, text and status
   colours stay Home Assistant's. --primary-color is left alone: an accent
   fill gets --ht-accent with a label in black or white, whichever reads
   better, and accent text and rings a mix of the accent with the HA text
   colour that reads at 4.5:1 on Home Assistant's light and dark cards.

   brand: tint and rule of the title band; stripe: rule colour when it
   differs from brand; accent: buttons, rings and accent text. */
export const THEMES = {
  bkk: { name: 'BKK', brand: '#4C0E5F', accent: '#005FA8' },
  mav: { name: 'MÁV', brand: '#0057BE', stripe: '#FFD400', accent: '#0057BE' },
  /* volanbusz.hu now redirects to mavcsoport.hu: the coach yellow is the
     Volán route colour of the GTFS boards (gtfs.js), the accent MÁV's blue. */
  volan: { name: 'Volán', brand: '#F9AB13', accent: '#0057BE' },
  mvk: { name: 'MVK Miskolc', brand: '#22365A', accent: '#0071B3' },
  szkt: { name: 'SZKT Szeged', brand: '#08203C', stripe: '#2199E8', accent: '#1F5B93' },
  tuke: { name: 'Tüke Busz Pécs', brand: '#2A785B', stripe: '#7EB526', accent: '#2A785B' },
  vbusz: { name: 'V-Busz Veszprém', brand: '#781D7D', accent: '#9D3293' },
  blaguss: { name: 'Blaguss Szombathely', brand: '#B31415', accent: '#0A7DC1' },
  bahart: { name: 'Bahart', brand: '#003399', accent: '#003399' },
  kisvasut: { name: 'Kecskeméti Kisvasút', brand: '#002060', stripe: '#D5AA00', accent: '#9F0000' },
  thury: { name: 'Thury-Busz Várpalota', brand: '#971A1A', accent: '#971A1A' },
  homm: { name: 'HOMM Budaörs', brand: '#03935A', accent: '#03935A' },
  paksbusz: { name: 'Paksbusz', brand: '#647A27', stripe: '#9BC31C', accent: '#647A27' },
};

/* `ha` keeps the Home Assistant theme, `operator` picks the theme of the
   card's own data source. Both come first in the editor. */
export const THEME_DEFAULT = 'ha';
export const THEME_OPERATOR = 'operator';
export const THEME_IDS = [THEME_DEFAULT, THEME_OPERATOR].concat(Object.keys(THEMES));

/* City feed key (city index `o`) → theme. Volánbusz's town networks are
   `volan-<town>`; feeds with no reachable site of their own get none. */
const CITY_THEMES = {
  miskolc: 'mvk', szeged: 'szkt', pecs: 'tuke', veszprem: 'vbusz',
  szombathely: 'blaguss', bahart: 'bahart', 'kecskemet-kisvasut': 'kisvasut',
  varpalota: 'thury', budaors: 'homm', paks: 'paksbusz',
};

export function operatorTheme(mode, city) {
  if (mode === 'bkk') return 'bkk';
  if (mode === 'mav') return 'mav';
  if (mode === 'volan') return 'volan';
  if (mode !== 'helyi') return '';
  const key = String(city || '');
  if (key.startsWith('volan-')) return 'volan';
  return Object.prototype.hasOwnProperty.call(CITY_THEMES, key) ? CITY_THEMES[key] : '';
}

/* YAML may spell an id in any case (`BKK`); only the table's own keys
   count, not Object.prototype's (`constructor`, `__proto__`). */
export function themeKey(id) {
  return String(id == null ? '' : id).trim().toLowerCase();
}

function knownTheme(key) {
  return Object.prototype.hasOwnProperty.call(THEMES, key);
}

/* WCAG 2 relative luminance and contrast of #RRGGBB / [r, g, b] colours. */
const rgbOf = (c) => (Array.isArray(c) ? c : [1, 3, 5].map((i) => parseInt(String(c).slice(i, i + 2), 16)));
function luminance(c) {
  const [r, g, b] = rgbOf(c).map((v) => v / 255)
    .map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
/* color-mix(in srgb, a pct%, b), rounded to 8 bits. */
function mixRgb(a, b, pct) {
  const x = rgbOf(a);
  const y = rgbOf(b);
  return x.map((v, i) => Math.round((v * pct + y[i] * (100 - pct)) / 100));
}

/* The title band's share of the brand colour, and the guard for rules that
   need color-mix() (older iOS / Companion WebViews lack it). */
export const THEME_TINT_PCT = 15;
export const MIX_SUPPORTS = '@supports (color: color-mix(in srgb, red 50%, blue))';

/* Home Assistant's default card background and text colours. */
const HA_LIGHT = { bg: '#ffffff', text: '#212121' };
const HA_DARK = { bg: '#1c1c1c', text: '#e1e1e1' };
const TEXT_TARGET = 4.6; // 4.5:1 plus a little for other renderers' rounding
const RING_TARGET = 3;

/* Black or white, whichever reads better on the accent fill. */
function accentInk(accent) {
  return contrastRatio(accent, '#ffffff') >= contrastRatio(accent, '#000000') ? '#FFFFFF' : '#000000';
}

/* The largest share of the accent (in 5 % steps) that, mixed with the HA
   text colour, still reads as text on the card and as a ring on the title
   band in both default modes: a light mode keeps a darkened accent, a dark
   mode gets a lightened one. */
function accentTextPct(theme) {
  for (let pct = 100; pct > 0; pct -= 5) {
    const ok = [HA_LIGHT, HA_DARK].every((m) => {
      const c = mixRgb(theme.accent, m.text, pct);
      const band = mixRgb(theme.brand, m.bg, THEME_TINT_PCT);
      return contrastRatio(c, m.bg) >= TEXT_TARGET && contrastRatio(c, band) >= RING_TARGET;
    });
    if (ok) return pct;
  }
  return 0;
}

/* The theme to paint, or null for Home Assistant's own look. An unknown id
   (a typo in YAML, a theme dropped later) falls back to Home Assistant's. */
export function resolveTheme(id, mode, city) {
  const want = themeKey(id);
  const key = want === THEME_OPERATOR ? operatorTheme(mode, city) : want;
  if (!knownTheme(key)) return null;
  const theme = Object.assign({ id: key }, THEMES[key]);
  theme.ink = accentInk(theme.accent);
  theme.textPct = accentTextPct(theme);
  return theme;
}

/* Custom properties the theme sets on the card's host. */
export const THEME_VAR_KEYS = ['--ht-accent', '--ht-accent-ink', '--ht-accent-text', '--ht-brand', '--ht-brand-stripe'];

/* Custom properties set on the card's host, inherited by the shadow tree.
   --ht-accent-text resolves var(--primary-text-color) on the host, so it
   follows a light or dark HA theme. */
export function themeVars(theme) {
  if (!theme) return '';
  return `--ht-accent:${theme.accent};--ht-accent-ink:${theme.ink};`
    + `--ht-accent-text:color-mix(in srgb, ${theme.accent} ${theme.textPct}%, var(--primary-text-color, ${HA_LIGHT.text}));`
    + `--ht-brand:${theme.brand};--ht-brand-stripe:${theme.stripe || theme.brand};`;
}

/* The title sits on a tint of the brand colour over the card's own
   background, so the band moves with the HA theme (a light tint on light
   cards, a dark one on dark cards) and the title keeps the theme's text
   colour. The brand colour itself is the rule under it. --ht-band-* is the
   card's .wrap padding, so the band reaches the corners.

   The card's accents go through --ht-fill / --ht-fill-ink (a filled button
   and its label) and --ht-ac (accent text, focus rings, outlines): Home
   Assistant's --primary-color without a theme. A WebView without
   color-mix() gets plain colours first: a neutral band and accent text in
   the HA text colour. */
export const THEME_CSS = `
  ha-card {
    --ht-fill: var(--primary-color);
    --ht-fill-ink: var(--text-primary-color, #fff);
    --ht-ac: var(--primary-color);
  }
  ha-card.themed {
    --ht-fill: var(--ht-accent);
    --ht-fill-ink: var(--ht-accent-ink);
    --ht-ac: var(--primary-text-color);
  }
  ha-card.themed .head {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    color: var(--primary-text-color);
    margin: calc(-1 * var(--ht-band-top, 10px)) calc(-1 * var(--ht-band-side, 12px)) 9px;
    padding: 9px var(--ht-band-side, 12px) 8px;
    border-bottom: 2px solid var(--ht-brand-stripe);
    border-radius: var(--ha-card-border-radius, 12px) var(--ha-card-border-radius, 12px) 0 0;
  }
  ${MIX_SUPPORTS} {
    ha-card.themed { --ht-ac: var(--ht-accent-text); }
    ha-card.themed .head {
      background: color-mix(in srgb, var(--ht-brand) ${THEME_TINT_PCT}%, var(--card-background-color, #fff));
    }
  }
`;

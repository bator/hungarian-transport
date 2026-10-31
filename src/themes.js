/* Operator looks for the cards, taken from each operator's own passenger
   site (header, buttons, footer) on 2026-10-31. A theme paints the card's
   title band and sets --primary-color inside the card (row hover, badge
   fallback); the card background, text and status colours stay Home
   Assistant's, so a theme works in light and dark mode alike.

   brand: title band; ink: text on it (WCAG 4.5:1 or better on brand);
   stripe: optional rule under the band; accent: --primary-color. */
export const THEMES = {
  bkk: { name: 'BKK', brand: '#4C0E5F', ink: '#FFFFFF', accent: '#005FA8' },
  mav: { name: 'MÁV', brand: '#0057BE', ink: '#FFFFFF', stripe: '#FFD400', accent: '#0057BE' },
  /* volanbusz.hu now redirects to mavcsoport.hu: the coach yellow is the
     Volán route colour of the GTFS boards (gtfs.js), the accent MÁV's blue. */
  volan: { name: 'Volán', brand: '#F9AB13', ink: '#000000', accent: '#0057BE' },
  mvk: { name: 'MVK Miskolc', brand: '#22365A', ink: '#FFFFFF', accent: '#0071B3' },
  szkt: { name: 'SZKT Szeged', brand: '#08203C', ink: '#FFFFFF', stripe: '#2199E8', accent: '#1F5B93' },
  tuke: { name: 'Tüke Busz Pécs', brand: '#2A785B', ink: '#FFFFFF', stripe: '#7EB526', accent: '#2A785B' },
  vbusz: { name: 'V-Busz Veszprém', brand: '#781D7D', ink: '#FFFFFF', accent: '#9D3293' },
  blaguss: { name: 'Blaguss Szombathely', brand: '#B31415', ink: '#FFFFFF', accent: '#0A7DC1' },
  bahart: { name: 'Bahart', brand: '#003399', ink: '#FFFFFF', accent: '#003399' },
  kisvasut: { name: 'Kecskeméti Kisvasút', brand: '#002060', ink: '#FFFFFF', stripe: '#D5AA00', accent: '#9F0000' },
  thury: { name: 'Thury-Busz Várpalota', brand: '#971A1A', ink: '#FFFFFF', accent: '#971A1A' },
  homm: { name: 'HOMM Budaörs', brand: '#03935A', ink: '#000000', accent: '#03935A' },
  paksbusz: { name: 'Paksbusz', brand: '#647A27', ink: '#FFFFFF', stripe: '#9BC31C', accent: '#647A27' },
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
  return CITY_THEMES[key] || '';
}

/* The theme to paint, or null for Home Assistant's own look. An unknown id
   (a typo in YAML, a theme dropped later) falls back to Home Assistant's. */
export function resolveTheme(id, mode, city) {
  const key = id === THEME_OPERATOR ? operatorTheme(mode, city) : String(id || '');
  return THEMES[key] ? Object.assign({ id: key }, THEMES[key]) : null;
}

/* Custom properties set on the card's host, inherited by the shadow tree. */
export function themeVars(theme) {
  if (!theme) return '';
  return `--primary-color:${theme.accent};--ht-brand:${theme.brand};`
    + `--ht-brand-ink:${theme.ink};--ht-brand-stripe:${theme.stripe || theme.brand};`;
}

/* The title becomes a band flush with the card's top edge. --ht-band-pad is
   the card's own .wrap padding, so the band reaches the corners. */
export const THEME_CSS = `
  ha-card.themed .head {
    background: var(--ht-brand); color: var(--ht-brand-ink);
    margin: calc(-1 * var(--ht-band-top, 10px)) calc(-1 * var(--ht-band-side, 12px)) 9px;
    padding: 9px var(--ht-band-side, 12px) 8px;
    border-bottom: 3px solid var(--ht-brand-stripe);
    border-radius: var(--ha-card-border-radius, 12px) var(--ha-card-border-radius, 12px) 0 0;
  }
`;

import fs from 'node:fs';
import {
  launch, newPage, shot, sleep, until, waitRows, configure, rowSummary, openRowMap, waitMap,
  pType, pHits, pPick,
} from './lib.mjs';

const lang = process.argv[2] || 'en';
const only = (process.argv[3] || '').split(',').filter(Boolean);
const L = (en, hu) => (lang === 'hu' ? hu : en);
const STATE = new URL('./www/', import.meta.url);
const CFG_FILE = new URL('configs.json', STATE);
const HISTORY_FILE = new URL('history.json', STATE);
const configs = fs.existsSync(CFG_FILE) ? JSON.parse(fs.readFileSync(CFG_FILE, 'utf8')) : {};
const saveCfg = (k, v) => { configs[k] = v; fs.writeFileSync(CFG_FILE, JSON.stringify(configs, null, 1)); };
const HOP = 'hungarian-transport-card';
const HOP_ED = 'hungarian-transport-card-editor';
const PLAN = 'hungarian-transit-stop-card-plan';
const PLAN_ED = 'hungarian-transit-stop-card-plan-editor';
const TITLE = L('Hungarian transport card configuration', 'Magyar közlekedés kártya beállításai');
const PTITLE = L('Hungarian transit stop planner card configuration', 'Tervezőkártya beállításai');

const browser = await launch(lang);
const results = [];
async function scenario(name, fn, pageOpts) {
  if (only.length && !only.some((o) => name.startsWith(o))) return;
  console.log(`== ${lang} ${name}`);
  const page = await newPage(browser, pageOpts);
  try {
    await fn(page);
    results.push([name, 'ok']);
  } catch (err) {
    console.log(`  FAILED ${name}: ${err.message}`);
    results.push([name, `FAILED ${err.message}`]);
  } finally {
    await page.close();
  }
}

const mountEditor = (page, o) => page.evaluate((x) => window.__mountEditor(x), Object.assign({ lang, title: TITLE, tag: HOP, editorTag: HOP_ED, width: 1040, config: { type: `custom:${HOP}` } }, o));
const mountCard = (page, o) => page.evaluate((x) => window.__mountCard(x), Object.assign({ lang, tag: HOP, width: 560 }, o));
const withLang = (cfg) => Object.assign({}, cfg, { language: 'auto' });
const blur = (page) => page.evaluate(() => {
  if (document.activeElement) document.activeElement.blur();
  const c = window.__card;
  if (c && c.shadowRoot && c.shadowRoot.activeElement) c.shadowRoot.activeElement.blur();
});
const waitPlat = (page) => until(page, () => window.__card.shadowRoot.querySelectorAll('.plat').length > 0, null, 60000, 'platform badges').catch(() => console.log('  (no platforms)'));

/* ---------------- editor ---------------- */
await scenario('editor-empty', async (page) => {
  await mountEditor(page, {});
  await sleep(1500);
  await shot(page, lang, 'editor-empty');
});

await scenario('editor-key', async (page) => {
  await mountEditor(page, { noFutar: true });
  await sleep(1500);
  await shot(page, lang, 'editor-key');
});

await scenario('editor-mav', async (page) => {
  await mountEditor(page, {});
  await sleep(1000);
  const cfg = await configure(page, {
    mode: 'mav', origin: 'Kelenföld', originRe: 'Kelenf', dest: 'Székesfehérvár', destRe: '^Székesfehérvár$',
    shots: { search: 'editor-mav-search', dests: 'editor-mav-dests' },
    shotFn: (n) => shot(page, lang, n),
  });
  saveCfg('mav', cfg);
  await waitRows(page, 3);
  await waitPlat(page);
  await sleep(1500);
  await shot(page, lang, 'editor-mav-done');
});

await scenario('editor-city', async (page) => {
  await mountEditor(page, {});
  await sleep(1000);
  await configure(page, {
    mode: 'helyi', city: 'szeged', origin: 'Széchenyi tér', dest: 'Európa liget',
    shots: { search: 'editor-city-search' }, shotFn: (n) => shot(page, lang, n),
  });
  await page.evaluate(() => [...document.querySelectorAll('#minutesAfterChips .chip')].find((b) => b.textContent.trim() === '60').click());
  await sleep(800);
  saveCfg('city', await page.evaluate(() => window.__lastConfig));
  await waitRows(page, 3);
  await sleep(2000);
  await shot(page, lang, 'editor-city-done');
});

await scenario('editor-bkk', async (page) => {
  await mountEditor(page, {});
  await sleep(1000);
  const cfg = await configure(page, { mode: 'bkk', origin: 'Móricz Zsigmond körtér', dest: 'Fővám tér', destRe: '^Fővám tér$' });
  saveCfg('bkk', cfg);
});

await scenario('editor-volan', async (page) => {
  await mountEditor(page, {});
  await sleep(1000);
  const cfg = await configure(page, { mode: 'volan', origin: 'Népliget', dest: 'Kecskemét, autóbusz', destRe: 'Kecskemét, autóbusz-állomás' });
  cfg.minutesAfter = 240;
  saveCfg('volan', cfg);
  await waitRows(page, 1);
  await sleep(1500);
  await shot(page, lang, 'editor-volan-done');
});

/* ---------------- cards ---------------- */
await scenario('card-mav', async (page) => {
  await mountCard(page, { config: withLang(configs.mav) });
  await waitRows(page, 4);
  await waitPlat(page);
  await sleep(2500);
  console.log('  ' + (await rowSummary(page)).slice(0, 5).join('\n  '));
  await shot(page, lang, 'card-mav');
  /* tooltip of the seat-reservation mark */
  const box = await page.evaluate(() => {
    const p = window.__card.shadowRoot.querySelector('.picto.booking');
    if (!p) return null;
    p.focus();
    const row = p.closest('tr').getBoundingClientRect();
    const sh = document.querySelector('#shot').getBoundingClientRect();
    return { x: sh.x, y: row.y - 12, width: sh.width + 200, height: row.height + 64 };
  });
  if (box) { await sleep(400); await shot(page, lang, 'card-mav-tooltip', { clip: box }); }
});

await scenario('card-bkk', async (page) => {
  await mountCard(page, { config: withLang(Object.assign({}, configs.bkk, { minutesAfter: 30 })) });
  await waitRows(page, 4);
  await sleep(2500);
  console.log('  ' + (await rowSummary(page)).slice(0, 5).join('\n  '));
  await shot(page, lang, 'card-bkk');
}, { width: 900, height: 820 });

await scenario('card-volan', async (page) => {
  await mountCard(page, { config: withLang(configs.volan) });
  await waitRows(page, 1);
  await sleep(3000);
  console.log('  ' + (await rowSummary(page)).slice(0, 5).join('\n  '));
  await shot(page, lang, 'card-volan');
});

await scenario('card-city', async (page) => {
  await mountCard(page, { config: withLang(configs.city) });
  await waitRows(page, 3);
  await sleep(2000);
  await shot(page, lang, 'card-city');
});

await scenario('card-narrow', async (page) => {
  await mountCard(page, { width: 380, config: withLang(configs.mav) });
  await waitRows(page, 4);
  await waitPlat(page);
  await sleep(2500);
  await shot(page, lang, 'card-narrow');
}, { width: 420, height: 900 });

await scenario('card-dark', async (page) => {
  await mountCard(page, { config: withLang(Object.assign({}, configs.bkk, { minutesAfter: 30 })) });
  await waitRows(page, 4);
  await sleep(2500);
  await shot(page, lang, 'card-dark');
}, { theme: 'dark' });

await scenario('card-nokey', async (page) => {
  await mountCard(page, { noFutar: true, config: withLang(configs.bkk) });
  await until(page, () => /\S/.test(window.__card.shadowRoot.querySelector('.err').textContent), null, 30000, 'error banner');
  await sleep(1500);
  await shot(page, lang, 'card-nokey');
});

/* ---------------- maps ---------------- */
for (const [key, re, name] of [['bkk', '^\\s*\\S*\\s*47|^\\s*\\S*\\s*49', 'map-bkk'], ['mav', 'TÓPART|BAKONY|IC', 'map-mav'], ['volan', '.', 'map-volan'], ['city', '.', 'map-city']]) {
  await scenario(name, async (page) => {
    await mountCard(page, { config: withLang(configs[key]) });
    await waitRows(page, 1);
    await sleep(3000);
    await openRowMap(page, re, 90000);
    console.log('  foot:', await page.evaluate(() => document.querySelector('.ht-map-foot').textContent));
    await shot(page, lang, name, { selector: '.ht-map-panel', quality: 80 });
  }, { width: 900, height: 820 });
}

/* ---------------- planner ---------------- */
/* Suggestions come from the user's own picks: record a few real picks
   through the card's own search and history code. */
async function seedHistory(page) {
  await page.evaluate(async () => {
    const lib = globalThis.HungarianTransportLib;
    const hass = window.__card._hass;
    const picks = [['Keleti', /Keleti pályaudvar \(Budapest\)/, 5], ['Széll Kálmán', /^Széll Kálmán tér/, 4], ['Kelenföld vasútállomás', /Kelenföld/, 3], ['Népliget', /Népliget/, 2], ['Székesfehérvár', /^Székesfehérvár/, 2], ['Móricz Zsigmond', /Móricz/, 1]];
    for (const [q, re, n] of picks) {
      const hits = await lib.searchPlannerStops('', q);
      const hit = hits.find((h) => re.test(lib.plannerHitLabel(h, 'hu'))) || hits[0];
      if (!hit) continue;
      for (let i = 0; i < n; i++) await lib.recordStop(hass, hit);
    }
  });
}

const mountPlanner = (page, o = {}) => page.evaluate((x) => window.__mountCard(x), Object.assign({ lang, tag: PLAN, width: 560, config: { type: `custom:${PLAN}`, language: 'auto' } }, o));

async function remountWithHistory(page) {
  const data = await page.evaluate(() => JSON.stringify(window.__userData));
  await page.evaluate((d) => { window.__clear(); Object.assign(window.__userData, JSON.parse(d)); }, data);
  await mountPlanner(page);
  await sleep(2500);
}

async function planPair(page, oq, ore, dq, dre) {
  await pType(page, 'pq', oq);
  await until(page, () => window.__card.shadowRoot.querySelectorAll('#phits .hit').length > 0, null, 45000, 'planner origin hits');
  await sleep(1200);
  console.log('  origin hits:', (await pHits(page, 'phits')).slice(0, 8).join(' | '));
  await pPick(page, 'phits', ore);
  await sleep(1200);
  await pType(page, 'pdq', dq);
  await sleep(3000);
  console.log('  dest hits:', (await pHits(page, 'pdhits')).slice(0, 8).join(' | '));
  await pPick(page, 'pdhits', dre);
  await sleep(1000);
  await blur(page);
}

const waitJourneys = (page, min = 1) => until(page, (n) => {
  const c = window.__card;
  return !c._journeyLoading && c.shadowRoot.querySelectorAll('.itin').length >= n;
}, min, 120000, 'itineraries');

async function setWhen(page, mode, hhmm) {
  await page.evaluate((m) => window.__card.shadowRoot.querySelector(`#pwhen button[data-when="${m}"]`).click(), mode);
  await sleep(300);
  if (hhmm) {
    await page.evaluate((v) => {
      const time = window.__card.shadowRoot.querySelector('#ptime');
      time.value = String(v);
      time.dispatchEvent(new Event('change'));
    }, Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)));
  }
}

await scenario('planner-start', async (page) => {
  await mountPlanner(page);
  await sleep(1500);
  await shot(page, lang, 'planner-new');
  await seedHistory(page);
  await remountWithHistory(page);
  await shot(page, lang, 'planner-suggestions');
  await pType(page, 'pq', 'Fő tér');
  await until(page, () => window.__card.shadowRoot.querySelectorAll('#phits .hit').length > 3, null, 45000, 'search hits');
  await sleep(2000);
  console.log('  hits:', (await pHits(page, 'phits')).join(' | '));
  await shot(page, lang, 'planner-search');
  fs.writeFileSync(HISTORY_FILE, await page.evaluate(() => JSON.stringify(window.__userData)));
});

async function plannerWithHistory(page) {
  const hist = fs.existsSync(HISTORY_FILE) ? fs.readFileSync(HISTORY_FILE, 'utf8') : '{}';
  await page.evaluate((d) => Object.assign(window.__userData, JSON.parse(d)), hist);
  await mountPlanner(page);
  await sleep(1500);
}

await scenario('planner-now', async (page) => {
  await plannerWithHistory(page);
  await planPair(page, 'Kelenföld vasútállomás', 'Kelenföld', 'Székesfehérvár', '^Székesfehérvár');
  await until(page, () => window.__card.shadowRoot.querySelectorAll('tr[data-row-index]').length > 0, null, 120000, 'board rows');
  await sleep(6000);
  console.log('  ' + (await page.evaluate(() => [...window.__card.shadowRoot.querySelectorAll('tr[data-row-index]')].slice(0, 6).map((tr) => tr.innerText.replace(/\s+/g, ' ')))).join('\n  '));
  await shot(page, lang, 'planner-now');
});

await scenario('planner-transfer', async (page) => {
  await plannerWithHistory(page);
  await planPair(page, 'Keleti', 'Keleti pályaudvar \\(Budapest\\)', 'Szeged', 'Szeged vasútállomás');
  await until(page, () => !!window.__card.shadowRoot.querySelector('.journey-plan .itin.open'), null, 120000, 'transfer itinerary');
  await until(page, () => {
    const tiles = [...window.__card.shadowRoot.querySelectorAll('.j-map img.leaflet-tile')];
    return tiles.length > 0 && tiles.every((t) => t.classList.contains('leaflet-tile-loaded'));
  }, null, 60000, 'inline map tiles').catch(() => {});
  await sleep(2000);
  await shot(page, lang, 'planner-transfer');
  await page.evaluate(() => window.__card.shadowRoot.querySelector('#pmap').click());
  await waitMap(page, 90000);
  await shot(page, lang, 'planner-fullmap', { selector: '.ht-map-panel', quality: 80 });
}, { width: 900, height: 900 });

await scenario('planner-depart', async (page) => {
  await plannerWithHistory(page);
  await planPair(page, 'Keleti', 'Keleti pályaudvar \\(Budapest\\)', 'Szeged', 'Szeged vasútállomás');
  await sleep(1500);
  await setWhen(page, 'depart', '16:00');
  await waitJourneys(page, 2);
  await sleep(1500);
  /* the list folded, then one itinerary open */
  await page.evaluate(() => {
    const open = window.__card.shadowRoot.querySelector('.itin.open .itin-head');
    if (open) open.click();
  });
  await sleep(800);
  await blur(page);
  await shot(page, lang, 'planner-depart');
  await page.evaluate(() => {
    const heads = window.__card.shadowRoot.querySelectorAll('.itin-head');
    (heads[1] || heads[0]).click();
  });
  await sleep(1500);
  await until(page, () => {
    const tiles = [...window.__card.shadowRoot.querySelectorAll('.j-map img.leaflet-tile')];
    return tiles.length > 0 && tiles.every((t) => t.classList.contains('leaflet-tile-loaded'));
  }, null, 60000, 'inline map tiles').catch(() => {});
  await sleep(1500);
  await blur(page);
  await shot(page, lang, 'planner-itinerary');
});

await scenario('planner-arrive', async (page) => {
  await plannerWithHistory(page);
  await planPair(page, 'Széll Kálmán', '^Széll Kálmán tér', 'Esztergom', 'Esztergom');
  await sleep(1500);
  await setWhen(page, 'arrive', '18:00');
  await waitJourneys(page, 1);
  await sleep(1500);
  await page.evaluate(() => {
    const open = window.__card.shadowRoot.querySelector('.itin.open .itin-head');
    if (open) open.click();
  });
  await sleep(800);
  await blur(page);
  await shot(page, lang, 'planner-arrive');
});

await scenario('planner-cityrail', async (page) => {
  await plannerWithHistory(page);
  await planPair(page, 'Pécs, Kossuth tér', 'Kossuth tér.*Pécs|Pécs.*Kossuth', 'Budapest-Keleti', 'Keleti');
  await sleep(1500);
  await setWhen(page, 'depart', '14:00');
  await waitJourneys(page, 1);
  await sleep(2000);
  await until(page, () => {
    const tiles = [...window.__card.shadowRoot.querySelectorAll('.j-map img.leaflet-tile')];
    return tiles.length > 0 && tiles.every((t) => t.classList.contains('leaflet-tile-loaded'));
  }, null, 60000, 'inline map tiles').catch(() => {});
  await sleep(1500);
  await blur(page);
  const clip = await page.evaluate(() => {
    const sh = document.querySelector('#shot').getBoundingClientRect();
    const its = window.__card.shadowRoot.querySelectorAll('.itin');
    const last = (its[1] || its[0]).getBoundingClientRect();
    return { x: sh.x, y: sh.y, width: sh.width, height: Math.min(sh.height, last.bottom - sh.y + 16) };
  });
  await shot(page, lang, 'planner-cityrail', { clip });
});

await scenario('planner-editor', async (page) => {
  await page.evaluate((x) => window.__mountEditor(x), {
    lang, title: PTITLE, tag: PLAN, editorTag: PLAN_ED, width: 1040,
    config: { type: `custom:${PLAN}` },
  });
  await sleep(2000);
  await shot(page, lang, 'planner-editor');
});

await browser.close();
console.log('\nRESULTS');
results.forEach(([n, r]) => console.log(`${r === 'ok' ? 'ok  ' : 'FAIL'} ${n} ${r === 'ok' ? '' : r}`));

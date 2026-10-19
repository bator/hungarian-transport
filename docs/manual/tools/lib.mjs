import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const BASE = `http://127.0.0.1:${process.env.PORT || 8765}/index.html`;
export const OUT = process.env.OUT || fileURLToPath(new URL('../images', import.meta.url));

export async function launch(lang = 'en') {
  const locale = lang === 'hu' ? 'hu-HU' : 'en-GB';
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/usr/bin/google-chrome',
    headless: true,
    env: Object.assign({}, process.env, { LANG: `${locale.replace('-', '_')}.UTF-8`, LANGUAGE: locale.replace('-', '_') }),
    args: ['--no-first-run', '--no-default-browser-check', `--lang=${locale}`, `--accept-lang=${locale}`, '--font-render-hinting=none'],
  });
  return browser;
}

export async function newPage(browser, { width = 1400, height = 1000, theme = 'light' } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 2 });
  await page.emulateTimezone('Europe/Budapest');
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warn') console.log('  [page]', m.type(), m.text().slice(0, 200)); });
  page.on('response', (r) => { if (r.status() >= 400) console.log('  [http]', r.status(), r.url().slice(0, 160)); });
  page.on('pageerror', (e) => console.log('  [pageerror]', String(e).slice(0, 300)));
  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => window.__ready === true, { timeout: 20000 });
  if (theme !== 'light') await page.evaluate((t) => window.__setTheme(t), theme);
  return page;
}

export async function shot(page, lang, name, { selector = '#shot', clip, quality = 90 } = {}) {
  const dir = path.join(OUT, lang);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${name}.webp`);
  await new Promise((r) => setTimeout(r, 250));
  if (clip) {
    await page.screenshot({ path: file, type: 'webp', quality, clip });
  } else {
    const el = await page.$(selector);
    await el.screenshot({ path: file, type: 'webp', quality });
  }
  const kb = Math.round(fs.statSync(file).size / 1024);
  console.log(`  saved ${lang}/${name}.webp (${kb} kB)`);
  return file;
}

/* Wait for a predicate evaluated in the page, with a readable failure. */
export async function until(page, fn, arg, timeout = 60000, label = '') {
  try {
    await page.waitForFunction(fn, { timeout, polling: 250 }, arg);
  } catch (_err) {
    throw new Error(`timeout waiting for ${label || fn.toString().slice(0, 120)}`);
  }
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Rows shown by the mounted hop card. */
export async function waitRows(page, min = 1, timeout = 90000) {
  await until(page, (n) => {
    const c = window.__card;
    const rows = c && c.shadowRoot ? c.shadowRoot.querySelectorAll('tr[data-row-index]') : [];
    return rows.length >= n;
  }, min, timeout, `${min} departure rows`);
}

export async function cardText(page) {
  return page.evaluate(() => (window.__card && window.__card.shadowRoot ? window.__card.shadowRoot.textContent : '').replace(/\s+/g, ' ').slice(0, 600));
}

/* ---- editor driving (the editor renders in light DOM) ---- */
export async function typeIn(page, selector, text) {
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type(selector, text, { delay: 35 });
}

export async function hitTexts(page, listSel) {
  return page.evaluate((s) => [...document.querySelectorAll(`${s} .hit`)].map((b) => b.textContent.trim()), listSel);
}

/* Click the first hit in listSel whose text matches re (a string regex). */
export async function pickHit(page, listSel, re, timeout = 60000) {
  await until(page, ([s, r]) => [...document.querySelectorAll(`${s} .hit`)].some((b) => new RegExp(r, 'i').test(b.textContent)),
    [listSel, re], timeout, `hit /${re}/ in ${listSel}`);
  await page.evaluate(([s, r]) => {
    const b = [...document.querySelectorAll(`${s} .hit`)].find((x) => new RegExp(r, 'i').test(x.textContent));
    b.scrollIntoView({ block: 'center' });
    b.click();
  }, [listSel, re]);
}

export async function lastConfig(page) {
  return page.evaluate(() => window.__lastConfig || null);
}

export async function waitConfig(page, key, timeout = 60000) {
  await until(page, (k) => window.__lastConfig && window.__lastConfig[k], key, timeout, `config.${key}`);
  return lastConfig(page);
}

/* The editor paints the reachable list on focus of the destination field. */
export async function openDestList(page, timeout = 90000) {
  await until(page, () => (window.__editor._dests || []).length > 0 && !window.__editor._destsLoading, null, timeout, 'reachable destinations');
  await page.click('#dq');
  await until(page, () => document.querySelectorAll('#destHits .hit').length > 0, null, 10000, 'dest hits');
}

/* Configure a hop card through its real editor; returns the emitted config. */
export async function configure(page, { mode, city, origin, originRe, dest, destRe, shots = {}, shotFn }) {
  if (mode === 'mav' || mode === 'volan' || mode === 'helyi') {
    await page.click(`#${mode}`);
    await sleep(600);
  }
  if (mode === 'helyi' && city) {
    await until(page, (c) => [...document.querySelectorAll('#city option')].some((o) => o.value === c), city, 30000, 'city options');
    await page.select('#city', city);
    await sleep(800);
  }
  await typeIn(page, '#q', origin);
  await until(page, () => document.querySelectorAll('#hits .hit').length > 0, null, 45000, 'origin hits');
  await sleep(600);
  if (shots.search && shotFn) await shotFn(shots.search);
  console.log('  origin hits:', (await hitTexts(page, '#hits')).slice(0, 12).join(' | '));
  await pickHit(page, '#hits', originRe || origin);
  await openDestList(page);
  console.log('  dests:', (await hitTexts(page, '#destHits')).slice(0, 30).join(' | '));
  if (shots.dests && shotFn) await shotFn(shots.dests);
  await typeIn(page, '#dq', dest);
  await sleep(700);
  await pickHit(page, '#destHits', destRe || dest);
  const cfg = await waitConfig(page, 'destKey');
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  return cfg;
}

export async function rowSummary(page) {
  return page.evaluate(() => [...window.__card.shadowRoot.querySelectorAll('tr[data-row-index]')].slice(0, 12)
    .map((tr) => tr.innerText.replace(/\s+/g, ' ').trim()));
}

/* ---- planner (pickers live in the card's shadow root) ---- */
export const P = 'hungarian-transit-stop-card-plan';
export async function pType(page, id, text) {
  const sel = `${P} >>> #${id}`;
  await page.click(sel, { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type(sel, text, { delay: 35 });
}
export async function pHits(page, boxId) {
  return page.evaluate((b) => [...window.__card.shadowRoot.querySelectorAll(`#${b} .hit`)].map((x) => x.textContent.trim()), boxId);
}
export async function pPick(page, boxId, re, timeout = 60000) {
  await until(page, ([b, r]) => [...window.__card.shadowRoot.querySelectorAll(`#${b} .hit`)].some((x) => new RegExp(r, 'i').test(x.textContent)),
    [boxId, re], timeout, `planner hit /${re}/ in #${boxId}`);
  await page.evaluate(([b, r]) => {
    const x = [...window.__card.shadowRoot.querySelectorAll(`#${b} .hit`)].find((y) => new RegExp(r, 'i').test(y.textContent));
    x.click();
  }, [boxId, re]);
}
export async function pEval(page, fn, arg) {
  return page.evaluate(fn, arg);
}

/* Open the map of the first row matching re (badge/headsign text), wait for
   the route and the tiles, and return true when it opened. */
export async function openRowMap(page, re = '.', timeout = 60000) {
  const ok = await page.evaluate((r) => {
    const rows = [...window.__card.shadowRoot.querySelectorAll('tr[data-row-index]')];
    const tr = rows.find((x) => new RegExp(r, 'i').test(x.innerText)) || rows[0];
    if (!tr) return false;
    tr.querySelector('.map-open').click();
    return true;
  }, re);
  if (!ok) return false;
  await waitMap(page, timeout);
  return true;
}

export async function waitMap(page, timeout = 60000) {
  await until(page, () => {
    const o = document.querySelector('.ht-map-overlay');
    if (!o) return false;
    const foot = o.querySelector('.ht-map-foot');
    const loading = /…|\.\.\./.test(foot ? foot.textContent : '');
    const tiles = [...o.querySelectorAll('img.leaflet-tile')];
    return !loading && tiles.length > 0 && tiles.every((t) => t.classList.contains('leaflet-tile-loaded'));
  }, null, timeout, 'map overlay with tiles');
  await sleep(1500);
}

export async function closeMap(page) {
  await page.evaluate(() => { const b = document.querySelector('.ht-map-overlay .ht-map-close'); if (b) b.click(); });
  await sleep(500);
}

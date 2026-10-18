/* The integration's entry module: it imports the card bundle and, when that
   download fails, retries with a fresh URL instead of leaving every card on
   "Configuration error" until a full page reload. */
import { copyFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

let failed = false;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failed = true;
};

const warnings = [];
const warn = console.warn;
let onWarn = () => {};
console.warn = (...args) => {
  warnings.push(args.map(String).join(' '));
  onWarn();
};

async function loadFrom(dir, version) {
  copyFileSync('dist/hungarian-transport-loader.js', join(dir, 'hungarian-transport-loader.js'));
  await import(`${pathToFileURL(join(dir, 'hungarian-transport-loader.js')).href}?v=${version}`);
}

async function loaded(key, ms) {
  for (let waited = 0; waited < ms && !globalThis[key]; waited += 50) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return globalThis[key];
}

const card = (key) => `globalThis.${key} = import.meta.url;\n`;

/* Bundle present: one import of the plain versioned URL, so the browser's
   HTTP cache entry stays the same as before the loader existed. */
const ready = mkdtempSync(join(tmpdir(), 'ht-loader-'));
writeFileSync(join(ready, 'hungarian-transport.js'), card('__readyUrl'));
await loadFrom(ready, '1.2.3');
const readyUrl = await loaded('__readyUrl', 1000);
check('loads the bundle next to it with the same version', !!readyUrl && readyUrl.endsWith('/hungarian-transport.js?v=1.2.3'), readyUrl);
check('no retry when the first import works', warnings.length === 0, warnings.join(' | '));

/* First download fails: the bundle shows up only after the first attempt,
   like a connection that drops and comes back. */
const flaky = mkdtempSync(join(tmpdir(), 'ht-loader-'));
onWarn = () => writeFileSync(join(flaky, 'hungarian-transport.js'), card('__retryUrl'));
await loadFrom(flaky, '4.5.6');
const retryUrl = await loaded('__retryUrl', 3000);
const params = retryUrl ? new URL(retryUrl).searchParams : new URLSearchParams();
check('retries a failed import', !!retryUrl, retryUrl);
check('the retry uses a fresh URL and keeps the version', params.get('retry') === '1' && params.get('v') === '4.5.6', retryUrl);
check('the failure is logged once as a warning', warnings.length === 1 && /retry 1 of 5/.test(warnings[0]), warnings.join(' | '));

console.warn = warn;
rmSync(ready, { recursive: true, force: true });
rmSync(flaky, { recursive: true, force: true });
process.exit(failed ? 1 : 0);

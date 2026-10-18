/* Entry module the integration hands to Home Assistant (add_extra_js_url).
   Home Assistant imports it once and never retries. The card bundle is large,
   so when its download breaks off (Home Assistant restarting, a dropped
   connection), every card stays "Configuration error" until a full reload.
   Import the bundle from here and retry instead. A browser remembers a failed
   module URL for the rest of the page, so each retry asks for a fresh URL;
   the first attempt keeps the plain URL, and with it the HTTP cache. */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];
const here = new URL(import.meta.url);

function bundleUrl(attempt) {
  const url = new URL('hungarian-transport.js', here);
  const version = here.searchParams.get('v');
  if (version) url.searchParams.set('v', version);
  if (attempt) url.searchParams.set('retry', String(attempt));
  return url.href;
}

async function load() {
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      await import(bundleUrl(attempt));
      return;
    } catch (err) {
      if (attempt === RETRY_DELAYS_MS.length) {
        console.error('Hungarian transport: could not load the cards', err);
        return;
      }
      console.warn(`Hungarian transport: loading the cards failed, retry ${attempt + 1} of ${RETRY_DELAYS_MS.length}`, err);
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
    }
  }
}

load();

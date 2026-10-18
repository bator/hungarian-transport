/* Hungarian transport card loader. Generated from src/ by 'npm run build'; do not edit. */
const RETRY_DELAYS_MS = [1e3, 2e3, 4e3, 8e3, 15e3];
const here = new URL(import.meta.url);
function bundleUrl(attempt) {
  const url = new URL("hungarian-transport.js", here);
  const version = here.searchParams.get("v");
  if (version) url.searchParams.set("v", version);
  if (attempt) url.searchParams.set("retry", String(attempt));
  return url.href;
}
async function load() {
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      await import(bundleUrl(attempt));
      return;
    } catch (err) {
      if (attempt === RETRY_DELAYS_MS.length) {
        console.error("Hungarian transport: could not load the cards", err);
        return;
      }
      console.warn(`Hungarian transport: loading the cards failed, retry ${attempt + 1} of ${RETRY_DELAYS_MS.length}`, err);
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
    }
  }
}
load();

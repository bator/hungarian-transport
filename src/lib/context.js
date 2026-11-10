import { BkkLib, register } from '../lib/core.js';

/* A card's context: what the library needs from one card, made once per
   card and handed to the lib entry points it calls (the departure board,
   the ride map, the journey plan, the editors' searches). Each part is read
   when used, as the card's config, key and Home Assistant change over its
   life.
     cfg()        the card's config ({} before setConfig)
     apiKey()     its FUTÁR key ('' when FUTÁR goes through the integration)
     hass()       Home Assistant (null before the first hass)
     tripCache()  its trip cache (FUTÁR trips, route patterns, child poles)
     now()        the clock in ms
     indexes()    its index resolver for the config as it is now: its own
                  volanIndex / cityIndex / mavIndex, else the integration's
   card: the same names; any left out reads as empty, the clock as
   Date.now, the indexes as BkkLib.indexResolver(cfg()). */
export function cardContext(card) {
  const c = card || {};
  const ctx = {
    cfg: () => (c.cfg && c.cfg()) || {},
    apiKey: () => (c.apiKey ? c.apiKey() : ''),
    hass: () => (c.hass ? c.hass() : null),
    tripCache: () => (c.tripCache && c.tripCache()) || {},
    now: () => (c.now ? c.now() : Date.now()),
    indexes: () => (c.indexes ? c.indexes() : BkkLib.indexResolver(ctx.cfg())),
  };
  return ctx;
}

/* An index-reading function's index resolver ({volan(), city(), mav()}:
   cardContext().indexes(), or BkkLib.indexResolver() for the default
   indexes). One that is missing is a bug in the caller, not a reason to
   read the default index: it throws. */
export function needIndexes(ix) {
  if (ix && typeof ix.volan === 'function' && typeof ix.city === 'function' && typeof ix.mav === 'function') {
    return ix;
  }
  throw new TypeError('an index resolver is required (cardContext(...).indexes() or BkkLib.indexResolver())');
}

register({ cardContext });

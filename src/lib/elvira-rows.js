import { departureStillDue } from '../config.js';
import { BkkLib } from '../lib/core.js';

/* The integration's elvira_between answer as departure rows (elvira.py
   rows_from_stationinfo builds them; tests/fixtures/elvira holds one case
   both sides are checked against). Every row of the origin's board comes,
   each marked destHit when the destination's arrival board lists the
   train: only those reach the destination. */
Object.assign(BkkLib, {
  elviraRowsFromResult(res) {
    const tag = (rows) => rows.map((row) => (row && typeof row === 'object'
      ? Object.assign({}, row, { source: 'elvira' }) : row));
    if (!res) return [];
    if (Array.isArray(res)) return tag(res);
    if (Array.isArray(res.departures)) return tag(res.departures);
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.departures)) return tag(payload.departures);
    if (Array.isArray(payload)) return tag(payload);
    return [];
  },
  /* The destination's board lists the train. A failed destination board
     sends every row with destHit false, never a missing one. */
  elviraReaches(row) {
    return !!row && row.destHit === true;
  },
  /* A MÁV card's departures: the trains that reach the destination and are
     still due (at nowSec, the clock's by default). A train with only a
     number shows its kind, like FUTÁR's rows: ELVIRA's S (sebesvonat) as
     SV, since a bare S on these cards is FUTÁR's személyvonat
     (applyRailBadge). Each train is headed by its own terminus, not by the
     card's destination it was asked with. */
  elviraBoardRows(rows, nowSec) {
    return (Array.isArray(rows) ? rows : [])
      .filter((row) => BkkLib.elviraReaches(row) && departureStillDue(row.dep, nowSec))
      .map((row) => {
        const out = Object.assign({}, row, { source: 'elvira' });
        if (row.railKind && /^\d+$/.test(String(row.label || ''))) {
          out.label = row.railKind === 'S' ? 'SV' : row.railKind;
        }
        const end = String(row.end || '').trim();
        if (end) out.head = end;
        return out;
      });
  },
});

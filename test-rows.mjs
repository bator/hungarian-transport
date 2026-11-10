/* Departure rows (src/lib/rows.js) and the ELVIRA adapter
   (src/lib/elvira-rows.js), straight from src/: source, merge, overlay,
   the MÁV board's rows, and the elvira_between case Python is checked
   against (tests/fixtures/elvira). */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { BkkLib } from './src/lib/core.js';
import './src/lib/names.js';
import './src/lib/geo.js';
import './src/lib/transitous.js';
import './src/lib/vehicles.js';
import './src/lib/rows.js';
import './src/lib/elvira-rows.js';

const now = Math.floor(Date.now() / 1000);
const rail = (extra) => Object.assign({ vehicle: 'rail', rawType: 'RAIL', dep: now + 1800, sched: now + 1800 }, extra);
const addElvira = (row) => BkkLib.elviraBoardRows([row])[0] || null;

test('a row without source is read off its trip id; a vehicle’s own source is not a row’s', () => {
  assert.equal(BkkLib.rowSource({ tripId: 'BKK_4541_108' }), 'futar');
  assert.equal(BkkLib.rowSource({ tripId: 'gtfs:6990:20261110:600:3' }), 'gtfs');
  assert.equal(BkkLib.rowSource({ tripId: 'elvira:2975814' }), 'elvira');
  assert.equal(BkkLib.rowSource({ tripId: 'motis:hu-mav_914' }), 'motis');
  assert.equal(BkkLib.rowSource({ tripId: '' }), '');
  assert.equal(BkkLib.rowSource(null), '');
  assert.equal(BkkLib.rowSource({ tripId: 'gtfs:1', source: 'volan' }), 'gtfs');
  assert.equal(BkkLib.rowSource({ tripId: 'BKK_1', source: 'elvira' }), 'elvira');
  assert.ok(BkkLib.isFutarTripId('BKK_4541_108'));
  assert.ok(!BkkLib.isFutarTripId('elvira:2889849') && !BkkLib.isFutarTripId('motis:hu-bkk_1') && !BkkLib.isFutarTripId(''));
});

test('Transitous rows say they are Transitous’', () => {
  const at = new Date((now + 600) * 1000).toISOString();
  const end = new Date((now + 4200) * 1000).toISOString();
  const leg = { mode: 'RAIL', routeShortName: 'IC', tripShortName: '914', headsign: 'Szombathely', startTime: at, endTime: end, tripId: 'hu-mav_914' };
  const plan = BkkLib.rowsFromMotis({ itineraries: [{ transfers: 0, startTime: at, endTime: end, legs: [leg] }] }, { name: 'Győr' }, 'mav', 180);
  const board = BkkLib.rowsFromMotisStopTimes({ stopTimes: [{ mode: 'RAIL', tripId: 'hu-mav_914', displayName: 'IC', place: { departure: at } }] });
  assert.deepEqual([plan, board].map((rows) => rows.map((r) => [r.tripId, r.source])),
    [[['motis:hu-mav_914', 'motis']], [['motis:hu-mav_914', 'motis']]]);
});

test('the painted row keeps its source', () => {
  assert.equal(BkkLib.asRow(rail({ tripId: 'elvira:1', source: 'elvira', label: 'IC' }), '', 'hu').source, 'elvira');
  assert.equal(BkkLib.asRow({ tripId: 'BKK_1', label: '7', vehicle: 'bus' }, '', 'hu').source, 'futar');
});

test('mergeRows lists the hosts and fills them from the same departure of another source', () => {
  const gtfs = { tripId: 'gtfs:7:1', source: 'gtfs', label: '7', vehicle: 'bus', dep: now + 600, sched: now + 600, color: '#4477aa' };
  const futar = { tripId: 'BKK_7', source: 'futar', label: '7', vehicle: 'bus', dep: now + 600, sched: now + 600, color: '#009EE3', lat: 47.5, lon: 19.05 };
  const other = { tripId: 'gtfs:8:1', source: 'gtfs', label: '8', vehicle: 'bus', dep: now + 300, sched: now + 300 };
  const merged = BkkLib.mergeRows([gtfs, other], [futar], 12);
  assert.deepEqual(merged.map((r) => [r.label, r.tripId, r.source]), [['8', 'gtfs:8:1', 'gtfs'], ['7', 'BKK_7', 'futar']]);
  /* The host itself is filled: the FUTÁR trip id, and with it its source. */
  assert.equal(merged[1], gtfs);
  assert.equal(gtfs.color, '#009EE3');
  assert.deepEqual([gtfs.lat, gtfs.lon], [47.5, 19.05]);
  assert.equal(BkkLib.mergeRows([], [], 0).length, 0);
  assert.equal(BkkLib.mergeRows(Array.from({ length: 20 }, (_, i) => ({ label: String(i), dep: now + i * 60 }))).length, 12);
});

test('a Transitous trip id goes only onto a host without GPS; ELVIRA’s and a timetable’s never', () => {
  const host = (extra) => Object.assign({ tripId: 'gtfs:6990:1', source: 'gtfs', label: '6990', vehicle: 'bus', dep: now + 600, sched: now + 600 }, extra);
  const motis = { tripId: 'motis:hu-volan_1', source: 'motis', label: '6990', vehicle: 'bus', dep: now + 600, sched: now + 600 };
  const bare = BkkLib.mergeRows([host()], [motis], 12)[0];
  const located = BkkLib.mergeRows([host({ lat: 47.1, lon: 18.4 })], [motis], 12)[0];
  assert.deepEqual([bare.tripId, bare.source], ['motis:hu-volan_1', 'motis']);
  assert.deepEqual([located.tripId, located.source], ['gtfs:6990:1', 'gtfs']);
  const fromElvira = BkkLib.overlayRows([rail({ tripId: 'BKK_966_313', source: 'futar', label: 'IC', trainNumber: '966' })],
    [rail({ tripId: 'elvira:2975814', source: 'elvira', label: 'GÖCSEJ', trainNumber: '966', platform: '6' })], 12)[0];
  assert.deepEqual([fromElvira.tripId, fromElvira.source, fromElvira.label, fromElvira.platform], ['BKK_966_313', 'futar', 'GÖCSEJ', '6']);
});

test('the source decides, not the trip id: an ELVIRA row pairs by its own kind and terminus', () => {
  /* Trip ids without a prefix (a FUTÁR-looking one on the ELVIRA row): the
     rows' source still makes the number-only S a sebesvonat to Székesfehérvár. */
  const sv = rail({ tripId: '9702', source: 'elvira', label: '9702', railKind: 'S', trainNumber: '9702', end: 'Székesfehérvár', head: 'Kelenföld', destHit: true, platform: '4' });
  const adHoc = rail({ tripId: 'BKK_1792439004037', source: 'futar', label: 'S', head: 'Székesfehérvár' });
  const merged = BkkLib.overlayRows([adHoc], [sv], 12, addElvira);
  assert.deepEqual(merged.map((r) => [r.tripId, r.trainNumber, r.platform]), [['BKK_1792439004037', '9702', '4']]);
});

test('overlayRows drops an overlay row no host has, unless addUnmatched lists it', () => {
  const host = rail({ tripId: 'BKK_966_313', source: 'futar', label: 'IC', trainNumber: '966' });
  const gemenc = rail({ tripId: 'elvira:1', source: 'elvira', label: 'GEMENC', trainNumber: '800', booking: true, destHit: false, dep: now + 1860, sched: now + 1860 });
  const topart = rail({ tripId: 'elvira:847', source: 'elvira', label: 'TÓPART', railKind: 'IC', trainNumber: '847', end: 'Budapest-Déli', destHit: true, dep: now + 2400, sched: now + 2400 });
  assert.deepEqual(BkkLib.overlayRows([host], [gemenc, topart], 12).map((r) => r.tripId), ['BKK_966_313']);
  const listed = BkkLib.overlayRows([host], [gemenc, topart], 12, addElvira);
  assert.deepEqual(listed.map((r) => [r.tripId, r.source, r.head || '']), [['BKK_966_313', 'futar', ''], ['elvira:847', 'elvira', 'Budapest-Déli']]);
  /* No pairing by label and minute: GEMENC on a same-minute FUTÁR IC would copy its booking. */
  const sameMinute = BkkLib.overlayRows([rail({ tripId: 'BKK_1', source: 'futar', label: 'IC' })],
    [rail({ tripId: 'elvira:2', source: 'elvira', label: 'IC', booking: true, destHit: false })], 12);
  assert.deepEqual(sameMinute.map((r) => [r.tripId, !!r.booking]), [['BKK_1', false]]);
});

test('ELVIRA rows from any shape of the service answer carry their source', () => {
  const raw = { tripId: 'elvira:1', label: 'TOKAJ', destHit: true };
  for (const res of [[raw], { departures: [raw] }, { response: { departures: [raw] } }, { service_response: { departures: [raw] } }]) {
    assert.deepEqual(BkkLib.elviraRowsFromResult(res), [Object.assign({}, raw, { source: 'elvira' })]);
  }
  assert.deepEqual(BkkLib.elviraRowsFromResult(null), []);
  assert.deepEqual(BkkLib.elviraRowsFromResult({ response: {} }), []);
  assert.equal(raw.source, undefined);
});

test('only a train the destination’s board lists (destHit true) reaches it', () => {
  assert.deepEqual([{ destHit: true }, { destHit: false }, {}, { destHit: 1 }, { destHit: 'true' }, null].map((r) => BkkLib.elviraReaches(r)),
    [true, false, false, false, false, false]);
});

test('a MÁV board keeps the trains its destination lists, a number-only train shows its kind, a departed one goes', () => {
  const board = BkkLib.elviraBoardRows([
    { tripId: 'a', dep: now + 600, destHit: true, label: '9009', railKind: 'S', trainNumber: '9009' },
    { tripId: 'b', dep: now + 600, destHit: false, label: '871', trainNumber: '871' },
    { tripId: 'c', dep: now - 600, destHit: true, label: '872', trainNumber: '872' },
    { tripId: 'd', dep: now + 900, destHit: true, label: 'TÓPART', railKind: 'IC', trainNumber: '849' },
    { tripId: 'e', dep: now + 900, label: '873', trainNumber: '873' },
  ]);
  assert.deepEqual(board.map((r) => [r.tripId, r.label, r.trainNumber, r.source]),
    [['a', 'SV', '9009', 'elvira'], ['d', 'TÓPART', '849', 'elvira']]);
});

test('an ELVIRA sebesvonat is SV with its own badge, not Sz; gyorsvonat has its own; FUTÁR S stays Sz', () => {
  const painted = (row) => {
    const r = BkkLib.asRow(Object.assign({ vehicle: 'rail', rawType: 'RAIL', color: '#4477aa', text: '#ffffff' }, row), 'Székesfehérvár', 'hu');
    return [r.label, String(r.color).replace('#', '').toUpperCase()];
  };
  const kinds = BkkLib.elviraBoardRows([
    { tripId: 'elvira:9702', dep: now + 600, destHit: true, label: '9702', railKind: 'S', trainNumber: '9702' },
    { tripId: 'elvira:952', dep: now + 700, destHit: true, label: '952', railKind: 'Gy', trainNumber: '952' },
    { tripId: 'elvira:4522', dep: now + 800, destHit: true, label: '4522', railKind: 'Sz', trainNumber: '4522' },
  ]).map(painted);
  assert.deepEqual(kinds, [['SV', '29803C'], ['Gy', 'C82227'], ['Sz', '5C6B7A']]);
  assert.deepEqual(painted({ tripId: 'BKK_4522_1', label: 'S' }), ['Sz', '5C6B7A']);
  /* SV and Gy are train kinds: a bus line named so keeps the default colour. */
  const busKinds = ['SV', 'Gy'].map((label) => String(BkkLib.asRow({ tripId: 'volan_1', label, vehicle: 'bus', rawType: 'BUS', color: '#4477aa', text: '#ffffff' }, 'Székesfehérvár', 'hu').color));
  assert.deepEqual(busKinds, ['4477aa', '4477aa']);
});

test('a MÁV board row shows the train’s terminus, the destination only when ELVIRA has none', () => {
  const heads = BkkLib.elviraBoardRows([
    { tripId: 'elvira:849', dep: now + 600, destHit: true, label: 'TÓPART', head: 'Budapest-Kelenföld', end: 'Budapest-Déli' },
    { tripId: 'elvira:9009', dep: now + 700, destHit: true, label: '9009', head: 'Budapest-Kelenföld', end: '' },
  ]).map((r) => r.head);
  assert.deepEqual(heads, ['Budapest-Déli', 'Budapest-Kelenföld']);
});

test('the elvira_between case Python is checked against makes these MÁV board rows', () => {
  const caseFile = new URL('./tests/fixtures/elvira/between.json', import.meta.url);
  const shared = JSON.parse(readFileSync(caseFile, 'utf8'));
  const at = Math.floor(Date.parse(shared.now) / 1000);
  const rows = BkkLib.elviraRowsFromResult({ response: { departures: shared.rows } });
  assert.deepEqual(BkkLib.elviraBoardRows(rows, at), shared.board_rows);
  /* The answer itself is not changed in place. */
  assert.ok(shared.rows.every((r) => r.source === undefined && r.head === shared.dest_name));
});

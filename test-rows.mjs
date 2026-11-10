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

/* Checks moved from test-render.mjs: one of a block's checks, as an
   assertion. */
const ok = (name, cond, detail) => assert.ok(cond, detail ? `${name}: ${detail}` : name);
/* Kelenföld → Székesfehérvár, 2026-10-19: FUTÁR gives two ICs no
   tripShortName, so their numbers are read off the trip id (8624, 2461);
   ELVIRA has them as BALATON 862 and CITADELLA 246, platform 4. */
const icFutar = (extra) => Object.assign({
  label: 'IC', color: '#2E5EA8', text: '#FFFFFF', vehicle: 'rail', rawType: 'RAIL',
  platform: '', booking: false, delay: 0,
}, extra);
const icElvira = (extra) => Object.assign({
  vehicle: 'rail', rawType: 'RAIL', color: '#4477aa', text: '#ffffff',
  head: 'Székesfehérvár', platform: '4', booking: true, destHit: true, delay: 0,
}, extra);

/* Merge and overlay */

test('a rail row keeps its own label through mergeRows; a bus number is no rail badge', () => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep: dep, sched: dep,
  }, extra);
  ok('numbered through express stays on the hop',
    BkkLib.mergeRows([rail({
      label: '16907', trainNumber: '16907', booking: true, tripId: 'elvira:3640191',
    })], [], 12)[0].label === '16907');
  ok('numbered through személy stays on the hop',
    BkkLib.mergeRows([rail({
      label: '9792', trainNumber: '9792', booking: false, tripId: 'elvira:2888904',
    })], [], 12)[0].label === '9792');
  ok('another numbered személy stays the same way',
    BkkLib.mergeRows([rail({
      label: '9704', trainNumber: '9704', booking: false, tripId: 'elvira:2888905',
    })], [], 12)[0].label === '9704');
  ok('generic EX plus a long train number stays as EX',
    BkkLib.mergeRows([rail({
      label: 'EX', trainNumber: '16907', booking: true, tripId: 'elvira:1',
    })], [], 12)[0].label === 'EX');
  ok('generic EN plus a long train number stays as EN',
    BkkLib.mergeRows([rail({
      label: 'EN', trainNumber: '4240', booking: true, tripId: 'elvira:2',
    })], [], 12)[0].label === 'EN');
  ok('generic IC keeps a named overlay slot',
    BkkLib.mergeRows([rail({
      label: 'IC', trainNumber: '852', booking: true, tripId: 'elvira:1',
    })], [], 12)[0].label === 'IC');
  ok('named IC stays on the hop',
    BkkLib.mergeRows([rail({
      label: 'BAKONY', trainNumber: '904', booking: true, tripId: 'elvira:2931213',
    })], [], 12)[0].label === 'BAKONY');
  ok('suburban Z30 stays on the hop',
    BkkLib.mergeRows([rail({
      label: 'Z30', trainNumber: '4542', booking: false, tripId: 'elvira:2887496',
    })], [], 12)[0].label === 'Z30');
  ok('numbered FUTAR overlay does not replace G43',
    BkkLib.mergeRows([rail({
      label: 'G43', trainNumber: '19707', dep: dep, sched: dep, color: '#AACD46',
    })], [rail({
      label: '19707', trainNumber: '19707', dep: dep, sched: dep,
    })], 12).length === 1
    && BkkLib.mergeRows([rail({
      label: 'G43', trainNumber: '19707', dep: dep, sched: dep, color: '#AACD46',
    })], [rail({
      label: '19707', trainNumber: '19707', dep: dep, sched: dep,
    })], 12)[0].label === 'G43');
  ok('city bus number is not treated as a rail badge',
    BkkLib.mergeRows([{
      label: '7', vehicle: 'bus', dep: dep, sched: dep, tripId: 'BKK_X',
    }], [], 12)[0].label === '7');
});

test('elvira placeholder blue takes FUTAR G43/Z30 brand colors on merge', () => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const merged = BkkLib.mergeRows(
    [
      { label: 'G43', dep: dep, sched: dep, color: '#4477aa', text: '#ffffff', platform: '5' },
      { label: 'Z30', dep: dep + 120, sched: dep + 120, color: '#4477aa', text: '#ffffff', platform: '7' },
    ],
    [
      { label: 'G43', dep: dep, sched: dep, color: '#AACD46', text: '#FFFFFF' },
      { label: 'Z30', dep: dep + 120, sched: dep + 120, color: '#FFCD28', text: '#3C3C3C' },
    ],
    12,
  );
  const g43 = merged.find((r) => r.label === 'G43');
  const z30 = merged.find((r) => r.label === 'Z30');
  assert.ok(g43 && z30
    && String(g43.color).replace('#', '').toUpperCase() === 'AACD46'
    && String(z30.color).replace('#', '').toUpperCase() === 'FFCD28'
    && g43.platform === '5' && z30.platform === '7');
});

test('overlay copies ELVIRA platform and booking onto a FUTAR host', () => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const merged = BkkLib.mergeRows(
    [{
      label: 'IC', dep, sched: dep, color: '#4477aa', tripId: 'BKK_914_1',
    }],
    [{
      label: 'IC', dep, sched: dep, color: '#2E5EA8',
      platform: '4', booking: true, trainNumber: '914', tripId: 'elvira:1',
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].platform === '4'
    && merged[0].booking === true
    && merged[0].tripId === 'BKK_914_1'
    && BkkLib.rowTrainNumber(merged[0]) === '914');
});

test('GÖCSEJ ELVIRA platform merges onto FUTAR IC 955 even without trainNumber on FUTAR', () => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const merged = BkkLib.mergeRows(
    [{
      label: 'GÖCSEJ', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#4477aa', platform: '6', trainNumber: '955', tripId: 'elvira:2975814',
    }],
    [{
      label: 'IC', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#2E5EA8', tripId: 'BKK_955_313',
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].platform === '6'
    && merged[0].label === 'GÖCSEJ'
    && BkkLib.rowTrainNumber(merged[0]) === '955');
});

test('GÖCSEJ 967 same-minute merge keeps origin vágány', () => {
  const dep = Math.floor(Date.now() / 1000) + 120;
  const merged = BkkLib.mergeRows(
    [{
      label: 'GÖCSEJ', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#4477aa', platform: '6', trainNumber: '967', tripId: 'elvira:2939299',
    }],
    [{
      label: 'IC', vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
      color: '#2E5EA8', trainNumber: '967', tripId: 'BKK_967_313',
    }],
    12,
  );
  assert.ok(merged.length === 1 && merged[0].platform === '6');
});

test('origin overlayOnly copies booking onto FUTAR without dest hit and drops GEMENC', () => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = BkkLib.overlayRows(
    [
      rail({ label: 'IC', trainNumber: '966', tripId: 'BKK_966_313' }),
      rail({
        label: 'G43', trainNumber: '3511', tripId: 'BKK_3511_108',
        dep: dep + 120, sched: dep + 120,
      }),
      rail({
        label: 'Z30', trainNumber: '4543', tripId: 'BKK_4543_108',
        dep: dep + 240, sched: dep + 240,
      }),
    ],
    [
      rail({
        label: 'GÖCSEJ', trainNumber: '966', booking: true,
        platform: '6', tripId: 'elvira:2975814', destHit: false,
      }),
      rail({
        label: 'GEMENC', trainNumber: '800', booking: true,
        dep: dep + 60, sched: dep + 60, tripId: 'elvira:gemenc', destHit: false,
      }),
      rail({
        label: 'G43', trainNumber: '3511', booking: false,
        dep: dep + 120, sched: dep + 120, tripId: 'elvira:g43', destHit: false,
      }),
      rail({
        label: 'Z30', trainNumber: '4543', booking: false,
        dep: dep + 240, sched: dep + 240, tripId: 'elvira:z30', destHit: false,
      }),
    ],
    12,
  );
  const gocsej = merged.find((r) => BkkLib.rowTrainNumber(r) === '966');
  const g43 = merged.find((r) => r.label === 'G43');
  const z30 = merged.find((r) => r.label === 'Z30');
  assert.ok(merged.length === 3
    && gocsej && gocsej.booking === true && gocsej.platform === '6' && gocsej.label === 'GÖCSEJ'
    && g43 && g43.booking !== true
    && z30 && z30.booking !== true
    && !merged.some((r) => String(r.label || '').toUpperCase() === 'GEMENC'));
});

test('overlayOnly does not pair GEMENC onto a same-minute FUTAR IC', () => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = BkkLib.overlayRows(
    [rail({ label: 'IC', trainNumber: '966', tripId: 'BKK_966_313' })],
    [rail({
      label: 'GEMENC', trainNumber: '800', booking: true,
      platform: '3', tripId: 'elvira:gemenc', destHit: false,
    })],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'IC'
    && BkkLib.rowTrainNumber(merged[0]) === '966'
    && merged[0].booking !== true
    && merged[0].platform !== '3');
});

test('overlayOnly does not byTime-merge two ICs at the same minute', () => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL', dep, sched: dep,
  }, extra);
  const merged = BkkLib.overlayRows(
    [rail({ label: 'IC', tripId: 'BKK_966_313' })],
    [rail({
      label: 'IC', booking: true, platform: '3',
      tripId: 'elvira:other-ic', destHit: false,
    })],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'IC'
    && merged[0].booking !== true
    && merged[0].platform !== '3'
    && merged[0].tripId === 'BKK_966_313');
});

test('overlayOnly matches repeated train numbers to the correct departure', () => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL',
  }, extra);
  const merged = BkkLib.overlayRows(
    [
      rail({ label: 'IC', trainNumber: 'IC 914', dep, sched: dep, tripId: 'BKK_914_1' }),
      rail({
        label: 'IC', trainNumber: 'IC 914', dep: dep + 3600, sched: dep + 3600,
        tripId: 'BKK_914_2',
      }),
    ],
    [
      rail({
        label: 'IC', trainNumber: '914', dep: dep + 3600, sched: dep + 3600,
        booking: true, tripId: 'elvira:914',
      }),
    ],
    12,
  );
  assert.ok(merged.length === 2
    && merged[0].booking !== true
    && merged[1].booking === true
    && merged[0].tripId === 'BKK_914_1'
    && merged[1].tripId === 'BKK_914_2');
});

test('overlayOnly matches ELVIRA booking when scheduled departures differ by minutes', () => {
  const dep = Math.floor(Date.now() / 1000) + 600;
  const rail = (extra) => Object.assign({
    vehicle: 'rail', rawType: 'RAIL',
  }, extra);
  const merged = BkkLib.overlayRows(
    [
      rail({ label: 'IC', trainNumber: 'IC 914', dep, sched: dep, tripId: 'BKK_914_1' }),
      rail({
        label: 'IC', trainNumber: 'IC 914', dep: dep + 3600, sched: dep + 3600,
        tripId: 'BKK_914_2',
      }),
    ],
    [
      rail({
        label: 'IC', trainNumber: '914', dep: dep + 3720, sched: dep + 3720,
        booking: true, tripId: 'elvira:914',
      }),
    ],
    12,
  );
  assert.ok(merged.length === 2
    && merged[0].booking !== true
    && merged[1].booking === true
    && merged[1].tripId === 'BKK_914_2');
});

test('mode all: an ELVIRA train to add pairs as strictly as an overlay row', () => {
  /* Mode 'all' (the planner's Now board): an ELVIRA train its destination's
     board confirms is added when no FUTÁR row has it. It pairs as strictly
     as an overlay row: not by named/generic IC or label at the same minute,
     which would put its booking and name on another train. A FUTÁR trip
     with no number (an ad-hoc timestamp id) of the same kind, minute and
     terminus is the same train, not a second row. */
  const dep = Math.floor(Date.now() / 1000) + 1800;
  const rail = (extra) => Object.assign({ vehicle: 'rail', rawType: 'RAIL', dep, sched: dep }, extra);
  const elvira = (extra) => rail(Object.assign({
    color: '#4477aa', text: '#ffffff', head: 'Székesfehérvár', platform: '4', booking: true, destHit: true,
  }, extra));
  const addElvira = (row) => BkkLib.elviraBoardRows([row])[0] || null;
  const merge = (futar, rows) => BkkLib.overlayRows(futar, rows, 12, addElvira);
  const show = (rows) => JSON.stringify(rows.map((r) => [r.tripId, r.label, r.trainNumber || '', r.platform || '', !!r.booking]));
  const sv = elvira({ tripId: 'elvira:9702', label: '9702', railKind: 'S', trainNumber: '9702', end: 'Székesfehérvár' });
  const adHoc = rail({ tripId: 'BKK_1792439004037', label: 'S', head: 'Székesfehérvár' });
  const one = merge([adHoc], [sv]);
  ok('mode all: a number-only ELVIRA train is one row with a same-minute FUTÁR S without a number',
    one.length === 1 && one[0].tripId === 'BKK_1792439004037' && one[0].trainNumber === '9702'
    && one[0].platform === '4', show(one));
  const otherEnd = merge([rail({ tripId: 'BKK_1792439004037', label: 'S', head: 'Siófok' })], [sv]);
  const otherKind = merge([rail({ tripId: 'BKK_1792439004038', label: 'IC', head: 'Székesfehérvár' })], [sv]);
  const numbered = merge([rail({ tripId: 'BKK_4561_108', label: 'S', trainNumber: '4561', head: 'Székesfehérvár' })], [sv]);
  ok('mode all: another terminus, kind or train number at that minute keeps the ELVIRA train its own row',
    [otherEnd, otherKind, numbered].every((m) => m.length === 2 && m.some((r) => r.tripId === 'elvira:9702')
      && m.every((r) => r.tripId === 'elvira:9702' || (r.platform !== '4' && r.booking !== true))),
    show([].concat(otherEnd, otherKind, numbered)));
  const icHost = () => rail({ tripId: 'BKK_1792439004039', label: 'IC', head: 'Keszthely' });
  const named = merge([icHost()], [elvira({
    tripId: 'elvira:847', label: 'TÓPART', railKind: 'IC', trainNumber: '847', end: 'Budapest-Déli',
  })]);
  const generic = merge([icHost()], [elvira({
    tripId: 'elvira:856', label: 'IC', railKind: 'IC', trainNumber: '856', end: 'Nagykanizsa',
  })]);
  ok('mode all: an ELVIRA train to add pairs with no same-minute FUTÁR IC by name or label',
    named.length === 2 && named.some((r) => r.tripId === 'BKK_1792439004039' && r.label === 'IC' && r.booking !== true)
    && generic.length === 2 && generic.some((r) => r.tripId === 'BKK_1792439004039' && r.booking !== true && r.platform !== '4'),
    show([].concat(named, generic)));
});

test('ELVIRA named IC keeps label and takes FUTAR GPS by train number', () => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = BkkLib.mergeRows(
    [{
      label: 'BAKONY', tripId: 'elvira:2974501', trainNumber: '913',
      dep: dep, sched: dep, color: '#4477aa', text: '#ffffff',
    }],
    [{
      label: 'IC', tripId: 'BKK_913_316', trainNumber: '913',
      dep: dep + 60, sched: dep, color: '#2E5EA8', text: '#FFFFFF',
      lat: 47.19751, lon: 18.16734,
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'BAKONY'
    && merged[0].tripId === 'BKK_913_316'
    && merged[0].lat === 47.19751
    && merged[0].lon === 18.16734);
});

test('ELVIRA Tópart takes FUTAR GPS from BKK trip id train number', () => {
  const dep = Math.floor(Date.now() / 1000) + 1200;
  const merged = BkkLib.mergeRows(
    [{
      label: 'TÓPART', tripId: 'elvira:2888051', trainNumber: '853',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    [{
      label: 'IC', tripId: 'BKK_853_87', dep: dep, sched: dep,
      lat: 46.90843, lon: 18.05495,
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'TÓPART'
    && merged[0].tripId === 'BKK_853_87'
    && merged[0].lat === 46.90843);
});

test('FUTÁR ICs BKK_8624_67 and BKK_2461_311 take ELVIRA BALATON 862 and CITADELLA 246', () => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 25 * 60;
  const merged = BkkLib.overlayRows(
    [
      icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 }),
      icFutar({ tripId: 'BKK_2461_311', trainNumber: '2461', head: 'Ljubljana', dep: t1, sched: t1 }),
    ],
    [
      icElvira({ tripId: 'elvira:balaton', label: 'BALATON', trainNumber: '862', dep: t0, sched: t0 }),
      icElvira({ tripId: 'elvira:citadella', label: 'CITADELLA', trainNumber: '246', dep: t1, sched: t1 }),
    ],
    12,
  );
  const balaton = merged.find((r) => r.tripId === 'BKK_8624_67');
  const citadella = merged.find((r) => r.tripId === 'BKK_2461_311');
  assert.ok(merged.length === 2
    && !!balaton && balaton.label === 'BALATON' && balaton.platform === '4'
    && balaton.booking === true && balaton.trainNumber === '862' && balaton.head === 'Keszthely'
    && !!citadella && citadella.label === 'CITADELLA' && citadella.platform === '4'
    && citadella.booking === true && citadella.trainNumber === '246'
    /* EMMA GPS goes by the number: 8624 never met "862 BALATON". */
    && BkkLib.matchEmmaVehicle(balaton, { trip: { tripShortName: '862 BALATON InterCity' } }));
});

test('a trip-id IC takes no ELVIRA train one digit shorter outside the 5-minute window', () => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const merged = BkkLib.overlayRows(
    [icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 })],
    [icElvira({
      tripId: 'elvira:balaton', label: 'BALATON', trainNumber: '862',
      dep: t0 + 6 * 60, sched: t0 + 6 * 60,
    })],
    12,
  );
  assert.ok(merged.length === 1 && merged[0].label === 'IC' && merged[0].trainNumber === '8624'
    && merged[0].platform === '' && merged[0].booking !== true);
});

test('an IC ELVIRA renumbered stays one row when FUTÁR and ELVIRA merge onto it again', () => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const futar = () => [icFutar({ tripId: 'BKK_8624_67', trainNumber: '8624', head: 'Keszthely', dep: t0, sched: t0 })];
  const elvira = [icElvira({ tripId: 'elvira:862', label: 'IC', trainNumber: '862', dep: t0, sched: t0 })];
  const first = BkkLib.overlayRows(futar(), elvira, 12);
  const again = BkkLib.overlayRows(BkkLib.mergeRows(first, futar(), 12), elvira, 12);
  assert.ok(first.length === 1 && first[0].trainNumber === '862' && first[0].platform === '4'
    && again.length === 1 && again[0].trainNumber === '862' && again[0].platform === '4'
    && again[0].booking === true && again[0].tripId === 'BKK_8624_67');
});

test('suburban 4-digit trip ids take no 3-digit ELVIRA train in the same minute', () => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const t1 = t0 + 30 * 60;
  const merged = BkkLib.overlayRows(
    [
      icFutar({
        label: 'Z30', color: '#FFCD28', tripId: 'BKK_4541_108', trainNumber: '4541',
        head: 'Székesfehérvár', dep: t0, sched: t0,
      }),
      icFutar({
        label: 'S', color: '#4477aa', tripId: 'BKK_4561_108', trainNumber: '4561',
        head: 'Székesfehérvár', dep: t1, sched: t1,
      }),
    ],
    [
      icElvira({ tripId: 'elvira:454', label: 'IC', trainNumber: '454', platform: '2', dep: t0 + 60, sched: t0 + 60 }),
      icElvira({ tripId: 'elvira:456', label: 'IC', trainNumber: '456', platform: '2', dep: t1, sched: t1 }),
    ],
    12,
  );
  assert.ok(merged.length === 2
    && merged.every((r) => r.platform === '' && r.booking !== true)
    && merged[0].label === 'Z30' && merged[0].trainNumber === '4541'
    && merged[1].label === 'S' && merged[1].trainNumber === '4561');
});

test('BALATON 866 merges onto FUTAR BKK_8661_47', () => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = BkkLib.mergeRows(
    [{
      label: 'BALATON', tripId: 'elvira:2891044', trainNumber: '866',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    [{
      label: 'IC', tripId: 'BKK_8661_47', trainNumber: '',
      dep: dep, sched: dep, color: '#2E5EA8',
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].tripId === 'BKK_8661_47'
    && BkkLib.rowTrainNumber(merged[0]) === '866');
});

test('ELVIRA GPS is kept over FUTAR overlay', () => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = BkkLib.mergeRows(
    [{
      label: 'BALATON', tripId: 'elvira:2890602', trainNumber: '861',
      dep: dep, sched: dep, color: '#4477aa',
      lat: 47.2290993, lon: 18.6682205,
    }],
    [{
      label: 'IC', tripId: 'BKK_861_74', trainNumber: '861',
      dep: dep, sched: dep, color: '#2E5EA8',
      lat: 47.5, lon: 19.05,
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].tripId === 'BKK_861_74'
    && merged[0].lat === 47.2290993
    && merged[0].lon === 18.6682205
    && String(merged[0].color).replace('#', '').toUpperCase() === '2E5EA8');
});

test('ELVIRA GPS still wins if FUTAR was added first', () => {
  const dep = Math.floor(Date.now() / 1000) + 900;
  const merged = BkkLib.mergeRows(
    [{
      label: 'IC', tripId: 'BKK_861_74', trainNumber: '861',
      dep: dep, sched: dep, color: '#2E5EA8',
      lat: 47.5, lon: 19.05,
    }],
    [{
      label: 'BALATON', tripId: 'elvira:2890602', trainNumber: '861',
      dep: dep, sched: dep, color: '#4477aa',
      lat: 47.2290993, lon: 18.6682205,
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].label === 'BALATON'
    && merged[0].lat === 47.2290993
    && merged[0].lon === 18.6682205);
});

test('ELVIRA S30 takes FUTAR trip id by train number', () => {
  const dep = Math.floor(Date.now() / 1000) + 1800;
  const merged = BkkLib.mergeRows(
    [{
      label: 'S30', tripId: 'elvira:2889849', trainNumber: '4541',
      dep: dep, sched: dep, color: '#4477aa',
    }],
    [{
      label: 'S30', tripId: 'BKK_4541_108', trainNumber: '4541',
      dep: dep, sched: dep, color: '#00AFF0',
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].tripId === 'BKK_4541_108'
    && String(merged[0].color).toUpperCase().indexOf('00AFF0') >= 0);
});

test('GPS ELVIRA 9068 still takes FUTAR trip id', () => {
  const dep = Math.floor(Date.now() / 1000) + 1800;
  const merged = BkkLib.mergeRows(
    [{
      label: 'Sz', tripId: 'elvira:9068', trainNumber: '9068',
      dep: dep, sched: dep, lat: 47.464, lon: 19.021, color: '#4477aa',
    }],
    [{
      label: 'Sz', tripId: 'BKK_90681_106', trainNumber: '9068',
      dep: dep, sched: dep, color: '#2E5EA8',
    }],
    12,
  );
  assert.ok(merged.length === 1
    && merged[0].tripId === 'BKK_90681_106'
    && merged[0].lat === 47.464);
});

test('a branded suburban train in the same minute stays off another numbered train', () => {
  const t0 = Math.floor(Date.now() / 1000) + 1200;
  const host = (n, head) => ({
    tripId: 'motis:' + n, label: n, trainNumber: n, head, vehicle: 'rail', rawType: 'RAIL', platform: '', dep: t0, sched: t0,
  });
  const merged = BkkLib.overlayRows(
    [host('8738', 'Siófok')],
    [{ tripId: 'elvira:4711', label: 'G44', trainNumber: '4711', head: 'Budapest-Déli', vehicle: 'rail', rawType: 'RAIL', platform: '2', dep: t0, sched: t0 }],
    12,
  );
  /* Without a number of its own, the branded row is still the same departure. */
  const unnumbered = BkkLib.mergeRows(
    [{ tripId: 'gtfs:G44:1', label: 'G44', trainNumber: '', head: 'Siófok', vehicle: 'rail', rawType: 'RAIL', platform: '', dep: t0, sched: t0 }],
    [host('8738', 'Siófok')],
    12,
  );
  assert.ok(merged.length === 1 && merged[0].label === '8738' && merged[0].platform === ''
    && unnumbered.length === 1 && unnumbered[0].label === 'G44' && unnumbered[0].trainNumber === '8738');
});

/* Two FUTÁR trips with the same line in the same minute are two rows, each
   with its own trip id and headsign. */
test('two FUTÁR trips of one line in the same minute stay two rows with their own headsigns', () => {
  const at = Math.floor(Date.now() / 1000) + 600;
  const a = { tripId: 'BKK_A1', label: '7', head: 'Bosnyák tér', dep: at, sched: at, color: '#009EE3', vehicle: 'bus' };
  const b = { tripId: 'BKK_B2', label: '7', head: 'Albertfalva', dep: at, sched: at, color: '#009EE3', vehicle: 'bus' };
  const out = BkkLib.mergeRows([], [a, b], 12);
  const pairs = out.map((r) => r.tripId + ' ' + r.head).sort();
  ok('two FUTÁR trips of one line in the same minute stay two rows with their own headsigns',
    out.length === 2 && pairs[0] === 'BKK_A1 Bosnyák tér' && pairs[1] === 'BKK_B2 Albertfalva',
    JSON.stringify(pairs));
});

/* National pass (Országbérlet) on seat-reservation trains. */
test('ELVIRA overlay copies the national-pass flags onto the FUTÁR train, and asRow keeps them', () => {
  const dep = Math.floor(Date.now() / 1000) + 3600;
  const rail = (extra) => Object.assign({ vehicle: 'rail', rawType: 'RAIL', dep, sched: dep }, extra);
  const merged = BkkLib.overlayRows(
    [rail({ label: 'IC', trainNumber: '950', tripId: 'BKK_950_313' })],
    [rail({ label: 'GÖCSEJ', trainNumber: '950', booking: true, nationalPass: true, reservationFreeSection: true, tripId: 'elvira:950', destHit: false })],
    12,
  );
  ok('ELVIRA overlay copies the national-pass flags onto the FUTÁR train',
    merged.length === 1 && merged[0].booking === true && merged[0].nationalPass === true
    && merged[0].reservationFreeSection === true, JSON.stringify(merged[0]));
  const asRow = BkkLib.asRow(merged[0], 'Székesfehérvár', 'hu');
  ok('asRow keeps the national-pass flags', asRow.nationalPass === true && asRow.reservationFreeSection === true);
});

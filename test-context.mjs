/* The library's namespace (src/lib/core.js), straight from src/: every
   lib module registers its members once, and a name defined twice is a
   load error. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BkkLib, register } from './src/lib/core.js';
import './src/lib/names.js';
import './src/lib/futar.js';
import './src/lib/transitous.js';
import './src/lib/journey.js';
import './src/lib/geo.js';
import './src/lib/dashboard-key.js';
import './src/lib/gtfs.js';
import './src/lib/services.js';
import './src/lib/departures.js';
import './src/lib/vehicles.js';
import './src/lib/realtime.js';
import './src/lib/stop-history.js';
import './src/lib/rows.js';
import './src/lib/elvira-rows.js';
import './src/lib/board.js';
import './src/lib/journey-plan.js';
import './src/lib/ride-map.js';

test('every lib module loads beside the others: no member is registered twice', () => {
  assert.equal(typeof BkkLib.nextBoard, 'function');
  assert.equal(typeof BkkLib.mergeRows, 'function');
});

test('registering a name that is already there throws and changes nothing', () => {
  const keep = BkkLib.mergeRows;
  assert.throws(() => register({ zzGuardNew: 1, mergeRows: () => [] }), /BkkLib\.mergeRows is registered twice/);
  assert.equal(BkkLib.mergeRows, keep);
  assert.ok(!('zzGuardNew' in BkkLib));
  register({ zzGuardNew: 1 });
  try {
    assert.throws(() => register({ zzGuardNew: 2 }), /registered twice/);
    assert.equal(BkkLib.zzGuardNew, 1);
  } finally {
    delete BkkLib.zzGuardNew;
  }
});

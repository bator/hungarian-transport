import { BKKHopCardEditor } from './editor.js';
import { t } from './i18n.js';

export class BKKPlannerCardEditor extends BKKHopCardEditor {
  _mode() { return 'all'; }

  /* The planner's pair changes in the card itself: an "A → B" name baked
     in here would be wrong after the first pick there. Its title is its own. */
  _autoName() { return false; }

  setConfig(config) {
    super.setConfig(config);
    this._hideModeControls();
  }

  _hideModeControls() {
    const row = this._el('modeSwitches');
    if (row) row.style.display = 'none';
    const city = this._el('cityWrap');
    if (city) city.style.display = 'none';
    /* A picked pair is the planner's title: it has no room for line badges. */
    const hb = this._el('headerBadges');
    const hbRow = hb && hb.closest('label');
    if (hbRow) hbRow.style.display = 'none';
  }

  _syncMode() {
    super._syncMode();
    this._hideModeControls();
    const hint = this._el('modeHint');
    if (hint) hint.textContent = t(this._lang(), 'plannerPickAll');
    const lblStop = this._el('lblStop');
    if (lblStop) lblStop.textContent = t(this._lang(), 'plannerStep1');
    const lblDest = this._el('lblDest');
    if (lblDest) lblDest.textContent = t(this._lang(), 'plannerStep2');
  }
}

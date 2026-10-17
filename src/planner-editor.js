import { BKKHopCardEditor } from './editor.js';
import { t } from './i18n.js';

export class BKKPlannerCardEditor extends BKKHopCardEditor {
  _mode() { return 'all'; }

  setConfig(config) {
    super.setConfig(config);
    this._hideModeControls();
  }

  _hideModeControls() {
    const row = this._el('modeSwitches');
    if (row) row.style.display = 'none';
    const city = this._el('cityWrap');
    if (city) city.style.display = 'none';
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

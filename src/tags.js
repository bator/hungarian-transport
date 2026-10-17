import { BKK_PLANNER_TAG, BKK_PLANNER_TAG_ALIAS } from './config.js';

export const BKK_HOP_TAG = 'hungarian-transport-card';
export const BKK_HOP_EDITOR = 'hungarian-transport-card-editor';
export const BKK_HOP_TAG_ALIAS = 'bkk-stop-card-r3';
export const BKK_HOP_EDITOR_ALIAS = 'bkk-stop-card-r3-editor';
export const BKK_PLANNER_EDITOR = 'hungarian-transit-stop-card-plan-editor';
export const BKK_PLANNER_EDITOR_ALIAS = 'bkk-stop-card-plan-editor';
export const OWN_CARD_TYPES = [BKK_HOP_TAG, BKK_HOP_TAG_ALIAS, BKK_PLANNER_TAG, BKK_PLANNER_TAG_ALIAS];
/* Highest compact-index layout this card understands; see scripts/gtfs_compact.py. */
export const INDEX_SCHEMA = 27;

import { fixtureApi } from './fixtureApi';
import { liveApi } from './liveApi';
import type { ScramApi } from './types';

export const API_MODE: 'fixture' | 'live' = import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'fixture';

export const api: ScramApi = API_MODE === 'live' ? liveApi : fixtureApi;

/** States the screen keeps polling through until they settle. */
export const TRANSIENT = new Set(['PARSING', 'RULE_RETRIEVED', 'CLASSIFIED', 'CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'ROLLING_BACK']);

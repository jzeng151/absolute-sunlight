import { fixtureApi, type RedlineApi } from './fixtureApi';
import { liveApi } from './liveApi';

export const API_MODE: 'fixture' | 'live' = import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'fixture';
export const api: RedlineApi = API_MODE === 'live' ? liveApi : fixtureApi;

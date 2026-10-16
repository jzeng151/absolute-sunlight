// Live back end — REDLINE Build Spec, section 5. Every endpoint returns a RaceView.
import type { RedlineApi } from './fixtureApi';
import type { RaceView } from '../types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

async function call(method: 'GET' | 'POST', path: string, body?: unknown): Promise<RaceView> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined
  });
  if (!res.ok) throw new Error(`${method} ${path} failed: ${res.status}`);
  return (await res.json()) as RaceView;
}

export const liveApi: RedlineApi = {
  load: (id) => call('POST', `/api/scenarios/${id}/load`),
  advance: () => call('POST', '/api/advance'),
  compare: () => call('POST', '/api/compare'),
  decide: async (choice, rationale) => {
    await call('POST', '/api/decide', { choice, rationale });
    return call('POST', '/api/outcome');
  },
  debrief: () => call('GET', '/api/debrief'),
  reset: () => call('POST', '/api/reset')
};

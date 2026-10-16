// Live back end — SCRAM Build Spec, section 5. Every endpoint returns a RunView.
import type { ScramApi } from './types';
import type { BaselineRow, RunView } from '../types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined
  });
  if (!res.ok) throw new Error(`${method} ${path} failed: ${res.status}`);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const liveApi: ScramApi = {
  createRun: (caseId) => call<RunView>('POST', '/api/runs', { case_id: caseId }),
  approve: (run, actorId) =>
    call<RunView>('POST', `/api/runs/${run.run_id}/approve`, { actor_id: actorId, action_hash: run.action?.action_hash }),
  reject: (run) => call<RunView>('POST', `/api/runs/${run.run_id}/reject`),
  getRun: (runId) => call<RunView>('GET', `/api/runs/${runId}`),
  setFault: (enabled) => call<void>('POST', '/api/fault', { enabled }),
  reset: () => call<void>('POST', '/api/reset'),
  baseline: () => call<BaselineRow[]>('GET', '/api/evals/baseline')
};

// Live back end — SCRAM Build Spec, section 5. Every endpoint returns a RunView.
import type { ScramApi } from './types';
import type { BaselineRow, RunView } from '../types';
import { ApiError } from './errors';

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined
    });
  } catch {
    // No answer: the server may or may not have processed this request.
    throw new ApiError('network', `${method} ${path}: no response`);
  }
  if (!res.ok) {
    let code: string | null = null;
    try {
      const j = await res.json();
      code = j?.reason_code ?? j?.detail?.reason_code ?? (typeof j?.detail === 'string' ? j.detail : null);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError('refused', `${method} ${path} refused: ${res.status}`, res.status, code);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const liveApi: ScramApi = {
  createRun: (caseId) => call<RunView>('POST', '/api/runs', { case_id: caseId }),
  approve: (run, actorId, idempotencyKey) =>
    call<RunView>(
      'POST',
      `/api/runs/${run.run_id}/approve`,
      { actor_id: actorId, action_hash: run.action?.action_hash, idempotency_key: idempotencyKey },
      idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}
    ),
  reject: (run) => call<RunView>('POST', `/api/runs/${run.run_id}/reject`),
  getRun: (runId) => call<RunView>('GET', `/api/runs/${runId}`),
  setFault: (enabled) => call<void>('POST', '/api/fault', { enabled }),
  reset: () => call<void>('POST', '/api/reset'),
  baseline: () => call<BaselineRow[]>('GET', '/api/evals/baseline')
};

import type { BaselineRow, CaseId, RunView } from '../types';

/** One interface, two implementations: fixtures (now) and the FastAPI back end (later).
 *  Errors are thrown as ApiError (see ./errors): 'refused' when the server said no,
 *  'network' when no answer arrived and the outcome is unknown. */
export interface ScramApi {
  createRun(caseId: CaseId): Promise<RunView>;
  /** idempotencyKey: the same key for retries of the same approval, so a retry can never execute twice. */
  approve(run: RunView, actorId: string, idempotencyKey?: string): Promise<RunView>;
  reject(run: RunView): Promise<RunView>;
  getRun(runId: string): Promise<RunView>;
  setFault(enabled: boolean): Promise<void>;
  reset(): Promise<void>;
  baseline(): Promise<BaselineRow[]>;
}

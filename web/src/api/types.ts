import type { BaselineRow, CaseId, RunView } from '../types';

/** One interface, two implementations: fixtures (now) and the FastAPI back end (later). */
export interface ScramApi {
  createRun(caseId: CaseId): Promise<RunView>;
  /** The operator's own words, parsed by the model and then governed like any case. */
  askRun(text: string): Promise<RunView>;
  approve(run: RunView, actorId: string): Promise<RunView>;
  reject(run: RunView): Promise<RunView>;
  getRun(runId: string): Promise<RunView>;
  setFault(enabled: boolean): Promise<void>;
  reset(): Promise<void>;
  baseline(): Promise<BaselineRow[]>;
}

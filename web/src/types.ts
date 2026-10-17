// Data contracts — SCRAM Build Spec, section 4.
// The back end returns these exact shapes. Change them only with both teams agreeing.

export type PolicyState = 'GREEN' | 'YELLOW' | 'RED';

export type MachineState =
  | 'IDLE'
  | 'PARSING'
  | 'RULE_RETRIEVED'
  | 'CLASSIFIED'
  | 'AWAITING_APPROVAL'
  | 'CHECKPOINTING'
  | 'EXECUTING'
  | 'VERIFYING'
  | 'ROLLING_BACK'
  | 'COMPLETED'
  | 'BLOCKED'
  | 'RESTORED'
  | 'LOCKED'
  | 'AUDITED';

export type ReasonCode =
  | 'READ_ONLY_ALLOWED'
  | 'HUMAN_APPROVAL_REQUIRED'
  | 'BOUNDARY_EXCEEDED'
  | 'SAFETY_BYPASS_FORBIDDEN'
  | 'NO_AUTHORITY'
  | 'PARSE_UNCERTAIN'
  | 'CONTROL_UNAVAILABLE'
  | 'APPROVAL_REJECTED'
  | 'APPROVAL_EXPIRED'
  | 'STATE_CHANGED'
  | 'HASH_MISMATCH'
  | 'APPROVAL_ALREADY_USED'
  | 'POSTCONDITION_FAILED'
  | 'ROLLBACK_FAILED';

export interface TypedAction {
  run_id: string;
  actor_id: string;
  tool: string;
  target: string;
  normalized_parameters: Record<string, number | string>;
  intent: string;
  expected_postcondition: Record<string, number | string>;
  confidence: number;
  action_hash: string;
}

export interface Rule {
  rule_id: string;
  title: string;
  text: string;
  default_state: PolicyState;
  policy_version: string;
}

export interface PolicyDecision {
  run_id: string;
  policy_version: string;
  rule_ids: string[];
  state: PolicyState;
  reason_code: ReasonCode;
  reversible: boolean;
  approval_required: boolean;
}

export interface ApprovalArtifact {
  approval_id: string;
  actor_id: string;
  action_hash: string;
  issued_at: string;
  expires_at: string;
  used: boolean;
  decision: 'approve' | 'reject';
}

export interface Checkpoint {
  checkpoint_id: string;
  run_id: string;
  before_state: { output_percent: number };
  checksum: string;
}

export interface ExecutionResult {
  tool: string;
  target: string;
  parameters: Record<string, number | string>;
  status: 'success' | 'error';
  resulting_state: { output_percent: number };
  executor_run_id: string;
  fault_injected: boolean;
}

export interface VerificationResult {
  expected_postcondition: Record<string, number | string>;
  actual_state: { output_percent: number };
  pass: boolean;
  reason_code: ReasonCode | null;
  diff: string;
}

export interface RollbackResult {
  triggered: boolean;
  checkpoint_id: string;
  status: 'running' | 'restored' | 'failed';
  restored_checksum: string;
  verified: boolean;
}

export interface TraceSpan {
  name: string;
  status: 'ok' | 'error' | 'skipped' | 'running' | 'pending';
  detail: string;
}

export interface Evaluator {
  name: string;
  description: string;
  pass: boolean;
  /** Live backend evaluations distinguish inapplicable and missing evidence. */
  label?: 'PASS' | 'FAIL' | 'N/A' | 'INCOMPLETE';
  score?: number | null;
  explanation?: string;
}

/** The single object the screen renders. GET /api/runs/{id} returns this. */
export interface RunView {
  run_id: string;
  /** 'OP' when the operator typed the request instead of choosing a case. */
  scenario_id: CaseId | 'OP';
  machine_state: MachineState;
  request_text: string;
  current_output_percent: number;
  action: TypedAction | null;
  rule: Rule | null;
  decision: PolicyDecision | null;
  /** When YELLOW is waiting: the approval window closes at this time. */
  approval_expires_at: string | null;
  approval: ApprovalArtifact | null;
  checkpoint: Checkpoint | null;
  execution: ExecutionResult | null;
  verification: VerificationResult | null;
  rollback: RollbackResult | null;
  /** Extra codes shown on a blocked replay, e.g. HASH_MISMATCH + APPROVAL_ALREADY_USED */
  block_reasons: ReasonCode[];
  /** Hash the operator approved earlier (replay case only) */
  approved_hash: string | null;
  voice_line: VoiceLine | null;
  trace_spans: TraceSpan[];
  evaluators: Evaluator[];
}

export type CaseId = 'C1' | 'C2' | 'C3' | 'C4';

export interface VoiceLine {
  text: string;
  /** File under /public/audio, e.g. "red_blocked.mp3". Plays if present. */
  file: string;
}

export interface BaselineRow {
  case_label: string;
  baseline: string;
  scram: string;
}

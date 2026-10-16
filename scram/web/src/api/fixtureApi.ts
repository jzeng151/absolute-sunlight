// Fixture back end. Behaves like the FastAPI service will, using /src/fixtures.
// Nothing here decides policy from model output: the policy comes from cases.json,
// exactly as the real deterministic policy engine will.

import casesFile from '../fixtures/cases.json';
import rulebook from '../fixtures/rulebook.json';
import baselineRows from '../fixtures/baseline.json';
import { actionHash } from './hash';
import { ApiError } from './errors';
import type { ScramApi } from './types';
import type {
  BaselineRow,
  CaseId,
  Evaluator,
  MachineState,
  PolicyState,
  ReasonCode,
  Rule,
  RunView,
  TraceSpan,
  TypedAction,
  VoiceLine
} from '../types';

/** Pace of execution steps, so an audience can read each one. */
export const STEP_MS = 900;

const REACTOR = casesFile.reactor;
const ACTOR = 'operator_nw';

type CaseDef = (typeof casesFile.cases)[number];

interface Internal {
  base: RunView;
  startedAt: number | null;
  fault: boolean;
}

const VOICE: Record<string, VoiceLine> = {
  red: { text: 'SCRAM activated. Action blocked. Boundary exceeded.', file: 'red_blocked.mp3' },
  yellow: { text: 'Approval requested. Increase output two points, to seventy-four. Awaiting operator.', file: 'yellow_c2.mp3' },
  approved: { text: 'Approved. Checkpoint created. Executing.', file: 'approved.mp3' },
  restored: { text: 'Verification failed. Safe state restored.', file: 'restored.mp3' },
  replay: { text: 'Approval invalid. Action changed. Blocked.', file: 'replay_blocked.mp3' }
};

const EVALUATORS: Evaluator[] = [
  { name: 'Unauthorized execution', description: 'RED or invalid approval never reaches a tool.', pass: true },
  { name: 'Approval integrity', description: 'Matching, unexpired, unused hash.', pass: true },
  { name: 'Rollback verification', description: 'Failed postcondition ends restored.', pass: true },
  { name: 'Trajectory order', description: 'No step skipped or out of sequence.', pass: true }
];

let runs = new Map<string, Internal>();
let fault = true;
let counter = 417;
let usedApprovalHash: string | null = null;
/** Idempotency: a retried approval with the same key returns the original result instead of executing again. */
let approvalKeys = new Map<string, string>();

/** Fixture-only demo levers, so the failure paths can be shown without a back end. */
export const fixtureLevers = {
  /** The server processes the next approve call, but its reply is lost on the way back. */
  loseNextResponse: false
};

const delay = (ms = 120) => new Promise((r) => setTimeout(r, ms));
const findCase = (id: CaseId) => casesFile.cases.find((c) => c.id === id) as CaseDef;
const findRule = (id: string) => rulebook.find((r) => r.rule_id === id) as Rule;

async function typedAction(c: CaseDef, runId: string): Promise<TypedAction> {
  const parts = {
    tool: c.tool,
    target: REACTOR.target,
    normalized_parameters: c.normalized_parameters as unknown as Record<string, number | string>,
    expected_postcondition: c.expected_postcondition as unknown as Record<string, number | string>
  };
  return { run_id: runId, actor_id: ACTOR, intent: c.intent, confidence: 0.97, action_hash: await actionHash(parts), ...parts };
}

const span = (name: string, status: TraceSpan['status'], detail = ''): TraceSpan => ({ name, status, detail });

function frontSpans(rule: string, policy: PolicyState): TraceSpan[] {
  return [
    span('request', 'ok'),
    span('normalize', 'ok', 'Gemini'),
    span(`rule · ${rule}`, 'ok'),
    span(`policy · ${policy}`, policy === 'RED' ? 'error' : 'ok')
  ];
}

function emptyView(runId: string, c: CaseDef): RunView {
  return {
    run_id: runId,
    scenario_id: c.id as CaseId,
    machine_state: 'IDLE',
    request_text: c.request_text,
    current_output_percent: REACTOR.output_percent,
    action: null,
    rule: null,
    decision: null,
    approval_expires_at: null,
    approval: null,
    checkpoint: null,
    execution: null,
    verification: null,
    rollback: null,
    block_reasons: [],
    approved_hash: null,
    voice_line: null,
    trace_spans: [],
    evaluators: EVALUATORS
  };
}

/** Advance an approved run through checkpoint → execute → verify → (rollback) by elapsed time. */
function project(run: Internal): RunView {
  const v: RunView = structuredClone(run.base);
  if (run.startedAt === null) return v;

  const k = Math.floor((Date.now() - run.startedAt) / STEP_MS);
  const f = run.fault;
  const expected = Number(v.action?.expected_postcondition.output_percent ?? REACTOR.output_percent);
  const actual = f ? 92 : expected;
  const steps: MachineState[] = f
    ? ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'ROLLING_BACK', 'RESTORED']
    : ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'COMPLETED'];
  const state = steps[Math.min(k, steps.length - 1)];
  const at = steps.indexOf(state);

  v.machine_state = state;
  v.voice_line = state === 'RESTORED' ? VOICE.restored : state === 'COMPLETED' ? null : VOICE.approved;
  v.checkpoint = {
    checkpoint_id: 'ckpt_0' + v.run_id.slice(-3),
    run_id: v.run_id,
    before_state: { output_percent: REACTOR.output_percent },
    checksum: REACTOR.checksum
  };

  if (at >= 2) {
    v.execution = {
      tool: v.action!.tool,
      target: v.action!.target,
      parameters: v.action!.normalized_parameters,
      status: 'success',
      resulting_state: { output_percent: actual },
      executor_run_id: 'sbx_7c1',
      fault_injected: f
    };
    v.current_output_percent = actual;
  }
  if (at >= 3) {
    v.verification = {
      expected_postcondition: { output_percent: expected },
      actual_state: { output_percent: actual },
      pass: !f,
      reason_code: f ? 'POSTCONDITION_FAILED' : null,
      diff: f ? `+${actual - expected}` : '0'
    };
  }
  if (f && at >= 3) {
    v.rollback = {
      triggered: true,
      checkpoint_id: v.checkpoint.checkpoint_id,
      status: state === 'RESTORED' ? 'restored' : 'running',
      restored_checksum: state === 'RESTORED' ? REACTOR.checksum : '',
      verified: state === 'RESTORED'
    };
  }
  if (state === 'RESTORED') v.current_output_percent = REACTOR.output_percent;

  const s = (i: number, okDetail: string, failed = false): TraceSpan =>
    span(['checkpoint', 'execute', 'verify'][i], at > i || (i === 2 && at >= 3) ? (failed ? 'error' : 'ok') : at === i ? 'running' : 'pending', okDetail);
  v.trace_spans = [
    ...frontSpans(v.rule!.rule_id, 'YELLOW'),
    span('approval', 'ok', v.approval?.action_hash ?? ''),
    s(0, v.checkpoint.checkpoint_id),
    s(1, 'Daytona'),
    s(2, at >= 3 ? (f ? `${actual} ≠ ${expected}` : 'pass') : '', f)
  ];
  if (f) v.trace_spans.push(span('rollback', state === 'RESTORED' ? 'ok' : at >= 3 ? 'running' : 'pending', state === 'RESTORED' ? 'verified' : ''));
  if (state === 'RESTORED' || state === 'COMPLETED') v.trace_spans.push(span('audit', 'ok', 'recorded'));
  return v;
}

export const fixtureApi: ScramApi = {
  async createRun(caseId) {
    await delay();
    const c = findCase(caseId);
    const runId = `run_0${++counter}`;
    const v = emptyView(runId, c);
    v.action = await typedAction(c, runId);
    v.rule = findRule(c.rule_id);
    v.decision = {
      run_id: runId,
      policy_version: v.rule.policy_version,
      rule_ids: [c.rule_id],
      state: c.policy as PolicyState,
      reason_code: c.reason_code as ReasonCode,
      reversible: c.intent === 'write',
      approval_required: c.policy === 'YELLOW'
    };

    if (caseId === 'C1') {
      v.machine_state = 'COMPLETED';
      v.trace_spans = [...frontSpans(c.rule_id, 'GREEN'), span('read', 'ok', 'no write'), span('audit', 'ok', 'recorded')];
    } else if (caseId === 'C2') {
      v.machine_state = 'AWAITING_APPROVAL';
      v.approval_expires_at = new Date(Date.now() + 5 * 60_000).toISOString();
      v.voice_line = VOICE.yellow;
      v.trace_spans = [...frontSpans(c.rule_id, 'YELLOW'), span('approval', 'running', 'waiting')];
    } else if (caseId === 'C3') {
      v.machine_state = 'BLOCKED';
      v.block_reasons = ['BOUNDARY_EXCEEDED'];
      v.voice_line = VOICE.red;
      v.trace_spans = [...frontSpans(c.rule_id, 'RED'), span('execute', 'skipped', 'never'), span('audit', 'ok', 'recorded')];
    } else {
      const c2 = await typedAction(findCase('C2'), runId);
      v.machine_state = 'BLOCKED';
      v.approved_hash = c2.action_hash;
      v.block_reasons = usedApprovalHash === c2.action_hash ? ['HASH_MISMATCH', 'APPROVAL_ALREADY_USED'] : ['HASH_MISMATCH'];
      v.voice_line = VOICE.replay;
      v.trace_spans = [
        ...frontSpans(c.rule_id, 'YELLOW'),
        span('approval check', 'error', 'hash mismatch'),
        span('execute', 'skipped', 'never'),
        span('audit', 'ok', 'recorded')
      ];
    }
    runs.set(runId, { base: v, startedAt: null, fault });
    return structuredClone(v);
  },

  async approve(run, actorId, idempotencyKey) {
    await delay();
    const r = runs.get(run.run_id);
    if (!r) throw new ApiError('refused', 'Unknown run', 404, 'RUN_NOT_FOUND');
    if (idempotencyKey && approvalKeys.get(idempotencyKey) === run.run_id) return project(r);
    if (r.base.machine_state !== 'AWAITING_APPROVAL') throw new ApiError('refused', 'Run is not awaiting approval', 409, 'NOT_AWAITING');
    if (r.base.approval_expires_at && Date.now() >= new Date(r.base.approval_expires_at).getTime())
      throw new ApiError('refused', 'Approval window expired', 409, 'APPROVAL_EXPIRED');
    if (run.action?.action_hash !== r.base.action!.action_hash) throw new ApiError('refused', 'Hash mismatch', 409, 'HASH_MISMATCH');
    const hash = r.base.action!.action_hash;
    r.base.approval = {
      approval_id: 'apv_0' + run.run_id.slice(-3),
      actor_id: actorId,
      action_hash: hash,
      issued_at: new Date().toISOString(),
      expires_at: r.base.approval_expires_at!,
      used: true,
      decision: 'approve'
    };
    usedApprovalHash = hash;
    if (idempotencyKey) approvalKeys.set(idempotencyKey, run.run_id);
    r.fault = fault; // frozen for this run
    r.startedAt = Date.now();
    if (fixtureLevers.loseNextResponse) {
      fixtureLevers.loseNextResponse = false;
      throw new ApiError('network', 'approve: reply lost');
    }
    return project(r);
  },

  async reject(run) {
    await delay();
    const r = runs.get(run.run_id);
    if (!r) throw new ApiError('refused', 'Unknown run', 404, 'RUN_NOT_FOUND');
    if (r.base.machine_state !== 'AWAITING_APPROVAL') throw new ApiError('refused', 'Run is not awaiting approval', 409, 'NOT_AWAITING');
    r.base.machine_state = 'BLOCKED';
    r.base.block_reasons = ['APPROVAL_REJECTED'];
    r.base.voice_line = null;
    r.base.approval = {
      approval_id: 'apv_0' + run.run_id.slice(-3),
      actor_id: ACTOR,
      action_hash: r.base.action!.action_hash,
      issued_at: new Date().toISOString(),
      expires_at: r.base.approval_expires_at!,
      used: true,
      decision: 'reject'
    };
    r.base.trace_spans = [...frontSpans(r.base.rule!.rule_id, 'YELLOW'), span('approval', 'error', 'rejected'), span('audit', 'ok', 'recorded')];
    return structuredClone(r.base);
  },

  async getRun(runId) {
    const r = runs.get(runId);
    if (!r) throw new ApiError('refused', 'Unknown run', 404, 'RUN_NOT_FOUND');
    return project(r);
  },

  async setFault(enabled) {
    await delay();
    fault = enabled;
  },

  async reset() {
    runs = new Map();
    fault = true;
    counter = 417;
    usedApprovalHash = null;
    approvalKeys = new Map();
    await delay();
  },

  async baseline(): Promise<BaselineRow[]> {
    await delay();
    return baselineRows;
  }
};

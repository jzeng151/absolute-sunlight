// Fixture back end with one shared reactor and serialized write transactions.

import casesFile from '../fixtures/cases.json';
import rulebook from '../fixtures/rulebook.json';
import baselineRows from '../fixtures/baseline.json';
import { actionHash, canonicalJson } from './hash';
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
  beforeOutput: number;
}

const VOICE: Record<string, VoiceLine> = {
  red: { text: 'SCRAM activated. Action blocked. Boundary exceeded.', file: 'red_blocked.mp3' },
  yellow: { text: 'Approval requested. Increase output two percent, to seventy-four. Awaiting operator.', file: 'yellow_c2.mp3' },
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
let currentOutput = REACTOR.output_percent;
let verifiedOutput = currentOutput;
let activeRun: string | null = null;
let queuedRuns: string[] = [];
let advancing = Promise.resolve();

const delay = (ms = 120) => new Promise((r) => setTimeout(r, ms));
const findCase = (id: CaseId) => casesFile.cases.find((c) => c.id === id) as CaseDef;
const findRule = (id: string) => rulebook.find((r) => r.rule_id === id) as Rule;

async function typedAction(c: CaseDef, runId: string): Promise<TypedAction> {
  const parts = {
    tool: c.tool,
    target: REACTOR.target,
    normalized_parameters: c.normalized_parameters as unknown as Record<string, number | string>,
    expected_postcondition: c.tool === 'adjust_output'
      ? { output_percent: verifiedOutput + Number(c.normalized_parameters.delta_percent) }
      : c.expected_postcondition as unknown as Record<string, number | string>
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
    current_output_percent: currentOutput,
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
  v.current_output_percent = currentOutput;
  if (run.startedAt === null || activeRun !== v.run_id) return v;

  const k = Math.floor((Date.now() - run.startedAt) / STEP_MS);
  const f = run.fault;
  const expected = Number(v.action?.expected_postcondition.output_percent ?? REACTOR.output_percent);
  const actual = run.beforeOutput + (f ? 20 : Number(v.action!.normalized_parameters.delta_percent));
  const steps: MachineState[] = f
    ? ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'ROLLING_BACK', 'RESTORED']
    : ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'COMPLETED'];
  const state = steps[Math.min(k, steps.length - 1)];
  const at = steps.indexOf(state);

  v.machine_state = state;
  v.voice_line = state === 'RESTORED' ? VOICE.restored : state === 'COMPLETED' ? null : VOICE.approved;

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
    currentOutput = actual;
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
      checkpoint_id: v.checkpoint!.checkpoint_id,
      status: state === 'RESTORED' ? 'restored' : 'running',
      restored_checksum: state === 'RESTORED' ? v.checkpoint!.checksum : '',
      verified: state === 'RESTORED'
    };
  }
  if (state === 'RESTORED') currentOutput = run.beforeOutput;
  v.current_output_percent = currentOutput;

  const s = (i: number, okDetail: string, failed = false): TraceSpan =>
    span(['checkpoint', 'execute', 'verify'][i], at > i || (i === 2 && at >= 3) ? (failed ? 'error' : 'ok') : at === i ? 'running' : 'pending', okDetail);
  v.trace_spans = [
    ...frontSpans(v.rule!.rule_id, 'YELLOW'),
    span('approval', 'ok', v.approval?.action_hash ?? ''),
    s(0, v.checkpoint!.checkpoint_id),
    s(1, 'Daytona'),
    s(2, at >= 3 ? (f ? `${actual} ≠ ${expected}` : 'pass') : '', f)
  ];
  if (f) v.trace_spans.push(span('rollback', state === 'RESTORED' ? 'ok' : at >= 3 ? 'running' : 'pending', state === 'RESTORED' ? 'verified' : ''));
  if (state === 'RESTORED' || state === 'COMPLETED') {
    verifiedOutput = currentOutput;
    v.trace_spans.push(span('audit', 'ok', 'recorded'));
    run.base = structuredClone(v);
    run.startedAt = null;
    activeRun = null;
  }
  return v;
}

function block(run: Internal, reason: ReasonCode) {
  run.base.machine_state = 'BLOCKED';
  run.base.block_reasons = [reason];
  run.base.voice_line = null;
  run.base.trace_spans.push(span('execute', 'skipped', 'never'), span('audit', 'ok', 'recorded'));
}

// Polling is a clock for the simulation. Every endpoint advances it before
// reading state, including when a caller starts a new run without polling.
function advanceRuns(): Promise<void> {
  const next = advancing.then(async () => {
    if (activeRun) project(runs.get(activeRun)!);
    while (!activeRun && queuedRuns.length) {
      const id = queuedRuns.shift()!;
      const run = runs.get(id)!;
      const action = run.base.action!;
      if (Date.now() >= Date.parse(run.base.approval_expires_at!)) {
        block(run, 'APPROVAL_EXPIRED');
        continue;
      }
      if (currentOutput + Number(action.normalized_parameters.delta_percent) !== action.expected_postcondition.output_percent) {
        block(run, 'STATE_CHANGED');
        continue;
      }
      activeRun = id;
      run.beforeOutput = currentOutput;
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(
        canonicalJson({ target: REACTOR.target, output_percent: currentOutput })));
      const checksum = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 6);
      run.base.checkpoint = {
        checkpoint_id: `ckpt_${id}`, run_id: id,
        before_state: { output_percent: currentOutput }, checksum
      };
      run.startedAt = Date.now();
    }
  });
  advancing = next.catch(() => undefined);
  return next;
}

export const fixtureApi: ScramApi = {
  async createRun(caseId) {
    await delay();
    await advanceRuns();
    const c = findCase(caseId);
    const runId = `run_0${++counter}`;
    const v = emptyView(runId, c);
    v.action = await typedAction(c, runId);
    const expected = Number(v.action.expected_postcondition.output_percent);
    const beyondBand = c.tool === 'adjust_output' && (expected > REACTOR.band_max || expected < REACTOR.band_min);
    const policy: PolicyState = beyondBand ? 'RED' : c.intent === 'read' ? 'GREEN' : 'YELLOW';
    v.rule = findRule(beyondBand ? 'NC-04' : c.rule_id);
    v.decision = {
      run_id: runId,
      policy_version: v.rule.policy_version,
      rule_ids: [v.rule.rule_id],
      state: policy,
      reason_code: beyondBand ? 'BOUNDARY_EXCEEDED' : policy === 'GREEN' ? 'READ_ONLY_ALLOWED' : 'HUMAN_APPROVAL_REQUIRED',
      reversible: c.intent === 'write',
      approval_required: policy === 'YELLOW'
    };

    if (caseId === 'C1') {
      v.machine_state = 'COMPLETED';
      v.trace_spans = [...frontSpans(c.rule_id, 'GREEN'), span('read', 'ok', 'no write'), span('audit', 'ok', 'recorded')];
    } else if (policy === 'RED') {
      v.machine_state = 'BLOCKED';
      v.block_reasons = ['BOUNDARY_EXCEEDED'];
      v.voice_line = VOICE.red;
      v.trace_spans = [...frontSpans(v.rule.rule_id, 'RED'), span('execute', 'skipped', 'never'), span('audit', 'ok', 'recorded')];
    } else if (caseId === 'C2') {
      v.machine_state = 'AWAITING_APPROVAL';
      v.approval_expires_at = new Date(Date.now() + 5 * 60_000).toISOString();
      v.voice_line = expected === 74 ? VOICE.yellow : null;
      v.trace_spans = [...frontSpans(c.rule_id, 'YELLOW'), span('approval', 'running', 'waiting')];
    } else {
      const c2 = await typedAction(findCase('C2'), runId);
      v.machine_state = 'BLOCKED';
      v.approved_hash = usedApprovalHash ?? c2.action_hash;
      v.block_reasons = usedApprovalHash ? ['HASH_MISMATCH', 'APPROVAL_ALREADY_USED'] : ['HASH_MISMATCH'];
      v.voice_line = VOICE.replay;
      v.trace_spans = [
        ...frontSpans(c.rule_id, 'YELLOW'),
        span('approval check', 'error', 'hash mismatch'),
        span('execute', 'skipped', 'never'),
        span('audit', 'ok', 'recorded')
      ];
    }
    runs.set(runId, { base: v, startedAt: null, fault, beforeOutput: currentOutput });
    return structuredClone(v);
  },

  async approve(run, actorId) {
    await delay();
    await advanceRuns();
    const r = runs.get(run.run_id);
    if (!r || r.base.machine_state !== 'AWAITING_APPROVAL') throw new Error('Run is not awaiting approval');
    const hash = r.base.action!.action_hash;
    if (run.action?.action_hash !== hash || Date.now() >= Date.parse(r.base.approval_expires_at!)) {
      block(r, run.action?.action_hash !== hash ? 'HASH_MISMATCH' : 'APPROVAL_EXPIRED');
      return project(r);
    }
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
    r.fault = fault;
    r.base.machine_state = 'CHECKPOINTING';
    queuedRuns.push(run.run_id);
    await advanceRuns();
    return project(r);
  },

  async reject(run) {
    await delay();
    await advanceRuns();
    const r = runs.get(run.run_id);
    if (!r || r.base.machine_state !== 'AWAITING_APPROVAL') throw new Error('Run is not awaiting approval');
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
    return project(r);
  },

  async getRun(runId) {
    await advanceRuns();
    const r = runs.get(runId);
    if (!r) throw new Error('Unknown run');
    return project(r);
  },

  async setFault(enabled) {
    fault = enabled;
  },

  async reset() {
    await advancing;
    runs = new Map();
    activeRun = null;
    queuedRuns = [];
    currentOutput = REACTOR.output_percent;
    verifiedOutput = currentOutput;
    fault = true;
    usedApprovalHash = null;
  },

  async baseline(): Promise<BaselineRow[]> {
    return baselineRows;
  }
};

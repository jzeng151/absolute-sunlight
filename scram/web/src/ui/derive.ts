// Turns a RunView into what each zone shows. Pure functions: no state, no fetching.
//
// Design-audit rule (item 2): every "done" label needs the evidence object behind it.
// Policy decision, execution phase, final outcome, audit evidence and connection state are
// separate concepts and are derived separately here, never inferred from one another.
import type { RunView, TraceSpan } from '../types';
import casesFile from '../fixtures/cases.json';

export const C = {
  green: 'var(--scram-green)',
  yellow: 'var(--scram-yellow)',
  red: 'var(--scram-red)',
  ink: 'var(--scram-ink)',
  muted: 'var(--scram-ink-muted)'
} as const;

export const REACTOR = casesFile.reactor;

export type Mode = 'idle' | 'green' | 'red' | 'yellow' | 'exec' | 'restored' | 'completed' | 'rejected' | 'replay' | 'locked';

const AFTER_CHECKPOINT = ['EXECUTING', 'VERIFYING', 'ROLLING_BACK', 'RESTORED', 'COMPLETED', 'LOCKED', 'AUDITED'];

export function modeOf(run: RunView | null): Mode {
  if (!run) return 'idle';
  switch (run.machine_state) {
    case 'AWAITING_APPROVAL':
      return 'yellow';
    case 'CHECKPOINTING':
    case 'EXECUTING':
    case 'VERIFYING':
    case 'ROLLING_BACK':
      return 'exec';
    case 'RESTORED':
      // "Restored" is an outcome only once the restore is verified.
      return run.rollback?.verified ? 'restored' : 'exec';
    case 'LOCKED':
      return 'locked';
    case 'COMPLETED':
    case 'AUDITED':
      // AUDITED is evidence about the record, not an outcome: keep the real outcome visible.
      if (run.rollback?.verified) return 'restored';
      if (run.decision?.state === 'RED') return 'red';
      if (run.block_reasons.includes('APPROVAL_REJECTED')) return 'rejected';
      if (run.block_reasons.includes('HASH_MISMATCH') || run.block_reasons.includes('APPROVAL_ALREADY_USED')) return 'replay';
      return run.decision?.state === 'GREEN' ? 'green' : 'completed';
    case 'BLOCKED':
      if (run.block_reasons.includes('APPROVAL_REJECTED')) return 'rejected';
      if (run.block_reasons.includes('HASH_MISMATCH') || run.block_reasons.includes('APPROVAL_ALREADY_USED')) return 'replay';
      return 'red';
    default:
      return 'idle';
  }
}

export function stateColor(mode: Mode, run: RunView | null): string {
  if (mode === 'green' || mode === 'restored' || mode === 'completed') return C.green;
  if (mode === 'yellow') return C.yellow;
  if (mode === 'red' || mode === 'replay' || mode === 'rejected' || mode === 'locked') return C.red;
  if (mode === 'exec') return run?.verification && !run.verification.pass ? C.red : C.yellow;
  return C.muted;
}

export function policyColor(run: RunView | null): string {
  const s = run?.decision?.state;
  return s === 'GREEN' ? C.green : s === 'YELLOW' ? C.yellow : s === 'RED' ? C.red : C.ink;
}

/** Evidence that the audit record exists: an 'audit' span the server marked ok. */
export function auditPersisted(run: RunView | null): boolean {
  return !!run?.trace_spans.some((s) => s.name === 'audit' && s.status === 'ok');
}

/** True when the server has confirmed no tool call happened in this run. */
export function noExecution(run: RunView): boolean {
  return run.execution === null;
}

// ─────────────────────────── execution ladder ───────────────────────────

export interface LadderRow {
  title: string;
  detail: string;
  mark: string;
  color: string;
  running: boolean;
  state: 'done' | 'running' | 'fail' | 'pending' | 'scram';
}

function row(title: string, detail: string, st: LadderRow['state']): LadderRow {
  const map = { done: ['✓', C.green], running: ['◐', C.yellow], fail: ['✕', C.red], pending: ['○', C.muted], scram: ['◐', C.red] } as const;
  return { title, detail, mark: map[st][0], color: map[st][1], running: st === 'running' || st === 'scram', state: st };
}

/** CHECKPOINT → EXECUTE → VERIFY → (RESTORE). A step reads "done" only when its evidence exists. */
export function ladder(run: RunView): LadderRow[] {
  const ms = run.machine_state;
  const v = run.verification;
  const ex = run.execution;
  const ck = run.checkpoint;
  const rb = run.rollback;
  const expected = run.action?.expected_postcondition.output_percent;
  const failed = !!v && !v.pass;
  const rows: LadderRow[] = [];

  if (ck && AFTER_CHECKPOINT.includes(ms)) rows.push(row('Checkpoint created', `${ck.checkpoint_id} · ${ck.before_state.output_percent} % · checksum ${ck.checksum}`, 'done'));
  else if (ms === 'CHECKPOINTING') rows.push(row('Creating checkpoint', '', 'running'));
  else rows.push(row('Create checkpoint — pending', '', 'pending'));

  if (ex) rows.push(row(ex.status === 'success' ? 'Tool reported success' : 'Tool reported an error', `${ex.executor_run_id} · a report, not proof — verification follows`, ex.status === 'success' ? 'done' : 'fail'));
  else if (ms === 'EXECUTING') rows.push(row('Executing in sandbox', '', 'running'));
  else rows.push(row('Execute — pending', '', 'pending'));

  if (v) {
    const actual = v.actual_state.output_percent;
    rows.push(v.pass ? row(`Verified: ${actual} % = target ${expected} %`, '', 'done') : row('Verification detected a mismatch', `expected ${expected} % · observed ${actual} % · diff ${v.diff}`, 'fail'));
  } else if (ms === 'VERIFYING' || (ex && ms === 'EXECUTING')) rows.push(row('Verifying observed state', '', 'running'));
  else rows.push(row('Verify — pending', '', 'pending'));

  if (failed) {
    if (!rb) rows.push(row('Restore checkpoint — pending', '', 'pending'));
    else if (rb.status === 'failed') rows.push(row('Restore failed — writes locked', 'operator reset required', 'fail'));
    else if (rb.status === 'running') rows.push(row('Restoring checkpoint — in progress', rb.checkpoint_id, 'scram'));
    else if (!rb.verified) rows.push(row('Checkpoint restored — verification pending', rb.checkpoint_id, 'running'));
    else rows.push(row('Restoration verified', `checksum ${ck?.checksum ?? '—'} = ${rb.restored_checksum}`, 'done'));
  } else if (!v) {
    rows.push(row('Restore checkpoint — only if verification fails', '', 'pending'));
  }
  return rows;
}

export function spanMark(s: TraceSpan): { mark: string; color: string } {
  switch (s.status) {
    case 'ok':
      return { mark: '✓', color: C.green };
    case 'error':
      return { mark: '✕', color: C.red };
    case 'running':
      return { mark: s.detail || 'running', color: C.yellow };
    case 'skipped':
      return { mark: s.detail || 'never', color: C.muted };
    default:
      return { mark: '○', color: C.muted };
  }
}

// ─────────────────────────── footer ───────────────────────────

export interface FooterItem {
  label: string;
  mark: '●' | '◐' | '○' | '✕' | '–';
  color: string;
  outcome?: boolean;
}

export function outcomeLabel(mode: Mode): { label: string; color: string } | null {
  switch (mode) {
    case 'red': return { label: 'OUTCOME: BLOCKED', color: C.red };
    case 'replay': return { label: 'OUTCOME: APPROVAL INVALID', color: C.red };
    case 'rejected': return { label: 'OUTCOME: REJECTED', color: C.red };
    case 'locked': return { label: 'OUTCOME: LOCKED', color: C.red };
    case 'restored': return { label: 'OUTCOME: RESTORED · VERIFIED', color: C.green };
    case 'completed': return { label: 'OUTCOME: COMPLETED · VERIFIED', color: C.green };
    case 'green': return { label: 'OUTCOME: READ-ONLY · NO WRITE', color: C.green };
    default: return null;
  }
}

/** The persistent state ladder in the footer. The final outcome is its own item, separate from AUDITED. */
export function footer(run: RunView | null, mode: Mode): FooterItem[] {
  const done = (label: string, color: string = C.ink): FooterItem => ({ label, mark: '●', color });
  const active = (label: string, color: string): FooterItem => ({ label, mark: '◐', color });
  const off = (label: string): FooterItem => ({ label, mark: '○', color: C.muted });
  const fail = (label: string): FooterItem => ({ label, mark: '✕', color: C.red });
  const never = (label: string): FooterItem => ({ label, mark: '–', color: C.muted });
  if (!run) return ['RECEIVED', 'NORMALIZED', 'POLICY', 'APPROVAL', 'CHECKPOINT', 'EXECUTE', 'VERIFY', 'RESTORE', 'AUDITED'].map(off);

  const ms = run.machine_state;
  const items: FooterItem[] = [done('RECEIVED'), run.action ? done('NORMALIZED') : active('NORMALIZING', C.yellow)];
  items.push(run.decision ? done(`POLICY: ${run.decision.state}`, policyColor(run)) : off('POLICY'));
  const blocked = mode === 'red' || mode === 'replay' || mode === 'rejected';

  if (mode === 'red') items.push(never('APPROVAL: NOT REQUESTED'));
  else if (mode === 'replay') items.push(fail('APPROVAL INVALID'));
  else if (mode === 'rejected') items.push(fail('REJECTED'));
  else if (mode === 'yellow') items.push(active('AWAITING APPROVAL', C.yellow));
  else if (run.approval?.decision === 'approve') items.push(done('APPROVED', C.yellow));
  else if (run.decision?.state === 'GREEN') items.push(never('APPROVAL: NOT NEEDED'));
  else items.push(off('APPROVAL'));

  if (blocked) {
    items.push(never('EXECUTE: NEVER'));
  } else if (run.decision?.state !== 'GREEN') {
    items.push(run.checkpoint && AFTER_CHECKPOINT.includes(ms) ? done('CHECKPOINTED') : ms === 'CHECKPOINTING' ? active('CHECKPOINTING', C.yellow) : off('CHECKPOINT'));
    items.push(run.execution ? done(run.execution.status === 'success' ? 'TOOL REPORTED' : 'TOOL ERROR', run.execution.status === 'success' ? C.ink : C.red) : ms === 'EXECUTING' ? active('EXECUTING', C.yellow) : off('EXECUTE'));
    const v = run.verification;
    items.push(v ? (v.pass ? done('VERIFIED', C.green) : fail('VERIFY: MISMATCH')) : run.execution ? active('VERIFYING', C.yellow) : off('VERIFY'));
    if (v && !v.pass) {
      const rb = run.rollback;
      items.push(rb?.verified ? done('RESTORE VERIFIED', C.green) : rb?.status === 'failed' ? fail('RESTORE FAILED') : active(rb?.status === 'restored' ? 'RESTORED · VERIFYING' : 'RESTORING', C.red));
    }
  }
  items.push(auditPersisted(run) ? done('AUDIT RECORDED') : off('AUDIT'));
  const out = outcomeLabel(mode);
  if (out) items.push({ label: out.label, mark: '●', color: out.color, outcome: true });
  return items;
}

// ─────────────────────────── comparison strip (audit item 5) ───────────────────────────

export interface StripCell {
  label: string;
  value: string;
  unit: boolean;
  note: string;
  color: string;
  pending: boolean;
}

/** Before · Approved target · Observed result · Restored result — fixed positions for the whole run. */
export function strip(run: RunView | null, mode: Mode): StripCell[] {
  const cell = (label: string, value: string, note: string, color: string = C.ink, unit = true, pending = false): StripCell => ({ label, value, unit, note, color, pending });
  const pend = (label: string, note = ''): StripCell => cell(label, 'Pending', note, C.muted, false, true);
  const before = run?.checkpoint?.before_state.output_percent ?? REACTOR.output_percent;
  if (!run || !run.action) return [cell('Before', String(before), 'confirmed baseline'), pend('Approved target'), pend('Observed result'), pend('Restored result')];

  const exp = Number(run.action.expected_postcondition.output_percent);
  const read = run.action.intent === 'read';
  const out: StripCell[] = [cell('Before', String(before), run.checkpoint ? `checkpoint ${run.checkpoint.checkpoint_id}` : 'at request')];

  if (read) out.push(cell('Approved target', 'None', 'read only · no change requested', C.muted, false));
  else if (mode === 'red') out.push(cell('Approved target', 'None', `requested ${exp} % · blocked`, C.red, false));
  else if (mode === 'replay') out.push(cell('Approved target', 'None valid', `presented ${exp} % · approval refused`, C.red, false));
  else if (mode === 'rejected') out.push(cell('Approved target', 'None', `${exp} % rejected by operator`, C.muted, false));
  else if (run.approval?.decision === 'approve') out.push(cell('Approved target', String(exp), 'approved · single use', C.yellow));
  else if (mode === 'yellow') out.push(cell('Approved target', String(exp), 'awaiting approval', C.yellow));
  else out.push(pend('Approved target'));

  const v = run.verification;
  if (v) out.push(cell('Observed result', String(v.actual_state.output_percent), v.pass ? 'verified' : `failed · expected ${exp} %`, v.pass ? C.green : C.red));
  else if (mode === 'red' || mode === 'replay' || mode === 'rejected') out.push(cell('Observed result', 'Not executed', 'no tool call · confirmed by server', C.muted, false));
  else if (mode === 'green') out.push(cell('Observed result', String(run.current_output_percent), 'read only · no write', C.green));
  else out.push(pend('Observed result', run.execution ? 'tool reported · verifying' : ''));

  const rb = run.rollback;
  if (rb?.verified) out.push(cell('Restored result', String(run.current_output_percent), 'verified · checksum matched', C.green));
  else if (rb?.status === 'failed') out.push(cell('Restored result', 'Failed', 'writes locked', C.red, false));
  else if (rb) out.push(pend('Restored result', rb.status === 'restored' ? 'restored · verifying' : 'restoring'));
  else if (v?.pass || mode === 'green') out.push(cell('Restored result', 'Not needed', v?.pass ? 'postcondition held' : 'nothing written', C.muted, false));
  else if (mode === 'red' || mode === 'replay' || mode === 'rejected') out.push(cell('Restored result', 'Not needed', 'nothing executed', C.muted, false));
  else out.push(pend('Restored result'));
  return out;
}

// ─────────────────────────── gauge / instrument ───────────────────────────

export interface GaugeView {
  value: number;
  label: string;
  color: string;
  target: number | null;
  targetColor: string;
}

export function gauge(run: RunView | null, mode: Mode): GaugeView {
  const value = run?.current_output_percent ?? REACTOR.output_percent;
  const expected = Number(run?.action?.expected_postcondition.output_percent ?? NaN);
  const inBand = value >= REACTOR.band_min && value <= REACTOR.band_max;
  const writes = run?.action?.tool === 'adjust_output';
  let label = 'Output · unchanged';
  let target: number | null = null;
  let targetColor: string = C.yellow;

  if (mode === 'red') { target = expected; targetColor = C.red; label = `Output · ${expected} % never reached`; }
  else if (mode === 'replay') { target = expected; targetColor = C.red; }
  else if (mode === 'yellow') { target = expected; label = `Output · target ${expected} %`; }
  else if (mode === 'exec') { target = expected; label = !inBand ? 'Output · outside band' : run?.verification?.pass ? 'Output · verified at target' : `Output · target ${expected} %`; }
  else if (mode === 'completed') label = 'Output · verified at target';
  else if (mode === 'restored') { target = run?.execution?.resulting_state.output_percent ?? null; targetColor = C.red; label = `Output · restored · verified · ${REACTOR.output_percent} → ${target} → ${value}`; }
  else if (mode === 'green' && !writes) label = 'Output · read-only, unchanged';

  const color = !inBand ? C.red : mode === 'restored' || mode === 'completed' ? C.green : C.ink;
  return { value, label, color, target: Number.isFinite(target as number) ? target : null, targetColor };
}

/** What the instrument draws. A read-only projection of confirmed values; animation never advances state. */
export interface InstrumentModel {
  observed: number;
  target: number | null;
  targetState: 'pending' | 'approved' | 'proposed';
  requested: number | null;
  checkpoint: number | null;
  gate: 'idle' | 'pending' | 'open' | 'closed';
  verified: boolean;
  failed: boolean;
}

export function instrument(run: RunView | null, mode: Mode): InstrumentModel {
  const observed = run?.current_output_percent ?? REACTOR.output_percent;
  const exp = run?.action && run.action.intent !== 'read' ? Number(run.action.expected_postcondition.output_percent) : null;
  const blocked = mode === 'red' || mode === 'replay';
  const v = run?.verification;
  return {
    observed,
    target: blocked ? null : exp,
    targetState: run?.approval?.decision === 'approve' ? 'approved' : mode === 'yellow' ? 'pending' : 'proposed',
    requested: blocked ? exp : null,
    checkpoint: run?.checkpoint && AFTER_CHECKPOINT.includes(run.machine_state) ? run.checkpoint.before_state.output_percent : null,
    gate: !run || !run.decision ? 'idle' : blocked || mode === 'rejected' ? 'closed' : mode === 'yellow' ? 'pending' : 'open',
    verified: !!run?.rollback?.verified || !!v?.pass,
    failed: !!v && !v.pass && !run?.rollback?.verified
  };
}

// ─────────────────────────── evaluators (audit item 6) ───────────────────────────

export type EvalStatus = 'PASS' | 'FAIL' | 'NOT APPLICABLE' | 'NOT EVALUATED';

export interface EvaluatorView {
  name: string;
  rule: string;
  status: EvalStatus;
  result: string;
  inputs: Record<string, unknown>;
  spans: string[];
}

const ORDER = ['request', 'normalize', 'rule', 'policy', 'approval', 'checkpoint', 'execute', 'verify', 'rollback', 'audit'];

/** Scores this run from its evidence. A green result always carries an inspectable reason. */
export function evaluatorViews(run: RunView | null, mode: Mode): EvaluatorView[] {
  if (!run) return [];
  const settled = !['idle', 'yellow', 'exec'].includes(mode);
  const ne = (name: string, rule: string): EvaluatorView => ({ name, rule, status: 'NOT EVALUATED', result: 'Run not settled yet.', inputs: {}, spans: [] });
  const names = run.trace_spans.map((s) => s.name);
  const blocked = mode === 'red' || mode === 'replay' || mode === 'rejected';
  const yellow = run.decision?.state === 'YELLOW';
  const out: EvaluatorView[] = [];

  const r1 = 'In this run: a RED decision, a rejected approval, or an invalid approval must produce no tool call.';
  if (!settled) out.push(ne('Unauthorized execution', r1));
  else if (blocked) out.push({ name: 'Unauthorized execution', rule: r1, status: run.execution ? 'FAIL' : 'PASS', result: run.execution ? 'A tool call was recorded.' : 'No execution result recorded: the tool was never called.', inputs: { decision: run.decision?.state, block_reasons: run.block_reasons, execution: run.execution ? 'present' : null }, spans: names.filter((n) => /policy|approval|execute/.test(n)) });
  else if (yellow) { const ok = run.approval?.decision === 'approve'; out.push({ name: 'Unauthorized execution', rule: r1, status: ok || !run.execution ? 'PASS' : 'FAIL', result: ok ? `Executed only after approval ${run.approval!.approval_id}.` : 'No approval and no execution.', inputs: { approval: run.approval?.approval_id ?? null, execution: run.execution ? 'present' : null }, spans: names.filter((n) => /approval|execute/.test(n)) }); }
  else out.push({ name: 'Unauthorized execution', rule: r1, status: 'NOT APPLICABLE', result: 'GREEN read-only: no authority required.', inputs: { decision: run.decision?.state }, spans: [] });

  const r2 = 'An approval is honored only if it matches the action hash, is unexpired and has not been used.';
  if (!settled) out.push(ne('Approval integrity', r2));
  else if (mode === 'replay') out.push({ name: 'Approval integrity', rule: r2, status: run.execution ? 'FAIL' : 'PASS', result: `Mismatched approval refused before the executor (${run.block_reasons.join(', ')}).`, inputs: { approved_hash: run.approved_hash, presented_hash: run.action?.action_hash }, spans: names.filter((n) => /approval/.test(n)) });
  else if (run.approval?.decision === 'approve') { const match = run.approval.action_hash === run.action?.action_hash; out.push({ name: 'Approval integrity', rule: r2, status: match ? 'PASS' : 'FAIL', result: match ? `Hash ${run.approval.action_hash.slice(0, 4)}…${run.approval.action_hash.slice(-4)} matched; single use consumed.` : 'Approved hash does not match the executed action.', inputs: { approval_hash: run.approval.action_hash, action_hash: run.action?.action_hash, expires_at: run.approval.expires_at, used: run.approval.used }, spans: names.filter((n) => /approval/.test(n)) }); }
  else out.push({ name: 'Approval integrity', rule: r2, status: 'NOT APPLICABLE', result: mode === 'rejected' ? 'Operator rejected; no approval was consumed.' : 'No approval in this path.', inputs: {}, spans: [] });

  const r3 = 'A failed postcondition must end in a verified restore (checksum match) or a write lock.';
  const v = run.verification;
  if (!settled) out.push(ne('Rollback verification', r3));
  else if (v && !v.pass) out.push({ name: 'Rollback verification', rule: r3, status: run.rollback?.verified ? 'PASS' : 'FAIL', result: run.rollback?.verified ? `Restored; checksum ${run.checkpoint?.checksum} = ${run.rollback.restored_checksum}.` : 'Restore not verified.', inputs: { expected: v.expected_postcondition, observed: v.actual_state, rollback: run.rollback }, spans: names.filter((n) => /verify|rollback/.test(n)) });
  else out.push({ name: 'Rollback verification', rule: r3, status: 'NOT APPLICABLE', result: v ? 'Postcondition held — rollback not exercised.' : 'Nothing executed — rollback not exercised.', inputs: {}, spans: [] });

  const r4 = 'Recorded steps follow the required order; nothing executes before policy and approval.';
  const idx = names.map((n) => ORDER.findIndex((o) => n.startsWith(o))).filter((i) => i >= 0);
  const ordered = idx.every((x, i) => i === 0 || x >= idx[i - 1]);
  if (!settled) out.push(ne('Trajectory order', r4));
  else out.push({ name: 'Trajectory order', rule: r4, status: ordered ? 'PASS' : 'FAIL', result: ordered ? `${names.length} recorded steps in order.` : 'A step is out of order.', inputs: { recorded: names }, spans: names });
  return out;
}

/** Display parameters. delta_percent is a change in percentage points, so say so (audit item 3). */
export function formatParams(p: Record<string, number | string> | undefined): string {
  if (!p) return '—';
  return Object.entries(p)
    .map(([k, v]) => (k === 'delta_percent' ? `${Number(v) > 0 ? '+' : ''}${v} pts` : `${k}=${v}`))
    .join(' · ');
}

export function pointsWords(delta: number): string {
  const n = Math.abs(delta);
  return `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${n} percentage point${n === 1 ? '' : 's'}`;
}

export function clockTime(t: number | null): string {
  return t ? new Date(t).toLocaleTimeString([], { hour12: false }) : '—';
}

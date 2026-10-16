// Turns a RunView into what each zone shows. Pure functions: no state, no fetching.
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

export function blockedDetail(run: RunView): { reason: string; detail: string } {
  const reason = run.block_reasons[0] ?? run.decision?.reason_code ?? 'CONTROL_UNAVAILABLE';
  if (reason === 'BOUNDARY_EXCEEDED') {
    const expected = Number(run.action?.expected_postcondition.output_percent);
    const detail = expected > REACTOR.band_max ? `${expected} % > ceiling ${REACTOR.band_max} %`
      : expected < REACTOR.band_min ? `${expected} % < floor ${REACTOR.band_min} %`
      : 'The requested action exceeds the authorized operating boundary.';
    return { reason, detail };
  }
  const details: Record<string, string> = {
    SAFETY_BYPASS_FORBIDDEN: 'Safety functions must remain enabled.',
    NO_AUTHORITY: 'No rule authorizes this action.',
    PARSE_UNCERTAIN: 'The requested action could not be determined reliably.',
    CONTROL_UNAVAILABLE: 'A required control is unavailable.',
    STATE_CHANGED: 'Reactor state changed. Request a fresh approval.',
    APPROVAL_EXPIRED: 'Approval expired before execution.',
    ROLLBACK_FAILED: 'Recovery could not be verified. Operator reset required.'
  };
  return { reason, detail: details[reason] ?? 'The action did not satisfy its authorization requirements.' };
}

export function lockCause(run: RunView): string {
  if (run.block_reasons.includes('ROLLBACK_FAILED')) return 'Recovery failed';
  if (run.block_reasons.includes('CONTROL_UNAVAILABLE')) return 'Audit unavailable';
  return 'Writes locked';
}

export type Mode = 'idle' | 'green' | 'red' | 'yellow' | 'exec' | 'restored' | 'completed' | 'rejected' | 'replay' | 'locked';

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
      return 'restored';
    case 'LOCKED':
      return 'locked';
    case 'COMPLETED':
    case 'AUDITED':
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

export interface LadderRow {
  title: string;
  detail: string;
  mark: string;
  color: string;
  running: boolean;
}

function row(title: string, detail: string, st: 'done' | 'running' | 'fail' | 'pending' | 'scram'): LadderRow {
  const map = { done: ['✓', C.green], running: ['◐', C.yellow], fail: ['✕', C.red], pending: ['○', C.muted], scram: ['◐', C.red] } as const;
  return { title, detail, mark: map[st][0], color: map[st][1], running: st === 'running' || st === 'scram' };
}

/** The execution ladder in the center zone: CHECKPOINT → EXECUTING → VERIFYING → ROLLBACK → RESTORED. */
export function ladder(run: RunView): LadderRow[] {
  const ms = run.machine_state;
  const at = ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'ROLLING_BACK', 'RESTORED', 'COMPLETED'].indexOf(ms);
  const v = run.verification;
  const failed = !!v && !v.pass;
  const expected = run.action?.expected_postcondition.output_percent;
  const rows: LadderRow[] = [
    row('Checkpoint created', run.checkpoint ? `${run.checkpoint.checkpoint_id} · ${run.checkpoint.checksum}` : '', at >= 0 ? 'done' : 'pending'),
    row('Executing', run.execution ? 'tool status: success' : 'sandbox', run.execution ? 'done' : at === 1 || at === 0 ? 'running' : 'pending'),
    row(
      failed ? 'Postcondition failed' : 'Verifying',
      v ? `expected ${expected} · actual ${v.actual_state.output_percent}` : '',
      v ? (failed ? 'fail' : 'done') : run.execution ? 'running' : 'pending'
    )
  ];
  if (failed || run.execution?.fault_injected) {
    rows.push(row('SCRAM · rolling back', run.rollback ? `restoring ${run.rollback.checkpoint_id}` : '', run.rollback?.status === 'restored' ? 'done' : run.rollback ? 'scram' : 'pending'));
    rows.push(row('Safe state restored', '', run.rollback?.verified ? 'done' : 'pending'));
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

export interface FooterItem {
  label: string;
  mark: '●' | '◐' | '○';
  color: string;
}

/** The persistent state ladder in the footer. */
export function footer(run: RunView | null, mode: Mode): FooterItem[] {
  const done = (label: string, color: string = C.ink): FooterItem => ({ label, mark: '●', color });
  const active = (label: string, color: string): FooterItem => ({ label, mark: '◐', color });
  const off = (label: string): FooterItem => ({ label, mark: '○', color: C.muted });
  if (!run) return ['RECEIVED', 'NORMALIZED', 'POLICY', 'APPROVAL', 'CHECKPOINT', 'EXECUTE', 'VERIFY', 'ROLLBACK', 'AUDITED'].map(off);

  const ms = run.machine_state;
  const items: FooterItem[] = [done('RECEIVED'), done('NORMALIZED'), done(`POLICY: ${run.decision?.state ?? '—'}`, policyColor(run))];
  if (mode === 'red') items.push(done('BLOCKED', C.red));
  else if (mode === 'replay') items.push(done('INVALID APPROVAL · BLOCKED', C.red));
  else if (mode === 'rejected') items.push(done('REJECTED · BLOCKED', C.red));
  else if (mode === 'yellow') items.push(active('AWAITING APPROVAL', C.yellow));
  else if (run.approval) items.push(done('APPROVED', C.yellow));
  else items.push(off('APPROVAL'));

  const ran = ['CHECKPOINTING', 'EXECUTING', 'VERIFYING', 'ROLLING_BACK', 'RESTORED', 'COMPLETED', 'LOCKED'].includes(ms) && !!run.approval;
  const failed = !!run.verification && !run.verification.pass;
  items.push(ran ? done('CHECKPOINT') : off('CHECKPOINT'));
  items.push(run.execution ? done('EXECUTE') : ran ? active('EXECUTE', C.yellow) : off('EXECUTE'));
  items.push(run.verification ? done(failed ? 'VERIFY: FAIL' : 'VERIFY: PASS', failed ? C.red : C.green) : run.execution ? active('VERIFY', C.yellow) : off('VERIFY'));
  if (mode === 'restored') items.push(done('RESTORED', C.green));
  else if (mode === 'locked') items.push(done('LOCKED', C.red));
  else if (ms === 'ROLLING_BACK') items.push(active('ROLLBACK', C.red));
  else items.push(off('ROLLBACK'));
  const settled = ['green', 'red', 'replay', 'rejected', 'restored', 'completed', 'locked'].includes(mode);
  const audit = run.trace_spans.find(s => s.name === 'audit');
  items.push(audit?.status === 'ok' ? done('AUDITED')
    : audit?.status === 'error' ? done('AUDIT FAILED', C.red)
    : settled ? active('AUDIT PENDING', C.yellow) : off('AUDITED'));
  return items;
}

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

  if (mode === 'red' && writes && Number.isFinite(expected) && run && blockedDetail(run).reason === 'BOUNDARY_EXCEEDED') {
    target = expected; targetColor = C.red; label = `Output · ${expected} % never reached`;
  }
  else if (mode === 'replay') { target = expected; targetColor = C.red; }
  else if (mode === 'yellow') { target = expected; label = `Output · target ${expected} %`; }
  else if (mode === 'exec') { target = expected; label = !inBand ? 'Output · outside band' : value === expected ? 'Output · at target' : `Output · target ${expected} %`; }
  else if (mode === 'completed') label = 'Output · at target';
  else if (mode === 'restored') { target = run?.execution?.resulting_state.output_percent ?? null; targetColor = C.red; label = `Output · restored · ${REACTOR.output_percent} → ${target} → ${value}`; }
  else if (mode === 'green' && !writes) label = 'Output · read-only, unchanged';

  const color = !inBand ? C.red : mode === 'restored' || mode === 'completed' ? C.green : C.ink;
  return { value, label, color, target: Number.isFinite(target as number) ? target : null, targetColor };
}

export function formatParams(p: Record<string, number | string> | undefined): string {
  if (!p) return '—';
  return Object.entries(p)
    .map(([k, v]) => (k === 'delta_percent' ? `${Number(v) > 0 ? '+' : ''}${v} %` : `${k}=${v}`))
    .join(' · ');
}

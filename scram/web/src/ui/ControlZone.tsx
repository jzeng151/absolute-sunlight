import { useEffect, useState } from 'react';
import { shortHash } from '../api/hash';
import type { RunView } from '../types';
import type { Notice, Unknown } from '../App';
import { REACTOR, clockTime, formatParams, ladder, noExecution, pointsWords, stateColor, type Mode } from './derive';
import { VoiceCard } from './VoiceCard';
import { CodexDrawing, MirrorNote } from './CodexDrawing';

interface Props {
  run: RunView | null;
  mode: Mode;
  caseLabel: string;
  busy: boolean;
  unknown: Unknown | null;
  notice: Notice | null;
  lastConfirmedAt: number | null;
  runDisabled: boolean;
  onRun: () => void;
  onApprove: () => void;
  onReject: () => void;
  onAgain: () => void;
}

/** Countdown that also reports expiry, so Approve can be disabled at zero (audit item 3). */
function useCountdown(iso: string | null): { text: string; expired: boolean; low: boolean } {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!iso) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [iso]);
  if (!iso) return { text: '—', expired: false, low: false };
  const s = Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000));
  return { text: s === 0 ? 'expired' : `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`, expired: s === 0, low: s <= 60 };
}

function Cells({ items }: { items: [string, string, string?][] }) {
  return (
    <div className="cells">
      {items.map(([k, v, color]) => (
        <div className="cell" key={k}>
          <div className="cell__k">{k}</div>
          <div className="cell__v" style={color ? { color } : undefined}>
            {v}
          </div>
        </div>
      ))}
    </div>
  );
}

const FOLIOS: Record<Mode, string> = { idle: '1r', red: '2r', yellow: '3r', exec: '3v', restored: '4r', completed: '4r', rejected: '3r', replay: '5r', green: '1v', locked: '4v' };

const STATE_LABELS: Record<string, string> = {
  AWAITING_APPROVAL: 'awaiting approval', CHECKPOINTING: 'creating checkpoint', EXECUTING: 'executing', VERIFYING: 'verifying', ROLLING_BACK: 'restoring checkpoint'
};

export function ControlZone({ run, mode, caseLabel, busy, unknown, notice, lastConfirmedAt, runDisabled, onRun, onApprove, onReject, onAgain }: Props) {
  const color = unknown ? 'var(--scram-red)' : stateColor(mode, run);
  const cd = useCountdown(mode === 'yellow' ? run?.approval_expires_at ?? null : null);
  const a = run?.action;
  const expected = Number(a?.expected_postcondition.output_percent);
  const delta = Number(a?.normalized_parameters.delta_percent ?? expected - REACTOR.output_percent);
  const settled = !['idle', 'yellow', 'exec'].includes(mode);
  const executorCalls = run && noExecution(run) ? '0 · confirmed' : '—';

  return (
    // No aria-live here: the Announcer reports meaningful transitions only (audit item 8).
    <section className="zone zone--center" aria-label="SCRAM decision" data-mode={mode} style={{ ['--state' as string]: color }}>
      <CodexDrawing mode={mode} color={color} />
      <span className="folio" aria-hidden="true">f. {unknown ? '?' : FOLIOS[mode]}</span>
      <MirrorNote />

      {notice && (
        <div className={`notice notice--${notice.tone}`} role="status">
          <strong>{notice.tone === 'red' ? 'Not confirmed' : 'Confirmed state'}</strong>
          <p>{notice.text}</p>
        </div>
      )}

      {unknown && (
        <div className="stage">
          <p className="state-eyebrow">Unknown outcome</p>
          <h2 className="state-label">STATUS<br />UNKNOWN</h2>
          <p className="state-sub state-sub--plain">Connection lost. Execution status unknown. Checking run…</p>
          <Cells
            items={[
              ['Run', run?.run_id ?? '—'],
              ['Last confirmed', `${run ? STATE_LABELS[run.machine_state] ?? run.machine_state.toLowerCase() : '—'} · ${clockTime(lastConfirmedAt)}`],
              ['Executor calls', run && noExecution(run) ? `0 as of ${clockTime(lastConfirmedAt)}` : 'unknown', 'var(--scram-red)']
            ]}
          />
          <p className="muted" style={{ margin: 0, maxWidth: 460 }}>Controls stay off until SCRAM confirms what happened. Nothing is resent.</p>
        </div>
      )}

      {!unknown && mode === 'idle' && (
        <div className="stage">
          <p className="state-eyebrow" style={{ color: 'var(--scram-ink-muted)' }}>Ready · {caseLabel}</p>
          <h2 className="state-label" style={{ color: 'var(--scram-ink)', fontSize: 60 }}>
            AWAITING
            <br />
            REQUEST
          </h2>
          <p className="muted" style={{ maxWidth: 380, margin: 0 }}>
            SCRAM finds the governing rule and decides before anything runs.
          </p>
          <button type="button" className="btn btn--primary" onClick={onRun} disabled={runDisabled}>
            {busy ? 'Sending…' : 'Run request'}
          </button>
        </div>
      )}

      {!unknown && mode === 'green' && (
        <div className="stage">
          <p className="state-eyebrow">Green · policy decided</p>
          <h2 className="state-label">AUTHORIZED</h2>
          <p className="state-sub">Read-only · no approval needed</p>
          <Cells items={[['Reason', run!.decision!.reason_code], ['Writes', run!.current_output_percent === REACTOR.output_percent ? '0 · output unchanged' : 'check'], ['Result', 'analysis complete']]} />
        </div>
      )}

      {!unknown && mode === 'red' && (
        <div className="stage">
          <p className="state-eyebrow">Red · policy decided</p>
          <h2 className="state-label">
            SCRAM
            <br />
            ACTIVATED
          </h2>
          <p className="state-sub">Action blocked before execution</p>
          <Cells
            items={[
              ['Reason', run!.decision!.reason_code],
              ['Violated', `${expected} % > ceiling ${REACTOR.band_max} %`],
              ['Executor calls', executorCalls, 'var(--scram-red)']
            ]}
          />
        </div>
      )}

      {!unknown && mode === 'yellow' && a && (
        <div className="stage">
          <p className="state-eyebrow">Yellow · paused before execution</p>
          <h2 className="state-label state-label--md">
            HUMAN AUTHORIZATION
            <br />
            REQUIRED
          </h2>
          {cd.expired && (
            <div className="notice notice--red" role="alert">
              <strong>Approval window expired.</strong>
              <p>Nothing has executed under this approval. Request a fresh approval to continue.</p>
            </div>
          )}
          <div className="approval">
            <h3 className="eyebrow">You are approving exactly</h3>
            <p className="approval__plain">
              Increase simulated output from <strong>{REACTOR.output_percent} %</strong> to <strong>{expected} %</strong>
            </p>
            <div className="approval__meta">
              <div>change<span>{pointsWords(delta)}</span></div>
              <div>authorized band<span>{REACTOR.band_min}–{REACTOR.band_max} %</span></div>
              <div>expires<span className={cd.low ? 'warn' : undefined}>{cd.text}</span></div>
              <div>target<span>{a.target}</span></div>
              <div>bound to<span>policy {run!.decision?.policy_version ?? '—'}</span></div>
              <div>use<span>single · unused</span></div>
            </div>
            <div className="approval__actions">
              {cd.expired ? (
                <button type="button" className="btn btn--approve" onClick={onRun} disabled={busy}>
                  Request fresh approval
                </button>
              ) : (
                <button type="button" className="btn btn--approve" onClick={onApprove} disabled={busy}>
                  {busy ? 'Sending…' : 'Approve exact action'}
                </button>
              )}
              <button type="button" className="btn btn--reject" onClick={onReject} disabled={busy}>
                Reject
              </button>
            </div>
            <p className="approval__note">Approver: operator_nw (demo identity) · changing any parameter voids this approval.</p>
            <details className="tech">
              <summary>Technical details</summary>
              <dl className="kv">
                <dt>tool</dt><dd>{a.tool}</dd>
                <dt>parameters</dt><dd>{formatParams(a.normalized_parameters)}</dd>
                <dt>action hash</dt><dd title={a.action_hash}>{shortHash(a.action_hash)}</dd>
                <dt>expires at</dt><dd>{run!.approval_expires_at ? clockTime(new Date(run!.approval_expires_at).getTime()) : '—'}</dd>
              </dl>
            </details>
          </div>
        </div>
      )}

      {!unknown && mode === 'exec' && run && (
        <div className="stage">
          <p className="state-eyebrow" style={{ color: run.verification && !run.verification.pass ? 'var(--scram-red)' : 'var(--scram-ink-muted)' }}>
            {run.verification && !run.verification.pass ? 'Postcondition failed' : `Execution · Daytona sandbox ${run.execution?.executor_run_id ?? ''}`}
          </p>
          <ol className="ladder">
            {ladder(run).map((r) => (
              <li key={r.title} style={{ ['--c' as string]: r.color }}>
                <span className={`ladder__mark${r.running ? ' ladder__mark--running' : ''}`} aria-hidden="true">
                  {r.mark}
                </span>
                <span className="ladder__title">
                  <span className="sr-only">{r.state}: </span>
                  {r.title}
                </span>
                <span className="ladder__detail">{r.detail}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {!unknown && mode === 'restored' && run && (
        <div className="stage">
          <p className="state-eyebrow">
            Restoration verified · checksum {run.checkpoint?.checksum} = {run.rollback?.restored_checksum}
          </p>
          <h2 className="state-label">
            SAFE STATE
            <br />
            RESTORED
          </h2>
          <p className="italic-line">The tool reported success. Verification detected a mismatch. SCRAM restored the checkpoint.</p>
          <p className="scope">
            Verified: simulated output restored to {run.current_output_percent} %; checkpoint checksum matched. Only the simulated output was checked.
          </p>
        </div>
      )}

      {!unknown && mode === 'completed' && run && (
        <div className="stage">
          <p className="state-eyebrow">Postcondition verified</p>
          <h2 className="state-label">
            VERIFIED
            <br />
            COMPLETE
          </h2>
          <p className="mono" style={{ margin: 0 }}>
            expected {expected} % · observed {run.verification?.actual_state.output_percent} % · approval used once
          </p>
          <p className="muted" style={{ margin: 0 }}>Turn on the fault switch and run it again to see the rollback.</p>
        </div>
      )}

      {!unknown && mode === 'rejected' && run && (
        <div className="stage">
          <p className="state-eyebrow">Operator decision</p>
          <h2 className="state-label">REJECTED</h2>
          <p className="state-sub">APPROVAL_REJECTED · executor calls {executorCalls}</p>
        </div>
      )}

      {!unknown && mode === 'replay' && run && (
        <div className="stage">
          <p className="state-eyebrow">Approval check · before executor</p>
          <h2 className="state-label">
            APPROVAL
            <br />
            INVALID
          </h2>
          <p className="state-sub state-sub--plain">
            Presented: {formatParams(a?.normalized_parameters)} → {expected} %. A material parameter changed, so this is a different action.
          </p>
          <div className="hashes">
            <div>
              <small>APPROVED</small>
              {shortHash(run.approved_hash)}
            </div>
            <div className="ne" aria-label="does not equal">≠</div>
            <div style={{ borderColor: 'var(--scram-red)' }}>
              <small>PRESENTED</small>
              {shortHash(run.action?.action_hash)}
            </div>
          </div>
          <div className="codes">
            {run.block_reasons.map((r) => (
              <span key={r}>{r}</span>
            ))}
          </div>
          <p className="mono muted" style={{ margin: 0, fontSize: 13 }}>executor calls {executorCalls}</p>
        </div>
      )}

      {!unknown && mode === 'locked' && (
        <div className="stage">
          <p className="state-eyebrow">Restoration not verified</p>
          <h2 className="state-label">LOCKED</h2>
          <p className="state-sub">All writes stopped · operator reset required</p>
        </div>
      )}

      {!unknown && run?.voice_line && mode !== 'idle' && <VoiceCard line={run.voice_line} />}

      {!unknown && settled && (
        <button type="button" className="btn" onClick={onAgain} disabled={runDisabled} style={{ position: 'relative' }}>
          Run this case again
        </button>
      )}
    </section>
  );
}

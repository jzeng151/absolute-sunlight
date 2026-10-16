import { useEffect, useState } from 'react';
import { shortHash } from '../api/hash';
import type { RunView } from '../types';
import { blockedDetail, formatParams, ladder, lockCause, stateColor, type Mode } from './derive';
import { VoiceCard } from './VoiceCard';
import { CodexDrawing, MirrorNote } from './CodexDrawing';

interface Props {
  run: RunView | null;
  mode: Mode;
  caseLabel: string;
  busy: boolean;
  onRun: () => void;
  onApprove: () => void;
  onReject: () => void;
  onAgain: () => void;
}

function useCountdown(iso: string | null): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!iso) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [iso]);
  if (!iso) return '—';
  const s = Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
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
const folio = (m: Mode) => FOLIOS[m];

export function ControlZone({ run, mode, caseLabel, busy, onRun, onApprove, onReject, onAgain }: Props) {
  const color = stateColor(mode, run);
  const countdown = useCountdown(mode === 'yellow' ? run?.approval_expires_at ?? null : null);
  const a = run?.action;
  const expected = a?.expected_postcondition.output_percent;
  const settled = !['idle', 'yellow', 'exec'].includes(mode);

  return (
    <section className="zone zone--center" aria-label="SCRAM control" aria-live="polite" data-mode={mode} style={{ ['--state' as string]: color }}>
      <CodexDrawing mode={mode} color={color} />
      <span className="folio" aria-hidden="true">f. {folio(mode)}</span>
      <MirrorNote />

      {mode === 'idle' && (
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
          <button type="button" className="btn btn--primary" onClick={onRun} disabled={busy}>
            Run request
          </button>
        </div>
      )}

      {mode === 'green' && (
        <div className="stage">
          <p className="state-eyebrow">Green · policy decided</p>
          <h2 className="state-label">AUTHORIZED</h2>
          <p className="state-sub">Read-only · no approval needed</p>
          <Cells items={[['Reason', run!.decision!.reason_code], ['Writes', '0'], ['Result', 'analysis complete']]} />
        </div>
      )}

      {mode === 'red' && (
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
              ['Reason', blockedDetail(run!).reason],
              ['Constraint', blockedDetail(run!).detail],
              ['Executor calls', '0', 'var(--scram-red)']
            ]}
          />
        </div>
      )}

      {mode === 'yellow' && a && (
        <div className="stage">
          <p className="state-eyebrow">Yellow · paused before execution</p>
          <h2 className="state-label state-label--md">
            HUMAN AUTHORIZATION
            <br />
            REQUIRED
          </h2>
          <div className="approval">
            <h3 className="eyebrow">You are approving exactly</h3>
            <p className="approval__exact">
              {a.tool} · {a.target} · {formatParams(a.normalized_parameters)} → {expected} %
            </p>
            <div className="approval__meta">
              <div>
                hash<span title={a.action_hash}>{shortHash(a.action_hash)}</span>
              </div>
              <div>
                expires<span>{countdown}</span>
              </div>
              <div>
                use<span>single · unused</span>
              </div>
            </div>
            <div className="approval__actions">
              <button type="button" className="btn btn--approve" onClick={onApprove} disabled={busy}>
                Approve exact action
              </button>
              <button type="button" className="btn btn--reject" onClick={onReject} disabled={busy}>
                Reject
              </button>
            </div>
            <p className="approval__note">Approver: operator_nw · changing any parameter voids this approval.</p>
          </div>
        </div>
      )}

      {mode === 'exec' && run && (
        <div className="stage">
          <p className="state-eyebrow" style={{ color: 'var(--scram-ink-muted)' }}>
            Execution · Daytona sandbox {run.execution?.executor_run_id ?? ''}
          </p>
          <ol className="ladder">
            {ladder(run).map((r) => (
              <li key={r.title} style={{ ['--c' as string]: r.color }}>
                <span className={`ladder__mark${r.running ? ' ladder__mark--running' : ''}`} aria-hidden="true">
                  {r.mark}
                </span>
                <span className="ladder__title">{r.title}</span>
                <span className="ladder__detail">{r.detail}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {mode === 'restored' && run && (
        <div className="stage">
          <p className="state-eyebrow">
            Rollback verified · checksum {run.checkpoint?.checksum} = {run.rollback?.restored_checksum}
          </p>
          <h2 className="state-label">
            SAFE STATE
            <br />
            RESTORED
          </h2>
          <p className="italic-line">The tool succeeded. The system failed safely.</p>
        </div>
      )}

      {mode === 'completed' && run && (
        <div className="stage">
          <p className="state-eyebrow">Verify · pass</p>
          <h2 className="state-label">
            VERIFIED
            <br />
            COMPLETE
          </h2>
          <p className="mono" style={{ margin: 0 }}>
            expected {expected} % · actual {run.verification?.actual_state.output_percent} % · approval consumed
          </p>
          <p className="muted" style={{ margin: 0 }}>Turn on the fault switch and run it again to see the rollback.</p>
        </div>
      )}

      {mode === 'rejected' && (
        <div className="stage">
          <p className="state-eyebrow">Operator decision</p>
          <h2 className="state-label">REJECTED</h2>
          <p className="state-sub">Not executed · APPROVAL_REJECTED</p>
        </div>
      )}

      {mode === 'replay' && run && (
        <div className="stage">
          <p className="state-eyebrow">Approval check · before executor</p>
          <h2 className="state-label">
            APPROVAL
            <br />
            INVALID
          </h2>
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
        </div>
      )}

      {mode === 'locked' && (
        <div className="stage">
          <p className="state-eyebrow">{lockCause(run!)}</p>
          <h2 className="state-label">LOCKED</h2>
          <p className="state-sub">All writes stopped · operator reset required</p>
        </div>
      )}

      {run?.voice_line && mode !== 'idle' && <VoiceCard line={run.voice_line} />}

      {settled && (
        <button type="button" className="btn" onClick={onAgain} style={{ position: 'relative' }}>
          Run this case again
        </button>
      )}
    </section>
  );
}

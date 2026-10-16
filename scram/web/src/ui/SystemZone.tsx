import type { RunView } from '../types';
import type { FaultState } from '../App';
import { fixtureLevers } from '../api/fixtureApi';
import { useState } from 'react';
import { clockTime, gauge, instrument, spanMark, type Mode } from './derive';
import { Instrument } from './Instrument';

interface Props {
  run: RunView | null;
  mode: Mode;
  fault: FaultState;
  faultLocked: boolean;
  onFault: (v: boolean) => void;
  lastConfirmedAt: number | null;
  stale: boolean;
  fixtureMode: boolean;
}

export function SystemZone({ run, mode, fault, faultLocked, onFault, lastConfirmedAt, stale, fixtureMode }: Props) {
  const g = gauge(run, mode);
  const [, bump] = useState(0);
  const lose = fixtureLevers.loseNextResponse; // re-read each render: the fixture clears it after use
  const frozen = run?.execution ? `frozen for ${run.run_id}: ${run.execution.fault_injected ? 'on' : 'off'}` : null;

  return (
    <section className="zone zone--right" aria-label="System state and evidence">
      <div style={{ ['--c' as string]: stale ? 'var(--scram-ink-muted)' : g.color }}>
        <h2 className="eyebrow">IV · Simulated system state</h2>
        <p className="out-value">
          {g.value}
          <small> %</small>
        </p>
        <p className="out-label">{g.label}</p>
        <p className="confirmed">
          last confirmed {clockTime(lastConfirmedAt)}
          {stale && <span style={{ color: 'var(--scram-red)' }}> · may be stale</span>}
        </p>
        <Instrument model={instrument(run, mode)} />
      </div>

      <div className="demo">
        <h2 className="eyebrow" style={{ marginBottom: 4 }}>Demo control</h2>
        <label>
          <span>
            Inject executor fault <span className="mono muted" style={{ fontSize: 12 }}>(+20 not +2)</span>
          </span>
          <input type="checkbox" checked={fault.value} disabled={faultLocked || fault.pending} onChange={(e) => onFault(e.target.checked)} />
        </label>
        <p className="ack" aria-live="off">
          {fault.pending ? (
            <span style={{ color: 'var(--scram-yellow)' }}>server: confirming…</span>
          ) : fault.error ? (
            <span style={{ color: 'var(--scram-red)' }}>server did not confirm ({fault.error}) · still {fault.value ? 'on' : 'off'}</span>
          ) : (
            <>server: fault {fault.value ? 'on' : 'off'} · confirmed{frozen ? ` · ${frozen}` : ''}</>
          )}
        </p>
        {fixtureMode && (
          <label>
            <span>
              Lose the next reply <span className="mono muted" style={{ fontSize: 12 }}>(fixture only · tests unknown outcome)</span>
            </span>
            <input
              type="checkbox"
              checked={lose}
              onChange={(e) => {
                fixtureLevers.loseNextResponse = e.target.checked;
                bump((n) => n + 1);
              }}
            />
          </label>
        )}
      </div>

      <div>
        <h2 className="eyebrow">V · Trace</h2>
        {run ? (
          <ul className="trace">
            {run.trace_spans.map((s, i) => {
              const m = spanMark(s);
              return (
                <li key={`${s.name}-${i}`}>
                  <span>{s.name}</span>
                  <span style={{ color: m.color }}>{m.mark}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="placeholder">Spans appear as each step runs.</p>
        )}
      </div>
    </section>
  );
}

import { useState } from 'react';
import type { Outcome, RaceView } from '../types';
import { Enso, Radio } from './parts';

const LABEL = { CONTINUE: 'CONTINUE', CONSERVE: 'CONSERVE', PIT: 'PIT THIS LAP' } as const;
const SUPPORT = { yes: 'supported', partial: 'partial', no: 'not supported' } as const;
const signalName = (s: string) => s.replace(/_(C|pct|s|L)$/, '').replace(/_/g, ' ');

/* 火 Fire — three ways forward, one decision */
export function Fire({ race, busy, onDecide }: { race: RaceView; busy: boolean; onDecide: (c: 'FOLLOW' | 'IGNORE', r: string) => void }) {
  const [rationale, setRationale] = useState('');
  const opts = race.options ?? [];
  const pref = opts.find((o) => o.preferred);
  return (
    <main className="stage-page">
      <div className="stage-head">
        <h2 className="display-xl">Three ways forward. One is supported.</h2>
        <p className="aphorism">See what the eye does not.</p>
      </div>
      <div className="zones zones--cards">
        {opts.map((o) => (
          <section key={o.action} className={`option${o.preferred ? ' option--preferred' : ''}`} aria-label={LABEL[o.action]}>
            <div className="option__head">
              <h3>{LABEL[o.action]}</h3>
              <span className={`support support--${o.supported}`}>{o.preferred ? 'preferred' : SUPPORT[o.supported]}</span>
            </div>
            <p className="option__body">{o.projected_tradeoff}</p>
            <p className="option__assume">Assumes: {o.assumption}</p>
          </section>
        ))}
      </div>
      <div className="decide">
        <div className="decide__why">
          <h3 className="eyebrow">Why {pref ? LABEL[pref.action] : ''}</h3>
          <p className="why">{race.explanation?.text}</p>
          <p className="mono muted small">
            cites: {race.explanation?.cited_signals.map(signalName).join(' · ')} · explanation {race.explanation?.evidence_check === 'pass' ? 'checked against evidence' : 'replaced by template'}
          </p>
        </div>
        <div className="decide__act">
          <h3 className="eyebrow">Strategist decides</h3>
          <div className="row-btns">
            <button type="button" className="btn-big btn-big--follow" onClick={() => onDecide('FOLLOW', rationale)} disabled={busy}>
              FOLLOW
            </button>
            <button type="button" className="btn-big btn-big--ignore" onClick={() => onDecide('IGNORE', rationale)} disabled={busy}>
              IGNORE
            </button>
          </div>
          <label className="field" htmlFor="rationale">
            Rationale (optional)
            <input id="rationale" type="text" value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="e.g. traffic clears this lap" />
          </label>
        </div>
      </div>
    </main>
  );
}

/* 風 Wind — the consequence, on the same seed */
function PositionChart({ outcomes, callLap }: { outcomes: Outcome[]; callLap: number }) {
  const all = outcomes.flatMap((o) => o.position_by_lap);
  const lapMin = Math.min(...all.map((p) => p[0]));
  const lapMax = Math.max(...all.map((p) => p[0]));
  const posMax = Math.max(12, ...all.map((p) => p[1]));
  const W = 900;
  const H = 260;
  const L = 44;
  const R = 150;
  const T = 24;
  const B = 34;
  const x = (lap: number) => L + ((lap - lapMin) / (lapMax - lapMin)) * (W - L - R);
  const y = (pos: number) => T + ((pos - 1) / (posMax - 1)) * (H - T - B);
  const style = {
    FOLLOW: { stroke: 'var(--rl-ink)', dash: undefined, width: 3.5 },
    IGNORE: { stroke: 'var(--rl-vermilion)', dash: undefined, width: 2.5 },
    BASELINE: { stroke: 'var(--rl-indigo)', dash: '8 6', width: 2.5 }
  } as const;
  const ticks = Array.from(new Set([lapMin, callLap, ...all.map((p) => p[0]), lapMax])).sort((a, b) => a - b).filter((l, i, arr) => i === 0 || l - arr[i - 1] >= 2);
  const summary = outcomes.map((o) => `${o.title}: ${o.finish}`).join('; ');
  return (
    <figure className="chart">
      <svg role="img" aria-label={`Race position by lap after the call on lap ${callLap}. ${summary}.`} viewBox={`0 0 ${W} ${H}`}>
        {[1, 4, 8, 12].filter((p) => p <= posMax).map((p) => (
          <g key={p}>
            <line x1={L} x2={W - R} y1={y(p)} y2={y(p)} style={{ stroke: 'var(--rl-line)' }} />
            <text x={L - 10} y={y(p) + 4} textAnchor="end" className="chart__tick">P{p}</text>
          </g>
        ))}
        {ticks.map((l) => (
          <text key={l} x={x(l)} y={H - 10} textAnchor="middle" className="chart__tick">{l}</text>
        ))}
        <line x1={x(callLap)} x2={x(callLap)} y1={T - 8} y2={H - B} style={{ stroke: 'var(--rl-ink)', strokeDasharray: '2 4' }} />
        <text x={x(callLap) + 6} y={T} className="chart__call">the call · lap {callLap}</text>
        {outcomes.map((o) => {
          const s = style[o.path];
          const pts = o.position_by_lap.map(([l, p]) => `${x(l)},${y(p)}`).join(' ');
          const [ll, lp] = o.position_by_lap[o.position_by_lap.length - 1];
          return (
            <g key={o.path}>
              <polyline points={pts} fill="none" style={{ stroke: s.stroke, strokeWidth: s.width, strokeDasharray: s.dash, strokeLinejoin: 'round', strokeLinecap: 'round' }} />
              {o.failure_lap !== null && (
                <g style={{ stroke: s.stroke, strokeWidth: 3 }}>
                  <line x1={x(ll) - 8} y1={y(lp) - 8} x2={x(ll) + 8} y2={y(lp) + 8} />
                  <line x1={x(ll) + 8} y1={y(lp) - 8} x2={x(ll) - 8} y2={y(lp) + 8} />
                </g>
              )}
              <text x={x(ll) + 14} y={y(lp) + 5} className="chart__label" style={{ fill: s.stroke }}>
                {o.title.toUpperCase()} · {o.finish}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function Wind({ race, busy, onDebrief, onReset }: { race: RaceView; busy: boolean; onDebrief: () => void; onReset: () => void }) {
  const outcomes = race.outcomes ?? [];
  return (
    <main className="stage-page">
      <div className="stage-head">
        <h2 className="display-xl">Race position after the call</h2>
        <p className="mono muted small">same seed · three paths · scenario design values</p>
      </div>
      <PositionChart outcomes={outcomes} callLap={race.call_lap ?? race.lap} />
      <div className="zones zones--cards">
        {outcomes.map((o) => (
          <section key={o.path} className={`outcome outcome--${o.path.toLowerCase()}${race.decision?.choice === o.path ? ' outcome--chosen' : ''}`}>
            <h3>
              {o.title}
              {race.decision?.choice === o.path && <span className="chosen">your call</span>}
            </h3>
            <p>{o.summary}</p>
            <p className="mono small muted">simulated · finish {o.finish}</p>
          </section>
        ))}
      </div>
      {race.radio && (
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <Radio line={race.radio} />
        </div>
      )}
      <div className="row-btns row-btns--center">
        <button type="button" className="btn btn--ink" onClick={onDebrief} disabled={busy}>
          Debrief
        </button>
        <button type="button" className="btn btn--quiet" onClick={onReset} disabled={busy}>
          Run again
        </button>
      </div>
    </main>
  );
}

/* 空 Void — the debrief */
export function Void({ race, onReset }: { race: RaceView; onReset: () => void }) {
  const toneColor = { ink: 'var(--rl-ink)', watch: 'var(--rl-indigo)', critical: 'var(--rl-vermilion)', decision: 'var(--rl-ink)' } as const;
  return (
    <main className="void">
      <div className="zones">
        <section className="zone zone--left" aria-label="The record">
          <h2 className="eyebrow">The record</h2>
          <ol className="ledger">
            {race.ledger.map((l, i) => (
              <li key={i}>
                <span className="mono muted">lap {l.lap}</span>
                <span style={{ color: toneColor[l.tone], fontWeight: l.tone === 'decision' ? 700 : 400 }}>{l.text}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="zone zone--center" aria-label="Trajectory and evaluators">
          <h2 className="eyebrow" style={{ alignSelf: 'stretch' }}>Trajectory · in required order · Arize AX</h2>
          <ol className="chips">
            {race.trace_spans.map((s, i) => (
              <li key={s.name} className={s.name === 'human' ? 'chip chip--human' : 'chip'}>
                {i > 0 && <span aria-hidden="true" className="chip__arrow">→</span>}
                <span className="chip__box">{s.name}</span>
              </li>
            ))}
          </ol>
          <div className="evals">
            {race.evaluators.map((e) => (
              <div key={e.name} className="eval">
                <h3>{e.name}</h3>
                <p>{e.description}</p>
                <span className="mono" style={{ color: e.pass ? 'var(--rl-ink)' : 'var(--rl-vermilion)' }}>{e.pass ? 'PASS' : 'FAIL'}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="zone zone--right" aria-label="Detection">
          <h2 className="eyebrow">Detected at</h2>
          <div className="detect">
            <div>
              <p className="detect__lap" style={{ color: 'var(--rl-vermilion)' }}>{race.detection?.redline_lap}</p>
              <p className="muted small">REDLINE</p>
            </div>
            <div>
              <p className="detect__lap" style={{ color: 'var(--rl-indigo)' }}>{race.detection?.baseline_lap}</p>
              <p className="muted small">naive threshold</p>
            </div>
          </div>
          <div className="enso-mini">
            <Enso color="var(--rl-ink)" k="void" size={140} />
          </div>
          <button type="button" className="btn btn--quiet" onClick={onReset}>
            Run again
          </button>
        </section>
      </div>
      <p className="closing">
        REDLINE does not drive the car.
        <br />
        It makes the call inspectable, comparable, accountable.
      </p>
    </main>
  );
}

import type { RunView } from '../types';
import { REACTOR, gauge, spanMark, type Mode } from './derive';

interface Props {
  run: RunView | null;
  mode: Mode;
  fault: boolean;
  faultLocked: boolean;
  onFault: (v: boolean) => void;
}

export function SystemZone({ run, mode, fault, faultLocked, onFault }: Props) {
  const g = gauge(run, mode);
  const bandLeft = REACTOR.band_min;
  const bandWidth = REACTOR.band_max - REACTOR.band_min;

  return (
    <section className="zone zone--right" aria-label="System state and evidence">
      <div style={{ ['--c' as string]: g.color }}>
        <h2 className="eyebrow">IV · Simulated system state</h2>
        <p className="out-value">
          {g.value}
          <small> %</small>
        </p>
        <p className="out-label">{g.label}</p>
        <div
          className="gauge"
          role="img"
          aria-label={`Output ${g.value} percent. Approved band ${REACTOR.band_min} to ${REACTOR.band_max}.${g.target !== null ? ` Marked: ${g.target} percent.` : ''}`}
        >
          <div className="gauge__band" style={{ left: `${bandLeft}%`, width: `${bandWidth}%` }} />
          {g.target !== null && <div className="gauge__target" style={{ left: `${g.target}%`, ['--c' as string]: g.targetColor }} />}
          <div className="gauge__actual" style={{ left: `${g.value}%` }} />
        </div>
        <div className="gauge__scale" aria-hidden="true">
          <span>0</span>
          <span>
            band {REACTOR.band_min}–{REACTOR.band_max}
          </span>
          <span>100</span>
        </div>
      </div>

      <div className="demo">
        <h2 className="eyebrow" style={{ marginBottom: 4 }}>Demo control</h2>
        <label>
          <span>
            Inject executor fault <span className="mono muted" style={{ fontSize: 12 }}>(+20 not +2)</span>
          </span>
          <input type="checkbox" checked={fault} disabled={faultLocked} onChange={(e) => onFault(e.target.checked)} />
        </label>
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

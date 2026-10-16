import type { RunView } from '../types';
import { strip, type Mode } from './derive';

/**
 * Before · Approved target · Observed result · Restored result (design audit, item 5).
 * Positions never move during a run; anything not yet observed reads "Pending".
 */
export function CompareStrip({ run, mode, stale }: { run: RunView | null; mode: Mode; stale: boolean }) {
  return (
    <section className="strip" aria-label="Critical values">
      {strip(run, mode).map((c) => (
        <div className="strip__cell" key={c.label}>
          <div className="strip__k">
            {c.label}
            {stale ? ' · last confirmed' : ''}
          </div>
          <div className={`strip__v${c.pending ? ' strip__v--pending' : ''}`} style={{ color: c.color }}>
            {c.value}
            {c.unit && <small> %</small>}
          </div>
          <div className="strip__note" style={{ color: c.pending ? undefined : c.color }}>{c.note || ' '}</div>
        </div>
      ))}
    </section>
  );
}

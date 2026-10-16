import { useEffect, useState } from 'react';
import { api } from '../api';
import type { BaselineRow, RunView } from '../types';
import { spanMark } from './derive';

export function AuditView({ run }: { run: RunView | null }) {
  const [rows, setRows] = useState<BaselineRow[]>([]);
  useEffect(() => {
    api.baseline().then(setRows).catch(() => setRows([]));
  }, []);

  const spans = run?.trace_spans ?? [];
  const evaluators = run?.evaluators ?? [];

  return (
    <main className="audit">
      <section>
        <h2 className="eyebrow">I · Ordered trajectory · Arize AX {run ? `· ${run.run_id}` : ''}</h2>
        {spans.length ? (
          <div className="spans">
            {spans.map((s, i) => {
              const m = spanMark(s);
              return (
                <div key={`${s.name}-${i}`}>
                  <div className="mono muted" style={{ fontSize: 11 }}>{String(i + 1).padStart(2, '0')}</div>
                  <div className="spans__name">{s.name}</div>
                  <div className="mono" style={{ fontSize: 12, marginTop: 6, color: m.color }}>
                    {m.mark} {s.status === 'ok' ? s.detail : ''}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="placeholder">Run a case to record a trajectory.</p>
        )}
      </section>

      <section>
        <h2 className="eyebrow">II · Evaluators</h2>
        {!evaluators.length && <p className="placeholder">Evaluators score each run once it settles.</p>}
        <div className="evals">
          {evaluators.map((e) => (
            <div className="eval" key={e.name}>
              <h3>{e.name}</h3>
              <p>{e.description}</p>
              <div className="mono" style={{ color: e.pass ? 'var(--scram-green)' : 'var(--scram-red)' }}>{e.pass ? 'PASS' : 'FAIL'}</div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <h2 className="eyebrow">III · Unsafe baseline vs SCRAM · same five cases</h2>
          <span className="mono muted" style={{ fontSize: 12 }}>expected by design · confirm with live run</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">CASE</th>
                <th scope="col">UNSAFE BASELINE</th>
                <th scope="col">SCRAM</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.case_label}>
                  <td>{r.case_label}</td>
                  <td style={{ color: 'var(--scram-red)' }}>{r.baseline}</td>
                  <td style={{ color: 'var(--scram-green)' }}>{r.scram}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <p className="closing">SCRAM claims no certification. It proves an agent stayed within authority and recovered safely.</p>
    </main>
  );
}

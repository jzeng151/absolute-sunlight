import { useEffect, useState } from 'react';
import { api, API_MODE } from '../api';
import { errorCode, isNetworkError } from '../api/errors';
import type { BaselineRow, RunView } from '../types';
import { evaluatorViews, outcomeLabel, spanMark, type EvalStatus, type Mode } from './derive';

type Load = { state: 'loading' } | { state: 'ok'; rows: BaselineRow[] } | { state: 'error'; why: string };

const STATUS_COLOR: Record<EvalStatus, string> = {
  PASS: 'var(--scram-green)',
  FAIL: 'var(--scram-red)',
  'NOT APPLICABLE': 'var(--scram-ink-muted)',
  'NOT EVALUATED': 'var(--scram-yellow)'
};

async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Export the run's evidence as a portable receipt. The digest identifies the record; it is not a signature. */
async function exportReceipt(run: RunView, mode: Mode) {
  const receipt = {
    schema: 'scram.run-receipt/1',
    exported_at: new Date().toISOString(),
    mode: API_MODE === 'fixture' ? 'fixture data (simulation)' : 'live API (simulation)',
    outcome: outcomeLabel(mode)?.label ?? 'in progress',
    run,
    evaluators: evaluatorViews(run, mode)
  };
  const body = JSON.stringify(receipt, null, 2);
  const digest = await sha256(body);
  const blob = new Blob([JSON.stringify({ digest: { alg: 'SHA-256', value: digest, note: 'Identifies this record; not a signature.' }, receipt }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `scram_${run.run_id}_receipt.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function AuditView({ run, mode }: { run: RunView | null; mode: Mode }) {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const fetchBaseline = () => {
    setLoad({ state: 'loading' });
    api.baseline()
      .then((rows) => setLoad({ state: 'ok', rows }))
      .catch((e) => setLoad({ state: 'error', why: isNetworkError(e) ? 'no response' : errorCode(e) })); // shown, never an empty table
  };
  useEffect(fetchBaseline, []);

  const spans = run?.trace_spans ?? [];
  const evals = evaluatorViews(run, mode);
  const settled = !!run && !['idle', 'yellow', 'exec'].includes(mode);

  return (
    <main className="audit">
      <section>
        <h2 className="eyebrow">Run metadata · from the run record</h2>
        {run ? (
          <div className="meta">
            {[
              ['Run', run.run_id],
              ['Case', run.scenario_id],
              ['Data', API_MODE === 'fixture' ? 'fixture data (simulation)' : 'live API (simulation)'],
              ['Policy', run.decision?.policy_version ?? '—'],
              ['Executor', run.execution ? `${run.execution.executor_run_id} · fault ${run.execution.fault_injected ? 'on' : 'off'}` : 'not called'],
              ['Outcome', outcomeLabel(mode)?.label.replace('OUTCOME: ', '') ?? 'in progress']
            ].map(([k, v]) => (
              <div key={k}>
                <div className="meta__k">{k}</div>
                <div className="meta__v">{v}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="placeholder">Run a case to record a trajectory.</p>
        )}
        {run && (
          <div className="export">
            <button type="button" className="btn btn--accent" onClick={() => void exportReceipt(run, mode)} disabled={!settled}>
              Export run receipt
            </button>
            <span className="muted">JSON of the full run record and evaluator results, with a SHA-256 digest. A digest identifies the record; it is not a signature.</span>
          </div>
        )}
      </section>

      <section>
        <h2 className="eyebrow">I · Ordered trajectory · as recorded {run ? `· ${run.run_id}` : ''}</h2>
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
        <h2 className="eyebrow">II · Evaluators · this run only</h2>
        {!evals.length && <p className="placeholder">Evaluators score each run once it settles.</p>}
        <div className="evals">
          {evals.map((e) => (
            <details className="eval" key={e.name}>
              <summary>
                <h3>{e.name}</h3>
                <p>{e.result}</p>
                <span className="mono" style={{ color: STATUS_COLOR[e.status] }}>{e.status}</span>
                <span className="eval__more">Inspect ▾</span>
              </summary>
              <dl className="eval__body">
                <dt>Rule</dt>
                <dd>{e.rule}</dd>
                <dt>Inputs</dt>
                <dd>{JSON.stringify(e.inputs)}</dd>
                <dt>Supporting spans</dt>
                <dd>{e.spans.length ? e.spans.join(' · ') : 'none'}</dd>
              </dl>
            </details>
          ))}
        </div>
        {!!evals.length && <p className="muted" style={{ fontSize: 14, margin: '10px 0 0' }}>A PASS here covers this run only — not every run.</p>}
      </section>

      <section>
        <div className="sec-head">
          <h2 className="eyebrow">III · Unsafe baseline vs SCRAM · same five cases</h2>
          <span className="mono muted" style={{ fontSize: 12 }}>expected by design · not measured · confirm with live run</span>
        </div>
        {load.state === 'loading' && <p className="placeholder" role="status">Loading the expected baseline…</p>}
        {load.state === 'error' && (
          <p className="placeholder" role="alert" style={{ color: 'var(--scram-red)' }}>
            Expected baseline unavailable ({load.why}). This is a load failure, not an empty result.{' '}
            <button type="button" className="btn btn--small" onClick={fetchBaseline}>Retry</button>
          </p>
        )}
        {load.state === 'ok' && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">CASE</th>
                  <th scope="col">UNSAFE BASELINE · EXPECTED</th>
                  <th scope="col">SCRAM · EXPECTED</th>
                </tr>
              </thead>
              <tbody>
                {load.rows.map((r) => (
                  <tr key={r.case_label}>
                    <td>{r.case_label}</td>
                    <td style={{ color: 'var(--scram-red)' }}>{r.baseline}</td>
                    <td style={{ color: 'var(--scram-green)' }}>{r.scram}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="closing">SCRAM claims no certification. In this simulation, each claim on this page links to the evidence behind it.</p>
    </main>
  );
}

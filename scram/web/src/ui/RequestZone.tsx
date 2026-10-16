import casesFile from '../fixtures/cases.json';
import { shortHash } from '../api/hash';
import type { CaseId, RunView } from '../types';
import { C, REACTOR, formatParams, policyColor } from './derive';

const CASE_COLORS: Record<string, string> = { GREEN: C.green, YELLOW: C.yellow, RED: C.red };

interface Props {
  caseId: CaseId;
  run: RunView | null;
  busy: boolean;
  onPick: (id: CaseId) => void;
}

export function RequestZone({ caseId, run, busy, onPick }: Props) {
  const def = casesFile.cases.find((c) => c.id === caseId)!;
  const a = run?.action;
  const color = policyColor(run);
  const expected = a?.expected_postcondition.output_percent;

  return (
    <section className="zone zone--left" aria-label="Request and authority">
      <div>
        <h2 className="eyebrow">Case</h2>
        <div className="cases">
          {casesFile.cases.map((c) => (
            <button
              key={c.id}
              type="button"
              className="case-btn"
              aria-pressed={c.id === caseId}
              disabled={busy}
              style={{ ['--state' as string]: c.id === 'C4' ? C.red : CASE_COLORS[c.policy] }}
              onClick={() => onPick(c.id as CaseId)}
            >
              <small>{c.id}</small>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="eyebrow">I · Operator request</h2>
        <p className="quote">“{run?.request_text ?? def.request_text}”</p>
      </div>

      <div>
        <h2 className="eyebrow">II · Normalized action</h2>
        {a ? (
          <dl className="kv">
            <dt>action</dt>
            <dd>{a.tool}</dd>
            <dt>target</dt>
            <dd>{a.target}</dd>
            <dt>current</dt>
            <dd>{run?.checkpoint?.before_state.output_percent ?? REACTOR.output_percent} %</dd>
            <dt>parameters</dt>
            <dd style={{ color }}>{formatParams(a.normalized_parameters)}</dd>
            <dt>postcondition</dt>
            <dd style={{ color }}>{a.intent === 'read' ? 'no change' : `output = ${expected} %`}</dd>
            <dt>hash</dt>
            <dd title={a.action_hash}>{shortHash(a.action_hash)}</dd>
          </dl>
        ) : (
          <p className="placeholder">Not parsed yet. Run the request.</p>
        )}
      </div>

      <div>
        <h2 className="eyebrow">III · Governing rule</h2>
        {run?.rule ? (
          <div className="rule-card" style={{ ['--state' as string]: color }}>
            <div className="rule-card__meta">
              <span>{run.rule.rule_id}</span>
              <span>{run.rule.title}</span>
            </div>
            <p className="rule-card__text">{run.rule.text}</p>
          </div>
        ) : (
          <p className="placeholder">No rule retrieved yet.</p>
        )}
      </div>
    </section>
  );
}

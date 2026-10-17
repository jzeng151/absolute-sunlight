import { useState } from 'react';
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
  onAsk: (text: string) => void;
}

export function RequestZone({ caseId, run, busy, onPick, onAsk }: Props) {
  const [draft, setDraft] = useState('');
  const ask = draft.trim();
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

      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          if (ask && !busy) onAsk(ask);
        }}
      >
        <label className="eyebrow" htmlFor="ask-text">Ask the assistant</label>
        <textarea
          id="ask-text"
          className="ask__text"
          rows={2}
          maxLength={500}
          value={draft}
          placeholder="e.g. Raise output two percent"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (ask && !busy) onAsk(ask);
            }
          }}
        />
        <button type="submit" className="btn" disabled={busy || !ask}>Send to assistant</button>
      </form>

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
            <dd>{REACTOR.output_percent} %</dd>
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

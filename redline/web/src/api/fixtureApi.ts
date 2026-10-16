// Fixture back end for REDLINE. Behaves like the FastAPI service will, using /src/fixtures.
import data from '../fixtures/scenarios.json';
import { assess } from './risk';
import type {
  Action,
  Evaluator,
  LedgerEntry,
  Outcome,
  RaceView,
  ScenarioId,
  StrategyOption,
  TelemetrySample,
  TraceSpan
} from '../types';

export interface RedlineApi {
  load(id: ScenarioId): Promise<RaceView>;
  advance(): Promise<RaceView>;
  compare(): Promise<RaceView>;
  decide(choice: 'FOLLOW' | 'IGNORE', rationale: string): Promise<RaceView>;
  debrief(): Promise<RaceView>;
  reset(): Promise<RaceView>;
}

type Def = (typeof data)['tire'] & { burn_L_per_lap?: Record<string, number> };

const EVALUATORS: Evaluator[] = [
  { name: 'Signal relevance', description: 'Cited signals exist and moved.', pass: true },
  { name: 'Correctness', description: 'The call matches the truth table.', pass: true },
  { name: 'Faithfulness', description: 'No unchanged signal is cited.', pass: true },
  { name: 'Trajectory', description: 'Human decision before outcome.', pass: true }
];

let scenario: ScenarioId = 'tire';
let lap = data.tire.start_lap;
let phase: RaceView['phase'] = 'MONITORING';
let decision: RaceView['decision'] = null;
let ledger: LedgerEntry[] = [];

const def = (): Def => data[scenario] as Def;
const samples = (): TelemetrySample[] =>
  def().samples.map((s) => ({ ...s, scenario_id: scenario, source_quality: 'ok' as const }));
const lastLap = () => def().samples[def().samples.length - 1].lap;
const delay = (ms = 100) => new Promise((r) => setTimeout(r, ms));

function logAssessment() {
  const a = assess(scenario, samples(), lap, def().burn_L_per_lap, def().laps_total);
  const last = ledger[ledger.length - 1];
  const text =
    a.state === 'NORMAL'
      ? 'Monitoring · NORMAL'
      : `${a.state} · ${a.triggering.map((e) => e.reason_code).join(', ')}`;
  if (!last || last.lap !== lap) ledger.push({ lap, text, tone: a.state === 'CRITICAL' ? 'critical' : a.state === 'WATCH' ? 'watch' : 'ink' });
}

function spans(): TraceSpan[] {
  const order: [string, boolean][] = [
    ['validate', true],
    ['features', true],
    ['risk', true],
    ['compare', phase !== 'MONITORING'],
    ['recommend', phase !== 'MONITORING'],
    ['human', !!decision],
    ['outcome', phase === 'OUTCOME_SIMULATED' || phase === 'DEBRIEFED']
  ];
  return order.map(([name, done]) => ({ name, status: done ? 'ok' : 'pending' }));
}

function view(): RaceView {
  const d = def();
  const s = samples().filter((x) => x.lap <= lap);
  const a = assess(scenario, samples(), lap, d.burn_L_per_lap, d.laps_total);
  const radio = phase === 'MONITORING' && (a.state === 'WATCH' || a.state === 'CRITICAL') ? d.radio[a.state] : null;
  const preferred = d.options.find((o) => o.preferred)!.action as Action;
  return {
    scenario_id: scenario,
    label: d.label,
    lap,
    laps_total: d.laps_total,
    phase,
    samples: s,
    assessment: a,
    options: phase === 'MONITORING' ? null : (d.options as StrategyOption[]),
    explanation:
      phase === 'MONITORING' ? null : { text: d.explanation, cited_signals: d.cited as never, source: 'template', evidence_check: 'pass' },
    decision,
    outcomes: phase === 'OUTCOME_SIMULATED' || phase === 'DEBRIEFED' ? (d.outcomes as Outcome[]) : null,
    call_lap: phase === 'MONITORING' ? null : lap,
    detection: phase === 'DEBRIEFED' ? { redline_lap: firstCriticalLap(), baseline_lap: d.baseline.lap } : null,
    radio: radio ?? (decision && phase === 'OUTCOME_SIMULATED' ? (decision.choice === 'FOLLOW' ? { text: preferred === 'CONSERVE' ? 'Copy. Lift and coast.' : 'Box, box. Confirm.', file: 'follow.mp3' } : { text: 'Copy, staying out.', file: 'ignore.mp3' }) : null),
    ledger: [...ledger],
    trace_spans: spans(),
    evaluators: phase === 'DEBRIEFED' ? EVALUATORS : [],
    can_advance: phase === 'MONITORING' && a.state !== 'CRITICAL' && lap < lastLap()
  };
}

function firstCriticalLap(): number {
  for (const s of samples()) {
    if (s.lap < def().start_lap) continue;
    if (assess(scenario, samples(), s.lap, def().burn_L_per_lap, def().laps_total).state === 'CRITICAL') return s.lap;
  }
  return lap;
}

function start(id: ScenarioId) {
  scenario = id;
  lap = data[id].start_lap;
  phase = 'MONITORING';
  decision = null;
  ledger = [];
  logAssessment();
}
start('tire');

export const fixtureApi: RedlineApi = {
  async load(id) {
    await delay();
    start(id);
    return view();
  },
  async advance() {
    await delay();
    const v = view();
    if (!v.can_advance) throw new Error('Decide on the current call before advancing');
    lap += 1;
    logAssessment();
    return view();
  },
  async compare() {
    await delay();
    const a = view().assessment;
    if (a.state !== 'WATCH' && a.state !== 'CRITICAL') throw new Error('Nothing to compare yet');
    phase = 'COMPARISON_READY';
    const pref = def().options.find((o) => o.preferred)!;
    ledger.push({ lap, text: `Recommended ${pref.action === 'PIT' ? 'PIT THIS LAP' : pref.action}`, tone: 'ink' });
    return view();
  },
  async decide(choice, rationale) {
    await delay();
    if (phase !== 'COMPARISON_READY') throw new Error('No call to decide on');
    const pref = def().options.find((o) => o.preferred)!.action as Action;
    decision = { choice, option_action: pref, lap, rationale };
    ledger.push({ lap, text: `Strategist: ${choice}${rationale ? ` · “${rationale}”` : ''}`, tone: 'decision' });
    phase = 'OUTCOME_SIMULATED';
    const o = def().outcomes.find((x) => x.path === choice)!;
    ledger.push({ lap: o.failure_lap ?? o.position_by_lap[o.position_by_lap.length - 1][0], text: `${o.finish === 'DNF' ? 'DNF' : o.finish} · simulated`, tone: o.finish === 'DNF' ? 'critical' : 'ink' });
    return view();
  },
  async debrief() {
    await delay();
    if (phase !== 'OUTCOME_SIMULATED') throw new Error('Simulate an outcome first');
    phase = 'DEBRIEFED';
    return view();
  },
  async reset() {
    await delay();
    start(scenario);
    return view();
  }
};

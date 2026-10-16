// Data contracts — REDLINE Build Spec, section 4. The back end returns these exact shapes.

export type ScenarioId = 'tire' | 'fuel' | 'engine';
export type RiskState = 'NORMAL' | 'WATCH' | 'CRITICAL' | 'NO_RECOMMENDATION';
export type Phase = 'MONITORING' | 'COMPARISON_READY' | 'OUTCOME_SIMULATED' | 'DEBRIEFED';
export type Action = 'CONTINUE' | 'CONSERVE' | 'PIT';
export type Signal = 'tire_temp_C' | 'tire_wear_pct' | 'lap_delta_s' | 'fuel_L' | 'engine_temp_C';
export type ReasonCode =
  | 'TIRE_WEAR_ACCEL'
  | 'TIRE_TEMP_RISE'
  | 'PACE_LOSS'
  | 'FUEL_SHORT'
  | 'ENGINE_TREND'
  | 'CHANNEL_STALE'
  | 'CHANNEL_MISSING'
  | 'UNIT_UNKNOWN'
  | 'SIGNALS_CONFLICT';

export interface TelemetrySample {
  scenario_id: ScenarioId;
  lap: number;
  tire_temp_C: number;
  tire_wear_pct: number;
  fuel_L: number;
  engine_temp_C: number;
  lap_delta_s: number;
  position: number;
  source_quality: 'ok' | 'stale' | 'missing';
}

export interface Evidence {
  signal: Signal;
  reason_code: ReasonCode;
  sentence: string;
}

export interface RiskAssessment {
  lap: number;
  state: RiskState;
  triggering: Evidence[];
  not_cited: Signal[];
  window_laps: number | null;
}

export interface StrategyOption {
  action: Action;
  projected_tradeoff: string;
  assumption: string;
  supported: 'yes' | 'partial' | 'no';
  preferred: boolean;
}

export interface Explanation {
  text: string;
  cited_signals: Signal[];
  source: 'gemini' | 'bedrock' | 'template';
  evidence_check: 'pass' | 'replaced';
}

export interface HumanDecision {
  choice: 'FOLLOW' | 'IGNORE';
  option_action: Action;
  lap: number;
  rationale: string;
}

export interface Outcome {
  path: 'FOLLOW' | 'IGNORE' | 'BASELINE';
  simulated: true;
  title: string;
  summary: string;
  finish: string;
  failure_lap: number | null;
  /** [lap, position]; position 0 means the car retired */
  position_by_lap: [number, number][];
}

export interface TraceSpan {
  name: string;
  status: 'ok' | 'error' | 'running' | 'pending';
}

export interface Evaluator {
  name: string;
  description: string;
  pass: boolean;
}

export interface VoiceLine {
  text: string;
  file: string;
}

export interface LedgerEntry {
  lap: number;
  text: string;
  tone: 'ink' | 'watch' | 'critical' | 'decision';
}

/** The single object the screen renders. Every endpoint returns this. */
export interface RaceView {
  scenario_id: ScenarioId;
  label: string;
  lap: number;
  laps_total: number;
  phase: Phase;
  samples: TelemetrySample[];
  assessment: RiskAssessment;
  options: StrategyOption[] | null;
  explanation: Explanation | null;
  decision: HumanDecision | null;
  outcomes: Outcome[] | null;
  call_lap: number | null;
  detection: { redline_lap: number; baseline_lap: number } | null;
  radio: VoiceLine | null;
  ledger: LedgerEntry[];
  trace_spans: TraceSpan[];
  evaluators: Evaluator[];
  can_advance: boolean;
}

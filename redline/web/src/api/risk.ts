// Deterministic risk rules — REDLINE Build Spec, section 6.
// Pure functions. The model never sets the risk state; these rules do.
import type { Evidence, RiskAssessment, ScenarioId, Signal, TelemetrySample } from '../types';

const ALL: Signal[] = ['tire_temp_C', 'tire_wear_pct', 'lap_delta_s', 'fuel_L', 'engine_temp_C'];
const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const r1 = (n: number) => Math.round(n * 10) / 10;

export const ENGINE_LIMIT_C = 118;
export const TIRE_LIMIT_C = 125;

export function assess(
  scenario: ScenarioId,
  samples: TelemetrySample[],
  lap: number,
  burn?: Record<string, number>,
  lapsTotal = 60
): RiskAssessment {
  const upTo = samples.filter((s) => s.lap <= lap);
  const now = upTo[upTo.length - 1];
  const at = (k: number) => upTo[upTo.length - 1 - k];

  if (now.source_quality !== 'ok') {
    return {
      lap,
      state: 'NO_RECOMMENDATION',
      triggering: [{ signal: 'tire_temp_C', reason_code: now.source_quality === 'stale' ? 'CHANNEL_STALE' : 'CHANNEL_MISSING', sentence: 'A required channel is not live. No confident call.' }],
      not_cited: [],
      window_laps: null
    };
  }

  const ev: Evidence[] = [];
  let state: RiskAssessment['state'] = 'NORMAL';
  let windowLaps: number | null = null;

  if (scenario === 'tire') {
    const rate = now.tire_wear_pct - at(1).tire_wear_pct;
    const prior = [1, 2, 3].map((k) => at(k).tire_wear_pct - at(k + 1).tire_wear_pct);
    const accel = rate >= 2 * median(prior);
    const rise = now.tire_temp_C - at(3).tire_temp_C;
    const widening = at(2).lap_delta_s < at(1).lap_delta_s && at(1).lap_delta_s < now.lap_delta_s && at(3).lap_delta_s < at(2).lap_delta_s;
    const pace = now.lap_delta_s >= 0.5 && widening;
    if (accel) ev.push({ signal: 'tire_wear_pct', reason_code: 'TIRE_WEAR_ACCEL', sentence: `Wear rate ${rate} %/lap, up from ${median(prior)}.` });
    if (rise >= 10) ev.push({ signal: 'tire_temp_C', reason_code: 'TIRE_TEMP_RISE', sentence: `Rear temp up ${rise} °C in 3 laps; ${TIRE_LIMIT_C - now.tire_temp_C} °C from limit.` });
    if (pace) ev.push({ signal: 'lap_delta_s', reason_code: 'PACE_LOSS', sentence: `Lap delta +${now.lap_delta_s.toFixed(2)} s and widening.` });
    state = ev.length >= 3 ? 'CRITICAL' : ev.length >= 1 ? 'WATCH' : 'NORMAL';
    if (state === 'CRITICAL') windowLaps = 2;
    if (accel && now.lap_delta_s < at(1).lap_delta_s) {
      state = 'WATCH';
      ev.push({ signal: 'lap_delta_s', reason_code: 'SIGNALS_CONFLICT', sentence: 'Wear is rising but pace is improving. Holding at WATCH.' });
    }
  }

  if (scenario === 'fuel') {
    const b = burn?.[String(lap)] ?? 1.5;
    const range = now.fuel_L / b;
    const remaining = lapsTotal - lap;
    const margin = range - remaining;
    if (margin < 1) {
      ev.push({ signal: 'fuel_L', reason_code: 'FUEL_SHORT', sentence: `Range ${r1(range)} laps for ${remaining} remaining (margin ${margin >= 0 ? '+' : ''}${r1(margin)}).` });
      state = margin < 0 ? 'CRITICAL' : 'WATCH';
    }
    windowLaps = Math.max(0, Math.floor(range));
  }

  if (scenario === 'engine') {
    const s1 = now.engine_temp_C - at(1).engine_temp_C;
    const s2 = at(1).engine_temp_C - at(2).engine_temp_C;
    if (s1 >= 2.5 && s2 >= 2.5) {
      const projected = now.engine_temp_C + 3 * s1;
      ev.push({ signal: 'engine_temp_C', reason_code: 'ENGINE_TREND', sentence: `Climbing ${s1} °C a lap; ${now.engine_temp_C} of ${ENGINE_LIMIT_C} °C now.` });
      state = projected >= ENGINE_LIMIT_C ? 'CRITICAL' : 'WATCH';
      windowLaps = Math.max(0, Math.floor((ENGINE_LIMIT_C - now.engine_temp_C) / s1));
    }
  }

  const cited = new Set(ev.map((e) => e.signal));
  return { lap, state, triggering: ev, not_cited: ALL.filter((s) => !cited.has(s)), window_laps: state === 'NORMAL' ? null : windowLaps };
}

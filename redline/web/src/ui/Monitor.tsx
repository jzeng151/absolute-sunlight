import type { RaceView, ScenarioId, Signal } from '../types';
import { ENGINE_LIMIT_C, TIRE_LIMIT_C } from '../api/risk';
import data from '../fixtures/scenarios.json';
import { Enso, Radio, Spark, riskColor } from './parts';

const SIGNALS: { key: Signal; label: string; unit: string; min: number; max: number; limit?: number; fmt?: (v: number) => string }[] = [
  { key: 'tire_temp_C', label: 'Rear tire temp', unit: '°C', min: 100, max: 127, limit: TIRE_LIMIT_C },
  { key: 'tire_wear_pct', label: 'Tire wear', unit: '%', min: 20, max: 60 },
  { key: 'lap_delta_s', label: 'Lap delta', unit: 's', min: 0, max: 0.8, fmt: (v) => `+${v.toFixed(2)}` },
  { key: 'fuel_L', label: 'Fuel', unit: 'L', min: 25, max: 50, fmt: (v) => v.toFixed(1) },
  { key: 'engine_temp_C', label: 'Engine temp', unit: '°C', min: 94, max: 120, limit: ENGINE_LIMIT_C }
];

const SCENARIOS: { id: ScenarioId; label: string }[] = [
  { id: 'tire', label: 'Tire degradation' },
  { id: 'fuel', label: 'Fuel window' },
  { id: 'engine', label: 'Engine heat' }
];

interface Props {
  race: RaceView;
  busy: boolean;
  onScenario: (id: ScenarioId) => void;
  onAdvance: () => void;
  onCompare: () => void;
  onReset: () => void;
}

export function Monitor({ race, busy, onScenario, onAdvance, onCompare, onReset }: Props) {
  const a = race.assessment;
  const window6 = race.samples.slice(-6);
  const first = window6[0]?.lap;
  const now = race.samples[race.samples.length - 1];
  const cited = new Map(a.triggering.map((e) => [e.signal, e]));
  const color = riskColor(a.state);
  const prev = race.ledger.slice(-3).filter((l) => l.tone === 'watch' || l.tone === 'critical');
  const burn = (data.fuel as { burn_L_per_lap?: Record<string, number> }).burn_L_per_lap ?? {};

  const call =
    a.state === 'CRITICAL'
      ? a.triggering.length > 1
        ? `No single value has crossed a limit. Together, ${a.triggering.length === 3 ? 'three' : 'two'} are moving the same way.`
        : a.triggering[0]?.sentence
      : a.state === 'WATCH'
        ? 'One signal is moving early. Watch the next lap.'
        : a.state === 'NO_RECOMMENDATION'
          ? 'A required channel is not live. REDLINE will not guess.'
          : 'No intervention indicated. Signals move as expected for this stint.';

  return (
    <main className="zones">
      <section className="zone zone--left" aria-label="Telemetry">
        <div className="eyebrow-row">
          <h2 className="eyebrow">
            Telemetry · laps {first}–{race.lap}
          </h2>
          <span className="eyebrow">5 / 5 live · units checked</span>
        </div>
        <ul className="signals">
          {SIGNALS.map((s) => {
            const ev = cited.get(s.key);
            const v = now[s.key];
            return (
              <li key={s.key} className={ev ? 'is-cited' : undefined}>
                <div className="signal__text">
                  <div className="signal__label">{s.label}</div>
                  <div className="signal__value">
                    {s.fmt ? s.fmt(v) : v} <small>{s.unit}</small>
                  </div>
                  {ev && <div className="signal__note">{ev.sentence}</div>}
                </div>
                <Spark values={window6.map((x) => x[s.key])} min={s.min} max={s.max} limit={s.limit} cited={!!ev} />
                <span className="dot" aria-label={ev ? 'cited' : 'normal'} />
              </li>
            );
          })}
        </ul>
      </section>

      <section className="zone zone--center" aria-label="Risk and call" aria-live="polite">
        {prev.length > 0 && (
          <p className="history mono">
            {prev.map((l, i) => (
              <span key={l.lap + l.text}>
                {i > 0 && ' → '}lap {l.lap} · <b style={{ color: l.tone === 'critical' ? 'var(--rl-vermilion)' : 'var(--rl-indigo)' }}>{l.text.split(' · ')[0]}</b>
              </span>
            ))}
          </p>
        )}
        <div className="enso-wrap">
          <Enso color={color} heavy={a.state === 'CRITICAL'} k={`${race.scenario_id}-${a.state}`} />
          <div className="enso-label">
            <p className="eyebrow" style={{ margin: 0 }}>Risk</p>
            <p className="state" style={{ color }}>{a.state === 'NO_RECOMMENDATION' ? 'NO CALL' : a.state}</p>
          </div>
        </div>
        {a.state === 'NORMAL' ? (
          <div className="call">
            <p className="eyebrow" style={{ margin: 0 }}>Recommendation</p>
            <p className="call__action">CONTINUE</p>
            <p className="call__note">{call}</p>
          </div>
        ) : (
          <div className="call">
            <p className="call__sentence">{call}</p>
            {(a.state === 'CRITICAL' || a.state === 'WATCH') && (
              <button type="button" className={`btn ${a.state === 'CRITICAL' ? 'btn--ink' : ''}`} onClick={onCompare} disabled={busy}>
                Compare options
              </button>
            )}
          </div>
        )}
        {race.radio && <Radio line={race.radio} />}
      </section>

      <section className="zone zone--right" aria-label="Scenario and evidence">
        <div>
          <h2 className="eyebrow">Scenario</h2>
          <div className="scenarios">
            {SCENARIOS.map((s) => (
              <button key={s.id} type="button" className="scenario" aria-pressed={race.scenario_id === s.id} onClick={() => onScenario(s.id)} disabled={busy}>
                {s.label}
                {s.id === 'tire' && <small>primary</small>}
              </button>
            ))}
          </div>
        </div>

        {a.triggering.length > 0 ? (
          <div>
            <h2 className="eyebrow">Evidence</h2>
            <div className="evidence">
              {a.triggering.map((e) => (
                <div key={e.reason_code} className="evidence__card">
                  <div className="mono evidence__code">{e.reason_code}</div>
                  <div>{e.sentence}</div>
                </div>
              ))}
            </div>
            {a.not_cited.length > 0 && <p className="muted small">Not cited: {a.not_cited.map((s) => s.replace(/_(C|pct|s|L)$/, '').replace(/_/g, ' ')).join(', ')} — unchanged.</p>}
          </div>
        ) : (
          <div>
            <h2 className="eyebrow">Watching</h2>
            <dl className="watching">
              <dt>Wear rate</dt>
              <dd className="mono">+{now.tire_wear_pct - race.samples[race.samples.length - 2].tire_wear_pct} %/lap</dd>
              <dt>Fuel range</dt>
              <dd className="mono">≈ {Math.floor(now.fuel_L / (burn[String(race.lap)] ?? 1.4))} laps</dd>
              <dt>Engine vs limit</dt>
              <dd className="mono">
                {now.engine_temp_C} / {ENGINE_LIMIT_C} °C
              </dd>
            </dl>
          </div>
        )}

        {a.window_laps !== null && (
          <div>
            <h2 className="eyebrow">Estimated window</h2>
            <p className="window">≈ {a.window_laps} laps</p>
            <p className="muted small" style={{ margin: 0 }}>estimate · synthetic model</p>
          </div>
        )}

        <div className="row-btns">
          <button type="button" className="btn" onClick={onAdvance} disabled={busy || !race.can_advance} title={race.can_advance ? undefined : 'Decide on the current call first'}>
            Advance lap
          </button>
          <button type="button" className="btn btn--quiet" onClick={onReset} disabled={busy}>
            Reset
          </button>
        </div>
        <p className="muted small" style={{ margin: 0 }}>Decision support only. REDLINE cannot command the car.</p>
      </section>
    </main>
  );
}

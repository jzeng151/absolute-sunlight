import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import type { RaceView, ScenarioId } from './types';
import { Header } from './ui/Header';
import { Monitor } from './ui/Monitor';
import { Fire, Void, Wind } from './ui/Rings';
import { RING, riskColor, type Ring } from './ui/parts';
import { usePrefs } from './ui/usePrefs';

function ringOf(r: RaceView | null): Ring {
  if (!r) return 'earth';
  if (r.phase === 'COMPARISON_READY') return 'fire';
  if (r.phase === 'OUTCOME_SIMULATED') return 'wind';
  if (r.phase === 'DEBRIEFED') return 'void';
  return r.assessment.state === 'NORMAL' ? 'earth' : 'water';
}

function Footer({ race }: { race: RaceView | null }) {
  const p = race?.phase;
  const s = race?.assessment.state;
  const items: [string, boolean, string?][] = [
    ['CONFIGURED', !!race],
    ['MONITORING', !!race],
    [s ?? 'RISK', !!race, s && s !== 'NORMAL' ? riskColor(s) : undefined],
    ['COMPARE', p !== 'MONITORING' && !!p],
    [race?.decision ? `HUMAN · ${race.decision.choice}` : 'HUMAN DECIDES', !!race?.decision, race?.decision ? 'var(--rl-vermilion)' : undefined],
    ['OUTCOME · SIMULATED', p === 'OUTCOME_SIMULATED' || p === 'DEBRIEFED'],
    ['DEBRIEF', p === 'DEBRIEFED']
  ];
  return (
    <ol className="footer" aria-label="State machine progress">
      {items.map(([label, done, color]) => (
        <li key={label} style={{ color: done ? color ?? 'var(--rl-ink)' : 'var(--rl-ink-muted)' }}>
          <span aria-hidden="true">{done ? '●' : '○'}</span> {label}
        </li>
      ))}
    </ol>
  );
}

export default function App() {
  const prefs = usePrefs();
  const [race, setRace] = useState<RaceView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = useCallback(async (fn: () => Promise<RaceView>) => {
    setBusy(true);
    setError(null);
    try {
      setRace(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void act(() => api.load('tire'));
  }, [act]);

  const ring = ringOf(race);
  const load = (id: ScenarioId) => act(() => api.load(id));
  const reset = () => act(() => api.reset());

  return (
    <div className="app" data-ring={ring}>
      <span className="watermark" aria-hidden="true">{RING[ring]}</span>
      <Header race={race} ring={ring} theme={prefs.theme} onTheme={prefs.toggleTheme} onSmaller={prefs.smaller} onLarger={prefs.larger} />
      {error && (
        <div className="error-bar" role="alert">
          {error}.
        </div>
      )}
      {race && ring !== 'fire' && ring !== 'wind' && ring !== 'void' && (
        <Monitor race={race} busy={busy} onScenario={load} onAdvance={() => act(() => api.advance())} onCompare={() => act(() => api.compare())} onReset={reset} />
      )}
      {race && ring === 'fire' && <Fire race={race} busy={busy} onDecide={(c, r) => act(() => api.decide(c, r))} />}
      {race && ring === 'wind' && <Wind race={race} busy={busy} onDebrief={() => act(() => api.debrief())} onReset={reset} />}
      {race && ring === 'void' && <Void race={race} onReset={reset} />}
      {!race && <main className="zones" aria-busy="true" />}
      <Footer race={race} />
    </div>
  );
}

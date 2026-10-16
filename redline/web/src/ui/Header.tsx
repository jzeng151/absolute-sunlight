import { API_MODE } from '../api';
import type { RaceView } from '../types';
import { RING, riskColor, type Ring } from './parts';
import type { Theme } from './usePrefs';

interface Props {
  race: RaceView | null;
  ring: Ring;
  theme: Theme;
  onTheme: () => void;
  onSmaller: (() => void) | null;
  onLarger: (() => void) | null;
}

const RING_NAME: Record<Ring, string> = { earth: 'Earth', water: 'Water', fire: 'Fire', wind: 'Wind', void: 'Void' };

export function Header({ race, ring, theme, onTheme, onSmaller, onLarger }: Props) {
  const pos = race?.samples[race.samples.length - 1]?.position;
  return (
    <header className="header">
      <div className="header__brand">
        <span className="kanji" title={`${RING_NAME[ring]} ring`} aria-label={`${RING_NAME[ring]} ring`}>
          {RING[ring]}
        </span>
        <div>
          <h1 className="wordmark">
            RED<span>LINE</span>
          </h1>
          <p className="header__sub">Pit wall copilot · Car 27 · synthetic race · {API_MODE === 'fixture' ? 'fixture data' : 'live API'}</p>
        </div>
      </div>
      <div className="header__tools">
        {race && (
          <dl className="readout">
            <div>
              <dt>Lap</dt>
              <dd>
                {race.lap}
                <small> / {race.laps_total}</small>
              </dd>
            </div>
            <div>
              <dt>Pos</dt>
              <dd>P{pos}</dd>
            </div>
            <div>
              <dt>Risk</dt>
              <dd style={{ color: riskColor(race.assessment.state), fontSize: 16 }}>{race.assessment.state.replace('_', ' ')}</dd>
            </div>
          </dl>
        )}
        <span className="stamp">SIMULATED</span>
        <div className="seg" role="group" aria-label="Text size">
          <button type="button" className="seg__btn" onClick={onSmaller ?? undefined} disabled={!onSmaller} aria-label="Smaller text">
            A−
          </button>
          <button type="button" className="seg__btn seg__btn--lg" onClick={onLarger ?? undefined} disabled={!onLarger} aria-label="Larger text">
            A+
          </button>
        </div>
        <button
          type="button"
          className="icon-btn"
          onClick={onTheme}
          aria-label={theme === 'light' ? 'Switch to dark: ink-wash night' : 'Switch to light: ink on washi'}
          title={theme === 'light' ? 'Dark' : 'Light'}
        >
          {theme === 'light' ? (
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
            </svg>
          ) : (
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="12" cy="12" r="4.5" />
              <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />
            </svg>
          )}
        </button>
      </div>
    </header>
  );
}

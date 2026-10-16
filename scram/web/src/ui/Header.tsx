import { API_MODE } from '../api';
import type { Theme } from './usePrefs';

interface Props {
  runId: string | null;
  view: 'console' | 'audit';
  theme: Theme;
  onTheme: () => void;
  onSmaller: (() => void) | null;
  onLarger: (() => void) | null;
  onToggleView: () => void;
  onReset: () => void;
}

function SunMoon({ theme }: { theme: Theme }) {
  return theme === 'dark' ? (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />
    </svg>
  ) : (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
    </svg>
  );
}

export function Header({ runId, view, theme, onTheme, onSmaller, onLarger, onToggleView, onReset }: Props) {
  return (
    <header className="header">
      <div className="header__brand">
        <h1 className="wordmark">
          SCRAM
          <svg className="wordmark__flourish" aria-hidden="true" viewBox="0 0 200 12" preserveAspectRatio="none">
            <path d="M2 8 C 40 2, 80 11, 120 6 S 180 3, 198 7" pathLength={1} />
          </svg>
        </h1>
        <span className="header__sub">Governance Control · Fictional Nuclear Commission</span>
      </div>
      <div className="header__tools">
        <span className="mode-tag">
          {runId ?? 'no run'} · policy v0.3 · {API_MODE === 'fixture' ? 'fixture data' : 'live API'}
        </span>
        <span className="badge">Simulation only · not a certification</span>
        <div className="seg" role="group" aria-label="Text size">
          <button type="button" className="seg__btn" onClick={onSmaller ?? undefined} disabled={!onSmaller} aria-label="Smaller text">
            A<span aria-hidden="true" style={{ fontSize: '0.75em' }}>−</span>
          </button>
          <button type="button" className="seg__btn seg__btn--lg" onClick={onLarger ?? undefined} disabled={!onLarger} aria-label="Larger text">
            A<span aria-hidden="true" style={{ fontSize: '0.75em' }}>+</span>
          </button>
        </div>
        <button
          type="button"
          className="icon-btn"
          onClick={onTheme}
          aria-label={theme === 'dark' ? 'Switch to light: red chalk on parchment' : 'Switch to dark: iron-gall ink'}
          title={theme === 'dark' ? 'Light' : 'Dark'}
        >
          <SunMoon theme={theme} />
        </button>
        <button type="button" className="btn btn--accent" onClick={onToggleView}>
          {view === 'console' ? 'Audit' : 'Back to console'}
        </button>
        <button type="button" className="btn" onClick={onReset}>
          Reset
        </button>
      </div>
    </header>
  );
}

import type { Mode } from './derive';

/**
 * The Vitruvian construction behind the center zone, drawn in red chalk like a Leonardo folio.
 * Base layer: always sanguine. Overlay: one ring in the current state color.
 * Keyed by mode, so each state change redraws the lines (CSS stroke animation; off for reduced motion).
 */
const TICKS = Array.from({ length: 72 }, (_, i) => i * 5);

export function CodexDrawing({ mode, color }: { mode: Mode; color: string }) {
  const r = 182;
  return (
    <svg key={mode} className="codex" data-mode={mode} aria-hidden="true" viewBox="0 0 400 400" fill="none">
      <g className="codex__chalk">
        {/* the man in the square and the circle */}
        <circle className="draw" cx="200" cy="200" r={r} pathLength={1} />
        <rect className="draw" x="62" y="62" width="276" height="276" pathLength={1} />
        <circle className="draw" cx="200" cy="200" r="138" pathLength={1} strokeDasharray="0.004 0.012" />
        <circle className="draw" cx="200" cy="200" r="92" pathLength={1} />
        <line className="draw" x1="10" y1="200" x2="390" y2="200" pathLength={1} />
        <line className="draw" x1="200" y1="10" x2="200" y2="390" pathLength={1} />
        <line className="draw" x1="62" y1="62" x2="338" y2="338" pathLength={1} />
        <line className="draw" x1="338" y1="62" x2="62" y2="338" pathLength={1} />
        {/* compass construction arcs */}
        <path className="draw" d="M 62 200 A 138 138 0 0 1 200 62" pathLength={1} />
        <path className="draw" d="M 338 200 A 138 138 0 0 1 200 338" pathLength={1} />
        {/* the dial: one tick every 5°, long every 30° */}
        {TICKS.map((deg) => {
          const long = deg % 30 === 0;
          const a = (deg * Math.PI) / 180;
          const r1 = r + 2;
          const r2 = r + (long ? 14 : 7);
          return (
            <line
              key={deg}
              className="tick"
              x1={200 + r1 * Math.cos(a)}
              y1={200 + r1 * Math.sin(a)}
              x2={200 + r2 * Math.cos(a)}
              y2={200 + r2 * Math.sin(a)}
            />
          );
        })}
      </g>
      {/* state ring */}
      <circle className="draw codex__state" cx="200" cy="200" r="168" pathLength={1} stroke={color} transform="rotate(-90 200 200)" />
    </svg>
  );
}

/** Leonardo wrote his notes mirrored. Decorative only; the plain reading is in the aria-hidden title. */
export function MirrorNote() {
  return (
    <p className="mirror" aria-hidden="true" title="L'autorità precede l'azione. Authority comes before action.">
      L’autorità precede l’azione.
    </p>
  );
}

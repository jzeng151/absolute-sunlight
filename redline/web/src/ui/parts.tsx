import { useEffect, useRef } from 'react';
import type { RiskState, VoiceLine } from '../types';

export const RING = { earth: '地', water: '水', fire: '火', wind: '風', void: '空' } as const;
export type Ring = keyof typeof RING;

export function riskColor(s: RiskState | null | undefined): string {
  if (s === 'CRITICAL') return 'var(--rl-vermilion)';
  if (s === 'WATCH' || s === 'NO_RECOMMENDATION') return 'var(--rl-indigo)';
  return 'var(--rl-ink)';
}

/** A brush enso: one open circle, gap at the top. Redraws when `k` changes. */
export function Enso({ color, heavy, k, size = 300 }: { color: string; heavy?: boolean; k: string; size?: number }) {
  return (
    <svg key={k} className="enso" aria-hidden="true" viewBox="0 0 240 240" width={size} height={size}>
      <path className="enso__main" d="M 167.5 37.7 A 95 95 0 1 1 128.3 25.4" pathLength={1} stroke={color} strokeWidth={heavy ? 15 : 13} />
      <path className="enso__echo" d="M 170 41 A 92 92 0 1 1 131 29" pathLength={1} stroke={color} strokeWidth={3} />
      <path className="enso__dry" d="M 160 33 A 99 99 0 0 1 182 52" pathLength={1} stroke={color} strokeWidth={2} />
    </svg>
  );
}

/** Six-lap sparkline. Values are drawn on one fixed scale per signal; a dashed line marks a limit. */
export function Spark({
  values,
  min,
  max,
  limit,
  cited
}: {
  values: number[];
  min: number;
  max: number;
  limit?: number;
  cited: boolean;
}) {
  const W = 180;
  const H = 52;
  const y = (v: number) => H - 4 - ((Math.min(Math.max(v, min), max) - min) / (max - min)) * (H - 8);
  const step = values.length > 1 ? W / (values.length - 1) : W;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = values[values.length - 1];
  return (
    <svg className="spark" aria-hidden="true" viewBox={`-4 0 ${W + 8} ${H}`} width={W} height={H}>
      {limit !== undefined && <line x1={0} x2={W} y1={y(limit)} y2={y(limit)} className="spark__limit" />}
      <polyline points={pts} fill="none" stroke={cited ? 'var(--rl-vermilion)' : 'var(--rl-ink)'} strokeWidth={cited ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={(values.length - 1) * step} cy={y(last)} r={3.2} fill={cited ? 'var(--rl-vermilion)' : 'var(--rl-ink)'} />
    </svg>
  );
}

/** Race-engineer radio. Plays /audio/<file> when it appears; the text always shows. */
export function Radio({ line }: { line: VoiceLine }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const play = () => {
    try {
      audio.current?.pause();
      audio.current = new Audio(`/audio/${line.file}`);
      void audio.current.play().catch(() => undefined);
    } catch {
      /* no audio */
    }
  };
  useEffect(() => {
    play();
    return () => audio.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line.file]);
  return (
    <div className="radio">
      <button type="button" className="radio__play" aria-label="Replay radio call" onClick={play}>
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M4 2 L13 8 L4 14 Z" />
        </svg>
      </button>
      <div>
        <p className="radio__text">“{line.text}”</p>
        <p className="radio__src">Pit radio · ElevenLabs</p>
      </div>
      <span className="radio__bars" aria-hidden="true">
        <i /> <i /> <i /> <i /> <i />
      </span>
    </div>
  );
}

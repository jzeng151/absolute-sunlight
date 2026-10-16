import { REACTOR, type InstrumentModel } from './derive';

/**
 * Flat gauge — the fallback for the 3D instrument (WebGL failure, reduced motion, or by choice).
 * Solid = observed, dashed ochre = target, dashed red = requested, diamond = checkpoint,
 * with the 60 and 80 boundaries written on the scale (audit item 5).
 */
export function Gauge2D({ m }: { m: InstrumentModel }) {
  const W = 400, pad = 22, h = 46;
  const x = (v: number) => pad + ((W - 2 * pad) * v) / 100;
  const marks: { v: number; color: string; dash: boolean; text: string; lvl?: number; w?: number }[] = [];
  if (m.target !== null) marks.push({ v: m.target, color: 'var(--scram-yellow)', dash: true, text: `${m.targetState === 'approved' ? 'approved' : 'target'} ${m.target}` });
  if (m.requested !== null) marks.push({ v: m.requested, color: 'var(--scram-red)', dash: true, text: `requested ${m.requested}` });
  marks.push({ v: m.observed, color: m.failed ? 'var(--scram-red)' : 'var(--scram-ink)', dash: false, text: `observed ${m.observed}${m.verified ? ' ✓' : ''}` });
  // stack labels so none overlap
  const placed: { x: number; w: number; lvl: number }[] = [];
  for (const mk of marks) {
    const w = mk.text.length * 7.4;
    let lvl = 0;
    while (placed.some((p) => p.lvl === lvl && Math.abs(p.x - x(mk.v)) < (p.w + w) / 2 + 6)) lvl++;
    mk.lvl = lvl; mk.w = w;
    placed.push({ x: x(mk.v), w, lvl });
  }
  const levels = Math.max(1, ...marks.map((mk) => (mk.lvl ?? 0) + 1));
  const y0 = 10 + levels * 18;
  const H = y0 + h + 34 + (m.checkpoint !== null ? 18 : 0);
  const lx = (mk: (typeof marks)[number]) => Math.min(Math.max(x(mk.v), (mk.w ?? 0) / 2 + 2), W - (mk.w ?? 0) / 2 - 2);

  return (
    <svg className="gauge2d" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <rect x={x(0)} y={y0} width={x(100) - x(0)} height={h} fill="none" style={{ stroke: 'var(--scram-line)' }} />
      <rect x={x(REACTOR.band_min)} y={y0} width={x(REACTOR.band_max) - x(REACTOR.band_min)} height={h} style={{ fill: 'var(--scram-band)', stroke: 'var(--scram-green)' }} strokeOpacity={0.5} />
      {[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((v) => (
        <line key={v} x1={x(v)} x2={x(v)} y1={y0 + h} y2={y0 + h + (v % 50 === 0 || v === 60 || v === 80 ? 8 : 4)} style={{ stroke: 'var(--scram-ink-muted)' }} />
      ))}
      {[0, REACTOR.band_min, REACTOR.band_max, 100].map((v) => (
        <text key={v} x={x(v)} y={y0 + h + 22} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="13" style={{ fill: v === REACTOR.band_min || v === REACTOR.band_max ? 'var(--scram-green)' : 'var(--scram-ink-muted)' }}>
          {v}
        </text>
      ))}
      {m.checkpoint !== null && (
        <g>
          <rect x={x(m.checkpoint) - 5} y={y0 + h / 2 - 5} width="10" height="10" transform={`rotate(45 ${x(m.checkpoint)} ${y0 + h / 2})`} style={{ fill: 'var(--scram-panel)', stroke: 'var(--scram-ink)' }} strokeWidth="2" />
          <text x={Math.min(Math.max(x(m.checkpoint), 60), W - 60)} y={y0 + h + 40} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="13" style={{ fill: 'var(--scram-ink)' }}>
            ◇ checkpoint {m.checkpoint}
          </text>
        </g>
      )}
      {marks.map((mk) => (
        <g key={mk.text}>
          <line className="gauge2d__mark" x1={x(mk.v)} x2={x(mk.v)} y1={y0 - 4} y2={y0 + h + 4} style={{ stroke: mk.color }} strokeWidth={mk.dash ? 2.5 : 3.5} strokeDasharray={mk.dash ? '5 4' : undefined} />
          <text x={lx(mk)} y={y0 - 8 - (mk.lvl ?? 0) * 18} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="13" style={{ fill: mk.color }}>
            {mk.text}
          </text>
        </g>
      ))}
    </svg>
  );
}

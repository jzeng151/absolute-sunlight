import type { FooterItem } from './derive';

export function FooterLadder({ items }: { items: FooterItem[] }) {
  return (
    <ol className="footer" aria-label="State machine progress">
      {items.map((f) => (
        <li key={f.label} className={f.outcome ? 'footer__outcome' : undefined} style={{ color: f.color }}>
          <span aria-hidden="true">{f.mark}</span> {f.label}
        </li>
      ))}
    </ol>
  );
}

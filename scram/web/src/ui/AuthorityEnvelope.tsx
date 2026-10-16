// The authority envelope — a Three.js instrument (design audit: frontier recommendation).
// Read-only projection of confirmed state: it never advances the run, and animation completion means nothing.
// Loaded with React.lazy so the approval controls are usable before this chunk arrives.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { InstrumentModel } from './derive';
import { REACTOR } from './derive';

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

type Labels = Record<'l0' | 'lmin' | 'lmax' | 'l100' | 'obs' | 'tgt' | 'req' | 'ck', HTMLSpanElement>;

class Envelope {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.OrthographicCamera(-60, 60, 30, -30, 0.1, 500);
  private disposables: { dispose: () => void }[] = [];
  private ro: ResizeObserver;
  private raf = 0;
  private cur = { obs: REACTOR.output_percent, tgt: REACTOR.output_percent, req: REACTOR.output_percent, gate: 1 };
  private m: InstrumentModel | null = null;
  private labels: Labels;
  private parts!: {
    base: THREE.Mesh; ticks: THREE.LineSegments; sep: THREE.Line; band: THREE.Mesh; bandEdge: THREE.LineSegments;
    obs: THREE.Mesh; tgt: THREE.LineSegments; req: THREE.LineSegments; gate: THREE.Mesh; ck: THREE.LineSegments; ring: THREE.Mesh;
  };

  constructor(private host: HTMLDivElement, overlay: HTMLDivElement, private onLost: () => void) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); // bounded pixel density
    this.renderer.domElement.setAttribute('aria-hidden', 'true');
    host.prepend(this.renderer.domElement);
    this.renderer.domElement.addEventListener('webglcontextlost', this.lost);
    this.camera.position.set(-14, 52, 64);
    this.camera.lookAt(0, 4, 0);
    this.scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const dl = new THREE.DirectionalLight(0xffffff, 1.1);
    dl.position.set(-30, 60, 40);
    this.scene.add(dl);
    const mk = (_k: keyof Labels, text = '') => { const el = document.createElement('span'); el.className = 'env__tag'; el.textContent = text; overlay.appendChild(el); return el; };
    this.labels = { l0: mk('l0', '0'), lmin: mk('lmin', String(REACTOR.band_min)), lmax: mk('lmax', String(REACTOR.band_max)), l100: mk('l100', '100'), obs: mk('obs'), tgt: mk('tgt'), req: mk('req'), ck: mk('ck') };
    this.labels.lmin.style.color = this.labels.lmax.style.color = 'var(--scram-green)';
    this.labels.tgt.style.color = 'var(--scram-yellow)';
    this.labels.req.style.color = 'var(--scram-red)';
    this.build();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
  }

  private lost = (e: Event) => { e.preventDefault(); this.onLost(); };
  private X = (v: number) => -50 + v;
  private track<T extends { dispose: () => void }>(o: T): T { this.disposables.push(o); return o; }

  private build() {
    const c = this.colors();
    const lambert = (color: THREE.Color, o: THREE.MeshLambertMaterialParameters = {}) => this.track(new THREE.MeshLambertMaterial({ color, ...o }));
    const basic = (color: THREE.Color, o: THREE.MeshBasicMaterialParameters = {}) => this.track(new THREE.MeshBasicMaterial({ color, ...o }));
    const line = (color: THREE.Color, o: THREE.LineBasicMaterialParameters = {}) => this.track(new THREE.LineBasicMaterial({ color, ...o }));
    const dash = (color: THREE.Color) => this.track(new THREE.LineDashedMaterial({ color, dashSize: 1.2, gapSize: 0.9 }));
    const geo = <T extends THREE.BufferGeometry>(g: T) => this.track(g);

    const base = new THREE.Mesh(geo(new THREE.BoxGeometry(100, 1.6, 22)), lambert(c.panel));
    base.position.y = -0.8;
    const pts: number[] = [];
    for (let v = 0; v <= 100; v += 5) { const len = v % 10 === 0 ? 3 : 1.5; const x = this.X(v); pts.push(x, 0.02, 11, x, 0.02, 11 - len); }
    const tg = geo(new THREE.BufferGeometry()); tg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const ticks = new THREE.LineSegments(tg, line(c.muted));
    const sep = new THREE.Line(geo(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-50, 0.03, 0), new THREE.Vector3(50, 0.03, 0)])), dash(c.line));
    sep.computeLineDistances();
    const bandW = REACTOR.band_max - REACTOR.band_min;
    const band = new THREE.Mesh(geo(new THREE.BoxGeometry(bandW, 12, 22)), basic(c.green, { transparent: true, opacity: 0.14, depthWrite: false }));
    band.position.set(this.X((REACTOR.band_min + REACTOR.band_max) / 2), 6, 0);
    const bandEdge = new THREE.LineSegments(geo(new THREE.EdgesGeometry(band.geometry)), line(c.green, { transparent: true, opacity: 0.7 }));
    bandEdge.position.copy(band.position);
    const obs = new THREE.Mesh(geo(new THREE.BoxGeometry(1.1, 14, 4)), lambert(c.ink));
    obs.position.set(0, 7, 5.5);
    const dashBox = (color: THREE.Color) => { const e = new THREE.LineSegments(geo(new THREE.EdgesGeometry(geo(new THREE.BoxGeometry(1.1, 14, 4)))), dash(color)); e.computeLineDistances(); e.position.set(0, 7, -5.5); return e; };
    const tgt = dashBox(c.yellow);
    const req = dashBox(c.red);
    const gate = new THREE.Mesh(geo(new THREE.BoxGeometry(6, 9, 0.5)), lambert(c.yellow, { transparent: true, opacity: 0.85 }));
    gate.position.set(0, 4.5, 0);
    const ck = new THREE.LineSegments(geo(new THREE.EdgesGeometry(geo(new THREE.OctahedronGeometry(1.6)))), line(c.ink));
    ck.position.set(0, 16.5, 5.5);
    const ring = new THREE.Mesh(geo(new THREE.TorusGeometry(3.2, 0.28, 8, 40)), basic(c.green));
    ring.rotation.x = Math.PI / 2; ring.position.set(0, 0.2, 5.5);
    this.parts = { base, ticks, sep, band, bandEdge, obs, tgt, req, gate, ck, ring };
    Object.values(this.parts).forEach((p) => this.scene.add(p));
  }

  private colors() {
    const col = (n: string) => new THREE.Color(css(n));
    return { ink: col('--scram-ink'), muted: col('--scram-ink-muted'), line: col('--scram-line'), panel: col('--scram-panel'), red: col('--scram-red'), yellow: col('--scram-yellow'), green: col('--scram-green') };
  }

  retheme() {
    const c = this.colors(); const p = this.parts;
    (p.base.material as THREE.MeshLambertMaterial).color = c.panel;
    (p.ticks.material as THREE.LineBasicMaterial).color = c.muted;
    (p.sep.material as THREE.LineDashedMaterial).color = c.line;
    (p.band.material as THREE.MeshBasicMaterial).color = c.green;
    (p.bandEdge.material as THREE.LineBasicMaterial).color = c.green;
    (p.tgt.material as THREE.LineDashedMaterial).color = c.yellow;
    (p.req.material as THREE.LineDashedMaterial).color = c.red;
    (p.ck.material as THREE.LineBasicMaterial).color = c.ink;
    (p.ring.material as THREE.MeshBasicMaterial).color = c.green;
    this.apply();
  }

  resize() {
    const w = this.host.clientWidth || 300, h = this.host.clientHeight || 220, aspect = w / h;
    let halfW = 63, hh = halfW / aspect;
    if (hh < 25) { hh = 25; halfW = hh * aspect; }
    Object.assign(this.camera, { left: -halfW, right: halfW, top: hh + 4, bottom: -hh + 4 });
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.apply();
  }

  update(m: InstrumentModel, snap = false) {
    this.m = m;
    const to = { obs: m.observed, tgt: m.target ?? this.cur.tgt, req: m.requested ?? this.cur.req, gate: m.gate === 'open' ? 0 : 1 };
    cancelAnimationFrame(this.raf);
    if (snap || reduced()) { this.cur = to; this.apply(); return; }
    const from = { ...this.cur }, t0 = performance.now(), dur = 700;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      (Object.keys(to) as (keyof typeof to)[]).forEach((key) => { this.cur[key] = from[key] + (to[key] - from[key]) * e; });
      this.apply();
      if (k < 1) this.raf = requestAnimationFrame(step); // animate meaningful transitions only, then stop
    };
    this.raf = requestAnimationFrame(step);
  }

  private apply() {
    const m = this.m; if (!m) return;
    const p = this.parts, c = this.colors(), X = this.X;
    p.obs.position.x = X(this.cur.obs);
    (p.obs.material as THREE.MeshLambertMaterial).color = m.failed ? c.red : c.ink;
    p.tgt.visible = m.target !== null; p.tgt.position.x = X(this.cur.tgt);
    p.req.visible = m.requested !== null; p.req.position.x = X(this.cur.req);
    const gx = m.requested !== null ? this.cur.req : m.target !== null ? this.cur.tgt : this.cur.obs;
    p.gate.visible = m.gate !== 'idle'; p.gate.position.x = X(gx);
    p.gate.scale.y = Math.max(0.04, this.cur.gate); p.gate.position.y = 4.5 * p.gate.scale.y;
    (p.gate.material as THREE.MeshLambertMaterial).color = m.gate === 'closed' ? c.red : c.yellow;
    p.ck.visible = m.checkpoint !== null; if (m.checkpoint !== null) p.ck.position.x = X(m.checkpoint);
    p.ring.visible = m.verified; p.ring.position.x = X(this.cur.obs);
    this.renderer.render(this.scene, this.camera); // render on demand

    const W = this.host.clientWidth, H = this.host.clientHeight;
    const place = (el: HTMLSpanElement, x: number, y: number, z: number, text?: string, show = true) => {
      if (!show) { el.style.display = 'none'; return; }
      const v = new THREE.Vector3(x, y, z).project(this.camera);
      el.style.display = '';
      if (text !== undefined) el.textContent = text;
      el.dataset.x = String(((v.x + 1) / 2) * W); el.dataset.y = String(((1 - v.y) / 2) * H);
    };
    const L = this.labels;
    place(L.l0, X(0), 0, 13.5); place(L.lmin, X(REACTOR.band_min), 0, 13.5); place(L.lmax, X(REACTOR.band_max), 0, 13.5); place(L.l100, X(100), 0, 13.5);
    place(L.obs, X(this.cur.obs), 15, 5.5, `observed ${Math.round(this.cur.obs)}${m.verified ? ' ✓' : ''}`);
    place(L.tgt, X(this.cur.tgt), 15, -5.5, `${m.targetState === 'approved' ? 'approved' : 'target'} ${m.target}`, m.target !== null);
    place(L.req, X(this.cur.req), 15, -5.5, `requested ${m.requested}`, m.requested !== null);
    place(L.ck, X(m.checkpoint ?? 0), 0, 19, `◇ checkpoint ${m.checkpoint}`, m.checkpoint !== null);
    L.obs.style.color = m.failed ? 'var(--scram-red)' : 'var(--scram-ink)';
    // clamp to the stage and lift colliding marker labels so all stay legible
    const placed: { x: number; y: number; w: number }[] = [];
    (Object.values(L) as HTMLSpanElement[]).forEach((el) => {
      if (el.style.display === 'none') return;
      const w = el.offsetWidth; const x = Math.min(Math.max(Number(el.dataset.x), w / 2 + 4), W - w / 2 - 4); let y = Number(el.dataset.y);
      const marker = el === L.obs || el === L.tgt || el === L.req;
      if (marker) for (let i = 0; i < 6 && placed.some((q) => Math.abs(q.x - x) < (q.w + w) / 2 + 6 && Math.abs(q.y - y) < 17); i++) y -= 18;
      if (marker) placed.push({ x, y, w });
      el.style.left = `${x}px`; el.style.top = `${y}px`;
      el.style.transform = el === L.ck || el === L.l0 || el === L.lmin || el === L.lmax || el === L.l100 ? 'translate(-50%, 0)' : 'translate(-50%, -100%)';
    });
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.renderer.domElement.removeEventListener('webglcontextlost', this.lost);
    this.disposables.forEach((d) => d.dispose());
    Object.values(this.labels).forEach((el) => el.remove());
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
}

export default function AuthorityEnvelope({ model, onFail }: { model: InstrumentModel; onFail: (why: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const env = useRef<Envelope | null>(null);

  useEffect(() => {
    try {
      env.current = new Envelope(host.current!, overlay.current!, () => onFail('WebGL context lost; showing the flat gauge.'));
      env.current.update(model, true);
    } catch {
      onFail('3D could not start here; showing the flat gauge.');
    }
    // Retheme when the light/dark switch flips data-theme.
    const mo = new MutationObserver(() => env.current?.retheme());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
    return () => { mo.disconnect(); env.current?.dispose(); env.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sig = JSON.stringify(model);
  useEffect(() => { env.current?.update(model); }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="env" ref={host}>
      <div className="env__overlay" ref={overlay} />
    </div>
  );
}

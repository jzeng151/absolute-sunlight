import { Component, Suspense, lazy, useState, type ReactNode } from 'react';
import { REACTOR, type InstrumentModel } from './derive';
import { Gauge2D } from './Gauge2D';

const AuthorityEnvelope = lazy(() => import('./AuthorityEnvelope'));

const KEY = 'scram.instrument';
/** Checked once per page and the probe context released — browsers cap live WebGL contexts (~16). */
let webglCache: boolean | null = null;
function webglOK(): boolean {
  if (webglCache !== null) return webglCache;
  try {
    const c = document.createElement('canvas');
    const gl = (c.getContext('webgl2') || c.getContext('webgl')) as WebGLRenderingContext | null;
    webglCache = !!gl;
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
  } catch {
    webglCache = false;
  }
  return webglCache;
}
function initial(): '3d' | '2d' {
  try { const s = localStorage.getItem(KEY); if (s === '2d' || s === '3d') return s; } catch { /* storage unavailable */ }
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? '2d' : '3d';
}

class Boundary extends Component<{ onFail: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onFail(); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** 3D authority envelope with the flat gauge as fallback. Both draw the same confirmed values. */
export function Instrument({ model }: { model: InstrumentModel }) {
  const [mode, setMode] = useState<'3d' | '2d'>(() => (webglOK() ? initial() : '2d'));
  const [why, setWhy] = useState<string | null>(webglOK() ? null : 'WebGL is unavailable here, so the flat gauge is shown.');
  const choose = (m: '3d' | '2d') => { setMode(m); setWhy(null); try { localStorage.setItem(KEY, m); } catch { /* ignore */ } };
  const fail = (msg: string) => { setMode('2d'); setWhy(msg); };
  const flat = <div className="instrument__flat"><Gauge2D m={model} /></div>;

  const desc = `Observed ${model.observed} percent` +
    (model.target !== null ? `, ${model.targetState} target ${model.target} percent` : '') +
    (model.requested !== null ? `, requested ${model.requested} percent, blocked` : '') +
    (model.checkpoint !== null ? `, checkpoint ${model.checkpoint} percent` : '') +
    `. Authorized band ${REACTOR.band_min} to ${REACTOR.band_max} percent.`;

  return (
    <div className="instrument">
      <div className="instrument__stage" role="img" aria-label={desc}>
        {mode === '3d' ? (
          <Boundary onFail={() => fail('3D could not load; showing the flat gauge.')}>
            <Suspense fallback={flat}>
              <AuthorityEnvelope model={model} onFail={fail} />
            </Suspense>
          </Boundary>
        ) : flat}
      </div>
      <div className="instrument__bar">
        <div className="legend" aria-hidden="true">
          <span><i />observed</span><span><i className="dash" />target</span><span><i className="req" />requested</span>
          <span><i className="ck" /> checkpoint</span><span><i className="band" />band {REACTOR.band_min}–{REACTOR.band_max}</span>{mode === '3d' && <span><i className="gate" />gate</span>}
        </div>
        <button type="button" className="btn btn--small" aria-pressed={mode === '3d'} onClick={() => choose(mode === '3d' ? '2d' : '3d')} disabled={!webglOK()}>
          {mode === '3d' ? '2D view' : '3D view'}
        </button>
      </div>
      {why && <p className="instrument__msg">{why}</p>}
    </div>
  );
}

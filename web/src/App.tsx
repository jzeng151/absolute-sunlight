import { useCallback, useEffect, useState } from 'react';
import casesFile from './fixtures/cases.json';
import { api, TRANSIENT } from './api';
import type { CaseId, RunView } from './types';
import { footer, modeOf } from './ui/derive';
import { Header } from './ui/Header';
import { RequestZone } from './ui/RequestZone';
import { ControlZone } from './ui/ControlZone';
import { SystemZone } from './ui/SystemZone';
import { FooterLadder } from './ui/FooterLadder';
import { AuditView } from './ui/AuditView';
import { usePrefs } from './ui/usePrefs';

const ACTOR = 'operator_nw';
const POLL_MS = 450;

export default function App() {
  const [caseId, setCaseId] = useState<CaseId>('C2');
  const [run, setRun] = useState<RunView | null>(null);
  const [fault, setFault] = useState(true);
  const [view, setView] = useState<'console' | 'audit'>('console');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prefs = usePrefs();

  const mode = modeOf(run);
  const running = !!run && TRANSIENT.has(run.machine_state);

  // A terminal state can arrive while its audit write and evaluations are finishing.
  useEffect(() => {
    const awaitingEvidence = run && ['COMPLETED', 'BLOCKED', 'RESTORED', 'LOCKED'].includes(run.machine_state)
      && run.evaluators.length === 0;
    if (!run || (!TRANSIENT.has(run.machine_state) && !awaitingEvidence)) return;
    const id = run.run_id;
    const t = setInterval(() => {
      api.getRun(id).then(setRun).catch((e: Error) => setError(e.message));
    }, POLL_MS);
    return () => clearInterval(t);
  }, [run?.run_id, run?.machine_state, run?.evaluators.length]);

  const act = useCallback(async (fn: () => Promise<RunView | void>) => {
    setBusy(true);
    setError(null);
    try {
      const next = await fn();
      if (next) setRun(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const pick = (id: CaseId) => {
    setCaseId(id);
    setRun(null);
  };
  const runCase = () => act(() => api.createRun(caseId));
  const ask = (text: string) => act(() => api.askRun(text));
  const approve = () => run && act(() => api.approve(run, ACTOR));
  const reject = () => run && act(() => api.reject(run));
  const again = () => setRun(null);
  const toggleFault = (v: boolean) => {
    setFault(v);
    void api.setFault(v);
  };
  const reset = () =>
    act(async () => {
      await api.reset();
      setCaseId('C2');
      setRun(null);
      setFault(true);
      setView('console');
    });

  const caseLabel = casesFile.cases.find((c) => c.id === caseId)!;

  return (
    <div className="app">
      <Header
        runId={run?.run_id ?? null}
        view={view}
        theme={prefs.theme}
        onTheme={prefs.toggleTheme}
        onSmaller={prefs.smaller}
        onLarger={prefs.larger}
        onToggleView={() => setView(view === 'console' ? 'audit' : 'console')} onReset={reset} />
      {error && (
        <div className="error-bar" role="alert">
          {error}. Nothing was executed. Reset to start again.
        </div>
      )}
      {view === 'console' ? (
        <main className="zones">
          <RequestZone caseId={caseId} run={run} busy={busy || running} onPick={pick} onAsk={ask} />
          <ControlZone
            run={run}
            mode={mode}
            caseLabel={run?.scenario_id === 'OP' ? 'Operator request' : `${caseLabel.id} ${caseLabel.label}`}
            busy={busy}
            onRun={runCase}
            onApprove={approve}
            onReject={reject}
            onAgain={again}
          />
          <SystemZone run={run} mode={mode} fault={fault} faultLocked={running} onFault={toggleFault} />
        </main>
      ) : (
        <AuditView run={run} />
      )}
      <FooterLadder items={footer(run, mode)} />
    </div>
  );
}

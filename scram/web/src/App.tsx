import { useCallback, useEffect, useRef, useState } from 'react';
import casesFile from './fixtures/cases.json';
import { api, API_MODE, TRANSIENT } from './api';
import { errorCode, isNetworkError } from './api/errors';
import type { CaseId, RunView } from './types';
import { clockTime, footer, modeOf, outcomeLabel } from './ui/derive';
import { Header } from './ui/Header';
import { RequestZone } from './ui/RequestZone';
import { ControlZone } from './ui/ControlZone';
import { SystemZone } from './ui/SystemZone';
import { FooterLadder } from './ui/FooterLadder';
import { AuditView } from './ui/AuditView';
import { CompareStrip } from './ui/CompareStrip';
import { Announcer, useAnnouncer } from './ui/Announcer';
import { usePrefs } from './ui/usePrefs';

const ACTOR = 'operator_nw'; // demo identity — replace with the authenticated identity beyond the demo
const POLL_MS = 450;

export type Conn = 'idle' | 'live' | 'delayed' | 'lost';
/** A mutation whose reply never arrived. Its outcome is UNKNOWN until the server confirms it. */
export interface Unknown { kind: 'submit' | 'approve' | 'reject'; at: number }
export interface Notice { tone: 'green' | 'yellow' | 'red'; text: string }
export interface FaultState { value: boolean; pending: boolean; error: string | null }

const newKey = (p: string) => `${p}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;

export default function App() {
  const [caseId, setCaseId] = useState<CaseId>('C2');
  const [run, setRun] = useState<RunView | null>(null);
  const [fault, setFaultState] = useState<FaultState>({ value: true, pending: false, error: null });
  const [view, setView] = useState<'console' | 'audit'>('console');
  const [busy, setBusy] = useState(false);
  const [conn, setConn] = useState<Conn>('idle');
  const [lastConfirmedAt, setLastConfirmedAt] = useState<number | null>(null);
  const [unknown, setUnknown] = useState<Unknown | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const prefs = usePrefs();
  const announcer = useAnnouncer();

  // Guards against stale replies: a session token per run, and the latest applied request sequence.
  const session = useRef(0);
  const runIdRef = useRef<string | null>(null);
  const seqSent = useRef(0);
  const seqApplied = useRef(0);
  const approvalKey = useRef<{ runId: string; key: string } | null>(null);
  const unknownRef = useRef<Unknown | null>(null);
  unknownRef.current = unknown;

  const mode = modeOf(run);
  const running = !!run && TRANSIENT.has(run.machine_state);
  const awaiting = run?.machine_state === 'AWAITING_APPROVAL';

  /** Apply a server snapshot only if it belongs to the current run and is not older than what is shown. */
  const apply = useCallback((next: RunView, token: number, seq: number) => {
    if (token !== session.current || next.run_id !== runIdRef.current || seq < seqApplied.current) return false;
    seqApplied.current = seq;
    setRun(next);
    setLastConfirmedAt(Date.now());
    setConn('live');
    return true;
  }, []);

  // One poll in flight at a time; keeps going while the run is transient, awaiting approval, or unknown.
  useEffect(() => {
    const id = run?.run_id;
    if (!id || !(running || awaiting || unknown)) return;
    const token = session.current;
    let alive = true;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const seq = ++seqSent.current;
      try {
        const next = await api.getRun(id);
        if (!alive) return;
        failures = 0;
        if (apply(next, token, seq) && unknownRef.current) reconcile(next, unknownRef.current);
      } catch (e) {
        if (!alive) return;
        failures += 1;
        if (isNetworkError(e)) setConn(failures >= 2 ? 'lost' : 'delayed');
      }
      if (alive) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id, running, awaiting, !!unknown, apply]);

  /** Tell the operator what actually happened, from the server's record — never assume. */
  function reconcile(server: RunView, u: Unknown) {
    const at = clockTime(Date.now());
    let n: Notice;
    if (u.kind === 'approve') {
      n = server.approval?.decision === 'approve'
        ? { tone: 'yellow', text: `Confirmed at ${at}: your approval reached SCRAM and was used. Execution went ahead — the steps below are the server's record.` }
        : { tone: 'green', text: `Confirmed at ${at}: the approval did not reach SCRAM. The run is still waiting and no tool has been called (execution: none).` };
    } else {
      n = server.block_reasons.includes('APPROVAL_REJECTED')
        ? { tone: 'green', text: `Confirmed at ${at}: the rejection was recorded. No tool was called.` }
        : { tone: 'yellow', text: `Confirmed at ${at}: the rejection did not reach SCRAM. The approval request is still open.` };
    }
    setUnknown(null);
    setNotice(n);
    announcer.say(n.text);
  }

  const startUnknown = (kind: Unknown['kind']) => {
    setUnknown({ kind, at: Date.now() });
    setConn('lost');
    announcer.say('Connection lost. Execution status unknown. Checking run.', true);
  };

  const pick = (id: CaseId) => {
    setCaseId(id);
    session.current += 1;
    runIdRef.current = null;
    setRun(null);
    setNotice(null);
    setRefusal(null);
  };

  const runCase = async () => {
    setBusy(true); setNotice(null); setRefusal(null);
    session.current += 1;
    const token = session.current;
    const seq = ++seqSent.current;
    try {
      const next = await api.createRun(caseId);
      runIdRef.current = next.run_id;
      approvalKey.current = null;
      apply(next, token, seq);
    } catch (e) {
      if (isNetworkError(e)) {
        // No run ID came back, so there is nothing to look up. Say so plainly.
        setConn('lost');
        setNotice({ tone: 'red', text: 'Connection lost before SCRAM confirmed the request. Its status is unknown, so the request was not resent. Check the connection, then Reset before running again.' });
        announcer.say('Connection lost. Request status unknown.', true);
      } else setRefusal(errorCode(e));
    } finally { setBusy(false); }
  };

  const approve = async () => {
    if (!run) return;
    if (!approvalKey.current || approvalKey.current.runId !== run.run_id) approvalKey.current = { runId: run.run_id, key: newKey('apv') };
    setBusy(true); setNotice(null); setRefusal(null);
    const token = session.current;
    const seq = ++seqSent.current;
    try {
      apply(await api.approve(run, ACTOR, approvalKey.current.key), token, seq);
    } catch (e) {
      if (isNetworkError(e)) startUnknown('approve');
      else setRefusal(errorCode(e));
    } finally { setBusy(false); }
  };

  const reject = async () => {
    if (!run) return;
    setBusy(true); setNotice(null); setRefusal(null);
    const token = session.current;
    const seq = ++seqSent.current;
    try {
      apply(await api.reject(run), token, seq);
    } catch (e) {
      if (isNetworkError(e)) startUnknown('reject');
      else setRefusal(errorCode(e));
    } finally { setBusy(false); }
  };

  const again = () => pick(caseId);

  const toggleFault = async (v: boolean) => {
    setFaultState((f) => ({ ...f, pending: true, error: null }));
    try {
      await api.setFault(v);
      setFaultState({ value: v, pending: false, error: null });
    } catch (e) {
      setFaultState((f) => ({ value: f.value, pending: false, error: isNetworkError(e) ? 'no response' : errorCode(e) }));
    }
  };

  const reset = async () => {
    setBusy(true);
    session.current += 1;
    runIdRef.current = null;
    setRun(null); setUnknown(null); setRefusal(null); setNotice(null);
    try {
      await api.reset();
      setCaseId('C2');
      setFaultState({ value: true, pending: false, error: null });
      setView('console');
      setLastConfirmedAt(Date.now());
      setConn('idle');
      setNotice({ tone: 'green', text: `Reset confirmed at ${clockTime(Date.now())}. Output ${casesFile.reactor.output_percent} %, fault switch on.` });
    } catch {
      setConn('lost');
      setNotice({ tone: 'red', text: 'Reset could not be confirmed. The baseline shown may be stale — try again when connected.' });
    } finally { setBusy(false); }
  };

  // Announce meaningful transitions only (not every tick).
  const outcome = outcomeLabel(mode);
  useEffect(() => {
    if (!run) return;
    const msg = mode === 'yellow' ? 'Human authorization required.' : mode === 'exec' ? (run.verification && !run.verification.pass ? 'Verification detected a mismatch. Restoring checkpoint.' : '') : outcome?.label.replace('OUTCOME: ', 'Outcome: ') ?? '';
    if (msg) announcer.say(msg, mode === 'red' || mode === 'replay' || mode === 'locked' || (mode === 'exec' && !!run.verification && !run.verification.pass));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, run?.run_id, !!run?.verification]);

  const caseLabel = casesFile.cases.find((c) => c.id === caseId)!;
  const locked = busy || running || !!unknown || fault.pending;

  return (
    <div className="app">
      <Header
        runId={run?.run_id ?? null}
        view={view}
        theme={prefs.theme}
        conn={unknown ? 'lost' : conn}
        unknown={!!unknown}
        lastConfirmedAt={lastConfirmedAt}
        polling={running || awaiting || !!unknown}
        onTheme={prefs.toggleTheme}
        onSmaller={prefs.smaller}
        onLarger={prefs.larger}
        onToggleView={() => setView(view === 'console' ? 'audit' : 'console')}
        onReset={reset}
        resetDisabled={busy}
      />
      {refusal && (
        <div className="error-bar" role="alert">
          SCRAM refused the request · {refusal}. {run && !run.execution ? 'No tool call is recorded for this run.' : ''}
        </div>
      )}
      {view === 'console' ? (
        <main className="zones">
          <CompareStrip run={run} mode={mode} stale={!!unknown || conn === 'lost'} />
          <ControlZone
            run={run}
            mode={mode}
            caseLabel={`${caseLabel.id} ${caseLabel.label}`}
            busy={busy}
            unknown={unknown}
            notice={notice}
            lastConfirmedAt={lastConfirmedAt}
            runDisabled={locked}
            onRun={runCase}
            onApprove={approve}
            onReject={reject}
            onAgain={again}
          />
          <SystemZone
            run={run}
            mode={mode}
            fault={fault}
            faultLocked={running || awaiting || busy}
            onFault={toggleFault}
            lastConfirmedAt={lastConfirmedAt}
            stale={!!unknown || conn === 'lost'}
            fixtureMode={API_MODE === 'fixture'}
          />
          <RequestZone caseId={caseId} run={run} busy={locked} onPick={pick} />
        </main>
      ) : (
        <AuditView run={run} mode={mode} />
      )}
      <FooterLadder items={footer(run, mode)} />
      <Announcer state={announcer.state} />
    </div>
  );
}

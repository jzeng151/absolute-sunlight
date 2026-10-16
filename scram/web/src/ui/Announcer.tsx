import { useCallback, useRef, useState } from 'react';

/**
 * A dedicated status announcer (design audit, item 8). Only meaningful transitions are announced —
 * never countdown ticks. Polite for progress, assertive for blocks and failures.
 */
export interface AnnouncerState { polite: string; assertive: string }

export function useAnnouncer() {
  const [state, setState] = useState<AnnouncerState>({ polite: '', assertive: '' });
  const last = useRef('');
  const say = useCallback((text: string, urgent = false) => {
    if (!text || text === last.current) return;
    last.current = text;
    // Clear first so repeated identical regions are re-read by assistive technology.
    setState((s) => (urgent ? { ...s, assertive: '' } : { ...s, polite: '' }));
    setTimeout(() => setState((s) => (urgent ? { ...s, assertive: text } : { ...s, polite: text })), 40);
  }, []);
  return { state, say };
}

export function Announcer({ state }: { state: AnnouncerState }) {
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{state.polite}</div>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true">{state.assertive}</div>
    </>
  );
}

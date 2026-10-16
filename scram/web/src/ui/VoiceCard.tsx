import { useEffect, useRef, useState } from 'react';
import type { VoiceLine } from '../types';

type VoiceState = 'ready' | 'playing' | 'unavailable' | 'blocked';
const LABEL: Record<VoiceState, string> = { ready: 'ready', playing: 'playing', unavailable: 'clip unavailable', blocked: 'press play to hear it' };

/**
 * Shows the control-room line and plays /audio/<file> when it appears.
 * Audio state is shown instead of being swallowed (audit item 8): ready, playing, unavailable, or blocked by the browser.
 */
export function VoiceCard({ line }: { line: VoiceLine }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<VoiceState>('ready');

  const play = () => {
    try {
      audio.current?.pause();
      const el = new Audio(`${import.meta.env.BASE_URL}audio/${line.file}`);
      audio.current = el;
      el.onplaying = () => setState('playing');
      el.onended = () => setState('ready');
      el.onerror = () => setState('unavailable');
      void el.play().catch((e: DOMException) => setState(e?.name === 'NotAllowedError' ? 'blocked' : 'unavailable'));
    } catch {
      setState('unavailable');
    }
  };

  useEffect(() => {
    setState('ready');
    play();
    return () => audio.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line.file]);

  return (
    <div className="voice">
      <button type="button" className="voice__play" aria-label="Play voice line" onClick={play}>
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="var(--scram-ink)">
          <path d="M4 2 L13 8 L4 14 Z" />
        </svg>
      </button>
      <div>
        <p className="voice__text">“{line.text}”</p>
        <p className="voice__src">
          Control-room voice · ElevenLabs · <span style={{ color: state === 'unavailable' ? 'var(--scram-red)' : undefined }}>{LABEL[state]}</span>
        </p>
      </div>
    </div>
  );
}

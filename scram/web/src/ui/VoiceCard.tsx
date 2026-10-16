import { useEffect, useRef } from 'react';
import type { VoiceLine } from '../types';

/**
 * Shows the control-room line and plays /audio/<file> when it appears.
 * If the clip is missing (not generated yet), the text still shows and nothing breaks.
 */
export function VoiceCard({ line }: { line: VoiceLine }) {
  const audio = useRef<HTMLAudioElement | null>(null);

  const play = () => {
    try {
      audio.current?.pause();
      audio.current = new Audio(`/audio/${line.file}`);
      void audio.current.play().catch(() => undefined);
    } catch {
      /* no audio available */
    }
  };

  useEffect(() => {
    play();
    return () => audio.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line.file]);

  return (
    <div className="voice">
      <button type="button" className="voice__play" aria-label="Replay voice line" onClick={play}>
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="var(--scram-ink)">
          <path d="M4 2 L13 8 L4 14 Z" />
        </svg>
      </button>
      <div>
        <p className="voice__text">“{line.text}”</p>
        <p className="voice__src">Control-room voice · ElevenLabs</p>
      </div>
    </div>
  );
}

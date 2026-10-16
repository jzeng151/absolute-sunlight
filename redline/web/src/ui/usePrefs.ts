import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';
export const SCALES = [0.9, 1, 1.12, 1.25] as const;

const KEY = 'redline.prefs';

function read(): { theme?: Theme; scale?: number } {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}');
  } catch {
    return {};
  }
}

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/** Theme (light / dark) and text size, remembered in this browser when storage is available. */
export function usePrefs() {
  const saved = read();
  const [theme, setTheme] = useState<Theme>(saved.theme ?? systemTheme());
  const [scale, setScale] = useState<number>(saved.scale && SCALES.includes(saved.scale as never) ? saved.scale : 1);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.setProperty('--ui-scale', String(scale));
    try {
      localStorage.setItem(KEY, JSON.stringify({ theme, scale }));
    } catch {
      /* storage unavailable: preferences last for this visit only */
    }
  }, [theme, scale]);

  const i = SCALES.indexOf(scale as (typeof SCALES)[number]);
  return {
    theme,
    toggleTheme: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')),
    scale,
    smaller: i > 0 ? () => setScale(SCALES[i - 1]) : null,
    larger: i < SCALES.length - 1 ? () => setScale(SCALES[i + 1]) : null
  };
}

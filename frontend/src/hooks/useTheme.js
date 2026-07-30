import { useEffect, useState } from 'react';

const STORAGE_KEY = 'jarvis-theme'; // 'light' | 'dark' | 'system'

function resolveSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function useTheme() {
  const [pref, setPref] = useState(() => localStorage.getItem(STORAGE_KEY) || 'system');

  useEffect(() => {
    const root = document.documentElement;
    if (pref === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', pref);
    }
    localStorage.setItem(STORAGE_KEY, pref);
  }, [pref]);

  const resolved = pref === 'system' ? resolveSystemTheme() : pref;

  const toggle = () => setPref(resolved === 'dark' ? 'light' : 'dark');

  return { theme: resolved, toggle };
}

import { useCallback, useEffect, useState } from 'react';

const KEY = 'jarvis.openedOn';

function firstOpenToday() {
  try {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    const today = new Date().toDateString();
    if (localStorage.getItem(KEY) === today) return false;
    localStorage.setItem(KEY, today);
    return true;
  } catch {
    return false;
  }
}

// Decided once per window load (StrictMode runs state initialisers twice,
// and the second call would already see today's date stored).
const PLAYS_THIS_LOAD = firstOpenToday();

/** [playing, stop, delaying] — `delaying` holds the console's entrance
 * back until the intro is over, and stays set until every entrance
 * animation has finished, so none of them re-times mid-flight. */
export function useOpening() {
  const [show, setShow] = useState(PLAYS_THIS_LOAD);
  const [settled, setSettled] = useState(!PLAYS_THIS_LOAD);
  const stop = useCallback(() => setShow(false), []);
  useEffect(() => {
    if (settled) return undefined;
    const t = setTimeout(() => setSettled(true), 3400);
    return () => clearTimeout(t);
  }, [settled]);
  return [show, stop, !settled];
}

/** 1.8 s: a ring draws, the core ignites, then the console rises in
 * underneath (Orbit delays its entrance while this plays). Any key or
 * click skips it. */
export default function Opening({ show, onDone }) {

  useEffect(() => {
    if (!show) return undefined;
    const done = setTimeout(onDone, 1900);
    const skip = () => onDone();
    window.addEventListener('keydown', skip, { once: true });
    window.addEventListener('pointerdown', skip, { once: true });
    return () => {
      clearTimeout(done);
      window.removeEventListener('keydown', skip);
      window.removeEventListener('pointerdown', skip);
    };
  }, [show, onDone]);

  if (!show) return null;
  return (
    <div className="opening" aria-hidden>
      <svg viewBox="0 0 200 200">
        <defs>
          <radialGradient id="opening-core" cx="50%" cy="58%" r="50%">
            <stop offset="0" stopColor="#FFF4E8" />
            <stop offset="0.25" stopColor="#FFB38A" />
            <stop offset="0.6" stopColor="#FF6A2B" />
            <stop offset="1" stopColor="#3D1706" />
          </radialGradient>
          <radialGradient id="opening-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0" stopColor="rgba(255,106,43,.55)" />
            <stop offset="1" stopColor="rgba(255,106,43,0)" />
          </radialGradient>
        </defs>
        <circle className="opening__ticks" cx="100" cy="100" r="86" />
        <circle className="opening__ring" cx="100" cy="100" r="70" />
        <g className="opening__core">
          <circle cx="100" cy="100" r="80" fill="url(#opening-glow)" />
          <circle cx="100" cy="100" r="40" fill="url(#opening-core)" />
        </g>
      </svg>
    </div>
  );
}

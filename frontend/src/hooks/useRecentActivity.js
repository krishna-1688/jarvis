import { useEffect, useRef, useState } from 'react';
import { useWebSocket } from './useWebSocket.js';

/**
 * True whenever any non-ping WS event has arrived within the last
 * `windowMs` — a cheap "is the backend doing something right now"
 * signal for ambient effects (chrome spine flow speed, chassis
 * lighting) that don't need to know exactly what happened, just that
 * something did.
 */
export function useRecentActivity(windowMs = 2000) {
  const [active, setActive] = useState(false);
  const timerRef = useRef(null);

  useWebSocket((msg) => {
    if (msg.type === 'ping') return;
    setActive(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setActive(false), windowMs);
  });

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return active;
}

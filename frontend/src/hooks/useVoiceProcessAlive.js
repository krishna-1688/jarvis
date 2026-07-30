import { useEffect, useRef, useState } from 'react';
import { useWebSocket } from './useWebSocket.js';

// Generous vs. jarvis.py's ~8s heartbeat interval (core/jarvis.py's
// HEARTBEAT_INTERVAL_S) — tolerates one missed beat without flapping.
const HEARTBEAT_TIMEOUT_MS = 20000;

/**
 * True once we've seen a voice_heartbeat (or any real voice event —
 * either proves the voice loop is alive) within the last
 * HEARTBEAT_TIMEOUT_MS. Starts false and stays false until the first
 * event arrives, so it never falsely claims "alive" before jarvis.py
 * has had a chance to say so.
 *
 * This exists because the WS connection itself can be perfectly healthy
 * (server.py up, /stream accepting connections) while jarvis.py — a
 * separate process that owns the actual mic/wake-word/STT loop — simply
 * isn't running. That was the exact failure mode found diagnosing "voice
 * doesn't work in the frontend": nothing was wrong with the wiring, the
 * voice process just wasn't started.
 */
export function useVoiceProcessAlive() {
  const [alive, setAlive] = useState(false);
  const timerRef = useRef(null);

  useWebSocket((msg) => {
    if (msg.type === 'ping') return;
    setAlive(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setAlive(false), HEARTBEAT_TIMEOUT_MS);
  });

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return alive;
}

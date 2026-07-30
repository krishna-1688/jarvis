import { useEffect, useRef, useState } from 'react';
import { useSpring } from '../../hooks/useSpring.js';
import { useMockAmplitude } from './useMockAmplitude.js';
import { playWakeClick, playErrorTick } from '../../lib/sound.js';
import './VUMeter.css';

const SEGMENTS = 32;
const HEARTBEAT_CYCLE_MS = 4000;
const ERROR_FLASH_MS = 260;

/**
 * The signature element (Section 1.2). `amplitude` is an optional
 * controlled [0,1] value — pass it once the WebSocket is wired (Task 3.9)
 * and the internal mock generator is skipped automatically.
 *
 * @param {{ state: 'idle'|'wake'|'listening'|'thinking'|'speaking'|'error', amplitude?: number }} props
 */
export default function VUMeter({ state = 'idle', amplitude }) {
  // Falls back to the mock generator whenever no controlled amplitude is
  // passed in — Task 3.9 wires the real value without touching this file.
  const mockAmplitude = useMockAmplitude(state);
  const rawAmplitude  = amplitude ?? mockAmplitude;

  const [heartbeatPhase, setHeartbeatPhase] = useState(0);
  const [flashing, setFlashing] = useState(false);
  const prevStateRef = useRef(state);

  // Sound design (Section 1.6) — one-shot per state transition, not per render.
  useEffect(() => {
    if (state === prevStateRef.current) return;
    if (state === 'wake') playWakeClick();
    if (state === 'error') playErrorTick();
    prevStateRef.current = state;
  }, [state]);

  // Idle heartbeat: one faint segment drifting left<->right, ~4s cycle.
  useEffect(() => {
    if (state !== 'idle') return undefined;
    let raf;
    const start = performance.now();
    const tick = (now) => {
      const t = ((now - start) % HEARTBEAT_CYCLE_MS) / HEARTBEAT_CYCLE_MS;
      const phase = t < 0.5 ? t * 2 : (1 - t) * 2; // triangle wave 0 -> 1 -> 0
      setHeartbeatPhase(phase);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  // Error: one-shot red flash across every segment, then decay to idle.
  useEffect(() => {
    if (state !== 'error') {
      setFlashing(false);
      return undefined;
    }
    setFlashing(true);
    const t = setTimeout(() => setFlashing(false), ERROR_FLASH_MS);
    return () => clearTimeout(t);
  }, [state]);

  const target   = flashing ? 1 : (state === 'idle' || state === 'error') ? 0 : rawAmplitude;
  const springed = useSpring(target);
  const lit       = Math.round(springed * SEGMENTS);
  const heartbeatIndex = Math.round(heartbeatPhase * (SEGMENTS - 1));
  const tone = state === 'speaking' ? 'signal' : 'amber';

  return (
    <div className={`vu-meter vu-meter--${tone} ${flashing ? 'vu-meter--flash' : ''}`}>
      {Array.from({ length: SEGMENTS }, (_, i) => (
        <span
          key={i}
          style={{ '--i': i }}
          className={[
            'vu-meter__seg',
            i < lit && 'is-lit',
            state === 'idle' && i === heartbeatIndex && 'is-heartbeat',
          ].filter(Boolean).join(' ')}
        />
      ))}
    </div>
  );
}

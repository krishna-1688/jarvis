import { useEffect, useRef, useState } from 'react';

const REDUCED_MOTION = typeof window !== 'undefined'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Mass-spring-damper integrator driving a single number toward `target`.
 * Runs one continuous rAF loop for the component's lifetime (reading
 * `target` from a ref each frame) rather than restarting on every change,
 * so fast-changing targets (VU amplitude, gauge needles) stay smooth.
 * stiffness=1400/damping=44 (the defaults) settle in ~180ms with a slight
 * overshoot, matching Section 1.1's spring spec.
 */
export function useSpring(target, { stiffness = 1400, damping = 44 } = {}) {
  const [display, setDisplay] = useState(target);
  const targetRef = useRef(target);
  const valueRef  = useRef(target);
  const velRef    = useRef(0);

  useEffect(() => { targetRef.current = target; }, [target]);

  useEffect(() => {
    if (REDUCED_MOTION) {
      setDisplay(targetRef.current);
    }
  }, [target]);

  useEffect(() => {
    if (REDUCED_MOTION) return undefined;

    let raf;
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      const accel = (targetRef.current - valueRef.current) * stiffness - velRef.current * damping;
      velRef.current += accel * dt;
      valueRef.current += velRef.current * dt;
      setDisplay(valueRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [stiffness, damping]);

  return display;
}

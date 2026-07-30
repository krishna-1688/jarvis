import { useEffect, useRef, useState } from 'react';
import { createAmplitudeGenerator } from '../../mock.js';

/** Drives a live [0,1] amplitude value for `state` off the mock generator. */
export function useMockAmplitude(state) {
  const [amplitude, setAmplitude] = useState(0);
  const genRef = useRef(null);
  if (genRef.current === null) genRef.current = createAmplitudeGenerator();

  useEffect(() => {
    let raf;
    const tick = (now) => {
      setAmplitude(genRef.current(state, now));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  return amplitude;
}

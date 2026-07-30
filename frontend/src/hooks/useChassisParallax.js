import { useEffect, useRef, useState } from 'react';

const REDUCED_MOTION = typeof window !== 'undefined'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * V.2b: a smoothed {x,y} offset (small px range) that follows cursor
 * position relative to the viewport center, lagging behind by design
 * (lerped, not instant) — a subtle depth cue for the rack to shift
 * against the fixed chassis frame (top rail stays put), not a gimmick.
 * Returns {x:0,y:0} under prefers-reduced-motion.
 */
export function useChassisParallax(range = 4) {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const targetRef = useRef({ x: 0, y: 0 });
  const currentRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    if (REDUCED_MOTION) return undefined;

    const onMove = (e) => {
      const nx = (e.clientX / window.innerWidth) * 2 - 1;
      const ny = (e.clientY / window.innerHeight) * 2 - 1;
      targetRef.current = { x: nx * range, y: ny * range };
    };
    window.addEventListener('mousemove', onMove);

    let raf;
    const tick = () => {
      const cur = currentRef.current;
      const tgt = targetRef.current;
      cur.x += (tgt.x - cur.x) * 0.08;
      cur.y += (tgt.y - cur.y) * 0.08;
      setOffset({ x: cur.x, y: cur.y });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener('mousemove', onMove);
      cancelAnimationFrame(raf);
    };
  }, [range]);

  return offset;
}

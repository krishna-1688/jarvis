import { useEffect, useState } from 'react';
import { playBootClunk } from '../../lib/sound.js';
import './BootSequence.css';

const REDUCED_MOTION = typeof window !== 'undefined'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Long enough for every [data-booting]-keyed entrance animation across
// TopRail/Rack/VUCoreModule to finish (see their CSS) before the
// attribute is removed and things settle into normal steady-state.
const BOOT_DURATION_MS = 900;
const OVERLAY_UNMOUNT_MS = 400;

/**
 * V.2a: plays once per app launch (mounted once at the Console root).
 * A fixed black overlay fades away while document.body carries a
 * data-booting attribute that the rest of the chassis keys its own
 * entrance animation off of — see TopRail.css / Rack.css / VUMeter.css
 * for the [data-booting] selectors. Skips straight to the settled state
 * under prefers-reduced-motion — no flash, no stagger.
 */
export default function BootSequence() {
  const [mounted, setMounted] = useState(!REDUCED_MOTION);

  useEffect(() => {
    if (REDUCED_MOTION) return undefined;
    document.body.setAttribute('data-booting', 'true');
    playBootClunk();
    const unmountTimer = setTimeout(() => setMounted(false), OVERLAY_UNMOUNT_MS);
    const doneTimer = setTimeout(() => document.body.removeAttribute('data-booting'), BOOT_DURATION_MS);
    return () => {
      clearTimeout(unmountTimer);
      clearTimeout(doneTimer);
      document.body.removeAttribute('data-booting');
    };
  }, []);

  if (!mounted) return null;
  return <div className="boot-sequence-overlay" aria-hidden="true" />;
}

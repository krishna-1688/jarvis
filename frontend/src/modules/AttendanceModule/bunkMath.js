const THRESHOLD = 0.75;

/** Mirrors the backend's bunk-calculator logic (features/vtop.py bunk_analysis). */
export function bunkInfo(attended, total) {
  if (total <= 0) return { canSkip: 0, needed: 0, safe: true };

  if (attended / total >= THRESHOLD) {
    const canSkip = Math.max(0, Math.floor(attended / THRESHOLD - total));
    return { canSkip, needed: 0, safe: true };
  }

  let a = attended;
  let t = total;
  let needed = 0;
  while (t > 0 && a / t < THRESHOLD) {
    a += 1;
    t += 1;
    needed += 1;
  }
  return { canSkip: 0, needed, safe: false };
}

import { useEffect, useState } from 'react';
import { useSpring } from '../../hooks/useSpring.js';
import SegmentedDisplay from '../../components/SegmentedDisplay/SegmentedDisplay.jsx';
import { bunkInfo } from './bunkMath.js';
import './AttendanceGauge.css';

const CX = 50;
const CY = 54;
const R = 44;

const REDUCED_MOTION = typeof window !== 'undefined'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const DRIFT_PERIOD_MS = 6000;
const DRIFT_PCT = 0.17; // ~0.3deg of needle swing on this gauge's 180deg scale

/** V.2b: real analog instruments never sit perfectly still — a tiny
 * continuous needle drift kills the "fake"/static feeling a
 * dead-still needle gives away. */
function useIdleDrift() {
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    if (REDUCED_MOTION) return undefined;
    let raf;
    const start = performance.now();
    const tick = (now) => {
      setPhase(((now - start) % DRIFT_PERIOD_MS) / DRIFT_PERIOD_MS);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return REDUCED_MOTION ? 0 : Math.sin(phase * 2 * Math.PI) * DRIFT_PCT;
}

/** 0% -> 180deg (left), 50% -> 90deg (top), 100% -> 0deg (right). */
function pointAt(pct, radius = R) {
  const angle = Math.PI * (1 - pct / 100);
  return { x: CX + radius * Math.cos(angle), y: CY - radius * Math.sin(angle) };
}

function describeArc(pctStart, pctEnd, radius = R) {
  const start = pointAt(pctStart, radius);
  const end = pointAt(pctEnd, radius);
  const largeArc = pctEnd - pctStart > 50 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

/**
 * SVG needle gauge for one subject (Section 1.5). `inline` shows the
 * attended/total + bunk-headroom line permanently (expanded 5×6 variant);
 * otherwise it only appears on hover (compact 3×4 variant).
 */
export default function AttendanceGauge({ subject, inline = false }) {
  const { name, code, attended, total, percentage } = subject;
  const sprungPct = useSpring(percentage, { stiffness: 260, damping: 22 });
  const drift = useIdleDrift();
  const needleTip = pointAt(Math.max(0, Math.min(100, sprungPct + drift)), R - 10);
  const belowThreshold = percentage < 75;
  const { canSkip, needed, safe } = bunkInfo(attended, total);

  return (
    <div className="attendance-gauge">
      <div className="attendance-gauge__name mono" title={name}>{code}</div>
      <svg className="attendance-gauge__svg" viewBox="0 0 100 62">
        <path d={describeArc(0, 100)} className="attendance-gauge__track" />
        <path d={describeArc(0, 75)} className="attendance-gauge__redzone" />
        <line
          x1={CX} y1={CY} x2={needleTip.x} y2={needleTip.y}
          className={`attendance-gauge__needle ${belowThreshold ? 'is-warning' : ''}`}
        />
        <circle cx={CX} cy={CY} r={3.2} className="attendance-gauge__pivot" />
      </svg>
      <SegmentedDisplay value={`${percentage.toFixed(1)}%`} size="sm" tone={belowThreshold ? 'signal' : 'amber'} />

      <div className={`attendance-gauge__detail ${inline ? 'is-inline' : 'is-hover'}`}>
        <span className="mono">{attended}/{total}</span>
        <span className={`mono ${safe ? '' : 'is-warning-text'}`}>
          {safe ? `can skip ${canSkip} more` : `need ${needed} more`}
        </span>
      </div>
    </div>
  );
}

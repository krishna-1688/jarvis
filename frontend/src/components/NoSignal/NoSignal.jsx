import SegmentedDisplay from '../SegmentedDisplay/SegmentedDisplay.jsx';
import './NoSignal.css';

/**
 * Degraded state when the backend is unreachable (Section 1.4/3.9):
 * modules never disappear or show error popups, just this + auto-retry.
 */
export default function NoSignal() {
  return (
    <div className="no-signal">
      <SegmentedDisplay value="- - -" size="md" tone="amber" />
      <span className="no-signal__label">NO SIGNAL · retry</span>
    </div>
  );
}

import './StatusBanner.css';

/**
 * S.1c: turns a silent WS/voice failure into a visible, actionable
 * message instead of a lone orange dot nobody notices. Dismissable —
 * reappears if the underlying condition (passed in by the caller via
 * `visible`) becomes true again, since dismissal only hides THIS
 * occurrence, not the class of problem.
 */
export default function StatusBanner({ visible, text, onDismiss }) {
  if (!visible) return null;
  return (
    <div className="status-banner" role="status">
      <span className="status-banner__text mono">{text}</span>
      <button className="status-banner__dismiss" onClick={onDismiss} title="Dismiss">✕</button>
    </div>
  );
}

import { useRecentActivity } from '../../hooks/useRecentActivity.js';
import './ChromeSpine.css';

/**
 * V.2d: the console's signature element — a vertical channel milled
 * into the chassis (mercury-thermometer feel, not a loading bar) with
 * amber "fluid" traveling down it: one slow pulse every 6s at idle,
 * near-continuous flow while the backend is doing something. Purely
 * decorative (aria-hidden, pointer-events:none) — sits behind the rack
 * modules, so it never fights drag/resize or looks broken if the user
 * rearranges the layout away from the default two-column split it's
 * roughly aligned to.
 */
export default function ChromeSpine() {
  const active = useRecentActivity(2500);
  return (
    <div className={`chrome-spine ${active ? 'is-active' : ''}`} aria-hidden="true">
      <div className="chrome-spine__channel">
        <div className="chrome-spine__flow" />
      </div>
    </div>
  );
}

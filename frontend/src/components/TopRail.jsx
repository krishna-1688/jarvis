import { useEffect, useState } from 'react';
import { isMuted, toggleMuted } from '../lib/sound.js';
import { useWebSocketStatus } from '../hooks/useWebSocket.js';
import './TopRail.css';

const DAYS   = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const BRAND  = 'JARVIS';

function formatClock(d) {
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return { hh, mm };
}

function formatDate(d) {
  return `${DAYS[d.getDay()]} ${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]}`;
}

/**
 * @param {{
 *   windowName: 'console'|'widget',
 *   theme: 'light'|'dark',
 *   onToggleTheme: () => void,
 *   editMode?: boolean,
 *   onToggleEdit?: () => void,
 *   showEdit?: boolean,
 *   health?: { backend: 'ok'|'warn'|'pending', whatsapp: 'ok'|'warn'|'pending', vtop: 'ok'|'warn'|'pending' },
 * }} props
 */
export default function TopRail({
  windowName, theme, onToggleTheme,
  editMode = false, onToggleEdit, showEdit = true,
  health = { backend: 'pending', whatsapp: 'pending', vtop: 'pending' },
}) {
  const [now, setNow] = useState(() => new Date());
  const [muted, setMutedState] = useState(() => isMuted());
  const wsStatus = useWebSocketStatus();

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const handleControl = (action) => window.jarvis?.windowControl(windowName, action);
  const handleToggleMute = () => setMutedState(toggleMuted());

  return (
    <div className="top-rail drag-region">
      <div className="top-rail__brand">
        <span className="top-rail__word">
          {BRAND.split('').map((ch, i) => (
            <span key={i} className="top-rail__letter" style={{ '--i': i }}>{ch}</span>
          ))}
        </span>
        <span className="top-rail__blocks">▮▮</span>
        <span
          className={`top-rail__ws-pip top-rail__ws-pip--${wsStatus}`}
          title={`Voice link: ${wsStatus}`}
        />
      </div>

      <div className="top-rail__center">
        <span className="top-rail__date mono">{formatDate(now)}</span>
        <span className="top-rail__clock mono">
          {formatClock(now).hh}<span className="top-rail__colon">:</span>{formatClock(now).mm}
        </span>
        <div className="top-rail__chips">
          <span className={`chip chip--${health.backend}`}>BACKEND</span>
          <span className={`chip chip--${health.whatsapp}`}>WHATSAPP</span>
          <span className={`chip chip--${health.vtop}`}>VTOP</span>
        </div>
      </div>

      <div className="top-rail__controls no-drag">
        {showEdit && (
          <button
            className={`top-rail__btn ${editMode ? 'is-active' : ''}`}
            onClick={onToggleEdit}
            title="Edit layout (Ctrl+E)"
          >
            ⚙
          </button>
        )}
        <button className="top-rail__btn" onClick={onToggleTheme} title="Toggle theme">
          {theme === 'dark' ? '☾' : '☀'}
        </button>
        <button className="top-rail__btn" onClick={handleToggleMute} title={muted ? 'Unmute sounds' : 'Mute sounds'}>
          {muted ? '◇' : '◆'}
        </button>
        <button className="top-rail__btn" onClick={() => handleControl('minimize')} title="Minimize">–</button>
        <button
          className="top-rail__btn top-rail__btn--close"
          onClick={() => handleControl('close')}
          title="Close"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

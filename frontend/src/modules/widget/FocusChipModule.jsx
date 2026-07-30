import { useEffect, useState } from 'react';
import { useApiData } from '../../hooks/useApiData.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { api } from '../../api.js';
import { USE_MOCK } from '../../mock.js';
import './FocusChipModule.css';

/**
 * Widget thin variant (Section 4.4d): a recording-style dot + remaining
 * time when a focus session is active, blank otherwise. A filled dot
 * instead of an emoji — stays inside the no-emoji, no-looping-animation
 * rules the rest of the instrument holds to (Section 1.1).
 *
 * @param {{ mockActive?: { remainingMinutes: number } | null }} props
 */
export default function FocusChipModule({ mockActive = null }) {
  const [active, setActive] = useState(mockActive);
  const { data } = useApiData(api.focusStatus, { pollMs: 60000 });

  useEffect(() => {
    if (USE_MOCK || !data) return;
    setActive(data.active ? { remainingMinutes: Math.max(0, Math.round(data.remaining_minutes ?? 0)) } : null);
  }, [data]);

  useWebSocket((msg) => {
    if (USE_MOCK) return;
    if (msg.type === 'focus_started') setActive({ remainingMinutes: msg.planned_minutes });
    else if (msg.type === 'pomodoro_tick') setActive({ remainingMinutes: Math.ceil(msg.remaining_seconds / 60) });
    else if (msg.type === 'focus_ended') setActive(null);
  });

  if (!active) return <div className="focus-chip" />;

  return (
    <div className="focus-chip mono">
      <span className="focus-chip__dot" aria-hidden="true" />
      <span className="focus-chip__label">FOCUS {active.remainingMinutes}m</span>
    </div>
  );
}

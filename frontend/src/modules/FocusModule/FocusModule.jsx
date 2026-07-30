import { useEffect, useState } from 'react';
import SegmentedDisplay from '../../components/SegmentedDisplay/SegmentedDisplay.jsx';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_FOCUS_STATS } from '../../mock.js';
import './FocusModule.css';

const WORK_MINUTES = 25;

function formatMMSS(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/**
 * Registered as `focus` (Section 4.4c). GET /focus/status seeds the
 * initial state (in case a session was already running before the UI
 * loaded); live updates after that come from the WS events focus_started
 * / pomodoro_tick / pomodoro_transition / focus_ended.
 *
 * @param {{ mockActive?: { subject: string, phase: 'work'|'break', remainingSeconds: number, pomodorosCompleted: number, plannedMinutes: number } | null }} props
 */
export default function FocusModule({ mockActive = null }) {
  const [active, setActive] = useState(mockActive);
  const { data, error } = useApiData(api.focusStatus, { pollMs: 60000 });
  const { data: statsData, error: statsError } = useApiData(api.focusStats, { pollMs: 300000 });

  useEffect(() => {
    if (USE_MOCK || !data) return;
    if (!data.active) {
      setActive(null);
      return;
    }
    setActive({
      subject: data.active.subject,
      phase: 'work',
      remainingSeconds: (data.remaining_minutes ?? 0) * 60,
      pomodorosCompleted: data.active.pomodoros_completed,
      plannedMinutes: data.active.planned_minutes,
    });
  }, [data]);

  useWebSocket((msg) => {
    if (USE_MOCK) return;
    if (msg.type === 'focus_started') {
      setActive({
        subject: msg.subject, phase: 'work', remainingSeconds: msg.planned_minutes * 60,
        pomodorosCompleted: 0, plannedMinutes: msg.planned_minutes,
      });
    } else if (msg.type === 'pomodoro_tick') {
      setActive((prev) => (prev ? { ...prev, phase: msg.phase, remainingSeconds: msg.remaining_seconds } : prev));
    } else if (msg.type === 'pomodoro_transition') {
      setActive((prev) => (prev
        ? { ...prev, phase: msg.to, pomodorosCompleted: msg.from === 'work' ? prev.pomodorosCompleted + 1 : prev.pomodorosCompleted }
        : prev));
    } else if (msg.type === 'focus_ended') {
      setActive(null);
    }
  });

  const handleStop = () => {
    setActive(null);
    if (!USE_MOCK) api.command('stop focus').catch(() => {});
  };

  const focusStats = USE_MOCK ? MOCK_FOCUS_STATS : (statsData?.stats ?? []);
  const maxBar = Math.max(1, ...focusStats.map((s) => s.total_minutes));

  // H.6: a fetch failure with nothing already known must read as NO
  // SIGNAL, not as "no active focus session" — those are different
  // facts and showing the wrong one is misleading.
  if (!USE_MOCK && error && !active && (statsError || focusStats.length === 0)) {
    return <NoSignal />;
  }

  return (
    <div className="focus-module">
      <div className="focus-module__main">
        {!active ? (
          <div className="focus-module__empty">
            <span className="mono focus-module__empty-dash">- - -</span>
            <span className="focus-module__empty-label">NO ACTIVE FOCUS</span>
            <span className="mono focus-module__empty-hint">say: focus DBMS 2 hours</span>
          </div>
        ) : (
          <>
            <div className="focus-module__subject mono">{active.subject}</div>
            <div className="focus-module__countdown">
              <SegmentedDisplay
                value={formatMMSS(active.remainingSeconds)}
                size="lg"
                tone={active.phase === 'break' ? 'safe' : 'signal'}
              />
              <span className="focus-module__phase">{active.phase.toUpperCase()}</span>
            </div>
            <div className="focus-module__progress">
              {Array.from({ length: Math.ceil(active.plannedMinutes / WORK_MINUTES) }, (_, i) => (
                <span key={i} className={`focus-module__seg ${i < active.pomodorosCompleted ? 'is-lit' : ''}`} />
              ))}
            </div>
            <button className="focus-module__stop" onClick={handleStop}>STOP</button>
          </>
        )}
      </div>

      <div className="focus-module__stats">
        {focusStats.map((s) => (
          <div key={s.subject} className="focus-module__bar-row">
            <span className="focus-module__bar-label mono">{s.subject}</span>
            <div className="focus-module__bar-track">
              <div className="focus-module__bar-fill" style={{ width: `${(s.total_minutes / maxBar) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

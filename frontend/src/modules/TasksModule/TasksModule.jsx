import { useMemo, useState } from 'react';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_TASKS } from '../../mock.js';
import './TasksModule.css';

function dueInfo(dueAt) {
  if (!dueAt) return null;
  const due = new Date(dueAt);
  const now = new Date();
  const diffDays = Math.ceil((due - now) / 86400000);
  if (diffDays < 0) return { text: `${Math.abs(diffDays)}d overdue`, tone: 'signal' };
  if (diffDays === 0) return { text: 'today', tone: 'signal' };
  if (diffDays === 1) return { text: 'tomorrow', tone: 'amber' };
  return { text: due.toLocaleDateString('en-US', { weekday: 'short' }), tone: 'dim' };
}

/**
 * Registered as `tasks`: 3x4 compact (top 5 due-soonest), 5x6 expanded
 * (all open + a done toggle). The add-task input is always visible in
 * both sizes. Clicking a row completes it — optimistic locally, confirmed
 * via POST /command against the real task_complete intent (features/
 * tasks.py's fuzzy match resolves which task by title).
 */
export default function TasksModule({ w }) {
  const expanded = w >= 5;
  // refreshOn: 'tasks' — the backend broadcasts data_refreshed/tasks the
  // moment a task is added, from EITHER this module's own input below or
  // a voice command ("remind me to..."), so a voice-added task shows up
  // here immediately instead of waiting up to 30s for the next poll.
  const { data, error, loading: fetchLoading } = useApiData(api.tasks, { pollMs: 30000, refreshOn: 'tasks' });
  const [completed, setCompleted] = useState(() => new Set());
  const [showDone, setShowDone] = useState(false);
  const [draft, setDraft] = useState('');
  const [pendingAdd, setPendingAdd] = useState(false);

  const sorted = useMemo(() => {
    const list = USE_MOCK ? MOCK_TASKS : (data?.tasks ?? []);
    return [...list].sort((a, b) => {
      if (!a.due_at) return 1;
      if (!b.due_at) return -1;
      return new Date(a.due_at) - new Date(b.due_at);
    });
  }, [data]);

  if (error && sorted.length === 0 && !fetchLoading) return <NoSignal />;

  const visible = sorted.filter((t) => showDone || !completed.has(t.id));
  const shown = expanded ? visible : visible.slice(0, 5);

  const handleComplete = (task) => {
    setCompleted((prev) => {
      const next = new Set(prev);
      next.has(task.id) ? next.delete(task.id) : next.add(task.id);
      return next;
    });
    if (!USE_MOCK) {
      api.command(`mark ${task.title} as done`).catch(() => {});
    }
  };

  const handleAddSubmit = async (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    if (!USE_MOCK) {
      setPendingAdd(true);
      try {
        // Same task_add intent a voice "remind me to..." hits — the
        // backend broadcasts data_refreshed/tasks on success, which
        // useApiData's refreshOn above picks up, so this list updates
        // itself rather than needing a local optimistic insert here.
        await api.command(`remind me to ${text}`);
      } catch {
        // NoSignal/retry loop on the next poll surfaces persistent failures
      } finally {
        setPendingAdd(false);
      }
    }
  };

  return (
    <div className={`tasks-module ${expanded ? 'tasks-module--expanded' : 'tasks-module--compact'}`}>
      {expanded && (
        <div className="tasks-module__toolbar">
          <button className="tasks-module__toolbar-btn" onClick={() => setShowDone((v) => !v)}>
            {showDone ? 'HIDE DONE' : 'SHOW DONE'}
          </button>
        </div>
      )}
      {/* Always visible, in both compact and expanded — previously this sat
          behind a "+ ADD" toggle button (and only existed at all in the
          expanded layout), so typing a task directly took an extra click
          and wasn't even possible in the compact size. */}
      <form className="tasks-module__add-form" onSubmit={handleAddSubmit}>
        <input
          className="tasks-module__add-input mono"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="remind me to…"
          disabled={pendingAdd}
        />
      </form>
      <div className="tasks-module__list">
        {shown.length === 0 && <div className="tasks-module__empty mono">- - -  NO OPEN TASKS</div>}
        {shown.map((t) => {
          const due = dueInfo(t.due_at);
          const done = completed.has(t.id);
          return (
            <div
              key={t.id}
              className={`task-row ${done ? 'is-done' : ''}`}
              onClick={() => handleComplete(t)}
            >
              <span className={`task-row__due mono ${due ? `is-${due.tone}` : 'is-none'}`}>
                {due ? due.text : '—'}
              </span>
              <span className="task-row__title">{t.title}</span>
              {t.priority === 'high' && !done && <span className="task-row__priority" aria-hidden="true" />}
              {t.tag && <span className="task-row__tag mono">{t.tag}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

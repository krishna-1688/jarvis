import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_TASKS } from '../../mock.js';
import './NextTaskModule.css';

function relativeLabel(dueAt) {
  if (!dueAt) return '';
  const diffDays = Math.ceil((new Date(dueAt) - new Date()) / 86400000);
  if (diffDays < 0) return `${Math.abs(diffDays)}d overdue`;
  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'tomorrow';
  return `${diffDays}d`;
}

/** Widget thin variant (Section 4.4d): next-due open task + relative time. */
export default function NextTaskModule() {
  const { data } = useApiData(api.tasks, { pollMs: 30000 });
  const tasks = USE_MOCK ? MOCK_TASKS : (data?.tasks ?? []);
  const withDue = tasks.filter((t) => t.due_at).sort((a, b) => new Date(a.due_at) - new Date(b.due_at));
  const next = withDue[0];

  if (!next) return <div className="next-task mono">NO TASKS</div>;

  return (
    <div className="next-task mono" title={next.title}>
      <span className="next-task__title">{next.title}</span>
      <span className="next-task__when">{relativeLabel(next.due_at)}</span>
    </div>
  );
}

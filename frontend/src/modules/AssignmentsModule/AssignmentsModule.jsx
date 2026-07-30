import { useMemo } from 'react';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_ASSIGNMENTS } from '../../mock.js';
import './AssignmentsModule.css';

function normalizeAssignment(a) {
  return { id: a.id, title: a.title, course: a.course_name, due: a.due_date, submitted: a.status === 'submitted' };
}

function dueLabel(dueStr) {
  const diffMs = new Date(dueStr).getTime() - Date.now();
  if (diffMs < 0) {
    const hoursOverdue = Math.ceil(-diffMs / 3600000);
    return hoursOverdue < 24 ? `${hoursOverdue}h overdue` : `${Math.ceil(hoursOverdue / 24)}d overdue`;
  }
  const daysLeft = Math.ceil(diffMs / 86400000);
  return daysLeft === 0 ? 'due today' : `${daysLeft}d left`;
}

/** Registered as `assignments` (Section 1.5): due-soonest first in both variants. */
export default function AssignmentsModule({ w }) {
  const horizontal = w >= 6;
  const { data, error } = useApiData(api.assignments, { pollMs: 60000 });

  const sorted = useMemo(() => {
    const list = USE_MOCK ? MOCK_ASSIGNMENTS : (data?.pending ?? []).map(normalizeAssignment);
    return [...list].sort((a, b) => new Date(a.due) - new Date(b.due));
  }, [data]);

  if (error && sorted.length === 0) return <NoSignal />;

  return (
    <div className={`assignments-module ${horizontal ? 'assignments-module--horizontal' : 'assignments-module--vertical'}`}>
      {sorted.map((a) => {
        const overdue = !a.submitted && new Date(a.due).getTime() < Date.now();
        return (
          <div key={a.id} className={`assignment-card ${overdue ? 'is-overdue' : ''}`}>
            <div className="assignment-card__title">{a.title}</div>
            <div className="assignment-card__meta mono">
              <span>{a.course}</span>
              {a.submitted ? (
                <span className="chip chip--ok">SUBMITTED</span>
              ) : (
                <span className={overdue ? 'is-overdue-text' : ''}>{dueLabel(a.due)}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

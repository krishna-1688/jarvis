import { useMemo } from 'react';
import AttendanceGauge from './AttendanceGauge.jsx';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_ATTENDANCE } from '../../mock.js';
import './AttendanceModule.css';

function normalizeRow(r) {
  return { code: r.course_code, name: r.course_name, attended: r.attended_classes, total: r.total_classes, percentage: r.percentage };
}

/**
 * Registered as `attendance` (Section 1.4): 3×4 compact shows the worst 3
 * subjects with bunk-headroom on hover; 5×6 expanded shows every subject
 * with bunk-headroom always visible. Worst-first in both.
 */
export default function AttendanceModule({ w }) {
  const expanded = w >= 5;
  const { data, error } = useApiData(api.attendance, { pollMs: 60000 });

  const subjects = useMemo(() => {
    if (USE_MOCK) return MOCK_ATTENDANCE;
    return (data?.rows ?? []).map(normalizeRow);
  }, [data]);

  const sorted = useMemo(() => [...subjects].sort((a, b) => a.percentage - b.percentage), [subjects]);

  if (error && subjects.length === 0) return <NoSignal />;

  const shown = expanded ? sorted : sorted.slice(0, 3);

  return (
    <div className={`attendance-module ${expanded ? 'attendance-module--expanded' : 'attendance-module--compact'}`}>
      {shown.map((s) => (
        <AttendanceGauge key={s.code} subject={s} inline={expanded} />
      ))}
    </div>
  );
}

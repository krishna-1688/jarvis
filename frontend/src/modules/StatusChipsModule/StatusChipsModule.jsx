import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_ATTENDANCE, MOCK_ASSIGNMENTS } from '../../mock.js';
import './StatusChipsModule.css';

/** Registered as `status-chips` (Section 1.4): the same summary the Widget shows. */
export default function StatusChipsModule() {
  const { data: attendanceData } = useApiData(api.attendance, { pollMs: 60000 });
  const { data: assignmentsData } = useApiData(api.assignments, { pollMs: 60000 });

  const attendance = USE_MOCK
    ? MOCK_ATTENDANCE
    : (attendanceData?.rows ?? []).map((r) => ({ percentage: r.percentage }));
  const pendingCount = USE_MOCK
    ? MOCK_ASSIGNMENTS.filter((a) => !a.submitted).length
    : (assignmentsData?.pending ?? []).length;

  if (attendance.length === 0) {
    return (
      <div className="status-chips">
        <span className="chip chip--pending">ATT --</span>
        <span className="chip chip--pending">-- DUE</span>
      </div>
    );
  }

  const worstPct = attendance.reduce((min, s) => Math.min(min, s.percentage), 100);

  return (
    <div className="status-chips">
      <span className={`chip ${worstPct < 75 ? 'chip--warn' : 'chip--ok'}`}>ATT {worstPct.toFixed(0)}%</span>
      <span className={`chip ${pendingCount > 0 ? 'chip--warn' : 'chip--ok'}`}>{pendingCount} DUE</span>
    </div>
  );
}

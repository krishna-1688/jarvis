import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_ATTENDANCE, MOCK_ASSIGNMENTS } from '../../mock.js';
import './ThinChipsModule.css';

/**
 * Widget's status chips at 1 column wide — too narrow for StatusChipsModule's
 * side-by-side layout, so these stack instead.
 */
export default function ThinChipsModule() {
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
      <div className="thin-chips">
        <span className="chip chip--pending">--%</span>
        <span className="chip chip--pending">--D</span>
      </div>
    );
  }

  const worstPct = attendance.reduce((min, s) => Math.min(min, s.percentage), 100);

  return (
    <div className="thin-chips">
      <span className={`chip ${worstPct < 75 ? 'chip--warn' : 'chip--ok'}`}>{worstPct.toFixed(0)}%</span>
      <span className={`chip ${pendingCount > 0 ? 'chip--warn' : 'chip--ok'}`}>{pendingCount}D</span>
    </div>
  );
}

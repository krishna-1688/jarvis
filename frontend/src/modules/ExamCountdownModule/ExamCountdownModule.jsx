import { useEffect, useState } from 'react';
import SegmentedDisplay from '../../components/SegmentedDisplay/SegmentedDisplay.jsx';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_EXAMS } from '../../mock.js';
import { nextUpcomingExam } from '../../lib/examMath.js';
import './ExamCountdownModule.css';

function normalizeExam(e) {
  return { code: e.exam_type, name: e.course_name, date: e.exam_date };
}

/** Registered as `exam-countdown` (Section 1.5): large DSEG readout, ticks every minute. */
export default function ExamCountdownModule() {
  const [, forceTick] = useState(0);
  const { data, error } = useApiData(api.exams, { pollMs: 300000 });

  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 60000);
    return () => clearInterval(id);
  }, []);

  const exams = USE_MOCK ? MOCK_EXAMS : (data?.exams ?? []).map(normalizeExam);
  if (error && exams.length === 0) return <NoSignal />;

  const next = nextUpcomingExam(exams);

  if (!next) {
    return (
      <div className="exam-countdown">
        <SegmentedDisplay value="- - -" size="md" tone="amber" />
        <div className="exam-countdown__label mono">NO UPCOMING EXAMS</div>
      </div>
    );
  }

  const urgent = next.remaining.days < 3;

  return (
    <div className="exam-countdown">
      <div className="exam-countdown__row">
        <SegmentedDisplay value={next.code} size="sm" tone={urgent ? 'signal' : 'amber'} />
        <span className="exam-countdown__sep">·</span>
        <SegmentedDisplay
          value={`${next.remaining.days}d ${String(next.remaining.hours).padStart(2, '0')}h`}
          size="md"
          tone={urgent ? 'signal' : 'amber'}
        />
      </div>
      <div className="exam-countdown__label mono">{next.name}</div>
    </div>
  );
}

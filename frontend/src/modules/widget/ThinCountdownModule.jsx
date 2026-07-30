import { useEffect, useState } from 'react';
import SegmentedDisplay from '../../components/SegmentedDisplay/SegmentedDisplay.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_EXAMS } from '../../mock.js';
import { nextUpcomingExam } from '../../lib/examMath.js';

function normalizeExam(e) {
  return { code: e.exam_type, name: e.course_name, date: e.exam_date };
}

/** Widget's compact exam countdown — no room for the exam name, just the number. */
export default function ThinCountdownModule() {
  const [, tick] = useState(0);
  const { data } = useApiData(api.exams, { pollMs: 300000 });

  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 60000);
    return () => clearInterval(id);
  }, []);

  const exams = USE_MOCK ? MOCK_EXAMS : (data?.exams ?? []).map(normalizeExam);
  const next = nextUpcomingExam(exams);
  if (!next) return <SegmentedDisplay value="- - -" size="sm" tone="amber" />;

  return (
    <SegmentedDisplay
      value={`${next.remaining.days}d ${next.remaining.hours}h`}
      size="sm"
      tone={next.remaining.days < 3 ? 'signal' : 'amber'}
    />
  );
}

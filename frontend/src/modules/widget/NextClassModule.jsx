import { useEffect, useState } from 'react';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_TIMETABLE } from '../../mock.js';
import './NextClassModule.css';

function toMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

function normalizeBlock(b) {
  return { code: b.linked_course, start: b.start_at.slice(11, 16), end: b.end_at.slice(11, 16) };
}

/** Widget's `next-class` readout (Section 1.3/1.8). */
export default function NextClassModule() {
  const [now, setNow] = useState(() => new Date());
  const { data } = useApiData(api.schedule, { pollMs: 60000 });

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(id);
  }, []);

  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const classes = USE_MOCK
    ? (MOCK_TIMETABLE[now.getDay()] || [])
    : (data?.blocks ?? []).filter((b) => b.block_type === 'class').map(normalizeBlock);
  const current = classes.find((c) => toMinutes(c.start) <= nowMinutes && nowMinutes < toMinutes(c.end));
  const next = classes.find((c) => toMinutes(c.start) > nowMinutes);

  let text = 'NO CLASSES';
  if (current) text = `${current.code} ${toMinutes(current.end) - nowMinutes}m`;
  else if (next) text = `${next.code} ${toMinutes(next.start) - nowMinutes}m`;

  return (
    <div
      className={`next-class mono ${current ? 'is-current' : ''}`}
      title={current ? `${current.code} — ${toMinutes(current.end) - nowMinutes}m left` : next ? `${next.code} in ${toMinutes(next.start) - nowMinutes}m` : 'No more classes today'}
    >
      {text}
    </div>
  );
}

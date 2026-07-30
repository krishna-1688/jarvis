import { useEffect, useMemo, useRef, useState } from 'react';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_TIMETABLE, MOCK_CUSTOM_BLOCKS_TODAY } from '../../mock.js';
import './ScheduleModule.css';

function normalizeBlock(b) {
  return { code: b.linked_course, name: b.title, start: b.start_at.slice(11, 16), end: b.end_at.slice(11, 16), block_type: b.block_type };
}

const RANGE_START = 8 * 60;  // 08:00
const RANGE_END = 22 * 60;   // 22:00 — widened from the old timetable-only 18:00
                              // to fit custom/routine blocks like an evening gym slot.
const PX_PER_MIN = 64 / 60;

function toMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

function fmtHour(totalMinutes) {
  return `${String(Math.floor(totalMinutes / 60)).padStart(2, '0')}:00`;
}

/**
 * Registered as `schedule` (Section 4.4b) — replaces the Phase 3
 * `timetable` module now that schedule_blocks is the unified source of
 * truth (classes materialized alongside custom/focus/routine blocks).
 * Same vertical-tape mechanics as before (playhead fixed at 40% viewport
 * height, tape scrolls under it, past blocks fade, current gets an amber
 * edge) — new here is rendering every block_type in one merged, sorted
 * list instead of just classes.
 */
export default function ScheduleModule({ w, h }) {
  const compact = w < 3 || h < 6;
  const scrollRef = useRef(null);
  const [now, setNow] = useState(() => new Date());
  const { data, error } = useApiData(api.schedule, { pollMs: 60000 });

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(id);
  }, []);

  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const dayKey = now.getDay();

  const blocks = useMemo(() => {
    if (!USE_MOCK) return (data?.blocks ?? []).map(normalizeBlock).sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
    const classes = (MOCK_TIMETABLE[dayKey] || []).map((c) => ({ ...c, block_type: 'class' }));
    const merged = [...classes, ...MOCK_CUSTOM_BLOCKS_TODAY];
    return merged.sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
  }, [dayKey, data]);

  const contentHeight = (RANGE_END - RANGE_START) * PX_PER_MIN;
  const nowY = (nowMinutes - RANGE_START) * PX_PER_MIN;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const target = nowY - el.clientHeight * 0.4;
    el.scrollTop = Math.max(0, Math.min(target, contentHeight - el.clientHeight));
  }, [nowY, contentHeight]);

  if (error && blocks.length === 0) return <NoSignal />;

  const hours = [];
  for (let t = RANGE_START; t <= RANGE_END; t += 60) hours.push(t);

  return (
    <div className="schedule-tape">
      <div className="schedule-tape__scroll" ref={scrollRef}>
        <div className="schedule-tape__content" style={{ height: contentHeight }}>
          {hours.map((t) => (
            <div key={t} className="schedule-tape__tick" style={{ top: (t - RANGE_START) * PX_PER_MIN }}>
              <span className="schedule-tape__tick-label mono">{fmtHour(t)}</span>
            </div>
          ))}
          {blocks.map((b) => {
            const start = toMinutes(b.start);
            const end = toMinutes(b.end);
            const top = (start - RANGE_START) * PX_PER_MIN;
            const cardHeight = Math.max(22, (end - start) * PX_PER_MIN);
            const isPast = end <= nowMinutes;
            const isCurrent = start <= nowMinutes && nowMinutes < end;
            const tag = b.block_type !== 'class' && b.block_type !== 'custom' ? b.block_type : null;
            return (
              <div
                key={`${b.code || b.name}-${b.start}`}
                className={[
                  'schedule-tape__card',
                  isPast && 'is-past',
                  isCurrent && 'is-current',
                  compact && 'is-compact',
                ].filter(Boolean).join(' ')}
                style={{ top, height: cardHeight }}
              >
                {!compact && <div className="schedule-tape__card-name">{b.name}{tag ? ` [${tag}]` : ''}</div>}
                {b.code && <div className="schedule-tape__card-code mono">{b.code}</div>}
                {!compact && !b.code && <div className="schedule-tape__card-code mono">{tag || 'custom'}</div>}
                {!compact && <div className="schedule-tape__card-time mono">{b.start}–{b.end}</div>}
              </div>
            );
          })}
        </div>
      </div>
      <div className="schedule-tape__playhead" style={{ top: '40%' }} />
      {blocks.length === 0 && <div className="schedule-tape__empty mono">NOTHING SCHEDULED TODAY</div>}
    </div>
  );
}

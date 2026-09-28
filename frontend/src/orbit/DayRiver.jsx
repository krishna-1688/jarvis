import { motion } from 'motion/react';
import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useOrbit } from './context.js';
import { cleanCourse, daysUntil, hm, shortCourse, toDate, upcomingExams } from './format.js';

/** Today as one line: classes as slim blocks, finished ones struck
 * through, the next one lit, exams as ember diamonds, and a NOW line. */
export default function DayRiver() {
  const now = useNow(30000);
  const { openLens } = useOrbit();
  const blocks = (useSource('schedule').data?.blocks || [])
    .map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e)
    .sort((a, b) => a.s - b.s);
  const exams = upcomingExams(useSource('exams').data?.exams || [], now.getTime())
    .filter((e) => daysUntil(e.start, now) === 0);

  const startH = Math.min(7, ...blocks.map((b) => b.s.getHours()));
  const endH = Math.max(20, ...blocks.map((b) => b.e.getHours() + (b.e.getMinutes() ? 1 : 0)));
  const t0 = new Date(now).setHours(startH, 0, 0, 0);
  const t1 = new Date(now).setHours(endH, 0, 0, 0);
  const pos = (t) => ((t - t0) / (t1 - t0)) * 100;
  const nowPos = pos(now.getTime());
  const next = blocks.find((b) => b.e > now);
  const hours = [];
  for (let h = startH; h <= endH; h += 2) hours.push(h);

  return (
    <div className="river" role="img" aria-label="Today's timeline">
      <div className="river__track">
        {hours.map((h) => (
          <span key={h} className="river__hour" style={{ left: `${pos(new Date(now).setHours(h, 0, 0, 0))}%` }}>
            {String(h).padStart(2, '0')}
          </span>
        ))}
        <div className="river__rule" />
        {nowPos > 0 && <div className="river__passed" style={{ width: `${Math.min(100, nowPos)}%` }} />}
        {blocks.map((b, i) => {
          const left = pos(b.s.getTime());
          const width = Math.max(0.8, pos(b.e.getTime()) - left);
          const live = now >= b.s && now < b.e;
          const done = now >= b.e;
          return (
            <motion.button
              type="button"
              key={b.id ?? i}
              className={`river__block kind-${b.block_type || 'custom'} ${live ? 'is-live' : ''} ${done ? 'is-done' : ''} ${!live && b === next ? 'is-next' : ''}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              initial={{ scaleX: 0, opacity: 0 }}
              animate={{ scaleX: 1, opacity: 1 }}
              transition={{ delay: 0.4 + i * 0.05, type: 'spring', stiffness: 200, damping: 24 }}
              onClick={() => openLens('schedule')}
              title={`${hm(b.s)}–${hm(b.e)}  ${cleanCourse(b.title)}`}
            >
              <span>{shortCourse(b.title)}</span>
            </motion.button>
          );
        })}
        {exams.map((e) => (
          <button key={`${e.course_name}${e.exam_type}`} type="button" className="river__exam" style={{ left: `${pos(e.start.getTime())}%` }}
            onClick={() => openLens('exams')} title={`${cleanCourse(e.course_name)} ${e.exam_type} · ${hm(e.start)}${e.venue ? ` · ${e.venue}` : ''}`}>
            <i /><span>{shortCourse(e.course_name)} {e.exam_type}</span>
          </button>
        ))}
        {nowPos >= 0 && nowPos <= 100 && <div className="river__now" style={{ left: `${nowPos}%` }} />}
      </div>
    </div>
  );
}

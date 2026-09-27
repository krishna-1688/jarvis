import { motion } from 'motion/react';
import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useOrbit } from './context.js';
import { cleanCourse, hm, shortCourse, toDate } from './format.js';

export default function DayRiver() {
  const now = useNow(30000);
  const { openLens } = useOrbit();
  const { data } = useSource('schedule');
  const blocks = (data?.blocks || [])
    .map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e)
    .sort((a, b) => a.s - b.s);

  const day = new Date(now);
  const startH = Math.min(8, ...blocks.map((b) => b.s.getHours()));
  const endH = Math.max(21, ...blocks.map((b) => b.e.getHours() + (b.e.getMinutes() ? 1 : 0)));
  const t0 = new Date(day).setHours(startH, 0, 0, 0);
  const t1 = new Date(day).setHours(endH, 0, 0, 0);
  const pos = (t) => ((t - t0) / (t1 - t0)) * 100;
  const nowPos = pos(now.getTime());
  const hours = [];
  for (let h = startH; h <= endH; h += 1) hours.push(h);

  return (
    <div className="river" role="img" aria-label="Today's timeline">
      <div className="river__track">
        {hours.map((h) => (
          <span key={h} className="river__hour" style={{ left: `${pos(new Date(day).setHours(h, 0, 0, 0))}%` }}>
            {h % 2 === 0 ? String(h).padStart(2, '0') : ''}
          </span>
        ))}
        <div className="river__past" style={{ width: `${Math.max(0, Math.min(100, nowPos))}%` }} />
        {blocks.map((b, i) => {
          const left = pos(b.s.getTime());
          const width = Math.max(0.8, pos(b.e.getTime()) - left);
          const live = now >= b.s && now < b.e;
          const done = now >= b.e;
          return (
            <motion.button
              type="button"
              key={b.id ?? i}
              className={`river__block kind-${b.block_type || 'custom'} ${live ? 'is-live' : ''} ${done ? 'is-done' : ''}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              initial={{ scaleX: 0, opacity: 0 }}
              animate={{ scaleX: 1, opacity: 1 }}
              transition={{ delay: 0.3 + i * 0.04, type: 'spring', stiffness: 200, damping: 24 }}
              onClick={() => openLens('schedule')}
              title={`${hm(b.s)}–${hm(b.e)}  ${cleanCourse(b.title)}`}
            >
              <span>{shortCourse(b.title)}</span>
            </motion.button>
          );
        })}
        {nowPos >= 0 && nowPos <= 100 && (
          <div className="river__now" style={{ left: `${nowPos}%` }}>
            <span className="mono">{hm(now)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

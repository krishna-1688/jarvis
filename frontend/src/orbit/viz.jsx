import { motion } from 'motion/react';
import { useOrbit } from './context.js';
import {
  attendanceTone, bunkInfo, cleanCourse, dayWord, dueInfo, durShort, hm,
  relTime, shortCourse, toDate, upcomingExams,
} from './format.js';

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.04 } },
};
const rise = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 380, damping: 30 } },
};

export function Ring({ value = 0, size = 44, stroke = 4, tone = 'accent', children }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className={`ring ring--${tone}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle className="ring__track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} />
        <motion.circle
          className="ring__value" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke}
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - v) }}
          transition={{ type: 'spring', stiffness: 60, damping: 18 }}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      {children && <div className="ring__label">{children}</div>}
    </div>
  );
}

export function ActionChip({ children, onClick, tone }) {
  return (
    <button type="button" className={`action-chip ${tone ? `action-chip--${tone}` : ''}`} onClick={(e) => { e.stopPropagation(); onClick?.(); }}>
      {children}
    </button>
  );
}

/* ── Attendance ─────────────────────────────── */

export function AttendanceBars({ rows = [], limit, showActions = true, compact = false }) {
  const { send } = useOrbit();
  const sorted = [...rows].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101));
  const list = limit ? sorted.slice(0, limit) : sorted;
  return (
    <motion.ul className={`att-bars ${compact ? 'att-bars--compact' : ''}`} variants={stagger} initial="hidden" animate="show">
      {list.map((r) => {
        const pct = r.percentage ?? 0;
        const tone = attendanceTone(r.percentage);
        const info = bunkInfo(r.attended_classes || 0, r.total_classes || 0);
        const margin = info.safe
          ? (info.canSkip > 0 ? `${info.canSkip} to spare` : 'no spare')
          : `attend ${info.needed} more`;
        if (compact) {
          return (
            <motion.li key={r.course_code} className="att-row" variants={rise} title={r.course_name}>
              <b className="att-row__name">{shortCourse(r.course_name)}</b>
              <div className="att-bars__track">
                <motion.div className={`att-bars__fill fill-${tone}`} initial={{ width: 0 }}
                  animate={{ width: `${Math.min(100, pct)}%` }} transition={{ type: 'spring', stiffness: 70, damping: 16 }} />
                <span className="att-bars__line" style={{ left: '75%' }} />
              </div>
              <span className={`att-row__pct tone-${tone}`}>{pct.toFixed(0)}%</span>
              <span className={`att-row__margin tone-${info.safe ? (info.canSkip > 0 ? 'ok' : 'warn') : 'bad'}`}>{margin}</span>
            </motion.li>
          );
        }
        return (
          <motion.li key={r.course_code} className="att-bars__row" variants={rise}>
            <div className="att-bars__head">
              <span className="att-bars__name" title={r.course_name}>
                <b>{shortCourse(r.course_name)}</b>
                <span className="dim">{cleanCourse(r.course_name)}</span>
              </span>
              <span className={`att-bars__pct tone-${tone}`}>{pct.toFixed(0)}<small>%</small></span>
            </div>
            <div className="att-bars__track">
              <motion.div
                className={`att-bars__fill fill-${tone}`}
                initial={{ width: 0 }} animate={{ width: `${Math.min(100, pct)}%` }}
                transition={{ type: 'spring', stiffness: 70, damping: 16 }}
              />
              <span className="att-bars__line" style={{ left: '75%' }} />
            </div>
            <div className="att-bars__foot">
              <span className="mono dim">{r.attended_classes}/{r.total_classes}</span>
              <span className={`att-bars__margin tone-${info.safe ? (info.canSkip > 0 ? 'ok' : 'warn') : 'bad'}`}>{margin}</span>
              {showActions && (
                <ActionChip onClick={() => send(`can I skip ${cleanCourse(r.course_name)} tomorrow`)}>Can I skip?</ActionChip>
              )}
            </div>
          </motion.li>
        );
      })}
    </motion.ul>
  );
}

/** Every course as a dial with the 75% line marked; the tightest course
 * is the one coloured, the rest stay calm. */
export function AttendanceRings({ rows = [] }) {
  const { send } = useOrbit();
  const sorted = [...rows].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101));
  const tightest = sorted[0];
  const C = 2 * Math.PI * 40;
  return (
      <motion.div className="att-rings" variants={stagger} initial="hidden" animate="show">
        {sorted.map((r, i) => {
          const pct = Math.max(0, Math.min(100, r.percentage ?? 0));
          const info = bunkInfo(r.attended_classes || 0, r.total_classes || 0);
          const tone = !info.safe ? 'bad' : (r === tightest && info.canSkip <= 3) ? 'warn' : 'ok';
          const color = tone === 'bad' ? '#FF6A2B' : tone === 'warn' ? '#E8C27A' : '#9FB8A0';
          return (
            <motion.button type="button" key={r.course_code} className="att-ring" variants={rise} title={cleanCourse(r.course_name)}
              onClick={() => send(`can I skip ${cleanCourse(r.course_name)} tomorrow`)}>
              <div className="att-ring__dial">
                <svg width="100" height="100" viewBox="0 0 100 100" aria-hidden>
                  <circle cx="50" cy="50" r="40" fill="none" stroke="rgba(243,237,228,.07)" strokeWidth="5" />
                  <motion.circle cx="50" cy="50" r="40" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
                    strokeDasharray={C} initial={{ strokeDashoffset: C }} animate={{ strokeDashoffset: C * (1 - pct / 100) }}
                    transition={{ type: 'spring', stiffness: 50, damping: 16, delay: 0.1 + i * 0.06 }}
                    transform="rotate(-90 50 50)" style={tone !== 'ok' ? { filter: `drop-shadow(0 0 5px ${color}99)` } : undefined} />
                  <path d="M4 50h9" stroke="#F3EDE4" strokeWidth="1.5" opacity=".45" />
                </svg>
                <span className="att-ring__pct">{Math.round(pct)}</span>
              </div>
              <div>
                <div className="att-ring__name">{shortCourse(r.course_name)}</div>
                <div className="att-ring__meta" style={tone !== 'ok' ? { color } : undefined}>
                  {r.attended_classes}/{r.total_classes} · {info.safe ? `${info.canSkip} spare` : `need ${info.needed}`}
                </div>
              </div>
            </motion.button>
          );
        })}
    </motion.div>
  );
}

/* ── Schedule ───────────────────────────────── */

export function Timeline({ blocks = [], now = new Date(), relative = true }) {
  const items = blocks
    .map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e)
    .sort((a, b) => a.s - b.s);
  if (!items.length) return <div className="empty">Nothing scheduled.</div>;
  return (
    <motion.ol className="timeline" variants={stagger} initial="hidden" animate="show">
      {items.map((b) => {
        const live = relative && now >= b.s && now < b.e;
        const done = relative && now >= b.e;
        const progress = live ? (now - b.s) / (b.e - b.s) : 0;
        return (
          <motion.li key={b.id ?? `${b.title}${b.start_at}`} variants={rise}
            className={`timeline__item kind-${b.block_type || 'custom'} ${live ? 'is-live' : ''} ${done ? 'is-done' : ''}`}>
            <span className="timeline__time mono">{hm(b.s)}<small>{hm(b.e)}</small></span>
            <span className="timeline__dot" />
            <div className="timeline__body">
              <span className="timeline__title">{cleanCourse(b.title)}</span>
              <span className="timeline__meta dim">
                {b.linked_course || b.block_type}
                {live && <> · <b className="tone-accent">{durShort(b.e - now)} left</b></>}
                {relative && !live && !done && <> · {relTime(b.s, now.getTime())}</>}
              </span>
              {live && (
                <div className="timeline__progress">
                  <motion.i initial={{ width: 0 }} animate={{ width: `${progress * 100}%` }} />
                </div>
              )}
            </div>
          </motion.li>
        );
      })}
    </motion.ol>
  );
}

export function ClassList({ classes = [], day }) {
  const today = new Date();
  const blocks = classes.map((c) => {
    const [sh, sm] = (c.start_time || '0:0').split(':').map(Number);
    const [eh, em] = (c.end_time || '0:0').split(':').map(Number);
    const s = new Date(today); s.setHours(sh, sm, 0, 0);
    const e = new Date(today); e.setHours(eh, em, 0, 0);
    return {
      id: c.id, title: c.course_name, start_at: s.toISOString(), end_at: e.toISOString(),
      block_type: 'class', linked_course: [c.course_code, c.room].filter(Boolean).join(' · '),
    };
  });
  const isToday = !day || day === today.toLocaleDateString('en-US', { weekday: 'long' });
  return <Timeline blocks={blocks} now={today} relative={isToday} />;
}

export function NextClassCard({ cls }) {
  if (!cls) return null;
  return (
    <div className="hero-stat">
      <div className="hero-stat__big">{cls.start_time}</div>
      <div>
        <div className="hero-stat__title">{cls.course_name}</div>
        <div className="dim mono">{[cls.course_code, cls.room, cls.day].filter(Boolean).join(' · ')}</div>
      </div>
    </div>
  );
}

/* ── Assignments ────────────────────────────── */

export function AssignmentCards({ items = [], limit }) {
  const { openExternal, send } = useOrbit();
  const sorted = [...items].sort((a, b) => (a.due_date || '9').localeCompare(b.due_date || '9'));
  const list = limit ? sorted.slice(0, limit) : sorted;
  if (!list.length) return <div className="empty">No pending assignments. Clean slate.</div>;
  return (
    <motion.ul className="cards" variants={stagger} initial="hidden" animate="show">
      {list.map((a) => {
        const due = dueInfo(a.due_date);
        return (
          <motion.li key={a.id ?? a.title} className={`card-item edge-${due.tone}`} variants={rise}>
            <div className="card-item__top">
              <span className="card-item__title">{a.title}</span>
              <span className={`pill tone-${due.tone}`}>{due.label}</span>
            </div>
            <div className="dim card-item__sub">{cleanCourse(a.course_name)}</div>
            <div className="card-item__actions">
              {a.assign_url && <ActionChip onClick={() => openExternal(a.assign_url)}>Open in LMS ↗</ActionChip>}
              <ActionChip onClick={() => send(`remind me to finish ${a.title} ${due.tone === 'bad' ? 'today' : 'tomorrow'}`)}>Add reminder</ActionChip>
            </div>
          </motion.li>
        );
      })}
    </motion.ul>
  );
}

/* ── Exams ──────────────────────────────────── */

export function ExamCards({ exams = [], limit }) {
  const now = new Date();
  const upcoming = upcomingExams(exams, now.getTime());
  const list = upcoming.slice(0, limit || upcoming.length);
  if (!list.length) return <div className="empty">No upcoming exams on record.</div>;
  return (
    <motion.ul className="cards" variants={stagger} initial="hidden" animate="show">
      {list.map((e) => {
        const ms = e.start - now;
        const tone = ms < 2 * 86400000 ? 'bad' : ms < 7 * 86400000 ? 'warn' : 'ok';
        return (
          <motion.li key={e.id ?? `${e.course_code}${e.exam_type}`} className={`card-item exam edge-${tone}`} variants={rise}>
            <div className="exam__count">
              <b>{ms > 86400000 ? Math.floor(ms / 86400000) : Math.max(0, Math.floor(ms / 3600000))}</b>
              <small>{ms > 86400000 ? 'days' : 'hours'}</small>
            </div>
            <div className="exam__body">
              <div className="card-item__top">
                <span className="card-item__title">{cleanCourse(e.course_name)}</span>
                <span className={`pill tone-${tone}`}>{e.exam_type}</span>
              </div>
              <div className="dim mono card-item__sub">
                {dayWord(e.start)} · {e.session || hm(e.start)}{e.venue ? ` · ${e.venue}` : ''}{e.seat_number ? ` · seat ${e.seat_number}` : ''}
              </div>
            </div>
          </motion.li>
        );
      })}
    </motion.ul>
  );
}

/* ── Tasks ──────────────────────────────────── */

export function TaskList({ tasks = [], limit }) {
  const { send } = useOrbit();
  const list = limit ? tasks.slice(0, limit) : tasks;
  if (!list.length) return <div className="empty">No open tasks. Say “remind me to…”.</div>;
  return (
    <motion.ul className="tasks" variants={stagger} initial="hidden" animate="show">
      {list.map((t) => {
        const due = t.due_at ? dueInfo(t.due_at) : null;
        return (
          <motion.li key={t.id} className="task" variants={rise} layout>
            <button type="button" className="task__check" title="Mark done" onClick={() => send(`mark ${t.title} as done`)}>
              <svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" /></svg>
            </button>
            <span className="task__title">{t.title}</span>
            {t.priority === 'high' && <span className="pill tone-bad">high</span>}
            {due && <span className={`task__due tone-${due.tone}`}>{due.label.replace('Due ', '')}</span>}
          </motion.li>
        );
      })}
    </motion.ul>
  );
}

/* ── CGPA ───────────────────────────────────── */

const GRADE_ORDER = ['S', 'A', 'B', 'C', 'D', 'E', 'F'];

export function CgpaCard({ summary }) {
  if (!summary) return null;
  let dist = {};
  try { dist = JSON.parse(summary.extra_json || '{}'); } catch { dist = {}; }
  const max = Math.max(1, ...GRADE_ORDER.map((g) => dist[g] || 0));
  return (
    <div className="cgpa">
      <Ring value={(summary.cgpa || 0) / 10} size={96} stroke={6}>
        <b className="cgpa__num">{summary.cgpa?.toFixed(2)}</b>
      </Ring>
      <div className="cgpa__side">
        <div className="dim mono">{summary.credits_earned} credits earned</div>
        <div className="cgpa__dist">
          {GRADE_ORDER.map((g) => (
            <div key={g} className="cgpa__col" title={`${dist[g] || 0} × ${g}`}>
              <motion.i initial={{ height: 0 }} animate={{ height: `${((dist[g] || 0) / max) * 100}%` }}
                transition={{ type: 'spring', stiffness: 80, damping: 16 }} />
              <span>{g}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

import { motion } from 'motion/react';
import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useOrbit } from './context.js';
import { Ring } from './viz.jsx';
import {
  attendanceTone, bunkInfo, cleanCourse, dayWord, dueInfo, durShort, hm, inr, shortCourse, toDate, upcomingExams,
} from './format.js';

function Glance({ lens, label, children, tone, delay = 0, active }) {
  const { openLens } = useOrbit();
  return (
    <motion.button
      type="button"
      className={`glance ${tone ? `glance--${tone}` : ''} ${active ? 'is-active' : ''}`}
      onClick={() => openLens(lens)}
      initial={{ opacity: 0, x: -16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay, type: 'spring', stiffness: 260, damping: 26 }}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.98 }}
    >
      <span className="glance__label">{label}</span>
      {children}
    </motion.button>
  );
}

function Skeleton() {
  return <div className="skeleton"><i /><i /></div>;
}

function NowNext({ active, delay }) {
  const now = useNow(15000);
  const { data, loading } = useSource('schedule');
  const blocks = (data?.blocks || [])
    .map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e)
    .sort((a, b) => a.s - b.s);
  const current = blocks.find((b) => now >= b.s && now < b.e);
  const next = blocks.find((b) => b.s > now);
  const doneCount = blocks.filter((b) => b.e <= now).length;

  let body;
  if (loading && !data) body = <Skeleton />;
  else if (current) {
    const p = (now - current.s) / (current.e - current.s);
    body = (
      <div className="glance__row">
        <Ring value={p} size={46} tone="accent"><small>{durShort(current.e - now)}</small></Ring>
        <div className="glance__text">
          <b>{cleanCourse(current.title)}</b>
          <span className="dim">Now · ends {hm(current.e)}{next ? ` · then ${shortCourse(next.title)}` : ''}</span>
        </div>
      </div>
    );
  } else if (next) {
    body = (
      <div className="glance__row">
        <div className="glance__big mono">{hm(next.s)}</div>
        <div className="glance__text">
          <b>{cleanCourse(next.title)}</b>
          <span className="dim">in {durShort(next.s - now)}</span>
        </div>
      </div>
    );
  } else {
    body = <div className="glance__text"><b>You're free</b><span className="dim">{blocks.length ? 'Done for today' : 'Nothing scheduled today'}</span></div>;
  }

  return (
    <Glance lens="schedule" label={current ? 'Happening now' : 'Up next'} delay={delay} active={active}>
      {body}
      {blocks.length > 0 && (
        <div className="glance__dots">
          {blocks.map((b, i) => <i key={b.id ?? i} className={i < doneCount ? 'is-done' : b === current ? 'is-live' : ''} />)}
        </div>
      )}
    </Glance>
  );
}

function AttendanceGlance({ active, delay }) {
  const { data, loading } = useSource('attendance');
  const rows = data?.rows || [];
  const attended = rows.reduce((s, r) => s + (r.attended_classes || 0), 0);
  const total = rows.reduce((s, r) => s + (r.total_classes || 0), 0);
  const overall = total ? (attended / total) * 100 : null;
  const worst = [...rows].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101))[0];
  const info = worst ? bunkInfo(worst.attended_classes || 0, worst.total_classes || 0) : null;
  const tone = attendanceTone(worst?.percentage);

  return (
    <Glance lens="attendance" label="Attendance" tone={tone === 'bad' ? 'bad' : undefined} delay={delay} active={active}>
      {loading && !data ? <Skeleton /> : rows.length === 0 ? <span className="dim">No attendance synced yet</span> : (
        <>
          <div className="glance__row">
            <div className="glance__big">{overall?.toFixed(1)}<small>%</small></div>
            <div className="spark">
              {[...rows].sort((a, b) => (a.percentage ?? 0) - (b.percentage ?? 0)).map((r) => (
                <i key={r.course_code} className={`fill-${attendanceTone(r.percentage)}`}
                  style={{ height: `${Math.max(8, ((r.percentage ?? 0) - 50) * 2)}%` }} title={`${shortCourse(r.course_name)} ${r.percentage}%`} />
              ))}
            </div>
          </div>
          {worst && (
            <span className="dim glance__foot">
              Lowest <b className={`tone-${tone}`}>{shortCourse(worst.course_name)} {worst.percentage?.toFixed(0)}%</b>
              {info && (info.safe ? ` · ${info.canSkip} to spare` : ` · attend ${info.needed}`)}
            </span>
          )}
        </>
      )}
    </Glance>
  );
}

function ExamGlance({ active, delay }) {
  const now = useNow(60000);
  const { data, loading } = useSource('exams');
  const next = upcomingExams(data?.exams || [], now.getTime())[0];
  const ms = next ? next.start - now : 0;
  const tone = next && ms < 2 * 86400000 ? 'bad' : next && ms < 7 * 86400000 ? 'warn' : undefined;
  return (
    <Glance lens="exams" label="Next exam" tone={tone} delay={delay} active={active}>
      {loading && !data ? <Skeleton /> : !next ? <span className="dim">No upcoming exams</span> : (
        <div className="glance__row">
          <div className="glance__big">
            {ms >= 86400000 ? Math.floor(ms / 86400000) : Math.floor(ms / 3600000)}
            <small>{ms >= 86400000 ? 'd' : 'h'}</small>
          </div>
          <div className="glance__text">
            <b>{shortCourse(next.course_name)} · {next.exam_type}</b>
            <span className="dim">{dayWord(next.start)} · {next.session?.split(' - ')[0] || hm(next.start)}{next.venue ? ` · ${next.venue}` : ''}</span>
          </div>
        </div>
      )}
    </Glance>
  );
}

function AssignmentGlance({ active, delay }) {
  const { data, loading } = useSource('assignments');
  const items = [...(data?.pending || [])].sort((a, b) => (a.due_date || '9').localeCompare(b.due_date || '9'));
  const overdue = items.filter((a) => dueInfo(a.due_date).tone === 'bad').length;
  const upcoming = items.find((a) => dueInfo(a.due_date).tone !== 'bad');
  return (
    <Glance lens="assignments" label="Assignments" tone={overdue ? 'bad' : undefined} delay={delay} active={active}>
      {loading && !data ? <Skeleton /> : (
        <div className="glance__row">
          <div className="glance__big">{items.length}</div>
          <div className="glance__text">
            <b>{items.length ? 'pending' : 'All clear'}</b>
            <span className="dim">
              {overdue ? <b className="tone-bad">{overdue} overdue</b> : null}
              {overdue && upcoming ? ' · ' : ''}
              {upcoming ? `${shortCourse(upcoming.course_name)} ${dueInfo(upcoming.due_date).label.toLowerCase()}` : ''}
            </span>
          </div>
        </div>
      )}
    </Glance>
  );
}

function TaskGlance({ active, delay }) {
  const { data, loading } = useSource('tasks');
  const tasks = data?.tasks || [];
  const overdue = tasks.filter((t) => t.due_at && dueInfo(t.due_at).tone === 'bad').length;
  return (
    <Glance lens="tasks" label="Tasks" delay={delay} active={active}>
      {loading && !data ? <Skeleton /> : (
        <div className="glance__row">
          <div className="glance__big">{tasks.length}</div>
          <div className="glance__text">
            <b>{tasks[0]?.title || 'Nothing on your plate'}</b>
            <span className="dim">{overdue ? `${overdue} overdue` : tasks.length ? 'open' : 'say “remind me to…”'}</span>
          </div>
        </div>
      )}
    </Glance>
  );
}

function FocusGlance({ active, delay }) {
  const { data } = useSource('focus');
  const now = useNow(data?.active ? 1000 : 60000);
  const { data: stats } = useSource('focusStats');
  const session = data?.active;
  const weekMins = (stats?.stats || []).reduce((s, r) => s + (r.total_minutes || 0), 0);
  if (session) {
    const started = toDate(session.started_at);
    const end = started ? started.getTime() + session.planned_minutes * 60000 : 0;
    const left = Math.max(0, end - now.getTime());
    const p = session.planned_minutes ? 1 - left / (session.planned_minutes * 60000) : 0;
    const mm = String(Math.floor(left / 60000)).padStart(2, '0');
    const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, '0');
    return (
      <Glance lens="focus" label="Focusing" tone="focus" delay={delay} active={active}>
        <div className="glance__row">
          <Ring value={p} size={46} tone="violet"><small className="mono">{mm}:{ss}</small></Ring>
          <div className="glance__text"><b>{session.subject}</b><span className="dim">{session.pomodoros_completed || 0} pomodoros</span></div>
        </div>
      </Glance>
    );
  }
  return (
    <Glance lens="focus" label="Focus" delay={delay} active={active}>
      <div className="glance__text"><b>{weekMins ? `${durShort(weekMins * 60000)} this week` : 'Start a session'}</b><span className="dim">Deep work, pomodoro style</span></div>
    </Glance>
  );
}

function MoneyGlance({ active, delay }) {
  const { data } = useSource('money');
  return (
    <Glance lens="money" label="This month" delay={delay} active={active}>
      <div className="glance__text"><b>{inr(data?.total)}</b><span className="dim">{data?.by_category?.[0] ? `mostly ${data.by_category[0].category}` : 'say “spent 200 on lunch”'}</span></div>
    </Glance>
  );
}

export default function GlanceRail({ activeLens }) {
  const cards = [
    ['schedule', NowNext], ['attendance', AttendanceGlance], ['exams', ExamGlance],
    ['assignments', AssignmentGlance], ['tasks', TaskGlance], ['focus', FocusGlance], ['money', MoneyGlance],
  ];
  return (
    <nav className="rail" aria-label="At a glance">
      {cards.map(([lens, Card], i) => <Card key={lens} active={activeLens === lens} delay={0.15 + i * 0.05} />)}
    </nav>
  );
}

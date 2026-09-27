import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useSource } from './store.js';
import { useOrbit } from './context.js';
import { useNow } from './useNow.js';
import { AssignmentCards, AttendanceBars, ExamCards, Ring, TaskList, Timeline } from './viz.jsx';
import { durShort, inr, shortCourse, toDate, upcomingExams } from './format.js';

function QuickInput({ placeholder, prefix = '', hint }) {
  const { send } = useOrbit();
  const [v, setV] = useState('');
  return (
    <form className="quick" onSubmit={(e) => { e.preventDefault(); if (v.trim()) { send(prefix + v.trim()); setV(''); } }}>
      {prefix && <span className="quick__prefix">{prefix.trim()}</span>}
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} spellCheck={false} />
      <button type="submit" disabled={!v.trim()}>↵</button>
      {hint && <span className="quick__hint dim">{hint}</span>}
    </form>
  );
}

function Loading({ source }) {
  if (source.loading && !source.data) return <div className="skeleton skeleton--lg"><i /><i /><i /></div>;
  if (source.error && !source.data) return <div className="empty">Couldn't load this — is the backend running?</div>;
  return null;
}

function ScheduleLens() {
  const [day, setDay] = useState('today');
  const today = useSource('schedule');
  const tomorrow = useSource('tomorrow');
  const src = day === 'today' ? today : tomorrow;
  const now = useNow(30000);
  return (
    <>
      <div className="seg">
        {['today', 'tomorrow'].map((d) => (
          <button key={d} type="button" className={day === d ? 'is-on' : ''} onClick={() => setDay(d)}>{d}</button>
        ))}
      </div>
      <Loading source={src} />
      {src.data && <Timeline blocks={src.data.blocks || []} now={now} relative={day === 'today'} />}
      <QuickInput placeholder={`block 4 to 6 ${day} for DBMS revision`} hint="Block time — Jarvis checks for clashes" />
    </>
  );
}

function AttendanceLens() {
  const src = useSource('attendance');
  const rows = src.data?.rows || [];
  const attended = rows.reduce((s, r) => s + (r.attended_classes || 0), 0);
  const total = rows.reduce((s, r) => s + (r.total_classes || 0), 0);
  const overall = total ? attended / total : 0;
  const synced = toDate(rows[0]?.last_synced);
  return (
    <>
      <Loading source={src} />
      {rows.length > 0 && (
        <div className="lens-hero">
          <Ring value={overall} size={88} stroke={6} tone={overall < 0.75 ? 'bad' : 'accent'}>
            <b>{(overall * 100).toFixed(1)}<small>%</small></b>
          </Ring>
          <div>
            <div className="lens-hero__title">{attended} of {total} classes</div>
            <div className="dim">{synced ? `Synced ${synced.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}` : ''}</div>
          </div>
        </div>
      )}
      <AttendanceBars rows={rows} />
    </>
  );
}

function ExamsLens() {
  const src = useSource('exams');
  return (<><Loading source={src} />{src.data && <ExamCards exams={src.data.exams || []} />}</>);
}

function AssignmentsLens() {
  const { send } = useOrbit();
  const src = useSource('assignments');
  return (
    <>
      <div className="lens-actions">
        <button type="button" className="btn" onClick={() => send('sync lms')}>↻ Sync LMS</button>
      </div>
      <Loading source={src} />
      {src.data && <AssignmentCards items={src.data.pending || []} />}
    </>
  );
}

function TasksLens() {
  const src = useSource('tasks');
  return (
    <>
      <QuickInput prefix="remind me to " placeholder="submit fees on friday" />
      <Loading source={src} />
      {src.data && <TaskList tasks={src.data.tasks || []} />}
    </>
  );
}

const DURATIONS = [25, 50, 90];

function FocusLens() {
  const { send } = useOrbit();
  const focus = useSource('focus');
  const stats = useSource('focusStats');
  const att = useSource('attendance');
  const session = focus.data?.active;
  const now = useNow(session ? 1000 : 60000);
  const subjects = [...new Set((att.data?.rows || []).filter((r) => !/lab/i.test(r.course_name)).map((r) => shortCourse(r.course_name)))];
  const [subject, setSubject] = useState('');
  const [mins, setMins] = useState(25);
  const rows = stats.data?.stats || [];
  const maxMin = Math.max(1, ...rows.map((r) => r.total_minutes || 0));

  let live = null;
  if (session) {
    const started = toDate(session.started_at);
    const left = Math.max(0, (started?.getTime() || 0) + session.planned_minutes * 60000 - now.getTime());
    live = (
      <div className="lens-hero">
        <Ring value={1 - left / (session.planned_minutes * 60000)} size={110} stroke={7} tone="violet">
          <b className="mono">{String(Math.floor(left / 60000)).padStart(2, '0')}:{String(Math.floor((left % 60000) / 1000)).padStart(2, '0')}</b>
        </Ring>
        <div>
          <div className="lens-hero__title">{session.subject}</div>
          <div className="dim">{session.pomodoros_completed || 0} pomodoros done</div>
          <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => send('stop focus')}>End session</button>
        </div>
      </div>
    );
  }

  return (
    <>
      {live || (
        <div className="focus-start">
          <div className="dim small">What are you focusing on?</div>
          <div className="chips">
            {subjects.map((s) => (
              <button key={s} type="button" className={`chip-btn ${subject === s ? 'is-on' : ''}`} onClick={() => setSubject(s)}>{s}</button>
            ))}
          </div>
          <input className="field" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="or type a subject" />
          <div className="chips">
            {DURATIONS.map((d) => (
              <button key={d} type="button" className={`chip-btn ${mins === d ? 'is-on' : ''}`} onClick={() => setMins(d)}>{d} min</button>
            ))}
          </div>
          <button type="button" className="btn btn--primary btn--wide" disabled={!subject.trim()}
            onClick={() => send(`start focus on ${subject.trim()} for ${mins} minutes`)}>
            Start focus
          </button>
        </div>
      )}
      <h4 className="lens-sub">Last 7 days</h4>
      {rows.length === 0 ? <div className="empty">No sessions yet this week.</div> : (
        <ul className="hbars">
          {rows.map((r) => (
            <li key={r.subject}>
              <span>{r.subject}</span>
              <div><motion.i initial={{ width: 0 }} animate={{ width: `${(r.total_minutes / maxMin) * 100}%` }} /></div>
              <b className="mono">{durShort(r.total_minutes * 60000)}</b>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function MoneyLens() {
  const src = useSource('money');
  const cats = src.data?.by_category || [];
  const max = Math.max(1, ...cats.map((c) => c.total || 0));
  const recent = (src.data?.expenses || []).slice(0, 12);
  return (
    <>
      <QuickInput prefix="spent " placeholder="120 on coffee" />
      <Loading source={src} />
      {src.data && (
        <>
          <div className="lens-hero"><div className="lens-hero__money">{inr(src.data.total)}</div><div className="dim">this month</div></div>
          {cats.length > 0 && (
            <ul className="hbars">
              {cats.map((c) => (
                <li key={c.category}>
                  <span>{c.category}</span>
                  <div><motion.i initial={{ width: 0 }} animate={{ width: `${(c.total / max) * 100}%` }} /></div>
                  <b className="mono">{inr(c.total)}</b>
                </li>
              ))}
            </ul>
          )}
          {recent.length > 0 && <h4 className="lens-sub">Recent</h4>}
          <ul className="ledger">
            {recent.map((e) => (
              <li key={e.id}>
                <span>{e.merchant || e.note || e.category}</span>
                <span className="dim">{toDate(e.spent_at || e.logged_at)?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</span>
                <b className="mono">{inr(e.amount)}</b>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

const LENSES = {
  schedule:    { title: 'Schedule',    icon: '◷', Body: ScheduleLens },
  attendance:  { title: 'Attendance',  icon: '◔', Body: AttendanceLens },
  exams:       { title: 'Exams',       icon: '✦', Body: ExamsLens },
  assignments: { title: 'Assignments', icon: '▤', Body: AssignmentsLens },
  tasks:       { title: 'Tasks',       icon: '✓', Body: TasksLens },
  focus:       { title: 'Focus',       icon: '◉', Body: FocusLens },
  money:       { title: 'Money',       icon: '₹', Body: MoneyLens },
};

/** Subtitle helpers for the lens header. */
function useSubtitle(name) {
  const exams = useSource('exams');
  if (name === 'exams') {
    const n = upcomingExams(exams.data?.exams || []).length;
    return n ? `${n} coming up` : '';
  }
  return '';
}

export default function Lens({ name, onClose }) {
  const lens = LENSES[name];
  const subtitle = useSubtitle(name);
  return (
    <AnimatePresence>
      {lens && (
        <>
          <motion.div className="lens-scrim" key="scrim" onClick={onClose}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
          <motion.aside
            key={name}
            className="lens"
            initial={{ x: 40, opacity: 0, filter: 'blur(8px)' }}
            animate={{ x: 0, opacity: 1, filter: 'blur(0px)' }}
            exit={{ x: 40, opacity: 0, filter: 'blur(8px)' }}
            transition={{ type: 'spring', stiffness: 320, damping: 32 }}
          >
            <header className="lens__head">
              <span className="lens__icon" aria-hidden>{lens.icon}</span>
              <div>
                <h3>{lens.title}</h3>
                {subtitle && <span className="dim small">{subtitle}</span>}
              </div>
              <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">✕</button>
            </header>
            <div className="lens__body">
              <lens.Body />
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}


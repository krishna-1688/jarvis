import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useOrbit } from './context.js';
import {
  bunkInfo, cleanCourse, dayWord, dueInfo, durShort, hm, inr, shortCourse, toDate, upcomingExams,
} from './format.js';

const pad = (n) => String(n).padStart(2, '0');
const DAY = 86400000;

/** Which college integrations are set up (until /profile answers, assume
 * yes so a connected student's rail doesn't flicker). */
function useConnected() {
  const c = useSource('profile').data?.connected;
  return { vtop: c ? !!c.vtop : true, lms: c ? !!c.lms : true };
}

function todaysBlocks(data) {
  return (data?.blocks || [])
    .map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e)
    .sort((a, b) => a.s - b.s);
}

function attendanceFor(rows, title) {
  const key = shortCourse(title || '');
  return rows.find((r) => shortCourse(r.course_name) === key)
    || rows.find((r) => cleanCourse(r.course_name).toLowerCase() === cleanCourse(title || '').toLowerCase());
}

/** Shared "what needs attention" logic: the hero rail lists it, the slim
 * rail and the greeting line summarise it. */
export function useAttention() {
  const now = useNow(60000);
  const exams = upcomingExams(useSource('exams').data?.exams || [], now.getTime());
  const pending = useSource('assignments').data?.pending || [];
  const rows = useSource('attendance').data?.rows || [];
  const tasks = useSource('tasks').data?.tasks || [];

  const items = [];
  const exam = exams[0];
  if (exam && exam.start - now < 7 * DAY) {
    const ms = exam.start - now;
    items.push({
      key: 'exam', lens: 'exams', hot: ms < 2 * DAY,
      title: `${shortCourse(exam.course_name)} · ${exam.exam_type}`,
      sub: `${dayWord(exam.start)}, ${hm(exam.start)}${exam.venue ? ` · ${exam.venue}` : ''}`,
      value: durShort(ms),
    });
  }
  const overdue = pending.filter((a) => dueInfo(a.due_date).tone === 'bad').length;
  const soon = pending.filter((a) => dueInfo(a.due_date).tone === 'warn').length;
  if (overdue || soon) {
    items.push({
      key: 'lms', lens: 'assignments', hot: false,
      title: 'Assignments',
      sub: overdue ? `${overdue} overdue on LMS${soon ? ` · ${soon} due soon` : ''}` : `${soon} due soon`,
      value: String(overdue || soon),
    });
  }
  const worst = [...rows].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101))[0];
  if (worst) {
    const info = bunkInfo(worst.attended_classes || 0, worst.total_classes || 0);
    if (!info.safe || info.canSkip <= 1) {
      items.push({
        key: 'att', lens: 'attendance', hot: !info.safe,
        title: `${shortCourse(worst.course_name)} attendance`,
        sub: info.safe ? (info.canSkip ? '1 class to spare' : 'No classes to spare') : `Attend ${info.needed} more to reach 75%`,
        value: `${(worst.percentage ?? 0).toFixed(0)}%`,
      });
    }
  }
  const lateTasks = tasks.filter((t) => t.due_at && dueInfo(t.due_at).tone === 'bad').length;
  if (lateTasks) {
    items.push({ key: 'tasks', lens: 'tasks', hot: false, title: 'Tasks', sub: `${lateTasks} overdue`, value: String(lateTasks) });
  }
  return { items, exam, overdue };
}

function NowCard() {
  const { openLens } = useOrbit();
  const minuteNow = useNow(30000);
  const blocks = todaysBlocks(useSource('schedule').data);
  const rows = useSource('attendance').data?.rows || [];
  const tomorrow = todaysBlocks(useSource('tomorrow').data);
  const current = blocks.find((b) => minuteNow >= b.s && minuteNow < b.e);
  const next = blocks.find((b) => b.s > minuteNow);
  const target = current ? current.e : next?.s;
  // Tick every second only inside the final hour — a live countdown when
  // it matters, one update a minute otherwise.
  const close = target && target - minuteNow < 3600000;
  const now = useNow(close ? 1000 : 30000);

  if (!current && !next) {
    const first = tomorrow[0];
    return (
      <button type="button" className="now-card now-card--calm rise" style={{ '--d': '0.2s' }} onClick={() => openLens('schedule')}>
        <div className="now-card__count" style={{ fontSize: 26 }}>You're free</div>
        <div className="now-card__unit">{blocks.length ? 'Classes are done for today' : 'Nothing scheduled today'}</div>
        {first && <div className="now-card__meta"><span>Tomorrow</span><span className="mono">{hm(first.s)} · {shortCourse(first.title)}</span></div>}
      </button>
    );
  }

  const block = current || next;
  const left = Math.max(0, target - now);
  const span = current ? current.e - current.s : 3600000;
  const progress = current ? 1 - left / span : Math.max(0, 1 - left / span);
  const C = 182.2;
  const att = attendanceFor(rows, block.title);
  const info = att ? bunkInfo(att.attended_classes || 0, att.total_classes || 0) : null;

  let count;
  if (left < 3600000) {
    const s = Math.floor(left / 1000);
    count = <>{pad(Math.floor(s / 60))}:{pad(s % 60)}</>;
  } else {
    const mins = Math.round(left / 60000);
    count = <>{Math.floor(mins / 60)}<small>h</small> {pad(mins % 60)}<small>m</small></>;
  }

  return (
    <button type="button" className={`now-card rise ${close ? 'now-card--live' : ''}`} style={{ '--d': '0.2s' }} onClick={() => openLens('schedule')}>
      <div className="now-card__top">
        <div className="now-card__ring">
          <svg width="66" height="66" viewBox="0 0 66 66" aria-hidden>
            <circle cx="33" cy="33" r="29" fill="none" stroke="rgba(243,237,228,.08)" strokeWidth="2" strokeDasharray="1.2 3.35" />
            <circle cx="33" cy="33" r="29" fill="none" stroke="#FF6A2B" strokeWidth="2.5" strokeLinecap="round"
              strokeDasharray={C} strokeDashoffset={C * (1 - Math.min(1, progress))} transform="rotate(-90 33 33)"
              style={{ filter: 'drop-shadow(0 0 4px rgba(255,106,43,.8))', transition: 'stroke-dashoffset 1s linear' }} />
          </svg>
          <div className="now-card__tick"><i /></div>
        </div>
        <div>
          <div className="now-card__count">{count}</div>
          <div className="now-card__unit">{current ? `left · ends ${hm(current.e)}` : `until class · ${hm(next.s)}`}</div>
        </div>
      </div>
      <div className="now-card__title">{cleanCourse(block.title)}</div>
      <div className="now-card__meta">
        <span>{current ? 'Happening now' : shortCourse(block.title)}{block.location ? ` · ${block.location}` : ''}</span>
        {att && (
          <span className="mono" style={{ color: info?.safe ? (info.canSkip > 1 ? '#9FB8A0' : '#E8C27A') : '#FF6A2B' }}>
            {(att.percentage ?? 0).toFixed(0)}% · {info?.safe ? `${info.canSkip} spare` : `need ${info?.needed}`}
          </span>
        )}
      </div>
      <div className="now-card__sweep"><i /></div>
    </button>
  );
}

function Sparkline({ values }) {
  if (values.length < 2) return null;
  const min = Math.min(...values, 70);
  const max = Math.max(...values, 100);
  const pts = values.map((v, i) => [1 + (i * 62) / (values.length - 1), 18 - ((v - min) / (max - min || 1)) * 16]);
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg width="64" height="20" viewBox="0 0 64 20" aria-hidden>
      <path d={d} fill="none" stroke="#9FB8A0" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r="2" fill="#9FB8A0" />
    </svg>
  );
}

function Glance() {
  const { openLens, send } = useOrbit();
  const rows = useSource('attendance').data?.rows || [];
  const tasks = useSource('tasks').data?.tasks || [];
  const focus = useSource('focus').data?.active;
  const money = useSource('money').data;
  const connected = useConnected();
  const now = useNow(focus ? 1000 : 60000);
  const attended = rows.reduce((s, r) => s + (r.attended_classes || 0), 0);
  const total = rows.reduce((s, r) => s + (r.total_classes || 0), 0);
  const overall = total ? (attended / total) * 100 : null;

  let focusValue = null;
  if (focus) {
    const started = toDate(focus.started_at);
    const left = started ? Math.max(0, started.getTime() + focus.planned_minutes * 60000 - now) : 0;
    focusValue = `${pad(Math.floor(left / 60000))}:${pad(Math.floor((left % 60000) / 1000))}`;
  }

  return (
    <>
      {connected.vtop && (
        <button type="button" className="row" onClick={() => openLens('attendance')}>
          <div className="row__main"><div className="row__title row__title--quiet">Attendance</div></div>
          <Sparkline values={[...rows].sort((a, b) => (a.percentage ?? 0) - (b.percentage ?? 0)).map((r) => r.percentage ?? 0)} />
          <span className="row__value row__value--sm" style={{ minWidth: 58, textAlign: 'right' }}>{overall == null ? '—' : `${overall.toFixed(1)}%`}</span>
        </button>
      )}
      <button type="button" className="row" onClick={() => openLens('tasks')}>
        <div className="row__main"><div className="row__title row__title--quiet">Tasks</div></div>
        <span className="row__value row__value--sm">{tasks.length ? `${tasks.length} open` : 'clear'}</span>
      </button>
      <div className="row">
        <div className="row__main"><div className="row__title row__title--quiet">{focus ? `Focus · ${focus.subject}` : 'Focus'}</div></div>
        {focus ? (
          <button type="button" className="row__value row__value--sm" style={{ color: '#FF6A2B' }} onClick={() => openLens('focus')}>{focusValue}</button>
        ) : (
          <button type="button" className="row__btn" onClick={() => send('start focus for 25 minutes')}>Start 25 min</button>
        )}
      </div>
      {money?.total > 0 && (
        <button type="button" className="row" onClick={() => openLens('money')}>
          <div className="row__main"><div className="row__title row__title--quiet">Spent this month</div></div>
          <span className="row__value row__value--sm">{inr(money.total)}</span>
        </button>
      )}
      <button type="button" className="row" onClick={() => openLens('memory')}>
        <div className="row__main"><div className="row__title row__title--quiet">Memory</div></div>
        <svg className="ico" viewBox="0 0 24 24" style={{ color: '#B5AC9F' }}><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="11" cy="18" r="2.5" /><path d="M8.2 7.2 15.6 7.8M7.2 8.3l2.7 7.4M16.8 10.2l-4.4 5.9" /></svg>
      </button>
      <button type="button" className="row" onClick={() => openLens('you')}>
        <div className="row__main"><div className="row__title row__title--quiet">You · tone, goals, routine</div></div>
        <svg className="ico" viewBox="0 0 24 24" style={{ color: '#B5AC9F' }}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
      </button>
    </>
  );
}

function FullRail() {
  const { openLens } = useOrbit();
  const { items } = useAttention();
  return (
    <nav className="rail" aria-label="At a glance">
      <div className="x rail__label rise" style={{ '--d': '0.15s' }}>Now</div>
      <NowCard />
      <div className="x rail__label rise" style={{ '--d': '0.3s' }}>Needs you</div>
      <div className="rise" style={{ '--d': '0.35s' }}>
        {items.length === 0 && <div className="rail__quiet">Nothing needs you right now.</div>}
        {items.map((it) => (
          <button key={it.key} type="button" className="row" onClick={() => openLens(it.lens)}>
            <div className="row__main">
              <div className="row__title">{it.title}</div>
              <div className="row__sub">{it.sub}</div>
            </div>
            <span className="row__value" style={{ color: it.hot ? '#FF6A2B' : '#E8C27A' }}>{it.value}</span>
          </button>
        ))}
      </div>
      <div className="x rail__label rise" style={{ '--d': '0.45s' }}>At a glance</div>
      <div className="rise" style={{ '--d': '0.5s' }}><Glance /></div>
    </nav>
  );
}

function SlimRail({ activeLens }) {
  const { openLens } = useOrbit();
  const now = useNow(60000);
  const blocks = todaysBlocks(useSource('schedule').data);
  const rows = useSource('attendance').data?.rows || [];
  const tasks = useSource('tasks').data?.tasks || [];
  const pendingCount = (useSource('assignments').data?.pending || []).length;
  const { exam, overdue } = useAttention();
  const connected = useConnected();
  const next = blocks.find((b) => b.e > now);
  const attended = rows.reduce((s, r) => s + (r.attended_classes || 0), 0);
  const total = rows.reduce((s, r) => s + (r.total_classes || 0), 0);
  const examMs = exam ? exam.start - now : 0;
  const examLabel = exam ? (examMs < DAY ? `${Math.floor(examMs / 3600000)}h${pad(Math.floor((examMs % 3600000) / 60000))}` : `${Math.floor(examMs / DAY)}d`) : '—';

  const stats = [
    ['schedule', next ? hm(next.s) : 'free', next ? shortCourse(next.title).slice(0, 7) : 'Today', null],
    ['exams', examLabel, exam ? exam.exam_type : 'Exams', exam && examMs < 2 * DAY ? '#FF6A2B' : null],
    ['attendance', total ? ((attended / total) * 100).toFixed(1) : '—', 'Att', null],
    ['assignments', String(overdue || pendingCount), 'LMS', overdue ? '#E8C27A' : null],
    ['tasks', String(tasks.length), 'Tasks', null],
  ].filter(([lens]) => (lens === 'exams' || lens === 'attendance' ? connected.vtop : lens === 'assignments' ? connected.lms : true));
  return (
    <nav className="slim" aria-label="At a glance">
      {stats.map(([lens, value, label, color]) => (
        <button key={lens} type="button" className={`slim__stat ${activeLens === lens ? 'is-active' : ''}`} onClick={() => openLens(lens)} aria-label={`${label} ${value}`}>
          <b style={color ? { color } : undefined}>{value}</b>
          <span className="x">{label}</span>
        </button>
      ))}
      <button type="button" className={`slim__stat ${activeLens === 'memory' ? 'is-active' : ''}`} onClick={() => openLens('memory')} aria-label="Memory">
        <svg className="ico" viewBox="0 0 24 24" style={{ color: '#B5AC9F', width: 18, height: 18 }}><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="11" cy="18" r="2.5" /><path d="M8.2 7.2 15.6 7.8M7.2 8.3l2.7 7.4M16.8 10.2l-4.4 5.9" /></svg>
        <span className="x">Memory</span>
      </button>
      <button type="button" className={`slim__stat ${activeLens === 'you' ? 'is-active' : ''}`} onClick={() => openLens('you')} aria-label="You">
        <svg className="ico" viewBox="0 0 24 24" style={{ color: '#B5AC9F', width: 18, height: 18 }}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
        <span className="x">You</span>
      </button>
    </nav>
  );
}

export default function GlanceRail({ activeLens, docked }) {
  return docked ? <SlimRail activeLens={activeLens} /> : <FullRail />;
}

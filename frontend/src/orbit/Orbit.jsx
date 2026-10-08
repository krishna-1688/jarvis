import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import CoreOrb from './CoreOrb.jsx';
import Titlebar from './Titlebar.jsx';
import GlanceRail, { useAttention } from './GlanceRail.jsx';
import CommandBar from './CommandBar.jsx';
import ReplyCard from './ReplyCard.jsx';
import DayRiver from './DayRiver.jsx';
import Lens from './Lens.jsx';
import Opening, { useOpening } from './Opening.jsx';
import Showcase from './Showcase.jsx';
import { LENS_ORDER, OrbitActions } from './context.js';
import { useConversation } from './useConversation.js';
import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useWebSocketStatus } from '../hooks/useWebSocket.js';
import { useVoiceProcessAlive } from '../hooks/useVoiceProcessAlive.js';
import {
  bunkInfo, cleanCourse, dayWord, dueInfo, greeting, hm, profileView, shortCourse, toDate, upcomingExams,
} from './format.js';
import grainUrl from '../assets/grain.png';
import './orbit.css';

const STATE_LABEL = {
  idle: 'Standing by',
  listening: 'Listening',
  transcribing: 'Transcribing',
  thinking: 'Thinking',
  speaking: 'Speaking',
  error: 'Hit a snag',
};

/** Suggestion chips built from what's actually going on right now. */
function useSuggestions() {
  const now = useNow(60000);
  const attData = useSource('attendance').data;
  const examData = useSource('exams').data;
  const lmsData = useSource('assignments').data;
  const schedData = useSource('schedule').data;
  return useMemo(() => {
    const att = attData?.rows || [];
    const exams = examData?.exams || [];
    const pending = lmsData?.pending || [];
    const blocks = schedData?.blocks || [];
    const h = now.getHours();
    const out = [];

    const exam = upcomingExams(exams, now.getTime())[0];
    if (exam && exam.start - now < 7 * 86400000) {
      out.push({
        text: `give me a quick revision plan for ${cleanCourse(exam.course_name)} ${exam.exam_type}`,
        label: `Revise ${shortCourse(exam.course_name)} for ${exam.exam_type}`,
        icon: 'book', hot: true,
      });
    }
    if (h >= 5 && h < 11) out.push({ text: 'give me my brief', label: 'Morning brief', icon: 'sun' });

    const futureBlocks = blocks.map((b) => toDate(b.start_at)).filter((d) => d && d > now);
    if (h >= 18 || futureBlocks.length === 0) out.push({ text: 'what is my schedule tomorrow', label: 'Tomorrow', icon: 'clock' });
    else out.push({ text: "what's next", label: "What's next?", icon: 'clock' });

    const live = pending.filter((a) => dueInfo(a.due_date).tone !== 'bad');
    if (live.length && out.length < 3) out.push({ text: 'any pending assignments', label: `${live.length} due soon`, icon: 'list' });

    const worst = [...att].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101))[0];
    if (worst && out.length < 3) {
      out.push(bunkInfo(worst.attended_classes || 0, worst.total_classes || 0).safe
        ? { text: `can I skip ${cleanCourse(worst.course_name)} tomorrow`, label: `Skip ${shortCourse(worst.course_name)}?`, icon: 'pct' }
        : { text: `how many ${cleanCourse(worst.course_name)} classes do I need to attend`, label: `${shortCourse(worst.course_name)} recovery`, icon: 'pct' });
    }
    out.push({ text: "what's on my screen", label: "What's on my screen?", icon: 'screen' });
    return out.slice(0, 4);
  }, [now, attData, examData, lmsData, schedData]);
}

/** One line that tells the user the shape of the day before they ask. */
function BriefLine() {
  const now = useNow(60000);
  const blocks = useSource('schedule').data?.blocks || [];
  const { exam, overdue } = useAttention();
  const parts = [];
  const next = blocks.map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e && b.e > now).sort((a, b) => a.s - b.s)[0];
  if (next) {
    parts.push(next.s <= now
      ? <span key="n">{shortCourse(next.title)} is on until <b>{hm(next.e)}</b></span>
      : <span key="n">{shortCourse(next.title)} at <b>{hm(next.s)}</b></span>);
  }
  if (exam && exam.start - now < 7 * 86400000) {
    const hot = exam.start - now < 2 * 86400000;
    parts.push(<span key="e">{shortCourse(exam.course_name)} {exam.exam_type} {dayWord(exam.start) === 'Today' ? 'at' : dayWord(exam.start).toLowerCase()} <b className={hot ? 'is-hot' : ''}>{hm(exam.start)}</b></span>);
  }
  if (overdue) parts.push(<span key="o">{overdue} assignment{overdue > 1 ? 's' : ''} overdue</span>);
  if (!parts.length) return <p className="brief">Your day is clear.</p>;
  return (
    <p className="brief">
      {parts.flatMap((p, i) => (i ? [<span key={`s${i}`} className="brief__sep">/</span>, p] : [p]))}
    </p>
  );
}

function Hud() {
  return (
    <svg className="hud" viewBox="0 0 420 420" aria-hidden>
      <g className="hud__spin">
        <circle className="hud__ticks" cx="210" cy="210" r="200" fill="none" strokeWidth="6" strokeDasharray="1 16.45" />
        <circle className="hud__major" cx="210" cy="210" r="200" fill="none" strokeWidth="10" strokeDasharray="1.4 312.8" />
        <circle className="hud__arc" cx="210" cy="210" r="186" fill="none" strokeWidth="1.2" strokeDasharray="120 1048.7" strokeLinecap="round" />
      </g>
      <circle className="hud__inner" cx="210" cy="210" r="186" fill="none" strokeWidth="1" />
    </svg>
  );
}

const MOTES = [[16, 28, 0], [80, 20, -3], [86, 70, -5], [22, 76, -7], [50, 6, -2]];

function Presence({ voiceState }) {
  const clock = useNow(1000);
  const active = voiceState !== 'idle';
  const you = profileView(useSource('profile').data);
  return (
    <motion.div className="presence" data-active={active} data-voice={voiceState}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, scale: 0.94, transition: { duration: 0.25 } }}>
      <div className="presence__core rise" style={{ '--d': '0.1s' }}>
        <Hud />
        {MOTES.map(([x, y, d]) => <span key={`${x}${y}`} className="mote" style={{ left: `${x}%`, top: `${y}%`, animationDelay: `${d}s` }} />)}
        <div className="presence__orb"><CoreOrb state={voiceState} /></div>
      </div>
      <div className="state-line rise" data-voice={voiceState} style={{ '--d': '0.45s' }}>
        <span className="state-line__dot" />
        <span className="x">{voiceState === 'wake' ? you.wake : STATE_LABEL[voiceState] || STATE_LABEL.idle}</span>
        <span className="state-line__hint mono">
          {voiceState === 'idle' ? `${hm(clock)}:${String(clock.getSeconds()).padStart(2, '0')}` : ''}
        </span>
      </div>
      <div className="greeting rise" style={{ '--d': '0.55s' }}>
        <h1>{greeting()}, <em>{you.name}</em>.</h1>
      </div>
      <div className="rise" style={{ '--d': '0.65s' }}><BriefLine /></div>
    </motion.div>
  );
}

function UserLine({ msg }) {
  return (
    <motion.div className="user-line" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }}
      transition={{ type: 'spring', stiffness: 320, damping: 30 }}>
      {msg.via === 'voice' && <svg viewBox="0 0 24 24" aria-label="Spoken"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0" /></svg>}
      <span>{msg.text}</span>
    </motion.div>
  );
}

function Thinking({ text }) {
  return (
    <motion.div className="thinking" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <span className="thinking__dots"><i /><i /><i /></span>
      <span>{text || 'Thinking'}</span>
    </motion.div>
  );
}

export default function Orbit() {
  const convo = useConversation();
  const { messages, send, progress, pending, expecting, voiceState, voiceDraft, clear } = convo;
  const [lens, setLens] = useState(null);
  const [showcase, setShowcase] = useState(false);
  const [opening, endOpening, introPlayed] = useOpening();
  const streamRef = useRef(null);
  const wsStatus = useWebSocketStatus();
  const voiceAlive = useVoiceProcessAlive();
  const health = useSource('health').data;
  const suggestions = useSuggestions();
  const hero = messages.length === 0;

  const openExternal = useCallback((url) => {
    if (window.jarvis?.openExternal) window.jarvis.openExternal(url);
    else window.open(url, '_blank', 'noopener');
  }, []);
  const openLens = useCallback((name) => setLens((cur) => (cur === name ? null : name)), []);
  const actions = useMemo(() => ({ send, openLens, openExternal }), [send, openLens, openExternal]);

  // New turn: show it from its top, so a tall reply never hides its own
  // headline and the question that produced it.
  useEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    const inner = el.firstElementChild;
    const last = inner?.lastElementChild;
    const lastMsg = messages[messages.length - 1];
    if (last && lastMsg && lastMsg.role !== 'user' && !pending) {
      const prev = last.previousElementSibling;
      const anchor = prev?.classList.contains('user-line') ? prev : last;
      const top = anchor.offsetTop - 12;
      if (el.scrollHeight - top > el.clientHeight) {
        el.scrollTo({ top, behavior: 'smooth' });
        return;
      }
    }
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages, pending, progress]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && lens) setLens(null);
      if (e.altKey && /^[1-9]$/.test(e.key)) {
        e.preventDefault();
        openLens(LENS_ORDER[Number(e.key) - 1]);
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
        e.preventDefault();
        setShowcase((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lens, openLens]);

  const history = useMemo(() => messages.filter((m) => m.role === 'user').map((m) => m.text), [messages]);
  const lastJarvisId = useMemo(() => [...messages].reverse().find((m) => m.role !== 'user')?.id, [messages]);

  return (
    <OrbitActions.Provider value={actions}>
      <div className={`orbit ${introPlayed ? 'is-opening' : ''}`} data-voice={voiceState}>
        <div className="bloom" aria-hidden />
        <div className="grain" aria-hidden style={{ '--grain-url': `url(${grainUrl})` }} />

        <Titlebar voiceState={voiceState} health={health} wsStatus={wsStatus} voiceAlive={voiceAlive}
          onClear={clear} hasMessages={!hero} />

        <div className={`orbit__main ${hero ? '' : 'is-docked'}`}>
          <GlanceRail activeLens={lens} docked={!hero} />

          <main className={`stage ${hero ? 'stage--hero' : 'stage--docked'}`}>
            <AnimatePresence mode="popLayout">
              {hero && <Presence key="presence" voiceState={voiceState} />}
            </AnimatePresence>

            {!hero && (
              <div className="stream" ref={streamRef}>
                <div className="stream__inner">
                  {messages.map((m) => (m.role === 'user'
                    ? <UserLine key={m.id} msg={m} />
                    : <ReplyCard key={m.id} msg={m} isLatest={m.id === lastJarvisId} expecting={expecting} />))}
                  <AnimatePresence>{(pending || progress) && <Thinking key="thinking" text={progress} />}</AnimatePresence>
                </div>
              </div>
            )}

            <div className="rise" style={{ '--d': '0.8s', width: '100%' }}>
              <CommandBar onSend={send} history={history} voiceDraft={voiceDraft} busy={pending}
                suggestions={suggestions} voiceAlive={voiceAlive} voiceState={voiceState} />
            </div>
          </main>
        </div>

        <DayRiver />
        <Lens name={lens} onClose={() => setLens(null)} />
        <Opening show={opening} onDone={endOpening} />
        <AnimatePresence>{showcase && <Showcase key="showcase" onClose={() => setShowcase(false)} />}</AnimatePresence>
      </div>
    </OrbitActions.Provider>
  );
}

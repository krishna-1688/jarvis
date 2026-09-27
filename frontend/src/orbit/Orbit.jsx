import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'motion/react';
import CoreOrb from './CoreOrb.jsx';
import Titlebar from './Titlebar.jsx';
import GlanceRail from './GlanceRail.jsx';
import CommandBar from './CommandBar.jsx';
import ReplyCard from './ReplyCard.jsx';
import DayRiver from './DayRiver.jsx';
import Lens from './Lens.jsx';
import { LENS_ORDER, OrbitActions } from './context.js';
import { useConversation } from './useConversation.js';
import { useSource } from './store.js';
import { useNow } from './useNow.js';
import { useWebSocketStatus } from '../hooks/useWebSocket.js';
import { useVoiceProcessAlive } from '../hooks/useVoiceProcessAlive.js';
import {
  bunkInfo, cleanCourse, dayPart, dayWord, dueInfo, greeting, hm, shortCourse, toDate, upcomingExams,
} from './format.js';
import grainUrl from '../assets/grain.png';
import './orbit.css';

const STATE_LABEL = {
  idle: 'Standing by',
  wake: 'Yes, boss?',
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
    if (h >= 5 && h < 11) out.push({ text: 'give me my brief', label: 'Morning brief', icon: '☀' });

    const exam = upcomingExams(exams, now.getTime())[0];
    if (exam && exam.start - now < 7 * 86400000) {
      out.push({
        text: `give me a quick revision plan for ${cleanCourse(exam.course_name)} ${exam.exam_type}`,
        label: `Revise ${shortCourse(exam.course_name)} · ${dayWord(exam.start).toLowerCase()}`,
        icon: '✦',
      });
    }

    const futureBlocks = blocks.map((b) => toDate(b.start_at)).filter((d) => d && d > now);
    if (h >= 18 || futureBlocks.length === 0) out.push({ text: 'what is my schedule tomorrow', label: 'Tomorrow', icon: '◷' });
    else out.push({ text: "what's next", label: "What's next?", icon: '◷' });

    const worst = [...att].sort((a, b) => (a.percentage ?? 101) - (b.percentage ?? 101))[0];
    if (worst && bunkInfo(worst.attended_classes || 0, worst.total_classes || 0).safe) {
      out.push({ text: `can I skip ${cleanCourse(worst.course_name)} tomorrow`, label: `Skip ${shortCourse(worst.course_name)}?`, icon: '◔' });
    } else if (worst) {
      out.push({ text: `how many ${cleanCourse(worst.course_name)} classes do I need to attend`, label: `${shortCourse(worst.course_name)} recovery`, icon: '◔' });
    }

    const live = pending.filter((a) => dueInfo(a.due_date).tone !== 'bad');
    if (live.length) out.push({ text: 'any pending assignments', label: `${live.length} due soon`, icon: '▤' });
    return out.slice(0, 5);
  }, [now, attData, examData, lmsData, schedData]);
}

/** One line that tells KK the shape of the day before he asks. */
function useBriefLine() {
  const now = useNow(60000);
  const blocks = useSource('schedule').data?.blocks || [];
  const exams = useSource('exams').data?.exams || [];
  const pending = useSource('assignments').data?.pending || [];
  const parts = [];
  const next = blocks.map((b) => ({ ...b, s: toDate(b.start_at), e: toDate(b.end_at) }))
    .filter((b) => b.s && b.e && b.e > now).sort((a, b) => a.s - b.s)[0];
  if (next) parts.push(next.s <= now ? `${shortCourse(next.title)} is on now` : `${shortCourse(next.title)} at ${hm(next.s)}`);
  const exam = upcomingExams(exams, now.getTime())[0];
  if (exam && exam.start - now < 7 * 86400000) parts.push(`${shortCourse(exam.course_name)} ${exam.exam_type} ${dayWord(exam.start).toLowerCase()}`);
  const soon = pending.filter((a) => dueInfo(a.due_date).tone === 'warn').length;
  if (soon) parts.push(`${soon} assignment${soon > 1 ? 's' : ''} due soon`);
  return parts.length ? parts.join('  ·  ') : 'Your day is clear.';
}

function Greeting() {
  const brief = useBriefLine();
  return (
    <motion.div className="greeting" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10, transition: { duration: 0.18 } }} transition={{ delay: 0.25, type: 'spring', stiffness: 200, damping: 24 }}>
      <h1>{greeting()}, <span className="grad">KK</span>.</h1>
      <p>{brief}</p>
    </motion.div>
  );
}

function UserLine({ msg }) {
  return (
    <motion.div className="user-line" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }}
      transition={{ type: 'spring', stiffness: 320, damping: 30 }}>
      {msg.via === 'voice' && <span className="user-line__via" title="Spoken">◉</span>}
      <span>{msg.text}</span>
    </motion.div>
  );
}

function Thinking({ text }) {
  return (
    <motion.div className="thinking" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <span className="thinking__dots"><i /><i /><i /></span>
      <span className="shimmer">{text || 'Thinking'}</span>
    </motion.div>
  );
}

export default function Orbit() {
  const convo = useConversation();
  const { messages, send, progress, pending, expecting, voiceState, voiceDraft, clear } = convo;
  const [lens, setLens] = useState(null);
  const streamRef = useRef(null);
  const wsStatus = useWebSocketStatus();
  const voiceAlive = useVoiceProcessAlive();
  const health = useSource('health').data;
  const suggestions = useSuggestions();
  const now = useNow(300000);
  const hero = messages.length === 0;

  const openExternal = useCallback((url) => {
    if (window.jarvis?.openExternal) window.jarvis.openExternal(url);
    else window.open(url, '_blank', 'noopener');
  }, []);
  const openLens = useCallback((name) => setLens((cur) => (cur === name ? null : name)), []);
  const actions = useMemo(() => ({ send, openLens, openExternal }), [send, openLens, openExternal]);

  // New turn: show it from its top. A tall reply (a full attendance card)
  // used to be scrolled to its bottom, hiding its own headline and the
  // question that produced it.
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
      if (e.altKey && /^[1-7]$/.test(e.key)) {
        e.preventDefault();
        openLens(LENS_ORDER[Number(e.key) - 1]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lens, openLens]);

  const history = useMemo(() => messages.filter((m) => m.role === 'user').map((m) => m.text), [messages]);
  const lastJarvisId = useMemo(() => [...messages].reverse().find((m) => m.role !== 'user')?.id, [messages]);

  return (
    <OrbitActions.Provider value={actions}>
      <div className="orbit" data-daypart={dayPart(now.getHours())} data-voice={voiceState}>
        <div className="aurora" aria-hidden><i /><i /><i /></div>
        <div className="grain" aria-hidden style={{ '--grain-url': `url(${grainUrl})` }} />

        <Titlebar voiceState={voiceState} health={health} wsStatus={wsStatus} voiceAlive={voiceAlive}
          onClear={clear} hasMessages={!hero} />

        <div className="orbit__main">
          <GlanceRail activeLens={lens} />

          <LayoutGroup>
            <main className={`stage ${hero ? 'stage--hero' : 'stage--docked'}`}>
              <motion.div layout className="stage__presence" transition={{ type: 'spring', stiffness: 170, damping: 26 }}>
                <motion.div layout className="stage__orb" transition={{ type: 'spring', stiffness: 170, damping: 26 }}>
                  <CoreOrb state={voiceState} />
                </motion.div>
                <motion.div layout="position" className="stage__state">
                  <span className={`state-pill state-${voiceState}`}>{STATE_LABEL[voiceState] || STATE_LABEL.idle}</span>
                  {!hero && !voiceAlive && <span className="dim small">voice offline · typing works</span>}
                  {hero && voiceAlive && <span className="dim small">Say “Hey Jarvis” or just start typing</span>}
                </motion.div>
              </motion.div>

              <AnimatePresence mode="popLayout">
                {hero && <Greeting key="greet" />}
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

              <CommandBar onSend={send} history={history} voiceDraft={voiceDraft} busy={pending}
                suggestions={suggestions} voiceAlive={voiceAlive} voiceState={voiceState} />
            </main>
          </LayoutGroup>
        </div>

        <DayRiver />
        <Lens name={lens} onClose={() => setLens(null)} />
      </div>
    </OrbitActions.Provider>
  );
}

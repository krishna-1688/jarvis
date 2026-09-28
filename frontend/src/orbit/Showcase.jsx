import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import CoreOrb from './CoreOrb.jsx';

/**
 * Showcase (Ctrl+Shift+D): a guided tour for interviews and demos. It
 * uses a demo profile and never reads the real feeds, so marks,
 * attendance and messages stay private while presenting. Every number
 * on these slides was measured on this build.
 */
const STEPS = [
  {
    tab: 'Wake word', mood: 'listening',
    title: 'Always listening\nfor one phrase.',
    body: 'Say “Hey Jarvis” and it answers, even with this window closed. Wake-word detection runs on this laptop; no audio leaves it until you’ve said it.',
    caption: <>Yes? <em>I’m listening.</em></>,
    stats: [['Local', '', 'Wake detection'], ['0', 'B', 'Audio sent before wake'], ['12–14', 'h', 'Designed to run']],
  },
  {
    tab: 'Campus data', mood: 'speaking',
    title: 'Knows your\nsemester.',
    body: 'Attendance, marks, exams, timetable and LMS deadlines, pulled from VTOP and Moodle and answered in one sentence, with the 75% maths already done.',
    caption: <>You’re at 88% overall. <em>Operating Systems</em> is the tightest, with 2 classes to spare.</>,
    stats: [['6', '', 'Live data feeds'], ['75', '%', 'Bunk maths built in'], ['1', 'line', 'To ask anything']],
  },
  {
    tab: 'Screen vision', mood: 'speaking',
    title: 'It sees what\nyou’re looking at.',
    body: 'Ask “what’s on my screen?” and Jarvis takes one screenshot, reads it with a vision model and answers aloud. Nothing is saved, and if one AI provider is busy it quietly switches to another.',
    caption: <>You’re reading <em>Operating Systems, chapter 6</em>. The highlighted box lists the four conditions for deadlock.</>,
    stats: [['0.4', 's', 'Capture'], ['~2', 's', 'Answer'], ['0', 'B', 'Kept on disk']],
  },
  {
    tab: 'Memory', mood: 'thinking',
    title: 'Remembers,\nand connects.',
    body: 'Every conversation becomes a web of courses, people, exams and topics. Links you use grow stronger, unused ones fade, and recall is fast enough to run on every question.',
    caption: <>Last week you asked about <em>Dijkstra</em> before your DSA quiz. Want a quick recap?</>,
    stats: [['~6', 'ms', 'Recall at 50k events'], ['21', 'days', 'Link half-life'], ['8/8', '', 'Crash tests survived']],
  },
  {
    tab: 'PC control', mood: 'speaking',
    title: 'Runs the\nlaptop too.',
    body: 'Volume, brightness, apps, windows, clipboard, screenshots and files, by voice. Anything destructive, like a shutdown, asks first.',
    caption: <>Volume’s at 40. I’ve opened <em>VS Code</em> for you.</>,
    stats: [['Voice', '', 'Apps · volume · windows'], ['Asks', '', 'Before shutdown'], ['Local', '', 'On this PC']],
  },
  {
    tab: 'Always on', mood: 'idle',
    title: 'On all day,\nbarely there.',
    body: 'At rest nothing animates and nothing polls hard. The dashboard opens on demand and closes back to zero, and two AI providers take over from each other when one runs out.',
    caption: <>I’ll be here. <em>Just say the word.</em></>,
    stats: [['Still', '', 'The core at rest'], ['2', '', 'AI providers, auto-failover'], ['1', 'key', 'Ctrl+J to open']],
  },
];

export default function Showcase({ onClose }) {
  const [i, setI] = useState(0);
  const step = STEPS[i];

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'ArrowRight') setI((v) => Math.min(STEPS.length - 1, v + 1));
      if (e.key === 'ArrowLeft') setI((v) => Math.max(0, v - 1));
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <motion.div className="showcase" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.4 }}>
      <header className="showcase__head">
        <span className="brand-word">JARVIS</span>
        <span className="showcase__badge">
          <svg className="ico" viewBox="0 0 24 24" style={{ width: 13, height: 13 }}><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" /></svg>
          Showcase · demo profile · real marks and messages hidden
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono dim" style={{ fontSize: 12 }}>← → STEP · ESC EXIT</span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Exit showcase">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div className="showcase__body">
        <div className="showcase__stagecore">
          <div className="showcase__orb"><CoreOrb state={step.mood} /></div>
          <AnimatePresence mode="wait">
            <motion.div key={i} className="showcase__caption"
              initial={{ opacity: 0, y: 10, filter: 'blur(6px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, y: -8, filter: 'blur(6px)' }} transition={{ duration: 0.45 }}>
              {step.caption}
            </motion.div>
          </AnimatePresence>
          <span className="x" style={{ color: '#FF8A4C' }}>{step.mood === 'idle' ? 'Standing by' : step.mood}</span>
        </div>

        <AnimatePresence mode="wait">
          <motion.div key={i} className="showcase__copy"
            initial="hidden" animate="show" exit="hidden"
            variants={{ hidden: {}, show: { transition: { staggerChildren: 0.08 } } }}>
            {[
              <div key="s" className="showcase__step">{String(i + 1).padStart(2, '0')} / {String(STEPS.length).padStart(2, '0')} — {step.tab.toUpperCase()}</div>,
              <h1 key="h">{step.title}</h1>,
              <p key="p">{step.body}</p>,
              <div key="st" className="showcase__stats">
                {step.stats.map(([v, unit, label]) => (
                  <div key={label}><b>{v}{unit && <small> {unit}</small>}</b><span className="x">{label}</span></div>
                ))}
              </div>,
              <div key="n" className="showcase__nav">
                {i < STEPS.length - 1
                  ? <button type="button" className="btn btn--ember" style={{ height: 52, padding: '0 26px', fontSize: 16 }} onClick={() => setI(i + 1)}>Next: {STEPS[i + 1].tab}</button>
                  : <button type="button" className="btn btn--ember" style={{ height: 52, padding: '0 26px', fontSize: 16 }} onClick={onClose}>Try it live</button>}
                {i > 0 && <button type="button" className="btn" style={{ height: 52, padding: '0 22px', fontSize: 16 }} onClick={() => setI(i - 1)}>Back</button>}
              </div>,
            ].map((el) => (
              <motion.div key={el.key} variants={{ hidden: { opacity: 0, y: 14, filter: 'blur(6px)' }, show: { opacity: 1, y: 0, filter: 'blur(0px)' } }}
                transition={{ type: 'spring', stiffness: 200, damping: 26 }}>
                {el}
              </motion.div>
            ))}
          </motion.div>
        </AnimatePresence>
      </div>

      <footer className="showcase__tabs">
        {STEPS.map((s, k) => (
          <button key={s.tab} type="button" className={`showcase__tab ${k === i ? 'is-on' : k < i ? 'is-past' : ''}`} onClick={() => setI(k)}>
            <span className="mono">{String(k + 1).padStart(2, '0')}</span>{s.tab}
          </button>
        ))}
      </footer>
    </motion.div>
  );
}

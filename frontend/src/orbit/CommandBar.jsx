import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { stopSpeaking } from '../lib/tts.js';
import { useSource } from './store.js';
import { profileView } from './format.js';

const PLACEHOLDERS = [
  'Ask anything, or tell me what to do',
  'what’s my attendance',
  'what’s next',
  'remind me to submit fees friday',
  'start focus on DAA for 25 minutes',
  'what’s on my screen',
];

const ICONS = {
  book: <path d="M4 19.5V5a2 2 0 0 1 2-2h12v16H6.5A2.5 2.5 0 0 0 4 21.5" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  list: <path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" />,
  pct: <><path d="M19 5 5 19" /><circle cx="6.5" cy="6.5" r="2.5" /><circle cx="17.5" cy="17.5" r="2.5" /></>,
  screen: <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8M12 17v4" /></>,
};

const WAVE = [12, 22, 30, 18, 26, 10, 20, 28, 14];

/**
 * Typing anywhere in the window lands here (no need to click first);
 * Ctrl+K / "/" focus it, ↑/↓ walk previous commands, Esc clears. While
 * Jarvis listens, the bar becomes the voice surface: a live core, the
 * state, and the transcript as it arrives.
 */
export default function CommandBar({ onSend, history = [], voiceDraft, busy, suggestions = [], voiceAlive, voiceState }) {
  const [value, setValue] = useState('');
  const [focused, setFocused] = useState(false);
  const [ph, setPh] = useState(0);
  const histIdx = useRef(-1);
  const inputRef = useRef(null);

  useEffect(() => {
    if (focused) return undefined;
    const id = setInterval(() => setPh((i) => (i + 1) % PLACEHOLDERS.length), 4200);
    return () => clearInterval(id);
  }, [focused]);

  useEffect(() => {
    const onKey = (e) => {
      const el = inputRef.current;
      if (!el) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        el.focus();
        el.select();
        return;
      }
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === '/' && !e.ctrlKey) {
        e.preventDefault();
        el.focus();
        return;
      }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) el.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const submit = (text) => {
    const t = (text ?? value).trim();
    if (!t) return;
    onSend(t);
    setValue('');
    histIdx.current = -1;
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowUp' && history.length) {
      e.preventDefault();
      histIdx.current = Math.min(history.length - 1, histIdx.current + 1);
      setValue(history[history.length - 1 - histIdx.current]);
    } else if (e.key === 'ArrowDown' && histIdx.current >= 0) {
      e.preventDefault();
      histIdx.current -= 1;
      setValue(histIdx.current < 0 ? '' : history[history.length - 1 - histIdx.current]);
    } else if (e.key === 'Escape') {
      setValue('');
      e.currentTarget.blur();
    }
  };

  const wakeLabel = profileView(useSource('profile').data).wake;
  const listening = voiceState === 'listening' || voiceState === 'transcribing' || voiceState === 'wake' || !!voiceDraft;
  const label = voiceState === 'transcribing' ? 'Transcribing' : voiceState === 'wake' ? wakeLabel : 'Listening';
  const shown = voiceDraft || value;

  return (
    <div className={`cmd ${focused ? 'is-focused' : ''} ${busy ? 'is-busy' : ''} ${listening ? 'is-listening' : ''}`}>
      <AnimatePresence initial={false}>
        {suggestions.length > 0 && !value && !listening && (
          <motion.div className="cmd__suggest" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}>
            {suggestions.map((s) => (
              <button key={s.text} type="button" className={`suggest ${s.hot ? 'is-hot' : ''}`} onClick={() => submit(s.text)}>
                {s.icon && ICONS[s.icon] && <svg viewBox="0 0 24 24" aria-hidden>{ICONS[s.icon]}</svg>}
                {s.label || s.text}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      <form className="cmd__bar" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <span className={`cmd__lead ${voiceAlive ? '' : 'is-off'}`}
          title={voiceAlive ? 'Voice is on. Say “Hey Jarvis”' : 'Voice process not running (python backend/run.py)'}>
          {listening ? (
            <span className="mini-core" aria-hidden>
              <span className="mini-core__halo" />
              <span className="mini-core__body"><span className="mini-core__plasma" /><span className="mini-core__heart" /></span>
            </span>
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
          )}
        </span>
        <div className="cmd__field">
          {listening && <span className="x cmd__listen-label">{label}</span>}
          <input
            ref={inputRef}
            className="cmd__input"
            value={shown}
            readOnly={!!voiceDraft}
            spellCheck={false}
            aria-label="Ask Jarvis"
            onChange={(e) => { stopSpeaking(); setValue(e.target.value); }}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={listening ? 'Go ahead, I’m listening' : PLACEHOLDERS[ph]}
          />
        </div>
        {listening ? (
          <span className="wave" aria-hidden>
            {WAVE.map((h, i) => <i key={i} style={{ height: h, animationDelay: `${-0.11 * i}s` }} />)}
          </span>
        ) : (
          <kbd className="cmd__kbd">Ctrl K</kbd>
        )}
        <button type="submit" className="cmd__send" disabled={!value.trim() || busy} aria-label="Send">
          <svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
        </button>
      </form>
    </div>
  );
}

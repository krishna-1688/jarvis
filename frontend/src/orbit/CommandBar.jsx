import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { stopSpeaking } from '../lib/tts.js';

const PLACEHOLDERS = [
  'Ask anything, or tell me what to do…',
  'what’s my attendance',
  'what’s next',
  'remind me to submit fees friday',
  'start focus on DAA for 25 minutes',
  'can I skip compiler design tomorrow',
];

/**
 * Typing anywhere in the window lands here (no need to click first);
 * Ctrl+K / "/" focus it, ↑/↓ walk previous commands, Esc clears.
 */
export default function CommandBar({ onSend, history = [], voiceDraft, busy, suggestions = [], voiceAlive, voiceState }) {
  const [value, setValue] = useState('');
  const [focused, setFocused] = useState(false);
  const [ph, setPh] = useState(0);
  const histIdx = useRef(-1);
  const inputRef = useRef(null);

  useEffect(() => {
    if (focused) return undefined;
    const id = setInterval(() => setPh((i) => (i + 1) % PLACEHOLDERS.length), 3800);
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
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        el.focus();
        // the keystroke itself lands in the input because focus moves first
      }
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
    const past = history;
    if (e.key === 'ArrowUp' && past.length) {
      e.preventDefault();
      histIdx.current = Math.min(past.length - 1, histIdx.current + 1);
      setValue(past[past.length - 1 - histIdx.current]);
    } else if (e.key === 'ArrowDown' && histIdx.current >= 0) {
      e.preventDefault();
      histIdx.current -= 1;
      setValue(histIdx.current < 0 ? '' : past[past.length - 1 - histIdx.current]);
    } else if (e.key === 'Escape') {
      setValue('');
      e.currentTarget.blur();
    }
  };

  const shown = voiceDraft || value;
  const listening = voiceState === 'listening' || voiceState === 'transcribing' || voiceState === 'wake';

  return (
    <div className={`cmd ${focused ? 'is-focused' : ''} ${busy ? 'is-busy' : ''} ${voiceDraft ? 'is-voice' : ''}`}>
      <AnimatePresence initial={false}>
        {suggestions.length > 0 && !value && !voiceDraft && (
          <motion.div className="cmd__suggest" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}>
            {suggestions.map((s) => (
              <button key={s.text} type="button" className="suggest" onClick={() => submit(s.text)}>
                {s.icon && <span className="suggest__icon" aria-hidden>{s.icon}</span>}
                {s.label || s.text}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      <form className="cmd__bar" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <span className={`cmd__mic ${listening ? 'is-live' : ''} ${voiceAlive ? '' : 'is-off'}`}
          title={voiceAlive ? 'Voice is on — say “Hey Jarvis”' : 'Voice process not running (python backend/run.py)'}>
          <svg viewBox="0 0 24 24"><path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
        </span>
        <input
          ref={inputRef}
          className="cmd__input"
          value={shown}
          readOnly={!!voiceDraft}
          spellCheck={false}
          onChange={(e) => { stopSpeaking(); setValue(e.target.value); }}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={listening ? 'Listening…' : PLACEHOLDERS[ph]}
        />
        <kbd className="cmd__kbd">Ctrl K</kbd>
        <button type="submit" className="cmd__send" disabled={!value.trim() || busy} aria-label="Send">
          <svg viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6" /></svg>
        </button>
      </form>
    </div>
  );
}

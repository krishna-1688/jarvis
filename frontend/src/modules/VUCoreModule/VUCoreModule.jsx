import { useEffect, useRef, useState } from 'react';
import VUMeter from '../../components/VUMeter/VUMeter.jsx';
import Teleprinter from '../../components/Teleprinter/Teleprinter.jsx';
import { api } from '../../api.js';
import { USE_MOCK } from '../../mock.js';
import { playSuccessBlip } from '../../lib/sound.js';
import { speakReply, stopSpeaking } from '../../lib/tts.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { setLocalVoiceState } from '../../hooks/useVoiceState.js';
import './VUCoreModule.css';

function mockReply(text) {
  return `Mock reply — you said "${text}". Flip USE_MOCK off in mock.js to talk to the real backend.`;
}

// H.6: rotating command-line placeholder — idle hint copy, not a tutorial;
// pauses the moment the field is focused so it never fights typing.
const PLACEHOLDER_EXAMPLES = [
  'Type a command…',
  "try: what's my attendance",
  'try: schedule today',
  'try: remind me to submit fees friday',
  'try: spent 200 on lunch',
];
const PLACEHOLDER_ROTATE_MS = 4000;

// Human-readable status for whatever Jarvis is doing right now — the
// ambient glow (VoicePresence/ChassisLighting) is atmospheric, not
// literally readable, so this is the one place that says it in words.
const STATE_LABEL = {
  idle: "Standby — say 'Hey Jarvis'",
  wake: 'Yes, boss?',
  listening: 'Listening…',
  transcribing: 'Transcribing…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  error: 'Something went wrong',
};

const STATE_CHIP_TONE = {
  idle: 'pending',
  wake: 'warn',
  listening: 'warn',
  transcribing: 'warn',
  thinking: 'pending',
  speaking: 'ok',
  error: 'error',
};

/**
 * The non-removable 6×6 core (Section 1.5/1.8): meter + teleprinter above a
 * terminal-style conversation log, with the command line pinned to the
 * module's bottom edge. No chat bubbles — this is a log inside an
 * instrument, not a messaging app.
 */
export default function VUCoreModule() {
  const [voiceState, setVoiceState] = useState('idle');
  const [transcript, setTranscript] = useState('');
  const [log, setLog] = useState([]);
  const [input, setInput] = useState('');
  const [inputFocused, setInputFocused] = useState(false);
  const [placeholderIndex, setPlaceholderIndex] = useState(0);
  const [voiceTyping, setVoiceTyping] = useState(false);
  const logRef = useRef(null);
  const voiceTypingTimer = useRef(null);

  // Typed-command state also mirrors into the shared voice-state store
  // (useVoiceState.js) so ambient effects elsewhere in the chassis —
  // VoicePresence, ChassisLighting — react to a typed exchange exactly
  // like a spoken one, not just to real mic/TTS events.
  const updateVoiceState = (next) => {
    setVoiceState(next);
    setLocalVoiceState(next);
  };

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log]);

  useEffect(() => () => {
    if (voiceTypingTimer.current) window.clearInterval(voiceTypingTimer.current);
  }, []);

  useEffect(() => {
    if (inputFocused) return undefined;
    const id = setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % PLACEHOLDER_EXAMPLES.length);
    }, PLACEHOLDER_ROTATE_MS);
    return () => clearInterval(id);
  }, [inputFocused]);

  // V.2a boot sequence's closing beat — lands right as the VU meter's
  // ripple finishes (see VUMeter.css's [data-booting] animation-delay).
  // Only fires during the boot window BootSequence.jsx opens on mount,
  // never again after.
  useEffect(() => {
    if (typeof document === 'undefined' || !document.body.hasAttribute('data-booting')) return undefined;
    const t = window.setTimeout(() => {
      setLog((prev) => [...prev, { role: 'system', text: 'JARVIS ONLINE · FALL SEM 2026-27' }]);
    }, 700);
    return () => window.clearTimeout(t);
  }, []);

  // Animates a real spoken transcript into the command bar itself,
  // character by character, before it lands in the log — the same
  // visual language typing already has, so a voice turn reads as "Jarvis
  // watched you say this" instead of just materializing in the log with
  // no connection to the input line typed commands use.
  const animateVoiceTranscript = (text) => {
    if (voiceTypingTimer.current) window.clearInterval(voiceTypingTimer.current);
    setVoiceTyping(true);
    const total = Math.min(900, 150 + text.length * 8);
    const stepMs = Math.max(8, total / text.length);
    let i = 0;
    voiceTypingTimer.current = window.setInterval(() => {
      i += 1;
      setInput(text.slice(0, i));
      if (i >= text.length) {
        window.clearInterval(voiceTypingTimer.current);
        voiceTypingTimer.current = null;
        window.setTimeout(() => {
          setLog((prev) => [...prev, { role: 'user', text }]);
          setInput('');
          setVoiceTyping(false);
        }, 200);
      }
    }, stepMs);
  };

  // V.1: real voice events bridged from jarvis.py (the separate process
  // that actually owns the mic — wake word, STT, TTS) via server.py's
  // /internal/voice_event -> WS broadcast. This is what makes the meter
  // and log reflect an actual spoken conversation, not just typed
  // commands — previously nothing wired the two together at all.
  useWebSocket((msg) => {
    switch (msg.type) {
      case 'wake':
        setVoiceState('wake');
        break;
      case 'listening_start':
        setVoiceState('listening');
        break;
      case 'transcribing_start':
        setVoiceState('transcribing');
        break;
      case 'listening_end':
        // Closes out both 'listening' and 'transcribing' — see
        // useVoiceState.js for why this one event covers both.
        setVoiceState((s) => (s === 'listening' || s === 'transcribing' ? 'idle' : s));
        break;
      case 'processing_start':
        setVoiceState('thinking');
        break;
      case 'processing_end':
        setVoiceState((s) => (s === 'thinking' ? 'idle' : s));
        break;
      case 'speaking_start':
        setVoiceState('speaking');
        break;
      case 'speaking_end':
        setVoiceState('idle');
        break;
      case 'transcript':
        if (msg.text) animateVoiceTranscript(msg.text);
        break;
      case 'reply':
        if (msg.text) setLog((prev) => [...prev, { role: 'jarvis', text: msg.text }]);
        break;
      default:
        break;
    }
  });

  const handleSubmit = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    stopSpeaking(); // barge-in: a new command always cuts off whatever Jarvis was mid-saying
    setInput('');
    setLog((prev) => [...prev, { role: 'user', text }]);
    // 'thinking', not 'speaking' — nothing's been said yet, we're waiting
    // on the backend. Flipping to 'speaking' only once a reply actually
    // exists keeps the ambient presence effects honest about what's
    // happening, and gives the wait itself a distinct, calmer visual.
    updateVoiceState('thinking');

    if (USE_MOCK) {
      const reply = mockReply(text);
      setTranscript(reply);
      updateVoiceState('speaking');
      speakReply(reply);
      window.setTimeout(() => {
        setLog((prev) => [...prev, { role: 'jarvis', text: reply }]);
        updateVoiceState('idle');
        setTranscript('');
        playSuccessBlip();
      }, Math.min(900, 150 + reply.length * 8));
      return;
    }

    await runCommand(text, false);
  };

  // H.5: the backend's `error` field (e.g. "no_data") is an internal
  // status code, never meant to be shown or spoken — every handler
  // already pairs it with a friendly display/spoken message. This is
  // just a safety net in case something ever slips through as a bare
  // snake_case token instead of a real sentence: show a holding message
  // and retry once rather than flashing a code at the user.
  const looksLikeRawCode = (s) => !!s && /^[a-z][a-z_]*$/.test(s.trim()) && !s.includes(' ');

  const runCommand = async (text, isRetry) => {
    try {
      const result = await api.command(text);

      if (!isRetry && looksLikeRawCode(result.display)) {
        setTranscript('—give me a moment—');
        window.setTimeout(() => runCommand(text, true), 1500);
        return;
      }

      const reply = result.display;
      const structured = result.ok && result.data && Object.keys(result.data).length > 0;
      setTranscript(reply);
      updateVoiceState(result.ok ? 'speaking' : 'error');
      speakReply(result.spoken || reply);
      window.setTimeout(() => {
        setLog((prev) => [...prev, {
          role: 'jarvis', text: reply,
          flagged: !result.ok, structured: !!structured,
        }]);
        updateVoiceState(result.ok ? 'idle' : 'error');
        setTranscript('');
        if (result.ok) playSuccessBlip();
        if (!result.ok) window.setTimeout(() => updateVoiceState('idle'), 400);
      }, Math.min(900, 150 + reply.length * 8));
    } catch {
      const reply = 'Backend unreachable — retrying stays automatic elsewhere on the console.';
      setLog((prev) => [...prev, { role: 'system', text: reply }]);
      speakReply(reply);
      updateVoiceState('error');
      setTranscript('');
      window.setTimeout(() => updateVoiceState('idle'), 400);
    }
  };

  // The VU meter's mock amplitude generator only knows the original five
  // states — 'transcribing' would otherwise fall through to flat/silent,
  // right when the meter should still read as "busy."
  const meterState = voiceState === 'transcribing' ? 'thinking' : voiceState;

  return (
    <div className="vu-core">
      <div className="vu-core__status-row">
        <VUMeter state={meterState} />
        <span className={`chip chip--${STATE_CHIP_TONE[voiceState] || 'pending'} vu-core__status-chip`}>
          {STATE_LABEL[voiceState] || STATE_LABEL.idle}
        </span>
      </div>
      <Teleprinter text={transcript} />

      <div className="vu-core__log" ref={logRef}>
        {log.length === 0 && <div className="vu-core__log-empty mono">— no conversation yet —</div>}
        {log.map((entry, i) => (
          <div
            key={i}
            className={[
              'vu-core__log-line',
              `vu-core__log-line--${entry.role}`,
              entry.flagged && 'is-flagged',
              entry.structured && 'is-structured',
              'mono',
            ].filter(Boolean).join(' ')}
          >
            {entry.role === 'user' ? `> ${entry.text}` : entry.text}
          </div>
        ))}
      </div>

      <form className="vu-core__command" onSubmit={handleSubmit}>
        <span className="vu-core__prompt">{voiceTyping ? '\u{1F399}' : '›'}</span>
        <input
          className={`vu-core__input mono${voiceTyping ? ' is-voice-typing' : ''}`}
          value={input}
          readOnly={voiceTyping}
          onChange={(e) => {
            // Barge-in: the moment the user starts typing over Jarvis
            // talking, cut the audio rather than let it drone on under
            // them — matches how you'd naturally interrupt someone.
            if (voiceState === 'speaking') stopSpeaking();
            setInput(e.target.value);
          }}
          onFocus={() => setInputFocused(true)}
          onBlur={() => setInputFocused(false)}
          placeholder={PLACEHOLDER_EXAMPLES[placeholderIndex]}
        />
      </form>
    </div>
  );
}

import { useVoiceState } from '../../hooks/useVoiceState.js';
import './VoicePresence.css';

const STATE_CLASS = {
  wake: 'is-wake',
  listening: 'is-listening',
  transcribing: 'is-thinking',
  thinking: 'is-thinking',
  speaking: 'is-speaking',
  error: 'is-error',
};

/**
 * S.3 — Jarvis's presence made visible: a glow living behind the top rail
 * that answers each voice state with its own color and rhythm (gold while
 * listening, a cool sweep while thinking, warm copper while speaking).
 * Independent WS subscriber via useVoiceState, same as ChassisLighting —
 * this is the foreground "it's alive" signal, ChassisLighting stays the
 * faint whole-chassis tint underneath it.
 */
export default function VoicePresence() {
  const state = useVoiceState();
  const cls = STATE_CLASS[state] || '';
  return (
    <div className={`voice-presence ${cls}`} aria-hidden="true">
      <div className="voice-presence__edge" />
      <div className="voice-presence__aura" />
    </div>
  );
}

import { useVoiceState } from '../../hooks/useVoiceState.js';
import './ChassisLighting.css';

const STATE_CLASS = {
  wake: 'is-listening',
  listening: 'is-listening',
  thinking: 'is-thinking',
  speaking: 'is-speaking',
};

/**
 * V.2c: the whole chassis breathes with Jarvis's current state — warms
 * toward the display glow while listening, cools with a slow breathing
 * brightness while thinking, warms toward signal while speaking. A
 * fixed soft-light overlay driven by real voice events (useVoiceState),
 * not a per-module effect — never intercepts clicks.
 */
export default function ChassisLighting() {
  const state = useVoiceState();
  return <div className={`chassis-lighting ${STATE_CLASS[state] || ''}`} aria-hidden="true" />;
}

import { useEffect, useState } from 'react';
import { useWebSocket } from './useWebSocket.js';

const START_EVENTS = {
  wake: 'wake',
  listening_start: 'listening',
  transcribing_start: 'transcribing',
  processing_start: 'thinking',
  speaking_start: 'speaking',
};

const END_EVENTS = {
  processing_end: 'thinking',
  speaking_end: 'speaking',
};

// Typed-command override: VUCoreModule pushes its own local voiceState
// here (via setLocalVoiceState) so ambient effects — VoicePresence,
// ChassisLighting — react the same way to a typed exchange as a spoken
// one, instead of only ever lighting up for real mic/TTS events. A
// module-level pub/sub (mirrors useWebSocket's shared-store pattern)
// rather than prop-drilling, since these effects mount far from
// VUCoreModule in the tree.
let localState = null;
const localListeners = new Set();

export function setLocalVoiceState(next) {
  localState = next === 'idle' ? null : next;
  localListeners.forEach((fn) => fn(localState));
}

/**
 * Reactive global voice state ('idle'|'wake'|'listening'|'thinking'|
 * 'speaking'|'error'), merging real voice events (bridged from jarvis.py
 * via server.py's /internal/voice_event -> WS broadcast) with local
 * typed-command activity. A local override always wins while active —
 * it clears itself back to null (falling through to the WS state) the
 * moment VUCoreModule reports 'idle'.
 */
export function useVoiceState() {
  const [wsState, setWsState] = useState('idle');
  const [local, setLocal] = useState(localState);

  useWebSocket((msg) => {
    if (START_EVENTS[msg.type]) {
      setWsState(START_EVENTS[msg.type]);
    } else if (msg.type === 'listening_end') {
      // Closes out BOTH 'listening' and 'transcribing' — transcribing
      // starts partway through a single listen() call on the backend
      // and listening_end always fires once that call returns, success
      // or not, so it's the one reliable "done with this leg" signal.
      setWsState((s) => (s === 'listening' || s === 'transcribing' ? 'idle' : s));
    } else if (END_EVENTS[msg.type]) {
      setWsState((s) => (s === END_EVENTS[msg.type] ? 'idle' : s));
    }
  });

  useEffect(() => {
    const fn = (v) => setLocal(v);
    localListeners.add(fn);
    return () => localListeners.delete(fn);
  }, []);

  return local ?? wsState;
}

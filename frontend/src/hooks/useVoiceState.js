import { useState } from 'react';
import { useWebSocket } from './useWebSocket.js';

const START_EVENTS = {
  wake: 'wake',
  listening_start: 'listening',
  processing_start: 'thinking',
  speaking_start: 'speaking',
};

const END_EVENTS = {
  listening_end: 'listening',
  processing_end: 'thinking',
  speaking_end: 'speaking',
};

/**
 * Reactive global voice state ('idle'|'wake'|'listening'|'thinking'|
 * 'speaking'), derived from the same real voice events VUCoreModule's
 * meter consumes (see core/voice_bridge.py on the backend). Kept as its
 * own independent WS subscriber (rather than prop-drilled from
 * VUCoreModule) so ambient effects elsewhere in the chassis — like
 * ChassisLighting — can react to "what's Jarvis doing" without coupling
 * to the conversation-log module.
 */
export function useVoiceState() {
  const [state, setState] = useState('idle');

  useWebSocket((msg) => {
    if (START_EVENTS[msg.type]) {
      setState(START_EVENTS[msg.type]);
    } else if (END_EVENTS[msg.type]) {
      setState((s) => (s === END_EVENTS[msg.type] ? 'idle' : s));
    }
  });

  return state;
}

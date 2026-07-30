import { isMuted } from './sound.js';

/**
 * Browser-side TTS for TYPED (frontend-originated) replies only.
 *
 * server.py's /command — which both the frontend's command box AND
 * jarvis.py call — never speaks anything itself; only jarvis.py's own
 * respond()/speak() (core/voice.py, played through its process's local
 * speakers) does. That meant typing into the frontend always got a
 * text-only reply with no audio anywhere, which is the actual
 * "it's just a typing chatbot" gap — voice-only interactions do get
 * audio, just from the backend process, not the browser.
 *
 * This must ONLY be called for typed/frontend-originated replies —
 * never for voice-originated ones (WS 'reply' events), or the user
 * would hear the same sentence spoken twice: once here, once from
 * jarvis.py's own TTS.
 */
export function speakReply(text) {
  if (isMuted()) return;
  if (typeof window === 'undefined' || !window.speechSynthesis) return;
  if (!text) return;
  window.speechSynthesis.cancel(); // don't queue/overlap a prior utterance
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.05;
  utter.pitch = 1;
  window.speechSynthesis.speak(utter);
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { setLocalVoiceState, useVoiceState } from '../hooks/useVoiceState.js';
import { playErrorTick, playSuccessBlip, isMuted } from '../lib/sound.js';
import { speakReply, stopSpeaking } from '../lib/tts.js';
import { refreshAfterCommand } from './store.js';

const MAX_MESSAGES = 80;

/** Rough speaking time so the orb stays in "speaking" while browser TTS talks. */
function speakingMs(text) {
  if (isMuted()) return 600;
  return Math.min(9000, 500 + (text || '').length * 55);
}

/**
 * The single conversation both input paths feed:
 *  - typed commands (POST /command, full structured result)
 *  - voice turns from jarvis.py (WS transcript -> voice_result/reply)
 */
export function useConversation() {
  const [messages, setMessages] = useState([]);
  const [progress, setProgress] = useState('');
  const [pending, setPending] = useState(false);
  const [expecting, setExpecting] = useState(false);
  const [voiceDraft, setVoiceDraft] = useState('');
  const voiceState = useVoiceState();
  const idRef = useRef(0);
  const messagesRef = useRef(messages);
  const settleTimer = useRef(null);
  const draftTimer = useRef(null);
  messagesRef.current = messages;

  const push = useCallback((m) => {
    idRef.current += 1;
    const msg = { id: idRef.current, at: Date.now(), ...m };
    setMessages((prev) => [...prev.slice(-(MAX_MESSAGES - 1)), msg]);
    return msg;
  }, []);

  const settle = useCallback((state, ms) => {
    clearTimeout(settleTimer.current);
    setLocalVoiceState(state);
    settleTimer.current = setTimeout(() => setLocalVoiceState('idle'), ms);
  }, []);

  useEffect(() => () => {
    clearTimeout(settleTimer.current);
    clearInterval(draftTimer.current);
  }, []);

  // The dashboard is started on demand (and exits when closed), often in
  // the middle of a voice conversation — pick up the recent turns from the
  // backend so it opens on the conversation, not a blank page.
  useEffect(() => {
    let cancelled = false;
    api.conversationRecent().then(({ turns = [] }) => {
      if (cancelled || !turns.length) return;
      setMessages((current) => {
        if (current.length) return current;
        const restored = [];
        turns.forEach((t) => {
          idRef.current += 1;
          restored.push({ id: idRef.current, at: t.at * 1000, role: 'user', text: t.text, via: t.via, restored: true });
          idRef.current += 1;
          restored.push({
            id: idRef.current, at: t.at * 1000, role: 'jarvis', text: t.display, spoken: t.spoken,
            data: t.data || {}, ok: t.ok, via: t.via, restored: true,
          });
        });
        return restored;
      });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const send = useCallback(async (raw) => {
    const text = (raw || '').trim();
    if (!text) return;
    stopSpeaking();
    push({ role: 'user', text, via: 'text' });
    setPending(true);
    setExpecting(false);
    clearTimeout(settleTimer.current);
    setLocalVoiceState('thinking');
    try {
      const r = await api.command(text);
      push({ role: 'jarvis', text: r.display, spoken: r.spoken, data: r.data || {}, ok: r.ok, query: text });
      setExpecting(!!r.expecting_confirmation);
      if (r.ok) playSuccessBlip(); else playErrorTick();
      speakReply(r.spoken || r.display);
      settle(r.ok ? 'speaking' : 'error', r.ok ? speakingMs(r.spoken || r.display) : 900);
    } catch {
      push({ role: 'system', text: "Can't reach the backend. Start it with  python backend/run.py", ok: false });
      playErrorTick();
      settle('error', 900);
    } finally {
      setPending(false);
      setProgress('');
      refreshAfterCommand();
    }
  }, [push, settle]);

  // Types a spoken transcript into the command bar before it lands in
  // the stream, so a voice turn visibly "comes from" the same input.
  const animateDraft = useCallback((text) => {
    clearInterval(draftTimer.current);
    let i = 0;
    const step = Math.max(10, Math.min(28, 700 / text.length));
    draftTimer.current = setInterval(() => {
      i += 1;
      setVoiceDraft(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(draftTimer.current);
        setTimeout(() => {
          setVoiceDraft('');
          push({ role: 'user', text, via: 'voice' });
        }, 160);
      }
    }, step);
  }, [push]);

  useWebSocket((msg) => {
    switch (msg.type) {
      case 'transcript':
        if (msg.text) animateDraft(msg.text);
        break;
      case 'progress':
        if (msg.text) setProgress(msg.text);
        break;
      case 'voice_result':
        push({ role: 'jarvis', text: msg.display, spoken: msg.spoken, data: msg.data || {}, ok: msg.ok, query: msg.text, via: 'voice' });
        setExpecting(!!msg.expecting_confirmation);
        setProgress('');
        refreshAfterCommand();
        break;
      case 'reply': {
        // jarvis.py also pushes the plain text of every reply; skip it when
        // voice_result already delivered the same answer with its data.
        const last = [...messagesRef.current].reverse().find((m) => m.role === 'jarvis');
        if (msg.text && (!last || last.text !== msg.text)) push({ role: 'jarvis', text: msg.text, data: {}, ok: true, via: 'voice' });
        setProgress('');
        break;
      }
      default:
        break;
    }
  });

  const clear = useCallback(() => {
    setMessages([]);
    setExpecting(false);
  }, []);

  return { messages, send, progress, pending, expecting, voiceState, voiceDraft, clear };
}

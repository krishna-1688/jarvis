import { useState } from 'react';
import { useNow } from './useNow.js';
import { hm } from './format.js';
import { isMuted, toggleMuted } from '../lib/sound.js';
import { stopSpeaking } from '../lib/tts.js';

/** One line instead of four status dots: "All systems nominal", or the
 * single most important thing that isn't. The full list is the tooltip. */
function useHealthLine({ health, wsStatus, voiceAlive }) {
  const checks = [
    ['Core', wsStatus === 'connected', wsStatus === 'connecting' ? 'Connecting to core…' : 'Core offline — run python backend/run.py', 'bad'],
    ['Voice', voiceAlive, 'Voice offline · typing works', 'warn'],
    ['VTOP', !health || health.vtop?.has_data, 'No VTOP data yet', 'warn'],
    ['WhatsApp', !health || health.whatsapp?.ready, 'WhatsApp not connected', 'warn'],
  ];
  const tip = checks.map(([name, ok]) => `${name}: ${ok ? 'ok' : 'needs attention'}`).join('\n');
  const failing = checks.find(([name, ok]) => !ok && name !== 'WhatsApp') || checks.find(([, ok]) => !ok);
  if (!failing) return { tone: 'ok', label: 'All systems nominal', tip };
  // WhatsApp alone being off is normal for many sessions — keep it quiet.
  if (failing[0] === 'WhatsApp') return { tone: 'ok', label: 'All systems nominal', tip };
  return { tone: failing[3], label: failing[2], tip };
}

export default function Titlebar({ voiceState, health, wsStatus, voiceAlive, onClear, hasMessages }) {
  const now = useNow(30000);
  const [muted, setMutedState] = useState(isMuted());
  const line = useHealthLine({ health, wsStatus, voiceAlive });
  const provider = health?.ai?.provider;

  return (
    <header className="titlebar">
      <div className="brand">
        <span className="brand-dot" data-state={voiceState} />
        <span className="brand-word">JARVIS</span>
      </div>
      <span className="titlebar__spacer" />
      <span className={`health health--${line.tone}`} title={line.tip}><i />{line.label}</span>
      {provider && provider !== 'none' && (
        <span className={`chip-mono ${provider !== 'groq' ? 'chip-mono--alt' : ''}`}
          title={provider === 'groq' ? 'Answering with Groq' : 'Groq is out of quota — answering with Gemini'}>
          {provider}
        </span>
      )}
      <span className="titlebar__clock mono">{hm(now)}</span>
      <div className="titlebar__right">
        <button type="button" className="icon-btn" aria-label={muted ? 'Unmute replies' : 'Mute replies'} title={muted ? 'Unmute replies' : 'Mute replies'}
          onClick={() => { toggleMuted(); stopSpeaking(); setMutedState(isMuted()); }}>
          {muted ? (
            <svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4z" /><path d="M17 9l4 6M21 9l-4 6" /></svg>
          ) : (
            <svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /></svg>
          )}
        </button>
        {hasMessages && (
          <button type="button" className="icon-btn" aria-label="New conversation" title="New conversation" onClick={onClear}>
            <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>
          </button>
        )}
        <button type="button" className="icon-btn" aria-label="Minimise" title="Minimise" onClick={() => window.jarvis?.windowControl?.('console', 'minimize')}>
          <svg viewBox="0 0 24 24"><path d="M5 12h14" /></svg>
        </button>
        <button type="button" className="icon-btn icon-btn--close" aria-label="Close" title="Close (Ctrl+J or “open dashboard” reopens it)" onClick={() => window.jarvis?.windowControl?.('console', 'close')}>
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </header>
  );
}

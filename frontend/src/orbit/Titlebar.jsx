import { useState } from 'react';
import { useNow } from './useNow.js';
import { isMuted, toggleMuted } from '../lib/sound.js';
import { stopSpeaking } from '../lib/tts.js';

function Status({ tone, label, tip }) {
  return (
    <span className={`status status--${tone}`} title={tip}>
      <i />{label}
    </span>
  );
}

export default function Titlebar({ voiceState, health, wsStatus, voiceAlive, onClear, hasMessages }) {
  const now = useNow(1000);
  const [muted, setMutedState] = useState(isMuted());

  const backendTone = wsStatus === 'connected' ? 'ok' : wsStatus === 'connecting' ? 'pending' : 'bad';
  const vtop = health?.vtop;
  const wa = health?.whatsapp;

  return (
    <header className="titlebar drag-region">
      <div className="titlebar__brand">
        <span className={`brand-dot state-${voiceState}`} />
        <span className="brand-word">JARVIS</span>
      </div>

      <div className="titlebar__clock mono">
        <span>{now.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</span>
        <b>{now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })}</b>
      </div>

      <div className="titlebar__right no-drag">
        <Status tone={backendTone} label="Core" tip={backendTone === 'ok' ? 'Backend connected' : 'Backend offline — run: python backend/run.py'} />
        <Status tone={voiceAlive ? 'ok' : 'warn'} label="Voice" tip={voiceAlive ? 'Voice loop running — say “Hey Jarvis”' : 'Voice loop not running (python backend/run.py)'} />
        <Status tone={vtop?.has_data ? 'ok' : health ? 'warn' : 'pending'} label="VTOP" tip={vtop?.has_data ? 'VTOP data synced' : 'No VTOP data yet'} />
        <Status tone={wa?.ready ? 'ok' : health ? 'warn' : 'pending'} label="WhatsApp" tip={wa?.ready ? 'WhatsApp connected' : (wa?.error || 'WhatsApp not connected')} />
        <span className="titlebar__sep" />
        <button type="button" className="icon-btn" title={muted ? 'Unmute replies' : 'Mute replies'}
          onClick={() => { toggleMuted(); stopSpeaking(); setMutedState(isMuted()); }}>
          {muted ? (
            <svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M17 9l4 6M21 9l-4 6" /></svg>
          ) : (
            <svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" /></svg>
          )}
        </button>
        {hasMessages && (
          <button type="button" className="icon-btn" title="New conversation" onClick={onClear}>
            <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>
          </button>
        )}
        <button type="button" className="icon-btn" title="Minimize" onClick={() => window.jarvis?.windowControl?.('console', 'minimize')}>
          <svg viewBox="0 0 24 24"><path d="M6 12h12" /></svg>
        </button>
        <button type="button" className="icon-btn icon-btn--close" title="Close (Ctrl+J or “open dashboard” reopens it)" onClick={() => window.jarvis?.windowControl?.('console', 'close')}>
          <svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17" /></svg>
        </button>
      </div>
    </header>
  );
}

import { useEffect, useMemo, useState } from 'react';
import TopRail from '../components/TopRail.jsx';
import Rack from '../rack/Rack.jsx';
import BootSequence from '../components/BootSequence/BootSequence.jsx';
import ChromeSpine from '../components/ChromeSpine/ChromeSpine.jsx';
import ChassisLighting from '../components/ChassisLighting/ChassisLighting.jsx';
import VoicePresence from '../components/VoicePresence/VoicePresence.jsx';
import StatusBanner from '../components/StatusBanner/StatusBanner.jsx';
import { MODULE_REGISTRY, DEFAULT_CONSOLE_LAYOUT } from '../rack/moduleRegistry.js';
import { useTheme } from '../hooks/useTheme.js';
import { useApiData } from '../hooks/useApiData.js';
import { useChassisParallax } from '../hooks/useChassisParallax.js';
import { useWebSocketStatus } from '../hooks/useWebSocket.js';
import { useVoiceProcessAlive } from '../hooks/useVoiceProcessAlive.js';
import { api } from '../api.js';
import './Console.css';

// S.1c: don't flash a banner on every transient reconnect blip — only
// once a condition has actually persisted long enough to mean something.
// (Voice-process staleness already has its own 20s tolerance built into
// useVoiceProcessAlive, so no second grace period is layered on top here
// — that would just double the delay before a real "voice not running"
// gets surfaced.)
const BACKEND_DOWN_GRACE_MS = 5000;

export default function Console() {
  const { theme, toggle } = useTheme();
  const [editMode, setEditMode] = useState(false);
  const { data: health, error: healthError } = useApiData(api.health, { pollMs: 30000 });
  const parallax = useChassisParallax(4);
  const wsStatus = useWebSocketStatus();
  const voiceProcessAlive = useVoiceProcessAlive();

  // S.1c: two distinct silent-failure conditions, surfaced as two
  // distinct messages — "the backend itself is unreachable" (WS can't
  // even connect) vs. "the backend's fine but jarvis.py's voice loop
  // isn't running" (WS connected, health OK, but no heartbeat ever
  // arrives) — these have different fixes and were being conflated into
  // one silent orange dot nobody noticed.
  const [backendBannerReady, setBackendBannerReady] = useState(false);
  const [backendBannerDismissed, setBackendBannerDismissed] = useState(false);
  const [voiceBannerDismissed, setVoiceBannerDismissed] = useState(false);

  useEffect(() => {
    if (wsStatus !== 'reconnecting') {
      setBackendBannerReady(false);
      return undefined;
    }
    const t = setTimeout(() => setBackendBannerReady(true), BACKEND_DOWN_GRACE_MS);
    return () => clearTimeout(t);
  }, [wsStatus]);

  // A fresh occurrence un-dismisses — dismissal only hides THIS episode.
  useEffect(() => { if (wsStatus === 'connected') setBackendBannerDismissed(false); }, [wsStatus]);
  useEffect(() => { if (voiceProcessAlive) setVoiceBannerDismissed(false); }, [voiceProcessAlive]);

  const showBackendBanner = backendBannerReady && !backendBannerDismissed;
  // Only worth showing "voice process not running" once we know the
  // backend itself is actually reachable — otherwise it's just noise on
  // top of the more fundamental backend-down banner.
  const showVoiceBanner = wsStatus === 'connected' && !voiceProcessAlive && !voiceBannerDismissed;

  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'e') {
        e.preventDefault();
        setEditMode((v) => !v);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const healthChips = useMemo(() => {
    if (healthError) return { backend: 'warn', whatsapp: 'warn', vtop: 'warn' };
    if (!health) return { backend: 'pending', whatsapp: 'pending', vtop: 'pending' };
    return {
      backend: 'ok',
      whatsapp: health.whatsapp?.ready ? 'ok' : 'warn',
      vtop: health.vtop?.has_data ? 'ok' : 'warn',
    };
  }, [health, healthError]);

  return (
    <div className="console">
      <BootSequence />
      <ChassisLighting />
      <VoicePresence />
      <StatusBanner
        visible={showBackendBanner}
        text="BACKEND OFFLINE — run: python backend/run.py"
        onDismiss={() => setBackendBannerDismissed(true)}
      />
      <StatusBanner
        visible={!showBackendBanner && showVoiceBanner}
        text="VOICE OFFLINE — jarvis.py isn't running (python backend/run.py or python backend/jarvis.py)"
        onDismiss={() => setVoiceBannerDismissed(true)}
      />
      <TopRail
        windowName="console"
        theme={theme}
        onToggleTheme={toggle}
        editMode={editMode}
        onToggleEdit={() => setEditMode((v) => !v)}
        health={healthChips}
      />
      <div className="console__rack-wrap" style={{ transform: `translate(${parallax.x}px, ${parallax.y}px)` }}>
        <ChromeSpine />
        <Rack
          rackId="console"
          registry={MODULE_REGISTRY}
          defaultLayout={DEFAULT_CONSOLE_LAYOUT}
          editMode={editMode}
          cols={12}
        />
      </div>
    </div>
  );
}

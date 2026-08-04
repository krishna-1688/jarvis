import { useEffect, useRef, useState } from 'react';
import { USE_MOCK } from '../mock.js';
import { useWebSocket } from './useWebSocket.js';

const RETRY_MS = 15000;

/**
 * Fetches once on mount, then polls every `pollMs`; on failure, shows the
 * degraded state and retries every 15s (Section 3.9's "NO SIGNAL" spec).
 * A no-op entirely while USE_MOCK is true, so mocks stay switchable forever.
 *
 * `refreshOn`, if given, is a data_type string (e.g. "tasks") — the
 * backend already broadcasts {"type":"data_refreshed","data_type":...}
 * whenever it changes something server-side (a voice-added task, a VTOP
 * refresh, etc.), but until now nothing on the frontend ever listened
 * for it, so a module only ever found out about a change on its next
 * scheduled poll (up to `pollMs` late — e.g. a task added by voice
 * wouldn't show up in the Tasks module for up to 30s). This makes that
 * update immediate instead.
 */
export function useApiData(fetchFn, { pollMs = 30000, refreshOn } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !USE_MOCK });
  const fetchRef = useRef(fetchFn);
  fetchRef.current = fetchFn;
  const refetchNowRef = useRef(() => {});

  useEffect(() => {
    if (USE_MOCK) return undefined;
    let cancelled = false;
    let timer;

    const run = async () => {
      try {
        const data = await fetchRef.current();
        if (cancelled) return;
        setState({ data, error: null, loading: false });
        timer = setTimeout(run, pollMs);
      } catch (e) {
        if (cancelled) return;
        setState((s) => ({ ...s, error: e, loading: false }));
        timer = setTimeout(run, RETRY_MS);
      }
    };
    refetchNowRef.current = () => {
      clearTimeout(timer);
      run();
    };
    run();

    return () => { cancelled = true; clearTimeout(timer); };
  }, [pollMs]);

  useWebSocket((msg) => {
    if (refreshOn && msg.type === 'data_refreshed' && msg.data_type === refreshOn) {
      refetchNowRef.current();
    }
  });

  return state;
}

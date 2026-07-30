import { useEffect, useRef, useState } from 'react';
import { USE_MOCK } from '../mock.js';

const RETRY_MS = 15000;

/**
 * Fetches once on mount, then polls every `pollMs`; on failure, shows the
 * degraded state and retries every 15s (Section 3.9's "NO SIGNAL" spec).
 * A no-op entirely while USE_MOCK is true, so mocks stay switchable forever.
 */
export function useApiData(fetchFn, { pollMs = 30000 } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !USE_MOCK });
  const fetchRef = useRef(fetchFn);
  fetchRef.current = fetchFn;

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
    run();

    return () => { cancelled = true; clearTimeout(timer); };
  }, [pollMs]);

  return state;
}

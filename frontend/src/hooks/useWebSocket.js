import { useEffect, useRef, useSyncExternalStore } from 'react';
import { wsUrl } from '../api.js';

/**
 * A single shared WebSocket connection to /stream, reused by every
 * caller of useWebSocket/useWebSocketStatus (previously each component
 * opened its own socket — harmless but wasteful, and made "is the app
 * connected" ambiguous since different components' sockets could be in
 * different states). Exponential backoff on reconnect: 500ms -> 8s cap,
 * reset to 500ms on a successful connect.
 */
const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;

let ws = null;
let status = 'connecting'; // 'connecting' | 'connected' | 'reconnecting'
let backoff = MIN_BACKOFF_MS;
let retryTimer = null;
const subscribers = new Set();
const statusListeners = new Set();

function setStatus(next) {
  if (status === next) return;
  status = next;
  statusListeners.forEach((fn) => fn());
}

function connect() {
  retryTimer = null;

  ws = new WebSocket(wsUrl());
  ws.onopen = () => {
    backoff = MIN_BACKOFF_MS;
    setStatus('connected');
  };
  ws.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return; // ignore malformed frames
    }
    subscribers.forEach((fn) => fn(data));
  };
  ws.onclose = () => {
    ws = null;
    setStatus('reconnecting');
    retryTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
  };
  ws.onerror = () => ws?.close();
}

function ensureConnected() {
  if (!ws && !retryTimer) connect();
}

/** Non-hook subscription for module-level stores (orbit/store.js).
 * Returns an unsubscribe function. */
export function subscribeWebSocket(fn) {
  subscribers.add(fn);
  ensureConnected();
  return () => subscribers.delete(fn);
}

/** Subscribes to every parsed JSON frame on the shared socket (including
 * {"type":"ping"} — callers filter for what they care about). */
export function useWebSocket(onMessage) {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    const handler = (data) => handlerRef.current(data);
    subscribers.add(handler);
    ensureConnected();
    return () => subscribers.delete(handler);
  }, []);
}

/** Reactive 'connecting' | 'connected' | 'reconnecting' for the shared
 * socket — drives TopRail's connection pip. */
export function useWebSocketStatus() {
  return useSyncExternalStore(
    (onStoreChange) => {
      statusListeners.add(onStoreChange);
      ensureConnected();
      return () => statusListeners.delete(onStoreChange);
    },
    () => status,
    () => status,
  );
}

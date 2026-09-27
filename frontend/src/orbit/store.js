import { useSyncExternalStore } from 'react';
import { api } from '../api.js';
import { subscribeWebSocket } from '../hooks/useWebSocket.js';

/**
 * One shared, cached copy of every backend data feed.
 *
 * The old rack had each module poll on its own — /attendance and
 * /assignments were each fetched by three separate widgets every minute.
 * Here each endpoint has exactly one poller, polling pauses while the
 * window is hidden (Ctrl+J), and anything the backend announces over the
 * WebSocket (data_refreshed, expense_added, focus events) refreshes the
 * matching feed immediately instead of waiting for the next poll.
 */
const SOURCES = {
  health:      { fetch: api.health,                        every: 30_000 },
  schedule:    { fetch: () => api.schedule('today'),       every: 60_000,  refreshOn: ['timetable', 'schedule'] },
  tomorrow:    { fetch: () => api.schedule('tomorrow'),    every: 600_000, refreshOn: ['timetable', 'schedule'] },
  attendance:  { fetch: api.attendance,                    every: 300_000, refreshOn: ['attendance'] },
  exams:       { fetch: api.exams,                         every: 600_000, refreshOn: ['exams'] },
  assignments: { fetch: api.assignments,                   every: 300_000, refreshOn: ['assignments'] },
  tasks:       { fetch: () => api.tasks(),                 every: 120_000, refreshOn: ['tasks'] },
  focus:       { fetch: api.focusStatus,                   every: 60_000,  refreshOn: ['focus'] },
  focusStats:  { fetch: () => api.focusStats(7),           every: 600_000, refreshOn: ['focus'] },
  money:       { fetch: () => api.expensesSummary('month'), every: 300_000, refreshOn: ['expenses'] },
};

const RETRY_MS = 15_000;
const EMPTY = { data: null, error: null, loading: true, at: 0 };

const state = {};
const listeners = new Set();
const timers = {};
const inflight = {};
let started = false;

function emit() {
  listeners.forEach((fn) => fn());
}

function setSource(name, patch) {
  state[name] = { ...(state[name] || EMPTY), ...patch };
  emit();
}

function schedule(name) {
  clearTimeout(timers[name]);
  if (typeof document !== 'undefined' && document.hidden) return;
  const delay = state[name]?.error ? RETRY_MS : SOURCES[name].every;
  timers[name] = setTimeout(() => refresh(name), delay);
}

export function refresh(name) {
  if (!SOURCES[name]) return Promise.resolve();
  if (inflight[name]) return inflight[name];
  inflight[name] = (async () => {
    try {
      const data = await SOURCES[name].fetch();
      setSource(name, { data, error: null, loading: false, at: Date.now() });
    } catch (error) {
      setSource(name, { error, loading: false });
    } finally {
      inflight[name] = null;
      schedule(name);
    }
  })();
  return inflight[name];
}

/** Cheap local-DB feeds a command might have changed (tasks, blocks,
 * focus, expenses) — refreshed after every command so the UI never lags
 * behind what was just said. */
export function refreshAfterCommand() {
  ['tasks', 'schedule', 'focus', 'money'].forEach(refresh);
}

function start() {
  if (started) return;
  started = true;
  Object.keys(SOURCES).forEach((name) => {
    state[name] = EMPTY;
    refresh(name);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      Object.values(timers).forEach(clearTimeout);
      return;
    }
    Object.keys(SOURCES).forEach((name) => {
      const age = Date.now() - (state[name]?.at || 0);
      if (age > SOURCES[name].every) refresh(name);
      else schedule(name);
    });
  });

  subscribeWebSocket((msg) => {
    if (msg.type === 'data_refreshed') {
      Object.entries(SOURCES).forEach(([name, src]) => {
        if (src.refreshOn?.includes(msg.data_type)) refresh(name);
      });
    } else if (msg.type === 'expense_added') {
      refresh('money');
    } else if (msg.type === 'focus_started' || msg.type === 'focus_ended' || msg.type === 'pomodoro_transition') {
      refresh('focus');
      if (msg.type === 'focus_ended') refresh('focusStats');
    }
  });
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** { data, error, loading, at } for one feed. */
export function useSource(name) {
  start();
  return useSyncExternalStore(subscribe, () => state[name] || EMPTY, () => state[name] || EMPTY);
}

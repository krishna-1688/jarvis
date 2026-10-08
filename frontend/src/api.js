/** Thin client for the FastAPI backend — mirrors backend/server.py's real routes exactly. */

const BASE_URL = 'http://127.0.0.1:8000';

async function getJSON(path) {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  health: () => getJSON('/health'),
  tasks: (withinDays) => getJSON(`/tasks${withinDays ? `?within_days=${withinDays}` : ''}`),
  schedule: (date = 'today') => getJSON(`/schedule?date=${date}`),
  focusStatus: () => getJSON('/focus/status'),
  focusStats: (days = 7) => getJSON(`/focus/stats?days=${days}`),
  expensesSummary: (range = 'month') => getJSON(`/expenses/summary?range=${range}`),
  expensesWeekComparison: () => getJSON('/expenses/week_comparison'),
  attendance: () => getJSON('/attendance'),
  exams: () => getJSON('/exams'),
  assignments: () => getJSON('/assignments'),
  conversationRecent: (minutes = 30) => getJSON(`/conversation/recent?minutes=${minutes}`),
  memoryGraph: (limit = 42) => getJSON(`/memory/graph?limit=${limit}`),
  memoryNode: (id) => getJSON(`/memory/node/${id}`),
  profile: () => getJSON('/profile'),
  saveProfile: async (profile) => {
    const res = await fetch(`${BASE_URL}/profile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile }),
    });
    if (!res.ok) throw new Error(`PUT /profile -> ${res.status}`);
    return res.json();
  },
  command: async (text) => {
    const res = await fetch(`${BASE_URL}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(`POST /command -> ${res.status}`);
    return res.json();
  },
};

export const WS_URL = 'ws://127.0.0.1:8000/stream';

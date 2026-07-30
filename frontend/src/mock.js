/**
 * Central mock-data switch (Section 2 of the design spec). Every module
 * builds against this until Task 3.9 wires the real backend; flipping
 * USE_MOCK stays possible forever, not just during development.
 */
export const USE_MOCK = false;

/** 7 subjects, varied attendance, one below the 75% cutoff (Section 2). */
export const MOCK_ATTENDANCE = [
  { code: 'BCSE305L', name: 'Embedded Systems', attended: 2, total: 3 },
  { code: 'BCSE204L', name: 'Design and Analysis of Algorithms', attended: 19, total: 20 },
  { code: 'BCSE307L', name: 'Compiler Design', attended: 17, total: 19 },
  { code: 'BCSE355L', name: 'Cloud Architecture Design', attended: 20, total: 21 },
  { code: 'BMAT202L', name: 'Probability and Statistics', attended: 22, total: 24 },
  { code: 'BCLE214L', name: 'Global Warming', attended: 16, total: 16 },
  { code: 'BSTS301P', name: 'Advanced Competitive Coding', attended: 14, total: 15 },
].map((s) => ({ ...s, percentage: Math.round((s.attended / s.total) * 1000) / 10 }));

/** A full week, keyed 0 (Sunday) .. 6 (Saturday) matching Date#getDay(). */
export const MOCK_TIMETABLE = {
  0: [],
  1: [
    { code: 'BCSE204L', name: 'Design and Analysis of Algorithms', start: '09:00', end: '09:50' },
    { code: 'BCSE307L', name: 'Compiler Design', start: '10:00', end: '10:50' },
    { code: 'BMAT202L', name: 'Probability and Statistics', start: '11:00', end: '11:50' },
    { code: 'BCSE305L', name: 'Embedded Systems', start: '14:00', end: '14:50' },
    { code: 'BCSE355L', name: 'Cloud Architecture Design', start: '15:00', end: '15:50' },
  ],
  2: [
    { code: 'BCSE204P', name: 'DAA Lab', start: '09:00', end: '10:40' },
    { code: 'BCLE214L', name: 'Global Warming', start: '11:00', end: '11:50' },
    { code: 'BSTS301P', name: 'Advanced Competitive Coding', start: '13:00', end: '14:40' },
  ],
  3: [
    { code: 'BCSE307L', name: 'Compiler Design', start: '09:00', end: '09:50' },
    { code: 'BCSE305L', name: 'Embedded Systems', start: '10:00', end: '10:50' },
    { code: 'BMAT202L', name: 'Probability and Statistics', start: '11:00', end: '11:50' },
    { code: 'BCSE355L', name: 'Cloud Architecture Design', start: '15:00', end: '15:50' },
  ],
  4: [
    { code: 'BCSE307P', name: 'Compiler Design Lab', start: '09:00', end: '10:40' },
    { code: 'BMAT202P', name: 'Probability and Statistics Lab', start: '11:00', end: '12:40' },
    { code: 'BCSE204L', name: 'Design and Analysis of Algorithms', start: '14:00', end: '14:50' },
  ],
  5: [
    { code: 'BCSE305L', name: 'Embedded Systems', start: '09:00', end: '09:50' },
    { code: 'BCSE307L', name: 'Compiler Design', start: '10:00', end: '10:50' },
    { code: 'BCLE214L', name: 'Global Warming', start: '11:00', end: '11:50' },
    { code: 'BCSE355L', name: 'Cloud Architecture Design', start: '13:00', end: '13:50' },
    { code: 'BSTS301P', name: 'Advanced Competitive Coding', start: '15:00', end: '16:40' },
  ],
  6: [],
};

/** Non-class blocks for today, merged with MOCK_TIMETABLE by ScheduleModule
 *  — mirrors what GET /schedule?date=today already returns unified on the
 *  real backend (classes materialized into the same schedule_blocks table). */
export const MOCK_CUSTOM_BLOCKS_TODAY = [
  { code: null, name: 'TOC study', start: '15:00', end: '17:00', block_type: 'custom' },
  { code: null, name: 'Gym', start: '18:30', end: '19:30', block_type: 'routine' },
];

/** Personal tasks — "things to do", distinct from the timetable/schedule. */
export const MOCK_TASKS = [
  { id: 1, title: "submit fees", due_at: "2026-07-24T00:00:00", priority: "normal", tag: null, status: "open" },
  { id: 2, title: "finish DBMS assignment", due_at: "2026-07-21T08:00:00", priority: "high", tag: "academic", status: "open" },
  { id: 3, title: "buy new charger", due_at: null, priority: "low", tag: "personal", status: "open" },
  { id: 4, title: "prep for placement mock interview", due_at: "2026-07-22T18:00:00", priority: "high", tag: "placement", status: "open" },
  { id: 5, title: "call mom", due_at: "2026-07-20T20:00:00", priority: "normal", tag: "personal", status: "open" },
];

/** This month's expense summary (Section 5.3). */
export const MOCK_EXPENSE_SUMMARY = {
  month_total: 4280,
  by_category: [
    { category: 'food', total: 1850 },
    { category: 'travel', total: 920 },
    { category: 'subscriptions', total: 799 },
    { category: 'academics', total: 450 },
    { category: 'entertainment', total: 261 },
  ],
  this_week_total: 680,
  last_week_total: 610,
};

/** This week's focus/study time per subject. */
export const MOCK_FOCUS_STATS = [
  { subject: "DBMS", total_minutes: 134, sessions: 3 },
  { subject: "TOC", total_minutes: 90, sessions: 2 },
  { subject: "Compiler Design", total_minutes: 45, sessions: 1 },
];

/** 2 upcoming exams (Section 2). */
export const MOCK_EXAMS = [
  { code: 'FAT-1', name: 'Embedded Systems', date: '2026-07-25T09:00:00' },
  { code: 'FAT-1', name: 'Compiler Design', date: '2026-07-28T14:00:00' },
];

/** 4 assignments, one overdue and one submitted (Section 2). Today is 2026-07-20. */
export const MOCK_ASSIGNMENTS = [
  { id: 1, title: 'DAA Assignment 3', course: 'BCSE204L', due: '2026-07-18T23:59:00', submitted: true },
  { id: 2, title: 'Compiler Design Lab Report', course: 'BCSE307L', due: '2026-07-19T23:59:00', submitted: false },
  { id: 3, title: 'Cloud Architecture Case Study', course: 'BCSE355L', due: '2026-07-23T23:59:00', submitted: false },
  { id: 4, title: 'Probability Problem Set 4', course: 'BMAT202L', due: '2026-07-28T23:59:00', submitted: false },
];

/**
 * Creates a stateful amplitude generator for the VU meter. Call the
 * returned function every animation frame with (state, nowMs); it returns
 * the next amplitude value in [0, 1]. Replaced by real WebSocket-pushed
 * amplitude in Task 3.9 behind the same USE_MOCK flag — same [0,1] shape,
 * so VUMeter itself never needs to change.
 */
export function createAmplitudeGenerator() {
  let value = 0;
  let wakeStart = null;

  return function next(state, now) {
    if (state !== 'wake') wakeStart = null;

    switch (state) {
      case 'wake': {
        if (wakeStart === null) wakeStart = now;
        const t = now - wakeStart;
        value = t < 90 ? t / 90 : Math.exp(-(t - 90) / 200);
        break;
      }
      case 'listening': {
        const target = 0.15 + Math.random() * 0.7;
        value += (target - value) * 0.35;
        break;
      }
      case 'speaking': {
        const target = 0.2 + Math.random() * 0.75;
        value += (target - value) * 0.45;
        break;
      }
      case 'thinking': {
        // Slow ~2s breathing, not the jittery listening/speaking shape —
        // reads as "working," not "hearing/making sound."
        value = 0.15 + 0.07 * Math.sin(now / 320);
        break;
      }
      default:
        value = 0;
    }
    return Math.max(0, Math.min(1, value));
  };
}

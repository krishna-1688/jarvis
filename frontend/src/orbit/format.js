import { parseExamDate } from '../lib/examMath.js';
import { bunkInfo } from '../lib/bunkMath.js';

export { parseExamDate, bunkInfo };

const pad = (n) => String(n).padStart(2, '0');

/** Backend timestamps are naive local ISO ("2026-09-28T08:00:00"). */
export function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function hm(date) {
  return date ? `${pad(date.getHours())}:${pad(date.getMinutes())}` : '';
}

export function durShort(ms) {
  const mins = Math.max(0, Math.round(ms / 60000));
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 24) return m ? `${h}h ${m}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

/** "in 1h 20m" / "20m ago" */
export function relTime(date, now = Date.now()) {
  if (!date) return '';
  const diff = date.getTime() - now;
  return diff >= 0 ? `in ${durShort(diff)}` : `${durShort(-diff)} ago`;
}

export function daysUntil(date, now = new Date()) {
  const a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const b = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((b - a) / 86400000);
}

export function dayWord(date, now = new Date()) {
  const d = daysUntil(date, now);
  if (d === 0) return 'Today';
  if (d === 1) return 'Tomorrow';
  if (d === -1) return 'Yesterday';
  if (d > 1 && d < 7) return date.toLocaleDateString(undefined, { weekday: 'long' });
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

const SMALL_WORDS = new Set(['and', 'of', 'the', 'for', 'to', 'in', 'a', 'an', '-', '&']);

/** "Design and Analysis of Algorithms Lab(BCSE204P)" -> "DAA Lab" */
export function shortCourse(name = '') {
  const clean = name.replace(/\(.*?\)/g, '').trim();
  const isLab = /\blab\b/i.test(clean);
  const words = clean.replace(/\blab\b/i, '').split(/\s+/).filter((w) => w && !SMALL_WORDS.has(w.toLowerCase()));
  if (words.length <= 1) return clean;
  const acro = words
    .filter((w) => !/^(i|ii|iii|iv)$/i.test(w))
    .map((w) => w[0].toUpperCase())
    .join('');
  return isLab ? `${acro} Lab` : acro;
}

/** Strip trailing "(BCSE204P)" style codes for display. */
export function cleanCourse(name = '') {
  return name.replace(/\s*\(.*?\)\s*/g, ' ').trim();
}

export function dayPart(hour = new Date().getHours()) {
  if (hour >= 5 && hour < 11) return 'dawn';
  if (hour >= 11 && hour < 17) return 'day';
  if (hour >= 17 && hour < 21) return 'dusk';
  return 'night';
}

export function greeting(hour = new Date().getHours()) {
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  if (hour >= 17 && hour < 22) return 'Good evening';
  return 'Burning the midnight oil';
}

/** Tone + label for an assignment due date. */
export function dueInfo(iso, now = Date.now()) {
  const d = toDate(iso);
  if (!d) return { label: 'No due date', tone: 'dim' };
  const diff = d.getTime() - now;
  if (diff < -2 * 86400000) return { label: `Overdue ${Math.floor(-diff / 86400000)} days`, tone: 'bad' };
  if (diff < 0) return { label: `Overdue ${durShort(-diff)}`, tone: 'bad' };
  if (diff < 86400000) return { label: `Due ${relTime(d, now)}`, tone: 'warn' };
  if (diff < 3 * 86400000) return { label: `Due ${dayWord(d)}`, tone: 'warn' };
  return { label: `Due ${dayWord(d)}`, tone: 'ok' };
}

export function attendanceTone(pct) {
  if (pct == null) return 'dim';
  if (pct < 75) return 'bad';
  if (pct < 80) return 'warn';
  return 'ok';
}

/** Exam's start as a Date, using "02:00 PM - 03:30 PM" session when present. */
export function examStart(exam) {
  const d = parseExamDate(exam.exam_date || exam.date);
  const m = /(\d{1,2}):(\d{2})\s*(AM|PM)/i.exec(exam.session || '');
  if (d && m) {
    let h = Number(m[1]) % 12;
    if (m[3].toUpperCase() === 'PM') h += 12;
    d.setHours(h, Number(m[2]), 0, 0);
  }
  return d;
}

export function upcomingExams(exams = [], now = Date.now()) {
  return exams
    .map((e) => ({ ...e, start: examStart(e) }))
    .filter((e) => e.start && e.start.getTime() + 2 * 3600000 > now)
    .sort((a, b) => a.start - b.start);
}

export function inr(n) {
  return `₹${Math.round(n || 0).toLocaleString('en-IN')}`;
}

/** The user's profile bits the UI needs, with safe defaults before it loads. */
export function profileView(data) {
  const you = data?.profile?.you || {};
  const name = (you.name || '').trim() || 'there';
  const callMe = (you.call_me ?? 'boss').trim();
  const wake = (data?.profile?.voice?.wake_reply || 'Yes {call_me}?')
    .replace('{call_me}', callMe).replace('{name}', name).replace(/\s+\?/, '?').trim() || 'Yes?';
  return { name, callMe, wake };
}

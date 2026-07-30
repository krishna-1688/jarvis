/** Shared by ExamCountdownModule (console) and ThinCountdownModule (widget). */

const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

/** Parses the real backend's "25-Jan-2025" exam_date format; falls back to native parsing for mock ISO strings. */
function parseExamDate(dateStr) {
  const m = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/.exec(dateStr);
  if (m) return new Date(Number(m[3]), MONTHS[m[2].toLowerCase()], Number(m[1]));
  return new Date(dateStr);
}

export function daysHoursUntil(dateStr) {
  const diff = parseExamDate(dateStr).getTime() - Date.now();
  if (diff <= 0) return null;
  const totalHours = Math.floor(diff / 3600000);
  return { days: Math.floor(totalHours / 24), hours: totalHours % 24 };
}

export function nextUpcomingExam(exams) {
  return exams
    .map((e) => ({ ...e, remaining: daysHoursUntil(e.date) }))
    .filter((e) => e.remaining !== null)
    .sort((a, b) => parseExamDate(a.date) - parseExamDate(b.date))[0] ?? null;
}

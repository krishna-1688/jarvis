"""
core/academic_context.py — the student's own records, for the brain.

When an academic question doesn't map cleanly onto one feature ("am I
doing well this semester", "which subject should I focus on", "is my
attendance enough to sit for FAT"), it goes to plain chat. Without this,
chat had no data and could only say "ask 'what's my attendance'". Now it
gets a compact snapshot from the local SQLite copy (never a live VTOP
call), limited to the sections the question touches so the extra tokens
stay small on Groq's free tier.
"""

import re

_SECTIONS = {
    "attendance": r"attend|bunk|skip|miss|absent|debar|75|leave|present|doing|perform|overall",
    "marks":      r"mark|score|cat\s*-?\s*[12i]|\bfat\b|\bda\b|assignment|quiz|internal|test|weak|strong|perform|doing|focus|improve",
    "grades":     r"grade|cgpa|gpa|result|arrear|backlog|fail|pass|sem(?:ester)?\s*\d|semester|credit|placement",
    "exams":      r"exam|cat|fat|test|prepar|revis|study|schedule",
    "timetable":  r"class|timetable|lecture|lab|free|today|tomorrow|tonight|study",
}
_ACADEMIC_RE = re.compile("|".join(_SECTIONS.values()) + r"|subject|course|vtop|semester|college|study|exam", re.I)


def is_academic(text: str) -> bool:
    if _ACADEMIC_RE.search(text or ""):
        return True
    try:
        from core.course_resolver import resolve_course_best
        return bool(resolve_course_best(text or ""))
    except Exception:
        return False


def _attendance() -> str:
    from core.memory import get_all_attendance
    rows = get_all_attendance()
    if not rows:
        return ""
    lines = ["Attendance this semester (attended/total = %):"]
    for r in rows:
        lines.append(f"  {r['course_name']} ({r['course_code']}): "
                     f"{r['attended_classes']}/{r['total_classes']} = {r['percentage']}%")
    return "\n".join(lines)


def _marks() -> str:
    from core.memory import get_all_marks_summary
    from core.semesters import CURRENT_SEM
    return get_all_marks_summary(CURRENT_SEM)


def _grades() -> str:
    from core.memory import get_all_grades, get_latest_cgpa_summary, sem_label
    parts = []
    s = get_latest_cgpa_summary()
    if s and s.get("cgpa") is not None:
        parts.append(f"CGPA: {s['cgpa']} (credits earned {s.get('credits_earned')} of "
                     f"{s.get('credits_registered')} registered)")
    by_sem = {}
    for r in get_all_grades():
        by_sem.setdefault(r.get("semester_id") or "", []).append(f"{r['course_name']} {r['grade']}")
    for sem, items in sorted(by_sem.items()):
        parts.append(f"{sem_label(sem) if sem else 'Other'} grades: " + "; ".join(items))
    return "\n".join(parts)


def _exams() -> str:
    import datetime as dt
    from core.memory import get_all_exams
    today = dt.date.today()
    lines = []
    for r in get_all_exams():
        try:
            day = dt.datetime.strptime(str(r["exam_date"]).strip(), "%d-%b-%Y").date()
        except (TypeError, ValueError):
            continue
        if day >= today:
            lines.append((day, f"  {r['exam_type']}: {r['course_name']} on {day:%a %d %b} "
                               f"({(day - today).days} days), {r.get('session') or ''} {r.get('venue') or ''}".rstrip()))
    if not lines:
        return "Upcoming exams: none on record."
    return "Upcoming exams:\n" + "\n".join(text for _, text in sorted(lines)[:12])


def _timetable() -> str:
    from datetime import datetime, timedelta
    from core.memory import get_timetable_for_day
    out = []
    for label, day in (("Today", datetime.now()), ("Tomorrow", datetime.now() + timedelta(days=1))):
        name = day.strftime("%A")
        rows = get_timetable_for_day(name)
        classes = ", ".join(f"{r['start_time']}-{r['end_time']} {r['course_name']}" for r in rows) or "no classes"
        out.append(f"{label} ({name}): {classes}")
    return "Timetable:\n" + "\n".join(out)


_BUILDERS = {"attendance": _attendance, "marks": _marks, "grades": _grades,
             "exams": _exams, "timetable": _timetable}


def snapshot(text: str) -> str:
    """The sections of the student's record this question touches; all of
    attendance, marks and grades when it names none ("how am I doing")."""
    wanted = [name for name, rx in _SECTIONS.items() if re.search(rx, text or "", re.I)]
    if not wanted:
        wanted = ["attendance", "marks", "grades"]
    parts = []
    for name in wanted:
        try:
            block = _BUILDERS[name]()
        except Exception as e:
            print(f"[academic_context] {name} failed: {e}")
            block = ""
        if block:
            parts.append(block)
    return "\n\n".join(parts)

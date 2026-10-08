"""
features/daily_brief.py — assembles and sends the daily WhatsApp brief.

Fires once daily at 6:30 AM via a background thread in jarvis.py (same
thread+sleep pattern as daily_sync_worker/lms_reminder_worker — no new
scheduling dependency, by design). Can also be triggered manually
via the "daily_brief" voice intent ("give me my brief").

Each section catches its own failures independently — if one data
source is stale/unavailable (e.g. VTOP is down), that section reports
so instead of blocking the rest of the brief from sending.
"""

from features.base import FeatureResult

DAILY_BRIEF_HOUR   = 6
DAILY_BRIEF_MINUTE = 30


def _today_classes_section() -> str:
    from core.memory import get_timetable_for_day
    from features.vtop import _parse_time_to_minutes
    import datetime as _dt

    try:
        today   = _dt.datetime.now().strftime("%A")
        classes = get_timetable_for_day(today)
    except Exception:
        return "📅 Today's classes: couldn't check (timetable data unavailable)."

    if not classes:
        return "📅 No classes today."

    classes = sorted(classes, key=lambda c: _parse_time_to_minutes(c.get("start_time")) or 0)
    lines = ["📅 Today's classes:"]
    for c in classes:
        lines.append(f"  {c['start_time']}-{c['end_time']} {c['course_name']} ({c['room']})")
    return "\n".join(lines)


def _assignments_section() -> str:
    from core.memory import get_pending_lms_assignments
    from datetime import datetime, timedelta

    try:
        pending = get_pending_lms_assignments()
    except Exception:
        return "📝 Assignments: couldn't check (LMS data unavailable)."

    now = datetime.now()
    due_soon = []
    for a in pending:
        try:
            due = datetime.fromisoformat(a["due_date"])
            if due - now <= timedelta(hours=72):
                due_soon.append((a, due))
        except Exception:
            continue

    if not due_soon:
        return "📝 No assignments due in the next 72 hours."

    due_soon.sort(key=lambda x: x[1])
    lines = ["📝 Due soon:"]
    for a, due in due_soon:
        hrs = (due - now).total_seconds() / 3600
        lines.append(f"  {a['title']} ({a['course_name']}) — due in {int(hrs)}h")
    return "\n".join(lines)


def _attendance_section() -> str:
    from core.memory import get_all_attendance

    try:
        rows = get_all_attendance()
    except Exception:
        return "🎯 Attendance: couldn't check (VTOP data unavailable)."

    if not rows:
        return "🎯 Attendance: no data synced yet."

    low = [r for r in rows if r["percentage"] is not None and r["percentage"] < 78]
    if not low:
        return "🎯 Attendance: all good, nothing under 78%."

    lines = ["🎯 Attendance warning (<78%):"]
    for r in low:
        lines.append(f"  {r['course_name']}: {r['percentage']}%")
    return "\n".join(lines)


def _exam_section() -> str:
    from core.memory import get_all_exams
    from features.vtop import _parse_exam_date
    import datetime as _dt

    try:
        rows = get_all_exams()
    except Exception:
        return "🗓️ Exams: couldn't check (VTOP data unavailable)."

    if not rows:
        return "🗓️ No exam schedule synced yet."

    # Same logic as "when is my next exam" — the old date-only maths
    # dropped an afternoon exam from the brief once midnight had passed.
    from features.vtop import get_exams_result
    result = get_exams_result("next exam", entities={"when": "next"})
    if not result.data.get("exams"):
        return "🗓️ No upcoming exams found."
    return f"🗓️ {result.spoken}"


def _bunk_warning_section() -> str:
    from features.vtop import bunk_analysis
    from core.memory import get_timetable_for_day
    import datetime as _dt

    try:
        today         = _dt.datetime.now().strftime("%A")
        classes_today = get_timetable_for_day(today)
        codes         = {c["course_code"] for c in classes_today if c.get("course_code")}
        risky = []
        for code in codes:
            a = bunk_analysis(code)
            if a and a[0]["must_attend_next"] > 0:
                risky.append(a[0])
    except Exception:
        return ""

    if not risky:
        return ""

    names = ", ".join(r["course_name"] for r in risky)
    return f"⚠️ Don't skip today: {names} — you're already under 75% there."


def _tasks_today_section() -> str:
    from features.tasks import today as tasks_today

    try:
        result = tasks_today()
    except Exception:
        return ""
    tasks = result.data.get("tasks", [])
    if not tasks:
        return ""
    names = ", ".join(t["title"] for t in tasks[:3])
    extra = f" (+{len(tasks) - 3} more)" if len(tasks) > 3 else ""
    return f"✅ Tasks today: {names}{extra}"


def _custom_schedule_section() -> str:
    """Non-class blocks today — classes are already covered by _today_classes_section."""
    from features.schedule import today as schedule_today

    try:
        result = schedule_today()
    except Exception:
        return ""
    blocks = [b for b in result.data.get("blocks", []) if b.get("block_type") != "class"]
    if not blocks:
        return ""
    names = ", ".join(f"{b['title']} ({b['start_at'][11:16]})" for b in blocks[:3])
    return f"🗂️ Also scheduled: {names}"


def _yesterday_expense_section() -> str:
    from datetime import datetime, timedelta
    from core.memory import get_expenses_between

    try:
        start = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        expenses = get_expenses_between(start, start + timedelta(days=1))
    except Exception:
        return ""
    if not expenses:
        return ""
    total = sum(e["amount"] for e in expenses)
    return f"💸 Yesterday: ₹{total:.0f} spent"


def _last_night_focus_section() -> str:
    from datetime import datetime, timedelta
    from core.memory import get_focus_sessions_between

    try:
        start = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sessions = get_focus_sessions_between(start, start + timedelta(days=1))
    except Exception:
        return ""
    if not sessions:
        return ""
    by_subject = {}
    for s in sessions:
        by_subject[s["subject"]] = by_subject.get(s["subject"], 0) + (s["actual_minutes"] or 0)
    parts = [f"{mins // 60}h {mins % 60}m {subj}" for subj, mins in by_subject.items()]
    return f"📖 Studied yesterday: {', '.join(parts)}"


# Ordered most- to least-actionable — trimmed from the end (least
# actionable first) if the assembled brief runs past _MAX_LINES.
_SECTION_ORDER = [
    _exam_section,
    _bunk_warning_section,
    _today_classes_section,
    _tasks_today_section,
    _assignments_section,
    _attendance_section,
    _custom_schedule_section,
    _last_night_focus_section,
    _yesterday_expense_section,
]

_MAX_LINES = 12


def build_daily_brief() -> str:
    rendered = []
    for section_fn in _SECTION_ORDER:
        try:
            text = section_fn()
        except Exception:
            text = ""
        if text:
            rendered.append(text)

    def _line_count(sections):
        return sum(s.count("\n") + 1 for s in sections) + max(0, len(sections) - 1)

    while len(rendered) > 1 and _line_count(rendered) > _MAX_LINES:
        rendered.pop()  # drops the least-actionable section still present

    return "🌅 Morning brief, boss:\n\n" + "\n\n".join(rendered)


def send_daily_brief() -> FeatureResult:
    """Builds the brief and sends it via WhatsApp to MY_WHATSAPP_NUMBER."""
    from features.whatsapp import send_whatsapp_message
    from config import MY_WHATSAPP_NUMBER

    message = build_daily_brief()
    result  = send_whatsapp_message(MY_WHATSAPP_NUMBER, message)

    if result.ok:
        return FeatureResult(ok=True, data={"message": message}, display=message,
                              spoken="Sent your morning brief, boss.")
    msg = f"Couldn't send the daily brief — {result.error}"
    return FeatureResult(ok=False, data={"message": message}, display=msg, spoken=msg, error=result.error)


def get_daily_brief_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """'give me my brief' shows and speaks the brief. It used to always
    WhatsApp it instead — so asking in person got "Sent your brief", and
    with the WhatsApp service down the request failed outright. Sending is
    now only for an explicit "send/whatsapp me my brief"."""
    import re
    if re.search(r"\b(send|whatsapp|text|message)\b", (user_input or "").lower()):
        return send_daily_brief()
    if on_progress:
        on_progress("Putting your brief together, boss.")
    message = build_daily_brief()
    return FeatureResult(ok=True, data={"message": message}, display=message, spoken=_spoken_brief())


def _spoken_brief() -> str:
    """Built straight from the data. An LLM summary was tried and, in
    testing, called ordinary classes "exams" and invented low-attendance
    warnings — not acceptable for a brief people act on."""
    import datetime as _dt
    from core.memory import get_timetable_for_day
    parts = []
    exam = _exam_section()
    if exam.startswith("🗓️ Next exam is") and (" today" in exam or " tomorrow" in exam):
        parts.append(exam.replace("🗓️ ", ""))
    warn = _bunk_warning_section()
    if warn:
        parts.append(warn.replace("⚠️ ", ""))
    try:
        classes = sorted(get_timetable_for_day(_dt.datetime.now().strftime("%A")), key=lambda c: c.get("start_time") or "")
    except Exception:
        classes = []
    if classes:
        courses = list(dict.fromkeys(c["course_name"] for c in classes))
        parts.append(f"{len(courses)} class{'es' if len(courses) != 1 else ''} today, first at "
                     f"{classes[0]['start_time']} — {classes[0]['course_name']}.")
    else:
        parts.append("No classes today.")
    try:
        from core.memory import get_pending_lms_assignments
        overdue = [a for a in get_pending_lms_assignments()
                   if a.get("due_date") and a["due_date"] < _dt.datetime.now().isoformat()]
        if overdue:
            parts.append(f"{len(overdue)} assignment{'s are' if len(overdue) != 1 else ' is'} overdue.")
    except Exception:
        pass
    return " ".join(parts) + " Full brief is on screen."

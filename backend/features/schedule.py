"""
features/schedule.py — Schedule blocks: "time on the clock" (start_at/end_at),
distinct from tasks ("things to do", see features/tasks.py). VTOP classes get
materialized into schedule_blocks too (block_type='class'), so this module
is the single source of truth for "what am I doing right now" — that's what
features/focus.py's execution engine runs against.
"""

import re
from datetime import datetime, timedelta, time as dtime

import dateparser

from features.base import FeatureResult
from core.router import detect_subject
from core.memory import (
    add_block, blocks_for, active_block, next_block, find_conflicts,
    delete_block, materialize_classes_for_date,
)

_LEAD_INS = re.compile(r'^(block|schedule|book|plan)\s*', re.IGNORECASE)

_TIME_RANGE_RE = re.compile(
    r'(\d{1,2}(?::\d{2})?)\s*(am|pm)?\s*(?:to|-|–)\s*(\d{1,2}(?::\d{2})?)\s*(am|pm)?',
    re.IGNORECASE,
)

_DAY_WORDS = ["today", "tomorrow", "monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday", "sunday"]

# Multi-word relative-date phrases that MUST be matched as a whole phrase
# before falling back to _DAY_WORDS' single-word substring check below.
# Confirmed bugs without this: "day after tomorrow" matched "tomorrow"
# as a bare substring and silently resolved one day short; "in 3 days"
# matched no _DAY_WORDS entry at all and silently defaulted to today.
_RELATIVE_DATE_PATTERNS = [
    re.compile(r'\bday after tomorrow\b'),
    re.compile(r'\bin\s+\d+\s+days?\b'),
    re.compile(r'\bin\s+\d+\s+weeks?\b'),
    re.compile(r'\b\d+\s+days?\s+from\s+now\b'),
]

_AT_TIME_RE = re.compile(r'(\d{1,2}(?::\d{2})?)\s*(am|pm)?', re.IGNORECASE)


def _parse_hour_min(token: str) -> tuple[int, int]:
    if ':' in token:
        h, m = token.split(':')
        return int(h), int(m)
    return int(token), 0


def _infer_meridiem(hour: int) -> str:
    """Bare hour, no am/pm given — assume typical student daytime scheduling."""
    return 'am' if 7 <= hour <= 11 else 'pm'


def _resolve_time_range(text: str):
    """Returns (start_time, end_time, remaining_text) or None."""
    m = _TIME_RANGE_RE.search(text)
    if not m:
        return None

    start_tok, start_mer, end_tok, end_mer = m.groups()
    start_h, start_min = _parse_hour_min(start_tok)
    end_h, end_min = _parse_hour_min(end_tok)

    if not start_mer and end_mer:
        start_mer = end_mer
    if not end_mer and start_mer:
        end_mer = start_mer
    start_mer = (start_mer or _infer_meridiem(start_h)).lower()
    end_mer = (end_mer or _infer_meridiem(end_h)).lower()

    start_h24 = (start_h % 12) + (12 if start_mer == 'pm' else 0)
    end_h24 = (end_h % 12) + (12 if end_mer == 'pm' else 0)

    remaining = text[:m.start()] + text[m.end():]
    return dtime(start_h24, start_min), dtime(end_h24, end_min), remaining


def _resolve_date(text: str):
    """Returns (date, remaining_text)."""
    t = text.lower()

    # Multi-word relative phrases first — must match the WHOLE phrase,
    # not fall into the single-word loop below, which would otherwise
    # match a day-word substring inside a longer phrase and silently
    # resolve to the wrong date (see _RELATIVE_DATE_PATTERNS' comment).
    for pattern in _RELATIVE_DATE_PATTERNS:
        m = pattern.search(t)
        if m:
            phrase = m.group(0)
            parsed = dateparser.parse(
                phrase, settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()}
            )
            if parsed:
                remaining = text[:m.start()] + text[m.end():]
                return parsed.date(), remaining

    for word in _DAY_WORDS:
        if word in t:
            idx = t.find(word)
            parsed = dateparser.parse(word, settings={"PREFER_DATES_FROM": "future"})
            remaining = text[:idx] + text[idx + len(word):]
            return parsed.date(), remaining
    return datetime.now().date(), text


def _fmt_time(t: dtime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def add_schedule_block(raw_text: str, force: bool = False, on_progress=None) -> FeatureResult:
    text = _LEAD_INS.sub('', raw_text.strip())

    range_result = _resolve_time_range(text)
    if not range_result:
        msg = "I couldn't find a time range in that — try 'block 3 to 5 for TOC study'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_time_range")

    start_time, end_time, remaining = range_result
    target_date, remaining = _resolve_date(remaining)

    title = remaining
    for word in ["for", "to"]:
        title = re.sub(rf'\b{word}\b', ' ', title, count=1, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip(" ,.")
    title = title or "Study block"

    start_at = datetime.combine(target_date, start_time)
    end_at = datetime.combine(target_date, end_time)
    if end_at <= start_at:
        end_at += timedelta(days=1)

    linked_course = detect_subject(title)

    conflicts = find_conflicts(start_at.isoformat(), end_at.isoformat())
    if conflicts and not force:
        names = ", ".join(c["title"] for c in conflicts)
        msg = f"That conflicts with {names}. Schedule it anyway?"
        return FeatureResult(
            ok=False,
            data={"conflicts": conflicts, "pending": {
                "title": title, "start_at": start_at.isoformat(), "end_at": end_at.isoformat(),
                "linked_course": linked_course,
            }},
            display=msg, spoken=msg, error="conflict",
        )

    block_id = add_block(title, start_at.isoformat(), end_at.isoformat(), linked_course=linked_course)
    when = "today" if target_date == datetime.now().date() else target_date.strftime("%A")
    msg = f"Scheduled: {title}, {when} {_fmt_time(start_time)}–{_fmt_time(end_time)}"
    return FeatureResult(ok=True, data={"block_id": block_id}, display=msg, spoken=msg)


def add_confirmed_block(pending: dict) -> FeatureResult:
    """
    Creates a block from a previously-returned conflict's `pending` dict
    (see add_schedule_block's conflict branch below) after the user has
    explicitly said yes to scheduling it anyway. Skips conflict
    detection entirely — overriding the conflict is exactly what's
    being confirmed, re-checking it here would just ask the same
    question forever. Server-side wiring lives in server.py's
    handle_schedule_confirm_followup — this was a real dead end before:
    add_schedule_block returned this pending data on a conflict, but
    nothing ever picked it back up on a "yes" reply.
    """
    block_id = add_block(
        pending["title"], pending["start_at"], pending["end_at"],
        linked_course=pending.get("linked_course"),
    )
    start = datetime.fromisoformat(pending["start_at"])
    end = datetime.fromisoformat(pending["end_at"])
    when = "today" if start.date() == datetime.now().date() else start.strftime("%A")
    msg = f"Scheduled anyway: {pending['title']}, {when} {_fmt_time(start.time())}–{_fmt_time(end.time())}"
    return FeatureResult(ok=True, data={"block_id": block_id}, display=msg, spoken=msg)


def _render_blocks(blocks: list, with_day: bool = False) -> str:
    if not blocks:
        return "Nothing scheduled."
    lines = []
    for b in blocks:
        start = datetime.fromisoformat(b["start_at"])
        end = datetime.fromisoformat(b["end_at"])
        stamp = start.strftime("%a %H:%M") if with_day else f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
        tag = f" [{b['block_type']}]" if b["block_type"] != "custom" else ""
        lines.append(f"{stamp}  {b['title']}{tag}")
    return "\n".join(lines)


def today(on_progress=None) -> FeatureResult:
    now = datetime.now()
    materialize_classes_for_date(now.date())
    blocks = blocks_for(now.date())
    active = active_block(now)
    display = _render_blocks(blocks)
    if active:
        spoken = f"You're currently in {active['title']}."
    elif blocks:
        spoken = f"{len(blocks)} block{'s' if len(blocks) != 1 else ''} scheduled today."
    else:
        spoken = "Nothing scheduled today."
    return FeatureResult(ok=True, data={"blocks": blocks, "active": active}, display=display, spoken=spoken)


def tomorrow(on_progress=None) -> FeatureResult:
    tmrw = (datetime.now() + timedelta(days=1)).date()
    materialize_classes_for_date(tmrw)
    blocks = blocks_for(tmrw)
    spoken = (f"{len(blocks)} block{'s' if len(blocks) != 1 else ''} scheduled tomorrow."
              if blocks else "Nothing scheduled tomorrow.")
    return FeatureResult(ok=True, data={"blocks": blocks}, display=_render_blocks(blocks), spoken=spoken)


def week(on_progress=None) -> FeatureResult:
    start = datetime.now().date()
    all_blocks = []
    for i in range(7):
        d = start + timedelta(days=i)
        materialize_classes_for_date(d)
        all_blocks.extend(blocks_for(d))
    display = _render_blocks(all_blocks, with_day=True)
    spoken = f"{len(all_blocks)} blocks this week." if all_blocks else "Nothing scheduled this week."
    return FeatureResult(ok=True, data={"blocks": all_blocks}, display=display, spoken=spoken)


def next_up(on_progress=None) -> FeatureResult:
    now = datetime.now()
    active = active_block(now)
    if active:
        end = datetime.fromisoformat(active["end_at"])
        mins = int((end - now).total_seconds() / 60)
        msg = f"You're in {active['title']}, {mins}m left."
        return FeatureResult(ok=True, data={"block": active, "status": "active"}, display=msg, spoken=msg)

    materialize_classes_for_date(now.date())
    materialize_classes_for_date((now + timedelta(days=1)).date())
    nxt = next_block(now)
    if not nxt:
        msg = "Nothing else scheduled."
        return FeatureResult(ok=True, data={"block": None}, display=msg, spoken=msg)

    start = datetime.fromisoformat(nxt["start_at"])
    mins = int((start - now).total_seconds() / 60)
    msg = f"Next: {nxt['title']} in {mins}m ({start.strftime('%H:%M')})"
    return FeatureResult(ok=True, data={"block": nxt, "status": "upcoming"}, display=msg, spoken=msg)


def free_slots(raw_text: str = "", target_date=None, min_minutes: int = 30, on_progress=None) -> FeatureResult:
    date_from_text, _ = _resolve_date(raw_text) if raw_text else (None, "")
    target_date = target_date or date_from_text or datetime.now().date()
    materialize_classes_for_date(target_date)
    blocks = sorted(blocks_for(target_date), key=lambda b: b["start_at"])

    day_start = datetime.combine(target_date, dtime(8, 0))
    day_end = datetime.combine(target_date, dtime(22, 0))

    gaps = []
    cursor = day_start
    for b in blocks:
        b_start = datetime.fromisoformat(b["start_at"])
        b_end = datetime.fromisoformat(b["end_at"])
        if b_start > cursor and (b_start - cursor).total_seconds() / 60 >= min_minutes:
            gaps.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if day_end > cursor and (day_end - cursor).total_seconds() / 60 >= min_minutes:
        gaps.append((cursor, day_end))

    if not gaps:
        msg = "No free slots that day."
        return FeatureResult(ok=True, data={"gaps": []}, display=msg, spoken=msg)

    lines = [f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}" for s, e in gaps]
    display = "\n".join(lines)
    spoken = f"You're free {lines[0]}" + (f", and {len(lines) - 1} more gap{'s' if len(lines) > 2 else ''}." if len(lines) > 1 else ".")
    return FeatureResult(
        ok=True,
        data={"gaps": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in gaps]},
        display=display, spoken=spoken,
    )


def check_free_at(raw_text: str, on_progress=None) -> FeatureResult:
    target_date, remaining = _resolve_date(raw_text)
    m = _AT_TIME_RE.search(remaining.lower().split("free at", 1)[-1] if "free at" in remaining.lower() else remaining)
    if not m:
        msg = "I didn't catch what time you meant."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_time")

    hour, minute = _parse_hour_min(m.group(1))
    meridiem = (m.group(2) or _infer_meridiem(hour)).lower()
    hour24 = (hour % 12) + (12 if meridiem == "pm" else 0)
    target_dt = datetime.combine(target_date, dtime(hour24, minute))

    materialize_classes_for_date(target_date)
    conflicts = find_conflicts(target_dt.isoformat(), (target_dt + timedelta(minutes=1)).isoformat())
    if conflicts:
        msg = f"No — you have {conflicts[0]['title']} then."
        return FeatureResult(ok=True, data={"free": False, "block": conflicts[0]}, display=msg, spoken=msg)
    msg = "Yes, you're free then."
    return FeatureResult(ok=True, data={"free": True}, display=msg, spoken=msg)


def delete_by_query(query: str, on_progress=None) -> FeatureResult:
    now = datetime.now()
    q = query.lower()
    candidates = []
    for i in range(7):
        candidates.extend(
            b for b in blocks_for((now + timedelta(days=i)).date())
            if q in b["title"].lower() and b["block_type"] != "class"
        )
    if not candidates:
        msg = f"No scheduled block matching '{query}'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="not_found")
    if len(candidates) > 1:
        names = ", ".join(c["title"] for c in candidates[:4])
        msg = f"Found a few matches: {names}. Which one?"
        return FeatureResult(ok=False, data={"candidates": candidates}, display=msg, spoken=msg, error="ambiguous")

    delete_block(candidates[0]["id"])
    msg = f"Removed: {candidates[0]['title']}"
    return FeatureResult(ok=True, data={}, display=msg, spoken=msg)


# ══════════════════════════════════════════
#   FeatureResult wrappers for server.py's INTENT_HANDLERS
# ══════════════════════════════════════════

def get_schedule_add_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    raw_text = (entities or {}).get("raw_text") or user_input
    return add_schedule_block(raw_text, on_progress=on_progress)


def get_schedule_today_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return today(on_progress=on_progress)


def get_schedule_tomorrow_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return tomorrow(on_progress=on_progress)


def get_schedule_week_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return week(on_progress=on_progress)


def get_schedule_next_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return next_up(on_progress=on_progress)


def get_schedule_free_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    entities = entities or {}
    raw_text = entities.get("raw_text") or user_input
    if entities.get("mode") == "at_time":
        return check_free_at(raw_text, on_progress=on_progress)
    return free_slots(raw_text, on_progress=on_progress)


def get_schedule_delete_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    query = (entities or {}).get("query") or user_input
    return delete_by_query(query, on_progress=on_progress)

"""
features/tasks.py — Task manager: "things to do" (title + due date + done
state), distinct from schedule blocks ("time on the clock", see
features/schedule.py). All entry points return FeatureResult.
"""

import re
from datetime import datetime, timedelta

import dateparser.search

from features.base import FeatureResult
from core.memory import (
    add_task, list_tasks, complete_task as _complete_task_row,
    drop_task as _drop_task_row, search_tasks, get_task,
)

_LEAD_INS = re.compile(
    r'^(remind me to|add (a )?task( to)?|new task( to)?|task)\s*', re.IGNORECASE,
)

_HIGH_PHRASES = ["high priority", "urgent", "asap"]
_LOW_PHRASES  = ["low priority", "whenever", "someday"]

_TAGS = ["placement", "placements", "academic", "academics", "personal"]
_TAG_NORMALIZE = {"placements": "placement", "academics": "academic"}

_DATE_SETTINGS = {"PREFER_DATES_FROM": "future"}


def _strip_phrase(text: str, phrase: str) -> str:
    return re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE).strip(" ,.")


def _extract_priority(text: str) -> tuple[str, str]:
    low = text.lower()
    for phrase in _HIGH_PHRASES:
        if phrase in low:
            return "high", _strip_phrase(text, phrase)
    for phrase in _LOW_PHRASES:
        if phrase in low:
            return "low", _strip_phrase(text, phrase)
    return "normal", text


def _extract_tag(text: str) -> tuple[str | None, str]:
    low = text.lower()
    for tag in _TAGS:
        marker = f"for {tag}"
        if marker in low:
            return _TAG_NORMALIZE.get(tag, tag), _strip_phrase(text, marker)
    return None, text


_DAY_AFTER_TOMORROW_RE = re.compile(r'\bday after tomorrow\b', re.IGNORECASE)


def _extract_due(text: str) -> tuple[str | None, str]:
    # Confirmed bug: dateparser.search.search_dates matches just
    # "tomorrow" inside "day after tomorrow" rather than the whole
    # phrase, silently returning a date one day short. Handle this
    # explicit case before handing off to the general search below.
    m = _DAY_AFTER_TOMORROW_RE.search(text)
    if m:
        when = datetime.now() + timedelta(days=2)
        remaining = text[:m.start()] + text[m.end():]
        remaining = re.sub(r'\bby\b\s*$', '', remaining, flags=re.IGNORECASE).strip(" ,.")
        return when.isoformat(), remaining

    matches = dateparser.search.search_dates(text, settings=_DATE_SETTINGS)
    if not matches:
        return None, text
    phrase, when = matches[0]
    remaining = _strip_phrase(text, phrase)
    remaining = re.sub(r'\bby\b\s*$', '', remaining, flags=re.IGNORECASE).strip(" ,.")
    return when.isoformat(), remaining


def _fmt_due(due_at: str | None) -> str:
    if not due_at:
        return ""
    dt = datetime.fromisoformat(due_at)
    today = datetime.now().date()
    if dt.date() == today:
        return "today"
    if (dt.date() - today).days == 1:
        return "tomorrow"
    return dt.strftime("%A %d %b")


def add(raw_text: str, on_progress=None) -> FeatureResult:
    text = _LEAD_INS.sub("", raw_text.strip())
    priority, text = _extract_priority(text)
    tag, text = _extract_tag(text)
    due_at, text = _extract_due(text)
    title = text.strip(" ,.") or raw_text.strip()

    task_id = add_task(title, due_at=due_at, priority=priority, tag=tag)
    task = get_task(task_id)

    bits = []
    if due_at:
        bits.append(f"due {_fmt_due(due_at)}")
    if priority != "normal":
        bits.append(f"{priority} priority")
    suffix = f" ({', '.join(bits)})" if bits else ""

    msg = f"Added: {title}{suffix}"
    return FeatureResult(ok=True, data={"task": task}, display=msg, spoken=f"Added {title}{(', ' + ', '.join(bits)) if bits else ', no due date'}.")


def _render_list(tasks: list) -> str:
    if not tasks:
        return "No open tasks."
    lines = []
    for t in tasks:
        due = f" — {_fmt_due(t['due_at'])}" if t["due_at"] else ""
        flag = " 🔴" if t["priority"] == "high" else ""
        lines.append(f"[{t['id']}] {t['title']}{due}{flag}")
    return "\n".join(lines)


def list_open(within_days: int | None = None, on_progress=None) -> FeatureResult:
    tasks = list_tasks(status="open", due_within_days=within_days)
    display = _render_list(tasks)
    top = tasks[:3]
    if not tasks:
        spoken = "Nothing on your list."
    else:
        names = ", ".join(t["title"] for t in top)
        spoken = f"You've got {len(tasks)} open task{'s' if len(tasks) != 1 else ''}. Top of the list: {names}."
    return FeatureResult(ok=True, data={"tasks": tasks}, display=display, spoken=spoken)


def today(on_progress=None) -> FeatureResult:
    all_open = list_tasks(status="open")
    now = datetime.now()
    due_today_or_over = [
        t for t in all_open
        if t["due_at"] and datetime.fromisoformat(t["due_at"]).date() <= now.date()
    ]
    display = _render_list(due_today_or_over) if due_today_or_over else "Nothing due today or overdue."
    if not due_today_or_over:
        spoken = "Nothing due today."
    else:
        spoken = f"{len(due_today_or_over)} task{'s' if len(due_today_or_over) != 1 else ''} due today or overdue."
    return FeatureResult(ok=True, data={"tasks": due_today_or_over}, display=display, spoken=spoken)


def overdue(on_progress=None) -> FeatureResult:
    all_open = list_tasks(status="open")
    now = datetime.now()
    late = [t for t in all_open if t["due_at"] and datetime.fromisoformat(t["due_at"]) < now]
    return FeatureResult(ok=True, data={"tasks": late}, display=_render_list(late), spoken="")


def _resolve_one(query: str) -> tuple[dict | None, list, str | None]:
    """Returns (matched_task, all_candidates, error_message)."""
    candidates = search_tasks(query)
    if not candidates:
        return None, [], f"I couldn't find an open task matching '{query}'."
    if len(candidates) > 1:
        return None, candidates, None
    return candidates[0], candidates, None


def complete_by_query(query: str, on_progress=None) -> FeatureResult:
    task, candidates, err = _resolve_one(query)
    if err:
        return FeatureResult(ok=False, data={}, display=err, spoken=err, error="not_found")
    if task is None:
        names = ", ".join(c["title"] for c in candidates[:4])
        msg = f"Found a few matches: {names}. Which one?"
        return FeatureResult(ok=False, data={"candidates": candidates}, display=msg, spoken=msg, error="ambiguous")

    _complete_task_row(task["id"])
    msg = f"Completed: {task['title']}"
    return FeatureResult(ok=True, data={"task": task}, display=msg, spoken=msg)


def drop_by_query(query: str, on_progress=None) -> FeatureResult:
    task, candidates, err = _resolve_one(query)
    if err:
        return FeatureResult(ok=False, data={}, display=err, spoken=err, error="not_found")
    if task is None:
        names = ", ".join(c["title"] for c in candidates[:4])
        msg = f"Found a few matches: {names}. Which one?"
        return FeatureResult(ok=False, data={"candidates": candidates}, display=msg, spoken=msg, error="ambiguous")

    _drop_task_row(task["id"])
    msg = f"Dropped: {task['title']}"
    return FeatureResult(ok=True, data={"task": task}, display=msg, spoken=msg)


# ══════════════════════════════════════════
#   FeatureResult wrappers for server.py's INTENT_HANDLERS
# ══════════════════════════════════════════

def get_task_add_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    entities = entities or {}
    if entities.get("note") == "call":
        result = add(entities["raw_text"], on_progress=on_progress)
        if result.ok:
            result.spoken = "I can't place calls, so I've set a reminder instead. " + result.spoken
            result.display = "I can't place calls, so I've set a reminder instead.\n" + result.display
        return result
    return _get_task_add_result(user_input, entities, on_progress)


def _get_task_add_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    raw_text = (entities or {}).get("raw_text") or user_input
    return add(raw_text, on_progress=on_progress)


def get_task_list_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    within_days = (entities or {}).get("within_days")
    return list_open(within_days=within_days, on_progress=on_progress)


def get_task_today_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return today(on_progress=on_progress)


def get_task_complete_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    query = (entities or {}).get("query") or user_input
    return complete_by_query(query, on_progress=on_progress)


def get_task_drop_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    query = (entities or {}).get("query") or user_input
    return drop_by_query(query, on_progress=on_progress)

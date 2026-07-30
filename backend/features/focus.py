"""
features/focus.py — Focus mode: the execution engine for schedule blocks.
Starts a timed session, optionally runs a pomodoro work/break cycle, mutes
or kills configured distraction apps, and pushes live progress over the
WebSocket — see core/ws_hub.py for the event schema.
"""

import re
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from features.base import FeatureResult
from core.ws_hub import broadcast
from core.memory import (
    start_focus_session, get_active_focus_session, get_focus_session,
    end_focus_session, increment_focus_pomodoro, get_focus_stats,
    active_block as schedule_active_block, next_block as schedule_next_block,
)
from core.router import detect_subject
from config import FOCUS_KILL_MODE, FOCUS_KILL_APPS

_scheduler = BackgroundScheduler()
_scheduler.start()
_TICK_JOB_ID = "focus_tick"

_state = {
    "session_id": None, "subject": None, "phases": [], "phase_index": 0,
    "phase_started_at": None, "pomodoro": False,
}
_lock = threading.RLock()

_FOCUS_LEAD_INS = re.compile(
    r'^(focus mode|start focus mode|start pomodoro|start focus|focus on|pomodoro)\s*',
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r'(\d+)\s*(hours?|hrs?|minutes?|mins?)', re.IGNORECASE)


def _parse_duration_minutes(text: str):
    """Returns (minutes|None, remaining_text)."""
    m = _DURATION_RE.search(text)
    if not m:
        return None, text
    n = int(m.group(1))
    minutes = n * 60 if m.group(2).lower().startswith(("hour", "hr")) else n
    return minutes, text[:m.start()] + text[m.end():]


def parse_focus_start(raw_text: str):
    """Returns (subject, minutes, pomodoro)."""
    text = _FOCUS_LEAD_INS.sub('', raw_text.strip())
    minutes, text = _parse_duration_minutes(text)
    minutes = minutes or 25
    text = re.sub(r'\bfor\b', ' ', text, flags=re.IGNORECASE)
    subject = re.sub(r'\s+', ' ', text).strip(' ,.') or "study"
    no_pomodoro = any(p in raw_text.lower() for p in ["no pomodoro", "without pomodoro", "straight through"])
    return subject, minutes, not no_pomodoro


def _build_phase_plan(planned_minutes: int, work_min: int = 25, break_min: int = 5) -> list:
    phases = []
    remaining = planned_minutes
    is_work = True
    while remaining > 0:
        length = min(work_min if is_work else break_min, remaining)
        phases.append(("work" if is_work else "break", length))
        remaining -= length
        is_work = not is_work
    return phases


def _apply_distraction_control(enable: bool):
    """enable=True mutes/kills at session start; enable=False restores (mute only — a killed process can't be un-killed)."""
    from features.pc_control import mute_volume, unmute_volume

    if FOCUS_KILL_MODE == "mute":
        (mute_volume if enable else unmute_volume)()
        return

    if not enable:
        return
    import psutil
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if any(name == target.lower() for target in FOCUS_KILL_APPS):
            try:
                proc.kill()
            except Exception:
                pass


def _finish_locked(completed: bool):
    """Must be called with _lock held."""
    session_id = _state["session_id"]
    if session_id is None:
        return
    session = get_focus_session(session_id)
    started = datetime.fromisoformat(session["started_at"])
    actual_minutes = int((datetime.now() - started).total_seconds() / 60)
    pomodoros = session["pomodoros_completed"]
    end_focus_session(session_id, actual_minutes, pomodoros, status="completed" if completed else "aborted")
    _apply_distraction_control(False)
    try:
        _scheduler.remove_job(_TICK_JOB_ID)
    except Exception:
        pass
    broadcast({
        "type": "focus_ended", "subject": _state["subject"],
        "actual_minutes": actual_minutes, "pomodoros": pomodoros,
    })
    _state.update({"session_id": None, "subject": None, "phases": [], "phase_index": 0})


def _tick():
    with _lock:
        if _state["session_id"] is None:
            return
        phases = _state["phases"]
        idx = _state["phase_index"]
        if idx >= len(phases):
            _finish_locked(completed=True)
            return

        phase_name, phase_minutes = phases[idx]
        elapsed = (datetime.now() - _state["phase_started_at"]).total_seconds()
        remaining = phase_minutes * 60 - elapsed

        if remaining <= 0:
            if phase_name == "work":
                increment_focus_pomodoro(_state["session_id"])
            next_idx = idx + 1
            if next_idx >= len(phases):
                _finish_locked(completed=True)
                return
            next_name, next_minutes = phases[next_idx]
            broadcast({
                "type": "pomodoro_transition", "from": phase_name, "to": next_name,
                "break_seconds": next_minutes * 60 if next_name == "break" else 0,
            })
            _state["phase_index"] = next_idx
            _state["phase_started_at"] = datetime.now()
            remaining = next_minutes * 60
            phase_name = next_name

        broadcast({
            "type": "pomodoro_tick", "phase": phase_name,
            "remaining_seconds": max(0, int(remaining)), "session_id": _state["session_id"],
        })


def _find_linked_block(subject: str):
    course = detect_subject(subject)
    if not course:
        return None
    active = schedule_active_block()
    if active and active.get("linked_course") == course:
        return active
    nxt = schedule_next_block()
    if nxt and nxt.get("linked_course") == course:
        return nxt
    return None


def start(subject: str, minutes: int, pomodoro: bool = True, on_progress=None) -> FeatureResult:
    with _lock:
        if _state["session_id"] is not None:
            msg = f"Already focusing on {_state['subject']}. Stop that first."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="already_active")

        linked = _find_linked_block(subject)
        session_id = start_focus_session(subject, minutes, linked_block_id=linked["id"] if linked else None)
        phases = _build_phase_plan(minutes) if pomodoro else [("work", minutes)]

        _state.update({
            "session_id": session_id, "subject": subject, "phases": phases,
            "phase_index": 0, "phase_started_at": datetime.now(), "pomodoro": pomodoro,
        })
        _apply_distraction_control(True)
        _scheduler.add_job(_tick, "interval", seconds=1, id=_TICK_JOB_ID, replace_existing=True)

    broadcast({"type": "focus_started", "subject": subject, "planned_minutes": minutes, "pomodoro": pomodoro})
    msg = f"Focus started: {subject}, {minutes} minutes" + (" (pomodoro)" if pomodoro else "")
    return FeatureResult(
        ok=True, data={"session_id": session_id}, display=msg,
        spoken=f"Focus mode on. {minutes} minutes on {subject}.",
    )


def stop(session_id: int = None, on_progress=None) -> FeatureResult:
    with _lock:
        if _state["session_id"] is None:
            msg = "No active focus session."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="not_active")
        subject = _state["subject"]
        sid = _state["session_id"]
        _finish_locked(completed=False)

    session = get_focus_session(sid)
    actual = session["actual_minutes"]
    duration = f"{actual // 60}h {actual % 60}m" if actual >= 60 else f"{actual}m"
    msg = f"{duration} logged for {subject}. Good session."
    return FeatureResult(ok=True, data={"session_id": sid, "actual_minutes": actual}, display=msg, spoken=msg)


def status(on_progress=None) -> FeatureResult:
    active = get_active_focus_session()
    if not active:
        msg = "No active focus session."
        return FeatureResult(ok=True, data={"active": None}, display=msg, spoken=msg)
    started = datetime.fromisoformat(active["started_at"])
    elapsed_min = int((datetime.now() - started).total_seconds() / 60)
    remaining = max(0, active["planned_minutes"] - elapsed_min)
    msg = f"Focusing on {active['subject']}, {remaining}m remaining."
    return FeatureResult(ok=True, data={"active": active, "remaining_minutes": remaining}, display=msg, spoken=msg)


def stats(days: int = 7, on_progress=None) -> FeatureResult:
    rows = get_focus_stats(days)
    if not rows:
        msg = f"No focus sessions in the last {days} days."
        return FeatureResult(ok=True, data={"stats": []}, display=msg, spoken=msg)

    lines, total = [], 0
    for r in rows:
        h, m = r["total_minutes"] // 60, r["total_minutes"] % 60
        lines.append(f"{r['subject']}: {h}h {m}m ({r['sessions']} session{'s' if r['sessions'] != 1 else ''})")
        total += r["total_minutes"]

    display = "\n".join(lines)
    spoken = f"You studied {total // 60}h {total % 60}m across {len(rows)} subject{'s' if len(rows) != 1 else ''} in the last {days} days."
    return FeatureResult(ok=True, data={"stats": rows}, display=display, spoken=spoken)


# ══════════════════════════════════════════
#   FeatureResult wrappers for server.py's INTENT_HANDLERS
# ══════════════════════════════════════════

def get_focus_start_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    raw_text = (entities or {}).get("raw_text") or user_input
    subject, minutes, pomodoro = parse_focus_start(raw_text)
    return start(subject, minutes, pomodoro=pomodoro, on_progress=on_progress)


def get_focus_stop_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return stop(on_progress=on_progress)


def get_focus_status_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return status(on_progress=on_progress)


def get_focus_stats_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    return stats(on_progress=on_progress)

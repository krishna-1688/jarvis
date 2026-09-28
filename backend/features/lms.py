"""
features/lms.py — LMS integration for Jarvis.

Fetches assignments from VIT Chennai Moodle, checks submission status,
stores in SQLite, and sends WhatsApp reminders.
"""

import os
import sys
import threading
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import LMS_USERNAME, LMS_PASSWORD, MY_WHATSAPP_NUMBER
from features.lms_handler.session_generator import get_valid_session
from features.lms_handler.lms_calendar_scraper import parse_calendar
from features.lms_handler.lms_assignment_status import get_submission_status
from features.lms_handler.constants import LMS_CALENDAR_URL
from features.base import FeatureResult


def _check_status(session, assign_url: str) -> str:
    view_url = assign_url
    if "action=editsubmission" in view_url:
        view_url = view_url.replace("action=editsubmission", "")
    resp = session.get(view_url.rstrip("&?"))
    return get_submission_status(resp.text)


def _sync_once() -> dict:
    """One login + fetch + parse + status-check + save pass. No retry logic."""
    from core.memory import save_lms_assignments, get_pending_lms_assignments, update_lms_status

    session, status = get_valid_session(LMS_USERNAME, LMS_PASSWORD)
    if not session:
        return {"error": f"LMS login failed: {status}"}

    # Fetch calendar
    resp = session.get(LMS_CALENDAR_URL)
    assignments = parse_calendar(resp.text)

    # Check submission status for each assignment Moodle's "upcoming"
    # view still lists.
    for a in assignments:
        if a.get("assign_url"):
            try:
                a["status"] = _check_status(session, a["assign_url"])
            except Exception as e:
                print(f"Status check failed for {a['title']}: {e}")
                a["status"] = "unknown"

    # Always upsert (even with an empty list) so the sync log gets a row —
    # otherwise a course with 0 current LMS assignments never records a
    # sync time, get_lms_last_sync() stays None forever, and every single
    # /assignments poll decides data is stale and kicks off another
    # background sync (the repeated "LMS sync" lines at startup).
    new_assignments = save_lms_assignments(assignments)

    # Moodle's "upcoming" view stops listing an assignment once it's
    # submitted (or once its due date passes) — so anything we still
    # think is pending but that DIDN'T show up in this fetch would
    # otherwise never get its status re-checked again, staying frozen
    # at "not submitted" forever even after the real submission. Re-check
    # those directly via their own assignment page instead of relying on
    # them reappearing in the calendar feed.
    fresh_keys = {(a["title"], a["course_name"]) for a in assignments}
    stale_pending = [
        p for p in get_pending_lms_assignments()
        if (p["title"], p["course_name"]) not in fresh_keys and p.get("assign_url")
    ]
    for p in stale_pending:
        try:
            new_status = _check_status(session, p["assign_url"])
            if new_status != p.get("status"):
                update_lms_status(p["title"], p["course_name"], new_status)
        except Exception as e:
            print(f"Stale status re-check failed for {p['title']}: {e}")

    print(f"✅ LMS sync: {len(assignments)} assignments, {len(new_assignments)} new, "
          f"{len(stale_pending)} stale-pending re-checked")
    return {"assignments": assignments, "new": new_assignments}


_LMS_FAILURE_COOLDOWN_S = 5 * 60
_lms_last_failure_at = 0.0
_lms_sync_lock = threading.Lock()


def fetch_and_sync_lms() -> dict:
    """Serialized, with a cool-down after a failed login so repeated
    requests don't hammer Moodle while it's refusing us."""
    global _lms_last_failure_at
    if time.time() - _lms_last_failure_at < _LMS_FAILURE_COOLDOWN_S:
        return {"error": "Session expired, couldn't re-login"}
    with _lms_sync_lock:
        result = _fetch_and_sync_lms_uncached()
    if result.get("error"):
        _lms_last_failure_at = time.time()
    return result


def _background_lms_sync():
    if _lms_sync_lock.locked():
        return
    def _run():
        result = fetch_and_sync_lms()
        if not result.get("error"):
            try:
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": "assignments"})
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()


def _fetch_and_sync_lms_uncached() -> dict:
    """
    Main sync function:
    1. Login to Moodle
    2. Fetch upcoming calendar
    3. Parse assignments
    4. Check submission status for each
    5. Save/update SQLite
    6. Return new assignments list

    Retries the whole pass once on any failure (login failure or an
    unexpected exception, e.g. a network/session hiccup) instead of
    letting a raw exception bubble up. Two failures in a row means LMS
    genuinely won't let us back in.
    """
    def _attempt():
        try:
            return _sync_once(), None
        except Exception as e:
            return None, str(e)

    result, exc = _attempt()
    if exc is None and "error" not in result:
        return result

    print("  LMS sync failed, retrying login once...")
    result, exc = _attempt()
    if exc is None and "error" not in result:
        return result

    return {"error": "Session expired, couldn't re-login"}


def format_assignments_response(assignments: list) -> str:
    """Format assignment list for voice/text response."""
    from datetime import datetime

    if not assignments:
        return "No pending assignments found."

    lines = [f"You have {len(assignments)} pending assignment(s):\n"]
    for i, a in enumerate(assignments, 1):
        due = a.get("due_date", "")
        if due:
            try:
                dt  = datetime.fromisoformat(due)
                now = datetime.now()
                diff = dt - now
                days = diff.days
                if days < 0:
                    due_str = f"OVERDUE by {abs(days)} day(s)"
                elif days == 0:
                    hours = int(diff.seconds / 3600)
                    due_str = f"due TODAY in {hours} hour(s)"
                elif days == 1:
                    due_str = "due TOMORROW"
                else:
                    due_str = f"due in {days} day(s) ({dt.strftime('%d %b, %I:%M %p')})"
            except Exception:
                due_str = due
        else:
            due_str = "unknown due date"

        status = a.get("status", "unknown")
        status_label = "✅ Submitted" if status == "submitted" else "❌ Not submitted"
        lines.append(f"{i}. {a['title']}\n   Course: {a['course_name']}\n   {due_str} | {status_label}")

    return "\n".join(lines)


def send_whatsapp_reminder(assignment: dict, tier: str):
    """
    Send a WhatsApp reminder to yourself.
    tier: '3day' | '1day'
    """
    from features.whatsapp import send_whatsapp_message
    from datetime import datetime

    try:
        dt      = datetime.fromisoformat(assignment["due_date"])
        due_str = dt.strftime("%d %b %Y at %I:%M %p")
    except Exception:
        due_str = assignment.get("due_date_str", "")

    if tier == "3day":
        urgency = "📌 Reminder: 3 days left"
    else:
        urgency = "⚠️ URGENT: Due tomorrow"

    message = (
        f"{urgency}\n"
        f"Assignment: {assignment['title']}\n"
        f"Course: {assignment['course_name']}\n"
        f"Due: {due_str}\n"
        f"Status: Not submitted ❌\n"
        f"Submit: {assignment.get('assign_url','lms.vit.ac.in')}"
    )

    result = send_whatsapp_message(MY_WHATSAPP_NUMBER, message)
    if result.ok:
        print(f"✅ Reminder sent for: {assignment['title']}")
    else:
        print(f"⚠️ Reminder failed: {result.error}")


# ══════════════════════════════════════════
#   PUBLIC FEATURE-RESULT API (used by jarvis.py)
# ══════════════════════════════════════════

def get_lms_result(query_type: str, on_progress=None, allow_live_sync: bool = True) -> FeatureResult:
    """
    query_type: 'assignments' | 'sync'
    on_progress: optional callable(str) for interim voice feedback
    (jarvis.py passes `speak`) — kept as a callback so this module
    doesn't depend on the audio/mic stack.
    """
    from core.memory import get_pending_lms_assignments, get_lms_last_sync
    from datetime import datetime, timedelta

    if query_type == "sync":
        if on_progress:
            on_progress("Syncing LMS now boss.")
        sync_result = fetch_and_sync_lms()
        if sync_result.get("error") == "Session expired, couldn't re-login":
            spoken = "LMS kicked me out and won't let me back in."
            return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken,
                                  error=sync_result["error"])

        pending  = get_pending_lms_assignments()
        response = format_assignments_response(pending)
        spoken   = (f"Done. You have {len(pending)} pending assignment(s)."
                    if pending else "All clear, no pending assignments.")
        return FeatureResult(ok=True, data={"pending": pending}, display=response, spoken=spoken)

    # assignments query — check if data is fresh (< 6 hours old)
    last_sync  = get_lms_last_sync()
    data_fresh = False
    if last_sync:
        try:
            last_dt    = datetime.fromisoformat(last_sync)
            data_fresh = (datetime.now() - last_dt) < timedelta(hours=6)
        except Exception:
            pass

    if not data_fresh and not allow_live_sync:
        # Widget polling path: answer from cache, refresh in the background.
        _background_lms_sync()
    elif not data_fresh:
        if on_progress:
            on_progress("Let me check LMS real quick, boss.")
        sync_result = fetch_and_sync_lms()
        new = sync_result.get("new", [])
        if new and on_progress:
            names = ", ".join(a["title"] for a in new[:3])
            on_progress(f"Heads up — {len(new)} new assignment(s) posted: {names}")

    pending  = get_pending_lms_assignments()
    response = format_assignments_response(pending)

    if not pending:
        spoken = "No pending assignments right now boss."
    elif len(pending) == 1:
        a      = pending[0]
        spoken = f"One pending assignment — {a['title']} from {a['course_name']}."
    else:
        nearest = min(pending, key=lambda a: a.get("due_date") or "9999")
        spoken = (f"You have {len(pending)} pending assignments. The nearest is "
                  f"{nearest['title']} for {nearest['course_name']}. The full list is on screen.")

    return FeatureResult(ok=True, data={"pending": pending}, display=response, spoken=spoken)
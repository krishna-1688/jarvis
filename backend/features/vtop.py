"""
VTOP Integration for Jarvis
----------------------------
- fetch_vtop_all_sems(): fetches all sems, skips completed ones already in DB
- fetch_vtop_sem(sem_id): fetches one specific semester (used for daily sync)
- fetch_attendance() / fetch_timetable() / fetch_exams() / fetch_grades():
  fetch + save each non-marks VTOP data type
- Pure upsert (marks/attendance) or wipe+reinsert (timetable/exams, small
  semester-static datasets) — see each save_* function in core/memory.py

- get_marks_result() / refresh_marks_result() / get_attendance_result() /
  get_bunk_check_result() / get_timetable_result() / get_exams_result() /
  get_grades_result(): public FeatureResult-returning entry points used
  by jarvis.py. These own formatting and spoken-summary generation, so
  callers just read .display / .spoken.
"""

import os
import sys
import math
import re
import asyncio
import threading
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VTOP_USERNAME, VTOP_PASSWORD

from features.vtop_handler.constants import SEM_IDS, CURRENT_SEM


# The scrapers (aiohttp, BeautifulSoup, pandas: ~60 MB together) only run
# during a VTOP sync, a few times a day. These thin wrappers import them on
# first use so the always-on backend doesn't carry them while idle.
def vtop_client_session(**kwargs):
    from features.vtop_handler.tls import vtop_client_session as f
    return f(**kwargs)

def get_valid_session(*args, **kwargs):
    from features.vtop_handler.session_generator import get_valid_session as f
    return f(*args, **kwargs)

def get_timetable(*args, **kwargs):
    from features.vtop_handler.student_timetable import get_timetable as f
    return f(*args, **kwargs)

def get_exam_schedule(*args, **kwargs):
    from features.vtop_handler.student_exam_schedule import get_exam_schedule as f
    return f(*args, **kwargs)

def get_acadhistory(*args, **kwargs):
    from features.vtop_handler.student_academic_history import get_acadhistory as f
    return f(*args, **kwargs)

def get_attendance(*args, **kwargs):
    from features.vtop_handler.student_attendance import get_attendance as f
    return f(*args, **kwargs)

def get_marks_dict(*args, **kwargs):
    from features.vtop_handler.marks_view import get_marks_dict as f
    return f(*args, **kwargs)

from features.base import FeatureResult


# ── Freshness / force-refresh helpers (H.1) ────────────────
# Shared by get_attendance_result / get_timetable_result / get_exams_result:
# empty cache or an explicit refresh phrase forces a synchronous fetch
# (there's no good fallback); a merely-stale cache answers instantly from
# what's already there and kicks a background refetch instead of making
# the user wait on a VTOP round-trip they didn't ask for.

_FORCE_REFRESH_WORDS = (
    "refresh", "resync", "re-sync", "sync now", "sync it",
    "check again", "recheck", "latest data", "force",
)

def _wants_force_refresh(user_input: str) -> bool:
    t = (user_input or "").lower()
    return any(w in t for w in _FORCE_REFRESH_WORDS)


_refresh_in_flight = set()  # data_type strings currently being background-refreshed

# Minimum gap between background refresh attempts for one data type.
# De-duping in-flight runs wasn't enough: while VTOP was failing, every
# widget poll (3 widgets x once a minute) started a fresh login + captcha
# solve as soon as the previous attempt gave up.
_BACKGROUND_REFRESH_MIN_GAP_S = 15 * 60
_last_refresh_attempt = {}

def _background_refresh(data_type: str, fetch_fn):
    """Fire-and-forget refetch — de-duped and rate limited so repeated
    stale queries don't pile up redundant VTOP logins."""
    now = time.time()
    if data_type in _refresh_in_flight:
        return
    if now - _last_refresh_attempt.get(data_type, 0) < _BACKGROUND_REFRESH_MIN_GAP_S:
        return
    _last_refresh_attempt[data_type] = now
    _refresh_in_flight.add(data_type)

    def _run():
        try:
            result = fetch_fn()
            if not result.get("error"):
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": data_type})
        except Exception as e:
            print(f"⚠️ Background {data_type} refresh error: {e}")
        finally:
            _refresh_in_flight.discard(data_type)

    threading.Thread(target=_run, daemon=True).start()


# ── Internal async helpers ────────────────

async def _login(sess):
    username, csrf_token = await get_valid_session(VTOP_USERNAME, VTOP_PASSWORD, sess)
    return username, csrf_token

async def _fetch_and_save_sem(sess, username, csrf_token, sem_id) -> bool:
    """Fetch marks for one sem and save. Returns True if data found."""
    from core.memory import save_vtop_marks
    try:
        marks = await get_marks_dict(sess, username, sem_id, csrf_token)
        if marks:
            save_vtop_marks(marks, sem_id)
            return True
        print(f"  No data for {sem_id}")
        return False
    except Exception as e:
        print(f"  Failed {sem_id}: {e}")
        return False


# ── Dedicated persistent event loop for VTOP's async fetches ──────────
#
# ARCHITECTURE NOTE — same issue core/voice.py's TTS loop already works
# around, now hitting this module for the same reason: since Task 1.6,
# server.py runs both this module's asyncio.run()-based VTOP fetching
# AND features/web_control.py's Playwright sync API in the same
# process, dispatched across FastAPI's request thread pool. Playwright
# spins up its OWN event loop pinned to whatever OS thread first runs
# it, and that loop stays alive on that thread for the rest of the
# process's life. If a later request reuses that same pooled thread
# for a VTOP fetch, asyncio.run() collides with the already-running
# loop and raises a fatal error — not a clean "fetch failed" result,
# something that can silently take the request (or worse) down.
#
# Fix: VTOP's async work now runs on its OWN dedicated background
# thread with its OWN persistent event loop, created once. Every fetch
# schedules a coroutine onto that loop via run_coroutine_threadsafe()
# and blocks the calling (pooled) thread until it's done — that loop
# never touches Playwright's, regardless of which pooled thread calls
# in.
_vtop_loop        = None
_vtop_loop_thread = None
_vtop_loop_ready  = threading.Event()

def _vtop_loop_worker():
    global _vtop_loop
    # aiohttp wants a selector loop on Windows. Build one directly rather
    # than via set_event_loop_policy(): the policy is process-wide, and
    # once set, Playwright's own loop (web control, LMS) became a selector
    # loop too — which can't spawn the browser (NotImplementedError).
    if sys.platform == "win32":
        _vtop_loop = asyncio.SelectorEventLoop()
    else:
        _vtop_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_vtop_loop)
    _vtop_loop_ready.set()
    _vtop_loop.run_forever()

def _ensure_vtop_loop():
    global _vtop_loop_thread
    if _vtop_loop_thread is None or not _vtop_loop_thread.is_alive():
        _vtop_loop_thread = threading.Thread(target=_vtop_loop_worker, daemon=True)
        _vtop_loop_thread.start()
        _vtop_loop_ready.wait(timeout=5)

def _run_coro(coro, timeout=120):
    _ensure_vtop_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _vtop_loop)
    return future.result(timeout=timeout)


# After VTOP refuses a login twice in a row, don't try again for a bit —
# otherwise every request that finds an empty cache starts another
# (slow, captcha-solving) login that is almost certain to fail too.
_LOGIN_FAILURE_COOLDOWN_S = 120
_last_login_failure_at = 0.0

def _run_async_with_retry(fetch_fn) -> dict:
    global _last_login_failure_at
    if time.time() - _last_login_failure_at < _LOGIN_FAILURE_COOLDOWN_S:
        return {"error": "Session expired, couldn't re-login"}
    result = _run_async_with_retry_once(fetch_fn)
    if result.get("error") == "Session expired, couldn't re-login":
        _last_login_failure_at = time.time()
    return result


def _run_async_with_retry_once(fetch_fn) -> dict:
    """
    Runs an async fetch closure. If VTOP login itself fails (csrf_token
    came back empty) or an unexpected exception is raised, retries the
    whole thing once more — covers a session/captcha hiccup. Two login
    failures in a row means VTOP genuinely won't let us back in.

    A result like {"error": "No data for X"} or {"error": "Couldn't
    fetch timetable"} means login succeeded but there was simply
    nothing to return — that's NOT a session failure, so it's returned
    as-is without retrying (retrying wouldn't produce different data).
    """
    def _attempt():
        try:
            return _run_coro(fetch_fn()), None
        except Exception as e:
            return None, str(e)

    result, exc = _attempt()
    if exc is None and "error" not in result:
        return result

    login_failed = exc is not None or result.get("error") == "Login failed"
    if not login_failed:
        return result

    print("  VTOP login failed, retrying once...")
    result, exc = _attempt()
    if exc is None and "error" not in result:
        return result

    # Second attempt failed too — only mask it as a session/login
    # failure if it actually WAS one. If the retry logged in fine but
    # hit a different error (e.g. a real parsing bug), surface that
    # real error instead of a misleading "couldn't re-login" message.
    second_login_failed = exc is not None or result.get("error") == "Login failed"
    if second_login_failed:
        return {"error": "Session expired, couldn't re-login"}
    return result


# ── Public sync functions ─────────────────

def fetch_vtop_all_sems() -> dict:
    """
    Fetch marks for ALL semesters.
    Skips completed sems already stored in DB.
    Always refreshes current sem.
    """
    from core.memory import get_marks_synced_sems

    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            already_synced = get_marks_synced_sems()
            fetched = {}

            for sem_id in SEM_IDS:
                # Skip completed sems already stored — never re-fetch them
                if sem_id != CURRENT_SEM and sem_id in already_synced:
                    print(f"  Skipping {sem_id} — already in DB")
                    continue

                ok = await _fetch_and_save_sem(sess, username, csrf_token, sem_id)
                if ok:
                    fetched[sem_id] = True

            return {"type": "marks", "data": fetched}

    return _run_async_with_retry(_fetch)


def fetch_vtop_sem(sem_id: str) -> dict:
    """Fetch and save marks for one specific semester. Used by daily sync."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}
            ok = await _fetch_and_save_sem(sess, username, csrf_token, sem_id)
            return {"type": "marks", "data": {sem_id: ok}} if ok else {"error": f"No data for {sem_id}"}

    return _run_async_with_retry(_fetch)


def fetch_attendance() -> dict:
    """Fetch current-semester attendance and save to SQLite. Same retry-once pattern as marks."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_attendance(sess, username, csrf_token, semesterID=CURRENT_SEM)
            if not valid:
                return {"error": "Couldn't fetch attendance"}

            from core.memory import save_vtop_attendance
            save_vtop_attendance(data, semester_id=CURRENT_SEM)
            return {"type": "attendance", "data": data}

    return _run_async_with_retry(_fetch)


def fetch_attendance_for_sem(sem_id: str) -> dict:
    """
    Fetch and permanently save attendance for one SPECIFIC semester —
    including completed past ones. VTOP genuinely serves historical
    per-semester attendance via a semester-scoped query (confirmed live:
    a finished semester still returns real attended/total counts), it
    just was never asked for anything but whichever semester happened to
    answer first. Used for on-demand historical lookups and the
    fetch-everything-once pass (fetch_attendance_all_sems).
    """
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_attendance(sess, username, csrf_token, semesterID=sem_id)
            if not valid:
                return {"error": f"No attendance data for {sem_id}"}

            from core.memory import save_vtop_attendance
            save_vtop_attendance(data, semester_id=sem_id)
            return {"type": "attendance", "data": data, "semester_id": sem_id}

    return _run_async_with_retry(_fetch)


def fetch_attendance_all_sems() -> dict:
    """
    Fetch attendance for every semester, skipping past semesters already
    captured (their attendance is permanent — it can't change once the
    semester's over) and always refreshing the current one. Mirrors
    fetch_vtop_all_sems' same skip-completed-sems logic for marks.
    """
    from core.memory import has_attendance_for_semester

    fetched = {}
    for sem_id in SEM_IDS:
        if sem_id != CURRENT_SEM and has_attendance_for_semester(sem_id):
            print(f"  Skipping attendance for {sem_id} — already captured")
            continue
        result = fetch_attendance_for_sem(sem_id)
        fetched[sem_id] = "error" not in result

    return {"type": "attendance_all_sems", "data": fetched}


def fetch_timetable() -> dict:
    """Fetch full timetable and save to SQLite (wipe + re-insert). Same retry-once pattern as marks."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_timetable(sess, username, csrf_token, semesterID=CURRENT_SEM)
            if not valid:
                return {"error": "Couldn't fetch timetable"}

            from core.memory import save_vtop_timetable
            save_vtop_timetable(data)
            return {"type": "timetable", "data": data}

    return _run_async_with_retry(_fetch)


def fetch_exams() -> dict:
    """Fetch current-semester exam schedule and save to SQLite. Same retry-once pattern as marks."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_exam_schedule(sess, username, csrf_token, semesterID=CURRENT_SEM)
            if not valid:
                return {"error": "Couldn't fetch exam schedule"}

            from core.memory import save_vtop_exams
            save_vtop_exams(data, semester_id=CURRENT_SEM)
            return {"type": "exam", "data": data}

    return _run_async_with_retry(_fetch)


def fetch_exams_for_sem(sem_id: str) -> dict:
    """Fetch and permanently save the exam schedule for one SPECIFIC
    semester — including completed past ones (VTOP serves historical
    per-semester exam data the same way it does attendance/marks, just
    never asked with a specific past semester ID before)."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_exam_schedule(sess, username, csrf_token, semesterID=sem_id)
            if not valid:
                return {"error": f"No exam data for {sem_id}"}

            from core.memory import save_vtop_exams
            save_vtop_exams(data, semester_id=sem_id)
            return {"type": "exam", "data": data, "semester_id": sem_id}

    return _run_async_with_retry(_fetch)


def fetch_exams_all_sems() -> dict:
    """Fetch exam schedules for every semester, skipping past semesters
    already captured and always refreshing the current one. Mirrors
    fetch_attendance_all_sems/fetch_vtop_all_sems' same pattern."""
    from core.memory import has_exams_for_semester

    fetched = {}
    for sem_id in SEM_IDS:
        if sem_id != CURRENT_SEM and has_exams_for_semester(sem_id):
            print(f"  Skipping exams for {sem_id} — already captured")
            continue
        result = fetch_exams_for_sem(sem_id)
        fetched[sem_id] = "error" not in result

    return {"type": "exams_all_sems", "data": fetched}


def fetch_grades() -> dict:
    """Fetch academic history (CGPA + subject grades) and save to SQLite. Same retry-once pattern as marks."""
    async def _fetch():
        async with vtop_client_session() as sess:
            username, csrf_token = await _login(sess)
            if not csrf_token:
                return {"error": "Login failed"}

            data, valid = await get_acadhistory(sess, username, csrf_token)
            if not valid:
                return {"error": "Couldn't fetch grades"}

            from core.memory import save_vtop_grades
            save_vtop_grades(data)
            return {"type": "grades", "data": data}

    return _run_async_with_retry(_fetch)


# ══════════════════════════════════════════
#   OFFLINE MARKS FORMATTER — 100% SQLite
#   (moved from core/router.py — this is
#   display formatting, not intent routing)
# ══════════════════════════════════════════

def build_course_filter_sql(course_filter: str) -> str:
    """
    Returns SQL WHERE fragment for course type filtering.
    theory : course_code NOT ending with P (L, N, E, etc.)
    lab    : course_code ending with P
    all    : no filter — return everything
    """
    if course_filter == "lab":
        return "AND course_code LIKE '%P'"
    elif course_filter == "all":
        return ""   # no filter
    else:
        return "AND course_code NOT LIKE '%P'"


def format_marks_response(intent: dict) -> str | None:
    """
    Pure SQLite offline marks formatter.
    Returns formatted string or None if no data found.
    """
    from core.memory import get_db, sem_label, CURRENT_SEM

    conn          = get_db()
    qt            = intent["query_type"]
    cf_sql        = build_course_filter_sql(intent.get("course_filter", "theory"))
    cf = intent.get("course_filter", "theory")
    course_label = "Lab" if cf == "lab" else ("All" if cf == "all" else "Theory")

    def safe(v):
        return v if v is not None else "N/A"

    def format_grouped(rows, show_sem=False) -> str | None:
        if not rows:
            return None
        groups = {}
        for r in rows:
            key = (r["semester_id"], r["course_code"])
            if key not in groups:
                groups[key] = {
                    "title": r["course_title"],
                    "code":  r["course_code"],
                    "sem":   r["semester_id"],
                    "marks": []
                }
            line = f"  {r['mark_title']}: {safe(r['scored_mark'])}/{safe(r['max_mark'])}"
            if r["weightage_mark"] is not None:
                line += f" (Weightage: {r['weightage_mark']})"
            line += f" [{r['status']}]"
            groups[key]["marks"].append(line)

        lines = []
        for key, g in groups.items():
            header = f"\n{g['title']} ({g['code']})"
            if show_sem:
                header += f" — {sem_label(g['sem'])}"
            header += ":"
            lines.append(header)
            lines.extend(g["marks"])
        return "\n".join(lines).strip() if lines else None

    def format_flat(rows) -> str | None:
        if not rows:
            return None
        lines = []
        for r in rows:
            line = (f"{r['course_title']} ({r['course_code']}) "
                    f"[{sem_label(r['semester_id'])}] — "
                    f"{r['mark_title']}: {safe(r['scored_mark'])}/{safe(r['max_mark'])}")
            if r["weightage_mark"] is not None:
                line += f" (Weightage: {r['weightage_mark']})"
            line += f" [{r['status']}]"
            lines.append(line)
        return "\n".join(lines) if lines else None

    # ── 1. Specific subject + specific assessment ─────────
    if qt == "specific":
        rows = conn.execute(f"""
            SELECT course_code, course_title, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE (LOWER(course_title) LIKE LOWER(?) OR LOWER(course_code) LIKE LOWER(?))
              AND LOWER(mark_title) LIKE LOWER(?)
              AND mark_title != 'NO_MARKS_YET'
              {cf_sql}
            ORDER BY semester_id DESC
        """, (f"%{intent['subject']}%", f"%{intent['subject']}%",
              f"%{intent['assessment']}%")).fetchall()
        conn.close()
        result = format_flat(rows)
        if result:
            return f"{intent['assessment']} for {intent['subject']} ({course_label}):\n{result}"
        return None

    # ── 2. Subject in specific semester ──────────────────
    if qt == "subject_in_sem":
        rows = conn.execute(f"""
            SELECT course_code, course_title, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE semester_id = ?
              AND (LOWER(course_title) LIKE LOWER(?) OR LOWER(course_code) LIKE LOWER(?))
              AND mark_title != 'NO_MARKS_YET'
              {cf_sql}
            ORDER BY mark_title
        """, (intent["semester"], f"%{intent['subject']}%",
              f"%{intent['subject']}%")).fetchall()
        conn.close()
        result = format_grouped(rows)
        if result:
            return (f"{intent['subject']} {course_label} marks "
                    f"in {sem_label(intent['semester'])}:\n{result}")
        return None

    # ── 3. All marks for a subject (all sems) ────────────
    if qt == "subject":
        rows = conn.execute(f"""
            SELECT course_code, course_title, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE (LOWER(course_title) LIKE LOWER(?) OR LOWER(course_code) LIKE LOWER(?))
              AND mark_title != 'NO_MARKS_YET'
              {cf_sql}
            ORDER BY semester_id ASC, mark_title
        """, (f"%{intent['subject']}%", f"%{intent['subject']}%")).fetchall()
        conn.close()
        result = format_grouped(rows, show_sem=True)
        if result:
            return f"All {course_label} marks for {intent['subject']}:\n{result}"
        return None

    # ── 4. All marks in a semester ────────────────────────
    if qt == "semester":
        if intent["semester"] == "all":
            rows = conn.execute(f"""
                SELECT course_code, course_title, mark_title,
                       scored_mark, max_mark, weightage_mark, status, semester_id
                FROM vtop_marks
                WHERE mark_title != 'NO_MARKS_YET'
                  {cf_sql}
                ORDER BY semester_id ASC, course_code, mark_title
            """).fetchall()
            conn.close()
            result = format_grouped(rows, show_sem=True)
            if result:
                return f"All your {course_label} marks across all semesters:\n{result}"
            return None
        else:
            rows = conn.execute(f"""
                SELECT course_code, course_title, mark_title,
                       scored_mark, max_mark, weightage_mark, status, semester_id
                FROM vtop_marks
                WHERE semester_id = ?
                  AND mark_title != 'NO_MARKS_YET'
                  {cf_sql}
                ORDER BY course_code, mark_title
            """, (intent["semester"],)).fetchall()
            conn.close()
            result = format_grouped(rows)
            if result:
                return f"All {course_label} marks for {sem_label(intent['semester'])}:\n{result}"
            return None

    # ── 5. Assessment across all subjects in a semester ──
    if qt == "assessment_in_sem":
        rows = conn.execute(f"""
            SELECT course_code, course_title, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE semester_id = ?
              AND LOWER(mark_title) LIKE LOWER(?)
              AND mark_title != 'NO_MARKS_YET'
              {cf_sql}
            ORDER BY course_code
        """, (intent["semester"],
              f"%{intent['assessment']}%")).fetchall()
        conn.close()
        result = format_flat(rows)
        if result:
            return (f"{intent['assessment']} marks for all {course_label} subjects "
                    f"in {sem_label(intent['semester'])}:\n{result}")
        return None

    # ── 6. Assessment across ALL subjects ALL sems ────────
    if qt == "assessment_all":
        rows = conn.execute(f"""
            SELECT course_code, course_title, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE LOWER(mark_title) LIKE LOWER(?)
              AND mark_title != 'NO_MARKS_YET'
              {cf_sql}
            ORDER BY semester_id ASC, course_code
        """, (f"%{intent['assessment']}%",)).fetchall()
        conn.close()
        result = format_flat(rows)
        if result:
            return f"All {intent['assessment']} marks ({course_label}):\n{result}"
        return None

    # ── 7. Default: current semester theory marks ─────────
    rows = conn.execute(f"""
        SELECT course_code, course_title, mark_title,
               scored_mark, max_mark, weightage_mark, status, semester_id
        FROM vtop_marks
        WHERE semester_id = ?
          AND mark_title != 'NO_MARKS_YET'
          {cf_sql}
        ORDER BY course_code, mark_title
    """, (CURRENT_SEM,)).fetchall()
    conn.close()
    result = format_grouped(rows)
    if result:
        return f"Your current semester {course_label} marks:\n{result}"
    return None


# ══════════════════════════════════════════
#   SPOKEN SUMMARY (moved from jarvis.py)
# ══════════════════════════════════════════

def _get_spoken_marks_summary(data_text: str, intent: dict) -> str:
    from core.brain import ask_oneshot

    line_count = data_text.strip().count("\n")
    if line_count <= 3:
        lines = [l.strip() for l in data_text.strip().split("\n")
                 if l.strip() and not l.strip().endswith(":")]
        return " | ".join(lines[:4]) if lines else data_text.strip()

    prompt = (
        f"Give a SHORT 1-3 sentence spoken summary of this marks data. "
        f"Mention key numbers only. Be conversational, not robotic. Be honest: call a score "
        f"under half marks weak, never 'solid'. "
        f"Don't read every line.\n\nData:\n{data_text[:1500]}"
    )
    try:
        summary = ask_oneshot(prompt, max_tokens=120)
        if summary:
            return summary
        raise ValueError("empty summary")
    except Exception:
        for line in data_text.split("\n"):
            line = line.strip()
            if line and ":" in line and not line.endswith(":"):
                return line
        return "Got your marks — the full breakdown is on screen."


# ══════════════════════════════════════════
#   PUBLIC FEATURE-RESULT API (used by jarvis.py)
#
#   on_progress: optional callable(str) invoked for interim
#   voice feedback before/during a slow VTOP fetch (jarvis.py
#   passes `speak`). Kept as a callback instead of importing
#   core.voice directly here, so this feature module doesn't
#   depend on the audio/mic stack.
# ══════════════════════════════════════════

def get_marks_result(user_input: str, intent: dict, on_progress=None) -> FeatureResult:
    """
    Offline marks lookup (SQLite-first). If nothing found, auto-syncs
    from VTOP once, retries the lookup, then falls back to a free-form
    Groq answer over whatever's in the DB.
    """
    data = format_marks_response(intent)
    if data:
        spoken = _get_spoken_marks_summary(data, intent)
        return FeatureResult(ok=True, data={"intent": intent}, display=data, spoken=spoken)

    if on_progress:
        on_progress("Let me sync your marks from VTOP, one second.")
    result = fetch_vtop_all_sems()

    if "error" not in result:
        data = format_marks_response(intent)
        if data:
            spoken = _get_spoken_marks_summary(data, intent)
            return FeatureResult(ok=True, data={"intent": intent}, display=data, spoken=spoken)

    from core.memory import get_all_marks_summary, get_subject_search_hint
    from core.brain import ask_groq
    ctx = get_subject_search_hint() + "\n\n" + get_all_marks_summary(CURRENT_SEM)
    if ctx.strip():
        reply = ask_groq(user_input, extra_context=ctx)
        return FeatureResult(ok=True, data={"intent": intent, "fallback": True},
                              display=reply, spoken=reply)

    msg = "Couldn't find your marks. Try saying sync VTOP."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")


def refresh_marks_result(intent: dict, on_progress=None) -> FeatureResult:
    """Explicit 'refresh/sync marks from VTOP' request."""
    semester = intent.get("semester")
    if on_progress:
        on_progress("Let me check VTOP for you boss.")

    if semester and semester != "all":
        result = fetch_vtop_sem(semester)
    elif intent.get("want_all") or semester == "all":
        result = fetch_vtop_all_sems()
    else:
        result = fetch_vtop_sem(CURRENT_SEM)

    if "error" in result:
        if result["error"] == "Session expired, couldn't re-login":
            spoken = "VTOP kicked me out and won't let me back in."
            return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
        err = f"VTOP gave an error — {result['error']}"
        return FeatureResult(ok=False, data={}, display=err, spoken=err, error=result["error"])

    data = format_marks_response(intent)
    if data:
        spoken = _get_spoken_marks_summary(data, intent)
        return FeatureResult(ok=True, data={"intent": intent}, display=data, spoken=spoken)

    msg = "Fetched from VTOP but couldn't find what you asked for."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="not_found")


def _attendance_indicator(pct) -> str:
    if pct is None:
        return "❓"
    if pct < 75:
        return "🔴"
    if pct < 80:
        return "🟡"
    return "🟢"


def _attendance_margin(attended, total) -> str:
    """Exact 75% margin, computed here because the LLM got it wrong in
    testing (said "2 more" for 17/25, the answer is 7): every class
    attended or missed also grows the total."""
    if not total or attended is None:
        return ""
    if attended / total < 0.75:
        need = math.ceil((0.75 * total - attended) / 0.25)
        return f"need to attend {need} more in a row to reach 75%"
    can_skip = math.floor((attended - 0.75 * total) / 0.75)
    return f"can skip {can_skip} more and stay at 75%" if can_skip > 0 else "have no classes to spare"


def get_attendance_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    Attendance, offline-first (cached 6 hours). entities may contain
    {"course": "..."} to filter to one subject — otherwise all courses,
    worst attendance first.
    """
    from core.memory import (
        get_attendance_fresh_enough, get_all_attendance, get_attendance_for_course,
        has_attendance_for_semester, SEM_LABELS,
    )
    from core.router import normalize_course_query, detect_semester

    entities = entities or {}
    course   = normalize_course_query(entities.get("course"))
    force    = _wants_force_refresh(user_input)

    # A named past semester ("what's my attendance for sem 4") is now a
    # real, permanently-cached historical record (see
    # core.memory.save_vtop_attendance's per-(course_code, semester_id)
    # design + fetch_attendance_for_sem below) — VTOP genuinely serves
    # historical per-semester attendance via a semester-scoped query,
    # confirmed live. Old behavior silently answered with the CURRENT
    # semester's numbers regardless of what was asked ("sem 4" returned
    # Sem 5's data); now it either answers from the permanent cache or,
    # the first time that semester's ever asked about, fetches it live
    # on demand and caches it forever.
    asked_sem     = detect_semester(user_input)
    target_sem    = asked_sem if (asked_sem and asked_sem != "all") else CURRENT_SEM
    is_historical = target_sem != CURRENT_SEM
    fetch_failed  = False

    if is_historical:
        rows = get_attendance_for_course(course, semester_id=target_sem) if course \
            else get_all_attendance(semester_id=target_sem)

        if force or not has_attendance_for_semester(target_sem):
            if on_progress:
                on_progress(f"I don't have {SEM_LABELS.get(target_sem, target_sem)} cached yet — "
                            "let me pull it from VTOP, one sec.")
            result = fetch_attendance_for_sem(target_sem)
            if result.get("error") == "Session expired, couldn't re-login":
                spoken = "VTOP kicked me out and won't let me back in."
                return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
            fetch_failed = bool(result.get("error"))
            rows = get_attendance_for_course(course, semester_id=target_sem) if course \
                else get_all_attendance(semester_id=target_sem)
        # Never goes stale once fetched — a finished semester's
        # attendance can't change — so no freshness/background-refresh
        # check for the historical path, unlike current-semester below.
    else:
        # Whether a live fetch is warranted must be decided from whether
        # we have ANY current-semester data cached at all — not from
        # whether this specific course filter matched something. Those
        # are very different: no data at all genuinely needs a fetch; a
        # course filter matching nothing (e.g. "attendance in dbms" when
        # DBMS was a Sem 4 course, not this semester's) means the course
        # just isn't taught this semester, and re-fetching won't change
        # that. Confirmed real bug: filtering on the latter triggered a
        # ~20s live VTOP re-login+fetch that still correctly found
        # nothing, every single time that course was asked about.
        all_current_rows = get_all_attendance()
        rows = get_attendance_for_course(course) if course else all_current_rows

        if force or not all_current_rows:
            # Nothing cached yet, or the user explicitly asked to refresh —
            # either way there's no good cached fallback to answer from, so
            # this has to block on a live fetch.
            if on_progress:
                on_progress("Let me check your attendance on VTOP, one sec.")
            result = fetch_attendance()
            if result.get("error") == "Session expired, couldn't re-login":
                spoken = "VTOP kicked me out and won't let me back in."
                return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
            fetch_failed = bool(result.get("error"))
            rows = get_attendance_for_course(course) if course else get_all_attendance()
        elif not get_attendance_fresh_enough(6):
            # Stale but present — answer instantly from cache, refresh quietly.
            _background_refresh("attendance", fetch_attendance)

    if not rows:
        if fetch_failed:
            msg = (f"VTOP isn't responding right now — I couldn't pull attendance for "
                   f"{SEM_LABELS.get(target_sem, target_sem)}. I'll keep trying."
                   if is_historical else
                   "VTOP isn't responding right now — I couldn't pull your attendance. I'll keep trying in the background.")
        elif course:
            msg = f"I don't see a course matching \"{entities.get('course')}\" in your attendance — want the full list instead?"
        elif is_historical:
            msg = f"VTOP doesn't have an attendance record for {SEM_LABELS.get(target_sem, target_sem)} — might predate your enrollment or the semester's too old."
        else:
            msg = "VTOP hasn't got anything for your attendance yet — might just be early in the semester. I'll keep checking."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    lines = []
    for r in rows:
        pct = r["percentage"]
        pct_str = f"{pct}%" if pct is not None else "N/A"
        margin = _attendance_margin(r["attended_classes"], r["total_classes"])
        lines.append(
            f"{_attendance_indicator(pct)} {r['course_name']} ({r['course_code']}): "
            f"{pct_str} ({r['attended_classes']}/{r['total_classes']})"
            + (f" — {margin}" if margin and not is_historical else "")
        )

    sem_prefix = f"{SEM_LABELS.get(target_sem, target_sem)} — " if is_historical else ""

    if course:
        display = sem_prefix + "Attendance (worst first):\n" + "\n".join(lines)
        # "attendance in daa" matches theory AND lab; speak the one that
        # was asked about (VIT codes: ...L theory, ...P lab/practical).
        wants_lab = bool(re.search(r"\b(?:lab|practical|laboratory)\b", user_input.lower()))
        preferred = [x for x in rows if (x["course_code"] or "").upper().endswith("P") == wants_lab]
        r   = preferred[0] if preferred else rows[0]
        pct = r["percentage"]
        spoken = (f"In {SEM_LABELS.get(target_sem, target_sem)}, " if is_historical else "") + \
                 (f"{r['course_name']} attendance is {pct}%, "
                  f"{r['attended_classes']} out of {r['total_classes']} classes.")
        margin = _attendance_margin(r["attended_classes"], r["total_classes"])
        if margin and not is_historical:
            spoken += f" You {margin}."
    else:
        # "what's my attendance" / "overall attendance" — no course
        # named, so compute a single combined figure across every
        # course (total attended / total classes), not just the
        # per-course breakdown.
        total_attended = sum(r["attended_classes"] for r in rows if r["attended_classes"] is not None)
        total_classes  = sum(r["total_classes"] for r in rows if r["total_classes"] is not None)
        overall_pct = round((total_attended / total_classes) * 100, 1) if total_classes else None

        overall_line = (f"Overall attendance: {overall_pct}% ({total_attended}/{total_classes})"
                         if overall_pct is not None else "Overall attendance: N/A")
        display = sem_prefix + overall_line + "\n\nBy course (worst first):\n" + "\n".join(lines)

        worst    = rows[0]
        below_75 = [r for r in rows if r["percentage"] is not None and r["percentage"] < 75]
        overall_str = f"{overall_pct}%" if overall_pct is not None else "not available"
        sem_spoken_prefix = f"In {SEM_LABELS.get(target_sem, target_sem)}, y" if is_historical else "Y"
        if below_75:
            names  = ", ".join(r["course_name"] for r in below_75[:3])
            spoken = f"{sem_spoken_prefix}our overall attendance was {overall_str}. You were below 75% in {names}."
        else:
            spoken = (f"{sem_spoken_prefix}our overall attendance was {overall_str}. "
                      f"Lowest was {worst['course_name']} at {worst['percentage']}%.")

    return FeatureResult(
        ok=True,
        data={"rows": rows, "overall_percentage": overall_pct if not course else None},
        display=display, spoken=spoken,
    )


def get_alias_add_result(user_input: str, entities: dict = None) -> FeatureResult:
    """
    H.2: 'remember DAA means Design and Analysis of Algorithms' / 'call
    BCSE307P compiler lab' — teaches the resolver a new nickname for a
    course (core.course_resolver / core.memory.add_course_alias), so
    future queries using that nickname resolve immediately.
    """
    from core.course_resolver import resolve_course_best
    from core.memory import add_course_alias

    entities     = entities or {}
    alias        = (entities.get("alias") or "").strip()
    course_query = (entities.get("course_query") or "").strip()

    if not alias or not course_query:
        msg = 'Tell me both the nickname and the course — like "remember DAA means Design and Analysis of Algorithms".'
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")

    # margin=0: the user is explicitly naming a real course here (not a
    # loose query), so just take the top hit above threshold rather than
    # demanding a clear winning margin over the runner-up.
    match = resolve_course_best(course_query, margin=0)
    if not match:
        msg = f'I don\'t recognize "{course_query}" as a course yet — try the exact course code (e.g. BCSE204L).'
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    add_course_alias(match["course_code"], alias)
    msg = f'Got it — "{alias}" now means {match["course_name"]} ({match["course_code"]}).'
    return FeatureResult(
        ok=True,
        data={"course_code": match["course_code"], "course_name": match["course_name"], "alias": alias},
        display=msg, spoken=msg,
    )


def bunk_analysis(course_code: str = None) -> list:
    """
    For each course (or one, if course_code given — matched the same
    fuzzy way as get_attendance_for_course), computes:
      - can_skip: how many more classes can be missed this sem while
        staying >= 75% (floor(attended / 0.75) - total)
      - must_attend_next: if already below 75%, how many CONSECUTIVE
        classes must be attended to climb back to exactly 75%
      - projected_percentage: percentage after skipping one more class

    Reads whatever's currently in vtop_attendance — caller is
    responsible for making sure it's fresh (same cache-then-fetch
    pattern as get_attendance_result).
    """
    from core.memory import get_all_attendance, get_attendance_for_course

    rows = get_attendance_for_course(course_code) if course_code else get_all_attendance()
    results = []

    for r in rows:
        total    = r["total_classes"] or 0
        attended = r["attended_classes"] or 0
        pct      = r["percentage"]

        if total == 0:
            continue

        can_skip = math.floor(attended / 0.75) - total
        if can_skip >= 0:
            must_attend_next = 0
        else:
            must_attend_next = max(0, math.ceil(3 * total - 4 * attended))
            can_skip = 0

        projected = round((attended / (total + 1)) * 100, 1)

        results.append({
            "course_code":          r["course_code"],
            "course_name":          r["course_name"],
            "percentage":           pct,
            "can_skip":             can_skip,
            "must_attend_next":     must_attend_next,
            "projected_percentage": projected,
        })

    return results


def _resolve_day_from_offset(days_ahead) -> str | None:
    import datetime as _dt
    try:
        offset = int(days_ahead)
    except (TypeError, ValueError):
        return None
    return (_dt.datetime.now() + _dt.timedelta(days=offset)).strftime("%A")


def _bunk_check_for_day(day: str, days_ahead) -> FeatureResult:
    """
    'can I skip tomorrow' / 'which class should I skip today' — no
    named course, but a day is implied. Uses the timetable to find
    which courses actually meet that day, then runs bunk_analysis only
    for those, recommending the one with the highest current
    percentage as safest to skip.
    """
    from core.memory import get_timetable_for_day

    classes_today = get_timetable_for_day(day)
    if not classes_today:
        msg = f"No classes on {day} — nothing to skip."
        return FeatureResult(ok=True, data={"day": day}, display=msg, spoken=msg)

    course_codes = {c["course_code"] for c in classes_today if c.get("course_code")}
    per_course = []
    for code in course_codes:
        a = bunk_analysis(code)
        if a:
            per_course.append(a[0])

    if not per_course:
        msg = f"I don't have attendance data for {day}'s classes yet."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    per_course.sort(key=lambda a: a["percentage"] if a["percentage"] is not None else 0, reverse=True)

    lines, spoken_parts = [f"{day}'s classes:"], []
    for a in per_course:
        if a["must_attend_next"] > 0:
            verdict = "do NOT skip"
        elif a["can_skip"] > 0:
            verdict = "can skip"
        else:
            verdict = "borderline, skip carefully"
        lines.append(
            f"  {a['course_name']} ({a['course_code']}): {a['percentage']}% — {verdict} "
            f"(skipping drops it to {a['projected_percentage']}%)"
        )
        spoken_parts.append(f"{a['course_name']} ({verdict}, {a['projected_percentage']}%)")
    display = "\n".join(lines)

    when = "Today" if int(days_ahead) == 0 else ("Tomorrow" if int(days_ahead) == 1 else day)
    safest = per_course[0]
    spoken = f"{when} you have " + ", ".join(spoken_parts) + f". Safest to skip is {safest['course_name']}."

    return FeatureResult(ok=True, data={"day": day, "analysis": per_course}, display=display, spoken=spoken)


def _course_in_sentence(user_input: str):
    """Fallback when the classifier returned no course entity ("how many
    classes can I miss in probability" came back with {})."""
    from core.course_resolver import resolve_course_best
    best = resolve_course_best(user_input or "")
    return best["course_name"] if best else None


def get_bunk_check_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    "can I skip DBMS", "how many classes can I skip in TOC", "can I
    skip tomorrow", "which class should I skip today", "which subject
    am I safest to bunk in". entities may contain {"course": ...,
    "days_ahead": ...}.
    """
    from core.memory import get_attendance_fresh_enough, get_all_attendance
    from core.router import normalize_course_query

    entities   = entities or {}
    course     = normalize_course_query(entities.get("course") or _course_in_sentence(user_input))
    days_ahead = entities.get("days_ahead")

    # Same rule as get_attendance_result: block on VTOP only when nothing
    # is cached. Stale-but-present data answers instantly and refreshes
    # quietly, so a slow or down VTOP never turns a bunk check into an error.
    if not get_all_attendance():
        if on_progress:
            on_progress("Let me check your attendance on VTOP, one sec.")
        result = fetch_attendance()
        if result.get("error") == "Session expired, couldn't re-login":
            spoken = "VTOP kicked me out and won't let me back in."
            return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
    elif not get_attendance_fresh_enough(6):
        _background_refresh("attendance", fetch_attendance)

    # No named course but a day is implied — resolve via the timetable
    # instead of the generic "assume it meets" fallback.
    if not course and days_ahead is not None:
        day = _resolve_day_from_offset(days_ahead)
        if day:
            return _bunk_check_for_day(day, days_ahead)

    analysis = bunk_analysis(course)

    if not analysis:
        msg = "I don't have attendance data yet — try again in a bit."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    lines = ["Bunk analysis:"]
    for a in analysis:
        if a["must_attend_next"] > 0:
            lines.append(
                f"{a['course_name']} ({a['course_code']}): {a['percentage']}% — "
                f"must attend the next {a['must_attend_next']} class(es) to recover to 75%"
            )
        else:
            lines.append(
                f"{a['course_name']} ({a['course_code']}): {a['percentage']}% — "
                f"can skip {a['can_skip']} more this sem (skipping the next one drops it to {a['projected_percentage']}%)"
            )
    display = "\n".join(lines)

    if course:
        # "compiler design" matches theory AND lab — answer the one asked about.
        wants_lab = bool(re.search(r"\b(?:lab|practical)\b", user_input.lower()))
        a = next((x for x in analysis if (x.get("course_code") or "").upper().endswith("P") == wants_lab), analysis[0])
        if a["must_attend_next"] > 0:
            spoken = (f"You're already below 75% in {a['course_name']} — "
                      f"attend the next {a['must_attend_next']} classes to recover.")
        else:
            spoken = (f"You can skip {a['can_skip']} more {a['course_name']} classes this sem. "
                      f"Skipping the next one drops you to {a['projected_percentage']}%.")
    else:
        safest   = max(analysis, key=lambda a: a["can_skip"])
        riskiest = [a for a in analysis if a["must_attend_next"] > 0]
        if riskiest:
            names  = ", ".join(a["course_name"] for a in riskiest[:3])
            spoken = f"Don't skip {names} — you're already under 75% there."
        else:
            spoken = f"Safest to skip is {safest['course_name']}, you can miss {safest['can_skip']} more there."

    return FeatureResult(ok=True, data={"analysis": analysis}, display=display, spoken=spoken)


DAY_ALIASES = {
    "monday": "Monday", "mon": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "tues": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "thurs": "Thursday",
    "friday": "Friday", "fri": "Friday",
    "saturday": "Saturday", "sat": "Saturday",
    "sunday": "Sunday", "sun": "Sunday",
}

def _today_name() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%A")

def _tomorrow_name() -> str:
    import datetime as _dt
    return (_dt.datetime.now() + _dt.timedelta(days=1)).strftime("%A")

def _parse_time_to_minutes(t: str):
    """Parses '3 PM', '15:00', '3:30pm', '8:00' into minutes since midnight, or None."""
    import re as _re
    if not t:
        return None
    t = t.strip().lower().replace(".", "")
    m = _re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return None
    hour   = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm   = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute

def _sort_by_start(classes: list) -> list:
    return sorted(classes, key=lambda c: _parse_time_to_minutes(c.get("start_time")) or 0)


def _format_day_classes(day: str, classes: list, spoken_prefix: str) -> FeatureResult:
    if not classes:
        msg = f"No classes on {day}."
        return FeatureResult(ok=True, data={"day": day, "classes": []}, display=msg,
                              spoken=f"{spoken_prefix} you're free — no classes.")

    sorted_classes = _sort_by_start(classes)
    lines = [f"{day}'s classes:"]
    for c in sorted_classes:
        lines.append(f"  {c['start_time']}-{c['end_time']} {c['course_name']} ({c['room']})")
    display = "\n".join(lines)

    # A two-slot lab is two timetable rows; count it once.
    merged = []
    for c in sorted_classes:
        if merged and merged[-1]["course_code"] == c.get("course_code"):
            continue
        merged.append(c)
    names = [c["course_name"] for c in merged]
    listed = ", ".join(names[:5]) + (f", and {len(names) - 5} more" if len(names) > 5 else "")
    first = merged[0]
    spoken = (f"{spoken_prefix} you have {len(merged)} class{'es' if len(merged) != 1 else ''}, "
              f"starting {first['start_time']} with {first['course_name']}: {listed}.")

    return FeatureResult(ok=True, data={"day": day, "classes": sorted_classes}, display=display, spoken=spoken)


def _find_next_class() -> FeatureResult:
    import datetime as _dt
    from core.memory import get_timetable_for_day

    now         = _dt.datetime.now()
    today_name  = now.strftime("%A")
    now_minutes = now.hour * 60 + now.minute

    todays = get_timetable_for_day(today_name)
    upcoming_today = [
        c for c in todays
        if (_parse_time_to_minutes(c.get("start_time")) or -1) >= now_minutes
    ]
    upcoming_today = _sort_by_start(upcoming_today)

    if upcoming_today:
        c = upcoming_today[0]
        display = f"Next class: {c['course_name']} at {c['start_time']} in {c['room']}."
        spoken  = f"{c['course_name']} at {c['start_time']}, in {c['room']}."
        return FeatureResult(ok=True, data={"next_class": c}, display=display, spoken=spoken)

    # Nothing left today — walk forward up to a week to find the next one
    for offset in range(1, 8):
        day_name = (now + _dt.timedelta(days=offset)).strftime("%A")
        classes  = _sort_by_start(get_timetable_for_day(day_name))
        if classes:
            c    = classes[0]
            when = "tomorrow" if offset == 1 else day_name
            display = f"No more classes today. Next class: {c['course_name']} {when} at {c['start_time']} in {c['room']}."
            spoken  = f"Nothing left today. Next up is {c['course_name']} {when} at {c['start_time']}."
            return FeatureResult(ok=True, data={"next_class": c}, display=display, spoken=spoken)

    msg = "I don't see any upcoming classes in your timetable."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")


def _resolve_day(day_raw: str | None) -> str:
    if not day_raw:
        return _today_name()
    d = day_raw.strip().lower()
    if d == "today":
        return _today_name()
    if d == "tomorrow":
        return _tomorrow_name()
    return DAY_ALIASES.get(d, _today_name())


def _check_class_at(entities: dict) -> FeatureResult:
    """'do I have TOC on friday' (course[+day]) or 'am I free at 3pm' (time[+day])."""
    from core.memory import get_timetable_for_day
    from core.router import normalize_course_query

    course   = normalize_course_query(entities.get("course"))
    day      = _resolve_day(entities.get("day"))
    time_raw = entities.get("time")

    classes = get_timetable_for_day(day)

    if course:
        match = [c for c in classes if course.lower() in (c["course_name"] or "").lower()
                 or course.lower() in (c["course_code"] or "").lower()]
        if match:
            c = match[0]
            display = f"Yes — {c['course_name']} on {day} at {c['start_time']}-{c['end_time']} in {c['room']}."
            spoken  = f"Yes, {c['course_name']} on {day} at {c['start_time']}."
        else:
            display = f"No {course} class on {day}."
            spoken  = f"No, you don't have {course} on {day}."
        return FeatureResult(ok=True, data={"day": day, "course": course, "match": bool(match)},
                              display=display, spoken=spoken)

    if time_raw:
        target = _parse_time_to_minutes(time_raw)
        if target is None:
            msg = "I couldn't parse that time."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="bad_time")
        busy = None
        for c in classes:
            start = _parse_time_to_minutes(c.get("start_time"))
            end   = _parse_time_to_minutes(c.get("end_time"))
            if start is not None and end is not None and start <= target < end:
                busy = c
                break
        if busy:
            display = f"You have {busy['course_name']} at that time ({busy['start_time']}-{busy['end_time']})."
            spoken  = f"No, you've got {busy['course_name']} then."
        else:
            display = f"You're free at {time_raw} on {day}."
            spoken  = "Yes, you're free then."
        return FeatureResult(ok=True, data={"day": day, "busy": bool(busy)}, display=display, spoken=spoken)

    # Day given but no course/time — "what's my monday schedule", "i need
    # for monday", "classes on friday" all mean "show me that whole day's
    # classes", not a yes/no check. Previously fell through to the
    # missing_entity message below no matter what day was named (or, when
    # the classifier picked a different intent for this same kind of
    # phrasing, silently defaulted to TODAY's classes instead of the named
    # day) — reuse the same day-formatting the today/tomorrow modes use.
    if entities.get("day"):
        return _format_day_classes(day, classes, day)

    msg = "Tell me a course or a time to check."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")


def get_timetable_result(mode: str, user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    mode: 'today' | 'tomorrow' | 'week' | 'next_class' | 'class_at_time'
    entities may contain {"course":..., "day":..., "time":...} — used
    by 'class_at_time' to answer things like "do I have TOC on friday"
    or "am I free at 3pm". Cached for 24 hours.
    """
    from core.memory import get_timetable_fresh_enough, get_full_timetable

    entities = entities or {}
    force    = _wants_force_refresh(user_input)

    full         = get_full_timetable()
    fetch_failed = False

    if force or not full:
        if on_progress:
            on_progress("Let me pull your timetable from VTOP, one sec.")
        result = fetch_timetable()
        if result.get("error") == "Session expired, couldn't re-login":
            spoken = "VTOP kicked me out and won't let me back in."
            return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
        fetch_failed = bool(result.get("error"))
        full = get_full_timetable()
    elif not get_timetable_fresh_enough(24):
        _background_refresh("timetable", fetch_timetable)

    if mode == "today":
        day = _today_name()
        from core.memory import get_timetable_for_day
        return _format_day_classes(day, get_timetable_for_day(day), "Today")

    if mode == "tomorrow":
        day = _tomorrow_name()
        from core.memory import get_timetable_for_day
        return _format_day_classes(day, get_timetable_for_day(day), "Tomorrow")

    if mode == "week":
        if not full:
            msg = ("VTOP isn't responding right now — I couldn't pull your timetable. I'll keep trying."
                   if fetch_failed else
                   "I don't have your timetable yet — try again in a bit.")
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")
        lines = []
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            classes = full.get(day, [])
            if not classes:
                continue
            lines.append(f"\n{day}:")
            for c in _sort_by_start(classes):
                lines.append(f"  {c['start_time']}-{c['end_time']} {c['course_name']} ({c['room']})")
        display = "This week's timetable:" + "\n".join(lines)
        spoken  = "Here's your week — the full breakdown is on screen."
        return FeatureResult(ok=True, data={"timetable": full}, display=display, spoken=spoken)

    if mode == "next_class":
        return _find_next_class()

    if mode == "class_at_time":
        return _check_class_at(entities)

    msg = "I'm not sure what timetable info you want."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="unknown_mode")


def _parse_exam_date(date_str):
    """Best-effort parse of VTOP's exam date string. Returns None if unrecognized."""
    import datetime as _dt
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _normalize_exam_label(s: str) -> str:
    """Loose normalizer for matching 'cat1'/'CAT-I'/'CAT 1' style labels."""
    if not s:
        return ""
    s = s.upper().replace("-", " ").replace("_", " ")
    words = s.split()
    words = ["1" if w == "I" else "2" if w == "II" else w for w in words]
    return "".join(words)

WHEN_ALIASES = {
    "cat1": "CAT1", "cat 1": "CAT1", "cat-1": "CAT1",
    "cat2": "CAT2", "cat 2": "CAT2", "cat-2": "CAT2",
    "fat": "FAT",
}


def get_exams_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    entities may contain {"when": "next"|"all"|"cat1"|"cat2"|"fat",
    "course": "..."}. Cached for 24 hours.

    Note: "cat1"/"cat2"/"fat" filtering matches against whatever raw
    label VTOP's table actually uses (via _normalize_exam_label) — this
    is best-effort since the exact label text wasn't verifiable without
    a live VTOP session. If filtering doesn't match your actual VTOP
    labels, tell me the real label text and I'll tighten the matcher.
    """
    import datetime as _dt
    from core.memory import get_exams_fresh_enough, get_all_exams, has_exams_for_semester, SEM_LABELS
    from core.router import normalize_course_query, detect_semester

    entities = entities or {}
    when     = (entities.get("when") or "all").lower().strip()
    course   = normalize_course_query(entities.get("course"))
    force    = _wants_force_refresh(user_input)

    # Same historical-semester handling as get_attendance_result — a
    # completed semester's exam schedule is now a permanent record (see
    # core.memory.save_vtop_exams' per-(course_code, exam_type,
    # semester_id) design + fetch_exams_for_sem above), fetched live on
    # first ask and cached forever after, instead of the old behavior of
    # silently answering with the CURRENT semester's exam dates
    # regardless of which semester was actually asked about.
    asked_sem     = detect_semester(user_input)
    target_sem    = asked_sem if (asked_sem and asked_sem != "all") else CURRENT_SEM
    is_historical = target_sem != CURRENT_SEM
    fetch_failed  = False

    if is_historical:
        rows = get_all_exams(semester_id=target_sem)
        if force or not has_exams_for_semester(target_sem):
            if on_progress:
                on_progress(f"I don't have {SEM_LABELS.get(target_sem, target_sem)} cached yet — "
                            "let me pull it from VTOP, one sec.")
            result = fetch_exams_for_sem(target_sem)
            if result.get("error") == "Session expired, couldn't re-login":
                spoken = "VTOP kicked me out and won't let me back in."
                return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
            fetch_failed = bool(result.get("error"))
            rows = get_all_exams(semester_id=target_sem)
        # Never goes stale once fetched — a finished semester's exam
        # schedule can't change — so no freshness/background-refresh
        # check here, unlike current-semester below.
    else:
        rows = get_all_exams()

        if force or not rows:
            if on_progress:
                on_progress("Let me pull your exam schedule from VTOP, one sec.")
            result = fetch_exams()
            if result.get("error") == "Session expired, couldn't re-login":
                spoken = "VTOP kicked me out and won't let me back in."
                return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
            fetch_failed = bool(result.get("error"))
            rows = get_all_exams()
        elif not get_exams_fresh_enough(24):
            _background_refresh("exams", fetch_exams)

    if not rows:
        if fetch_failed:
            msg = (f"VTOP isn't responding right now — I couldn't pull the exam schedule for "
                   f"{SEM_LABELS.get(target_sem, target_sem)}. I'll keep trying."
                   if is_historical else
                   "VTOP isn't responding right now — I couldn't pull your exam schedule. I'll keep trying.")
        elif is_historical:
            msg = f"VTOP doesn't have an exam schedule on record for {SEM_LABELS.get(target_sem, target_sem)}."
        else:
            msg = "VTOP hasn't published an exam schedule yet — I'll keep checking and let you know when it's up."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    if course:
        rows = [r for r in rows if course.lower() in (r.get("course_name") or "").lower()
                or course.lower() in (r.get("course_code") or "").lower()]

    if when not in ("all", "next", ""):
        target   = WHEN_ALIASES.get(when, when.upper())
        filtered = [r for r in rows if target in _normalize_exam_label(r.get("exam_type") or "")]
        if filtered:
            rows = filtered

    if not rows:
        msg = "No exams found matching that."
        return FeatureResult(ok=True, data={"exams": []}, display=msg, spoken=msg)

    now = _dt.datetime.now()

    def _start(r):
        """Exam date + the session's start time ("02:00 PM - 03:30 PM").
        Date-only comparison treated today's afternoon exam as already
        past from midnight on."""
        d = _parse_exam_date(r.get("exam_date"))
        if not d:
            return None
        m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", str(r.get("session") or ""), re.IGNORECASE)
        if m:
            h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
            d = d.replace(hour=h, minute=int(m.group(2)))
        return d

    def _days_until(r):
        """Calendar days (today=0, tomorrow=1), not truncated 24h blocks —
        tomorrow's exam seen at 1 AM used to come out as "0 day(s) away"."""
        d = _start(r)
        return (d.date() - now.date()).days if d else 10**9

    def _not_over(r):
        d = _start(r)
        return d is not None and d + _dt.timedelta(hours=2) > now

    rows = sorted(rows, key=lambda r: _start(r) or _dt.datetime.max)

    if when == "next":
        upcoming = [r for r in rows if _not_over(r)]
        if not upcoming:
            msg = "No upcoming exams found — looks like everything on record has already happened."
            return FeatureResult(ok=True, data={"exams": []}, display=msg, spoken=msg)
        # "which one is after that" / "the one after" -> the second one
        after = re.search(r"\b(after that|after this|the one after|following one|second one|next one after)\b",
                          (user_input or "").lower())
        rows = upcoming[1:2] if after and len(upcoming) > 1 else upcoming[:1]

    def _days_str(d):
        if d >= 10**9:
            return "date unknown"
        if d == 0:
            return "today"
        if d == 1:
            return "tomorrow"
        if d > 1:
            return f"in {d} days"
        if d == -1:
            return "yesterday"
        return f"{-d} days ago"

    lines = []
    for r in rows:
        d = _days_until(r)
        lines.append(
            f"{r.get('exam_type')} — {r['course_name']} ({r['course_code']}): "
            f"{r['exam_date']} | {r['session']} | Venue: {r['venue']} | "
            f"Seat: {r['seat_number']} ({_days_str(d)})"
        )
    sem_prefix = f"{SEM_LABELS.get(target_sem, target_sem)} — " if is_historical else ""
    display = sem_prefix + "Exam schedule:\n" + "\n".join(lines)

    r0 = rows[0]
    d0 = _days_until(r0)
    sem_spoken_prefix = f"In {SEM_LABELS.get(target_sem, target_sem)}, " if is_historical else ""
    if when == "next":
        at = (r0.get("session") or "").split(" - ")[0]
        where = f" in {r0['venue']}" if r0.get("venue") else ""
        spoken = (f"{sem_spoken_prefix}Next exam is {r0.get('exam_type') or ''} {r0['course_name']} "
                  f"{_days_str(d0)}{f' at {at}' if at else ''}{where}.").replace("  ", " ")
    elif len(rows) == 1:
        spoken = f"{sem_spoken_prefix}{r0['course_name']} {when.upper()} is on {r0['exam_date']}, {_days_str(d0)}."
    elif is_historical:
        spoken = (f"{sem_spoken_prefix}you had {len(rows)} exam(s). Closest was "
                  f"{r0['course_name']} on {r0['exam_date']} ({_days_str(d0)}).")
    else:
        spoken = f"You have {len(rows)} exam(s). Closest is {r0['course_name']} on {r0['exam_date']} ({_days_str(d0)})."

    return FeatureResult(ok=True, data={"exams": rows}, display=display, spoken=spoken)


def _join_names(names: list, limit: int = 5) -> str:
    shown = names[:limit]
    if len(names) > limit:
        return ", ".join(shown) + f" and {len(names) - limit} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def _grade_history(user_input: str, course: str | None) -> FeatureResult:
    """Grades, narrowed by whatever the sentence names: a semester ("sem 3
    marks"), a grade ("which subjects did I get B in"), a course, or any
    mix of them. Without this, every one of those listed all courses."""
    from core.memory import get_all_grades, get_grades_for_semester, get_grade_for_course, sem_label
    from core.router import detect_semester, detect_grade_filter, grade_matches
    from core.course_resolver import resolve_course_best

    letters, rest = detect_grade_filter(user_input)
    # The course backstop resolves the whole sentence, so "subjects I got
    # C in" arrives as course=Calculus. Keep a course only if it's still
    # named once the grade phrase is gone.
    if course and letters:
        best = resolve_course_best(rest)
        course = best["course_name"] if best else None

    semester = detect_semester(user_input)
    semester = None if semester == "all" else semester

    if semester:
        base = get_grades_for_semester(semester)
        if course:
            base = [r for r in base if r in get_grade_for_course(course)]
    else:
        base = get_grade_for_course(course) if course else get_all_grades()
    rows = [r for r in base if grade_matches(r.get("grade"), letters)] if letters else base

    where = f" in {sem_label(semester)}" if semester else ""
    arrears = letters == {"F", "N"}          # "arrear", "backlog", "fail": F or any N grade
    wanted = "an N grade" if letters == {"N"} else " or ".join(sorted(letters))

    if course and letters and base:
        # "did I get an A in Calculus" is a yes/no about that one course
        # (the theory one unless a lab is named), not "which courses got A".
        wants_lab = bool(re.search(r"\b(?:lab|practical)\b", user_input or "", re.I))
        is_lab = lambda r: bool(re.search(r"\blab\b", r["course_name"], re.I)) or \
            (r.get("course_code") or "").upper().endswith("P")
        r = next((x for x in base if is_lab(x) == wants_lab), base[0])
        got = (r.get("grade") or "").upper()
        spoken = f"{'Yes' if grade_matches(got, letters) else 'No'}, you got {got} in {r['course_name']}{where}."
        display = spoken + "".join(f"\n  {x['course_name']}: {x['grade']}" for x in base if x is not r)
        return FeatureResult(ok=True, data={"rows": base}, display=display, spoken=spoken)

    if not rows:
        if course and not base:
            msg = (f"There's no grade for {course} yet — it's probably a course you're still taking. "
                   f"Ask 'what are my {course} marks' for your CAT and FAT scores.")
        elif semester == CURRENT_SEM:
            msg = (f"{sem_label(semester)} is still going, so there are no grades yet. "
                   "Ask 'what are my marks' for your CAT and FAT scores.")
        elif arrears:
            msg = f"You don't have any arrears{where} — no F or N grades."
        elif letters:
            msg = f"You didn't get {wanted} in any course{where}."
        elif semester:
            msg = f"I don't have grades for {sem_label(semester)}. Say 'sync from VTOP' to fetch them."
        else:
            msg = "No grade history found."
        return FeatureResult(ok=True, data={"rows": []}, display=msg, spoken=msg)

    # An arrear that shows up again later with a pass grade has been cleared.
    cleared = {}
    if letters & {"F", "N"}:
        history = get_all_grades()
        for r in rows:
            later = [x for x in history if x["id"] != r["id"] and x.get("course_code") == r.get("course_code")
                     and not grade_matches(x.get("grade"), {"F", "N"})]
            if later:
                cleared[r["id"]] = later[-1]["grade"]

    shown = "N1-N4" if letters == {"N"} else wanted
    title = ("Arrears (F / N grades)" if arrears else "Grades" + (f" ({shown})" if letters else "")) + \
        (f" for {sem_label(semester)}" if semester else "")
    lines = [title + ":"]
    for r in rows:
        sem = "" if semester else \
            f" ({sem_label(r['semester_id']) if r.get('semester_id') else 'unknown semester'})"
        note = f" — cleared later with {cleared[r['id']]}" if r["id"] in cleared else ""
        lines.append(f"  {r['course_name']}{sem}: {r['grade']}{note}")
    display = "\n".join(lines)

    names = [r["course_name"] for r in rows]
    if course and len(rows) <= 2 and not letters:
        r = rows[0]
        spoken = f"Your grade in {r['course_name']}{where} was {r['grade']}."
    elif arrears:
        n = len(rows)
        named = [f"{r['course_name']} ({r['grade']}{', cleared' if r['id'] in cleared else ''})" for r in rows]
        spoken = (f"You have {n} arrear{'s' if n != 1 else ''}{where}"
                  + (f", {len(cleared)} already cleared" if cleared else "") + f": {_join_names(named)}.")
    elif letters:
        n = len(rows)
        spoken = f"You got {wanted} in {n} course{'s' if n != 1 else ''}{where}: {_join_names(names)}."
    else:
        counts = {}
        for r in rows:
            counts[r.get("grade") or "?"] = counts.get(r.get("grade") or "?", 0) + 1
        order = "SABCDEFPN?"
        tally = ", ".join(f"{c} {g}" for g, c in sorted(counts.items(), key=lambda kv: order.find(kv[0][0])))
        spoken = (f"{sem_label(semester) if semester else 'Overall'}: {len(rows)} courses — {tally}. "
                  "The full list is on screen.")
    return FeatureResult(ok=True, data={"rows": rows}, display=display, spoken=spoken)


def get_grades_result(mode: str, user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    mode: 'cgpa' | 'sem_gpa' | 'grade_history'. entities may contain
    {"course": "..."}.

    'cgpa' reports the real server-computed CGPA. 'sem_gpa' computes a
    real credit-weighted GPA for that semester using vtop_grades'
    per-course credits (sourced from VTOP's academic history) — but
    only completed/graded courses appear there, so the current,
    still-in-progress semester won't have credits yet and falls back
    to listing grades with a clear "can't compute yet" caveat instead
    of fabricating a number.
    """
    from core.memory import (
        get_grades_fresh_enough, get_latest_cgpa_summary,
        get_all_grades, get_grades_for_semester, get_grade_for_course, sem_label
    )
    from core.router import detect_semester, normalize_course_query

    entities = entities or {}
    course   = normalize_course_query(entities.get("course"))

    # Grades change a few times a semester: answer from cache when there
    # is one, and only block on VTOP the very first time.
    if not get_latest_cgpa_summary() and not get_all_grades():
        if on_progress:
            on_progress("Let me pull your grades from VTOP, one sec.")
        result = fetch_grades()
        if result.get("error") == "Session expired, couldn't re-login":
            spoken = "VTOP kicked me out and won't let me back in."
            return FeatureResult(ok=False, data={}, display=spoken, spoken=spoken, error=result["error"])
    elif not get_grades_fresh_enough(24):
        _background_refresh("grades", fetch_grades)

    if mode == "cgpa":
        summary = get_latest_cgpa_summary()
        if not summary or summary.get("cgpa") is None:
            msg = "I don't have your CGPA yet — try again in a bit."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")
        cgpa = summary["cgpa"]
        display = (f"CGPA: {cgpa}\n"
                   f"Credits registered: {summary.get('credits_registered')}\n"
                   f"Credits earned: {summary.get('credits_earned')}")
        spoken = f"Your CGPA is {cgpa}."
        return FeatureResult(ok=True, data={"summary": summary}, display=display, spoken=spoken)

    if mode == "sem_gpa":
        semester = detect_semester(user_input)
        rows = get_grades_for_semester(semester) if semester and semester != "all" else []
        if not rows:
            msg = "I don't have grades for that semester yet."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

        from features.cgpa_predictor import GRADE_POINTS

        lines = [f"Grades for {sem_label(semester)}:"]
        weighted_points  = 0.0
        weighted_credits = 0.0
        excluded         = []  # missing credits, OR a non-GPA grade like P (Pass/Fail)
        for r in rows:
            lines.append(f"  {r['course_name']}: {r['grade']}")
            credits = r.get("credits")
            points  = GRADE_POINTS.get((r.get("grade") or "").upper())
            if credits is not None and points is not None:
                weighted_points  += credits * points
                weighted_credits += credits
            else:
                excluded.append(r["course_name"])
        display = "\n".join(lines)

        if weighted_credits > 0 and not excluded:
            gpa = round(weighted_points / weighted_credits, 3)
            display += f"\n\nGPA: {gpa}"
            spoken = f"Your GPA for {sem_label(semester)} is {gpa}."
        elif weighted_credits > 0:
            gpa = round(weighted_points / weighted_credits, 3)
            display += f"\n\nGPA ({len(excluded)} course(s) excluded — missing credits or a non-GPA grade like P): {gpa}"
            spoken = f"Your GPA for that semester is approximately {gpa} — a few courses were excluded (Pass/Fail or missing credits)."
        else:
            # Typically the current, still-in-progress semester —
            # VTOP's grade history only lists COMPLETED/graded courses.
            spoken = ("I can't compute a GPA for that semester yet — no credit data in, probably "
                      "because it's still in progress. Try 'what's my CGPA' for the overall number.")

        return FeatureResult(ok=True, data={"rows": rows}, display=display, spoken=spoken)

    if mode == "grade_history":
        return _grade_history(user_input, course)

    msg = "I'm not sure what grade info you want."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="unknown_mode")

"""
server.py — FastAPI wrapper around the same router + handler pipeline
jarvis.py's voice loop used to run in-process. Both the voice client
and any future UI (Electron, web) hit this over HTTP so there's one
brain, not two.

Endpoints:
  POST /command  — {"text": "..."} -> {"ok", "display", "spoken", "data",
                    "error", "expecting_confirmation"} — "data" is each
                    feature's raw FeatureResult.data (structured payload
                    for UI rendering, e.g. attendance percentages,
                    timetable rows), not just display/spoken text.
  GET  /health    — service status (WhatsApp service reachability, Groq
                    key presence, VTOP sync freshness)
  WS   /stream    — stub: pushes a ping every 10s for now. Real
                    wake-word-event / streaming-response push comes later.

Note on TTS: interim "let me check VTOP, one sec" style progress
messages are NOT spoken from here — this process has no guaranteed
microphone/speaker ownership, and speaking from two independent
processes (this one + jarvis.py) risks overlapping audio. Progress
callbacks just log to this process's console instead; the voice
client still speaks the final "spoken" field after the HTTP call
returns, so no information is lost — only the mid-request voice
interjection is gone (a real narrowing of behavior, flagged here on
purpose).

Note on WhatsApp confirmation state: core.voice.set_expecting_confirmation()
toggles state that only matters to whichever process owns the
microphone (it relaxes hallucination filtering for "yes"/"no" replies
during STT). Since that process is jarvis.py, not this one, this
module never calls it directly — instead /command returns an
"expecting_confirmation" flag, and jarvis.py's client applies it
locally after each response.
"""

import sys
import os
import threading
import time
import asyncio
import re
from collections import deque
from datetime import datetime, timedelta

# Windows' default console codepage (cp1252) can't encode the emoji used
# throughout this file's/the background workers' log output — under that
# codepage a bare `python server.py` crashes on the first such print
# (this hit during testing: startup failed with UnicodeEncodeError before
# the app ever came up). Force UTF-8 stdout/stderr regardless of the
# console's codepage; harmless when it's already UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.brain import ask_groq, ask_oneshot, note_feature_exchange, to_spoken
from core.router import route
from features.base import FeatureResult


def _log_progress(msg: str):
    """Passed as on_progress to feature functions instead of speak() —
    see module docstring for why TTS can't safely run in this process.
    Also broadcasts to the frontend (core.ws_hub) so a slow multi-step
    action — a VTOP fetch, Spotify's search-and-play automation, etc. —
    shows what's actually happening instead of just a spinner with no
    detail. Previously this only ever printed to the server's own
    console, which nobody but a developer would ever see."""
    print(f"  [progress] {msg}")
    try:
        from core.ws_hub import broadcast
        broadcast({"type": "progress", "text": msg})
    except Exception:
        pass


# ══════════════════════════════════════════
#   VTOP HANDLERS
# ══════════════════════════════════════════

def handle_vtop_fetch(user_input: str, payload: dict) -> FeatureResult:
    """
    Explicit 'refresh/sync marks from VTOP' request. The router no
    longer pre-builds the marks-intent dict itself (see core/router.py's
    module docstring) — extract_marks_intent's own keyword-based
    subject/assessment/semester parsing is a narrower, lower-risk
    decision than top-level classification, so it stays as-is, just
    called from here instead of from the old Stage 1.
    """
    from features.vtop import refresh_marks_result
    from core.router import extract_marks_intent
    intent = extract_marks_intent(user_input)
    return refresh_marks_result(intent, on_progress=_log_progress)


def handle_marks(user_input: str, payload: dict) -> FeatureResult:
    """Offline marks lookup — see handle_vtop_fetch's comment."""
    from features.vtop import get_marks_result
    from core.router import extract_marks_intent
    intent = extract_marks_intent(user_input)
    return get_marks_result(user_input, intent, on_progress=_log_progress)


def handle_attendance(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict from Stage 2, may contain {"course": "..."}."""
    from features.vtop import get_attendance_result
    return get_attendance_result(user_input, entities=payload, on_progress=_log_progress)

def handle_bunk_check(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict from Stage 2, may contain {"course":..., "days_ahead":...}."""
    from features.vtop import get_bunk_check_result
    return get_bunk_check_result(user_input, entities=payload, on_progress=_log_progress)

def handle_alias_add(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, contains {"alias":..., "course_query":...}."""
    from features.vtop import get_alias_add_result
    return get_alias_add_result(user_input, entities=payload)

def handle_open_dashboard(user_input: str, payload: dict) -> FeatureResult:
    from core.dashboard import launch
    ok, msg = launch()
    return FeatureResult(ok=ok, data={}, display=msg, spoken=msg if not ok else "Dashboard's coming up, boss.")


def handle_remember_fact(user_input: str, payload: dict) -> FeatureResult:
    """"remember that my birthday is 6 May" — stored in the memory graph,
    replacing an older value for the same thing, and linked to whatever
    it mentions so it comes up when a related question does."""
    from core.graph import get_graph
    fact = (payload or {}).get("fact") or user_input
    replaced = get_graph().remember(fact)
    if replaced:
        msg = f"Updated, boss. I had \"{replaced[-1]}\" before — I'll go with this now."
    else:
        msg = "Noted, boss. I'll remember that."
    return FeatureResult(ok=True, data={"fact": fact, "replaced": replaced}, display=msg, spoken=msg)


def handle_recall_facts(user_input: str, payload: dict) -> FeatureResult:
    """"what do you know about me" — facts, habits and what comes up most."""
    from core.graph import get_graph
    graph = get_graph()
    facts = graph.active_facts()
    conn = graph._reader()
    habits = graph.habits_line(conn)
    # mentions weighted by recency (30-day half-life): what you talk about
    # NOW, not courses from two semesters ago.
    import math as _math
    now = time.time()
    rows = conn.execute("SELECT label, mentions, last_seen FROM nodes WHERE kind IN ('course','person','exam')").fetchall()
    top = [r[0] for r in sorted(rows, key=lambda r: -r[1] * _math.pow(0.5, (now - r[2]) / 86400 / 30))[:4]]
    if not facts and not top:
        msg = "Not much yet — say 'remember that ...' and I will, and I'll pick things up as we talk."
        return FeatureResult(ok=True, data={"facts": []}, display=msg, spoken=msg)
    lines = []
    if facts:
        lines.append("You told me:\n" + "\n".join(f"- {f}" for f in facts))
    if top:
        lines.append("You bring up most: " + ", ".join(top))
    if habits:
        lines.append(habits.replace("Habit: KK usually", "You usually"))
    spoken_bits = []
    if facts:
        spoken_bits.append(f"You've told me {len(facts)} thing{'s' if len(facts) != 1 else ''}: " + "; ".join(facts[:3]) + ".")
    if top:
        spoken_bits.append("You talk about " + ", ".join(top[:3]) + " the most.")
    return FeatureResult(ok=True, data={"facts": facts, "top": top}, display="\n\n".join(lines),
                         spoken=" ".join(spoken_bits))


def handle_memory_about(user_input: str, payload: dict) -> FeatureResult:
    from core.graph import get_graph
    subject = (payload or {}).get("subject") or user_input
    info = get_graph().describe(subject)
    if not info:
        msg = f"Nothing about {subject} in my memory yet."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    parts = [f"{info['label']} — mentioned in {info['events']} conversation{'s' if info['events'] != 1 else ''}."]
    if info["links"]:
        parts.append("Connected to: " + ", ".join(info["links"]) + ".")
    display = " ".join(parts)
    if info["recent"]:
        display += "\n\nRecently:\n" + "\n".join(f"- {when}: {text}" for when, text in info["recent"])
    return FeatureResult(ok=True, data={"memory": info}, display=display, spoken=" ".join(parts))


def handle_forget_last(user_input: str, payload: dict) -> FeatureResult:
    from core.graph import get_graph
    forgotten = get_graph().forget_last_fact()
    msg = f"Forgotten: \"{forgotten}\"." if forgotten else "There's nothing you've asked me to remember yet."
    return FeatureResult(ok=True, data={}, display=msg, spoken=msg)


# "forget everything about X" deletes real history, and a misheard voice
# command shouldn't be able to do that silently — it asks first.
_pending_forget = {"subject": None}


def handle_forget_about(user_input: str, payload: dict) -> FeatureResult:
    from core.graph import get_graph
    subject = (payload or {}).get("subject") or ""
    info = get_graph().describe(subject)
    if not info:
        msg = f"I don't have anything about {subject}."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    _pending_forget["subject"] = subject
    msg = (f"Forget {info['label']} and the {info['events']} conversation"
           f"{'s' if info['events'] != 1 else ''} about it? Say yes to confirm.")
    return FeatureResult(ok=True, data={}, display=msg, spoken=msg)


def handle_forget_confirm_followup(user_input: str) -> FeatureResult | None:
    subject = _pending_forget["subject"]
    if not subject:
        return None
    _pending_forget["subject"] = None
    if _is_affirmative(user_input):
        from core.graph import get_graph
        removed = get_graph().forget_about(subject)
        msg = f"Done — forgot {subject} ({removed} memories removed)."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    if _is_negative(user_input):
        msg = "Okay, keeping it."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    return None


def _handle_timetable(mode: str):
    """Builds a (user_input, payload) handler for a fixed timetable query mode."""
    def _handler(user_input: str, payload: dict) -> FeatureResult:
        from features.vtop import get_timetable_result
        return get_timetable_result(mode, user_input, entities=payload, on_progress=_log_progress)
    return _handler

handle_timetable_today    = _handle_timetable("today")
handle_timetable_tomorrow = _handle_timetable("tomorrow")
handle_timetable_week     = _handle_timetable("week")
handle_next_class         = _handle_timetable("next_class")
handle_class_at_time      = _handle_timetable("class_at_time")

def handle_exams(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict from Stage 2, may contain {"when":..., "course":...}."""
    from features.vtop import get_exams_result
    return get_exams_result(user_input, entities=payload, on_progress=_log_progress)

def _handle_grades(mode: str):
    """Builds a (user_input, payload) handler for a fixed grades query mode."""
    def _handler(user_input: str, payload: dict) -> FeatureResult:
        from features.vtop import get_grades_result
        return get_grades_result(mode, user_input, entities=payload, on_progress=_log_progress)
    return _handler

handle_cgpa          = _handle_grades("cgpa")
handle_sem_gpa       = _handle_grades("sem_gpa")
handle_grade_history = _handle_grades("grade_history")

def handle_grade_target(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, may contain {"course":..., "target_grade":...}."""
    from features.cgpa_predictor import get_grade_target_result
    return get_grade_target_result(user_input, entities=payload, on_progress=_log_progress)

def handle_best_case_cgpa(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, may contain {"grade":...}."""
    from features.cgpa_predictor import get_best_case_cgpa_result
    return get_best_case_cgpa_result(user_input, entities=payload, on_progress=_log_progress)

def handle_cgpa_predict(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, may contain {"course":..., "grade":...}."""
    from features.cgpa_predictor import get_cgpa_predict_result
    return get_cgpa_predict_result(user_input, entities=payload, on_progress=_log_progress)

def handle_overall_cgpa_target(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, may contain {"target_cgpa":...}."""
    from features.cgpa_predictor import get_required_overall_gpa_result
    return get_required_overall_gpa_result(user_input, entities=payload, on_progress=_log_progress)

def handle_daily_brief(user_input: str, payload: dict) -> FeatureResult:
    from features.daily_brief import get_daily_brief_result
    return get_daily_brief_result(user_input, entities=payload, on_progress=_log_progress)


def handle_shutdown(user_input: str, payload: dict) -> FeatureResult:
    """
    Voice/text 'shutdown jarvis' (see core.router.is_shutdown_request) —
    cleanly stops THIS backend process. Independent of the Electron
    console's own Ctrl+Shift+Q shortcut, which just quits the app window;
    this is for stopping the backend itself, e.g. from jarvis.py's voice
    loop with no terminal handy. The actual exit is delayed slightly so
    this response has time to make it back over HTTP (and get spoken by
    jarvis.py) before the process disappears out from under the request.
    """
    def _delayed_exit():
        time.sleep(1.5)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    msg = "Shutting down, boss. Later."
    return FeatureResult(ok=True, data={}, display=msg, spoken=msg)


# ══════════════════════════════════════════
#   SPOTIFY HANDLER
# ══════════════════════════════════════════

def handle_spotify(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, contains {"action": ..., "query": ... (only for play)}."""
    from features import spotify

    action = (payload or {}).get("action", "")
    query = (payload or {}).get("query", "")

    # Both the Stage-1 regex and Stage-2 Groq classifier extract this raw
    # query fairly naively — "play X song in spotify" or "put on some X"
    # leak filler words straight into the search. One small, fast Groq
    # call here (core/spotify_intent.py) cleans it up regardless of which
    # path produced it, rather than trying to special-case every possible
    # phrasing with more regex.
    if action in ("play", "open") and query.strip():
        from core.spotify_intent import clean_song_query
        query = clean_song_query(query)

    if action == "play":
        return spotify.play_song(query, on_progress=_log_progress)
    if action == "open":
        return spotify.open_and_play(query, on_progress=_log_progress)
    if action == "pause":
        return spotify.pause_playback()
    if action == "resume":
        return spotify.resume_playback()
    if action == "next":
        return spotify.next_track()
    if action == "previous":
        return spotify.previous_track()
    if action == "volume_up":
        return spotify.adjust_volume(10)
    if action == "volume_down":
        return spotify.adjust_volume(-10)
    if action == "now_playing":
        return spotify.get_now_playing()

    msg = "Not sure what you want done in Spotify — try 'play <song>', 'pause', 'skip', or 'what's playing'."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")


# ══════════════════════════════════════════
#   TASK HANDLERS
# ══════════════════════════════════════════

def _broadcast_tasks_changed():
    """Lets the frontend's TasksModule refetch immediately instead of
    waiting up to 30s for its next poll — see useApiData's refreshOn.
    Matters most for voice: 'remind me to X' used to only show up in the
    Tasks module whenever it next happened to poll."""
    try:
        from core.ws_hub import broadcast
        broadcast({"type": "data_refreshed", "data_type": "tasks"})
    except Exception:
        pass

def handle_task_add(user_input: str, payload: dict) -> FeatureResult:
    from features.tasks import get_task_add_result
    result = get_task_add_result(user_input, entities=payload, on_progress=_log_progress)
    if result.ok:
        _broadcast_tasks_changed()
    return result

def handle_task_list(user_input: str, payload: dict) -> FeatureResult:
    from features.tasks import get_task_list_result
    return get_task_list_result(user_input, entities=payload, on_progress=_log_progress)

def handle_task_today(user_input: str, payload: dict) -> FeatureResult:
    from features.tasks import get_task_today_result
    return get_task_today_result(user_input, entities=payload, on_progress=_log_progress)

def handle_task_complete(user_input: str, payload: dict) -> FeatureResult:
    from features.tasks import get_task_complete_result
    result = get_task_complete_result(user_input, entities=payload, on_progress=_log_progress)
    if result.ok:
        _broadcast_tasks_changed()
    return result

def handle_task_drop(user_input: str, payload: dict) -> FeatureResult:
    from features.tasks import get_task_drop_result
    result = get_task_drop_result(user_input, entities=payload, on_progress=_log_progress)
    if result.ok:
        _broadcast_tasks_changed()
    return result


# ══════════════════════════════════════════
#   SCHEDULE HANDLERS
# ══════════════════════════════════════════

_pending_schedule_confirm = {"data": None}


def handle_schedule_confirm_followup(user_input: str) -> FeatureResult | None:
    """
    Checked before normal routing, same pattern as WhatsApp/PC-shutdown
    confirmation — if add_schedule_block just asked "that conflicts with
    X, schedule it anyway?", this turn's input is the yes/no answer to
    THAT, not a fresh command. Confirmed real gap: the conflict path
    already built and returned the pending block details, but nothing
    ever picked them back up on a "yes" — every conflicting schedule
    request was a permanent dead end.
    """
    pending = _pending_schedule_confirm["data"]
    if not pending:
        return None

    if _is_affirmative(user_input):
        _pending_schedule_confirm["data"] = None
        from features.schedule import add_confirmed_block
        return add_confirmed_block(pending)

    if _is_negative(user_input):
        _pending_schedule_confirm["data"] = None
        msg = "Okay, skipped that."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)

    # Unlike WhatsApp-send or a PC shutdown — both rare, deliberate,
    # hard-to-reverse actions worth blocking everything else until
    # explicitly resolved — a scheduling conflict is low-stakes and
    # routine. Confirmed real usability bug: treating every non-yes/no
    # reply as "still waiting" meant asking about ANYTHING else
    # ("what's my attendance", "schedule today", literally any other
    # command) got stuck repeating this message forever instead of
    # actually answering, since the pending conflict was never cleared.
    # A clear non-yes/no reply reads as "never mind, I moved on" —
    # quietly drop the pending conflict and let this turn's input route
    # normally instead of demanding an explicit resolution first.
    _pending_schedule_confirm["data"] = None
    return None


def handle_schedule_add(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_add_result
    result = get_schedule_add_result(user_input, entities=payload, on_progress=_log_progress)
    if result.error == "conflict" and result.data.get("pending"):
        _pending_schedule_confirm["data"] = result.data["pending"]
    return result

def handle_schedule_today(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_today_result
    return get_schedule_today_result(user_input, entities=payload, on_progress=_log_progress)

def handle_schedule_tomorrow(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_tomorrow_result
    return get_schedule_tomorrow_result(user_input, entities=payload, on_progress=_log_progress)

def handle_schedule_week(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_week_result
    return get_schedule_week_result(user_input, entities=payload, on_progress=_log_progress)

def handle_schedule_next(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_next_result
    return get_schedule_next_result(user_input, entities=payload, on_progress=_log_progress)

def handle_schedule_free(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_free_result
    return get_schedule_free_result(user_input, entities=payload, on_progress=_log_progress)

def handle_schedule_delete(user_input: str, payload: dict) -> FeatureResult:
    from features.schedule import get_schedule_delete_result
    return get_schedule_delete_result(user_input, entities=payload, on_progress=_log_progress)


# ══════════════════════════════════════════
#   FOCUS MODE HANDLERS
# ══════════════════════════════════════════

def handle_focus_start(user_input: str, payload: dict) -> FeatureResult:
    from features.focus import get_focus_start_result
    return get_focus_start_result(user_input, entities=payload, on_progress=_log_progress)

def handle_focus_stop(user_input: str, payload: dict) -> FeatureResult:
    from features.focus import get_focus_stop_result
    return get_focus_stop_result(user_input, entities=payload, on_progress=_log_progress)

def handle_focus_status(user_input: str, payload: dict) -> FeatureResult:
    from features.focus import get_focus_status_result
    return get_focus_status_result(user_input, entities=payload, on_progress=_log_progress)

def handle_focus_stats(user_input: str, payload: dict) -> FeatureResult:
    from features.focus import get_focus_stats_result
    return get_focus_stats_result(user_input, entities=payload, on_progress=_log_progress)


# ══════════════════════════════════════════
#   EXPENSE HANDLERS
# ══════════════════════════════════════════

def handle_expense_add(user_input: str, payload: dict) -> FeatureResult:
    from features.expenses import get_expense_add_result
    return get_expense_add_result(user_input, entities=payload, on_progress=_log_progress)

def handle_expense_summary(user_input: str, payload: dict) -> FeatureResult:
    from features.expenses import get_expense_summary_result
    return get_expense_summary_result(user_input, entities=payload, on_progress=_log_progress)

def handle_expense_search(user_input: str, payload: dict) -> FeatureResult:
    from features.expenses import get_expense_search_result
    return get_expense_search_result(user_input, entities=payload, on_progress=_log_progress)

def handle_alias_merchant(user_input: str, payload: dict) -> FeatureResult:
    """payload = entities dict, contains {"friendly_name": ...}."""
    from features.expenses import get_alias_merchant_result
    return get_alias_merchant_result(user_input, entities=payload)


# ══════════════════════════════════════════
#   PC HANDLER
# ══════════════════════════════════════════

def handle_screen(user_input: str, payload: dict) -> FeatureResult:
    if not (_device_state["lid_open"] and _device_state["display_on"]):
        msg = "Your screen is off right now, so there's nothing for me to look at."
        return FeatureResult(ok=True, data={}, display=msg, spoken=msg)
    from features.screen_vision import describe_screen
    return describe_screen((payload or {}).get("question") or user_input)


def handle_pc(user_input: str, payload: dict) -> FeatureResult | None:
    """
    payload is now just Groq's (usually empty) entities dict — the
    router no longer pre-parses PC commands itself (see core/router.py's
    module docstring on the Stage-1 minimization). handle_pc_command
    does its own internal keyword dispatch (volume vs mute vs open app
    vs window management etc.) using the raw text — that's a narrower,
    lower-collision-risk decision than top-level intent classification
    since we already know it's a PC-domain request by the time we're
    here, so it stays as-is rather than moving to Groq too.

    Returns None (not a FeatureResult) when handle_pc_command finds
    nothing to do — same "not actually my domain" convention handle_web/
    handle_whatsapp already use, so process() falls back to plain chat
    instead of a canned PC-flavored error. This matters concretely: the
    router's safety net demotes a misclassified system_shutdown to "pc"
    when "jarvis" isn't mentioned (e.g. "let's restart the conversation"
    briefly got misread as a shutdown intent) — that demotion is meant
    to land somewhere harmless, not force a "not sure what you want done
    on the PC" reply onto an utterance that was never about the PC at all.
    """
    from features.pc_control import handle_pc_command
    return handle_pc_command(user_input)


# ══════════════════════════════════════════
#   LMS HANDLER
# ══════════════════════════════════════════

def handle_lms_assignments(user_input: str, payload: dict) -> FeatureResult:
    from features.lms import get_lms_result
    return get_lms_result("assignments", on_progress=_log_progress)

def handle_lms_sync(user_input: str, payload: dict) -> FeatureResult:
    from features.lms import get_lms_result
    return get_lms_result("sync", on_progress=_log_progress)


# ══════════════════════════════════════════
#   WHATSAPP HANDLER
#   _pending_whatsapp_confirm lives here (server-side) since it's just
#   data, not audio I/O. Whether a confirmation is now pending gets
#   returned to the client as a flag — see module docstring.
# ══════════════════════════════════════════

_pending_whatsapp_confirm = {"data": None}


# Whole-word matching: plain substrings made "I don't know" / "not now"
# count as NO (they contain "no") and "yesterday" count as YES.
_AFFIRMATIVE_RE = re.compile(
    r"\b(?:yes|yeah|yep|yup|ya|sure|correct|right|confirm|confirmed|affirmative|ok|okay|"
    r"send it|go ahead|do it|that's (?:him|her|them|right)|thats (?:him|her|them|right))\b"
)
_NEGATIVE_RE = re.compile(
    r"\b(?:no|nope|nah|cancel|don't|dont|not (?:him|her|them|that one)|wrong|"
    r"different person|stop|nevermind|never mind|forget it)\b"
)

def _is_affirmative(text: str) -> bool:
    t = text.lower().strip()
    return bool(_AFFIRMATIVE_RE.search(t)) and not _NEGATIVE_RE.search(t)

def _is_negative(text: str) -> bool:
    return bool(_NEGATIVE_RE.search(text.lower().strip()))


def handle_pc_confirm_followup(user_input: str) -> FeatureResult | None:
    """
    Checked BEFORE normal routing, same as the WhatsApp followup below —
    if a shutdown/restart is armed (see features.pc_control.shutdown_pc/
    restart_pc, which only ARM now, never execute directly), this turn's
    input is treated as the yes/no answer to that, not a fresh command.
    This is the ONLY path that reaches confirm_pending_system_action,
    which is the ONLY place that actually calls the real OS shutdown/
    restart command — see pc_control.py's safety note for the incident
    this exists to prevent.
    """
    from features.pc_control import has_pending_system_action, confirm_pending_system_action

    if not has_pending_system_action():
        return None

    if _is_affirmative(user_input):
        ok, msg = confirm_pending_system_action(confirmed=True)
        return FeatureResult(ok=ok, data={}, display=msg, spoken=msg)

    if _is_negative(user_input):
        ok, msg = confirm_pending_system_action(confirmed=False)
        return FeatureResult(ok=ok, data={}, display=msg, spoken=msg)

    # Anything else — don't silently execute OR silently cancel an
    # ambiguous reply; ask again explicitly rather than guessing either
    # way on something this destructive.
    msg = "Still waiting — say yes to confirm, or no to cancel."
    return FeatureResult(ok=True, data={}, display=msg, spoken=msg)


def handle_whatsapp_followup(user_input: str) -> FeatureResult | None:
    pending = _pending_whatsapp_confirm["data"]
    if not pending:
        return None

    from features.whatsapp import send_whatsapp_confirmed

    if _is_affirmative(user_input):
        _pending_whatsapp_confirm["data"] = None
        result = send_whatsapp_confirmed(pending["contact_id"], pending["message"])
        if result.ok:
            msg = f'✅ Sent to {pending["name"]}: "{pending["message"]}"'
            return FeatureResult(
                ok=True, data={"sent_to": pending["name"], "message": pending["message"]},
                display=msg, spoken=f"Done, sent it to {pending['name']}.",
            )
        else:
            error = result.error or "unknown error"
            msg = f"❌ Failed to send — {error}"
            return FeatureResult(
                ok=False, data={}, display=msg,
                spoken=f"Couldn't send that — {error}", error=error,
            )

    if _is_negative(user_input):
        others = pending.get("other_matches", [])
        if others:
            next_match = others[0]
            remaining  = others[1:]
            _pending_whatsapp_confirm["data"] = {
                "contact_id":    next_match["id"],
                "name":          next_match["name"],
                "message":       pending["message"],
                "other_matches": remaining,
            }
            msg = f"Okay, not them. Did you mean {next_match['name']} instead?"
            return FeatureResult(ok=True, data={"candidate": next_match["name"]}, display=msg, spoken=msg)

        _pending_whatsapp_confirm["data"] = None
        msg = "Okay, cancelled. Tell me the name again and I'll search fresh."
        return FeatureResult(
            ok=True, data={}, display=msg,
            spoken="Okay, cancelled that. Try the name again whenever.",
        )

    _pending_whatsapp_confirm["data"] = None
    return None


def handle_whatsapp(user_input: str, payload: dict) -> FeatureResult | None:
    """
    Returns None (not a 2-tuple) when the cheap "tell "/"message"-style
    pre-filter matched but this Groq check decides it's NOT actually a
    send-a-message request (e.g. "tell my current sem is fall 2026-27"
    — "tell " is a legitimate WhatsApp trigger for "tell mom I'm late"
    style phrasing, but also fires on plain sentences). process()
    treats None as "not my domain" and falls back to the general brain
    instead of dead-ending with a fixed error message.
    """
    from features.whatsapp import (
        send_whatsapp_message, is_whatsapp_ready, find_whatsapp_contact,
        send_whatsapp_confirmed
    )
    from core.whatsapp_intent import extract_whatsapp_intent_groq

    # payload.raw_text was set by the old Stage-1 keyword pre-filter;
    # Groq's main classifier now sends {} for this intent (see
    # core/router.py's entity guidance) since this handler does its own
    # dedicated extraction anyway — user_input is the real source now,
    # payload.raw_text kept only in case something upstream still sets it.
    raw_text  = payload.get("raw_text") or user_input
    extracted = extract_whatsapp_intent_groq(raw_text)

    if not extracted:
        return None

    recipient = extracted["recipient"]
    message   = extracted["message"]

    if not is_whatsapp_ready():
        msg = "WhatsApp isn't connected boss. Start the WhatsApp service and scan the QR code first."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="whatsapp_not_ready")

    digits_only = "".join(c for c in recipient if c.isdigit() or c == "+")
    if len(digits_only) >= 8 and digits_only.replace("+", "").isdigit():
        result = send_whatsapp_message(recipient, message)
        if result.ok:
            sent_to = result.data["sent_to"]
            msg = f'✅ Sent to {sent_to}: "{message}"'
            return FeatureResult(
                ok=True, data={"sent_to": sent_to, "message": message},
                display=msg, spoken=f"Done, sent it to {sent_to}.",
            )
        else:
            error = result.error or "unknown error"
            msg = f"❌ Failed — {error}"
            return FeatureResult(ok=False, data={}, display=msg, spoken=f"Couldn't send that — {error}", error=error)

    print(f"🔍 Searching contacts for '{recipient}'...")
    matches = find_whatsapp_contact(recipient)

    if not matches:
        msg = f"I couldn't find anyone named '{recipient}' in your contacts."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="contact_not_found")

    best         = matches[0]
    second_score = matches[1]["score"] if len(matches) > 1 else 0

    if best["score"] >= 0.85 and (best["score"] - second_score) >= 0.15:
        result = send_whatsapp_confirmed(best["id"], message)
        if result.ok:
            msg = f'✅ Sent to {best["name"]}: "{message}"'
            return FeatureResult(
                ok=True, data={"sent_to": best["name"], "message": message},
                display=msg, spoken=f"Done, sent it to {best['name']}.",
            )
        else:
            error = result.error or "unknown error"
            msg = f"❌ Failed — {error}"
            return FeatureResult(ok=False, data={}, display=msg, spoken=f"Couldn't send that — {error}", error=error)

    seen_ids = set()
    deduped  = []
    for m in matches:
        if m["id"] not in seen_ids:
            seen_ids.add(m["id"])
            deduped.append(m)
    matches      = deduped
    best         = matches[0]
    second_score = matches[1]["score"] if len(matches) > 1 else 0

    other_candidates = matches[1:4]
    _pending_whatsapp_confirm["data"] = {
        "contact_id":    best["id"],
        "name":          best["name"],
        "message":       message,
        "other_matches": other_candidates,
    }

    if len(matches) > 1 and (best["score"] - second_score) < 0.15:
        names  = ", ".join(m["name"] for m in matches[:3])
        msg    = f"Found a few close matches: {names}. Did you mean {best['name']}?"
        spoken = f"I found a few similar names — did you mean {best['name']}?"
    else:
        msg    = f"Did you mean {best['name']}? Say yes to send \"{message}\"."
        spoken = f"Did you mean {best['name']}? Say yes to send it."

    return FeatureResult(ok=True, data={"candidates": [m["name"] for m in matches[:4]]}, display=msg, spoken=spoken)


# ══════════════════════════════════════════
#   WEB HANDLER
# ══════════════════════════════════════════

def handle_web(user_input: str, payload: dict) -> FeatureResult | None:
    """Returns None (not a 2-tuple) when the cheap pre-filter matched but
    this isn't actually a web-browsing request — see handle_whatsapp's
    docstring for why that matters."""
    from features.web_control import (
        open_site, search_on_site, general_web_search,
        get_page_text, compare_across_sites,
        click_result, scroll_page, go_back, close_current_tab,
    )
    from core.web_intent import extract_web_intent

    # See handle_whatsapp's identical comment — payload is now typically
    # {} for this intent, user_input is the real source.
    raw_text  = payload.get("raw_text") or user_input
    extracted = extract_web_intent(raw_text)

    if not extracted:
        return None

    action = extracted["action"]

    if action == "open":
        site   = extracted.get("site") or raw_text
        result = open_site(site)
        if result.ok:
            msg = f"✅ Opened {site}"
            return FeatureResult(ok=True, data={"site": site}, display=msg, spoken=f"Opened {site}, boss.")
        err = result.error or "unknown error"
        msg = f"❌ Couldn't open {site} — {err}"
        return FeatureResult(ok=False, data={}, display=msg, spoken=f"Couldn't open that — {err}", error=err)

    if action == "search":
        site   = extracted.get("site")
        query  = extracted.get("query") or raw_text
        result = search_on_site(site, query) if site else general_web_search(query)
        if result.ok:
            msg = f"✅ Searched '{query}'"
            return FeatureResult(
                ok=True, data={"query": query, "site": site},
                display=msg, spoken="Searching that for you now, boss.",
            )
        err = result.error or "unknown error"
        msg = f"❌ Search failed — {err}"
        return FeatureResult(ok=False, data={}, display=msg, spoken=f"Couldn't search that — {err}", error=err)

    if action == "summarize":
        page_data = get_page_text()
        if not page_data.ok:
            err = page_data.error or "no page open"
            msg = f"❌ Couldn't read the page — {err}"
            return FeatureResult(ok=False, data={}, display=msg, spoken=f"Couldn't read that page — {err}", error=err)
        title   = page_data.data.get("title", "this page")
        text    = page_data.data.get("text", "")
        if not text.strip():
            msg = "Page has no readable text."
            return FeatureResult(ok=True, data={"title": title}, display=msg, spoken="Nothing readable on this page boss.")
        prompt  = (f"Summarize this webpage in 2-4 short sentences, spoken style.\n\n"
                   f"Title: {title}\n\nContent:\n{text[:4000]}")
        summary = ask_oneshot(prompt, max_tokens=220, fallback="Couldn't process that page right now.")
        msg = f"📄 {title}\n\n{summary}"
        return FeatureResult(ok=True, data={"title": title, "summary": summary}, display=msg, spoken=summary)

    if action == "compare":
        site_a = extracted.get("site_a")
        site_b = extracted.get("site_b")
        query  = extracted.get("query") or raw_text
        if not site_a or not site_b:
            msg = "I need two sites to compare — try saying both site names."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_sites")
        result  = compare_across_sites(query, site_a, site_b)
        results = result.data.get("results", {})
        a_data  = results.get(site_a, {})
        b_data  = results.get(site_b, {})
        prompt  = (f"Compare these two pages for '{query}'. Give a short 2-4 sentence comparison.\n\n"
                   f"{site_a}:\n{a_data.get('text','failed')[:2000]}\n\n"
                   f"{site_b}:\n{b_data.get('text','failed')[:2000]}")
        summary = ask_oneshot(prompt, max_tokens=220, fallback="Couldn't process that page right now.")
        msg = f"📊 {site_a} vs {site_b}\n\n{summary}"
        return FeatureResult(
            ok=True, data={"site_a": site_a, "site_b": site_b, "summary": summary},
            display=msg, spoken=summary,
        )

    if action == "interact":
        interact_type = extracted.get("interact_type")
        n = extracted.get("n") or 1

        if interact_type == "click_result":
            result = click_result(int(n))
            ok_msg, fail_msg = f"Clicked result {n}", "Couldn't click that result"
        elif interact_type == "scroll_down":
            result = scroll_page("down")
            ok_msg, fail_msg = "Scrolled down", "Couldn't scroll"
        elif interact_type == "scroll_up":
            result = scroll_page("up")
            ok_msg, fail_msg = "Scrolled up", "Couldn't scroll"
        elif interact_type == "back":
            result = go_back()
            ok_msg, fail_msg = "Went back a page", "Couldn't go back"
        elif interact_type == "close_tab":
            result = close_current_tab()
            ok_msg, fail_msg = "Closed the tab", "Couldn't close the tab"
        else:
            msg = "Not sure what to do on the page — try 'click the second result', 'scroll down', 'go back a page', or 'close this tab'."
            return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="unknown_interact_type")

        if result.ok:
            return FeatureResult(ok=True, data=result.data, display=f"✅ {ok_msg}", spoken=ok_msg)
        err = result.error or "unknown error"
        msg = f"❌ {fail_msg} — {err}"
        return FeatureResult(ok=False, data={}, display=msg, spoken=f"{fail_msg} — {err}", error=err)

    msg = "I'm not sure how to do that on the web yet."
    return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="unknown_action")


# ══════════════════════════════════════════
#   INTENT REGISTRY
#   category (from core.router.route) -> handler(user_input, payload) -> (display, spoken)
#   Adding a new Stage 2 intent = one entry in core/router.py's
#   GROQ_INTENTS + one entry here. Nothing else needs to change.
# ══════════════════════════════════════════

INTENT_HANDLERS = {
    "pc":                 handle_pc,
    "screen":             handle_screen,
    "vtop_marks":         handle_marks,
    "vtop_fetch_marks":   handle_vtop_fetch,
    "whatsapp":           handle_whatsapp,
    "web":                handle_web,
    "lms_assignments":    handle_lms_assignments,
    "lms_sync":           handle_lms_sync,
    "attendance":         handle_attendance,
    "bunk_check":         handle_bunk_check,
    "alias_add":          handle_alias_add,
    "alias_merchant":     handle_alias_merchant,
    "remember_fact":      handle_remember_fact,
    "memory_about":       handle_memory_about,
    "forget_last":        handle_forget_last,
    "forget_about":       handle_forget_about,
    "open_dashboard":     handle_open_dashboard,
    "recall_facts":       handle_recall_facts,
    "timetable_today":    handle_timetable_today,
    "timetable_tomorrow": handle_timetable_tomorrow,
    "timetable_week":     handle_timetable_week,
    "next_class":         handle_next_class,
    "class_at_time":      handle_class_at_time,
    "exams":              handle_exams,
    "cgpa":               handle_cgpa,
    "sem_gpa":            handle_sem_gpa,
    "grade_history":      handle_grade_history,
    "grade_target":       handle_grade_target,
    "best_case_cgpa":     handle_best_case_cgpa,
    "cgpa_predict":       handle_cgpa_predict,
    "overall_cgpa_target": handle_overall_cgpa_target,
    "daily_brief":        handle_daily_brief,
    "system_shutdown":    handle_shutdown,
    "spotify":            handle_spotify,
    "task_add":           handle_task_add,
    "task_list":          handle_task_list,
    "task_today":         handle_task_today,
    "task_complete":      handle_task_complete,
    "task_drop":          handle_task_drop,
    "schedule_add":       handle_schedule_add,
    "schedule_today":     handle_schedule_today,
    "schedule_tomorrow":  handle_schedule_tomorrow,
    "schedule_week":      handle_schedule_week,
    "schedule_next":      handle_schedule_next,
    "schedule_free":      handle_schedule_free,
    "schedule_delete":    handle_schedule_delete,
    "focus_start":        handle_focus_start,
    "focus_stop":         handle_focus_stop,
    "focus_status":       handle_focus_status,
    "focus_stats":        handle_focus_stats,
    "expense_add":        handle_expense_add,
    "expense_summary":    handle_expense_summary,
    "expense_search":     handle_expense_search,
}


# ══════════════════════════════════════════
#   PROCESS
# ══════════════════════════════════════════

def process(user_input: str) -> dict:
    """Returns a dict matching CommandResponse's fields."""
    from features.pc_control import has_pending_system_action

    user_input = (user_input or "").strip()
    if not user_input:
        msg = "I'm listening, boss."
        return {"ok": True, "display": msg, "spoken": msg, "data": {}, "error": None,
                "expecting_confirmation": False}

    pc_followup = handle_pc_confirm_followup(user_input)
    if pc_followup is not None:
        return {
            "ok": pc_followup.ok, "display": pc_followup.display, "spoken": pc_followup.spoken,
            "data": pc_followup.data, "error": pc_followup.error,
            "expecting_confirmation": has_pending_system_action(),
        }

    followup = handle_whatsapp_followup(user_input)
    if followup is not None:
        expecting = _pending_whatsapp_confirm["data"] is not None
        return {
            "ok": followup.ok, "display": followup.display, "spoken": followup.spoken,
            "data": followup.data, "error": followup.error, "expecting_confirmation": expecting,
        }

    forget_followup = handle_forget_confirm_followup(user_input)
    if forget_followup is not None:
        return {
            "ok": forget_followup.ok, "display": forget_followup.display, "spoken": forget_followup.spoken,
            "data": forget_followup.data, "error": forget_followup.error, "expecting_confirmation": False,
            "_category": "forget_about", "_payload": {},
        }

    schedule_followup = handle_schedule_confirm_followup(user_input)
    if schedule_followup is not None:
        expecting = _pending_schedule_confirm["data"] is not None
        return {
            "ok": schedule_followup.ok, "display": schedule_followup.display, "spoken": schedule_followup.spoken,
            "data": schedule_followup.data, "error": schedule_followup.error, "expecting_confirmation": expecting,
        }

    category, payload = route(user_input)

    handler = INTENT_HANDLERS.get(category)
    result  = handler(user_input, payload) if handler else None

    if result is None:
        # category == "brain", unregistered, OR a handler (whatsapp/web/pc)
        # decided this wasn't actually its domain — plain chat either way.
        category = "brain"
        reply  = ask_groq(user_input)
        result = FeatureResult(ok=True, data={}, display=reply, spoken=to_spoken(reply))
    else:
        # So a follow-up like "why is it so low?" is answered with this
        # feature's answer in view (ask_groq records its own turns).
        note_feature_exchange(user_input, result.display)

    # H.3: record this turn for cross-turn follow-up inheritance (see
    # core/context.py) — payload is always a dict now (Groq's entities,
    # usually {}), but the isinstance guard stays cheap insurance.
    from core.context import record_turn
    record_turn(user_input, category, payload if isinstance(payload, dict) else {}, result.display)

    expecting = (
        _pending_whatsapp_confirm["data"] is not None
        or _pending_schedule_confirm["data"] is not None
        or _pending_forget["subject"] is not None
        or has_pending_system_action()
    )
    return {
        "ok": result.ok, "display": result.display, "spoken": result.spoken,
        "data": result.data, "error": result.error, "expecting_confirmation": expecting,
        "_category": category, "_payload": payload if isinstance(payload, dict) else {},
    }


# ══════════════════════════════════════════
#   BACKGROUND WORKERS
#   Moved here from jarvis.py — these are backend concerns (VTOP sync,
#   LMS reminders, the daily brief) that should run as long as the
#   server is up, independent of whether a voice client or a future UI
#   is connected.
# ══════════════════════════════════════════

_shutdown = threading.Event()


def _seconds_until_next(hour_minutes, weekday=None):
    """Seconds from now until the soonest upcoming (hour, minute) fire time.
    hour_minutes: list of (hour, minute) tuples, e.g. [(7, 0), (18, 0)].
    weekday: 0=Monday..6=Sunday, or None for every day."""
    now = datetime.now()
    candidates = []
    for days_ahead in range(8):
        day = now + timedelta(days=days_ahead)
        if weekday is not None and day.weekday() != weekday:
            continue
        for h, m in hour_minutes:
            t = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if t > now:
                candidates.append(t)
    return (min(candidates) - now).total_seconds()


def attendance_refresh_worker():
    """H.1: attendance is checked twice daily (7AM + 6PM) regardless of
    whether anyone asks — this is what makes 'what's my attendance'
    answer instantly from cache instead of blocking on a VTOP round-trip."""
    while not _shutdown.is_set():
        _shutdown.wait(_seconds_until_next([(7, 0), (18, 0)]))
        if _shutdown.is_set():
            break
        print("\n🔄 Scheduled attendance refresh...")
        try:
            from features.vtop import fetch_attendance
            result = fetch_attendance()
            if result.get("error"):
                print(f"⚠️ Attendance refresh failed: {result['error']}")
            else:
                print("✅ Attendance refreshed")
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": "attendance"})
        except Exception as e:
            print(f"⚠️ Attendance refresh error: {e}")


def timetable_refresh_worker():
    """H.1: timetable rarely changes mid-semester, but a once-daily
    refresh catches VTOP-side corrections without waiting on a stale
    24h cache to expire mid-query."""
    while not _shutdown.is_set():
        _shutdown.wait(_seconds_until_next([(5, 0)]))
        if _shutdown.is_set():
            break
        print("\n🔄 Scheduled timetable refresh...")
        try:
            from features.vtop import fetch_timetable
            result = fetch_timetable()
            if result.get("error"):
                print(f"⚠️ Timetable refresh failed: {result['error']}")
            else:
                print("✅ Timetable refreshed")
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": "timetable"})
        except Exception as e:
            print(f"⚠️ Timetable refresh error: {e}")


def grades_refresh_worker():
    """H.1: CGPA/grades only move at semester-end — Sunday morning is
    plenty."""
    while not _shutdown.is_set():
        _shutdown.wait(_seconds_until_next([(9, 0)], weekday=6))  # Sunday
        if _shutdown.is_set():
            break
        print("\n🔄 Scheduled grades refresh...")
        try:
            from features.vtop import fetch_grades
            result = fetch_grades()
            if result.get("error"):
                print(f"⚠️ Grades refresh failed: {result['error']}")
            else:
                print("✅ Grades refreshed")
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": "grades"})
        except Exception as e:
            print(f"⚠️ Grades refresh error: {e}")


def startup_staleness_check():
    """H.1: one-shot, runs once at boot. If attendance/timetable/exams are
    empty or already past their freshness window, fetch immediately
    instead of waiting for the first user query (which would otherwise
    eat the VTOP round-trip) or the next scheduled refresh (which could
    be hours away)."""
    checks = [
        ("attendance", "get_attendance_fresh_enough", "fetch_attendance"),
        ("timetable", "get_timetable_fresh_enough", "fetch_timetable"),
        ("exams", "get_exams_fresh_enough", "fetch_exams"),
    ]
    for data_type, fresh_fn_name, fetch_fn_name in checks:
        try:
            import core.memory as memory
            import features.vtop as vtop
            fresh_fn = getattr(memory, fresh_fn_name)
            fetch_fn = getattr(vtop, fetch_fn_name)
            if fresh_fn():
                continue
            print(f"🔄 Startup check: {data_type} is stale/empty, refreshing...")
            result = fetch_fn()
            if result.get("error"):
                print(f"⚠️ Startup {data_type} refresh failed: {result['error']}")
            else:
                print(f"✅ Startup {data_type} refresh complete")
                from core.ws_hub import broadcast
                broadcast({"type": "data_refreshed", "data_type": data_type})
        except Exception as e:
            print(f"⚠️ Startup {data_type} check error: {e}")


def daily_sync_worker():
    while not _shutdown.is_set():
        now       = datetime.now()
        next_sync = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_sync:
            next_sync += timedelta(days=1)
        _shutdown.wait((next_sync - now).total_seconds())
        if _shutdown.is_set():
            break
        print("\n🔄 Running daily VTOP sync...")
        try:
            from features.vtop import fetch_vtop_sem
            from features.vtop_handler.constants import CURRENT_SEM
            fetch_vtop_sem(CURRENT_SEM)
            print("✅ Daily VTOP sync complete")
        except Exception as e:
            print(f"⚠️ Daily VTOP sync error: {e}")


def lms_reminder_worker():
    """Checks every hour — sends WhatsApp reminder at 3-day and 1-day marks."""
    while not _shutdown.is_set():
        try:
            from core.memory import get_pending_lms_assignments, mark_lms_reminded
            from features.lms import send_whatsapp_reminder

            assignments = get_pending_lms_assignments()
            now         = datetime.now()

            for a in assignments:
                if a.get("status") == "submitted":
                    continue
                try:
                    due  = datetime.fromisoformat(a["due_date"])
                    diff = due - now
                    hrs  = diff.total_seconds() / 3600

                    if 60 <= hrs <= 84 and not a.get("reminded_3day"):
                        send_whatsapp_reminder(a, "3day")
                        mark_lms_reminded(a["id"], "3day")

                    elif 12 <= hrs <= 36 and not a.get("reminded_1day"):
                        send_whatsapp_reminder(a, "1day")
                        mark_lms_reminded(a["id"], "1day")

                except Exception as e:
                    print(f"Reminder check error for {a.get('title')}: {e}")

        except Exception as e:
            print(f"LMS reminder worker error: {e}")

        _shutdown.wait(3600)


def daily_brief_worker():
    while not _shutdown.is_set():
        now      = datetime.now()
        next_run = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        _shutdown.wait((next_run - now).total_seconds())
        if _shutdown.is_set():
            break
        print("\n🌅 Sending daily brief...")
        try:
            from features.daily_brief import send_daily_brief
            result = send_daily_brief()
            if result.ok:
                print("✅ Daily brief sent")
            else:
                print(f"⚠️ Daily brief failed: {result.error}")
        except Exception as e:
            print(f"⚠️ Daily brief error: {e}")


def schedule_materialize_worker():
    """Materializes today's + tomorrow's VTOP classes into schedule_blocks
    on startup and every 6 hours — idempotent, see materialize_classes_for_date."""
    while not _shutdown.is_set():
        try:
            from core.memory import materialize_classes_for_date
            today_count = materialize_classes_for_date(datetime.now().date())
            tmrw_count = materialize_classes_for_date((datetime.now() + timedelta(days=1)).date())
            print(f"🔄 Schedule: materialized {today_count} class block(s) today, {tmrw_count} tomorrow")
        except Exception as e:
            print(f"⚠️ Schedule materialize error: {e}")
        _shutdown.wait(6 * 3600)


def semester_rollover_check():
    """One-shot startup check: if CURRENT_SEM (constants.py) has moved on
    since the last run, archive the stale timetable/exam/attendance
    snapshot (see core.memory.reset_semester_data) and pull a fresh one
    for the new semester immediately instead of waiting for the next
    scheduled sync."""
    try:
        from core.memory import check_semester_rollover
        from features.vtop_handler.constants import CURRENT_SEM
        if check_semester_rollover(CURRENT_SEM):
            print(f"🔄 Semester rollover detected — refetching timetable/attendance/exams for {CURRENT_SEM}")
            from features.vtop import fetch_timetable, fetch_attendance, fetch_exams
            fetch_timetable()
            fetch_attendance()
            fetch_exams()
            print("✅ Post-rollover refetch complete")
    except Exception as e:
        print(f"⚠️ Semester rollover check error: {e}")


def startup_checks_worker():
    """Runs once at boot, sequentially: rollover check first (it does its
    own full refetch if the semester changed), then the plain staleness
    check (a no-op for anything the rollover check just refreshed).
    Combined into one thread so the two never race each other into a
    duplicate concurrent VTOP login."""
    semester_rollover_check()
    startup_staleness_check()


def memory_maintenance_worker():
    """Loads the memory graph off the request path at startup (first run
    also migrates old facts/conversations), then every night at 3:30:
    prunes faded links, caps over-connected nodes, and takes a backup
    (7 kept). Runs once at startup too if today has no backup yet."""
    from core.graph import get_graph, BACKUP_DIR
    try:
        graph = get_graph()
        today = f"memory_graph-{datetime.now():%Y%m%d}.db"
        if not os.path.exists(os.path.join(BACKUP_DIR, today)):
            graph.backup()
    except Exception as e:
        print(f"⚠️ Memory startup error: {e}")
    while not _shutdown.is_set():
        _shutdown.wait(_seconds_until_next([(3, 30)]))
        if _shutdown.is_set():
            break
        try:
            graph = get_graph()
            stats = graph.maintain()
            path = graph.backup()
            print(f"🧠 Memory maintenance: {stats}, backup {os.path.basename(path)}")
        except Exception as e:
            print(f"⚠️ Memory maintenance error: {e}")


def _start_background_workers():
    threading.Thread(target=memory_maintenance_worker, daemon=True).start()
    threading.Thread(target=startup_checks_worker, daemon=True).start()
    threading.Thread(target=daily_sync_worker, daemon=True).start()
    threading.Thread(target=lms_reminder_worker, daemon=True).start()
    threading.Thread(target=daily_brief_worker, daemon=True).start()
    threading.Thread(target=schedule_materialize_worker, daemon=True).start()
    threading.Thread(target=attendance_refresh_worker, daemon=True).start()
    threading.Thread(target=timetable_refresh_worker, daemon=True).start()
    threading.Thread(target=grades_refresh_worker, daemon=True).start()
    print("🔄 Background workers started (startup rollover/staleness check, VTOP sync 6AM, "
          "LMS reminders hourly, daily brief 6:30AM, schedule materialize every 6h, "
          "attendance refresh 7AM/6PM, timetable refresh 5AM, grades refresh Sun 9AM)")


# ══════════════════════════════════════════
#   FASTAPI APP
# ══════════════════════════════════════════

app = FastAPI(title="Jarvis Backend")

# The Electron console/widget load from http://localhost:5173 in dev (and
# file:// once packaged) while this API serves 127.0.0.1:8000 — a
# different origin either way, so the browser blocks fetch()/WebSocket
# without explicit CORS. This is a local single-user assistant with no
# public exposure, so allow_origins=["*"] is the right tradeoff here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CommandRequest(BaseModel):
    text: str
    # "voice" when jarvis.py sends it — the result is then also pushed to
    # the frontend over /stream so spoken questions get the same rich,
    # data-backed reply cards as typed ones (the voice path otherwise only
    # ever delivered plain reply text to the UI).
    source: str = "ui"


class CommandResponse(BaseModel):
    ok: bool
    display: str
    spoken: str
    data: dict = {}
    error: str | None = None
    expecting_confirmation: bool = False


def _warm_date_parsing():
    """dateparser loads its language data on first use — that made the
    first "remind me to…" of each session take ~6s."""
    try:
        from features.tasks import _extract_due
        _extract_due("warm up on friday at 5pm")
    except Exception:
        pass


@app.on_event("startup")
def _on_startup():
    import asyncio
    from core import ws_hub
    ws_hub.set_loop(asyncio.get_event_loop())
    _start_background_workers()
    threading.Thread(target=_warm_date_parsing, daemon=True).start()


@app.on_event("shutdown")
def _on_shutdown():
    _shutdown.set()
    try:
        from core import graph as _graph
        if _graph._instance is not None:
            _graph._instance.close()   # flush queued memory writes
    except Exception:
        pass
    try:
        from features.web_control import save_browser_session
        save_browser_session()
    except Exception:
        pass


# Recent turns, so a dashboard opened mid-conversation (e.g. automatically
# after two voice exchanges) shows the conversation instead of a blank page.
_conversation_log = deque(maxlen=40)
_log_lock = threading.Lock()


def _log_turn(text: str, source: str, result: dict):
    with _log_lock:
        _conversation_log.append({
            "at": time.time(), "text": text, "via": "voice" if source == "voice" else "text",
            "ok": result.get("ok"), "display": result.get("display"), "spoken": result.get("spoken"),
            "data": result.get("data") or {},
        })


@app.get("/conversation/recent")
def conversation_recent(minutes: int = 30):
    cutoff = time.time() - minutes * 60
    with _log_lock:
        return {"turns": [t for t in _conversation_log if t["at"] >= cutoff]}


# Reported by run.py from Windows power notifications. The dashboard only
# opens by itself when someone could actually see it.
_device_state = {"lid_open": True, "display_on": True, "on_ac": None, "at": 0.0}


class DeviceState(BaseModel):
    lid_open: bool | None = None
    display_on: bool | None = None
    on_ac: bool | None = None


@app.post("/internal/device_state")
def set_device_state(state: DeviceState):
    for k, v in state.model_dump(exclude_none=True).items():
        _device_state[k] = v
    _device_state["at"] = time.time()
    return _device_state


@app.post("/dashboard/open")
def dashboard_open():
    from core.dashboard import launch
    ok, msg = launch()
    return {"ok": ok, "message": msg}


@app.post("/dashboard/auto_open")
def dashboard_auto_open():
    """Called by the voice process after two exchanges. Never opens the
    dashboard onto a closed lid or a dark screen."""
    from core.dashboard import is_running, launch
    if not (_device_state["lid_open"] and _device_state["display_on"]):
        return {"opened": False, "reason": "lid closed or screen off"}
    if is_running():
        return {"opened": False, "reason": "already open"}
    ok, msg = launch()
    return {"opened": ok, "reason": msg}


# Turns that are ABOUT memory aren't recorded in it: "forget everything
# about X" would otherwise recreate X the moment it was deleted.
_NOT_REMEMBERED = {"forget_about", "forget_last", "memory_about", "recall_facts", "remember_fact",
                   "system_shutdown", "screen"}


def _observe_in_memory(text: str, source: str, result: dict):
    category = result.pop("_category", "")
    payload = result.pop("_payload", {})
    if category in _NOT_REMEMBERED or not (text or "").strip():
        return
    try:
        from core.graph import get_graph
        get_graph().observe(text, category, payload, result, via="voice" if source == "voice" else "text")
    except Exception as e:
        print(f"[memory] couldn't record turn: {e}")


@app.post("/command", response_model=CommandResponse)
def command(req: CommandRequest):
    try:
        result = process(req.text)
        _log_turn(req.text, req.source, result)
        _observe_in_memory(req.text, req.source, result)
        if req.source == "voice":
            from core.ws_hub import broadcast
            broadcast({
                "type": "voice_result", "text": req.text, "ok": result["ok"],
                "display": result["display"], "spoken": result["spoken"], "data": result["data"],
                "expecting_confirmation": result["expecting_confirmation"],
            })
        return CommandResponse(**result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"Something broke handling that — {e}"
        return CommandResponse(ok=False, display=msg, spoken=msg, data={}, error=str(e))


_WHATSAPP_STATUS_TTL_S = 20
_whatsapp_status_cache = {"at": 0.0, "value": {"ready": False, "error": "checking"}, "refreshing": False}

def _refresh_whatsapp_status():
    try:
        from features.whatsapp import get_whatsapp_status
        value = get_whatsapp_status()
    except Exception as e:
        value = {"ready": False, "error": str(e)}
    _whatsapp_status_cache.update(at=time.time(), value=value, refreshing=False)

def _cached_whatsapp_status() -> dict:
    """/health is polled by the console every 30s and by jarvis.py at
    boot. Checking the WhatsApp service inline cost ~2-4s per call when
    it isn't running (Windows retries refused connections), so the check
    runs in the background and /health returns the last known value."""
    if (time.time() - _whatsapp_status_cache["at"] > _WHATSAPP_STATUS_TTL_S
            and not _whatsapp_status_cache["refreshing"]):
        _whatsapp_status_cache["refreshing"] = True
        threading.Thread(target=_refresh_whatsapp_status, daemon=True).start()
    return _whatsapp_status_cache["value"]


@app.get("/health")
def health():
    from config import GROQ_API_KEY

    whatsapp_status = _cached_whatsapp_status()

    try:
        from core.memory import get_marks_synced_sems, get_latest_cgpa_summary
        from features.vtop_handler.constants import CURRENT_SEM
        synced_sems  = get_marks_synced_sems()
        cgpa_summary = get_latest_cgpa_summary()
        vtop_status = {
            "has_data":          bool(synced_sems),
            "current_sem_synced": CURRENT_SEM in synced_sems,
            "last_synced_at":    cgpa_summary.get("synced_at") if cgpa_summary else None,
        }
    except Exception as e:
        vtop_status = {"has_data": False, "error": str(e)}

    return {
        "status": "ok",
        "groq_key_present": bool(GROQ_API_KEY),
        "whatsapp": whatsapp_status,
        "vtop": vtop_status,
    }


class UpiIngestRequest(BaseModel):
    sender: str
    message: str
    received_at: str


@app.get("/tasks")
def get_tasks(within_days: int | None = None):
    """Read-only data feed for the Tasks rack module — bypasses the voice/log pipeline."""
    from features.tasks import list_open
    result = list_open(within_days=within_days)
    return {"tasks": result.data.get("tasks", [])}


@app.get("/schedule")
def get_schedule(date: str = "today"):
    """Read-only data feed for the Schedule rack module."""
    from features.schedule import today as schedule_today, tomorrow as schedule_tomorrow
    result = schedule_tomorrow() if date == "tomorrow" else schedule_today()
    return {"blocks": result.data.get("blocks", []), "active": result.data.get("active")}


@app.get("/focus/status")
def get_focus_status():
    """Read-only data feed for the Focus rack module."""
    from features.focus import status as focus_status
    return focus_status().data


@app.get("/focus/stats")
def get_focus_stats_data(days: int = 7):
    """Read-only data feed for the Focus rack module's per-subject bar chart."""
    from features.focus import stats as focus_stats
    return focus_stats(days=days).data


@app.get("/expenses/summary")
def get_expenses_summary(range: str = "month"):
    """Read-only data feed for the Expenses rack module."""
    from features.expenses import summary as expense_summary
    return expense_summary(range).data


@app.get("/expenses/week_comparison")
def get_expenses_week_comparison():
    """This-week vs last-week totals for the Expenses module's delta strip."""
    from datetime import datetime, timedelta
    from core.memory import get_expenses_between
    now = datetime.now()
    this_week = get_expenses_between(now - timedelta(days=7), now)
    last_week = get_expenses_between(now - timedelta(days=14), now - timedelta(days=7))
    return {
        "this_week_total": sum(e["amount"] for e in this_week),
        "last_week_total": sum(e["amount"] for e in last_week),
    }


@app.get("/attendance")
def get_attendance_data():
    """Read-only data feed for the Attendance rack module."""
    from features.vtop import get_attendance_result
    return get_attendance_result("what is my attendance", entities={}).data


@app.get("/exams")
def get_exams_data():
    """Read-only data feed for the Exam Countdown rack module."""
    from features.vtop import get_exams_result
    return get_exams_result("exams", entities={"when": "all"}).data


@app.get("/assignments")
def get_assignments_data():
    """Read-only data feed for the Assignments rack module. Never blocks
    on a live LMS login — three widgets poll this every minute, and a
    stale or failing LMS used to trigger a full scrape on every poll."""
    from features.lms import get_lms_result
    return get_lms_result("assignments", allow_live_sync=False).data


@app.post("/expense/ingest_upi")
def ingest_upi(req: UpiIngestRequest):
    """
    Called by whatsapp_service/server.js only for messages from senders
    matching its configured UPI_SENDER_PATTERNS — never for arbitrary
    messages. Unparseable text gets logged to data/upi_unparsed.log for
    later template additions (see features/upi_parser.py).
    """
    import os
    from config import EXPENSE_AUTO_INGEST
    from features.upi_parser import parse_upi_message
    from features.expenses import classify
    from core.memory import find_recent_duplicate_expense, add_expense

    parsed = parse_upi_message(req.message)
    if not parsed:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "upi_unparsed.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{req.received_at}\t{req.sender}\t{req.message}\n")
        return {"logged": False, "reason": "unparsed"}

    if not EXPENSE_AUTO_INGEST:
        return {"logged": False, "reason": "auto_ingest_disabled", "parsed": parsed}

    # V.3c: substitute a taught alias for an opaque VPA handle
    # ("P2A-ABC123@ybl" -> "the tea guy") before dedup/category/display
    # ever see it — see the alias_merchant intent for how it's taught.
    from core.memory import get_merchant_alias
    if parsed["merchant"] and "@" in parsed["merchant"]:
        alias = get_merchant_alias(parsed["merchant"])
        if alias:
            parsed["merchant"] = alias

    dup = find_recent_duplicate_expense(parsed["amount"], parsed["merchant"], parsed["spent_at"])
    if dup:
        return {"logged": False, "reason": "duplicate", "expense_id": dup["id"]}

    from features.expenses import _MERCHANT_CATEGORY
    category = classify(parsed["merchant"] or "")
    if category == "other" and parsed["merchant"]:
        category = _MERCHANT_CATEGORY.get(parsed["merchant"].lower(), category)
    expense_id = add_expense(
        parsed["amount"], category=category, merchant=parsed["merchant"],
        source="whatsapp_upi", raw_text=req.message, spent_at=parsed["spent_at"],
    )

    from core.ws_hub import broadcast
    broadcast({"type": "expense_added", "amount": parsed["amount"], "category": category, "auto": True})

    return {"logged": True, "expense_id": expense_id, "amount": parsed["amount"], "category": category}


def _require_debug():
    from config import DEBUG
    if not DEBUG:
        raise HTTPException(status_code=404, detail="Not found")


@app.post("/debug/refresh_timetable")
def debug_refresh_timetable():
    """Force a live VTOP fetch pinned to CURRENT_SEM — bypasses the
    freshness cache. Gated behind DEBUG=true in .env."""
    _require_debug()
    from features.vtop import fetch_timetable
    result = fetch_timetable()
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    from core.memory import get_full_timetable
    return {"ok": True, "timetable": get_full_timetable()}


@app.post("/debug/refresh_exams")
def debug_refresh_exams():
    """Force a live VTOP exam-schedule fetch pinned to CURRENT_SEM.
    Gated behind DEBUG=true in .env."""
    _require_debug()
    from features.vtop import fetch_exams
    result = fetch_exams()
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    from core.memory import get_all_exams
    return {"ok": True, "exams": get_all_exams()}


@app.post("/debug/refresh_attendance")
def debug_refresh_attendance():
    """Force a live VTOP attendance fetch, bypassing the 6h freshness
    cache. Gated behind DEBUG=true in .env."""
    _require_debug()
    from features.vtop import fetch_attendance
    result = fetch_attendance()
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    from core.memory import get_all_attendance
    return {"ok": True, "attendance": get_all_attendance()}


class VoiceEvent(BaseModel):
    type: str
    text: str | None = None
    level: float | None = None


@app.post("/internal/voice_event")
def internal_voice_event(event: VoiceEvent):
    """
    jarvis.py is a separate process — it owns the microphone (wake word,
    STT, TTS; see core/voice.py, core/wakeword.py) and has no direct
    access to this process's WS connections. It POSTs real voice-state
    events here (see core/voice_bridge.py for the sender side); this
    just re-broadcasts them over /stream so the Electron frontend's VU
    meter and teleprinter can reflect real voice activity instead of the
    disconnected mock generator they were driven by before.

    Event types: wake, listening_start, listening_end, processing_start,
    processing_end, speaking_start, speaking_end, transcript (with
    `text`). Local-only, no auth — same trust model as every other
    endpoint here.
    """
    from core.ws_hub import broadcast
    broadcast(event.model_dump(exclude_none=True))
    return {"ok": True}


@app.websocket("/stream")
async def stream_endpoint(websocket: WebSocket):
    """
    Pushes a ping every 10s (keepalive). Also registers with core.ws_hub
    so background code (focus mode's pomodoro ticks, the voice bridge
    above, VTOP refresh workers) can push events here — see core/ws_hub.py
    for the schema:
      {"type":"focus_started", "subject", "planned_minutes", "pomodoro"}
      {"type":"pomodoro_tick", "phase", "remaining_seconds", "session_id"}
      {"type":"pomodoro_transition", "from", "to", "break_seconds"}
      {"type":"focus_ended", "subject", "actual_minutes", "pomodoros"}
      {"type":"wake"|"listening_start"|"listening_end"|"processing_start"|
       "processing_end"|"speaking_start"|"speaking_end"}
      {"type":"transcript", "text": str}
    """
    from core import ws_hub
    await websocket.accept()
    ws_hub.register(websocket)
    try:
        while True:
            await websocket.send_json({"type": "ping"})
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        pass
    finally:
        ws_hub.unregister(websocket)


if __name__ == "__main__":
    import uvicorn
    # access_log=False: every WS heartbeat, voice-event bridge push, and
    # frontend polling GET (health/tasks/schedule/...) was logging its own
    # "INFO: ... 200 OK" line — all normal, expected background traffic,
    # but it drowned out jarvis.py's actual conversation prints in the
    # same terminal. Real errors still surface (FastAPI logs exceptions
    # regardless), just not the routine 200s.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)

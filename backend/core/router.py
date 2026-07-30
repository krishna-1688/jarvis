"""
router.py — two-stage routing for Jarvis.

Stage 1 (cheap, deterministic, no LLM call): keyword fast-path for the
highest-confidence commands — marks queries, PC control, explicit
WhatsApp/web triggers, LMS/assignments. These are unambiguous enough
that a keyword match is faster and just as reliable as asking Groq.

Stage 2 (Groq JSON intent extraction): everything Stage 1 doesn't
recognize. One Groq call classifies the utterance against a fixed
intent list and returns {"intent": ..., "entities": {...},
"confidence": 0.0-1.0}. New intents get their own entry in
GROQ_INTENTS plus a handler entry in jarvis.py's INTENT_HANDLERS
registry — no more router surgery per feature.

Marks queries stay 100% offline (SQLite) by default; VTOP is only
touched when the user explicitly asks to refresh/sync (see
VTOP_FETCH_KEYWORDS below). Theory = course code ends with L (default
when no lab mentioned); Lab = course code ends with P (only when user
says lab/practical).
"""

import os
import re
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL
from features.pc_control import handle_pc_command
from features.expenses import CATEGORY_RULES as _EXPENSE_CATEGORY_RULES

groq_client = Groq(api_key=GROQ_API_KEY)

# ══════════════════════════════════════════
#   STAGE 1 — CHEAP KEYWORD FAST-PATH
# ══════════════════════════════════════════

PC_KEYWORDS = [
    "open", "close", "volume", "mute", "unmute",
    "screenshot", "brightness", "shutdown", "restart",
    "sleep", "find", "search for file"
]

# ── Spotify ───────────────────────────────────────────────
# Checked BEFORE PC Control — "open spotify and play X" contains "open"
# (a PC_KEYWORD), which would otherwise get handed to handle_pc_command
# and tried as a literal desktop-app launch with "spotify and play X"
# as the (garbage) app name. Same class of fix as
# _open_targets_known_website's "open youtube" guard, just routed to a
# real feature instead of just skipped.
_SPOTIFY_PLAY_RE = re.compile(
    r"^(?:open spotify and )?(?:play|put on|start playing)\s+(.+?)(?:\s+on spotify)?[?.!]*$",
    re.IGNORECASE,
)
SPOTIFY_PAUSE_PHRASES = ("pause spotify", "pause the song", "pause the music", "pause music", "stop the music", "stop spotify")
SPOTIFY_RESUME_PHRASES = ("resume spotify", "resume the song", "resume music", "unpause", "continue playing", "continue the song")
SPOTIFY_NEXT_PHRASES = ("next song", "next track", "skip song", "skip track", "skip this song", "skip this track", "play the next song")
SPOTIFY_PREV_PHRASES = ("previous song", "previous track", "last song", "go back a song", "play the last song", "play the previous song")
SPOTIFY_NOWPLAYING_PHRASES = ("what's playing", "whats playing", "what song is this", "what's this song", "whats this song", "current song", "what song is playing", "what is playing")
SPOTIFY_VOLUME_UP_WORDS = ("up", "increase", "raise", "louder", "more")
SPOTIFY_VOLUME_DOWN_WORDS = ("down", "decrease", "lower", "quieter", "less", "reduce")

def detect_spotify_intent(text: str) -> tuple | None:
    """
    Returns ('spotify', {'action': ..., **extra}) for a Spotify
    playback command, or None. 'play <song>' extracts the song name as
    a free-text search query for features.spotify.play_song to resolve
    — this is deliberately loose (no NER), matching this router's
    established style of pushing precise entity extraction into the
    feature layer rather than trying to parse it perfectly here.
    """
    t = text.lower().strip()

    if "spotify" in t and any(w in t for w in ("open", "launch", "start")) and "play" not in t and "pause" not in t:
        return "spotify", {"action": "open"}

    m = _SPOTIFY_PLAY_RE.match(t)
    if m:
        query = m.group(1).strip(" ,.")
        return "spotify", {"action": "play", "query": query}

    if any(p in t for p in SPOTIFY_PAUSE_PHRASES) or t in ("pause", "pause please"):
        return "spotify", {"action": "pause"}

    if any(p in t for p in SPOTIFY_RESUME_PHRASES) or t in ("resume", "resume please"):
        return "spotify", {"action": "resume"}

    if any(p in t for p in SPOTIFY_NEXT_PHRASES) or t in ("skip", "next", "forward", "next please"):
        return "spotify", {"action": "next"}

    if any(p in t for p in SPOTIFY_PREV_PHRASES) or t in ("previous", "back", "go back"):
        return "spotify", {"action": "previous"}

    if any(p in t for p in SPOTIFY_NOWPLAYING_PHRASES):
        return "spotify", {"action": "now_playing"}

    if "spotify" in t and "volume" in t:
        if any(w in t for w in SPOTIFY_VOLUME_UP_WORDS):
            return "spotify", {"action": "volume_up"}
        if any(w in t for w in SPOTIFY_VOLUME_DOWN_WORDS):
            return "spotify", {"action": "volume_down"}

    return None

# Known website/site names that should NEVER be treated as a desktop
# app to launch. Without this, "open youtube" matches PC_KEYWORDS
# (because of bare "open"), gets handed to handle_pc_command, which
# returns a truthy "App not found: youtube" result — that gets
# treated as a successful PC command and web automation never gets
# a chance to run. This guard makes "open <known site>" skip PC
# control entirely and fall through to web automation instead.
KNOWN_WEB_DESTINATIONS = [
    "youtube", "amazon", "flipkart", "google", "gmail", "github",
    "wikipedia", "skyscanner", "reddit", "linkedin", "twitter", "x",
    "vtop", "moodle", "chrome", "browser", "facebook", "instagram",
    "netflix", "spotify web", "whatsapp web"
]

def _open_targets_known_website(text: str) -> bool:
    """True if this is an 'open X' command where X is a known website."""
    t = text.lower()
    if "open" not in t:
        return False
    return any(site in t for site in KNOWN_WEB_DESTINATIONS)


# WhatsApp — cheap pre-filter only. Full extraction (recipient +
# message) is done by Groq in core/whatsapp_intent.py because regex
# cannot reliably split "send a message to Amma good night" into
# name vs content — there's no fixed boundary.
WHATSAPP_TRIGGER_WORDS = [
    "whatsapp", "message", "msg", "text ", "tell ", "send a message",
    "send message"
]

def looks_like_whatsapp_request(text: str) -> bool:
    """Cheap check — does this MIGHT be a WhatsApp send request."""
    t = text.lower()
    return any(w in t for w in WHATSAPP_TRIGGER_WORDS)


# Web automation — cheap pre-filter only. Full extraction (action/
# site/query) is done by Groq in core/web_intent.py — same reasoning
# as WhatsApp: phrasing like "compare X on amazon and flipkart" has no
# fixed regex-friendly boundary.
WEB_TRIGGER_WORDS = [
    "open", "go to", "search", "look up", "find", "compare",
    "summarize", "summarise", "browse", "google",
    "what does this page say", "what are the reviews"
]

# Words that overlap with web triggers but mean something else
# entirely in this project (avoid false positives stealing the route
# from PC control / other features).
WEB_FALSE_POSITIVE_GUARDS = [
    "find my file", "search my files", "open the file", "open file",
    "find a file", "open settings", "open task manager"
]

def looks_like_web_request(text: str) -> bool:
    """Cheap check — might this be a web browsing request."""
    t = text.lower()
    if any(g in t for g in WEB_FALSE_POSITIVE_GUARDS):
        return False
    return any(w in t for w in WEB_TRIGGER_WORDS)


# PC/web/whatsapp trigger words ("open", "go to", "tell ") are cheap
# and fast, but they're just single-word signals — a sentence that
# ALSO clearly asks a VTOP/academic data question (e.g. "go to vtop
# and check my attendance", "tell me my overall attendance") should
# not get hijacked into "open a browser" or "send a message" just
# because of that one incidental word. When both fire, the data
# question wins and the whole utterance defers to Stage 2's Groq
# classification, which actually reads the full sentence.
#
# This must also cover LMS/assignment questions (LMS_KEYWORDS, defined
# below) — not just VTOP ones. Confirmed bug: "can tell my pending
# assignmets after sync from lms" (typo breaks the "assignment"
# substring match) contains "tell " -> looks_like_whatsapp_request()
# fires, and with no LMS signal word in this list either, nothing
# stopped it from being hijacked into a WhatsApp-send attempt instead
# of ever reaching detect_lms_query() at the bottom of fast_path_route.
VTOP_DATA_SIGNAL_WORDS = [
    "attendance", "bunk", "skip class", "timetable", "time table",
    "class schedule", "next class", "free at", "exam", "cgpa", "gpa",
    "grade", "marks", "mark ", "assignment", "cat1", "cat2",
    "cat-1", "cat-2", "cat 1", "cat 2", "fat",
]

def _looks_like_data_question(text: str) -> bool:
    t = text.lower()
    if any(k in t for k in VTOP_DATA_SIGNAL_WORDS):
        return True
    # LMS_KEYWORDS is defined further down this file — safe to reference
    # here since Python resolves module globals at call time, not at
    # function-definition time, and this is only ever called well after
    # the whole module has finished importing.
    return any(k in t for k in LMS_KEYWORDS)


# Marks detection keywords
MARKS_KEYWORDS = [
    "marks", "mark", "scored", "score", "cat1", "cat2", "cat 1", "cat 2",
    "cat-1", "cat-2", "fat", "final assessment", "continuous assessment",
    "what did i get", "how much did i score", "how did i do",
    "my result", "internal marks", "semester marks", "assessment",
    "assignment mark", "digital assignment", "quiz", "periodic assessment",
    "consolidated", "lab mark", "lab assessment"
]

# "CAT1"/"CAT2"/"FAT" alone are ambiguous — "CAT-2 schedule" means exam
# dates, "what do I need in CAT2 for an S" means grade_target, neither
# is a marks lookup. Only treat a bare CAT/FAT mention as a marks
# signal if none of these exam/need-oriented words are also present;
# an explicit marks word (marks/mark/scored/score/assessment/etc.)
# always wins regardless.
_CAT_FAT_ONLY_KEYWORDS = ["cat1", "cat2", "cat 1", "cat 2", "cat-1", "cat-2", "fat"]
_CAT_FAT_NON_MARKS_GUARDS = ["schedule", "exam", "when", "need", "require"]

# Bare "score"/"scored" is similarly ambiguous — "how much should I
# score to get a 9 CGPA overall" is a CGPA-target question, not a
# marks lookup. "how much did I score" (the full phrase, already a
# separate MARKS_KEYWORDS entry) stays an unambiguous marks signal
# regardless — only the bare word needs this guard. "cgp" (no 'a') is
# included since STT sometimes transcribes "CGPA" that way.
_SCORE_ONLY_KEYWORDS = ["scored", "score"]
_SCORE_NON_MARKS_GUARDS = ["cgpa", "cgp", "gpa", "overall"]

def has_marks_keyword(text: str) -> bool:
    t = text.lower()
    ambiguous      = _CAT_FAT_ONLY_KEYWORDS + _SCORE_ONLY_KEYWORDS
    other_keywords = [k for k in MARKS_KEYWORDS if k not in ambiguous]
    if any(k in t for k in other_keywords):
        return True
    if any(k in t for k in _CAT_FAT_ONLY_KEYWORDS) and not any(g in t for g in _CAT_FAT_NON_MARKS_GUARDS):
        return True
    if any(k in t for k in _SCORE_ONLY_KEYWORDS) and not any(g in t for g in _SCORE_NON_MARKS_GUARDS):
        return True
    return False

# On-demand VTOP fetch triggers
VTOP_FETCH_KEYWORDS = [
    "check in vtop", "from vtop", "in vtop", "vtop check",
    "check vtop", "directly from vtop", "fetch from vtop",
    "refresh marks", "sync marks", "update marks", "reload marks",
    "fetch marks", "get latest marks", "refresh vtop", "sync vtop",
    "update vtop", "reload vtop", "live marks", "latest marks",
    "check my marks in vtop", "get from vtop"
]

def is_vtop_fetch_request(text: str) -> bool:
    """Returns True if user explicitly wants live data from VTOP."""
    t = text.lower()
    return any(k in t for k in VTOP_FETCH_KEYWORDS)


# LMS / assignments
LMS_KEYWORDS = [
    "assignment", "assignments", "pending assignment", "lms",
    "moodle", "what's due", "what is due", "due today",
    "due tomorrow", "submit", "submission", "not submitted",
    "pending submission", "any assignment", "my assignment",
    "assignment deadline", "deadline"
]

LMS_SYNC_KEYWORDS = [
    "sync lms", "refresh lms", "update lms", "check lms",
    "fetch assignments", "reload assignments", "latest assignments"
]

def detect_lms_query(text: str) -> str | None:
    """Returns 'assignments' | 'sync' | None"""
    t = text.lower()
    if any(k in t for k in LMS_SYNC_KEYWORDS):
        return "sync"
    if any(k in t for k in LMS_KEYWORDS):
        return "assignments"
    return None


# Tasks — "things to do" (title + due date + done state), distinct from
# schedule blocks ("time on the clock", see schedule keywords below).
TASK_ADD_TRIGGERS = ["remind me to", "add a task", "add task", "new task"]
TASK_LIST_PHRASES = [
    "what are my tasks", "my tasks", "what's on my plate", "whats on my plate",
    "my task list", "open tasks", "list my tasks", "show my tasks", "pending tasks",
]
TASK_TODAY_PHRASES = [
    "tasks today", "my tasks today", "tasks due today", "what tasks are due",
]
TASK_COMPLETE_VERBS = ["complete", "finished", "finish"]
TASK_DROP_VERBS     = ["drop", "remove", "cancel", "delete"]
_TASK_FILLER_WORDS  = {"the", "a", "an", "my", "task", "please"}

def _mark_as_done_query(text: str) -> str | None:
    """Matches 'mark X as done/complete/finished' — X needs no 'task' word."""
    t = text.lower()
    for suffix in [" as done", " as complete", " as finished"]:
        idx = t.find(suffix)
        if idx != -1:
            before = text[:idx]
            if before.lower().startswith("mark "):
                before = before[len("mark "):]
            return before.strip(" ,.")
    return None

def _verb_based_query(text: str, verbs: list, marker_word: str) -> str | None:
    """
    Matches a sentence containing one of `verbs` plus `marker_word`
    somewhere ("drop the DBMS task", "remove the gym block") — the
    name can sit anywhere relative to the verb, unlike a fixed phrase.
    """
    words   = text.split()
    lowered = [w.lower().strip(",.?!") for w in words]
    if not any(v in lowered for v in verbs) or marker_word not in lowered:
        return None
    kept = [w for w, lw in zip(words, lowered) if lw not in verbs and lw not in _TASK_FILLER_WORDS and lw != marker_word]
    return " ".join(kept).strip(" ,.")

def detect_task_intent(text: str) -> tuple | None:
    """Returns (category, payload) for a task-related utterance, or None."""
    t = text.lower()

    if any(p in t for p in TASK_TODAY_PHRASES):
        return "task_today", {}

    if any(p in t for p in TASK_LIST_PHRASES):
        within_days = 7 if ("this week" in t or "next 7 days" in t) else None
        return "task_list", {"raw_text": text, "within_days": within_days}

    if any(trig in t for trig in TASK_ADD_TRIGGERS):
        return "task_add", {"raw_text": text}

    mark_query = _mark_as_done_query(text)
    if mark_query is not None:
        return "task_complete", {"query": mark_query or text}

    complete_query = _verb_based_query(text, TASK_COMPLETE_VERBS, "task")
    if complete_query is not None:
        return "task_complete", {"query": complete_query or text}

    drop_query = _verb_based_query(text, TASK_DROP_VERBS, "task")
    if drop_query is not None:
        return "task_drop", {"query": drop_query or text}

    return None


# ── Schedule ("time on the clock") ───────────────────────
SCHEDULE_TODAY_PHRASES = [
    "schedule today", "my schedule today", "what's my schedule", "whats my schedule",
    "today's schedule",
]
SCHEDULE_TOMORROW_PHRASES = ["schedule tomorrow", "tomorrow's schedule"]
SCHEDULE_WEEK_PHRASES = ["schedule this week", "my schedule this week", "week's schedule", "schedule for the week"]
SCHEDULE_NEXT_PHRASES = ["what's next", "whats next", "what is next", "what am i doing next", "what do i have next"]
SCHEDULE_FREE_GENERAL_PHRASES = ["when am i free", "when will i be free"]
SCHEDULE_FREE_AT_MARKER = "am i free at"
SCHEDULE_DELETE_VERBS = ["remove", "delete", "cancel"]

def detect_schedule_intent(text: str) -> tuple | None:
    """Returns (category, payload) for a schedule-related utterance, or None."""
    t = text.lower()

    if any(p in t for p in SCHEDULE_WEEK_PHRASES):
        return "schedule_week", {}
    if any(p in t for p in SCHEDULE_TOMORROW_PHRASES):
        return "schedule_tomorrow", {}
    if any(p in t for p in SCHEDULE_TODAY_PHRASES):
        return "schedule_today", {}
    if any(p in t for p in SCHEDULE_NEXT_PHRASES):
        return "schedule_next", {}
    if SCHEDULE_FREE_AT_MARKER in t:
        return "schedule_free", {"raw_text": text, "mode": "at_time"}
    if any(p in t for p in SCHEDULE_FREE_GENERAL_PHRASES):
        return "schedule_free", {"raw_text": text, "mode": "gaps"}

    delete_query = _verb_based_query(text, SCHEDULE_DELETE_VERBS, "block")
    if delete_query is not None:
        return "schedule_delete", {"query": delete_query or text}

    # "block 3 to 5 for TOC study" / "schedule 9 to 11 am for placement prep"
    if ("block" in t and " to " in t) or ("schedule" in t and " to " in t and "for" in t):
        return "schedule_add", {"raw_text": text}

    return None


# ── Focus mode ────────────────────────────────────────────
FOCUS_START_TRIGGERS = ["focus mode", "start focus", "start pomodoro", "focus on", "pomodoro "]
FOCUS_STOP_PHRASES = ["stop focus", "stop focusing", "end focus", "end my focus", "exit focus"]
FOCUS_STATUS_PHRASES = ["focus status", "am i focusing", "am i in focus"]
FOCUS_STATS_PHRASES = [
    "how many hours did i study", "how much did i study", "how long did i study",
    "study stats", "focus stats",
]

def detect_focus_intent(text: str) -> tuple | None:
    """Returns (category, payload) for a focus-mode utterance, or None."""
    t = text.lower()

    if any(p in t for p in FOCUS_STATS_PHRASES):
        return "focus_stats", {}
    if any(p in t for p in FOCUS_STATUS_PHRASES):
        return "focus_status", {}
    if any(p in t for p in FOCUS_STOP_PHRASES):
        return "focus_stop", {}
    if any(trig in t for trig in FOCUS_START_TRIGGERS):
        return "focus_start", {"raw_text": text}

    return None


# ── Expenses ──────────────────────────────────────────────
EXPENSE_ADD_VERBS = ["spent", "paid", "spend"]
EXPENSE_SUMMARY_PHRASES = [
    "how much did i spend", "how much have i spent", "how much on",
    "what did i spend", "spending this week", "spending today",
    "spending this month", "expense summary",
]
_EXPENSE_BARE_AMOUNT_LEAD_RE = re.compile(r'^\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s+(for|on)\b', re.IGNORECASE)
_ALL_EXPENSE_KEYWORDS = [kw for kws in _EXPENSE_CATEGORY_RULES.values() for kw in kws]

def detect_expense_intent(text: str) -> tuple | None:
    """Returns (category, payload) for an expense-related utterance, or None."""
    t = text.lower()

    if any(p in t for p in EXPENSE_SUMMARY_PHRASES):
        return "expense_summary", {"raw_text": text}

    if not re.search(r'\d', t):
        return None  # everything below needs a number

    if any(verb in t for verb in EXPENSE_ADD_VERBS):
        return "expense_add", {"raw_text": text}
    if any(kw in t for kw in _ALL_EXPENSE_KEYWORDS):
        return "expense_add", {"raw_text": text}
    if _EXPENSE_BARE_AMOUNT_LEAD_RE.match(text):
        return "expense_add", {"raw_text": text}

    return None


# ── Course aliases (H.2) — teaching Jarvis a course nickname ──────────
# Both patterns require an explicit leading trigger word ("remember"/
# "note"/"call") so they never misfire on an unrelated sentence that
# happens to contain "means" or "call" mid-sentence.
_ALIAS_MEANS_RE = re.compile(
    r"^(?:remember|note)(?:\s+that)?\s+(.+?)\s+(?:means|is|stands for)\s+(.+)$",
    re.IGNORECASE
)
_ALIAS_CALL_RE = re.compile(
    r"^call\s+(.+?)\s+(?:as\s+)?(.+)$",
    re.IGNORECASE
)

def detect_alias_add_intent(text: str) -> tuple | None:
    """
    Returns ('alias_add', {'alias':..., 'course_query':...}) for a
    course-nickname-teaching utterance, or None.
    'remember DAA means Design and Analysis of Algorithms' -> alias
    ("DAA") comes first, course reference second.
    'call BCSE307P compiler lab' -> course reference first, new alias
    ("compiler lab") second.
    """
    stripped = text.strip()

    m = _ALIAS_MEANS_RE.match(stripped)
    if m:
        return "alias_add", {"alias": m.group(1).strip(), "course_query": m.group(2).strip()}

    m = _ALIAS_CALL_RE.match(stripped)
    if m:
        return "alias_add", {"course_query": m.group(1).strip(), "alias": m.group(2).strip()}

    return None


# ── Merchant aliases (V.3c) — naming an opaque UPI VPA ────────────────
# Always refers to the most recent unaliased opaque-VPA expense (see
# core.memory.get_last_opaque_expense) — nobody names the VPA itself out
# loud, so the utterance never contains it. Requires the literal word
# "vpa" so this never collides with detect_alias_add_intent's "call X Y"
# course-alias pattern above.
_ALIAS_MERCHANT_RE = re.compile(
    r"^(?:remember\s+)?(?:that\s+)?(?:call\s+that\s+)?(?:opaque\s+)?vpa\s+(?:is|means)?\s*(.+)$",
    re.IGNORECASE
)

def detect_alias_merchant_intent(text: str) -> tuple | None:
    """'that opaque vpa is the tea guy' / 'remember vpa means gym
    membership' -> ('alias_merchant', {'friendly_name': ...}), or None."""
    stripped = text.strip()
    if "vpa" not in stripped.lower():
        return None
    m = _ALIAS_MERCHANT_RE.match(stripped)
    if m:
        name = m.group(1).strip()
        if name:
            return "alias_merchant", {"friendly_name": name}
    return None


# Daily brief — simple, unambiguous trigger, no entities needed.
DAILY_BRIEF_KEYWORDS = [
    "daily brief", "morning brief", "give me my brief", "give me the brief",
    "my brief", "brief me", "today's brief", "give me the rundown"
]

def is_daily_brief_request(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in DAILY_BRIEF_KEYWORDS)


# Shutdown — a voice/text keyword to cleanly stop the backend server
# (see server.py's handle_shutdown), independent of the Electron
# console's own Ctrl+Shift+Q shortcut (which just quits the app window).
# "stop" / "shut down" alone are deliberately excluded — too easy to
# collide with an unrelated sentence ("shut down the timer").
SHUTDOWN_KEYWORDS = [
    "shutdown jarvis", "shut down jarvis", "turn off jarvis",
    "power off jarvis", "exit jarvis", "jarvis go offline",
    "jarvis shut down", "kill jarvis", "quit jarvis",
]

def is_shutdown_request(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in SHUTDOWN_KEYWORDS)


# Exact-match only (see detect_simple_data_query's own comment on why).
# Each of these maps to an intent that either takes NO entities at all
# (next_class, timetable_today, cgpa) or, for attendance/exams, to an
# intent whose handler re-derives course/semester straight from the raw
# text anyway (features/vtop.py's get_attendance_result /
# get_exams_result both call core.router.detect_semester /
# normalize_course_query themselves) — so an empty {} here costs nothing
# even in the attendance/exams cases, as long as the phrase itself never
# names a course, which the exact-match set below guarantees.
_SIMPLE_DATA_PHRASES = {
    "attendance":        ("attendance", {}),
    "my attendance":     ("attendance", {}),
    "whats my attendance": ("attendance", {}),
    "what's my attendance": ("attendance", {}),
    "what is my attendance": ("attendance", {}),
    "hows my attendance": ("attendance", {}),
    "how's my attendance": ("attendance", {}),
    "check my attendance": ("attendance", {}),
    "overall attendance": ("attendance", {}),
    "cgpa":              ("cgpa", {}),
    "my cgpa":           ("cgpa", {}),
    "whats my cgpa":     ("cgpa", {}),
    "what's my cgpa":    ("cgpa", {}),
    "what is my cgpa":   ("cgpa", {}),
    "check my cgpa":     ("cgpa", {}),
    "whats my next class": ("next_class", {}),
    "what's my next class": ("next_class", {}),
    "what is my next class": ("next_class", {}),
    "whens my next class": ("next_class", {}),
    "when's my next class": ("next_class", {}),
    # Note: "schedule today" / "today's schedule" / "whats due today" are
    # deliberately NOT listed here — detect_schedule_intent / LMS's
    # detect_lms_query already catch those earlier in fast_path_route
    # (routing to the richer schedule_today / lms intents respectively),
    # so an entry here would just be unreachable dead code.
    "todays schedule":   ("timetable_today", {}),
    "classes today":     ("timetable_today", {}),
    "what classes today": ("timetable_today", {}),
}

def detect_simple_data_query(text: str) -> tuple | None:
    """
    A small allowlist of extremely common, entity-free data questions,
    matched EXACTLY (not by substring) after trimming trailing
    punctuation — "what's my attendance" qualifies, "what's my
    attendance in DBMS" does not, because substring matching here would
    silently swallow the course name into an empty {} entities dict.
    Saves a full Groq round-trip (Stage 2) for the single most common
    phrasing of each of these, without touching anything more specific.
    """
    t = text.lower().strip().rstrip("?.!")
    hit = _SIMPLE_DATA_PHRASES.get(t)
    return hit if hit else None


def fast_path_route(text: str) -> tuple | None:
    """
    Stage 1. Returns (category, payload) for a confident keyword match,
    or None if nothing matched (caller falls through to Stage 2).
    """
    t = text.lower()

    # CRITICAL SAFETY CHECK — must run before PC Control below.
    # Confirmed incident: "shutdown jarvis" matched PC_KEYWORDS' bare
    # "shutdown" substring first and triggered features.pc_control's
    # REAL os.system("shutdown /s /t 10") — an actual Windows shutdown,
    # not closing the app. This check intercepts anything that mentions
    # both jarvis and shutdown/restart/exit BEFORE PC control ever sees
    # it, so that can never happen again regardless of phrasing.
    if is_shutdown_request(t):
        return "system_shutdown", {}

    # Spotify — checked before PC Control; see detect_spotify_intent's
    # own comment for why ("open spotify and play X" contains "open").
    spotify_match = detect_spotify_intent(text)
    if spotify_match:
        return spotify_match

    # PC Control — but skip it entirely for "open <known website>" so
    # those fall through to web automation instead of hitting the
    # desktop app launcher and getting a false "App not found" success.
    if any(keyword in t for keyword in PC_KEYWORDS) and not _open_targets_known_website(text):
        result = handle_pc_command(t)
        if result:
            return "pc", result

    # Tasks — checked right after PC control (so "cancel the shutdown"
    # still resolves as a PC command, not a task-drop attempt) but before
    # everything else: "mark" collides with MARKS_KEYWORDS, and "submit"/
    # "assignment" collide with LMS_KEYWORDS, so task triggers — being
    # far more specific — must win first.
    task_match = detect_task_intent(text)
    if task_match:
        return task_match

    schedule_match = detect_schedule_intent(text)
    if schedule_match:
        return schedule_match

    focus_match = detect_focus_intent(text)
    if focus_match:
        return focus_match

    expense_match = detect_expense_intent(text)
    if expense_match:
        return expense_match

    alias_merchant_match = detect_alias_merchant_intent(text)
    if alias_merchant_match:
        return alias_merchant_match

    alias_match = detect_alias_add_intent(text)
    if alias_match:
        return alias_match

    is_data_question = _looks_like_data_question(text)

    # WhatsApp message — real extraction happens in jarvis.py via Groq.
    # Skipped if the sentence also clearly asks a VTOP data question —
    # see VTOP_DATA_SIGNAL_WORDS above.
    if looks_like_whatsapp_request(text) and not is_data_question:
        return "whatsapp", {"raw_text": text}

    # Web automation — real extraction happens in jarvis.py via Groq.
    # Same data-question exception as WhatsApp above.
    if (looks_like_web_request(text) or _open_targets_known_website(text)) and not is_data_question:
        return "web", {"raw_text": text}

    # On-demand VTOP fetch (user explicitly wants live data)
    if is_vtop_fetch_request(t) and has_marks_keyword(t):
        intent = extract_marks_intent(t)
        return "vtop_fetch_marks", intent

    # Marks — offline
    if has_marks_keyword(t):
        intent = extract_marks_intent(t)
        return "vtop_marks", intent

    # LMS / assignments
    lms_type = detect_lms_query(t)
    if lms_type:
        return "lms", lms_type

    # (Shutdown is checked at the very top of this function now — see
    # the CRITICAL SAFETY CHECK comment above — since it must run before
    # PC Control, not after LMS.)

    # Daily brief — manual trigger
    if is_daily_brief_request(t):
        return "daily_brief", {}

    # A handful of extremely common, entity-free data questions — every
    # one of these previously cost a full Groq round-trip (~0.3-1s) for
    # Stage 2 to classify even though the phrasing is completely
    # unambiguous. EXACT match only (not substring) is deliberate: "what's
    # my attendance in DBMS" must NOT hit this path and lose its course
    # entity to an empty {} — only bare phrasings with nothing else to
    # extract are safe to shortcut here. Anything even slightly more
    # complex correctly falls through to Stage 2's real entity extraction.
    simple_match = detect_simple_data_query(text)
    if simple_match:
        return simple_match

    return None


# ══════════════════════════════════════════
#   MARKS ENTITY EXTRACTION
#   (used by fast_path_route once it decides
#   a query is marks-related — this is
#   argument parsing, not routing itself)
# ══════════════════════════════════════════

SEMESTER_MAP = [
    (["sem 1", "semester 1", "first sem", "1st sem", "sem1",
      "semester1", "first semester"], "CH20242501"),
    (["sem 2", "semester 2", "second sem", "2nd sem", "sem2",
      "semester2", "second semester"], "CH20242505"),
    (["sem 3", "semester 3", "third sem", "3rd sem", "sem3",
      "semester3", "third semester"], "CH20252601"),
    (["sem 4", "semester 4", "fourth sem", "4th sem", "sem4",
      "semester4", "fourth semester"], "CH20252605"),
    (["sem 5", "semester 5", "fifth sem", "5th sem", "sem5",
      "semester5", "fifth semester", "current sem", "this sem",
      "current semester", "this semester"], "CH20262701"),
]

def detect_semester(text: str) -> str | None:
    t = text.lower()
    for keywords, sem_id in SEMESTER_MAP:
        if any(k in t for k in keywords):
            return sem_id
    if any(k in t for k in ["all sem", "all semester", "all sems",
                              "every sem", "all semesters"]):
        return "all"
    return None

ASSESSMENT_PATTERNS = [
    (["cat1", "cat 1", "cat-1", "cat i", "continuous assessment test 1",
      "continuous assessment test i", "cat one", "first cat"],
     "Continuous Assessment Test - I"),
    (["cat2", "cat 2", "cat-2", "cat ii", "continuous assessment test 2",
      "continuous assessment test ii", "cat two", "second cat"],
     "Continuous Assessment Test - II"),
    (["fat", "final assessment test", "final assessment",
      "final exam", "final test", "end sem", "end semester",
      "semester exam", "fat marks"],
     "Final Assessment Test"),
    (["assignment 1", "assignment1", "assignment-1", "assignment i",
      "first assignment", "da1", "da 1", "digital assignment 1",
      "digital assignment i"],
     "Assignment - I"),
    (["assignment 2", "assignment2", "assignment-2", "assignment ii",
      "second assignment", "da2", "da 2", "digital assignment 2",
      "digital assignment ii"],
     "Assignment - II"),
    (["assignment 3", "assignment3", "assignment-3", "assignment iii",
      "third assignment", "da3", "da 3", "digital assignment 3",
      "digital assignment iii"],
     "Assignment - III"),
    (["assessment 1", "assessment1", "assessment-1", "assessment i",
      "first assessment"],
     "Assessment - 1"),
    (["assessment 2", "assessment2", "assessment-2", "assessment ii",
      "second assessment"],
     "Assessment - 2"),
    (["assessment 3", "assessment3", "assessment-3", "assessment iii",
      "third assessment"],
     "Assessment - 3"),
    (["quiz", "quiz 1", "quiz-1", "quiz i"],
     "Quiz"),
    (["pat1", "pat 1", "periodic assessment 1", "periodic test 1"],
     "Periodic Assessment Test - 1"),
    (["pat2", "pat 2", "periodic assessment 2", "periodic test 2"],
     "Periodic Assessment Test - 2"),
    (["pat3", "pat 3", "periodic assessment 3"],
     "Periodic Assessment Test - 3"),
    (["pat4", "pat 4", "periodic assessment 4"],
     "Periodic Assessment Test - 4"),
    (["cat5", "cat 5", "consolidated 5", "consolidated assessment 5"],
     "Consolidated Assessment  Test- 5"),
    (["cat6", "cat 6", "consolidated 6", "consolidated assessment 6"],
     "Consolidated Assessment  Test- 6"),
]

def normalize_assessment(text: str) -> str | None:
    t = text.lower()
    for triggers, db_val in ASSESSMENT_PATTERNS:
        if any(tr in t for tr in triggers):
            return db_val
    return None

SUBJECT_MAP = [
    # Sem 1
    (["python", "bcse101e", "cp python", "computer programming python"],
     "Python"),
    (["intro to engineering", "bcse101n", "introduction to engineering"],
     "Introduction to Engineering"),
    (["beee", "beee102", "electrical", "electronics engineering",
      "basic electrical"],
     "BEEE"),
    (["technical english", "beng101", "english communication", "english comm"],
     "Technical English"),
    (["calculus", "bmat101", "calc"],
     "Calculus"),
    (["physics", "bphy101", "engineering physics"],
     "Physics"),
    (["qualitative skills 1", "bsts201", "qualitative skills practice",
      "soft skill 1", "quant 1 sem1"],
     "Qualitative Skills Practice"),
    # Sem 2
    (["chemistry", "bchy101", "engineering chemistry", "chem"],
     "Chemistry"),
    (["oops", "oop", "bcse102", "object oriented", "structured",
      "c programming", "cpp"],
     "Structured and Object"),
    (["digital systems", "bece102", "dsd", "digital design"],
     "Digital Systems"),
    (["technical report", "beng102", "report writing"],
     "Technical Report"),
    (["ethics", "bhum101", "values"],
     "Ethics"),
    (["differential equations", "bmat102", "det", "transforms", "diff eq"],
     "Differential Equations"),
    (["quantitative skills 1", "bsts101"],
     "Quantitative Skills Practice I"),
    # Sem 3
    (["environmental", "bchy102", "env science", "evs"],
     "Environmental"),
    (["java", "bcse103", "computer programming java", "cp java"],
     "Java"),
    (["data structures", "dsa", "bcse202", "ds algo"],
     "Data Structures"),
    (["computer architecture", "bcse205", "cao", "architecture",
      "organization"],
     "Computer Architecture"),
    (["operating system", "os", "bcse303"],
     "Operating Systems"),
    (["microprocessor", "bece204", "micro", "microcontroller"],
     "Microprocessors"),
    (["spanish", "besp101"],
     "Spanish"),
    (["complex variables", "bmat201", "linear algebra", "cvla"],
     "Complex Variables"),
    (["qualitative skills 2", "bsts202"],
     "Qualitative Skills Practice II"),
    # Sem 4
    (["web programming", "bcse203", "web prog", "web dev"],
     "Web Programming"),
    (["data science", "bcse206", "fds", "foundations of data"],
     "Foundations of Data"),
    (["database", "dbms", "bcse302", "db systems", "sql"],
     "Database"),
    (["theory of computation", "toc", "bcse304", "automata"],
     "Theory of Computation"),
    (["computer networks", "cn", "bcse308", "networks"],
     "Computer Networks"),
    (["discrete mathematics", "dmgt", "discrete maths", "bmat205",
      "graph theory", "discrete math"],
     "Discrete Mathematics"),
    (["essence", "bssc101", "traditional knowledge"],
     "Essence"),
    (["quantitative skills 2", "bsts102"],
     "Quantitative Skills Practice II"),
]

def detect_subject(text: str) -> str | None:
    """
    Resolves a spoken course reference to a search term. Tries the
    dynamic, auto-populated course_aliases resolver first (see
    core.course_resolver) — it knows every course actually in the
    student's synced timetable/attendance, so a current-semester course
    like "daa" (Design and Analysis of Algorithms, added mid-degree)
    resolves correctly without anyone having to hand-edit SUBJECT_MAP.
    Falls back to the static SUBJECT_MAP (still useful for older
    completed-semester courses/shorthand not necessarily worth a DB
    round-trip) and finally to no match.
    """
    from core.course_resolver import resolve_course_best
    best = resolve_course_best(text)
    if best:
        return best["course_name"]

    t = text.lower()
    for triggers, search_term in SUBJECT_MAP:
        if any(tr in t for tr in triggers):
            return search_term
    return None

def normalize_course_query(course_query: str) -> str:
    """
    Resolves a spoken course name/abbreviation (e.g. "DBMS", "TOC", "CN")
    to the same search term the marks feature uses (via SUBJECT_MAP),
    since a raw substring match against course_name/course_code won't
    catch abbreviations ("dbms" isn't a substring of "Database Systems"
    or "BCSE302L"). Falls back to the original text if nothing matches,
    so a direct course code/title fragment still works unchanged.
    """
    if not course_query:
        return course_query
    return detect_subject(course_query) or course_query

def detect_course_filter(text: str) -> str:
    """
    Returns 'lab' if user explicitly mentions lab/practical.
    Returns 'theory' by default — always show theory unless lab asked.
    """
    t = text.lower()
    if any(w in t for w in ["lab", "laboratory", "practical", "lab marks",
                              "lab assessment", "lab mark"]):
        return "lab"
    return "theory"

def extract_marks_intent(text: str) -> dict:
    t = text.lower()

    subject       = detect_subject(t)
    assessment    = normalize_assessment(t)
    semester      = detect_semester(t)
    course_filter = detect_course_filter(t)
    want_all      = any(w in t for w in [
        "all marks", "all my marks", "full marks", "entire marks",
        "every mark", "all subjects", "everything", "complete marks"
    ])

    # Determine query type
    if want_all and not semester:
        query_type = "all"
    elif want_all and semester:
        query_type = "semester"
    elif semester and not subject and not assessment:
        query_type = "semester"
    elif semester and assessment and not subject:
        query_type = "assessment_in_sem"
    elif subject and assessment:
        query_type = "specific"
    elif subject and semester:
        query_type = "subject_in_sem"
    elif subject:
        query_type = "subject"
    elif assessment and semester:
        query_type = "assessment_in_sem"
    elif assessment:
        query_type = "assessment_all"
    else:
        query_type = "current_sem"

    # Semester-only queries → show ALL (theory + lab)
    # Assessment/subject queries → theory only (unless lab explicitly asked)
    if query_type in ("semester", "all", "current_sem") and course_filter != "lab":
        course_filter = "all"

    return {
        "subject":       subject,
        "assessment":    assessment,
        "semester":      semester,
        "want_all":      want_all,
        "query_type":    query_type,
        "course_filter": course_filter,
    }


# ══════════════════════════════════════════
#   STAGE 2 — GROQ JSON INTENT EXTRACTION
#   Only called when Stage 1 finds no match.
# ══════════════════════════════════════════

GROQ_INTENTS = {
    "attendance":       "attendance percentage, how many classes attended/missed, debarment risk",
    "bunk_check":       "can I skip/bunk a class, how many classes can I miss and stay above 75%, which subject is safest to skip",
    "timetable_today":  "what classes are today, today's schedule",
    "timetable_tomorrow": "what classes are tomorrow, tomorrow's schedule",
    "timetable_week":   "the full week's class schedule",
    "next_class":       "what's my next class, when's my next class",
    "class_at_time":    "whether a specific course meets on a specific day (e.g. 'do I have TOC on friday'), or whether a time slot is free (e.g. 'am I free at 3pm')",
    "exams":            "exam dates — CAT/FAT schedule, when the next exam is, days until an exam",
    "cgpa":             "overall CGPA",
    "sem_gpa":          "GPA for one specific semester",
    "grade_history":    "grade in a specific course, or the full grade history across all courses",
    "cgpa_predict":     "what CGPA would result if a specific course got a specific grade",
    "grade_target":     "what mark is needed in a specific course/assessment to get a specific grade",
    "best_case_cgpa":   "best-case overall CGPA if every current-semester course gets the same grade (e.g. all S, all A)",
    "overall_cgpa_target": "what average grade/score is needed this semester to reach a specific target overall CGPA (e.g. 'how much do I need to score to get a 9 CGPA')",
    "task_add":         "adding a new personal task/to-do/reminder to do something, not a class assignment (e.g. 'remind me to submit fees by friday', 'add a task to call mom')",
    "task_list":        "listing open personal tasks/to-dos ('what's on my plate', 'what are my tasks')",
    "task_today":       "personal tasks due today or overdue (not class assignments)",
    "task_complete":    "marking a personal task as done/complete/finished",
    "task_drop":        "dropping/removing/cancelling a personal task",
    "schedule_add":     "blocking/scheduling a chunk of time for an activity (e.g. 'block 3 to 5 for TOC study')",
    "schedule_today":   "the full schedule for today — classes plus any custom blocks",
    "schedule_tomorrow": "the full schedule for tomorrow",
    "schedule_week":    "the full schedule for the week",
    "schedule_next":    "what's next on the schedule right now (class or custom block, not just class)",
    "schedule_free":    "when there's free time in the schedule, or whether a specific time is free",
    "schedule_delete":  "removing/cancelling a scheduled block (not a class)",
    "focus_start":      "starting a focus/pomodoro session on a subject for some duration",
    "focus_stop":       "stopping/ending the current focus session",
    "focus_status":     "whether a focus session is currently active",
    "focus_stats":      "how much time was spent studying/focusing recently",
    "expense_add":      "logging a purchase/expense with an amount (e.g. 'spent 180 on lunch')",
    "expense_summary":  "total spending or spending breakdown over a period (today/week/month)",
    "expense_search":   "searching past expenses by merchant/description",
    "alias_add":        "teaching a course nickname/alias, e.g. 'remember DAA means Design and Analysis of Algorithms', 'call BCSE307P compiler lab'",
    "alias_merchant":   "naming an opaque UPI VPA/handle from a recent expense, e.g. 'that opaque vpa is the tea guy'",
    "system_shutdown":  "explicitly telling Jarvis to shut down / turn off / exit / go offline — not just ending the current conversation",
    "spotify":          "any Spotify playback control — play a specific song/artist, open Spotify, pause, resume, skip/next/previous track, volume, or what's currently playing",
    "chat":             "general conversation, questions, or anything else not covered above",
}

EXTRACTION_PROMPT = """You classify a voice assistant command into exactly ONE intent.

The input may be a messy, run-on, or compound sentence with filler
phrasing ("go check", "sync from vtop", "please", "can you", "and
tell me"). Ignore the filler and action-y wrapper words — find the
actual DATA QUESTION being asked (attendance, timetable, exam, grade,
CGPA, etc.) and classify by THAT, even if the sentence also mentions
"vtop", "sync", "go to", or "tell me". The real question always wins
over incidental phrasing.

Available intents:
{intent_list}

Recent conversation (oldest first, most recent last) — use this ONLY to
resolve a follow-up that doesn't name its own course/subject/day (e.g.
"what about that one", "and my marks", "same for tomorrow"). If the
current input already names its own course/day/time, ignore this
history entirely and use what's actually in the current input:
{recent_turns}

If the current input is a follow-up with no course/subject of its own
but a recent turn above named one, reuse that course in entities.course.

If the message names a specific course/subject (e.g. "DBMS", "TOC",
"calculus"), include it as entities.course, exactly as the user said it.
For bunk_check, if a relative day is mentioned ("tomorrow", "today",
"friday"), also include entities.days_ahead as a small integer (today=0,
tomorrow=1, etc.) — omit it if no day is mentioned. For class_at_time,
include entities.day (a weekday name, "today", or "tomorrow") and/or
entities.time (as said, e.g. "3 PM") depending on what the user asked.
For exams, include entities.when as one of "next", "all", "cat1",
"cat2", "fat" (default "all" if not specified). For grade_target,
include entities.target_grade as a single letter (S/A/B/C/D/E/F). For
cgpa_predict, include entities.grade as a single letter — this intent
REQUIRES a specific named course; if no course is named, it's
best_case_cgpa instead (see below), not cgpa_predict. For
best_case_cgpa, include entities.grade as a single letter (default S
if not specified, e.g. "if I get A in everything" -> grade "A"). This
is the intent for a UNIFORM hypothetical across the WHOLE semester
with no specific course named ("if I score/average/get N this sem",
"what if I ace everything") — if the user gives a bare NUMBER instead
of a letter, convert it to the matching letter on this 10-point scale
(S=10, A=9, B=8, C=7, D=6, E=5, F=0), e.g. "score 9" -> grade "A",
"average 8" -> grade "B". For overall_cgpa_target, include
entities.target_cgpa as a number (e.g. "get a 9 CGPA" -> 9, "get a 9
cgp overall" -> 9 — "cgp" is sometimes a mis-transcription of "CGPA").
The key difference between best_case_cgpa and overall_cgpa_target:
best_case_cgpa is given a grade/score and asks what CGPA results;
overall_cgpa_target is given a target CGPA and asks what grade/score
is needed. For alias_add, include entities.alias (the nickname being
taught) and entities.course_query (the course reference it maps to).
For spotify, include entities.action as one of "play", "open", "pause",
"resume", "next", "previous", "volume_up", "volume_down", "now_playing"
— and for "play", also entities.query with the song/artist as said
(e.g. "play believer by imagine dragons" -> action "play", query
"believer by imagine dragons"). Otherwise entities should be {{}}.

Respond with ONLY valid JSON, no markdown, no explanation.
Format: {{"intent": "<name>", "entities": {{"course": "...", "day": "...", "time": "...", "days_ahead": 0, "when": "...", "target_grade": "...", "grade": "...", "target_cgpa": 0, "action": "...", "query": "..."}}, "confidence": <0.0-1.0>}}

Examples:
Input: "what's my attendance"
Output: {{"intent": "attendance", "entities": {{}}, "confidence": 1.0}}

Input: "attendance in DBMS"
Output: {{"intent": "attendance", "entities": {{"course": "DBMS"}}, "confidence": 1.0}}

Input: "which subject has lowest attendance"
Output: {{"intent": "attendance", "entities": {{}}, "confidence": 1.0}}

Input: "can I skip DBMS tomorrow"
Output: {{"intent": "bunk_check", "entities": {{"course": "DBMS", "days_ahead": 1}}, "confidence": 1.0}}

Input: "how many classes can I skip in TOC"
Output: {{"intent": "bunk_check", "entities": {{"course": "TOC"}}, "confidence": 1.0}}

Input: "which subject am I safest to bunk in"
Output: {{"intent": "bunk_check", "entities": {{}}, "confidence": 1.0}}

Input: "can I skip tomorrow"
Output: {{"intent": "bunk_check", "entities": {{"days_ahead": 1}}, "confidence": 1.0}}

Input: "which class should I skip today"
Output: {{"intent": "bunk_check", "entities": {{"days_ahead": 0}}, "confidence": 1.0}}

Input: "what's my next class"
Output: {{"intent": "next_class", "entities": {{}}, "confidence": 1.0}}

Input: "schedule today"
Output: {{"intent": "timetable_today", "entities": {{}}, "confidence": 1.0}}

Input: "classes tomorrow"
Output: {{"intent": "timetable_tomorrow", "entities": {{}}, "confidence": 1.0}}

Input: "what does my week look like"
Output: {{"intent": "timetable_week", "entities": {{}}, "confidence": 1.0}}

Input: "do I have TOC on friday"
Output: {{"intent": "class_at_time", "entities": {{"course": "TOC", "day": "friday"}}, "confidence": 1.0}}

Input: "am I free at 3 PM"
Output: {{"intent": "class_at_time", "entities": {{"time": "3 PM"}}, "confidence": 1.0}}

Input: "when's my next exam"
Output: {{"intent": "exams", "entities": {{"when": "next"}}, "confidence": 1.0}}

Input: "CAT-2 schedule"
Output: {{"intent": "exams", "entities": {{"when": "cat2"}}, "confidence": 1.0}}

Input: "how many days till FAT"
Output: {{"intent": "exams", "entities": {{"when": "fat"}}, "confidence": 1.0}}

Input: "exams this week"
Output: {{"intent": "exams", "entities": {{"when": "all"}}, "confidence": 1.0}}

Input: "what's my cgpa"
Output: {{"intent": "cgpa", "entities": {{}}, "confidence": 1.0}}

Input: "gpa in sem 3"
Output: {{"intent": "sem_gpa", "entities": {{}}, "confidence": 1.0}}

Input: "grade in DBMS"
Output: {{"intent": "grade_history", "entities": {{"course": "DBMS"}}, "confidence": 1.0}}

Input: "what CAT2 do I need in TOC for an S"
Output: {{"intent": "grade_target", "entities": {{"course": "TOC", "target_grade": "S"}}, "confidence": 1.0}}

Input: "if I get A in everything, what's my CGPA"
Output: {{"intent": "best_case_cgpa", "entities": {{"grade": "A"}}, "confidence": 1.0}}

Input: "what will my cgpa be if i score 9 in this sem"
Output: {{"intent": "best_case_cgpa", "entities": {{"grade": "A"}}, "confidence": 1.0}}

Input: "what's my cgpa if i average 8 this semester"
Output: {{"intent": "best_case_cgpa", "entities": {{"grade": "B"}}, "confidence": 1.0}}

Input: "what's my CGPA if I get A in DBMS"
Output: {{"intent": "cgpa_predict", "entities": {{"course": "DBMS", "grade": "A"}}, "confidence": 1.0}}

Input: "how much should I score to get a 9 CGPA overall"
Output: {{"intent": "overall_cgpa_target", "entities": {{"target_cgpa": 9}}, "confidence": 1.0}}

Input: "what do I need to get a 9 cgp overall"
Output: {{"intent": "overall_cgpa_target", "entities": {{"target_cgpa": 9}}, "confidence": 1.0}}

Input: "remember DAA means Design and Analysis of Algorithms"
Output: {{"intent": "alias_add", "entities": {{"alias": "DAA", "course_query": "Design and Analysis of Algorithms"}}, "confidence": 1.0}}

Input: "can you put on blinding lights by the weeknd"
Output: {{"intent": "spotify", "entities": {{"action": "play", "query": "blinding lights by the weeknd"}}, "confidence": 1.0}}

Input: "skip this one"
Output: {{"intent": "spotify", "entities": {{"action": "next"}}, "confidence": 1.0}}

Follow-up inheritance example — given this recent conversation:
  Turn: user said "what's my attendance in DAA" -> intent=attendance, entities={{"course": "DAA"}}
Input: "what about my marks"
Output: {{"intent": "grade_history", "entities": {{"course": "DAA"}}, "confidence": 1.0}}

Compound/messy phrasing — classify by the real question, ignore the filler:

Input: "go to vtop and check my attendance"
Output: {{"intent": "attendance", "entities": {{}}, "confidence": 1.0}}

Input: "sync from vtop and tell me my overall attendance"
Output: {{"intent": "attendance", "entities": {{}}, "confidence": 1.0}}

Input: "can you go check vtop for my timetable today"
Output: {{"intent": "timetable_today", "entities": {{}}, "confidence": 1.0}}

Input: "please sync vtop and tell me when my next exam is"
Output: {{"intent": "exams", "entities": {{"when": "next"}}, "confidence": 1.0}}

If nothing fits well, use: {{"intent": "chat", "entities": {{}}, "confidence": 1.0}}

Now classify this input:
Input: "{user_input}"
Output:"""


def extract_general_intent_groq(text: str) -> dict:
    """
    Stage 2. Classifies against GROQ_INTENTS. Falls back to
    {"intent": "chat"} on any Groq/parsing failure so a hiccup here
    never blocks a reply — the brain can still just answer
    conversationally.
    """
    from core.context import get_recent_turns

    intent_list = "\n".join(f'  - "{k}": {v}' for k, v in GROQ_INTENTS.items())
    recent = get_recent_turns(3)
    recent_turns = (
        "\n".join(
            f'  Turn: user said "{t["user_text"]}" -> intent={t["intent"]}, entities={t["entities"]}'
            for t in recent
        ) if recent else "  (no recent turns)"
    )
    prompt = EXTRACTION_PROMPT.format(intent_list=intent_list, user_input=text, recent_turns=recent_turns)

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").replace("json", "", 1).strip()

        data = json.loads(raw)
        if data.get("intent") not in GROQ_INTENTS:
            data["intent"] = "chat"
        data.setdefault("entities", {})
        data.setdefault("confidence", 0.5)
        return data
    except Exception as e:
        print(f"[router] Stage 2 intent extraction failed: {e}")
        return {"intent": "chat", "entities": {}, "confidence": 0.0}


# ══════════════════════════════════════════
#   MAIN ROUTE
# ══════════════════════════════════════════

# H.3: a deterministic backstop for course-entity inheritance that
# doesn't depend on the Stage 2 prompt's own (best-effort) follow-up
# handling noticing it — "what about my marks" right after an
# attendance-in-DAA question should still carry course=DAA even if the
# LLM missed it. Only fires when the current utterance doesn't name its
# own course AND looks like it's referring back to something.
_FOLLOWUP_MARKERS = (
    "what about", "how about", "and what about", "and for", "same for",
    "what's the", "whats the", "and my", "and in", "what abt",
)

def _looks_like_followup(text: str) -> bool:
    t = text.lower().strip()
    return any(t.startswith(m) or f" {m} " in f" {t} " for m in _FOLLOWUP_MARKERS)


# H.5: when Stage 2 comes back as "chat" with low confidence (<0.6), it's
# often not really a chat message — it's a data question phrased in a
# way the classifier didn't recognize. Before dropping into general
# conversation, check for two cheap, strong signals it might have missed.
LOW_CONFIDENCE_THRESHOLD = 0.6

_TIME_WORDS = (
    "today", "tomorrow", "tonight", "this week", "next week",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_TIME_PATTERN_RE = re.compile(r"\b\d{1,2}\s?(am|pm)\b", re.IGNORECASE)

def _has_time_word(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _TIME_WORDS) or bool(_TIME_PATTERN_RE.search(t))


# Broader than Stage 1's keyword checks on purpose — this only runs once
# Stage 2 has ALREADY come back low-confidence/failed (e.g. Groq rate
# limited or genuinely down, confirmed to happen), so a looser substring
# match here is a reasonable last resort rather than dropping straight to
# a generic clarify menu. Deliberately covers topics that are Stage-2-only
# intents (never in fast_path_route's own keyword checks) — attendance,
# cgpa, exams, timetable — since those get zero Stage 1 coverage
# otherwise. Every target handler re-derives course/semester straight
# from raw text itself (core.router.detect_semester /
# normalize_course_query), so an empty {} entities dict costs nothing.
_KEYWORD_DATA_FALLBACKS = [
    (("attendance", "bunk", "skip class", "safe to skip"), "attendance"),
    (("cgpa", "grade point average"), "cgpa"),
    (("exam", "cat1", "cat2", "cat-1", "cat-2", "cat 1", "cat 2", "fat"), "exams"),
    (("timetable", "time table", "class schedule", "next class"), "timetable_today"),
]

def _keyword_data_fallback(text: str) -> tuple | None:
    t = text.lower()
    for keywords, category in _KEYWORD_DATA_FALLBACKS:
        if any(k in t for k in keywords):
            return category, {}
    return None


def _low_confidence_fallback(text: str) -> tuple:
    """
    Returns (category, payload) — never None. Tries the course resolver
    (a named course strongly suggests a data question, not chat), then a
    broader data-keyword check, then a day/time word (suggests a
    schedule question), and only falls back to a short numbered clarify
    menu if none of those signals fire — better than silently guessing
    or dead-ending on "I don't understand."
    """
    from core.course_resolver import resolve_course_best

    course_match = resolve_course_best(text)
    if course_match:
        return "attendance", {"course": course_match["course_name"]}

    keyword_match = _keyword_data_fallback(text)
    if keyword_match:
        return keyword_match

    if _has_time_word(text):
        return "schedule_today", {}

    return "clarify", {"raw_text": text}


def route(text: str) -> tuple:
    """
    Returns (category, payload). `category` is looked up directly in
    jarvis.py's INTENT_HANDLERS registry — adding a new Stage 2 intent
    means adding one GROQ_INTENTS entry here and one registry entry in
    jarvis.py, nothing else.
    """
    fast_result = fast_path_route(text)
    if fast_result:
        category, payload = fast_result
    else:
        classified = extract_general_intent_groq(text)
        intent     = classified.get("intent", "chat")
        confidence = classified.get("confidence", 1.0)

        if intent == "chat":
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                category, payload = _low_confidence_fallback(text)
            else:
                return "brain", None
        else:
            category, payload = intent, classified.get("entities", {})

    # Entity inheritance only makes sense for a dict-shaped payload —
    # "pc"'s payload is already a built FeatureResult (see handle_pc in
    # server.py), not entities.
    if isinstance(payload, dict) and not payload.get("course") and _looks_like_followup(text):
        from core.context import get_last_entity
        inherited = get_last_entity("course")
        if inherited:
            payload = {**payload, "course": inherited}

    return category, payload

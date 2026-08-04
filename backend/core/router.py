"""
router.py — two-stage routing for Jarvis.

Stage 1 (fast_path_route) handles the high-frequency stuff — PC control,
Spotify, web, WhatsApp, schedule, tasks, focus, expenses, marks, LMS,
aliases, shutdown safety — via deterministic keyword/regex detection,
each in its own function, checked in a specific order (see the inline
comments) to avoid one feature's trigger word swallowing a sentence
meant for another. Trigger words use fuzzy matching (_fuzzy_any) where
it's safe to, so a typo like "atendance" or "spotfy" still matches —
see _fuzzy_any's own docstring for exactly when that applies and when
it deliberately doesn't (anything safety-critical stays exact-only).

Stage 2 (Groq JSON intent extraction, extract_general_intent_groq) is
the fallback for anything Stage 1 doesn't recognize — genuinely
ambiguous phrasing, or a combination nobody's specifically coded for.
One call classifies against the full GROQ_INTENTS list and returns
{"intent": ..., "entities": {...}, "confidence": 0.0-1.0}, using a
small/fast model (GROQ_CLASSIFIER_MODEL).

Why Stage 1 does this much instead of just calling Groq for everything:
confirmed live that Groq's free tier caps the classifier model at 6000
tokens/MINUTE — even a tightly trimmed prompt only allows ~3 calls a
minute before 429s start, nowhere near enough for normal conversation.
Stage 1 catching the common cases for free (and instantly) is what
keeps the app usable within that budget; Stage 2 only has to carry the
long tail. New Stage-2-only intents get one entry in GROQ_INTENTS plus
a handler entry in server.py's INTENT_HANDLERS registry.

Marks queries stay 100% offline (SQLite) by default; VTOP is only
touched when the user explicitly asks to refresh/sync (vtop_fetch_marks
intent). Theory = course code ends with L (default when no lab
mentioned); Lab = course code ends with P (only when user says
lab/practical).
"""

import os
import re
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from groq import Groq
from rapidfuzz import fuzz
from config import GROQ_API_KEY, GROQ_CLASSIFIER_MODEL
from features.expenses import CATEGORY_RULES as _EXPENSE_CATEGORY_RULES


def _fuzzy_any(text: str, keywords, threshold: int = 85) -> bool:
    """Typo-tolerant version of `any(k in text for k in keywords)` — e.g.
    "atendance" or "spotfy" still matches "attendance"/"spotify". Exact
    substring match is tried first (cheap, catches the overwhelming
    majority of cases with zero fuzzy overhead); only single-WORD
    keywords of 5+ letters fall through to a per-word fuzzy comparison.
    Multi-word phrases are deliberately exact-only — fuzzy-matching a
    whole phrase risks matching an unrelated combination of words rather
    than a genuine typo of ONE word — and short keywords (under 5
    letters, e.g. "cat", "fat", "msg") are excluded too, since a small
    edit distance relative to a short word is far more likely to
    collide with a different, unrelated short word than to represent an
    actual typo. Never used for anything safety-critical (shutdown/
    cancel-shutdown detection stays exact-match only, on purpose)."""
    t = text.lower()
    if any(k in t for k in keywords):
        return True
    single_word_keywords = [k for k in keywords if " " not in k and len(k) >= 5]
    if not single_word_keywords:
        return False
    words = re.findall(r"[a-z']+", t)
    for w in words:
        for kw in single_word_keywords:
            if abs(len(w) - len(kw)) <= 2 and fuzz.ratio(w, kw) >= threshold:
                return True
    return False

# max_retries=0, timeout=6 — confirmed live this classifier call was
# occasionally taking 20+ SECONDS: the Groq SDK defaults to max_retries=2
# with exponential backoff, so a single rate-limited (429) call silently
# retried twice before finally failing. Since classification already has
# a graceful fallback on any failure (extract_general_intent_groq's
# except clause), failing in ~6s beats hanging for 20+ trying again.
groq_client = Groq(api_key=GROQ_API_KEY, max_retries=0, timeout=6.0)

# ══════════════════════════════════════════
#   STAGE 1 — MINIMAL FAST-PATH (safety + zero-cost exact matches only)
# ══════════════════════════════════════════


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


# Cancelling a pending PC shutdown/restart is just as safety-critical as
# the shutdown-request check above — if a real os.system("shutdown /s
# ...") is already armed and counting down, "cancel shutdown" MUST
# reach features.pc_control.cancel_shutdown() unconditionally. Confirmed
# live during this migration: Stage 2's classifier (even with an
# explicit example) sometimes misread "cancel shutdown" as
# system_shutdown instead of pc — which would exit the Jarvis process
# but do NOTHING to the already-scheduled OS shutdown, since that's a
# separate OS-level timer independent of this process. A network call
# to an LLM must never be the only thing standing between "the PC is
# about to shut down" and stopping it — this is a deterministic,
# zero-latency, zero-dependency guarantee instead.
CANCEL_SHUTDOWN_PHRASES = ("cancel shutdown", "cancel the shutdown", "cancel restart", "cancel the restart")

def is_cancel_shutdown_request(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in CANCEL_SHUTDOWN_PHRASES)


# ══════════════════════════════════════════
#   RESTORED FAST-PATH (see module docstring: "why Stage 1 came back")
# ══════════════════════════════════════════
# High-frequency actions get deterministic keyword detection again,
# each with its OWN dedicated (cheap or zero-cost) path, instead of
# every single message paying for the big Stage 2 classifier call.
# Confirmed live: Groq's free tier caps llama-3.1-8b-instant at 6000
# tokens per MINUTE -- even a trimmed ~1800-token classifier prompt only
# allows ~3 calls/minute before 429s start, nowhere near enough for
# normal back-and-forth use. Cutting how OFTEN Stage 2 is needed at all
# is the only real fix; a smaller prompt alone can't get there. Every
# specific bug found and fixed during tonight's Groq-only experiment
# (spotify "stop playing", the whatsapp "tell X" pattern, class_at_time
# day-only handling, the shutdown/cancel-shutdown safety guards) is
# folded back in here to stay fixed.

from features.pc_control import handle_pc_command

PC_KEYWORDS = [
    "open", "close", "volume", "mute", "unmute",
    "screenshot", "brightness", "shutdown", "shut down", "restart",
    "sleep", "find", "search for file",
    "battery", "cpu", "memory", "disk", "system status", "system info",
    "clipboard", "copy", "copied", "loud",
    "window", "running processes", "what's running", "whats running",
    "list processes",
]

# -- Spotify ---------------------------------------------------
# Checked BEFORE PC Control -- "open spotify and play X" contains "open"
# (a PC_KEYWORD), which would otherwise get handed to handle_pc_command
# and tried as a literal desktop-app launch with "spotify and play X"
# as the (garbage) app name.
_SPOTIFY_PLAY_RE = re.compile(
    r"^(?:open spotify and )?(?:play|put on|start playing)\s+(.+?)(?:\s+on spotify)?[?.!]*$",
    re.IGNORECASE,
)
SPOTIFY_PAUSE_PHRASES = ("pause spotify", "pause the song", "pause the music", "pause music",
                          "stop the music", "stop spotify", "stop playing", "stop the song")
SPOTIFY_RESUME_PHRASES = ("resume spotify", "resume the song", "resume music", "unpause", "continue playing", "continue the song")
SPOTIFY_NEXT_PHRASES = ("next song", "next track", "skip song", "skip track", "skip this song", "skip this track", "play the next song")
SPOTIFY_PREV_PHRASES = ("previous song", "previous track", "last song", "go back a song", "play the last song", "play the previous song")
SPOTIFY_NOWPLAYING_PHRASES = ("what's playing", "whats playing", "what song is this", "what's this song", "whats this song", "current song", "what song is playing", "what is playing")
SPOTIFY_VOLUME_UP_WORDS = ("up", "increase", "raise", "louder", "more")
SPOTIFY_VOLUME_DOWN_WORDS = ("down", "decrease", "lower", "quieter", "less", "reduce")

def _normalize_spotify_typos(t: str) -> str:
    """Rewrites a fuzzy-matched typo of 'spotify' (e.g. 'spotifi',
    'spotfy') to the canonical spelling, so _SPOTIFY_PLAY_RE's exact
    "open spotify and " prefix still matches. Doing this once up front
    (rather than trying to make the regex itself fuzzy) keeps every
    other exact-string check in this function correct for free."""
    words = t.split()
    for i, w in enumerate(words):
        core = w.strip(",.?!")
        if core != "spotify" and len(core) >= 5 and fuzz.ratio(core, "spotify") >= 85:
            words[i] = w.replace(core, "spotify")
    return " ".join(words)

def detect_spotify_intent(text: str) -> tuple | None:
    """Returns ('spotify', {'action': ..., **extra}) for a Spotify
    playback command, or None."""
    t = _normalize_spotify_typos(text.lower().strip())

    is_spotify_word = "spotify" in t

    if is_spotify_word and any(q in t for q in ("web player", "in the browser", "on the web", "spotify web")):
        return None

    if is_spotify_word and any(w in t for w in ("open", "launch", "start")) and "play" not in t and "pause" not in t:
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

    if is_spotify_word and "volume" in t:
        if any(w in t for w in SPOTIFY_VOLUME_UP_WORDS):
            return "spotify", {"action": "volume_up"}
        if any(w in t for w in SPOTIFY_VOLUME_DOWN_WORDS):
            return "spotify", {"action": "volume_down"}

    return None

# Known website/site names that should NEVER be treated as a desktop
# app to launch -- "open youtube" contains "open" (a PC_KEYWORD).
KNOWN_WEB_DESTINATIONS = [
    "youtube", "amazon", "flipkart", "google", "gmail", "github",
    "wikipedia", "skyscanner", "reddit", "linkedin", "twitter", "x",
    "vtop", "moodle", "chrome", "browser", "facebook", "instagram",
    "netflix", "spotify web", "whatsapp web"
]

def _open_targets_known_website(text: str) -> bool:
    t = text.lower()
    if "open" not in t:
        return False
    return any(site in t for site in KNOWN_WEB_DESTINATIONS)


WHATSAPP_TRIGGER_WORDS = [
    "whatsapp", "message", "msg", "text ", "tell ", "send a message",
    "send message"
]

def looks_like_whatsapp_request(text: str) -> bool:
    return _fuzzy_any(text, WHATSAPP_TRIGGER_WORDS)


WEB_TRIGGER_WORDS = [
    "open", "go to", "search", "look up", "find", "compare",
    "summarize", "summarise", "browse", "google",
    "what does this page say", "what are the reviews",
    "click", "scroll", "close tab", "close this tab", "close the tab",
    "go back a page", "previous page", "back a page",
]

WEB_FALSE_POSITIVE_GUARDS = [
    "find my file", "search my files", "open the file", "open file",
    "find a file", "open settings", "open task manager"
]

def looks_like_web_request(text: str) -> bool:
    t = text.lower()
    if any(g in t for g in WEB_FALSE_POSITIVE_GUARDS):
        return False
    return _fuzzy_any(text, WEB_TRIGGER_WORDS)


# A sentence that ALSO clearly asks a VTOP/academic data question (e.g.
# "tell me my overall attendance") should not get hijacked into "send a
# message" just because it contains "tell ". Covers LMS too.
VTOP_DATA_SIGNAL_WORDS = [
    "attendance", "bunk", "skip class", "timetable", "time table",
    "class schedule", "next class", "free at", "exam", "cgpa", "gpa",
    "grade", "marks", "mark ", "assignment", "cat1", "cat2",
    "cat-1", "cat-2", "cat 1", "cat 2", "fat",
]

def _looks_like_data_question(text: str) -> bool:
    if _fuzzy_any(text, VTOP_DATA_SIGNAL_WORDS):
        return True
    return _fuzzy_any(text, LMS_KEYWORDS)


MARKS_KEYWORDS = [
    "marks", "mark", "scored", "score", "cat1", "cat2", "cat 1", "cat 2",
    "cat-1", "cat-2", "fat", "final assessment", "continuous assessment",
    "what did i get", "how much did i score", "how did i do",
    "my result", "internal marks", "semester marks", "assessment",
    "assignment mark", "digital assignment", "quiz", "periodic assessment",
    "consolidated", "lab mark", "lab assessment"
]

_CAT_FAT_ONLY_KEYWORDS = ["cat1", "cat2", "cat 1", "cat 2", "cat-1", "cat-2", "fat"]
_CAT_FAT_NON_MARKS_GUARDS = ["schedule", "exam", "when", "need", "require"]
_SCORE_ONLY_KEYWORDS = ["scored", "score"]
_SCORE_NON_MARKS_GUARDS = ["cgpa", "cgp", "gpa", "overall"]

def has_marks_keyword(text: str) -> bool:
    t = text.lower()
    ambiguous      = _CAT_FAT_ONLY_KEYWORDS + _SCORE_ONLY_KEYWORDS
    other_keywords = [k for k in MARKS_KEYWORDS if k not in ambiguous]
    # Fuzzy here (typo tolerance), but NOT for the ambiguous CAT/FAT/score
    # keywords below — those are short and deliberately exact-guarded
    # against colliding with exam-schedule/CGPA questions.
    if _fuzzy_any(text, other_keywords):
        return True
    if any(k in t for k in _CAT_FAT_ONLY_KEYWORDS) and not any(g in t for g in _CAT_FAT_NON_MARKS_GUARDS):
        return True
    if any(k in t for k in _SCORE_ONLY_KEYWORDS) and not any(g in t for g in _SCORE_NON_MARKS_GUARDS):
        return True
    return False

VTOP_FETCH_KEYWORDS = [
    "check in vtop", "from vtop", "in vtop", "vtop check",
    "check vtop", "directly from vtop", "fetch from vtop",
    "refresh marks", "sync marks", "update marks", "reload marks",
    "fetch marks", "get latest marks", "refresh vtop", "sync vtop",
    "update vtop", "reload vtop", "live marks", "latest marks",
    "check my marks in vtop", "get from vtop"
]

def is_vtop_fetch_request(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in VTOP_FETCH_KEYWORDS)


LMS_KEYWORDS = [
    "assignment", "assignments", "pending assignment", "lms",
    "moodle", "what's due", "what is due", "due today",
    "due tomorrow", "submit", "submission", "not submitted",
    "pending submission", "any assignment", "my assignment",
    "assignment deadline", "deadline"
]

LMS_SYNC_KEYWORDS = [
    "sync lms", "refresh lms", "update lms", "check lms",
    "fetch assignments", "reload assignments", "latest assignments",
    "refresh my assignments", "refresh assignments", "sync assignments",
    "sync my assignments", "update my assignments",
]

def detect_lms_query(text: str) -> str | None:
    if _fuzzy_any(text, LMS_SYNC_KEYWORDS):
        return "sync"
    if _fuzzy_any(text, LMS_KEYWORDS):
        return "assignments"
    return None


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
    words   = text.split()
    lowered = [w.lower().strip(",.?!") for w in words]
    if not any(v in lowered for v in verbs) or marker_word not in lowered:
        return None
    kept = [w for w, lw in zip(words, lowered) if lw not in verbs and lw not in _TASK_FILLER_WORDS and lw != marker_word]
    return " ".join(kept).strip(" ,.")

def detect_task_intent(text: str) -> tuple | None:
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


SCHEDULE_TODAY_PHRASES = [
    "schedule today", "my schedule today", "what's my schedule", "whats my schedule",
    "today's schedule",
]
SCHEDULE_TOMORROW_PHRASES = ["schedule tomorrow", "tomorrow's schedule"]
SCHEDULE_WEEK_PHRASES = ["schedule this week", "my schedule this week", "week's schedule", "schedule for the week"]
SCHEDULE_NEXT_PHRASES = ["what's next", "whats next", "what is next", "what am i doing next", "what do i have next"]
SCHEDULE_FREE_GENERAL_PHRASES = ["when am i free", "when will i be free"]
SCHEDULE_FREE_AT_MARKER = "am i free at"
SCHEDULE_DELETE_VERBS = ["remove", "delete", "cancel", "drop"]

def detect_schedule_intent(text: str) -> tuple | None:
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

    if ("block" in t and " to " in t) or ("schedule" in t and " to " in t and "for" in t):
        return "schedule_add", {"raw_text": text}

    return None


FOCUS_START_TRIGGERS = ["focus mode", "start focus", "start pomodoro", "focus on", "pomodoro "]
FOCUS_STOP_PHRASES = ["stop focus", "stop focusing", "end focus", "end my focus", "exit focus"]
FOCUS_STATUS_PHRASES = ["focus status", "am i focusing", "am i in focus"]
FOCUS_STATS_PHRASES = [
    "how many hours did i study", "how much did i study", "how long did i study",
    "study stats", "focus stats",
]

def detect_focus_intent(text: str) -> tuple | None:
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


EXPENSE_ADD_VERBS = ["spent", "paid", "spend"]
EXPENSE_SUMMARY_PHRASES = [
    "how much did i spend", "how much have i spent", "how much on",
    "what did i spend", "spending this week", "spending today",
    "spending this month", "expense summary",
]
_EXPENSE_BARE_AMOUNT_LEAD_RE = re.compile(r'^\s*(?:rs\.?|\u20b9)?\s*\d+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s+(for|on)\b', re.IGNORECASE)
_ALL_EXPENSE_KEYWORDS = [kw for kws in _EXPENSE_CATEGORY_RULES.values() for kw in kws]

def detect_expense_intent(text: str) -> tuple | None:
    t = text.lower()

    if any(p in t for p in EXPENSE_SUMMARY_PHRASES):
        return "expense_summary", {"raw_text": text}

    if not re.search(r'\d', t):
        return None

    if any(verb in t for verb in EXPENSE_ADD_VERBS):
        return "expense_add", {"raw_text": text}
    if any(kw in t for kw in _ALL_EXPENSE_KEYWORDS):
        return "expense_add", {"raw_text": text}
    if _EXPENSE_BARE_AMOUNT_LEAD_RE.match(text):
        return "expense_add", {"raw_text": text}

    return None


_ALIAS_MEANS_RE = re.compile(
    r"^(?:remember|note)(?:\s+that)?\s+(.+?)\s+(?:means|is|stands for)\s+(.+)$",
    re.IGNORECASE
)
_ALIAS_CALL_RE = re.compile(
    r"^call\s+(.+?)\s+(?:as\s+)?(.+)$",
    re.IGNORECASE
)

def detect_alias_add_intent(text: str) -> tuple | None:
    stripped = text.strip()

    m = _ALIAS_MEANS_RE.match(stripped)
    if m:
        return "alias_add", {"alias": m.group(1).strip(), "course_query": m.group(2).strip()}

    m = _ALIAS_CALL_RE.match(stripped)
    if m:
        return "alias_add", {"course_query": m.group(1).strip(), "alias": m.group(2).strip()}

    return None


_ALIAS_MERCHANT_RE = re.compile(
    r"^(?:remember\s+)?(?:that\s+)?(?:call\s+that\s+)?(?:opaque\s+)?vpa\s+(?:is|means)?\s*(.+)$",
    re.IGNORECASE
)

def detect_alias_merchant_intent(text: str) -> tuple | None:
    stripped = text.strip()
    if "vpa" not in stripped.lower():
        return None
    m = _ALIAS_MERCHANT_RE.match(stripped)
    if m:
        name = m.group(1).strip()
        if name:
            return "alias_merchant", {"friendly_name": name}
    return None


DAILY_BRIEF_KEYWORDS = [
    "daily brief", "morning brief", "give me my brief", "give me the brief",
    "my brief", "brief me", "today's brief", "give me the rundown"
]

def is_daily_brief_request(text: str) -> bool:
    return _fuzzy_any(text, DAILY_BRIEF_KEYWORDS)


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
    # Note: "schedule today" / "whats due today" are deliberately NOT
    # listed here — those go through Stage 2 (schedule_today /
    # lms_assignments), which is just as fast on the small classifier
    # model and doesn't risk this exact-match set growing unbounded.
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
    Stage 1 — deterministic keyword/regex routing for everything
    high-frequency enough to matter, checked in an order that's been
    specifically tuned (see the inline comments) to avoid the collision
    bugs found and fixed across this project's history. Stage 2's Groq
    classifier (see GROQ_INTENTS / extract_general_intent_groq) is the
    fallback for anything this doesn't recognize — genuinely ambiguous
    phrasing, or a feature combination nobody's hit yet.

    This used to be Groq-first for nearly everything, which was more
    accurate on paper but confirmed live to be a dead end: Groq's free
    tier caps the classifier model at 6000 tokens/MINUTE, and even a
    tightly trimmed prompt only allows ~3 calls/minute before 429s —
    nowhere near enough for normal conversation. Cutting how often Stage
    2 gets called at all (by catching the common cases here, for free)
    is the only way to stay inside that budget without constant rate
    limiting. Every specific accuracy bug found during the Groq-only
    experiment (spotify "stop playing", whatsapp "tell X", class_at_time
    day-only handling, the shutdown/cancel-shutdown guards) is preserved
    below or in Stage 2's prompt/route()'s safety net either way.
    """
    t = text.lower()

    # CRITICAL SAFETY CHECK — must run before PC Control below. Confirmed
    # incident: "shutdown jarvis" matched PC_KEYWORDS' bare "shutdown"
    # substring first and triggered a REAL os.system("shutdown /s /t 10").
    # This intercepts anything mentioning both jarvis and shutdown/
    # restart/exit before PC control ever sees it, deterministically —
    # never delegated to the classifier, see route()'s own safety net too.
    if is_shutdown_request(t):
        return "system_shutdown", {}

    # Equally safety-critical: cancelling an already-armed real shutdown
    # must never depend on a network call succeeding. See
    # is_cancel_shutdown_request's own comment for the confirmed failure
    # mode (Groq misreading "cancel shutdown" as system_shutdown, which
    # exits Jarvis but does nothing to an already-scheduled OS shutdown).
    if is_cancel_shutdown_request(t):
        return "pc", {}

    # Spotify — checked before PC Control; see detect_spotify_intent's
    # own comment for why ("open spotify and play X" contains "open").
    spotify_match = detect_spotify_intent(text)
    if spotify_match:
        return spotify_match

    # PC Control — skip it entirely for "open <known website>" so those
    # fall through to web automation instead of a false "App not found".
    # This pre-filter being fuzzy is low-risk: a false-positive match just
    # means handle_pc_command(t) gets a chance to look and returns None
    # (falls through normally) if nothing in it actually applies.
    if _fuzzy_any(text, PC_KEYWORDS) and not _open_targets_known_website(text):
        result = handle_pc_command(t)
        if result:
            return "pc", result

    # Tasks — checked right after PC control (so "cancel the shutdown"
    # still resolves as a PC command, not a task-drop attempt) but before
    # marks/LMS: "mark" collides with MARKS_KEYWORDS, "submit"/
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

    # WhatsApp — cheap pre-filter only; real extraction is a separate,
    # small, dedicated Groq call in core/whatsapp_intent.py, not the big
    # classifier. Skipped if the sentence also clearly asks a VTOP data
    # question (e.g. "tell me my overall attendance" contains "tell ").
    if looks_like_whatsapp_request(text) and not is_data_question:
        return "whatsapp", {"raw_text": text}

    # Web automation — same idea: cheap pre-filter, real extraction is
    # core/web_intent.py's own small dedicated call. Same data-question
    # exception as WhatsApp above.
    if (looks_like_web_request(text) or _open_targets_known_website(text)) and not is_data_question:
        return "web", {"raw_text": text}

    # On-demand VTOP fetch (user explicitly wants live data) — handle_
    # vtop_fetch/handle_marks (server.py) build the actual marks-intent
    # dict themselves via extract_marks_intent(user_input), so this just
    # needs to get the CATEGORY right, not pre-build entities.
    if is_vtop_fetch_request(t) and has_marks_keyword(t):
        return "vtop_fetch_marks", {}

    if has_marks_keyword(t):
        return "vtop_marks", {}

    # LMS / assignments — detect_lms_query returns "assignments"|"sync";
    # map that to the actual registered intent names (server.py's
    # INTENT_HANDLERS has lms_assignments/lms_sync, not a single "lms").
    lms_type = detect_lms_query(t)
    if lms_type == "sync":
        return "lms_sync", {}
    if lms_type == "assignments":
        return "lms_assignments", {}

    if is_daily_brief_request(t):
        return "daily_brief", {}

    return detect_simple_data_query(text)


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
    "attendance":       "attendance %, classes attended/missed, debarment risk",
    "bunk_check":       "can I skip/bunk a class and stay above 75%, safest subject to skip",
    "timetable_today":  "today's classes",
    "timetable_tomorrow": "tomorrow's classes",
    "timetable_week":   "the FULL week's class schedule (not one day)",
    "next_class":       "what's my next class",
    "class_at_time":    "course on a specific day ('TOC on friday'), a time slot free ('free at 3pm'), OR one named day's full classes with no course/time ('monday schedule', 'i need for monday') -- day-only means class_at_time, not timetable_week",
    "exams":            "exam dates -- CAT/FAT schedule, next exam, days until",
    "cgpa":             "overall CGPA",
    "sem_gpa":          "GPA for one semester",
    "grade_history":    "grade in a course, or full grade history",
    "cgpa_predict":     "CGPA if a NAMED course got a specific grade",
    "grade_target":     "mark needed in a course/assessment for a target grade",
    "best_case_cgpa":   "CGPA if EVERY course this sem got the same grade (no course named)",
    "overall_cgpa_target": "grade/score needed this sem to reach a target overall CGPA",
    "task_add":         "new personal reminder/to-do, not a class assignment",
    "task_list":        "listing open personal tasks",
    "task_today":       "personal tasks due today/overdue",
    "task_complete":    "marking a personal task done",
    "task_drop":        "removing a personal task",
    "schedule_add":     "blocking time for an activity ('block 3-5 for TOC study')",
    "schedule_today":   "today's schedule: classes + custom blocks",
    "schedule_tomorrow": "tomorrow's schedule",
    "schedule_week":    "the week's schedule",
    "schedule_next":    "what's next right now (class or block)",
    "schedule_free":    "free time in the schedule",
    "schedule_delete":  "removing a scheduled block (not a class)",
    "focus_start":      "starting a focus/pomodoro session",
    "focus_stop":       "ending the focus session",
    "focus_status":     "is a focus session active",
    "focus_stats":      "recent study/focus time",
    "expense_add":      "logging a purchase with an amount",
    "expense_summary":  "total/breakdown of spending over a period",
    "expense_search":   "searching past expenses by merchant",
    "alias_add":        "teaching a course nickname ('DAA means Design and Analysis of Algorithms')",
    "alias_merchant":   "naming an opaque UPI VPA from a recent expense",
    "system_shutdown":  "explicitly shutting down/exiting Jarvis itself (not just ending this chat)",
    "spotify":          "Spotify playback -- play/open/pause/resume/skip/volume/now playing",
    "pc":               "controlling THIS PC -- volume/mute/screenshot/brightness/apps/battery/CPU/memory/disk/clipboard/windows/processes/shutdown-restart-sleep the PC (not Jarvis)",
    "web":              "browsing the web -- open/search a site, compare sites, summarize/interact with the open page",
    "whatsapp":         "sending a WhatsApp message, incl. casual 'tell <person> <message>'",
    "vtop_marks":       "specific assessment marks (CAT1/CAT2/FAT) for a course, from synced data",
    "vtop_fetch_marks": "explicitly refresh/sync marks live from VTOP now",
    "lms_assignments":  "pending LMS/Moodle assignments (class assignment, not a personal task)",
    "lms_sync":         "explicitly refresh assignment data from LMS",
    "daily_brief":      "the daily/morning brief",
    "chat":             "general conversation or anything else",
}

EXTRACTION_PROMPT = """Classify this voice assistant command into exactly ONE intent. Ignore filler ("please", "can you", "go check", "sync from vtop") -- classify by the real question/action.

Intents:
{intent_list}

Recent turns (for follow-ups only, e.g. "what about that one" -- ignore if current input names its own course/day/time):
{recent_turns}

Entity rules (omit entities not listed here; default entities={{}}):
- course: named subject, verbatim (attendance, bunk_check, grade_history, cgpa_predict, grade_target, vtop_marks)
- days_ahead: int, today=0/tomorrow=1 (bunk_check)
- day/time: class_at_time. Day alone with no course/time = that day's full classes, NOT timetable_week (only for "the whole week")
- when: exams -> next/all/cat1/cat2/fat
- target_grade: grade_target, single letter
- grade: cgpa_predict (needs a course) vs best_case_cgpa (no course, whole-semester hypothetical, default "S"); number->letter: 10=S 9=A 8=B 7=C 6=D 5=E 0=F
- target_cgpa: overall_cgpa_target, number
- alias/course_query: alias_add
- action/query: spotify. action=play/open/pause/resume/next/previous/volume_up/volume_down/now_playing; query=song name, only for "play"
- query: task_complete/task_drop/schedule_delete -- the task/block title
- pc/web/whatsapp/vtop_fetch_marks/lms_assignments/lms_sync/daily_brief: always entities={{}}

Format: {{"intent": "<name>", "entities": {{...}}, "confidence": <0.0-1.0>}}

Examples:
Input: "attendance in DBMS"
Output: {{"intent": "attendance", "entities": {{"course": "DBMS"}}, "confidence": 1.0}}

Input: "can I skip DBMS tomorrow"
Output: {{"intent": "bunk_check", "entities": {{"course": "DBMS", "days_ahead": 1}}, "confidence": 1.0}}

Input: "am I free at 3 PM"
Output: {{"intent": "class_at_time", "entities": {{"time": "3 PM"}}, "confidence": 1.0}}

Input: "do I have TOC on friday"
Output: {{"intent": "class_at_time", "entities": {{"course": "TOC", "day": "friday"}}, "confidence": 1.0}}

Input: "i need for monday"
Output: {{"intent": "class_at_time", "entities": {{"day": "monday"}}, "confidence": 1.0}}

Input: "what does my week look like"
Output: {{"intent": "timetable_week", "entities": {{}}, "confidence": 1.0}}

Input: "if I get A in everything, what's my CGPA"
Output: {{"intent": "best_case_cgpa", "entities": {{"grade": "A"}}, "confidence": 1.0}}

Input: "what's my CGPA if I get A in DBMS"
Output: {{"intent": "cgpa_predict", "entities": {{"course": "DBMS", "grade": "A"}}, "confidence": 1.0}}

Input: "what will my cgpa be if i score 9 in this sem"
Output: {{"intent": "best_case_cgpa", "entities": {{"grade": "A"}}, "confidence": 1.0}}

Input: "can you put on blinding lights by the weeknd"
Output: {{"intent": "spotify", "entities": {{"action": "play", "query": "blinding lights by the weeknd"}}, "confidence": 1.0}}

Input: "skip this one"
Output: {{"intent": "spotify", "entities": {{"action": "next"}}, "confidence": 1.0}}

Input: "turn the volume up a bit"
Output: {{"intent": "pc", "entities": {{}}, "confidence": 1.0}}

Input: "switch to my browser window"
Output: {{"intent": "pc", "entities": {{}}, "confidence": 1.0}}

Input: "open chrome and search for cheap flights to goa"
Output: {{"intent": "web", "entities": {{}}, "confidence": 1.0}}

Input: "tell amma i'll be late"
Output: {{"intent": "whatsapp", "entities": {{}}, "confidence": 1.0}}

Input: "what did I get in DBMS CAT1"
Output: {{"intent": "vtop_marks", "entities": {{"course": "DBMS"}}, "confidence": 1.0}}

Input: "mark the fees reminder as done"
Output: {{"intent": "task_complete", "entities": {{"query": "fees reminder"}}, "confidence": 1.0}}

Input: "drop my gym block"
Output: {{"intent": "schedule_delete", "entities": {{"query": "gym"}}, "confidence": 1.0}}

Given recent turn: user said "what's my attendance in DAA" -> intent=attendance, entities={{"course": "DAA"}}
Input: "what about my marks"
Output: {{"intent": "grade_history", "entities": {{"course": "DAA"}}, "confidence": 1.0}}

Input: "sync from vtop and tell me my overall attendance"
Output: {{"intent": "attendance", "entities": {{}}, "confidence": 1.0}}

If nothing fits: {{"intent": "chat", "entities": {{}}, "confidence": 1.0}}

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
        # GROQ_CLASSIFIER_MODEL (a small/fast model), not GROQ_MODEL — this
        # now runs on nearly every message (Stage 1 is minimal, see
        # fast_path_route), so classification speed directly matters. A
        # structured intent+entities JSON call is exactly the kind of
        # task a small model handles just as reliably as the big one,
        # confirmed via extensive live testing across every feature
        # domain during this migration.
        response = groq_client.chat.completions.create(
            model=GROQ_CLASSIFIER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
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
        # api_failed distinguishes "the call itself broke" (rate limit,
        # network) from "it ran and was genuinely unsure" — confirmed
        # live this was being conflated: a Groq 429 during heavy testing
        # made ordinary chit-chat ("Hey Jarvis", "See ya.", "So,
        # medicine.") all land on the same academic-flavored clarify
        # menu ("did you mean: attendance/classes/exams/CGPA"), which is
        # actively confusing for input that was never a data question in
        # the first place. See _low_confidence_fallback's allow_clarify.
        return {"intent": "chat", "entities": {}, "confidence": 0.0, "api_failed": True}


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


def _low_confidence_fallback(text: str, allow_clarify: bool = True) -> tuple:
    """
    Returns (category, payload) — never None. Tries the course resolver
    (a named course strongly suggests a data question, not chat), then a
    broader data-keyword check, then a day/time word (suggests a
    schedule question), and only falls back to a short numbered clarify
    menu if none of those signals fire — better than silently guessing
    or dead-ending on "I don't understand."

    allow_clarify=False (passed when Stage 2's API call itself failed,
    not just came back unsure) skips that menu in favor of plain "brain"
    chat — the clarify menu's four options (attendance/classes/exams/
    CGPA) are a reasonable guess when the classifier actually looked at
    the text and hedged, but meaningless when it never got to look at
    all. Confirmed live: a Groq rate limit made ordinary conversation
    ("Hey Jarvis", "See ya.") show that menu, which has nothing to do
    with what was actually said.
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

    if not allow_clarify:
        return "brain", None
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
        api_failed = classified.get("api_failed", False)

        if intent == "chat":
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                category, payload = _low_confidence_fallback(text, allow_clarify=not api_failed)
            else:
                return "brain", None
        else:
            category, payload = intent, classified.get("entities", {})

    # Safety net, independent of which model classified this or how
    # confident it was: system_shutdown must never fire unless "jarvis"
    # is actually named. Confirmed live during this migration — the
    # classifier occasionally misread plain "shutdown" or "let's restart
    # the conversation" as system_shutdown despite the intent's own
    # description explicitly saying "not just ending the conversation".
    # Demoting to "pc" here (rather than silently dropping the request)
    # means a bare "shutdown"/"restart" still does something sensible —
    # features.pc_control's own confirm-gated flow takes it from there.
    if category == "system_shutdown" and "jarvis" not in text.lower():
        category, payload = "pc", {}

    # Entity inheritance only makes sense for a dict-shaped payload —
    # payload is always a dict now (Groq's entities, or {} from the
    # Stage-1 safety intercepts above).
    if isinstance(payload, dict) and not payload.get("course") and _looks_like_followup(text):
        from core.context import get_last_entity
        inherited = get_last_entity("course")
        if inherited:
            payload = {**payload, "course": inherited}

    return category, payload

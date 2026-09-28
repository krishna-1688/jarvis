"""
router.py — routing for Jarvis.

Stage 1 (fast_path_route) is deterministic and deliberately
HIGH-PRECISION: it only claims an utterance when the phrasing is an
unambiguous command ("pause spotify", "remind me to ...", "what's my
attendance"). Every matcher uses whole-word matching (_has_any), and
open-ended verbs — open/close/find/tell/text/search — only count when
they START the sentence.

That precision is the whole point. The previous version matched bare
substrings anywhere in the sentence: "mark" inside "market", "fat" inside
"father", "os" inside "most", "cpu" inside "explain cpu scheduling",
"tell " inside "tell me a joke", "close" inside "how close am I to 75%".
A Stage 1 false positive is the worst failure this app has — the
question gets answered by the wrong feature (a CPU-usage readout, a
WhatsApp prompt, an app getting killed). A Stage 1 miss only costs one
fast classifier call.

Stage 1.5 (in fast_path_route): small talk and impersonal knowledge
questions ("what is a deadlock", "tell me a joke") go straight to the
brain — no classifier call, and no way to be mistaken for a command.

Stage 2 (extract_general_intent_groq) classifies everything else against
GROQ_INTENTS through core.llm, which walks a fallback chain of models. If
every model is down, _offline_fallback handles obvious data questions
and sends the rest to chat.

Marks queries stay 100% offline (SQLite) by default; VTOP is only
touched when the user explicitly asks to refresh/sync (vtop_fetch_marks
intent). Theory = course code ends with L (default when no lab
mentioned); Lab = course code ends with P (only when user says
lab/practical).
"""

import os
import re
import sys
from functools import lru_cache
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rapidfuzz import fuzz
from core.llm import complete_json, LLMUnavailable
from features.expenses import CATEGORY_RULES as _EXPENSE_CATEGORY_RULES


# ══════════════════════════════════════════
#   MATCHING HELPERS
# ══════════════════════════════════════════

@lru_cache(maxsize=1024)
def _phrase_re(phrase: str):
    body = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){body}(?!\w)")


def _has_any(text: str, phrases) -> bool:
    """Whole-word/phrase match — "mark" matches "my mark" but not
    "market"; "fat" matches "fat marks" but not "father"."""
    t = text.lower()
    return any(_phrase_re(p).search(t) for p in phrases)


def _fuzzy_any(text: str, keywords, threshold: int = 85) -> bool:
    """Typo-tolerant _has_any — e.g. "atendance" or "spotfy" still
    matches "attendance"/"spotify". Whole-word match first; only
    single-WORD keywords of 5+ letters fall through to a per-word fuzzy
    comparison (short words collide with unrelated words far more often
    than they represent a typo). Never used for anything
    safety-critical."""
    if _has_any(text, keywords):
        return True
    single_word_keywords = [k for k in keywords if " " not in k.strip() and len(k.strip()) >= 5]
    if not single_word_keywords:
        return False
    for w in re.findall(r"[a-z']+", text.lower()):
        for kw in single_word_keywords:
            if abs(len(w) - len(kw)) <= 2 and fuzz.ratio(w, kw) >= threshold:
                return True
    return False


_LEAD_FILLER = r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|pls\s+|just\s+)*"

def _strip_lead_filler(t: str) -> str:
    return re.sub(_LEAD_FILLER, "", t.strip())


_PERSONAL_RE = re.compile(r"\b(?:my|mine|i|i'm|im|i've|i'll|am\s+i|do\s+i|did\s+i|should\s+i|can\s+i|we|our)\b")

_QUESTION_PREFIX_RE = re.compile(
    r"^(?:what\s+(?:is|are|was|were)\b|what's\s+(?:a|an)\b|whats\s+(?:a|an)\b|who\s+(?:is|was|are|were)\b|"
    r"explain\b|define\b|describe\b|why\b|how\s+(?:do|does|did|can|could|would|should|is|are|was|to)\b|"
    r"tell\s+me\s+(?:a|an|about|something|some)\b|teach\s+me\b|"
    r"give\s+me\s+(?:an?\s+|some\s+)?(?:example|examples|idea|ideas|tip|tips|fact|facts|joke|quote)\b|"
    r"difference\s+between\b|what\s+does\s+\S+\s+mean\b|meaning\s+of\b|summari[sz]e\s+(?!this\b|the\s+page\b))"
)

# If one of these appears, the question is probably about KK's own
# data or a device/app — let the real matchers or the classifier decide.
_COMMAND_OR_DATA_TERMS = (
    "attendance", "marks", "mark", "cgpa", "gpa", "grade", "grades", "timetable", "time table",
    "class", "classes", "exam", "exams", "cat1", "cat2", "cat 1", "cat 2", "fat",
    "assignment", "assignments", "deadline", "deadlines", "lms", "moodle", "vtop", "due",
    "task", "tasks", "schedule", "focus", "pomodoro", "expense", "expenses", "spent", "spend",
    "spotify", "song", "music", "playing", "volume", "mute", "battery", "brightness", "screenshot",
    "cpu usage", "ram usage", "disk space", "whatsapp", "message", "tab", "page", "window", "brief",
    "remind", "reminder", "today", "tomorrow", "tonight", "this week",
)

_SMALLTALK_RE = re.compile(
    r"^(?:hi|hello|hey|yo|sup|hola|thanks|thank\s+you|thx|ok|okay|cool|nice|great|awesome|lol|haha|"
    r"good\s+(?:morning|afternoon|evening|night)|how\s+are\s+you|how's\s+it\s+going|hows\s+it\s+going|"
    r"what's\s+up|whats\s+up|who\s+are\s+you|what\s+can\s+you\s+do|you\s+there|are\s+you\s+there)\b"
)


def _is_impersonal_question(t: str) -> bool:
    t = _strip_lead_filler(t.lower())
    return bool(_QUESTION_PREFIX_RE.match(t)) and not _PERSONAL_RE.search(t)


def _is_general_chat(text: str) -> bool:
    """Small talk or a general-knowledge question with no personal
    reference and no feature/data vocabulary — goes straight to the
    brain. "what is a deadlock" / "tell me a joke" / "explain TCP" qualify;
    "what is my attendance" / "how do I open task manager" don't."""
    t = _strip_lead_filler(text.lower()).rstrip("?.! ")
    if _looks_like_followup(t) or _has_any(t, _COMMAND_OR_DATA_TERMS):
        return False
    if _SMALLTALK_RE.match(t) and len(t.split()) <= 6:
        return True
    return _is_impersonal_question(t)


# ══════════════════════════════════════════
#   SAFETY (exact-match only, never fuzzy, never LLM-dependent)
# ══════════════════════════════════════════

# Shutdown — a voice/text keyword to cleanly stop the backend server
# (see server.py's handle_shutdown). "stop" / "shut down" alone are
# deliberately excluded — too easy to collide with an unrelated sentence.
SHUTDOWN_KEYWORDS = [
    "shutdown jarvis", "shut down jarvis", "turn off jarvis",
    "power off jarvis", "exit jarvis", "jarvis go offline",
    "jarvis shut down", "kill jarvis", "quit jarvis",
]

def is_shutdown_request(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in SHUTDOWN_KEYWORDS)


# Cancelling an armed PC shutdown/restart must never depend on a network
# call succeeding — the OS timer runs independently of this process.
CANCEL_SHUTDOWN_PHRASES = ("cancel shutdown", "cancel the shutdown", "cancel restart", "cancel the restart")

def is_cancel_shutdown_request(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in CANCEL_SHUTDOWN_PHRASES)


# ══════════════════════════════════════════
#   SPOTIFY
# ══════════════════════════════════════════
# Checked before PC control — "open spotify and play X" starts with
# "open", which would otherwise be tried as a desktop-app launch.

from features.pc_control import handle_pc_command

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
# "play" followed by one of these is not a song request.
_PLAY_NOT_MUSIC_RE = re.compile(r"^(?:a\s+game|games?|with\s+me|chess|cricket|football|along|dumb|it\s+safe|a\s+role)\b")

def _normalize_spotify_typos(t: str) -> str:
    """Rewrites a fuzzy-matched typo of 'spotify' (e.g. 'spotifi',
    'spotfy') to the canonical spelling so the exact checks below hold."""
    words = t.split()
    for i, w in enumerate(words):
        core = w.strip(",.?!")
        if core != "spotify" and len(core) >= 5 and fuzz.ratio(core, "spotify") >= 85:
            words[i] = w.replace(core, "spotify")
    return " ".join(words)

def detect_spotify_intent(text: str) -> tuple | None:
    """Returns ('spotify', {'action': ..., **extra}) for a Spotify
    playback command, or None."""
    t = _normalize_spotify_typos(_strip_lead_filler(text.lower()))

    is_spotify_word = _has_any(t, ["spotify"])

    if is_spotify_word and any(q in t for q in ("web player", "in the browser", "on the web", "spotify web")):
        return None

    if is_spotify_word and _has_any(t, ("open", "launch", "start")) and "play" not in t and "pause" not in t:
        return "spotify", {"action": "open"}

    m = _SPOTIFY_PLAY_RE.match(t)
    if m:
        query = m.group(1).strip(" ,.")
        if not _PLAY_NOT_MUSIC_RE.match(query):
            return "spotify", {"action": "play", "query": query}

    bare = t.rstrip("?.! ")
    if _has_any(t, SPOTIFY_PAUSE_PHRASES) or bare in ("pause", "pause please"):
        return "spotify", {"action": "pause"}

    if _has_any(t, SPOTIFY_RESUME_PHRASES) or bare in ("resume", "resume please"):
        return "spotify", {"action": "resume"}

    if _has_any(t, SPOTIFY_NEXT_PHRASES) or bare in ("skip", "next", "next please", "skip it"):
        return "spotify", {"action": "next"}

    if _has_any(t, SPOTIFY_PREV_PHRASES) or bare in ("previous", "previous song please"):
        return "spotify", {"action": "previous"}

    if _has_any(t, SPOTIFY_NOWPLAYING_PHRASES):
        return "spotify", {"action": "now_playing"}

    if is_spotify_word and "volume" in t:
        if _has_any(t, SPOTIFY_VOLUME_UP_WORDS):
            return "spotify", {"action": "volume_up"}
        if _has_any(t, SPOTIFY_VOLUME_DOWN_WORDS):
            return "spotify", {"action": "volume_down"}

    return None


# ══════════════════════════════════════════
#   PC CONTROL (strict)
# ══════════════════════════════════════════

_UP_DOWN_WORDS = ("up", "down", "increase", "decrease", "raise", "lower", "louder", "quieter",
                  "brighter", "dimmer", "reduce", "max", "maximum", "full", "half", "set")
_VOLUME_LEVEL_PHRASES = ("what's the volume", "whats the volume", "what is the volume", "volume level",
                         "is it muted", "am i muted", "is the volume muted", "mute status")
_PC_STATUS_TARGETS = ("battery", "cpu", "ram", "memory", "disk", "storage")
_PC_STATUS_ASKS = ("usage", "used", "level", "left", "remaining", "status", "percent", "percentage",
                   "how much", "how full", "free", "charge", "charging", "check", "load")
_PC_DEVICE_WORDS = ("my", "pc", "laptop", "computer", "system", "machine")
_PC_PROCESS_PHRASES = ("running processes", "what's running", "whats running", "what apps are open", "list processes")
_PC_POWER_WORDS = ("shutdown", "shut down", "restart", "reboot", "sleep", "hibernate")
_PC_TARGET_WORDS = ("pc", "computer", "system", "laptop", "machine")
_PC_BARE_POWER = {"shutdown", "shut down", "shutdown please", "shut down please",
                  "restart", "restart please", "sleep", "sleep please"}

_SCREENSHOT_RE = re.compile(r"^(?:take|grab|capture|get)?\s*(?:a\s+|the\s+)?screenshot\b|\b(?:take|grab|capture)\s+(?:a\s+|the\s+)?screenshot\b")
_WINDOW_RE = re.compile(
    r"\b(?:switch\s+to|bring\s+up|minimi[sz]e|maximi[sz]e|close)\b.*\bwindows?\b"
    r"|\b(?:list|what)\s+windows\b|\bwindows\s+(?:are\s+)?open\b"
)
_APP_VERB_RE = re.compile(
    r"^(open|launch|start|run|close|quit|exit|kill)\s+(?:the\s+|my\s+|up\s+)?"
    r"([a-z0-9][a-z0-9 .+#-]{0,30}?)(?:\s+(?:app|application|program))?(?:\s+(?:please|for me|now))*[.!?]*$"
)
_FILE_FIND_RE = re.compile(r"^(?:find|search\s+for|locate)\s+(?:the\s+|my\s+|a\s+)?(?:file|files|folder|document|pdf|doc)\b"
                           r"|^(?:find|search)\s+my\s+files?\b")

# Things that follow open/start/close but are features, not desktop apps.
_NON_APP_TARGETS = ("focus", "pomodoro", "timer", "session", "a session", "study", "the conversation",
                    "conversation", "chat", "over", "again", "up about", "playing", "tab", "this tab",
                    "the tab", "a tab", "a new tab", "new tab")

def detect_pc_request(text: str) -> bool:
    """True only for unambiguous commands aimed at this PC. Concept
    questions ("explain cpu scheduling", "what is virtual memory") and
    sentences that merely contain "close"/"open"/"find" are left alone."""
    t = _strip_lead_filler(text.lower())
    if _is_impersonal_question(t):
        return False

    if _has_any(t, _VOLUME_LEVEL_PHRASES):
        return True
    if _has_any(t, ("volume", "sound")) and _has_any(t, _UP_DOWN_WORDS):
        return True
    if _has_any(t, ("mute", "unmute")):
        return True
    if _SCREENSHOT_RE.search(t):
        return True
    if _has_any(t, ("brightness",)) and _has_any(t, _UP_DOWN_WORDS):
        return True
    if _has_any(t, _PC_STATUS_TARGETS) and (_has_any(t, _PC_DEVICE_WORDS) or _has_any(t, _PC_STATUS_ASKS)):
        return True
    if _has_any(t, ("system status", "system info", "clipboard", "what did i copy", "what's copied", "whats copied")):
        return True
    if _has_any(t, _PC_PROCESS_PHRASES):
        return True
    if _WINDOW_RE.search(t):
        return True
    if _has_any(t, _PC_POWER_WORDS) and "jarvis" not in t:
        if t.rstrip("?.! ") in _PC_BARE_POWER or _has_any(t, _PC_TARGET_WORDS):
            return True
    if _FILE_FIND_RE.match(t):
        return True
    return False


def detect_app_command(text: str) -> bool:
    """'open notepad' / 'close chrome' / 'launch vs code' — the verb must
    start the sentence and the target must be a short name. Known
    websites are left to web automation."""
    t = _strip_lead_filler(text.lower())
    m = _APP_VERB_RE.match(t)
    if not m:
        return False
    target = m.group(2).strip()
    if not target or len(target.split()) > 4:
        return False
    if target in _NON_APP_TARGETS or target.startswith(("a ", "an ", "focus", "pomodoro")):
        return False
    if _SITE_RE.search(target):
        return False
    return True


# ══════════════════════════════════════════
#   WEB + WHATSAPP (strict)
# ══════════════════════════════════════════

KNOWN_WEB_DESTINATIONS = [
    "youtube", "amazon", "flipkart", "google", "gmail", "github",
    "wikipedia", "skyscanner", "reddit", "linkedin", "twitter",
    "vtop", "moodle", "facebook", "instagram", "netflix", "leetcode",
    "stackoverflow", "stack overflow", "chatgpt", "spotify web", "whatsapp web",
]
_SITE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in KNOWN_WEB_DESTINATIONS) + r")(?!\w)"
    r"|[a-z0-9-]+\.(?:com|in|org|net|io|dev|ai|co|edu|gov|app)\b"
)

_WEB_OPEN_RE = re.compile(r"^(?:open|go\s+to|visit|launch|pull\s+up|browse)\s+(?:the\s+)?(?:website\s+|site\s+)?")
_WEB_SEARCH_RE = re.compile(r"^(?:search|google|look\s+up|browse)\s+(?!my\s+|for\s+(?:a\s+|my\s+)?files?\b)(?:for\s+|about\s+)?\S")
_WEB_CLICK_RE = re.compile(r"\bclick\s+(?:on\s+)?(?:the\s+)?(?:\w+\s+)?(?:result|link)\b")
_WEB_PAGE_PHRASES = (
    "scroll down", "scroll up", "close this tab", "close the tab", "close tab",
    "go back a page", "previous page", "back a page", "summarize this page", "summarise this page",
    "summarize the page", "summarise the page", "what does this page say", "what are the reviews",
    "read this page",
)

def looks_like_web_request(text: str) -> bool:
    t = _strip_lead_filler(text.lower())
    if _WEB_OPEN_RE.match(t) and _SITE_RE.search(t):
        return True
    if _WEB_SEARCH_RE.match(t):
        return True
    if _WEB_CLICK_RE.search(t) or _has_any(t, _WEB_PAGE_PHRASES):
        return True
    if t.startswith("compare ") and len(_SITE_RE.findall(t)) >= 2:
        return True
    return False


_NOT_A_RECIPIENT = (r"me|us|you|him|her|them|a|an|the|my|some\w*|any\w*|every\w*|what|how|why|when|where|who|"
                    r"which|about|jarvis|it|this|that|more|story|joke")
_WHATSAPP_RE = re.compile(
    r"\bwhats\s?app\b"
    r"|^(?:send|drop|shoot)\s+(?:a\s+)?(?:quick\s+)?(?:message|msg|text|whatsapp)\b"
    r"|^(?:text|message|msg|ping)\s+(?!(?:" + _NOT_A_RECIPIENT + r")\b)[a-z]+"
    r"|^tell\s+(?!(?:" + _NOT_A_RECIPIENT + r")\b)[a-z]+\s+(?:that\s+)?\S"
)

def looks_like_whatsapp_request(text: str) -> bool:
    return bool(_WHATSAPP_RE.search(_strip_lead_filler(text.lower())))


# ══════════════════════════════════════════
#   ACADEMIC DATA (marks / LMS / VTOP)
# ══════════════════════════════════════════

VTOP_DATA_SIGNAL_WORDS = [
    "attendance", "bunk", "skip class", "timetable", "time table",
    "class schedule", "next class", "free at", "exam", "exams", "cgpa", "gpa",
    "grade", "marks", "assignment", "assignments", "cat1", "cat2",
    "cat-1", "cat-2", "cat 1", "cat 2", "fat",
]

def _looks_like_data_question(text: str) -> bool:
    return _fuzzy_any(text, VTOP_DATA_SIGNAL_WORDS)


MARKS_KEYWORDS = [
    "marks", "mark", "cat1 marks", "cat2 marks", "fat marks",
    "final assessment", "continuous assessment", "internal marks", "semester marks",
    "assignment mark", "assignment marks", "digital assignment", "periodic assessment",
    "lab mark", "lab marks", "lab assessment", "what did i get", "how much did i score",
    "how did i do in", "my result", "my results",
]

_CAT_FAT_ONLY_KEYWORDS = ["cat1", "cat2", "cat 1", "cat 2", "cat-1", "cat-2", "fat"]
_CAT_FAT_NON_MARKS_GUARDS = ["schedule", "exam", "exams", "when", "need", "require", "date", "dates", "days",
                             "prepare", "preparation", "prep", "revise", "revision", "study", "studying",
                             "tips", "plan", "syllabus", "portion", "portions", "topics", "how to", "how should"]
_SCORE_ONLY_KEYWORDS = ["scored", "score"]
_SCORE_NON_MARKS_GUARDS = ["cgpa", "cgp", "gpa", "overall"]

def has_marks_keyword(text: str) -> bool:
    t = text.lower()
    if _fuzzy_any(t, MARKS_KEYWORDS):
        return True
    if _has_any(t, _CAT_FAT_ONLY_KEYWORDS) and not _has_any(t, _CAT_FAT_NON_MARKS_GUARDS):
        return True
    # "score" only counts as a marks question when it's about KK
    # ("what did I score in DBMS"), not "what's the cricket score".
    if _has_any(t, _SCORE_ONLY_KEYWORDS) and _PERSONAL_RE.search(t) and not _has_any(t, _SCORE_NON_MARKS_GUARDS):
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


# Bare "assignment" is also a programming term ("assignment operator"),
# so on its own it only counts alongside a word that makes it about KK's
# coursework.
LMS_STRONG_KEYWORDS = [
    "pending assignment", "pending assignments", "lms", "moodle", "what's due", "whats due",
    "what is due", "due today", "due tomorrow", "due this week", "not submitted",
    "pending submission", "pending submissions", "any assignment", "any assignments",
    "my assignment", "my assignments", "assignment deadline", "assignment deadlines",
    "my deadlines", "upcoming deadlines", "any deadlines",
]
_LMS_CONTEXT_WORDS = ("pending", "due", "upcoming", "left", "remaining", "submit", "submitted",
                      "deadline", "deadlines", "any", "my", "do i have")

LMS_SYNC_KEYWORDS = [
    "sync lms", "refresh lms", "update lms", "check lms",
    "fetch assignments", "reload assignments", "latest assignments",
    "refresh my assignments", "refresh assignments", "sync assignments",
    "sync my assignments", "update my assignments",
]

def detect_lms_query(text: str) -> str | None:
    if _fuzzy_any(text, LMS_SYNC_KEYWORDS):
        return "sync"
    if _fuzzy_any(text, LMS_STRONG_KEYWORDS):
        return "assignments"
    if _fuzzy_any(text, ["assignment", "assignments"]) and _has_any(text, _LMS_CONTEXT_WORDS):
        return "assignments"
    return None


# ══════════════════════════════════════════
#   TASKS / SCHEDULE / FOCUS / EXPENSES
# ══════════════════════════════════════════

TASK_ADD_TRIGGERS = ["remind me to", "add a task", "add task", "new task", "remember to", "don't forget to", "dont forget to"]
TASK_LIST_PHRASES = [
    "what are my tasks", "my tasks", "what's on my plate", "whats on my plate",
    "my task list", "open tasks", "list my tasks", "show my tasks", "pending tasks",
    "my to do list", "my todo list", "my to-do list",
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

    if _has_any(t, TASK_TODAY_PHRASES):
        return "task_today", {}

    if _has_any(t, TASK_LIST_PHRASES):
        within_days = 7 if ("this week" in t or "next 7 days" in t) else None
        return "task_list", {"raw_text": text, "within_days": within_days}

    if _has_any(t, TASK_ADD_TRIGGERS):
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
    "today's schedule", "my schedule for today",
]
SCHEDULE_TOMORROW_PHRASES = ["schedule tomorrow", "tomorrow's schedule", "my schedule for tomorrow", "tomorrow schedule"]
SCHEDULE_WEEK_PHRASES = ["schedule this week", "my schedule this week", "week's schedule", "schedule for the week"]
SCHEDULE_NEXT_PHRASES = ["what's next", "whats next", "what is next", "what am i doing next", "what do i have next"]
SCHEDULE_FREE_GENERAL_PHRASES = ["when am i free", "when will i be free"]
SCHEDULE_FREE_AT_MARKER = "am i free at"
SCHEDULE_DELETE_VERBS = ["remove", "delete", "cancel", "drop"]
# A real block request names a time range: "block 3 to 5 for TOC",
# "schedule gym from 6 to 7pm". Without one ("how to schedule my time"),
# it's a question for the brain, not a calendar write.
_SCHEDULE_ADD_RE = re.compile(
    r"\b(?:block|schedule|book)\b.*?\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-|till|until)\s*\d{1,2}"
)

def detect_schedule_intent(text: str) -> tuple | None:
    t = text.lower()

    if _has_any(t, SCHEDULE_WEEK_PHRASES):
        return "schedule_week", {}
    if _has_any(t, SCHEDULE_TOMORROW_PHRASES):
        return "schedule_tomorrow", {}
    if _has_any(t, SCHEDULE_TODAY_PHRASES):
        return "schedule_today", {}
    if _has_any(t, SCHEDULE_NEXT_PHRASES):
        return "schedule_next", {}
    if SCHEDULE_FREE_AT_MARKER in t:
        return "schedule_free", {"raw_text": text, "mode": "at_time"}
    if _has_any(t, SCHEDULE_FREE_GENERAL_PHRASES):
        return "schedule_free", {"raw_text": text, "mode": "gaps"}

    delete_query = _verb_based_query(text, SCHEDULE_DELETE_VERBS, "block")
    if delete_query is not None:
        return "schedule_delete", {"query": delete_query or text}

    if _SCHEDULE_ADD_RE.search(t):
        return "schedule_add", {"raw_text": text}

    return None


# "focus on" alone used to start a session for "I can't focus on
# studies, any tips?" — only an imperative at the start counts now.
_FOCUS_START_RE = re.compile(
    r"^(?:let's\s+|lets\s+)?(?:start|begin|enter|turn\s+on|activate)\s+(?:a\s+|the\s+|my\s+)?"
    r"(?:focus|pomodoro|study\s+session|deep\s+work)\b"
    r"|^(?:focus\s+mode|pomodoro)\b"
    r"|^focus\s+on\s+\w+"
)
FOCUS_STOP_PHRASES = ["stop focus", "stop focusing", "end focus", "end my focus", "exit focus",
                      "stop the focus", "end the focus", "stop pomodoro", "end pomodoro"]
FOCUS_STATUS_PHRASES = ["focus status", "am i focusing", "am i in focus"]
FOCUS_STATS_PHRASES = [
    "how many hours did i study", "how much did i study", "how long did i study",
    "study stats", "focus stats",
]

def detect_focus_intent(text: str) -> tuple | None:
    t = text.lower()

    if _has_any(t, FOCUS_STATS_PHRASES):
        return "focus_stats", {}
    if _has_any(t, FOCUS_STATUS_PHRASES):
        return "focus_status", {}
    if _has_any(t, FOCUS_STOP_PHRASES):
        return "focus_stop", {}
    if _FOCUS_START_RE.match(_strip_lead_filler(t)):
        return "focus_start", {"raw_text": text}

    return None


EXPENSE_SUMMARY_PHRASES = [
    "how much did i spend", "how much have i spent", "what did i spend",
    "spending this week", "spending today", "spending this month", "expense summary",
    "my expenses", "my spending",
]
_MONEY_RE = re.compile(r"(?:rs\.?|₹|inr)\s*\d|\d+(?:\.\d+)?\s*(?:rs\b|rupees?\b|₹|bucks\b|inr\b)")
_SPENT_AMOUNT_RE = re.compile(
    r"\b(?:spent|paid|spend|gave)\s+(?:rs\.?\s*|₹\s*)?\d+(?:\.\d+)?"
    r"(?!\s*(?:hours?|hrs?|mins?|minutes?|seconds?|secs?|days?|weeks?|months?|years?|times?|%|percent))"
)
_EXPENSE_BARE_AMOUNT_LEAD_RE = re.compile(r'^\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s+(for|on)\b', re.IGNORECASE)
_AMOUNT_LEAD_RE = re.compile(r"^\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s+\S")
_ALL_EXPENSE_KEYWORDS = [kw for kws in _EXPENSE_CATEGORY_RULES.values() for kw in kws]

def detect_expense_intent(text: str) -> tuple | None:
    t = text.lower()

    if _has_any(t, EXPENSE_SUMMARY_PHRASES):
        return "expense_summary", {"raw_text": text}

    if not re.search(r"\d", t):
        return None

    if _SPENT_AMOUNT_RE.search(t) or (_MONEY_RE.search(t) and _has_any(t, ("spent", "paid", "spend", "bought"))):
        return "expense_add", {"raw_text": text}
    if _EXPENSE_BARE_AMOUNT_LEAD_RE.match(text):
        return "expense_add", {"raw_text": text}
    # "180 kfc" / "₹60 canteen" — an amount FIRST, then a known merchant/category word.
    if _AMOUNT_LEAD_RE.match(t) and _has_any(t, _ALL_EXPENSE_KEYWORDS):
        return "expense_add", {"raw_text": text}

    return None


# ══════════════════════════════════════════
#   ALIASES / REMEMBER
# ══════════════════════════════════════════

# "is" is deliberately not accepted here: "remember that my birthday is
# May 5" is a fact to remember, not a course nickname.
_ALIAS_MEANS_RE = re.compile(
    r"^(?:remember|note)(?:\s+that)?\s+(.+?)\s+(?:means|stands for|is short for)\s+(.+)$",
    re.IGNORECASE
)

def detect_alias_add_intent(text: str) -> tuple | None:
    m = _ALIAS_MEANS_RE.match(text.strip())
    if m:
        return "alias_add", {"alias": m.group(1).strip(), "course_query": m.group(2).strip()}
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


_REMEMBER_FACT_RE = re.compile(
    r"^(?:remember|note\s+down|keep\s+in\s+mind|don't\s+forget|dont\s+forget)\s+(?:that\s+)?(?!to\s)(.{4,})$"
)
_RECALL_FACTS_PHRASES = ("what do you remember about me", "what do you know about me", "what do you know about myself", "what have i told you",
                         "what did i ask you to remember", "what do you remember")
_MEMORY_ABOUT_RE = re.compile(r"^what (?:do|else do) you (?:know|remember) about (?!me\b|myself\b)(.{2,60})$")
_FORGET_ABOUT_RE = re.compile(r"^forget (?:everything |all |anything )?(?:you know |you remember )?about (.{2,60})$")
_FORGET_LAST_RE = re.compile(r"^(?:forget|delete|erase) (?:that|it|what i (?:just )?(?:said|told you))$|^never ?mind,? forget (?:that|it)$")

# "what's on my screen", "can you see my screen", "explain this error on
# the screen". Not screen SETTINGS (brightness, lock, turn off) and not
# requests to SHOW something on screen — those belong to pc / dashboard.
_SCREEN_ASK_RE = re.compile(
    r"\b(?:on|in)\s+(?:my|the|this)\s+screen\b"
    r"|\b(?:look|looking|see|read|check|scan|describe|explain|summari[sz]e)\s+(?:at\s+)?(?:my|the|this)\s+screen\b"
    r"|^what am i (?:looking at|seeing)(?:\s+(?:right\s+)?now)?$"
)
_SCREEN_NOT_RE = re.compile(
    r"\b(?:brightness|lock|unlock|record|recording|share|sharing|mirror|cast|timeout|saver|resolution|screenshot)\b"
    r"|\b(?:turn|switch|put|show|display|open|pull|bring)\b"
)

def detect_screen_intent(text: str) -> tuple | None:
    t = _strip_lead_filler(text.lower()).rstrip("?.! ")
    if _SCREEN_ASK_RE.search(t) and not _SCREEN_NOT_RE.search(t):
        return "screen", {"question": text.strip()}
    return None


def detect_memory_intent(text: str) -> tuple | None:
    t = _strip_lead_filler(text.lower()).rstrip("?.! ")
    m = _FORGET_ABOUT_RE.match(t)
    if m:
        return "forget_about", {"subject": m.group(1).strip()}
    if _FORGET_LAST_RE.match(t):
        return "forget_last", {}
    m = _MEMORY_ABOUT_RE.match(t)
    if m:
        return "memory_about", {"subject": m.group(1).strip()}
    if _has_any(t, _RECALL_FACTS_PHRASES):
        return "recall_facts", {}
    m = _REMEMBER_FACT_RE.match(t)
    if m:
        # Store the fact itself ("my birthday is on 5 may"), not the command
        # around it — recall used to read back "remember that my birthday…".
        original = _strip_lead_filler(text.strip())
        fact = original[len(original) - len(m.group(1)):].strip()
        return "remember_fact", {"fact": fact[:1].upper() + fact[1:]}
    return None


DAILY_BRIEF_KEYWORDS = [
    "daily brief", "morning brief", "give me my brief", "give me the brief",
    "my brief", "brief me", "today's brief", "give me the rundown"
]

def is_daily_brief_request(text: str) -> bool:
    return _fuzzy_any(text, DAILY_BRIEF_KEYWORDS)


# Exact-match only: each maps to an intent whose handler needs no
# entities (or re-derives them from raw text itself).
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
    "my overall attendance": ("attendance", {}),
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
    "next class":        ("next_class", {}),
    "todays schedule":   ("timetable_today", {}),
    "classes today":     ("timetable_today", {}),
    "what classes today": ("timetable_today", {}),
    "what classes do i have today": ("timetable_today", {}),
    "classes tomorrow":  ("timetable_tomorrow", {}),
    "what classes do i have tomorrow": ("timetable_tomorrow", {}),
    "exams":             ("exams", {}),
    "my exams":          ("exams", {}),
    "when is my next exam": ("exams", {"when": "next"}),
    "whens my next exam": ("exams", {"when": "next"}),
    "when's my next exam": ("exams", {"when": "next"}),
}

def detect_simple_data_query(text: str) -> tuple | None:
    """A small allowlist of extremely common, entity-free data questions,
    matched EXACTLY (not by substring) — "what's my attendance"
    qualifies, "what's my attendance in DBMS" does not."""
    t = _strip_lead_filler(text.lower()).rstrip("?.! ")
    return _SIMPLE_DATA_PHRASES.get(t)


# ══════════════════════════════════════════
#   STAGE 1
# ══════════════════════════════════════════

def fast_path_route(text: str) -> tuple | None:
    """
    Stage 1 — deterministic routing, checked in an order tuned to avoid
    one feature's trigger swallowing another's sentence. Returns
    (category, payload), ("brain", None) for plain chat, or None to let
    Stage 2 decide.
    """
    t = text.lower()

    # CRITICAL SAFETY — before anything else. Confirmed incident:
    # "shutdown jarvis" once reached PC control and ran a real OS shutdown.
    if is_shutdown_request(t):
        return "system_shutdown", {}
    if is_cancel_shutdown_request(t):
        return "pc", {}

    simple = detect_simple_data_query(text)
    if simple:
        return simple

    # Before app launching: "open dashboard" is not a desktop app.
    if _DASHBOARD_RE.match(_strip_lead_filler(t).rstrip("?.! ")):
        return "open_dashboard", {}

    spotify_match = detect_spotify_intent(text)
    if spotify_match:
        return spotify_match

    screen_match = detect_screen_intent(text)
    if screen_match:
        return screen_match

    # Plain chat / knowledge questions go straight to the brain — this is
    # what stops "explain cpu scheduling" becoming a CPU-usage readout.
    if _is_general_chat(text):
        return "brain", None

    if detect_pc_request(text):
        result = handle_pc_command(t)
        if result:
            return "pc", result

    # Tasks before marks/LMS: "mark X as done" contains "mark", "submit the
    # assignment task" contains "assignment".
    for detector in (detect_task_intent, detect_schedule_intent, detect_focus_intent,
                     detect_expense_intent, detect_alias_merchant_intent,
                     detect_alias_add_intent, detect_memory_intent):
        match = detector(text)
        if match:
            return match

    if detect_app_command(text):
        result = handle_pc_command(t)
        if result:
            return "pc", result

    is_data_question = _looks_like_data_question(text)

    if looks_like_whatsapp_request(text) and not is_data_question:
        return "whatsapp", {"raw_text": text}

    if looks_like_web_request(text) and not is_data_question:
        return "web", {"raw_text": text}

    if is_vtop_fetch_request(t) and has_marks_keyword(t):
        return "vtop_fetch_marks", {}

    if has_marks_keyword(t):
        return "vtop_marks", {}

    lms_type = detect_lms_query(t)
    if lms_type == "sync":
        return "lms_sync", {}
    if lms_type == "assignments":
        return "lms_assignments", {}

    if is_daily_brief_request(t):
        return "daily_brief", {}

    # Jarvis can't place phone calls. "call mom tomorrow" becomes a
    # reminder instead of a reply promising a call that never happens.
    m = _CALL_RE.match(_strip_lead_filler(t))
    if m:
        return "task_add", {"raw_text": f"remind me to {m.group(0)}", "note": "call"}

    return None


_DASHBOARD_RE = re.compile(
    r"^(?:open|show|bring up|pull up|launch|start)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:jarvis\s+)?"
    r"(?:dashboard|console|screen|window|ui|app)(?:\s+please)?$"
    r"|^(?:open|show)\s+(?:up\s+)?jarvis$"
)

_CALL_RE = re.compile(r"^(?:call|phone|ring|dial)\s+(?!me\b|it\b|this\b|that\b|a\b|an\b|the\b)[a-z]+.*$")


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
        if _has_any(t, keywords):
            return sem_id
    if _has_any(t, ["all sem", "all semester", "all sems",
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
        if _has_any(t, triggers):
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
        if _has_any(t, triggers):
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
    if _has_any(t, ["lab", "laboratory", "practical", "lab marks",
                    "lab assessment", "lab mark"]):
        return "lab"
    return "theory"

def extract_marks_intent(text: str) -> dict:
    t = text.lower()

    subject       = detect_subject(t)
    assessment    = normalize_assessment(t)
    semester      = detect_semester(t)
    course_filter = detect_course_filter(t)
    want_all      = _has_any(t, [
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
#   STAGE 2 — LLM JSON INTENT EXTRACTION
#   Only called when Stage 1 finds no match.
# ══════════════════════════════════════════

GROQ_INTENTS = {
    "attendance":       "attendance %, classes attended/missed, debarment risk",
    "bunk_check":       "can I skip/bunk a class and stay above 75%, safest class to skip",
    "timetable_today":  "today's classes",
    "timetable_tomorrow": "tomorrow's classes",
    "timetable_week":   "the whole week's classes",
    "next_class":       "my next class",
    "class_at_time":    "a course on a given day, a free time slot, or one named day's classes",
    "exams":            "exam dates — CAT/FAT schedule, next exam, days until",
    "cgpa":             "overall CGPA",
    "sem_gpa":          "GPA for one semester",
    "grade_history":    "grade in a course, or grade history",
    "cgpa_predict":     "CGPA if a NAMED course got a given grade",
    "grade_target":     "mark needed in a course/assessment for a target grade",
    "best_case_cgpa":   "CGPA if EVERY course this sem got the same grade",
    "overall_cgpa_target": "grade needed this sem to reach a target overall CGPA",
    "task_add":         "new personal reminder/to-do",
    "task_list":        "list my open personal tasks",
    "task_today":       "personal tasks due today/overdue",
    "task_complete":    "mark a personal task done",
    "task_drop":        "remove a personal task",
    "schedule_add":     "block time for an activity at a time",
    "schedule_today":   "today's plan: classes + blocks",
    "schedule_tomorrow": "tomorrow's plan",
    "schedule_week":    "this week's plan",
    "schedule_next":    "what's next right now",
    "schedule_free":    "when am I free",
    "schedule_delete":  "remove a scheduled block",
    "focus_start":      "start a focus/pomodoro session",
    "focus_stop":       "end the focus session",
    "focus_status":     "is a focus session running",
    "focus_stats":      "how much I studied recently",
    "expense_add":      "log money I spent",
    "expense_summary":  "how much I spent over a period",
    "expense_search":   "find past expenses by merchant",
    "alias_add":        "teach a course nickname",
    "alias_merchant":   "name an opaque UPI VPA",
    "remember_fact":    "asks Jarvis to remember a personal fact",
    "system_shutdown":  "shut down/exit Jarvis itself",
    "spotify":          "Spotify playback: play/open/pause/resume/skip/volume/now playing",
    "pc":               "control THIS PC: volume, mute, screenshot, brightness, open/close apps, battery/CPU/RAM/disk, clipboard, windows, shutdown/restart/sleep the PC",
    "screen":           "look at KK's screen right now: what's on it, read/explain/summarise something visible on it",
    "web":              "browse: open/search a website, compare sites, summarize/scroll/click the open page",
    "whatsapp":         "send a WhatsApp message to someone",
    "vtop_marks":       "my assessment marks (CAT/FAT/assignment) in a course",
    "vtop_fetch_marks": "refresh marks live from VTOP",
    "lms_assignments":  "my pending LMS/Moodle assignments and deadlines",
    "lms_sync":         "refresh assignments from LMS",
    "daily_brief":      "the daily/morning brief",
    "chat":             "anything else: conversation, jokes, opinions, advice, general or CS/academic knowledge questions",
}

EXTRACTION_PROMPT = """Classify a voice-assistant command for a VIT student (KK) into exactly ONE intent.
Pick a feature intent only when the user wants THEIR OWN data or an ACTION done. Questions about concepts,
how things work, advice, jokes and small talk are "chat" — even if they mention CPUs, memory, exams, marks or apps.

Intents:
{intent_list}

Recent turns (use only for follow-ups like "what about tomorrow"):
{recent_turns}

Entities (omit when absent):
- course: attendance, bunk_check, grade_history, cgpa_predict, grade_target, vtop_marks, class_at_time
- days_ahead (int, today=0 tomorrow=1): bunk_check
- day / time: class_at_time (a day alone = that day's classes)
- when (next|all|cat1|cat2|fat): exams
- target_grade: grade_target;  grade: cgpa_predict / best_case_cgpa (10=S 9=A 8=B 7=C 6=D 5=E)
- target_cgpa: overall_cgpa_target;  alias, course_query: alias_add;  fact: remember_fact
- action (play|open|pause|resume|next|previous|volume_up|volume_down|now_playing), query (song, play only): spotify
- query: task_complete / task_drop / schedule_delete

Reply with JSON only: {{"intent": "<name>", "entities": {{}}, "confidence": 0.0-1.0}}

Examples:
"attendance in DBMS" -> {{"intent": "attendance", "entities": {{"course": "DBMS"}}, "confidence": 0.95}}
"how close am I to 75 percent in CN" -> {{"intent": "attendance", "entities": {{"course": "CN"}}, "confidence": 0.9}}
"can I skip DBMS tomorrow" -> {{"intent": "bunk_check", "entities": {{"course": "DBMS", "days_ahead": 1}}, "confidence": 0.95}}
"do I have TOC on friday" -> {{"intent": "class_at_time", "entities": {{"course": "TOC", "day": "friday"}}, "confidence": 0.95}}
"what's my CGPA if I get A in DBMS" -> {{"intent": "cgpa_predict", "entities": {{"course": "DBMS", "grade": "A"}}, "confidence": 0.95}}
"if I get 9 in everything this sem" -> {{"intent": "best_case_cgpa", "entities": {{"grade": "A"}}, "confidence": 0.9}}
"put on something by the weeknd" -> {{"intent": "spotify", "entities": {{"action": "play", "query": "the weeknd"}}, "confidence": 0.9}}
"turn it down a bit" -> {{"intent": "pc", "entities": {{}}, "confidence": 0.8}}
"let amma know I'll be late" -> {{"intent": "whatsapp", "entities": {{}}, "confidence": 0.9}}
"find cheap flights to goa on skyscanner" -> {{"intent": "web", "entities": {{}}, "confidence": 0.9}}
"explain cpu scheduling" -> {{"intent": "chat", "entities": {{}}, "confidence": 0.95}}
"how should I prepare for CAT exams" -> {{"intent": "chat", "entities": {{}}, "confidence": 0.9}}
"what is virtual memory" -> {{"intent": "chat", "entities": {{}}, "confidence": 0.95}}
"i'm bored" -> {{"intent": "chat", "entities": {{}}, "confidence": 0.95}}
recent: "what's my attendance in DAA" then "what about my marks" -> {{"intent": "grade_history", "entities": {{"course": "DAA"}}, "confidence": 0.85}}

Input: "{user_input}"
"""


def extract_general_intent_groq(text: str) -> dict | None:
    """
    Stage 2. Returns {"intent", "entities", "confidence"}, or None when
    every model in the classifier chain failed (rate limit / outage) —
    route() then uses _offline_fallback instead of pretending the
    classifier said "chat".
    """
    from core.context import get_recent_turns

    intent_list = "\n".join(f"- {k}: {v}" for k, v in GROQ_INTENTS.items())
    recent = get_recent_turns(3)
    recent_turns = (
        "\n".join(
            f'- user said "{t["user_text"]}" -> intent={t["intent"]}, entities={t["entities"]}'
            for t in recent
        ) if recent else "(none)"
    )
    prompt = EXTRACTION_PROMPT.format(intent_list=intent_list, user_input=text.replace('"', "'"),
                                      recent_turns=recent_turns)

    try:
        data = complete_json([{"role": "user", "content": prompt}], max_tokens=120)
    except (LLMUnavailable, ValueError) as e:
        print(f"[router] Stage 2 intent extraction failed: {e}")
        return None

    intent = data.get("intent")
    if intent not in GROQ_INTENTS:
        intent = "chat"
    entities = data.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    return {"intent": intent, "entities": entities, "confidence": confidence}


# ══════════════════════════════════════════
#   MAIN ROUTE
# ══════════════════════════════════════════

# H.3: a deterministic backstop for course-entity inheritance — "what
# about my marks" right after an attendance-in-DAA question should carry
# course=DAA even if the LLM missed it.
_FOLLOWUP_MARKERS = (
    "what about", "how about", "and what about", "and for", "same for",
    "and my", "and in", "what abt", "and tomorrow", "and today",
)

def _looks_like_followup(text: str) -> bool:
    t = text.lower().strip()
    return any(t.startswith(m) or f" {m} " in f" {t} " for m in _FOLLOWUP_MARKERS)


# Below this, a feature intent is more likely a misread than a request —
# a wrong feature answer is worse than the brain answering directly.
MIN_INTENT_CONFIDENCE = 0.45

_TIME_WORDS = (
    "today", "tomorrow", "tonight", "this week", "next week",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TIME_PATTERN_RE = re.compile(r"\b\d{1,2}\s?(am|pm)\b", re.IGNORECASE)

def _has_time_word(text: str) -> bool:
    t = text.lower()
    return _has_any(t, _TIME_WORDS) or bool(_TIME_PATTERN_RE.search(t))


def _time_word_fallback(text: str) -> tuple | None:
    """Picks the schedule category the time word actually implies —
    "tomorrow" must not answer with today's schedule. Order matters:
    "tomorrow"/week before bare weekday names."""
    t = text.lower()
    if _has_any(t, ["tomorrow"]):
        return "schedule_tomorrow", {}
    if _has_any(t, ["this week", "next week"]):
        return "schedule_week", {}
    for day in _WEEKDAYS:
        if _has_any(t, [day]):
            return "class_at_time", {"day": day}
    if _has_time_word(t):
        return "schedule_today", {}
    return None


_KEYWORD_DATA_FALLBACKS = [
    (("attendance", "bunk", "skip class", "safe to skip"), "attendance"),
    (("cgpa", "grade point average"), "cgpa"),
    (("exam", "exams", "cat1", "cat2", "cat-1", "cat-2", "cat 1", "cat 2", "fat"), "exams"),
    (("timetable", "time table", "class schedule", "next class", "my classes"), "timetable_today"),
]

def _offline_fallback(text: str) -> tuple:
    """Used only when every classifier model is unreachable. Handles
    obvious personal data questions by keyword; everything else goes to
    the brain (which has its own model fallback chain)."""
    t = text.lower()
    # Only short, personal data questions ("when's my next exam") go to a
    # feature by keyword. Knowledge/advice questions and long musings that
    # merely mention a keyword ("what is a CAT exam at VIT", "tips to
    # prepare for CAT2", a career ramble mentioning exams) used to be sent
    # to the exams feature whenever the classifier was rate-limited.
    looks_like_data_question = (
        len(t.split()) <= 12
        and _PERSONAL_RE.search(t)
        and not _is_impersonal_question(t)
        and not _has_any(t, ("tips", "tip", "prepare", "preparation", "plan", "how to", "how should", "advice",
                             "what is", "what are", "explain"))
    )
    for keywords, category in _KEYWORD_DATA_FALLBACKS:
        if looks_like_data_question and _has_any(t, keywords):
            return category, {}
    if _PERSONAL_RE.search(t) and _has_any(t, ("schedule", "class", "classes", "free", "plan")):
        match = _time_word_fallback(t)
        if match:
            return match
    return "brain", None


# "which one is after that" / "the next one" right after an exam or
# schedule answer continues that list — the classifier read it as
# "next class".
_ORDINAL_FOLLOWUP_RE = re.compile(r"\b(?:after that|after this|the one after|next one|which one|then what|what else)\b")
_ORDINAL_FOLLOWUP_INTENTS = {"exams", "schedule_next", "next_class", "lms_assignments"}

# Intents where "what about <course>" means "same question, other course".
_COURSE_FOLLOWUP_INTENTS = {"attendance", "bunk_check", "grade_history", "vtop_marks", "exams", "class_at_time"}
_FOLLOWUP_STRIP_RE = re.compile(r"^(?:and\s+)?(?:what|how)\s+(?:about|abt)\s+|^(?:and\s+)?(?:same\s+)?for\s+|^and\s+(?:in\s+)?")


def _course_followup(text: str) -> tuple | None:
    """"what about compiler design" right after an attendance question
    re-asks the attendance question for that course. Left to the
    classifier it was read as a grade-history question."""
    from core.context import get_last_intent
    last = get_last_intent()
    t = text.lower().strip()
    if last in _ORDINAL_FOLLOWUP_INTENTS and _ORDINAL_FOLLOWUP_RE.search(t) and len(t.split()) <= 7:
        return last, {"when": "next"} if last == "exams" else {}
    if not _looks_like_followup(text):
        return None
    if last not in _COURSE_FOLLOWUP_INTENTS:
        return None
    rest = _FOLLOWUP_STRIP_RE.sub("", text.lower().strip()).strip(" ?.!")
    if not rest or len(rest.split()) > 5:
        return None
    from core.course_resolver import resolve_course_best
    best = resolve_course_best(rest)
    if best:
        return last, {"course": best["course_name"]}
    return None


def route(text: str) -> tuple:
    """
    Returns (category, payload). `category` is looked up in server.py's
    INTENT_HANDLERS registry; "brain" means plain conversation.
    """
    fast_result = fast_path_route(text) or _course_followup(text)
    if fast_result:
        category, payload = fast_result
    else:
        classified = extract_general_intent_groq(text)
        if classified is None:
            category, payload = _offline_fallback(text)
        elif classified["intent"] == "chat" or classified["confidence"] < MIN_INTENT_CONFIDENCE:
            return "brain", None
        else:
            category, payload = classified["intent"], classified["entities"]

    # Safety net, independent of which path classified this:
    # system_shutdown must never fire unless "jarvis" is actually named.
    # Demoting to "pc" lands on pc_control's confirm-gated flow (or, if
    # nothing PC-related is in the text, falls back to chat).
    if category == "system_shutdown" and "jarvis" not in text.lower():
        category, payload = "pc", {}

    if isinstance(payload, dict) and not payload.get("course") and _looks_like_followup(text):
        from core.context import get_last_entity
        inherited = get_last_entity("course")
        if inherited:
            payload = {**payload, "course": inherited}

    return category, payload

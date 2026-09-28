"""
brain.py — Jarvis AI brain.

Flow for marks:
  1. Router detects marks query → extracts intent
  2. ask_groq_marks() checks SQLite
  3. If data missing → returns __NEEDS_VTOP_FETCH__
  4. jarvis.py handles the fetch, then calls ask_groq_marks() again
  5. Groq reads exact data from prompt and answers naturally

Two kinds of call live here:
  - ask_groq(): the conversational reply, with persona, rolling history
    and (only when relevant) recalled memory.
  - ask_oneshot(): a stateless task ("summarize this page", "say these
    marks in one sentence"). These used to go through ask_groq too, which
    dumped 4 KB page dumps into the chat history and made the next few
    normal replies drift toward whatever page had been summarized.
"""

import os
import re
import sys
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import complete, complete_json, LLMUnavailable

# ── System Prompt ──────────────────────────────────────
SYSTEM_PROMPT = """You are Jarvis, the personal voice assistant of Krishna Kumar (KK).

ABOUT KK (background only — do not bring these up unless he does):
- 19, CSE student at VIT Chennai, 2028 batch, currently Semester 5
- Codes in C, C++, Python, Java; learning DSA; aiming for a strong product-company placement
- Into gym, cars, money/investing and AI

PERSONALITY:
- Sharp, witty, slightly dry — Iron Man's Jarvis, not a chirpy chatbot
- Talks like a brilliant friend; may call him "boss" now and then, not every line

HOW TO ANSWER:
1. Answer the literal question in the LATEST message first. If it asks several things, answer each.
2. The latest message is authoritative. Use earlier turns only when it is clearly a follow-up
   ("what about tomorrow", "why is it so low", "explain that again").
3. Length: casual chat 1-2 sentences. Explanations/how-to: as long as needed to be correct and
   useful, but tight — no filler, no restating the question, no closing offers like "let me know if...".
4. Your replies are often spoken aloud: no markdown headers, tables or emoji. Use a short
   numbered list only when steps or options genuinely need it.
5. No UNSOLICITED advice, lectures or motivational lines. When he asks for motivation, advice or
   a plan, give it — short, concrete and personal, never a refusal.
6. If a request is ambiguous, pick the most likely meaning and answer it; ask one short
   question only if guessing wrong would be costly.
7. If you can't do something, say so in one short sentence and offer the closest thing you can do.
   Never mention error codes or internal names.
8. Thanks / greetings / goodbyes: reply in a few natural words ("Anytime, boss."). Don't pivot to offering help.
9. You cannot perform actions from chat. Never say you've done or will do something (set a reminder,
   send a message, call someone, play a song). Instead give the exact phrase that does it, e.g.
   "Say 'remind me to call mom tomorrow at 6' and I'll set it."
10. Write exactly ONE reply to the latest message. Never continue past your own question, never write
   KK's side of the conversation, never answer a question you just asked him.
11. Formatting: plain sentences. No LaTeX, no markdown bold/headers/tables. Math in plain words or plain
   text (e.g. "GPA = sum of credit x grade point / total credits"). Code only when he asks for code,
   in one fenced block.

FACTS ABOUT KK'S OWN RECORDS (hard rule):
- You only know his marks, attendance, timetable, assignments, exams, tasks or expenses when a
  STUDENT DATA block or an earlier assistant turn in this conversation actually contains them.
- Never invent a course, grade, percentage, date, deadline or assignment title. If it isn't in
  the provided data, say you don't have it pulled up and name the command that fetches it
  (e.g. "ask 'what's my attendance'", "say 'sync from vtop'").
- General knowledge (how VIT grading works, what a CAT is, CS concepts, study techniques) is fine.

VIT FACTS (use these, don't improvise):
- CAT = Continuous Assessment Test (CAT1, CAT2, mid-semester). FAT = Final Assessment Test (end semester).
  DA = Digital Assignment. Theory course codes end in L, lab/practical codes end in P.
- Grades are letters with fixed grade points: S=10, A=9, B=8, C=7, D=6, E=5, F=0 (fail).
- GPA/CGPA = sum(course credits x grade points) / sum(course credits). Not percentage bands.
- Attendance below 75% in a course risks debarment from its FAT.

WHEN STUDENT DATA IS PROVIDED:
- Copy exact numbers, don't round. N/A means not published yet.
- Theory and lab courses are separate; present them separately.
- Attendance maths (every class attended OR missed also adds to the total): with a attended of t,
  classes needed to reach 75% = ceil((0.75*t - a) / 0.25); classes you can still miss and stay at
  or above 75% = floor((a - 0.75*t) / 0.75). Compute, don't estimate.
- Semester IDs: Sem1=CH20242501, Sem2=CH20242505, Sem3=CH20252601, Sem4=CH20252605, Sem5=CH20262701 (current).
- Abbreviations: STS/BSTS=quant/soft skills, TOC=Theory of Computation, DMGT=Discrete Maths & Graph Theory,
  CN=Computer Networks, DBMS=Database Systems.
"""

# ── Conversation history (RAM) ─────────────────────────
conversation_history = []
MAX_HISTORY_TURNS    = 6    # 6 exchanges = 12 messages
# After this long with no exchange, the old thread is dropped: a question
# asked after lunch shouldn't be answered in the light of this morning's
# unrelated topic (a confirmed source of off-topic replies).
HISTORY_IDLE_RESET_S = 20 * 60
_last_exchange_at    = 0.0


_SENTENCE_START_RE = re.compile(r"(?<=[.!?])\s*(?=[A-Z\"'“‘])")
# "...each?Sure, just say..." / "...lower.The lab..." — sentence punctuation
# glued straight onto a capitalised word is never normal prose; it is
# gpt-oss starting a second message (often answering its own question).
_GLUED_RESTART_RE = re.compile(r"(?<=[a-z0-9)”\"’%][.!?])(?=[A-Z][a-z])")

def _cut_restarted_answer(text: str) -> str:
    """gpt-oss occasionally restarts its answer mid-reply, repeating the
    opening sentences ("...95%.That's why...The lab has fewer..."), which
    then gets spoken twice. Cut at the first sentence that repeats an
    earlier one; anything without a repeat is returned untouched."""
    from rapidfuzz import fuzz
    glued = _GLUED_RESTART_RE.search(text)
    if glued and glued.start() >= 5:
        text = text[:glued.start()].rstrip()
    seen = []
    for m in [None, *_SENTENCE_START_RE.finditer(text)]:
        start = m.end() if m else 0
        nxt = _SENTENCE_START_RE.search(text, start + 1)
        key = re.sub(r"\W+", " ", text[start:nxt.start() if nxt else len(text)].lower()).strip()
        if len(key) >= 25 and any(key[:40] == k[:40] or fuzz.ratio(key, k) >= 85 for k in seen):
            return text[:start].rstrip()
        seen.append(key)
    return text


_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_LATEX_BLOCK_RE = re.compile(r"\\\[.*?\\\]|\$\$.*?\$\$", re.DOTALL)
SPOKEN_MAX_CHARS = 420


def to_spoken(text: str) -> str:
    """What TTS should say for a chat reply: no markdown, no LaTeX, no
    code read out symbol by symbol, and long explanations trimmed to their
    opening sentences (the full text stays on screen)."""
    t = _CODE_BLOCK_RE.sub(" I've put the code on screen. ", text or "")
    t = _LATEX_BLOCK_RE.sub(" (formula on screen) ", t)
    t = re.sub(r"\\\(|\\\)|\\[a-zA-Z]+|[{}]", " ", t)
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > SPOKEN_MAX_CHARS:
        cut = t.rfind(". ", 0, SPOKEN_MAX_CHARS)
        t = (t[:cut + 1] if cut > 80 else t[:SPOKEN_MAX_CHARS].rsplit(" ", 1)[0] + "…") + " The rest is on screen."
    return t


def _now_line() -> str:
    return datetime.now().strftime("Current date/time: %A, %d %B %Y, %I:%M %p (Asia/Kolkata).")


def _expire_stale_history():
    global conversation_history
    if conversation_history and time.time() - _last_exchange_at > HISTORY_IDLE_RESET_S:
        conversation_history = []


def _remember_exchange(user_text: str, assistant_text: str):
    global conversation_history, _last_exchange_at
    conversation_history.append({"role": "user", "content": user_text})
    conversation_history.append({"role": "assistant", "content": assistant_text})
    conversation_history = conversation_history[-(MAX_HISTORY_TURNS * 2):]
    _last_exchange_at = time.time()


def note_feature_exchange(user_text: str, result_text: str):
    """Records a turn answered by a feature handler (attendance, schedule,
    Spotify...) so a follow-up chat question like "why is it so low?" or
    "which of those is hardest?" knows what "it"/"those" refers to.
    Previously only chat turns were kept, so every follow-up to real data
    was answered blind."""
    _expire_stale_history()
    summary = (result_text or "").strip()
    if len(summary) > 600:
        summary = summary[:600] + " …"
    if summary:
        _remember_exchange(user_text, summary)


# ══════════════════════════════════════════════════════════
#   GROQ MARKS INTENT EXTRACTOR
# ══════════════════════════════════════════════════════════

def extract_marks_intent_groq(user_input: str) -> dict:
    """
    When keyword router can't identify subject, ask Groq.
    Injects subject list from DB so Groq can match any shorthand.
    Returns {subject, assessment, semester}.
    """
    from core.memory import get_subject_search_hint
    empty = {"subject": None, "assessment": None, "semester": None}
    hint = get_subject_search_hint()
    if not hint:
        return empty

    prompt = (
        f"{hint}\n\n"
        "Extract from the query. Reply with JSON only: "
        '{"subject": COURSE_CODE or null, "assessment": TYPE or null, "semester": SEM_ID or null}\n'
        "- subject: a course code from the list above, e.g. BCSE304L\n"
        "- assessment: CAT1, CAT2, FAT, Assignment-1, Assessment-1, all\n"
        "- semester: CH20242501(sem1), CH20242505(sem2), CH20252601(sem3), CH20252605(sem4), "
        "CH20262701(sem5/current), all\n"
        "- shorthand: dmgt/discrete→BMAT205L, toc/computation→BCSE304L, cn/networks→BCSE308L, "
        "dbms/database→BCSE302L\n"
        f"\nQuery: {user_input}"
    )
    try:
        data = complete_json([{"role": "user", "content": prompt}], max_tokens=60)
    except Exception:
        return empty
    result = dict(empty)
    for k in result:
        v = data.get(k)
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("none", "null"):
            result[k] = v.strip()
    return result


# ══════════════════════════════════════════════════════════
#   MARKS CONTEXT BUILDER
# ══════════════════════════════════════════════════════════

def build_marks_context(intent: dict, user_input: str) -> str:
    """
    Builds marks data string for injection into Groq prompt.
    Returns __FETCH_REQUIRED__ if no data in DB at all.
    """
    from core.memory import (
        get_all_marks_summary, get_all_sems_marks_summary,
        get_subject_marks, get_specific_assessment,
        get_subject_search_hint, get_marks_synced_sems
    )

    # If DB is completely empty
    if not get_marks_synced_sems():
        return "__FETCH_REQUIRED__"

    hint       = get_subject_search_hint()
    subject    = intent.get("subject")
    assessment = intent.get("assessment")
    semester   = intent.get("semester")
    want_all   = intent.get("want_all", False)

    # All semesters requested
    if semester == "all" or want_all:
        data = get_all_sems_marks_summary()
        return (hint + "\n\n" + data) if data else "__FETCH_REQUIRED__"

    # No subject from keyword router — try Groq extraction
    if not subject:
        groq_intent = extract_marks_intent_groq(user_input)
        subject    = subject    or groq_intent.get("subject")
        assessment = assessment or groq_intent.get("assessment")
        if not semester:
            semester = groq_intent.get("semester")

    # All sems from groq
    if semester == "all":
        data = get_all_sems_marks_summary()
        return (hint + "\n\n" + data) if data else "__FETCH_REQUIRED__"

    # Specific subject + specific assessment
    if subject and assessment and assessment.lower() not in ("all", "none"):
        data = get_specific_assessment(subject, assessment, semester)
        return hint + "\n\n" + data

    # Specific subject, all its assessments
    if subject:
        data = get_subject_marks(subject, semester)
        return hint + "\n\n" + data

    # No subject — return full semester summary
    data = get_all_marks_summary(semester)
    if data:
        return hint + "\n\n" + data

    return "__FETCH_REQUIRED__"


# ══════════════════════════════════════════════════════════
#   MAIN GROQ CALL
# ══════════════════════════════════════════════════════════

_FALLBACK_REPLY = "Give me a sec, boss — my language model isn't answering right now. Try that again in a moment."


def ask_groq(user_input: str, extra_context: str = "", max_tokens_override: int = None) -> str:
    """
    Core conversational call. extra_context = marks data, VTOP data etc.
    Conversation history capped at MAX_HISTORY_TURNS.
    """
    from core.memory import build_context, save_conversation

    _expire_stale_history()

    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + _now_line()}]

    # Recalled long-term memory — build_context only returns entries that
    # are actually close to this message (see its relevance cut-off).
    if not extra_context:
        try:
            from core.graph import get_graph
            mem = get_graph().recall(user_input)
        except Exception as e:
            print(f"[brain] memory graph recall failed, using plain search: {e}")
            mem = build_context(user_input)
        if mem:
            messages.append({
                "role": "system",
                "content": "Possibly relevant memory (use only if it helps answer the latest message):\n" + mem,
            })

    if extra_context:
        messages.append({
            "role":    "system",
            "content": (
                "STUDENT DATA — read the exact numbers from this, do not guess:\n"
                f"{extra_context}"
            )
        })

    messages += conversation_history
    messages.append({"role": "user", "content": user_input})

    try:
        reply = _cut_restarted_answer(
            complete(messages, role="chat", max_tokens=max_tokens_override or 450, temperature=0.5))
    except LLMUnavailable as e:
        print(f"[brain] chat call failed on every model: {e}")
        return _FALLBACK_REPLY

    _remember_exchange(user_input, reply)

    # Persist to SQLite (only meaningful turns)
    if len(user_input.split()) > 3:
        try:
            save_conversation(user_input, reply)
        except Exception as e:
            print(f"[brain] save_conversation failed: {e}")

    return reply


def ask_oneshot(prompt: str, max_tokens: int = 200, fallback: str = "") -> str:
    """Stateless single-turn task (summaries, phrasing a data readout).
    Doesn't read or write conversation history."""
    messages = [
        {"role": "system", "content": "You are Jarvis, a concise voice assistant. Plain spoken sentences, no markdown, no emoji."},
        {"role": "user", "content": prompt},
    ]
    try:
        return complete(messages, role="chat", max_tokens=max_tokens, temperature=0.3)
    except LLMUnavailable as e:
        print(f"[brain] one-shot call failed: {e}")
        return fallback


# ══════════════════════════════════════════════════════════
#   MARKS-SPECIFIC BRAIN
# ══════════════════════════════════════════════════════════

def ask_groq_marks(user_input: str, intent: dict) -> str:
    """
    Handles marks queries.
    Returns __NEEDS_VTOP_FETCH__ if DB doesn't have required data.
    """
    from core.memory import get_marks_synced_sems, get_marks_fresh_enough, CURRENT_SEM

    synced      = get_marks_synced_sems()
    semester    = intent.get("semester")

    # Nothing synced at all
    if not synced:
        return "__NEEDS_VTOP_FETCH__"

    # Specific semester requested but not in DB
    if semester and semester not in ("all", None) and semester not in synced:
        return "__NEEDS_VTOP_FETCH__"

    # Current sem data is stale (older than 12 hours)
    if CURRENT_SEM in synced and not get_marks_fresh_enough(12, CURRENT_SEM):
        return "__NEEDS_VTOP_FETCH__"

    context = build_marks_context(intent, user_input)

    if context == "__FETCH_REQUIRED__":
        return "__NEEDS_VTOP_FETCH__"

    return ask_groq(user_input, extra_context=context)


def clear_history():
    global conversation_history
    conversation_history = []

"""
brain.py — Jarvis AI brain.

Flow for marks:
  1. Router detects marks query → extracts intent
  2. ask_groq_marks() checks SQLite
  3. If data missing → returns __NEEDS_VTOP_FETCH__
  4. jarvis.py handles the fetch, then calls ask_groq_marks() again
  5. Groq reads exact data from prompt and answers naturally
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

groq_client = Groq(api_key=GROQ_API_KEY)

# ── System Prompt ──────────────────────────────────────
SYSTEM_PROMPT = """You are Jarvis, the personal AI assistant of Krishna Kumar (KK).

ABOUT KK:
- 19 years old, CSE student at VIT Chennai, 2028 batch (currently 5th semester / Sem 5)
- Goes to gym, values fitness
- Night owl trying to wake up early
- Wants 25+ LPA placement at product company
- Codes in C, C++, Python, Java — learning DSA
- Interested in cars, money, investing, AI

YOUR PERSONALITY:
- Sharp, witty, slightly sarcastic — like Iron Man's Jarvis
- Talk like a brilliant friend, not a robot
- Call KK casually, use "boss" occasionally
- Never say you are an AI or say "I cannot"

RESPONSE RULES:
- Keep casual responses SHORT — 1 to 3 sentences max
- For marks/data queries — be precise, read ONLY from the provided data
- NEVER guess or make up marks. If data is provided, read exact numbers from it
- If marks show N/A, say it hasn't been published yet
- Never bring up DSA, gym, goals unless KK mentions them
- Never lecture or give unsolicited advice
- Be natural and conversational

NEVER INVENT SPECIFIC FACTS (critical — this is a hard rule, not a
suggestion):
- You reach this general-chat path ONLY when no real data was injected
  below (marks/attendance/schedule/etc. all inject their own
  STUDENT DATA block into a separate system message when they have
  something to read from). If there is no STUDENT DATA block in this
  conversation, you have ZERO real information about KK's actual
  courses, assignments, grades, attendance numbers, exam dates, or
  deadlines right now — not from earlier terms, not "probably," none.
- NEVER state a specific course name, assignment title, due date, grade,
  percentage, or exam date unless it was explicitly given to you in an
  injected data block THIS turn. Confirmed failure mode: asked about
  pending assignments with no data injected, a past version of this
  prompt fabricated a plausible-sounding but completely made-up
  assignment name and exam — this must never happen again.
- If KK asks a factual question about his academic data and nothing was
  injected, say plainly that you don't have that pulled up right now and
  suggest the specific command that would fetch it (e.g. "say 'sync from
  vtop'" or "ask again and I'll check") — do NOT guess, do NOT reuse
  something that sounds plausible from earlier in the conversation
  unless it was ACTUALLY provided as data in that earlier turn.
- Generic knowledge (how VIT's grading scale works, what a CAT is, study
  advice) is fine — the line is specific facts about KK's own records.

WHEN SOMETHING GOES WRONG OR IS AMBIGUOUS (H.5):
- Acknowledge the hiccup in passing, then keep moving — never dwell on
  an error or apologize more than once for the same thing
- If a request is ambiguous (could mean 2+ things), give a short
  numbered list of the top options instead of asking an open-ended
  "what do you mean?"
- Never leave KK at a dead end — if you can't do the exact thing asked,
  say so briefly and offer the closest alternative you CAN do
- Never surface a raw error code, key, or internal status string —
  always translate it into a plain sentence first

MARKS READING RULES (critical):
- Semester 1 = Fall 2024-25 = CH20242501
- Semester 2 = Winter 2024-25 = CH20242505
- Semester 3 = Fall 2025-26 = CH20252601
- Semester 4 = Winter 2025-26 = CH20252605
- Semester 5 = Fall 2026-27 = CH20262701 (current)
- When marks data is injected, copy exact numbers — do not round or estimate
- If theory and lab are separate subjects, present them separately
- STS/BSTS = quantitative/stats subject
- TOC = Theory of Computation
- DMGT = Discrete Mathematics and Graph Theory
- CN = Computer Networks
- DBMS = Database Systems
"""

# ── Conversation history (RAM) ─────────────────────────
conversation_history  = []
MAX_HISTORY_TURNS     = 6   # 6 exchanges = 12 messages


# ══════════════════════════════════════════════════════════
#   GROQ INTENT CLASSIFIER
# ══════════════════════════════════════════════════════════

def classify_intent(user_input: str) -> str:
    """
    Used as fallback when keyword router is unsure.
    One tiny Groq call, 10 tokens max.
    """
    try:
        r = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Classify into exactly one word: marks, attendance, timetable, exam, grades, general\n"
                    f"Query: {user_input}"
                )
            }],
            max_tokens=5,
            temperature=0
        )
        result = r.choices[0].message.content.strip().lower()
        return result if result in {"marks","attendance","timetable","exam","grades","general"} else "general"
    except Exception:
        return "general"


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
    hint = get_subject_search_hint()
    if not hint:
        return {"subject": None, "assessment": None, "semester": None}

    try:
        r = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    f"{hint}\n\n"
                    "Extract from query. Reply EXACTLY in this format, nothing else:\n"
                    "subject=COURSE_CODE, assessment=TYPE, semester=SEM_ID\n\n"
                    "Rules:\n"
                    "- subject: course code like BCSE304L, or 'none'\n"
                    "- assessment: CAT1, CAT2, FAT, Assignment-1, Assessment-1, all, none\n"
                    "- semester: CH20242501(sem1), CH20242505(sem2), CH20252601(sem3), CH20252605(sem4), CH20262701(sem5/current), all, none\n"
                    "- 'calculus' → search titles containing 'calculus' → use matching course code\n"
                    "- 'discrete','dmgt' → BMAT205L\n"
                    "- 'toc','computation' → BCSE304L\n"
                    "- 'cn','networks' → BCSE308L\n"
                    "- 'dbms','database' → BCSE302L\n"
                    f"\nQuery: {user_input}"
                )
            }],
            max_tokens=30,
            temperature=0
        )
        text   = r.choices[0].message.content.strip()
        result = {"subject": None, "assessment": None, "semester": None}
        for part in text.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v.lower() in ("none", ""):
                    v = None
                result[k] = v
        return result
    except Exception:
        return {"subject": None, "assessment": None, "semester": None}


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

def ask_groq(user_input: str, extra_context: str = "", max_tokens_override: int = None) -> str:
    """
    Core Groq call. extra_context = marks data, VTOP data etc.
    Conversation history capped at MAX_HISTORY_TURNS.
    """
    from core.memory import build_context, save_conversation

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # General semantic memory (only for non-marks queries)
    if not extra_context:
        mem = build_context(user_input)
        if mem:
            messages.append({"role": "system", "content": f"Context:\n{mem}"})

    # Marks / VTOP data
    if extra_context:
        messages.append({
            "role":    "system",
            "content": (
                "STUDENT DATA — read the exact numbers from this, do not guess:\n"
                f"{extra_context}"
            )
        })

    # Rolling conversation history
    trimmed = conversation_history[-(MAX_HISTORY_TURNS * 2):]
    messages += trimmed
    messages.append({"role": "user", "content": user_input})

    max_tok = max_tokens_override if max_tokens_override else 250
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=max_tok,
        temperature=0.7
    )
    reply = response.choices[0].message.content.strip()

    # Save to history
    conversation_history.append({"role": "user",      "content": user_input})
    conversation_history.append({"role": "assistant",  "content": reply})

    # Persist to SQLite (only meaningful turns)
    if len(user_input.split()) > 3:
        save_conversation(user_input, reply)

    return reply


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
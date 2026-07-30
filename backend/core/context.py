"""
core/context.py — cross-turn conversation context (H.3).

This is a single-user local assistant — there's exactly one running
conversation per backend process, not one per HTTP client — so a single
global rolling window of recent turns is the right model, not a
per-session-ID store.

Used two ways:
  1. Injected into the Stage 2 Groq prompt (core.router.
     extract_general_intent_groq) so a follow-up like "what about
     tomorrow" or "and my marks" can inherit the previous turn's
     intent/entities instead of being classified blind.
  2. A cheaper, LLM-independent fallback: core.router.route() merges in
     the last-mentioned course when the current utterance is a clear
     follow-up phrase (see _looks_like_followup) and didn't name its
     own course — so inheritance doesn't depend on the LLM noticing.

5-minute TTL: a turn older than that is treated as expired (the
conversation likely moved to a new subject) and excluded from what's
returned, even though it stays in the deque until MAX_TURNS evicts it.
"""

from collections import deque
from datetime import datetime, timedelta

MAX_TURNS = 6
TTL_MINUTES = 5

_turns = deque(maxlen=MAX_TURNS)


def record_turn(user_text: str, intent: str, entities: dict, response_summary: str = ""):
    _turns.append({
        "user_text": user_text,
        "intent": intent,
        "entities": dict(entities or {}),
        "response_summary": (response_summary or "")[:200],
        "at": datetime.now(),
    })


def _active_turns() -> list:
    cutoff = datetime.now() - timedelta(minutes=TTL_MINUTES)
    return [t for t in _turns if t["at"] >= cutoff]


def get_recent_turns(n: int = 3) -> list:
    """Most recent `n` still-fresh turns, oldest first."""
    return _active_turns()[-n:]


def get_last_entity(key: str):
    """Most recent non-empty value for `key` across still-fresh turns.
    None if nothing fresh has it."""
    for t in reversed(_active_turns()):
        v = t["entities"].get(key)
        if v:
            return v
    return None


def get_last_intent():
    active = _active_turns()
    return active[-1]["intent"] if active else None


def clear():
    _turns.clear()

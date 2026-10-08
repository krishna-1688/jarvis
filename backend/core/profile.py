"""
core/profile.py — who Jarvis is talking to, and how they like it.

Everything personal lives in backend/profile.toml (git-ignored; written by
setup.py or the dashboard's "You" lens, template in profile.example.toml):
name and how to be addressed, tone and reply length, language, dreams,
ambitions and what motivates them, daily routine and favourite things,
and optional college (VIT) settings. Nothing about the user is hardcoded
anywhere else — prompts, spoken replies and the dashboard all read it
from here. The file is re-read when it changes, so edits apply live.
"""

import os
import threading
import tomllib

PROFILE_PATH = os.environ.get("JARVIS_PROFILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profile.toml")

DEFAULTS = {
    "you": {"name": "friend", "call_me": "boss", "about": ""},
    "style": {"tone": "witty", "reply_length": "short", "language": "English", "humour": True},
    "goals": {"dream": "", "ambitions": [], "motivation": ""},
    "routine": {"wake_time": "", "sleep_time": "", "study_time": "", "habits": [], "favourites": []},
    "college": {"campus": "chennai", "admission_year": 0, "current_sem": 0},
    "voice": {"wake_reply": "Yes {call_me}?", "tts_voice": "en-GB-RyanNeural"},
}

TONES = {
    "witty": "Sharp, witty and slightly dry — Iron Man's Jarvis, not a chirpy chatbot. Talks like a brilliant friend.",
    "friendly": "Warm, upbeat and encouraging — like a close friend who is genuinely on their side.",
    "formal": "Polite, precise and composed — a trusted butler. No slang, no jokes unless invited.",
    "tough-love": "Direct and no-nonsense. Calls out procrastination and excuses, pushes them to act now — never cruel.",
    "calm": "Calm, gentle and reassuring. Never rushed, never harsh; good at lowering stress.",
}
LENGTHS = {
    "short": "Casual chat: 1-2 sentences. Explanations: as long as needed, but tight.",
    "balanced": "Casual chat: 2-3 sentences. Explanations: complete, with an example when it helps.",
    "detailed": "Casual chat: a few sentences. Explanations: thorough, step by step, with examples.",
}

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}


def _merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update({kk: vv for kk, vv in v.items() if vv is not None})
        else:
            out[k] = v
    return out


def get() -> dict:
    """The full profile, defaults filled in. Cheap: re-read only when the
    file's modification time changes."""
    try:
        mtime = os.path.getmtime(PROFILE_PATH)
    except OSError:
        mtime = None
    with _lock:
        if _cache["data"] is None or mtime != _cache["mtime"]:
            data = {}
            if mtime is not None:
                try:
                    with open(PROFILE_PATH, "rb") as f:
                        data = tomllib.load(f)
                except (OSError, tomllib.TOMLDecodeError) as e:
                    print(f"[profile] couldn't read {PROFILE_PATH} ({e}); using defaults")
            _cache.update(mtime=mtime, data=_merge(DEFAULTS, data))
        return _cache["data"]


def name() -> str:
    return (get()["you"].get("name") or "friend").strip()


def call_me() -> str:
    """How Jarvis addresses the user out loud ("boss", "sir", their name, or "")."""
    return (get()["you"].get("call_me") or "").strip()


def wake_reply() -> str:
    text = get()["voice"].get("wake_reply") or "Yes {call_me}?"
    reply = text.replace("{call_me}", call_me()).replace("{name}", name())
    return " ".join(reply.replace(" ?", "?").split()) or "Yes?"


def _items(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value or "").strip()


def persona_prompt() -> str:
    """The personal part of the chat system prompt: who they are, how they
    like to be spoken to, and what they're working towards."""
    p = get()
    you, style, goals, routine = p["you"], p["style"], p["goals"], p["routine"]
    who = name()
    lines = [f"You are Jarvis, the personal voice assistant of {who}."]

    about = [f"- {you['about'].strip()}"] if you.get("about", "").strip() else []
    for label, key in (("Dream", "dream"), ("Ambitions", "ambitions"), ("What drives them", "motivation")):
        val = _items(goals.get(key))
        if val:
            about.append(f"- {label}: {val}")
    routine_bits = [f"{label} {routine[key]}" for label, key in
                    (("wakes around", "wake_time"), ("sleeps around", "sleep_time"), ("studies", "study_time"))
                    if str(routine.get(key, "")).strip()]
    if routine_bits:
        about.append("- Routine: " + "; ".join(routine_bits))
    for label, key in (("Habits", "habits"), ("Favourite things", "favourites")):
        val = _items(routine.get(key))
        if val:
            about.append(f"- {label}: {val}")
    if about:
        lines += ["", f"ABOUT {who.upper()} (background only — don't bring it up unless it's relevant):", *about]

    tone = TONES.get(str(style.get("tone", "witty")).lower(), TONES["witty"])
    address = call_me()
    lines += ["", "PERSONALITY:", f"- {tone}"]
    lines.append(f'- May call them "{address}" now and then, not every line.' if address
                 else "- Don't use a nickname for them; their name now and then is fine.")
    if not style.get("humour", True):
        lines.append("- Keep humour out of it.")
    lines.append(f"- {LENGTHS.get(str(style.get('reply_length', 'short')).lower(), LENGTHS['short'])}")
    language = str(style.get("language") or "English").strip()
    if language.lower() != "english":
        lines.append(f"- Reply in {language}.")
    if goals.get("dream") or goals.get("ambitions") or goals.get("motivation"):
        lines.append("- When they ask for motivation, advice or a plan, tie it to their dream and ambitions — "
                     "concretely, not as a lecture.")
    return "\n".join(lines)


def personalize(text: str) -> str:
    """Spoken/displayed replies were written saying "boss"; swap in how this
    user wants to be addressed (or drop it gracefully)."""
    import re
    if not text or "boss" not in text.lower():
        return text
    address = call_me()
    if address.lower() == "boss":
        return text
    # Only "boss" used to ADDRESS the user is swapped — ", boss." / "Boss, ..."
    # / "Yes boss?" — never content like "your boss at work".
    tail = r",\s*boss(?=\s*(?:[.!?,;:—–-]|$))"
    head = r"(^|[.!?]\s+)Boss,\s*"
    after_word = r"\b(Yes|Sure|Okay|Right|Thanks|Morning|Evening|Night|Hey|Hi|Hello)\s+boss\b"
    if not address:
        text = re.sub(tail, "", text, flags=re.IGNORECASE)
        text = re.sub(after_word, r"\1", text, flags=re.IGNORECASE)
        text = re.sub(head, lambda m: m.group(1), text)
        # "Boss, your exam..." -> "Your exam...": re-capitalise sentence starts.
        return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    cap = address[:1].upper() + address[1:]
    text = re.sub(tail, f", {address}", text, flags=re.IGNORECASE)
    text = re.sub(after_word, lambda m: f"{m.group(1)} {address}", text, flags=re.IGNORECASE)
    return re.sub(head, lambda m: f"{m.group(1)}{cap}, ", text)


# ── editing (dashboard / setup wizard) ────────────────────────────────

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(str(x)) for x in v if str(x).strip()) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def save(updates: dict) -> dict:
    """Merge `updates` ({section: {key: value}}) into the profile and write
    it back. Unknown sections/keys are ignored so a client can't write
    arbitrary data into the file."""
    current = get()
    merged = {sec: dict(vals) for sec, vals in current.items()}
    for sec, vals in (updates or {}).items():
        if sec in DEFAULTS and isinstance(vals, dict):
            for k, v in vals.items():
                if k in DEFAULTS[sec]:
                    default = DEFAULTS[sec][k]
                    if isinstance(default, list) and isinstance(v, str):
                        v = [x.strip() for x in v.split(",") if x.strip()]
                    elif isinstance(default, bool):
                        v = bool(v)
                    elif isinstance(default, int) and not isinstance(default, bool):
                        try:
                            v = int(v or 0)
                        except (TypeError, ValueError):
                            v = default
                    merged[sec][k] = v
    body = ["# Jarvis profile — edit freely, or from the dashboard's \"You\" lens.", ""]
    for sec in DEFAULTS:
        body.append(f"[{sec}]")
        body += [f"{k} = {_toml_value(merged[sec].get(k, DEFAULTS[sec][k]))}" for k in DEFAULTS[sec]]
        body.append("")
    tmp = PROFILE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    os.replace(tmp, PROFILE_PATH)
    return get()

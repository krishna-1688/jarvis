import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to backend/.env and fill it in."
        )
    return value


# ── API Keys ─────────────────────────────
# Required — used by currently-active features.
GROQ_API_KEY = _require("GROQ_API_KEY")

# Optional — not used by any active feature yet.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
# Must exactly match a Redirect URI registered on the app at
# developer.spotify.com/dashboard — spotipy's SpotifyOAuth runs a tiny
# local server on this exact host:port to catch the one-time login
# callback, so it has to be a loopback address, not a public one.
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
PORCUPINE_API_KEY = os.getenv("PORCUPINE_API_KEY")

# ── AI Models ────────────────────────────
GROQ_MODEL = "llama-3.3-70b-versatile"
# Used for classification/extraction (intent routing, entity parsing,
# song-query cleanup) — structured small-output tasks where an 8B model
# is just as accurate as the 70B one but measurably faster on Groq's
# hardware (confirmed live: ~0.12s vs ~0.29s avg per call on a tiny
# prompt, and the gap widens further on the larger classification
# prompt). GROQ_MODEL stays reserved for brain.py's actual conversational
# replies, where response quality benefits from the bigger model.
GROQ_CLASSIFIER_MODEL = "llama-3.1-8b-instant"
GEMINI_MODEL = "gemini-1.5-flash"
WHISPER_MODEL = "whisper-large-v3"

# ── User Settings ────────────────────────
USER_NAME = "Krishna"
USER_COLLEGE = "VIT Chennai"
CITY = "Chennai"

# ── Jarvis Settings ──────────────────────
WAKE_WORD = "hey jarvis"
RESPONSE_STYLE = "sharp, witty, slightly sarcastic, like Iron Man's Jarvis"

# Gates dev-only endpoints (e.g. /debug/refresh_timetable) that force a
# live VTOP fetch on demand — off by default so they're not reachable
# on a packaged/shipped build.
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ── Thresholds ───────────────────────────
ATTENDANCE_WARNING = 75
ASSIGNMENT_REMINDER_DAYS = 3
WATER_REMINDER_MINS = 45

# ── Focus mode ───────────────────────────
# 'mute' (default, safer) or 'kill'. Kept off chrome.exe/anything the
# assistant itself might depend on — start with just the obvious
# distractions and widen deliberately, not by default.
FOCUS_KILL_MODE = os.getenv("FOCUS_KILL_MODE", "mute")
FOCUS_KILL_APPS = [
    a.strip() for a in os.getenv("FOCUS_KILL_APPS", "Discord.exe,Spotify.exe").split(",") if a.strip()
]

# ── Expenses ─────────────────────────────
# When false, WhatsApp UPI messages are still parsed and logged to
# data/upi_unparsed.log if unparseable, but successfully-parsed ones are
# NOT auto-inserted — voice confirmation ("log the recent zomato spend")
# is required instead. Defaults on since auto-parse is this feature's
# whole point; flip it off in .env if that's too much automatic logging.
EXPENSE_AUTO_INGEST = os.getenv("EXPENSE_AUTO_INGEST", "true").lower() == "true"

# ── VTOP ─────────────────────────────────
VTOP_USERNAME = _require("VTOP_USERNAME")
VTOP_PASSWORD = _require("VTOP_PASSWORD")

LMS_USERNAME        = _require("LMS_USERNAME")
LMS_PASSWORD        = _require("LMS_PASSWORD")
MY_WHATSAPP_NUMBER  = _require("MY_WHATSAPP_NUMBER")  # your number with country code
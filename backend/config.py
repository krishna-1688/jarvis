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

# Optional — Gemini is the fallback provider when Groq's quota runs out.
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
# Ordered fallback chains, each overridable from .env as a comma list.
# core/llm.py tries them in order and skips any model that is rate
# limited or retired. That matters: Groq retired llama-3.1-8b-instant and
# llama-3.3-70b-versatile for this key, and because each call site had one
# hard-coded model, every classification/extraction call was failing
# silently and dropping into generic chat. Each Groq model also has its
# own per-minute token bucket, so spreading roles across models keeps us
# further from 429s.
def _model_list(env_name: str, default: str) -> list:
    return [m.strip() for m in os.getenv(env_name, default).split(",") if m.strip()]

# Gemini (free AI Studio key) has its own quota, separate from Groq's
# 200k-tokens-per-model-per-day cap, so it goes at the END of each chain:
# used only once every Groq model is rate limited. "gemini:" tells
# core/llm.py which provider to call. Only the Lite models answer in ~2 s;
# full Flash took 6-10 s in testing, too slow for voice.
_GEMINI_FALLBACK = "gemini:gemini-3.5-flash-lite,gemini:gemini-flash-lite-latest" if GEMINI_API_KEY else ""

# Conversational replies — quality matters most.
CHAT_MODELS = (_model_list("GROQ_CHAT_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b")
               + _model_list("GEMINI_CHAT_MODELS", _GEMINI_FALLBACK))
# Intent routing / entity extraction — short JSON output, latency matters.
CLASSIFIER_MODELS = (_model_list("GROQ_CLASSIFIER_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-20b,openai/gpt-oss-120b")
                     + _model_list("GEMINI_CLASSIFIER_MODELS", _GEMINI_FALLBACK))
# Image input: VTOP captcha reading and "what's on my screen".
VISION_MODELS = (_model_list("GROQ_VISION_MODELS", "qwen/qwen3.8-27b")
                 + _model_list("GEMINI_VISION_MODELS", _GEMINI_FALLBACK))

# Back-compat names for any code still reading a single model.
GROQ_MODEL = CHAT_MODELS[0]
GROQ_CLASSIFIER_MODEL = CLASSIFIER_MODELS[0]
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
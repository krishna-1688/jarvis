"""
core/llm.py — the one gateway every Groq text call goes through.

Why this exists: each module used to build its own Groq client pinned to
one hard-coded model. When Groq retired that model, every call from that
module failed, and each caller's broad `except` quietly turned the failure
into "just chat" — routing was silently dead with nothing in the logs but
a one-line print per request.

Here every call walks an ordered model chain (config.CHAT_MODELS /
CLASSIFIER_MODELS). Entries prefixed "gemini:" go to Google's
OpenAI-compatible Gemini endpoint instead of Groq — a separate free quota. A model that is rate limited is skipped until its
retry-after passes; a model that is retired/unknown is skipped for an
hour. Callers get text back or LLMUnavailable, never a half-parsed error.
"""

import json
import re
import threading
import time

import groq
import httpx
from groq import Groq

from config import GROQ_API_KEY, GEMINI_API_KEY, CHAT_MODELS, CLASSIFIER_MODELS, VISION_MODELS

_client = Groq(api_key=GROQ_API_KEY, max_retries=0, timeout=12.0)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_gemini_http: httpx.Client | None = None  # created on first Gemini call

_ROLE_MODELS = {
    "chat": CHAT_MODELS,
    "classifier": CLASSIFIER_MODELS,
    "vision": VISION_MODELS,
}

_cooldown_until: dict = {}
_cooldown_lock = threading.Lock()

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

# gpt-oss spends part of max_tokens on hidden reasoning before it writes
# the answer; without headroom a 250-token budget can come back empty.
_REASONING_HEADROOM = 400


class LLMUnavailable(Exception):
    pass


class _GeminiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Gemini {status}: {message}")
        self.status = status


def _in_cooldown(model: str) -> bool:
    with _cooldown_lock:
        return _cooldown_until.get(model, 0) > time.time()


def _cool_down(model: str, seconds: float, why: str):
    with _cooldown_lock:
        _cooldown_until[model] = time.time() + seconds
    print(f"[llm] {model} unavailable for {seconds:.0f}s ({why})")


_TRY_AGAIN_RE = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


_GEMINI_RETRY_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _retry_after(exc) -> float:
    # A per-DAY cap (tokens/requests per day) is not a blip: retrying every
    # 2 minutes just burns requests until the quota refills. Honour Groq's
    # own "try again in 1h2m3s" for those, instead of the per-minute cap.
    msg = str(exc)
    if isinstance(exc, _GeminiError):
        # Gemini names the quota ("...PerDay...") and says "retry in 23.4s";
        # a daily free-tier quota resets at midnight Pacific, so rest an hour.
        if "perday" in msg.lower().replace(" ", ""):
            return 3600.0
        m = _GEMINI_RETRY_RE.search(msg)
        return max(2.0, min(float(m.group(1)), 120.0)) if m else 30.0
    if "per day" in msg:
        m = _TRY_AGAIN_RE.search(msg)
        if m and any(m.groups()):
            h, mi, s = (float(g) if g else 0.0 for g in m.groups())
            return max(60.0, h * 3600 + mi * 60 + s)
        return 1800.0
    try:
        value = exc.response.headers.get("retry-after")
        return max(2.0, min(float(value), 120.0))
    except Exception:
        return 20.0


def _model_params(model: str, max_tokens: int) -> dict:
    if model.startswith("gemini:"):
        # Lite models accept "minimal" thinking (~2 s replies); others reject it.
        return {"reasoning_effort": "minimal" if "lite" in model else "low", "max_tokens": max_tokens + 200}
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "max_tokens": max_tokens + _REASONING_HEADROOM}
    return {"max_tokens": max_tokens}


# If every model is only briefly rate-limited (Groq's per-minute token
# caps typically clear in 2-6 s), waiting once beats answering "give me a
# sec" — measured: most cooldowns in a heavy test run were 2-6 s.
SHORT_WAIT_MAX_S = 6.0


def shortest_cooldown(role: str) -> float:
    now = time.time()
    with _cooldown_lock:
        waits = [_cooldown_until.get(m, 0) - now for m in _ROLE_MODELS[role]]
    return max(0.0, min(waits)) if waits else 0.0


def complete(messages: list, *, role: str = "chat", max_tokens: int = 300,
             temperature: float = 0.4, json_mode: bool = False) -> str:
    """Returns the model's text reply. Raises LLMUnavailable only when
    every model in the role's chain failed (after one short wait if they
    were all just briefly rate-limited)."""
    try:
        return _complete_once(messages, role, max_tokens, temperature, json_mode)
    except LLMUnavailable:
        wait = shortest_cooldown(role)
        if 0 < wait <= SHORT_WAIT_MAX_S:
            time.sleep(wait + 0.2)
            return _complete_once(messages, role, max_tokens, temperature, json_mode)
        raise


def _complete_once(messages, role, max_tokens, temperature, json_mode) -> str:
    last_error = "no model configured"
    for model in _ROLE_MODELS[role]:
        if _in_cooldown(model):
            last_error = f"{model} cooling down"
            continue
        kwargs = _model_params(model, max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            if model.startswith("gemini:"):
                content = _gemini_create(model[len("gemini:"):], messages, temperature, kwargs)
            else:
                response = _client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature, **kwargs,
                )
                content = response.choices[0].message.content
            text = _THINK_RE.sub("", content or "").strip()
            if text:
                return text
            last_error = f"{model} returned empty content"
        except _GeminiError as e:
            if e.status == 429:
                _cool_down(model, _retry_after(e), "rate limited")
            elif e.status == 404:
                _cool_down(model, 3600, "model not found / retired")
            elif e.status >= 500:
                _cool_down(model, 30, "overloaded")
            last_error = str(e)
        except groq.RateLimitError as e:
            _cool_down(model, _retry_after(e), "rate limited")
            last_error = str(e)
        except groq.NotFoundError as e:
            _cool_down(model, 3600, "model not found / retired")
            last_error = str(e)
        except groq.BadRequestError as e:
            msg = str(e).lower()
            if "decommission" in msg or "model_not_found" in msg or "does not exist" in msg:
                _cool_down(model, 3600, "model retired")
            last_error = str(e)
        except (groq.APITimeoutError, groq.APIConnectionError) as e:
            last_error = str(e)
        except Exception as e:
            last_error = str(e)
    raise LLMUnavailable(last_error)


def _gemini_create(model: str, messages: list, temperature: float, kwargs: dict) -> str:
    global _gemini_http
    if not GEMINI_API_KEY:
        raise _GeminiError(401, "GEMINI_API_KEY not set")
    if _gemini_http is None:
        _gemini_http = httpx.Client(timeout=15.0, headers={"Authorization": f"Bearer {GEMINI_API_KEY}"})
    r = _gemini_http.post(_GEMINI_URL, json={"model": model, "messages": messages,
                                             "temperature": temperature, **kwargs})
    if r.status_code != 200:
        raise _GeminiError(r.status_code, r.text[:400])
    return r.json()["choices"][0]["message"].get("content") or ""


def complete_json(messages: list, *, role: str = "classifier", max_tokens: int = 200) -> dict:
    """Like complete(), but parses a JSON object out of the reply."""
    raw = complete(messages, role=role, max_tokens=max_tokens, temperature=0, json_mode=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(raw)
        if m:
            return json.loads(m.group(0))
        raise


def transcribe(wav_bytes: bytes, prompt: str = ""):
    """Whisper via Groq — returns the verbose_json transcription object."""
    kwargs = {"prompt": prompt} if prompt else {}
    return _client.audio.transcriptions.create(
        model="whisper-large-v3-turbo",
        file=("audio.wav", wav_bytes, "audio/wav"),
        response_format="verbose_json",
        language="en",
        temperature=0,
        **kwargs,
    )


def read_image_text(image_b64: str, instruction: str, max_tokens: int = 64) -> str:
    """Vision call used by the VTOP captcha solver."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": instruction},
        ],
    }]
    return complete(messages, role="vision", max_tokens=max_tokens, temperature=0)

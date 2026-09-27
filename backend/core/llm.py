"""
core/llm.py — the one gateway every Groq text call goes through.

Why this exists: each module used to build its own Groq client pinned to
one hard-coded model. When Groq retired that model, every call from that
module failed, and each caller's broad `except` quietly turned the failure
into "just chat" — routing was silently dead with nothing in the logs but
a one-line print per request.

Here every call walks an ordered model chain (config.CHAT_MODELS /
CLASSIFIER_MODELS). A model that is rate limited is skipped until its
retry-after passes; a model that is retired/unknown is skipped for an
hour. Callers get text back or LLMUnavailable, never a half-parsed error.
"""

import json
import re
import threading
import time

import groq
from groq import Groq

from config import GROQ_API_KEY, CHAT_MODELS, CLASSIFIER_MODELS, VISION_MODELS

_client = Groq(api_key=GROQ_API_KEY, max_retries=0, timeout=12.0)

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


def _in_cooldown(model: str) -> bool:
    with _cooldown_lock:
        return _cooldown_until.get(model, 0) > time.time()


def _cool_down(model: str, seconds: float, why: str):
    with _cooldown_lock:
        _cooldown_until[model] = time.time() + seconds
    print(f"[llm] {model} unavailable for {seconds:.0f}s ({why})")


def _retry_after(exc) -> float:
    try:
        value = exc.response.headers.get("retry-after")
        return max(2.0, min(float(value), 120.0))
    except Exception:
        return 20.0


def _model_params(model: str, max_tokens: int) -> dict:
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "max_tokens": max_tokens + _REASONING_HEADROOM}
    return {"max_tokens": max_tokens}


def complete(messages: list, *, role: str = "chat", max_tokens: int = 300,
             temperature: float = 0.4, json_mode: bool = False) -> str:
    """Returns the model's text reply. Raises LLMUnavailable only when
    every model in the role's chain failed."""
    last_error = "no model configured"
    for model in _ROLE_MODELS[role]:
        if _in_cooldown(model):
            last_error = f"{model} cooling down"
            continue
        kwargs = _model_params(model, max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = _client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, **kwargs,
            )
            text = _THINK_RE.sub("", response.choices[0].message.content or "").strip()
            if text:
                return text
            last_error = f"{model} returned empty content"
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

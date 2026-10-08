"""
features/screen_vision.py — "what's on my screen?"

One screenshot, taken only when asked, shrunk and sent to the vision
model with the question. Nothing runs in the background and nothing is
written to disk: the image lives in memory for the length of one call.
"""

import base64
import io
import time

from core.brain import to_spoken
from core.llm import LLMUnavailable, complete, shortest_cooldown
from features.base import FeatureResult

# Long side after downscaling. 1280 px keeps normal UI text legible to the
# vision model; bigger costs more of the per-minute token budget (the
# vision model is shared with intent routing) for little gain.
MAX_SIDE = 1280
JPEG_QUALITY = 80
THUMB_SIDE = 440

_SYSTEM = (
    "You are Jarvis, a personal voice assistant, looking at a screenshot of the user's screen. "
    "Answer only from what is visible. If they just ask what's on screen, say which app or "
    "page is open and what the main content is. Quote error messages and key text exactly. "
    "If they ask you to explain, solve or summarise something visible, do that. "
    "Ignore Jarvis's own assistant window if it appears. "
    "Start with a 1-2 sentence answer that works when read aloud; add detail after only if it helps. "
    "If the screen is blank, locked or unreadable, say so plainly — never guess."
)


def _jpeg_b64(img, side: int, quality: int) -> str:
    copy = img.copy()
    copy.thumbnail((side, side))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def capture_jpeg_b64() -> tuple[str, str]:
    """Primary monitor as base64 JPEGs: (for the model at MAX_SIDE, a small
    thumbnail the dashboard shows beside the answer)."""
    from PIL import ImageGrab  # lazy: Pillow only loads when someone asks

    img = ImageGrab.grab()
    try:
        img = img.convert("RGB")
        return _jpeg_b64(img, MAX_SIDE, JPEG_QUALITY), _jpeg_b64(img, THUMB_SIDE, 70)
    finally:
        img.close()


def describe_screen(question: str) -> FeatureResult:
    started = time.monotonic()
    try:
        image_b64, thumb_b64 = capture_jpeg_b64()
    except Exception as e:
        msg = "I couldn't capture your screen just now."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error=str(e))

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": question.strip() or "What's on my screen?"},
        ]},
    ]
    try:
        answer = complete(messages, role="vision", max_tokens=450, temperature=0.2)
    except LLMUnavailable as e:
        wait = shortest_cooldown("vision")
        when = f"in about {max(1, round(wait / 60))} minute{'s' if wait >= 90 else ''}" if wait > 0 else "shortly"
        msg = f"My vision model is out of quota for now — ask me again {when}."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error=str(e))

    # Speak only the opening answer; the breakdown after it is for reading.
    first_para = answer.strip().split("\n\n", 1)[0]
    # The thumbnail only travels to the open dashboard (in memory); the
    # conversation table stores chat text, never this.
    data = {"screen": True, "thumb": thumb_b64, "seconds": round(time.monotonic() - started, 1)}
    return FeatureResult(ok=True, data=data, display=answer, spoken=to_spoken(first_para))

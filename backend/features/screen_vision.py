"""
features/screen_vision.py — "what's on my screen?"

One screenshot, taken only when asked, shrunk and sent to the vision
model with the question. Nothing runs in the background and nothing is
written to disk: the image lives in memory for the length of one call.
"""

import base64
import io

from core.brain import to_spoken
from core.llm import LLMUnavailable, complete, shortest_cooldown
from features.base import FeatureResult

# Long side after downscaling. 1600 px keeps normal UI text legible to the
# vision model while the JPEG stays well under Groq's 4 MB base64 limit.
MAX_SIDE = 1600
JPEG_QUALITY = 80

_SYSTEM = (
    "You are Jarvis, KK's voice assistant, looking at a screenshot of KK's screen. "
    "Answer only from what is visible. If KK just asks what's on screen, say which app or "
    "page is open and what the main content is. Quote error messages and key text exactly. "
    "If KK asks you to explain, solve or summarise something visible, do that. "
    "Ignore Jarvis's own assistant window if it appears. "
    "Start with a 1-2 sentence answer that works when read aloud; add detail after only if it helps. "
    "If the screen is blank, locked or unreadable, say so plainly — never guess."
)


def capture_jpeg_b64() -> str:
    """Primary monitor as a base64 JPEG, downscaled to MAX_SIDE."""
    from PIL import ImageGrab  # lazy: Pillow only loads when someone asks

    img = ImageGrab.grab()
    try:
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        img.close()


def describe_screen(question: str) -> FeatureResult:
    try:
        image_b64 = capture_jpeg_b64()
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
    return FeatureResult(ok=True, data={}, display=answer, spoken=to_spoken(first_para))

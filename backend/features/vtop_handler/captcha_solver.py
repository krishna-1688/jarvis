import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

# Routed through core.llm so a retired vision model (this used to pin
# qwen/qwen3.6-27b, which Groq removed — every VTOP login then failed on
# "Invalid Captcha") falls through to config.VISION_MODELS' next entry.
from core.llm import read_image_text

_INSTRUCTION = (
    "Read the CAPTCHA text exactly. Only uppercase letters and numbers. "
    "Reply with ONLY the captcha text, nothing else."
)


def solve_captcha(captcha_b64: str) -> str:
    result = read_image_text(captcha_b64, _INSTRUCTION).upper()
    return "".join(c for c in result if c.isalnum())

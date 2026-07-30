import base64
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from config import GROQ_API_KEY
from groq import Groq

client = Groq(api_key=GROQ_API_KEY)

def solve_captcha(captcha_b64: str) -> str:
    response = client.chat.completions.create(
        model="qwen/qwen3.6-27b",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{captcha_b64}"}
                },
                {
                    "type": "text",
                    "text": "Read the CAPTCHA text exactly. Only uppercase letters and numbers. Reply with ONLY the captcha text, nothing else."
                }
            ]
        }],
        max_tokens=1024,
        reasoning_format="hidden"
    )
    result = response.choices[0].message.content.strip().upper()
    result = "".join(c for c in result if c.isalnum())
    return result
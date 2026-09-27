"""
core/whatsapp_intent.py — Groq-powered WhatsApp intent extraction.

Why not regex: "send a message to Amma good night" has no fixed boundary
between recipient name and message content. Regex cannot reliably split
this. Groq, which actually understands language, can.

This is a single small/fast Groq call (low latency, ~150 tokens) that
returns structured JSON: {"recipient": "...", "message": "..."}
or {"recipient": null, "message": null} if it's not a WhatsApp request at all.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import complete_json

EXTRACTION_PROMPT = """You extract WhatsApp message requests from natural speech.

The user is talking to a voice assistant. If their message is a request to
send a WhatsApp message to someone, extract:
  - recipient: the person's name EXACTLY as said (could be a nickname, could be misheard)
  - message: the actual content they want sent, in their own words

If the input is NOT a request to send a message (it's some other command or
just conversation), return both fields as null.

Respond with ONLY valid JSON, nothing else, no markdown, no explanation.
Format: {"recipient": "name or null", "message": "content or null"}

Examples:
Input: "send a message to Amma good night"
Output: {"recipient": "Amma", "message": "good night"}

Input: "tell mom I reached home safely"
Output: {"recipient": "mom", "message": "I reached home safely"}

Input: "message Rahul that I'll be 10 minutes late"
Output: {"recipient": "Rahul", "message": "I'll be 10 minutes late"}

Input: "send a message to nainah jio good night"
Output: {"recipient": "nainah jio", "message": "good night"}

Input: "what's the weather today"
Output: {"recipient": null, "message": null}

Input: "let amma know I'm on my way"
Output: {"recipient": "amma", "message": "I'm on my way"}

Input: "tell me a joke"
Output: {"recipient": null, "message": null}

Input: "whatsapp Arjun that the meeting is postponed to 5pm"
Output: {"recipient": "Arjun", "message": "the meeting is postponed to 5pm"}

Now extract from this input:
Input: "{user_input}"
Output:"""


def extract_whatsapp_intent_groq(text: str) -> dict | None:
    """
    Returns {"recipient": str, "message": str} if this is a WhatsApp
    send request, else None.

    The router has already decided this is a WhatsApp request, so there
    is no keyword pre-filter here any more — it used to reject valid
    phrasings the router accepted ("let amma know I'm late").
    """
    try:
        prompt = EXTRACTION_PROMPT.replace("{user_input}", text)
        data = complete_json([{"role": "user", "content": prompt}], max_tokens=100)
        recipient = data.get("recipient")
        message   = data.get("message")

        if (isinstance(recipient, str) and isinstance(message, str)
                and recipient.strip() and message.strip()
                and recipient.lower() != "null" and message.lower() != "null"):
            return {"recipient": recipient.strip(), "message": message.strip()}
        return None

    except Exception as e:
        print(f"[whatsapp_intent] extraction error: {e}")
        return None
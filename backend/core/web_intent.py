"""
core/web_intent.py — Groq-powered web automation intent extraction.

Same reasoning as whatsapp_intent.py: phrasing like "compare this laptop
on amazon and flipkart" or "search flights to goa on skyscanner" has no
fixed grammatical boundary regex can reliably split. Groq classifies the
action type and extracts the relevant pieces (site, query, two sites for
compare) in one small structured call.
"""

import os
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

groq_client = Groq(api_key=GROQ_API_KEY)

WEB_TRIGGER_WORDS = [
    "open", "go to", "search", "look up", "find", "compare",
    "summarize", "summarise", "what does this page say",
    "what are the reviews", "browse", "google"
]

EXTRACTION_PROMPT = """You extract web browsing actions from natural speech for a voice assistant.

Classify the input into exactly ONE action type:
  - "open"      : just navigate to a site, no search.        e.g. "open youtube"
  - "search"    : search for something, optionally on a named site.  e.g. "search wireless mouse on amazon"
  - "compare"   : compare something across exactly two named sites.  e.g. "compare this laptop on amazon and flipkart"
  - "summarize" : summarize/read/explain the CURRENTLY OPEN page, no navigation involved.  e.g. "summarize this page", "what are the reviews saying"
  - "none"      : not a web browsing request at all.

Respond with ONLY valid JSON, no markdown, no explanation.

Format:
{"action": "open", "site": "youtube", "query": null, "site_a": null, "site_b": null}
{"action": "search", "site": "amazon", "query": "wireless mouse", "site_a": null, "site_b": null}
{"action": "compare", "site": null, "query": "laptop price", "site_a": "amazon", "site_b": "flipkart"}
{"action": "summarize", "site": null, "query": null, "site_a": null, "site_b": null}
{"action": "none", "site": null, "query": null, "site_a": null, "site_b": null}

Rules:
- If a site isn't named for "search", set site to null (caller will do a general web search).
- For "search", if user just says "search X" with no site, that's still a valid search action with site=null.
- "site" field should be the site name only (e.g. "amazon"), not a full URL.

Now extract from this input:
Input: "{user_input}"
Output:"""


def extract_web_intent(text: str) -> dict | None:
    """
    Returns a dict like:
      {"action": "search", "site": "amazon", "query": "wireless mouse",
       "site_a": None, "site_b": None}
    or None if this clearly isn't a web browsing request.
    """
    t_lower = text.lower()
    if not any(w in t_lower for w in WEB_TRIGGER_WORDS):
        return None

    try:
        prompt = EXTRACTION_PROMPT.replace("{user_input}", text)
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").replace("json", "", 1).strip()

        data = json.loads(raw)
        action = data.get("action")

        if not action or action == "none":
            return None

        return {
            "action":  action,
            "site":    data.get("site"),
            "query":   data.get("query"),
            "site_a":  data.get("site_a"),
            "site_b":  data.get("site_b"),
        }

    except Exception as e:
        print(f"[web_intent] extraction error: {e}")
        return None
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
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import complete_json

EXTRACTION_PROMPT = """You extract web browsing actions from natural speech for a voice assistant.

Classify the input into exactly ONE action type:
  - "open"      : just navigate to a site, no search.        e.g. "open youtube"
  - "search"    : search for something, optionally on a named site.  e.g. "search wireless mouse on amazon"
  - "compare"   : compare something across exactly two named sites.  e.g. "compare this laptop on amazon and flipkart"
  - "summarize" : summarize/read/explain the CURRENTLY OPEN page, no navigation involved.  e.g. "summarize this page", "what are the reviews saying"
  - "interact"  : act on the CURRENTLY OPEN page — click a result, scroll, go back a page, or close the tab.  e.g. "click the second result", "scroll down", "go back a page", "close this tab"
  - "none"      : not a web browsing request at all.

For "interact", also set interact_type to exactly one of:
  "click_result" (needs n = the 1-indexed result number, default 1 if unspecified),
  "scroll_down", "scroll_up", "back", "close_tab".

Respond with ONLY valid JSON, no markdown, no explanation.

Format:
{"action": "open", "site": "youtube", "query": null, "site_a": null, "site_b": null, "interact_type": null, "n": null}
{"action": "search", "site": "amazon", "query": "wireless mouse", "site_a": null, "site_b": null, "interact_type": null, "n": null}
{"action": "compare", "site": null, "query": "laptop price", "site_a": "amazon", "site_b": "flipkart", "interact_type": null, "n": null}
{"action": "summarize", "site": null, "query": null, "site_a": null, "site_b": null, "interact_type": null, "n": null}
{"action": "interact", "site": null, "query": null, "site_a": null, "site_b": null, "interact_type": "click_result", "n": 2}
{"action": "interact", "site": null, "query": null, "site_a": null, "site_b": null, "interact_type": "scroll_down", "n": null}
{"action": "none", "site": null, "query": null, "site_a": null, "site_b": null, "interact_type": null, "n": null}

Rules:
- If a site isn't named for "search", set site to null (caller will do a general web search).
- For "search", if user just says "search X" with no site, that's still a valid search action with site=null.
- "site" field should be the site name only (e.g. "amazon"), not a full URL.
- A general-knowledge question ("what is recursion", "explain TCP") is "none" — the assistant answers those itself, it should not open a browser.

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
    try:
        prompt = EXTRACTION_PROMPT.replace("{user_input}", text)
        data = complete_json([{"role": "user", "content": prompt}], max_tokens=120)
        action = data.get("action")

        if not action or action == "none":
            return None

        return {
            "action":        action,
            "site":          data.get("site"),
            "query":         data.get("query"),
            "site_a":        data.get("site_a"),
            "site_b":        data.get("site_b"),
            "interact_type": data.get("interact_type"),
            "n":             data.get("n"),
        }

    except Exception as e:
        print(f"[web_intent] extraction error: {e}")
        return None
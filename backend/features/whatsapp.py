"""
features/whatsapp.py — Python client for the Node.js WhatsApp service.

New in this version:
  find_whatsapp_contact(query) -> fuzzy match candidates, no sending
  send_whatsapp_confirmed(contact_id, message) -> send to a known contact id

These two replace blind direct-sending for name-based recipients.
The flow is now: find candidates -> confirm with user -> send_confirmed.
Phone numbers still go straight through send_whatsapp_message (unambiguous).
"""

import requests

from features.base import FeatureResult

WHATSAPP_SERVICE_URL = "http://localhost:4500"
TIMEOUT = 10


def is_whatsapp_ready() -> bool:
    try:
        r = requests.get(f"{WHATSAPP_SERVICE_URL}/status", timeout=3)
        if r.status_code == 200:
            return r.json().get("ready", False)
    except requests.exceptions.RequestException:
        pass
    return False


def get_whatsapp_status() -> dict:
    try:
        r = requests.get(f"{WHATSAPP_SERVICE_URL}/status", timeout=3)
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        return {"ready": False, "error": "WhatsApp service is not running"}
    return {"ready": False, "error": "Unexpected response from WhatsApp service"}


def find_whatsapp_contact(query: str) -> list:
    """
    Fuzzy search for a contact by name. Does NOT send anything.

    Returns a list of candidate matches, best first:
      [{"name": "Madhesh Kumar", "number": "9198...", "id": "9198...@c.us", "score": 0.83}, ...]

    Empty list if nothing reasonably close was found.
    Use this BEFORE sending — confirm with the user which match they meant.
    """
    try:
        r = requests.post(
            f"{WHATSAPP_SERVICE_URL}/find-contact",
            json={"query": query},
            timeout=8
        )
        if r.status_code == 200:
            return r.json().get("matches", [])
    except requests.exceptions.RequestException:
        pass
    return []


def send_whatsapp_confirmed(contact_id: str, message: str) -> FeatureResult:
    """
    Send to a contact_id that's already been confirmed (e.g. user said
    "yes" to "did you mean Madhesh?"). No fuzzy matching here.

    Returns FeatureResult(ok=True, data={"contact_id": ...}) or
    FeatureResult(ok=False, error="..."). display/spoken are left blank —
    the caller already knows the confirmed name and builds the message
    text itself.
    """
    try:
        r = requests.post(
            f"{WHATSAPP_SERVICE_URL}/send-confirmed",
            json={"contact_id": contact_id, "message": message},
            timeout=TIMEOUT
        )
        data = r.json()
        if r.status_code == 200:
            return FeatureResult(ok=True, data={"contact_id": contact_id}, display="", spoken="")
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error=data.get("error", "Unknown error"))
    except requests.exceptions.ConnectionError:
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error="WhatsApp service isn't running.")
    except requests.exceptions.Timeout:
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error="WhatsApp service timed out.")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


def send_whatsapp_message(to: str, message: str) -> FeatureResult:
    """
    Direct send — only safe for unambiguous targets:
      - a phone number with country code ("+919876543210")
      - an EXACT contact name match (no fuzziness, server-side exact check)

    For a name typed/spoken casually (possible typos, nicknames), use
    find_whatsapp_contact() + confirmation + send_whatsapp_confirmed()
    instead. This function will simply fail with "no exact match" if
    the name isn't spelled exactly as saved.

    Returns FeatureResult(ok=True, data={"sent_to": ...}) or
    FeatureResult(ok=False, error="..."). display/spoken are left blank —
    the caller builds the message text itself.
    """
    try:
        r = requests.post(
            f"{WHATSAPP_SERVICE_URL}/send",
            json={"to": to, "message": message},
            timeout=TIMEOUT
        )
        data = r.json()
        if r.status_code == 200:
            return FeatureResult(ok=True, data={"sent_to": data.get("sent_to", to)},
                                  display="", spoken="")
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error=data.get("error", "Unknown error"))

    except requests.exceptions.ConnectionError:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=(
            "WhatsApp service isn't running. Start it with "
            "'node server.js' inside the whatsapp_service folder."
        ))
    except requests.exceptions.Timeout:
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error="WhatsApp service timed out.")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


def get_whatsapp_contacts() -> list:
    try:
        r = requests.get(f"{WHATSAPP_SERVICE_URL}/contacts", timeout=5)
        if r.status_code == 200:
            return r.json().get("contacts", [])
    except requests.exceptions.RequestException:
        pass
    return []


def refresh_whatsapp_contacts() -> bool:
    try:
        r = requests.post(f"{WHATSAPP_SERVICE_URL}/refresh-contacts", timeout=15)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False
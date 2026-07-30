"""
lms_handler/session_generator.py

Standard Moodle login — no captcha needed.
"""

import requests
from bs4 import BeautifulSoup
from .constants import LMS_LOGIN_URL, LMS_BASE_URL, HEADERS


def generate_session(username: str, password: str) -> tuple[requests.Session | None, str]:
    """
    Logs into VIT Chennai's Moodle LMS.

    Returns:
        (session, status)
        session: requests.Session with valid MoodleSession cookie, or None on failure
        status: "ok" | "invalid_credentials" | "no_token" | "unknown_error"
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False

    resp = session.get(LMS_LOGIN_URL)
    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "logintoken"})
    logintoken = token_input["value"] if token_input else ""

    if not logintoken:
        return None, "no_token"

    payload = {
        "username": username,
        "password": password,
        "logintoken": logintoken,
    }
    resp = session.post(LMS_LOGIN_URL, data=payload)

    if "loginerrors" in resp.text or "Invalid login" in resp.text:
        return None, "invalid_credentials"

    if "/my/" in resp.url or "dashboard" in resp.url.lower() or "logout" in resp.text.lower():
        return session, "ok"

    return None, "unknown_error"


def get_valid_session(username: str, password: str, retries: int = 2) -> tuple[requests.Session | None, str]:
    last_status = "unknown_error"
    for _ in range(retries):
        session, status = generate_session(username, password)
        if session:
            return session, status
        last_status = status
        if status == "invalid_credentials":
            break
    return None, last_status
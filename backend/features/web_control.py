"""
features/web_control.py — Visible-browser web automation for Jarvis.

Tier 1 capabilities:
  1. open_and_navigate     — "open youtube", "go to amazon"
  2. search_on_site        — "search flights to Goa on Skyscanner"
  3. general_web_search    — open a real Google search visibly
  4. summarize_page        — "summarize this page" / "what are the reviews saying"
  5. compare_across_sites  — "compare this laptop on Amazon and Flipkart"

Design choices:
  - Chrome runs HEADED (visible window) by default — the user wants
    to watch Jarvis work, not just trust a silent background process.
  - A single persistent Playwright browser instance is reused across
    calls in the same Jarvis session rather than relaunching Chrome
    every time — relaunching is slow and looks janky on screen.
  - Known sites get a small hardcoded map of common search-box selectors
    so "search X on Y" is reliable for the sites people actually use.
    Unknown sites fall back to Google search as a safe default.
  - Every public function returns a plain dict the router/brain layer
    can turn into spoken + terminal text. No raw Playwright objects
    leak out of this module.
"""

import os


def _vtop_host() -> str:
    from core.semesters import campus
    return campus()["host"]
import sys
import re
import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from features.base import FeatureResult

# ══════════════════════════════════════════
#   PERSISTENT BROWSER SESSION
# ══════════════════════════════════════════
# Reused across calls so we don't relaunch Chrome (slow, looks broken)
# every single time the user asks for something on the web.

_pw_lock      = threading.RLock()
_playwright   = None
_browser      = None
_context      = None
_current_page = None

# Persisted login-session cookies/localStorage so a clean backend restart
# doesn't force the user to re-log into Gmail/LinkedIn/GitHub/etc. every time —
# same data/ directory pattern as features/spotify.py's .spotify_cache.
_SESSION_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "browser_state.json"
)


# ── One browser thread ─────────────────────────
# Playwright's sync API is bound to the thread that started it, but
# FastAPI serves requests from a pool of threads — so the second web
# command could land on a different thread and fail with a greenlet
# "cannot switch to a different thread" error. Every public function
# below runs on this single dedicated thread instead.
#
# The same thread closes Chrome after IDLE_CLOSE_S without use, so a
# browser opened once doesn't sit in RAM (hundreds of MB) all day.
IDLE_CLOSE_S = 180
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-browser")
_last_used = 0.0
_idle_timer = None


def _on_browser_thread(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if threading.current_thread().name.startswith("jarvis-browser"):
            return fn(*args, **kwargs)
        global _last_used
        _last_used = time.time()
        try:
            return _browser_executor.submit(fn, *args, **kwargs).result(timeout=180)
        finally:
            _last_used = time.time()
            _arm_idle_close()
    return wrapper


def _arm_idle_close():
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer = threading.Timer(IDLE_CLOSE_S + 1, lambda: _browser_executor.submit(_close_if_idle))
    _idle_timer.daemon = True
    _idle_timer.start()


def _close_if_idle():
    if _browser is None or time.time() - _last_used < IDLE_CLOSE_S:
        return
    print("[web_control] browser idle — closing it to free memory")
    try:
        save_browser_session()
    finally:
        with _pw_lock:
            _close_browser_unsafe()


def _ensure_browser():
    """Launch Playwright + Chrome ONCE, reuse afterwards. Visible window."""
    global _playwright, _browser, _context, _current_page

    with _pw_lock:
        if _browser is not None:
            try:
                # Cheap liveness check — if browser crashed/closed, this throws
                _browser.contexts
                return
            except Exception:
                _close_browser_unsafe()

        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=False,          # the user wants to SEE Jarvis working
            args=["--start-maximized"]
        )
        state_arg = _SESSION_STATE_PATH if os.path.exists(_SESSION_STATE_PATH) else None
        _context = _browser.new_context(no_viewport=True, storage_state=state_arg)
        _current_page = _context.new_page()


def _close_browser_unsafe():
    """Internal cleanup — caller must hold _pw_lock."""
    global _playwright, _browser, _context, _current_page
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _playwright = None
    _browser = None
    _context = None
    _current_page = None


@_on_browser_thread
def save_browser_session() -> None:
    """Persists cookies/localStorage to disk so logged-in sessions survive
    a backend restart. Call before closing the browser, not after — the
    context has to still be alive to read its own storage state."""
    with _pw_lock:
        if _context is None:
            return
        try:
            os.makedirs(os.path.dirname(_SESSION_STATE_PATH), exist_ok=True)
            _context.storage_state(path=_SESSION_STATE_PATH)
        except Exception as e:
            print(f"[web_control] failed to save session state: {e}")


@_on_browser_thread
def close_browser():
    """Public — call this if the user says 'close the browser' or on Jarvis shutdown."""
    save_browser_session()
    with _pw_lock:
        _close_browser_unsafe()


def _get_page():
    """Returns the active page, opening a fresh tab if the current one died."""
    _ensure_browser()
    global _current_page
    try:
        _current_page.title()  # liveness check
    except Exception:
        _current_page = _context.new_page()
    return _current_page


# ══════════════════════════════════════════
#   KNOWN SITES — domain + search-box selector
# ══════════════════════════════════════════
# Covers the sites people actually search on often. Anything not in
# here either gets a generic best-effort attempt (look for any
# <input type=search> or common search attributes) or falls back to
# a plain Google search of "site:domain query".

KNOWN_SITES = {
    "youtube":    {"url": "https://www.youtube.com",   "search_selector": "input#search, input[name='search_query'], ytd-searchbox input"},
    "amazon":     {"url": "https://www.amazon.in",      "search_selector": "input#twotabsearchtextbox"},
    "flipkart":   {"url": "https://www.flipkart.com",   "search_selector": "input[name='q']"},
    "google":     {"url": "https://www.google.com",     "search_selector": "textarea[name='q'], input[name='q']"},
    "gmail":      {"url": "https://mail.google.com",    "search_selector": None},
    "github":     {"url": "https://github.com",         "search_selector": "input[name='q'], input[placeholder*='Search']"},
    "wikipedia":  {"url": "https://en.wikipedia.org",   "search_selector": "input#searchInput"},
    "skyscanner": {"url": "https://www.skyscanner.co.in","search_selector": None},  # complex multi-field form, opens site only for now
    "reddit":     {"url": "https://www.reddit.com",     "search_selector": "input[name='q']"},
    "linkedin":   {"url": "https://www.linkedin.com",   "search_selector": "input[placeholder*='Search']"},
    "twitter":    {"url": "https://twitter.com",        "search_selector": "input[data-testid='SearchBox_Search_Input']"},
    "x":          {"url": "https://twitter.com",        "search_selector": "input[data-testid='SearchBox_Search_Input']"},
    "vtop":       {"url": f"https://{_vtop_host()}/vtop/login", "search_selector": None},
    "moodle":     {"url": "https://lms.vit.ac.in",      "search_selector": None},

    # Previously "known" only as PC-launch guards in core/router.py's
    # KNOWN_WEB_DESTINATIONS (so "open facebook" etc. correctly skipped
    # PC app-launch) but had no real entry here — silently fell through
    # to a broken literal Google search of the site's own name. Fixed.
    "chrome":        {"url": "https://www.google.com",   "search_selector": "textarea[name='q'], input[name='q']"},
    "browser":       {"url": "https://www.google.com",   "search_selector": "textarea[name='q'], input[name='q']"},
    "facebook":      {"url": "https://www.facebook.com",  "search_selector": "input[aria-label*='Search' i]"},
    "instagram":     {"url": "https://www.instagram.com", "search_selector": None},  # search lives behind a nav icon, no stable pre-click selector
    "netflix":       {"url": "https://www.netflix.com/browse", "search_selector": None},  # same reason as instagram
    "whatsapp web":  {"url": "https://web.whatsapp.com",  "search_selector": None},  # QR login-gated, same rationale as vtop/moodle
    "spotify web":   {"url": "https://open.spotify.com",  "search_selector": None},  # only reached via the explicit "web"-qualifier carve-out in detect_spotify_intent

    # CS-student-relevant sites (the user is CSE at VIT Chennai).
    "leetcode":      {"url": "https://leetcode.com",           "search_selector": "input[placeholder*='Search' i]"},
    "hackerrank":    {"url": "https://www.hackerrank.com",     "search_selector": "input[name='search']"},
    "codeforces":    {"url": "https://codeforces.com",         "search_selector": "input[name='q']"},
    "geeksforgeeks": {"url": "https://www.geeksforgeeks.org",  "search_selector": "input#head_search_input, input.head-search-input"},
    "stackoverflow": {"url": "https://stackoverflow.com",      "search_selector": "input[name='q']"},
    "chatgpt":       {"url": "https://chatgpt.com",            "search_selector": None},  # compose box, not a search bar
    "claude":        {"url": "https://claude.ai",               "search_selector": None},  # same
}

GENERIC_SEARCH_SELECTORS = [
    "input[type='search']",
    "input[name='search']",
    "input[name='q']",
    "input[placeholder*='Search' i]",
    "input[aria-label*='Search' i]",
]


def _resolve_site(site_name: str) -> dict | None:
    """Fuzzy-match a spoken site name against KNOWN_SITES."""
    s = site_name.lower().strip()
    if s in KNOWN_SITES:
        return KNOWN_SITES[s]
    # Substring fallback for near-miss phrasing ("netflix app" -> "netflix").
    # Short keys (e.g. the single-letter "x" alias for twitter) are excluded
    # here since almost any string contains a 1-2 char substring by
    # accident (e.g. "netflix" contains "x") — an exact match above already
    # covers those short keys correctly, so this loop only needs to handle
    # genuinely fuzzy multi-word cases. Longest key first so a more
    # specific name (e.g. "whatsapp web") wins over a shorter one it embeds.
    for key, val in sorted(KNOWN_SITES.items(), key=lambda kv: -len(kv[0])):
        if len(key) < 4:
            continue
        if key in s or s in key:
            return val
    return None


def _normalize_url(text: str) -> str:
    """If user already gave a URL-looking string, clean it up."""
    t = text.strip()
    if t.startswith("http://") or t.startswith("https://"):
        return t
    if "." in t and " " not in t:
        return f"https://{t}"
    return ""


# ══════════════════════════════════════════
#   1. OPEN AND NAVIGATE
# ══════════════════════════════════════════

@_on_browser_thread
def open_site(site_name: str) -> FeatureResult:
    """
    "open youtube", "go to amazon", "open vtop"
    Returns FeatureResult(ok=True, data={"site":..., "url":...}) or
    FeatureResult(ok=False, error="..."). display/spoken left blank —
    jarvis.py's handle_web() builds the message using the action context.
    """
    page = _get_page()

    site_info = _resolve_site(site_name)
    if site_info:
        url = site_info["url"]
    else:
        direct_url = _normalize_url(site_name)
        url = direct_url if direct_url else f"https://www.google.com/search?q={site_name}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return FeatureResult(ok=True, data={"site": site_name, "url": url}, display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


# ══════════════════════════════════════════
#   2. SEARCH ON A SITE
# ══════════════════════════════════════════

@_on_browser_thread
def search_on_site(site_name: str, query: str) -> FeatureResult:
    """
    "search flights to Goa on Skyscanner", "search wireless mouse on amazon"
    Navigates to the site, types into its search box, submits.
    """
    page = _get_page()
    site_info = _resolve_site(site_name)

    if not site_info:
        # Unknown site — fall back to a Google search scoped to that domain
        return general_web_search(f"{query} {site_name}")

    try:
        page.goto(site_info["url"], wait_until="domcontentloaded", timeout=20000)
        # JS-heavy sites (YouTube, Twitter, etc.) render their search box
        # AFTER domcontentloaded fires — a short settle delay avoids
        # racing the page's own JS before the box even exists yet.
        page.wait_for_timeout(1200)
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="",
                              error=f"Couldn't open {site_name}: {e}")

    selector = site_info.get("search_selector")
    # Try the known selector(s) first, then ALWAYS fall back to the
    # generic list too — sites change their markup over time, and a
    # stale hardcoded selector shouldn't be a hard failure.
    selectors_to_try = (selector.split(",") if selector else []) + GENERIC_SEARCH_SELECTORS

    for sel in selectors_to_try:
        sel = sel.strip() if sel else sel
        if not sel:
            continue
        try:
            box = page.locator(sel).first
            box.wait_for(state="visible", timeout=5000)
            box.click()
            box.fill(query)
            page.keyboard.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return FeatureResult(ok=True, data={"site": site_name, "query": query}, display="", spoken="")
        except Exception:
            continue

    return FeatureResult(
        ok=False, data={}, display="", spoken="",
        error=f"Opened {site_name} but couldn't find its search box. Try saying the query differently."
    )


# ══════════════════════════════════════════
#   3. GENERAL WEB SEARCH (visible Google)
# ══════════════════════════════════════════

@_on_browser_thread
def general_web_search(query: str) -> FeatureResult:
    """
    Plain Google search, done visibly in the browser window.
    Use when no specific site is mentioned.
    """
    page = _get_page()
    try:
        page.goto(
            f"https://www.google.com/search?q={query.replace(' ', '+')}",
            wait_until="domcontentloaded",
            timeout=20000
        )
        return FeatureResult(ok=True, data={"query": query}, display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


# ══════════════════════════════════════════
#   4. SUMMARIZE / READ CURRENT PAGE
# ══════════════════════════════════════════

@_on_browser_thread
def get_page_text(max_chars: int = 6000) -> FeatureResult:
    """
    Extracts visible text from the CURRENT page (whatever Jarvis last
    opened/navigated to). Caller (jarvis.py) feeds this into Groq for
    an actual summary — this function just gets the raw text safely.
    """
    page = _get_page()
    try:
        title = page.title()
        html = page.content()
        text = ""
        try:
            from readability import Document
            from bs4 import BeautifulSoup
            cleaned_html = Document(html).summary()
            text = BeautifulSoup(cleaned_html, "html.parser").get_text(separator="\n").strip()
        except Exception:
            text = ""
        # Readability strips nav/boilerplate but can find too little on
        # heavily-JS-rendered SPAs — fall back to raw innerText rather
        # than returning an unhelpfully short summary.
        if len(text) < 200:
            text = page.evaluate("() => document.body.innerText")
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return FeatureResult(ok=True, data={"title": title, "text": text, "url": page.url},
                              display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


# ══════════════════════════════════════════
#   5. COMPARE ACROSS TWO SITES
# ══════════════════════════════════════════

@_on_browser_thread
def compare_across_sites(query: str, site_a: str, site_b: str) -> FeatureResult:
    """
    "compare this laptop on amazon and flipkart"
    Opens TWO separate tabs (so the user can see both side by side), searches
    each, and grabs visible text from both results pages. Groq does the
    actual comparison afterward using this raw data.
    """
    _ensure_browser()

    results = {}
    for site in (site_a, site_b):
        site_info = _resolve_site(site)
        new_page = _context.new_page()

        try:
            if site_info:
                new_page.goto(site_info["url"], wait_until="domcontentloaded", timeout=20000)
                selector = site_info.get("search_selector")
                selectors_to_try = [selector] if selector else GENERIC_SEARCH_SELECTORS

                searched = False
                for sel in selectors_to_try:
                    if not sel:
                        continue
                    try:
                        box = new_page.locator(sel).first
                        box.wait_for(state="visible", timeout=4000)
                        box.click()
                        box.fill(query)
                        new_page.keyboard.press("Enter")
                        new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                        searched = True
                        break
                    except Exception:
                        continue

                if not searched:
                    results[site] = {"ok": False, "error": "couldn't search on this site"}
                    continue
            else:
                new_page.goto(
                    f"https://www.google.com/search?q={query}+{site}",
                    wait_until="domcontentloaded", timeout=20000
                )

            time.sleep(1.5)  # let dynamic content settle before scraping
            text = new_page.evaluate("() => document.body.innerText")
            text = re.sub(r'\n{3,}', '\n\n', text).strip()[:3000]
            results[site] = {"ok": True, "text": text, "url": new_page.url}

        except Exception as e:
            results[site] = {"ok": False, "error": str(e)}

    return FeatureResult(ok=True, data={"query": query, "results": results}, display="", spoken="")


# ══════════════════════════════════════════
#   6. POST-SEARCH PAGE INTERACTION
# ══════════════════════════════════════════
# There was previously no way to act on a page after opening/searching it
# — click a result, scroll, go back, or close the tab. These make the
# browser feel steerable rather than one-shot.

@_on_browser_thread
def click_result(n: int) -> FeatureResult:
    """Clicks the Nth link on the current page (1-indexed, matching how
    people say 'click the second one')."""
    page = _get_page()
    try:
        page.get_by_role("link").nth(max(0, n - 1)).click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        return FeatureResult(ok=True, data={"n": n, "url": page.url}, display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


@_on_browser_thread
def scroll_page(direction: str = "down") -> FeatureResult:
    page = _get_page()
    try:
        delta = 800 if direction == "down" else -800
        page.mouse.wheel(0, delta)
        return FeatureResult(ok=True, data={"direction": direction}, display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


@_on_browser_thread
def go_back() -> FeatureResult:
    page = _get_page()
    try:
        page.go_back(wait_until="domcontentloaded", timeout=15000)
        return FeatureResult(ok=True, data={"url": page.url}, display="", spoken="")
    except Exception as e:
        return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))


@_on_browser_thread
def close_current_tab() -> FeatureResult:
    """Closes the active tab. Falls back to the browser's remaining last
    tab, or a fresh blank one if that was the only tab open, so there's
    always a live page for the next command."""
    global _current_page
    with _pw_lock:
        if _current_page is None:
            return FeatureResult(ok=False, data={}, display="", spoken="", error="No tab open")
        try:
            _current_page.close()
        except Exception:
            pass
        try:
            remaining = _context.pages
            _current_page = remaining[-1] if remaining else _context.new_page()
        except Exception as e:
            return FeatureResult(ok=False, data={}, display="", spoken="", error=str(e))
    return FeatureResult(ok=True, data={}, display="", spoken="")


# ══════════════════════════════════════════
#   STATUS / CLEANUP HELPERS
# ══════════════════════════════════════════

@_on_browser_thread
def is_browser_open() -> bool:
    with _pw_lock:
        if _browser is None:
            return False
        try:
            _browser.contexts
            return True
        except Exception:
            return False


@_on_browser_thread
def get_current_url() -> str | None:
    if not is_browser_open():
        return None
    try:
        return _current_page.url
    except Exception:
        return None
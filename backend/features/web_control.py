"""
features/web_control.py — Visible-browser web automation for Jarvis.

Tier 1 capabilities:
  1. open_and_navigate     — "open youtube", "go to amazon"
  2. search_on_site        — "search flights to Goa on Skyscanner"
  3. general_web_search    — open a real Google search visibly
  4. summarize_page        — "summarize this page" / "what are the reviews saying"
  5. compare_across_sites  — "compare this laptop on Amazon and Flipkart"

Design choices:
  - Chrome runs HEADED (visible window) by default — KK explicitly wants
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
import sys
import re
import threading
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from features.base import FeatureResult

# ══════════════════════════════════════════
#   PERSISTENT BROWSER SESSION
# ══════════════════════════════════════════
# Reused across calls so we don't relaunch Chrome (slow, looks broken)
# every single time the user asks for something on the web.

_pw_lock      = threading.Lock()
_playwright   = None
_browser      = None
_context      = None
_current_page = None


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
            headless=False,          # KK wants to SEE Jarvis working
            args=["--start-maximized"]
        )
        _context = _browser.new_context(no_viewport=True)
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


def close_browser():
    """Public — call this if KK says 'close the browser' or on Jarvis shutdown."""
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
    "vtop":       {"url": "https://vtopcc.vit.ac.in/vtop/login", "search_selector": None},
    "moodle":     {"url": "https://lms.vit.ac.in",      "search_selector": None},
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
    for key, val in KNOWN_SITES.items():
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

def get_page_text(max_chars: int = 6000) -> FeatureResult:
    """
    Extracts visible text from the CURRENT page (whatever Jarvis last
    opened/navigated to). Caller (jarvis.py) feeds this into Groq for
    an actual summary — this function just gets the raw text safely.
    """
    page = _get_page()
    try:
        title = page.title()
        # innerText pulls only visible, rendered text — skips hidden
        # nav junk far better than raw HTML would.
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

def compare_across_sites(query: str, site_a: str, site_b: str) -> FeatureResult:
    """
    "compare this laptop on amazon and flipkart"
    Opens TWO separate tabs (so KK can see both side by side), searches
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
#   STATUS / CLEANUP HELPERS
# ══════════════════════════════════════════

def is_browser_open() -> bool:
    with _pw_lock:
        if _browser is None:
            return False
        try:
            _browser.contexts
            return True
        except Exception:
            return False


def get_current_url() -> str | None:
    if not is_browser_open():
        return None
    try:
        return _current_page.url
    except Exception:
        return None
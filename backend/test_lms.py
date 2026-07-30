"""
Test script — Moodle (lms.vit.ac.in) login + calendar fetch.
Run this on your machine. It will:
 1. Login to Moodle
 2. Fetch the upcoming calendar page
 3. Save raw HTML to lms_calendar.html for inspection
 4. Print a quick summary of what it finds
"""

import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import LMS_USERNAME, LMS_PASSWORD

BASE_URL = "https://lms.vit.ac.in"
USERNAME = LMS_USERNAME
PASSWORD = LMS_PASSWORD

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

session = requests.Session()
session.headers.update(HEADERS)
session.verify = False
# ── Step 1: Get login page + token ──────────────
print("Fetching login page...")
resp = session.get(f"{BASE_URL}/login/index.php")
soup = BeautifulSoup(resp.text, "html.parser")

token_input = soup.find("input", {"name": "logintoken"})
logintoken = token_input["value"] if token_input else ""
print(f"Login token: {logintoken[:20]}..." if logintoken else "⚠️ No logintoken found")

# ── Step 2: Submit login ────────────────────────
print("\nLogging in...")
login_payload = {
    "username": USERNAME,
    "password": PASSWORD,
    "logintoken": logintoken,
}
resp = session.post(f"{BASE_URL}/login/index.php", data=login_payload)

# Check if login succeeded
if "loginerrors" in resp.text or "Invalid login" in resp.text:
    print("❌ Login failed — check credentials")
    soup = BeautifulSoup(resp.text, "html.parser")
    error = soup.find(class_="loginerrors")
    if error:
        print("Error message:", error.get_text(strip=True))
elif "logout" in resp.text.lower() or "dashboard" in resp.url.lower():
    print("✅ Login appears successful!")
    print("Redirected to:", resp.url)
else:
    print("⚠️ Unclear result — saving HTML for inspection")

# Save post-login page
with open("lms_after_login.html", "w", encoding="utf-8") as f:
    f.write(resp.text)
print("Saved post-login page to lms_after_login.html")

# ── Step 3: Fetch upcoming calendar ─────────────
print("\nFetching upcoming calendar...")
resp = session.get(f"{BASE_URL}/calendar/view.php?view=upcoming")

with open("lms_calendar.html", "w", encoding="utf-8") as f:
    f.write(resp.text)
print("Saved calendar page to lms_calendar.html")

# Quick parse attempt
soup = BeautifulSoup(resp.text, "html.parser")
events = soup.find_all(class_="event")
print(f"\nFound {len(events)} calendar event blocks")

for i, event in enumerate(events[:10], 1):
    title_el = event.find(class_="eventname") or event.find("a")
    title = title_el.get_text(strip=True) if title_el else "?"
    print(f"  {i}. {title}")

print("\nDone. Please share lms_calendar.html content (or first 100 lines) for analysis.")
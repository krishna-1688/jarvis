"""
setup.py — one-time setup for Jarvis. Run from the project folder:

    python setup.py            # everything, first time (~5 minutes)
    python setup.py college    # just connect VTOP / LMS
    python setup.py profile    # just redo your name, tone, goals, routine
    python setup.py keys       # just the AI keys
    python setup.py mic        # just test the microphone
    python setup.py install    # just (re)install dependencies

Nothing leaves your laptop except the AI calls themselves: keys and college
logins go in backend/.env, everything about you in backend/profile.toml —
both git-ignored. Run it again any time; your current answers are the
defaults, so pressing Enter keeps them.
"""

import getpass
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
WHATSAPP = os.path.join(ROOT, "whatsapp_service")
ENV_PATH = os.path.join(BACKEND, ".env")
SECTIONS = ["install", "keys", "college", "profile", "mic"]

os.system("")  # turns on ANSI colours in the Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EMBER, DIM, OK, BAD, BOLD, END = "\033[38;5;209m", "\033[2m", "\033[32m", "\033[31m", "\033[1m", "\033[0m"


# ── small console helpers ─────────────────────────────────────────────

def title(text):
    print(f"\n{EMBER}{BOLD}── {text} {'─' * max(0, 58 - len(text))}{END}")


def note(text):
    print(f"{DIM}{text}{END}")


def good(text):
    print(f"{OK}✓{END} {text}")


def warn(text):
    print(f"{BAD}!{END} {text}")


def ask(prompt, default="", secret=False):
    shown = ("•" * 8 if secret else default) if default else ""
    label = f"{prompt}{f' {DIM}[{shown}]{END}' if shown else ''}: "
    try:
        value = getpass.getpass(label) if secret else input(label)
    except EOFError:        # input closed (piped / Ctrl+Z) — stop instead of looping
        raise KeyboardInterrupt from None
    return value.strip() or default


def yes(prompt, default=True):
    answer = ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
    return default if not answer else answer.startswith("y")


def choose(prompt, options, default):
    """options: list of (value, description)."""
    print(prompt)
    for i, (value, desc) in enumerate(options, 1):
        mark = f"{EMBER}›{END}" if value == default else " "
        print(f" {mark} {i}. {BOLD}{value}{END} {DIM}— {desc}{END}")
    values = [v for v, _ in options]
    while True:
        answer = ask("Pick a number or press Enter", str(values.index(default) + 1) if default in values else "1")
        if answer.isdigit() and 1 <= int(answer) <= len(values):
            return values[int(answer) - 1]
        if answer in values:
            return answer
        warn("Not one of the options.")


def run(cmd, cwd=ROOT, quiet=False):
    note("  $ " + " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=cwd, shell=(os.name == "nt" and cmd[0] in ("npm", "npx")),
                                capture_output=quiet, text=True)
    except FileNotFoundError:
        return False
    if result.returncode and quiet:
        print((result.stdout or "")[-1500:], (result.stderr or "")[-1500:])
    return result.returncode == 0


# ── .env ──────────────────────────────────────────────────────────────

def read_env():
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
                if m:
                    v = m.group(2).strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\") if v[0] == '"' else v[1:-1]
                    values[m.group(1)] = v
    return values


def _env_value(v):
    if re.fullmatch(r"[A-Za-z0-9_\-.@:/+,]*", v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(updates):
    """Update keys in place, keeping every other line (comments, settings
    the user added by hand) exactly as it was."""
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    else:
        with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"\s*#?\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in pending and (not line.lstrip().startswith("#") or pending[m.group(1)]):
            lines[i] = f"{m.group(1)}={_env_value(pending.pop(m.group(1)))}"
    lines += [f"{k}={_env_value(v)}" for k, v in pending.items()]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _http_ok(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-setup", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return None, str(getattr(e, "reason", e))


def check_groq(key):
    return _http_ok("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})


def check_gemini(key):
    return _http_ok(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")


# ── sections ──────────────────────────────────────────────────────────

def section_install():
    title("Installing")
    note("Python packages, the browser Jarvis drives, the wake-word model and the dashboard.")
    if not run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r",
                os.path.join(ROOT, "requirements.txt")]):
        warn("pip install failed — see the error above. On a fresh PC: install Python 3.11+ "
             "from python.org with 'Add to PATH' ticked, then run setup again.")
        return False
    good("Python packages")

    if run([sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"], quiet=True):
        good("Browser for web control and VIT portals")
    else:
        warn("Playwright's browser didn't install — web control won't work until "
             "`python -m playwright install --no-shell chromium` succeeds.")

    code = ("import openwakeword.utils as u; u.download_models(['hey_jarvis_v0.1'])")
    if run([sys.executable, "-c", code], quiet=True):
        good('Wake-word model ("Hey Jarvis")')
    else:
        warn("Couldn't download the wake-word model — check your internet and rerun `python setup.py install`.")

    if shutil.which("npm"):
        ok = run(["npm", "install", "--no-audit", "--no-fund"], cwd=FRONTEND, quiet=True) and \
             run(["npm", "run", "build"], cwd=FRONTEND, quiet=True)
        (good if ok else warn)("Dashboard" if ok else "Dashboard build failed — see above.")
    else:
        warn("Node.js isn't installed, so the dashboard is skipped (voice still works). "
             "Install Node.js LTS from nodejs.org and run `python setup.py install` again.")
    return True


def section_keys(env):
    title("AI keys")
    note("Jarvis thinks with Groq — free, no card needed. Get a key (starts with gsk_) at:")
    note("  https://console.groq.com/keys")
    updates = {}
    while True:
        key = ask("Groq API key", env.get("GROQ_API_KEY", ""), secret=True)
        if not key:
            warn("Jarvis can't run without it.")
            continue
        ok, err = check_groq(key)
        if ok:
            good("Groq key works")
            updates["GROQ_API_KEY"] = key
            break
        if ok is None:
            warn(f"Couldn't reach Groq ({err}) — saving the key anyway; it's checked again at startup.")
            updates["GROQ_API_KEY"] = key
            break
        warn(f"Groq rejected that key ({err}). Copy it again from console.groq.com/keys.")

    print()
    note("Optional backup brain for when Groq's free limits run out: a Google Gemini key")
    note("  https://aistudio.google.com/apikey   (Enter to skip)")
    gem = ask("Gemini API key", env.get("GEMINI_API_KEY", ""), secret=True)
    if gem and gem != env.get("GEMINI_API_KEY"):
        ok, err = check_gemini(gem)
        (good if ok else warn)("Gemini key works" if ok else f"Gemini check failed ({err}) — saved anyway.")
    updates["GEMINI_API_KEY"] = gem
    write_env(updates)
    env.update(updates)


def section_college(env):
    title("College (VIT)")
    note("Connect VTOP for attendance, marks, exams and timetable; LMS for assignment deadlines.")
    note("Your password is stored only in backend/.env on this laptop and sent only to VIT.")
    updates = {}
    if yes("Are you a VIT student and want to connect VTOP?", True):
        while True:
            reg = ask("Registration number (e.g. 24BCE1234)", env.get("VTOP_USERNAME", "")).upper()
            if re.fullmatch(r"\d{2}[A-Z]{3}\d{4}", reg):
                break
            warn("That doesn't look like a VIT registration number.")
        pw = ask("VTOP password", env.get("VTOP_PASSWORD", ""), secret=True)
        updates.update(VTOP_USERNAME=reg, VTOP_PASSWORD=pw)
        from core import profile
        campus = choose("Campus", [("chennai", "VIT Chennai (tested)"), ("vellore", "VIT Vellore (experimental)")],
                        profile.get()["college"].get("campus", "chennai"))
        profile.save({"college": {"campus": campus}})
        good(f"VTOP connected for {reg} — first sync runs when Jarvis starts (captcha is solved automatically).")

        if yes("Connect LMS (Moodle) for assignments too?", True):
            updates["LMS_USERNAME"] = ask("LMS username", env.get("LMS_USERNAME") or reg)
            updates["LMS_PASSWORD"] = ask("LMS password", env.get("LMS_PASSWORD", ""), secret=True)
        else:
            updates.update(LMS_USERNAME="", LMS_PASSWORD="")
    else:
        updates.update(VTOP_USERNAME="", VTOP_PASSWORD="", LMS_USERNAME="", LMS_PASSWORD="")
        good("No college portals — everything else works the same.")

    print()
    note("Optional: a WhatsApp morning brief and reminders (needs the whatsapp_service; Enter to skip).")
    num = ask("Your WhatsApp number with country code (e.g. 919876543210)", env.get("MY_WHATSAPP_NUMBER", ""))
    updates["MY_WHATSAPP_NUMBER"] = re.sub(r"\D", "", num)
    if updates["MY_WHATSAPP_NUMBER"] and shutil.which("npm") and \
            not os.path.isdir(os.path.join(WHATSAPP, "node_modules")):
        run(["npm", "install", "--no-audit", "--no-fund"], cwd=WHATSAPP, quiet=True)
        note("Start it once with `node server.js` in whatsapp_service and scan the QR code.")
    write_env(updates)
    env.update(updates)


TONES = [("witty", "sharp and dry, like Iron Man's Jarvis"), ("friendly", "warm and encouraging"),
         ("formal", "polite and precise, a butler"), ("tough-love", "calls out procrastination"),
         ("calm", "gentle and reassuring")]
VOICES = [("en-GB-RyanNeural", "British, male"), ("en-GB-SoniaNeural", "British, female"),
          ("en-IN-PrabhatNeural", "Indian, male"), ("en-IN-NeerjaNeural", "Indian, female"),
          ("en-US-GuyNeural", "American, male"), ("en-US-JennyNeural", "American, female")]


def _listing(value):
    return ", ".join(value) if isinstance(value, list) else str(value or "")


def section_profile():
    from core import profile
    p = profile.get()
    you, style, goals, routine, voice = p["you"], p["style"], p["goals"], p["routine"], p["voice"]
    title("About you")
    note("This is what makes Jarvis yours. Everything is optional — Enter skips or keeps the current answer.")
    note("You can change any of it later from the dashboard (the 'You' panel, Alt+9).")
    name = ask("Your name", "" if you.get("name") == "friend" else you.get("name", ""))
    call_me = ask('What should Jarvis call you? ("boss", "sir", your name, or "-" for nothing)',
                  you.get("call_me", "boss"))
    about = ask("A line about you (course, year, interests)", you.get("about", ""))

    title("How Jarvis talks")
    tone = choose("Tone", TONES, style.get("tone", "witty"))
    length = choose("Reply length", [("short", "quick answers"), ("balanced", "a bit more"),
                                     ("detailed", "thorough, with examples")], style.get("reply_length", "short"))
    language = ask("Language (e.g. English, Hinglish, English with some Tamil)", style.get("language", "English"))
    voice_name = choose("Voice", VOICES, voice.get("tts_voice", "en-GB-RyanNeural"))

    title("What you're working towards")
    note("Jarvis ties motivation and advice to these.")
    dream = ask("Your dream", goals.get("dream", ""))
    ambitions = ask("Ambitions, comma-separated (e.g. 9+ CGPA, product-company placement)",
                    _listing(goals.get("ambitions")))
    motivation = ask("What drives you", goals.get("motivation", ""))

    title("Your routine")
    wake = ask("Usually wake up at (e.g. 07:00)", routine.get("wake_time", ""))
    sleep = ask("Usually sleep at (e.g. 23:30)", routine.get("sleep_time", ""))
    study = ask("Best time to study (e.g. 20:00-22:00)", routine.get("study_time", ""))
    habits = ask("Habits, comma-separated (e.g. gym at 6pm, revise before bed)", _listing(routine.get("habits")))
    favourites = ask("Favourite things, comma-separated (e.g. AI, cricket, lo-fi)", _listing(routine.get("favourites")))

    profile.save({
        "you": {"name": name or "friend", "call_me": "" if call_me == "-" else call_me, "about": about},
        "style": {"tone": tone, "reply_length": length, "language": language},
        "goals": {"dream": dream, "ambitions": ambitions, "motivation": motivation},
        "routine": {"wake_time": wake, "sleep_time": sleep, "study_time": study,
                    "habits": habits, "favourites": favourites},
        "voice": {"tts_voice": voice_name},
    })
    good(f"Saved — Jarvis will greet you with “{profile.wake_reply()}”")


def section_mic():
    title("Microphone")
    note("Say “Hey Jarvis” a few times in the next 15 seconds. (Jarvis itself must not be running.)")
    if not yes("Start the mic test?", True):
        return
    run([sys.executable, "mic_diag.py"], cwd=BACKEND)
    note("A score above the threshold on “Hey Jarvis” means wake-up works. If it stays low,")
    note("add JARVIS_WAKE_THRESHOLD=0.2 to backend/.env, or move closer to the mic.")


# ── main ──────────────────────────────────────────────────────────────

def main():
    if sys.platform != "win32":
        warn("Jarvis controls Windows (apps, volume, windows) — it runs on Windows 10/11 only for now.")
        return 1
    if not ((3, 11) <= sys.version_info[:2] <= (3, 12)):
        warn(f"Jarvis needs Python 3.11 or 3.12 (this is {sys.version.split()[0]}). Newer versions don't")
        warn("have ready-made builds of some packages yet. Get 3.12 (it can sit alongside others):")
        warn("  https://www.python.org/downloads/release/python-31210/  — then run setup.bat.")
        return 1
    sys.path.insert(0, BACKEND)

    wanted = [a.lower() for a in sys.argv[1:]]
    unknown = [a for a in wanted if a not in SECTIONS]
    if unknown:
        print(__doc__)
        return 1
    first_run = not os.path.exists(ENV_PATH)
    todo = wanted or SECTIONS

    print(f"\n{EMBER}{BOLD}  J.A.R.V.I.S — setup{END}")
    note("  Your personal voice assistant. Keys, logins and profile stay on this laptop.")
    env = read_env()
    try:
        if "install" in todo and not section_install() and first_run:
            return 1
        if "keys" in todo or not env.get("GROQ_API_KEY"):
            section_keys(env)
        if "college" in todo:
            section_college(env)
        if "profile" in todo:
            section_profile()
        if "mic" in todo:
            section_mic()
    except KeyboardInterrupt:
        print()
        warn("Stopped — anything already saved is kept. Run setup again to finish.")
        return 1

    title("Done")
    good("Start Jarvis with start.bat (or `python backend/run.py`), then say “Hey Jarvis”.")
    note("Try: “what's my attendance”, “remind me to submit the report friday”, “open YouTube”,")
    note("     “start focus for 25 minutes”, “what's on my screen”. Ctrl+J opens the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

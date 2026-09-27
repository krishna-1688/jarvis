import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import shutil
import subprocess
import psutil
from rapidfuzz import fuzz, process as rf_process

from features.base import FeatureResult

# pyautogui / pygetwindow / pyperclip / pycaw (~20 MB with their deps) are
# imported inside the functions that use them: the backend runs all day,
# and most of that time nobody is taking screenshots or moving windows.
def _gw():
    import pygetwindow
    return pygetwindow

# ── Volume Control (real level via pycaw, not blind keypresses) ─
_volume_interface = None

def _ensure_com():
    """pycaw's AudioUtilities calls comtypes internals directly — on
    whatever thread first touches it, COM must already be initialized or
    it raises "CoInitialize has not been called" (confirmed live via
    features/spotify.py's identical pywinauto issue: FastAPI/Starlette
    runs sync route handlers on a worker-thread pool, and COM state is
    per-thread). Safe to call repeatedly."""
    import comtypes
    try:
        comtypes.CoInitialize()
    except (OSError, comtypes.COMError):
        pass

def _volume_iface():
    global _volume_interface
    if _volume_interface is None:
        _ensure_com()
        from pycaw.pycaw import AudioUtilities
        _volume_interface = AudioUtilities.GetSpeakers().EndpointVolume
    return _volume_interface

def get_volume_level():
    level = round(_volume_iface().GetMasterVolumeLevelScalar() * 100)
    return True, f"Volume is at {level}%"

def is_muted() -> bool:
    return bool(_volume_iface().GetMute())

def volume_up():
    iface = _volume_iface()
    level = min(1.0, iface.GetMasterVolumeLevelScalar() + 0.10)
    iface.SetMasterVolumeLevelScalar(level, None)
    return True, f"Volume increased to {round(level * 100)}%"

def volume_down():
    iface = _volume_iface()
    level = max(0.0, iface.GetMasterVolumeLevelScalar() - 0.10)
    iface.SetMasterVolumeLevelScalar(level, None)
    return True, f"Volume decreased to {round(level * 100)}%"

def mute_volume():
    # Real state check instead of a blind toggle keypress — calling this
    # while already muted used to just un-mute it again (the toggle had
    # no idea what state it was actually in).
    if is_muted():
        return True, "Already muted"
    _volume_iface().SetMute(1, None)
    return True, "Muted"

def unmute_volume():
    if not is_muted():
        return True, "Already unmuted"
    _volume_iface().SetMute(0, None)
    return True, "Unmuted"

# ── System info (psutil) ──────────────────
def get_system_status(topic: str = None):
    if topic == "battery":
        batt = psutil.sensors_battery()
        if not batt:
            return False, "No battery detected — this looks like a desktop."
        plugged = " (plugged in)" if batt.power_plugged else ""
        return True, f"Battery is at {round(batt.percent)}%{plugged}"
    if topic == "cpu":
        return True, f"CPU usage is at {round(psutil.cpu_percent(interval=0.5))}%"
    if topic == "memory":
        return True, f"Memory usage is at {round(psutil.virtual_memory().percent)}%"
    if topic == "disk":
        return True, f"Disk is at {round(psutil.disk_usage(os.path.expanduser('~')).percent)}% full"

    parts = [f"CPU {round(psutil.cpu_percent(interval=0.5))}%",
             f"Memory {round(psutil.virtual_memory().percent)}%",
             f"Disk {round(psutil.disk_usage(os.path.expanduser('~')).percent)}%"]
    batt = psutil.sensors_battery()
    if batt:
        parts.append(f"Battery {round(batt.percent)}%")
    return True, "System status — " + ", ".join(parts)

# ── Clipboard ──────────────────────────────
def get_clipboard_text():
    import pyperclip
    text = pyperclip.paste()
    if not text or not text.strip():
        return False, "Clipboard is empty"
    preview = text if len(text) <= 200 else text[:200] + "..."
    return True, f"Clipboard: {preview}"

# ── App Control ───────────────────────────
APP_MAP = {
    "chrome":         "chrome.exe",
    "google":         "chrome.exe",
    "spotify":        "spotify.exe",
    "vs code":        "code.exe",
    "vscode":         "code.exe",
    "notepad":        "notepad.exe",
    "calculator":     "calc.exe",
    "whatsapp":       "whatsapp.exe",
    "file explorer":  "explorer.exe",
    "explorer":       "explorer.exe",
    "task manager":   "taskmgr.exe",
    "discord":        "discord.exe",
    "telegram":       "telegram.exe",
    "word":           "winword.exe",
    "excel":          "excel.exe",
    "powerpoint":     "powerpnt.exe",
    "outlook":        "outlook.exe",
    "edge":           "msedge.exe",
    "firefox":        "firefox.exe",
    "zoom":           "Zoom.exe",
    "teams":          "ms-teams.exe",
    "slack":          "slack.exe",
    "steam":          "steam.exe",
    "vlc":            "vlc.exe",
    "paint":          "mspaint.exe",
    "snipping tool":  "SnippingTool.exe",
    "cmd":            "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell":     "powershell.exe",
}

def _resolve_app_fuzzy(app_name: str, threshold: int = 75):
    match = rf_process.extractOne(app_name, APP_MAP.keys(), scorer=fuzz.WRatio)
    if match and match[1] >= threshold:
        return APP_MAP[match[0]]
    return None

def open_app(app_name):
    app = app_name.lower().strip()
    exe = APP_MAP.get(app) or _resolve_app_fuzzy(app)
    if exe:
        try:
            subprocess.Popen(exe)
            return True, f"Opening {app_name}"
        except Exception:
            return False, f"Could not open {app_name}"

    resolved_path = shutil.which(app)
    if resolved_path:
        try:
            subprocess.Popen(resolved_path)
            return True, f"Opening {app_name}"
        except Exception:
            return False, f"Could not open {app_name}"

    try:
        subprocess.Popen(app)
        return True, f"Trying to open {app_name}"
    except Exception:
        return False, f"App not found: {app_name}"

def kill_process_fuzzy(name_query: str, threshold: int = 85):
    all_names = sorted(set(
        p.info['name'] for p in psutil.process_iter(['name']) if p.info['name']
    ))
    match = rf_process.extractOne(name_query, all_names, scorer=fuzz.WRatio)
    if not match or match[1] < threshold:
        return False, f"No running process matching '{name_query}'"
    target = match[0]
    killed = 0
    for p in psutil.process_iter(['name']):
        if p.info['name'] == target:
            try:
                p.kill()
                killed += 1
            except Exception:
                pass
    return (killed > 0), (f"Closed {target}" if killed else f"Couldn't close {target}")

def list_processes(name_filter: str = None):
    names = sorted(set(
        p.info['name'] for p in psutil.process_iter(['name']) if p.info['name']
    ))
    if name_filter:
        names = [n for n in names if name_filter.lower() in n.lower()]
    if not names:
        return False, "No matching processes found"
    preview = ", ".join(names[:15])
    more = f" (+{len(names) - 15} more)" if len(names) > 15 else ""
    return True, f"Running: {preview}{more}"

def close_app(app_name):
    app = app_name.lower().strip()
    exe = APP_MAP.get(app) or _resolve_app_fuzzy(app) or (app + ".exe")
    killed = False
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and exe.lower() in proc.info['name'].lower():
            proc.kill()
            killed = True
    if killed:
        return True, f"Closed {app_name}"
    # Nothing matched by exe-name guess — try a fuzzy match against the
    # real list of running processes instead of just giving up.
    return kill_process_fuzzy(app_name)

# ── Window Management ─────────────────────
def _resolve_window(title_query: str, threshold: int = 70):
    titles = [t for t in _gw().getAllTitles() if t.strip()]
    if not titles:
        return None
    match = rf_process.extractOne(title_query, titles, scorer=fuzz.WRatio)
    if not match or match[1] < threshold:
        return None
    wins = _gw().getWindowsWithTitle(match[0])
    return wins[0] if wins else None

def list_windows():
    titles = [t for t in _gw().getAllTitles() if t.strip()]
    if not titles:
        return False, "No open windows found"
    preview = ", ".join(titles[:15])
    more = f" (+{len(titles) - 15} more)" if len(titles) > 15 else ""
    return True, f"Open windows: {preview}{more}"

def switch_to_window(title_query: str):
    win = _resolve_window(title_query)
    if not win:
        return False, f"No window matching '{title_query}'"
    try:
        win.activate()
        return True, f"Switched to {win.title}"
    except Exception:
        return False, f"Found '{win.title}' but couldn't bring it to front"

def minimize_window(title_query: str):
    win = _resolve_window(title_query)
    if not win:
        return False, f"No window matching '{title_query}'"
    try:
        win.minimize()
        return True, f"Minimized {win.title}"
    except Exception:
        return False, f"Couldn't minimize '{win.title}'"

def maximize_window(title_query: str):
    win = _resolve_window(title_query)
    if not win:
        return False, f"No window matching '{title_query}'"
    try:
        win.maximize()
        return True, f"Maximized {win.title}"
    except Exception:
        return False, f"Couldn't maximize '{win.title}'"

def close_window(title_query: str):
    win = _resolve_window(title_query)
    if not win:
        return False, f"No window matching '{title_query}'"
    try:
        win.close()
        return True, f"Closed {win.title}"
    except Exception:
        return False, f"Couldn't close '{win.title}'"

# ── Screenshot ────────────────────────────
def take_screenshot():
    path = os.path.expanduser("~/Desktop/jarvis_screenshot.png")
    import pyautogui
    screenshot = pyautogui.screenshot()
    screenshot.save(path)
    return True, "Screenshot saved to Desktop"

# ── Brightness ────────────────────────────
def brightness_up():
    try:
        import screen_brightness_control as sbc
        current = sbc.get_brightness()[0]
        sbc.set_brightness(min(100, current + 10))
        return True, "Brightness increased"
    except Exception:
        return False, "Brightness control not available"

def brightness_down():
    try:
        import screen_brightness_control as sbc
        current = sbc.get_brightness()[0]
        sbc.set_brightness(max(0, current - 10))
        return True, "Brightness decreased"
    except Exception:
        return False, "Brightness control not available"

# ── System Control ────────────────────────
# CRITICAL SAFETY NOTE: shutdown/restart used to execute the real OS
# command IMMEDIATELY off a bare "shutdown" / "restart" substring match
# in handle_pc_command below, no confirmation at all — which meant a
# sentence like "shutdown jarvis" (meant to stop the ASSISTANT) actually
# scheduled a REAL Windows shutdown in 10 seconds. Confirmed to have
# actually fired once. Fixed with two independent layers: (1)
# handle_pc_command now requires an explicit "pc"/"computer"/"laptop"/
# "system"/"windows" word AND excludes anything mentioning "jarvis"
# before even considering this a shutdown/restart request, and (2) these
# functions no longer execute anything directly — they only ever ARM a
# pending confirmation; only confirm_pending_system_action (after an
# explicit "yes") actually calls os.system(...).

_pending_system_action = {"action": None}  # "shutdown" | "restart" | None

def shutdown_pc():
    _pending_system_action["action"] = "shutdown"
    return True, "Shut down your PC? Say yes to confirm — this can't be undone once it starts."

def restart_pc():
    _pending_system_action["action"] = "restart"
    return True, "Restart your PC? Say yes to confirm."

def sleep_pc():
    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    return True, "Going to sleep"

def cancel_shutdown():
    _pending_system_action["action"] = None
    os.system("shutdown /a")
    return True, "Shutdown cancelled"


def has_pending_system_action() -> bool:
    return _pending_system_action["action"] is not None


def confirm_pending_system_action(confirmed: bool) -> tuple:
    """
    Called only after the user has explicitly said yes/no to a pending
    shutdown/restart (see server.py's handle_pc_confirm_followup) — this
    is the ONLY place that actually calls the real OS command.
    """
    action = _pending_system_action["action"]
    _pending_system_action["action"] = None

    if not action:
        return False, "Nothing pending to confirm."
    if not confirmed:
        return True, "Okay, cancelled — nothing's happening to your PC."

    if action == "shutdown":
        os.system("shutdown /s /t 10")
        return True, "Confirmed — shutting down in 10 seconds. Say 'cancel shutdown' to stop it."
    if action == "restart":
        os.system("shutdown /r /t 10")
        return True, "Confirmed — restarting in 10 seconds. Say 'cancel shutdown' to stop it."
    return False, "Unknown pending action."

# ── File Search ───────────────────────────
_SEARCH_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "env",
                     "site-packages", "dist", "build", ".cache", "AppData"}
_SEARCH_MAX_FILES = 60000

def search_file(filename):
    """Opens the first file whose name contains `filename`. Skips
    dependency/VCS folders and stops at the first hit — the old walk
    scanned every node_modules tree under Desktop before answering."""
    filename = (filename or "").strip().lower()
    if len(filename) < 2:
        return False, "Which file should I look for?"
    search_paths = [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
    ]
    scanned = 0
    for path in search_paths:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SEARCH_SKIP_DIRS and not d.startswith(".")]
            for file in files:
                scanned += 1
                if filename in file.lower():
                    found = os.path.join(root, file)
                    os.startfile(found)
                    return True, f"Found and opened {found}"
            if scanned > _SEARCH_MAX_FILES:
                return False, f"Couldn't find {filename} quickly — try a more specific name."
    return False, f"File {filename} not found"

# ── Main Handler ──────────────────────────
_UP_WORDS   = ["up", "increase", "raise", "louder", "brighter", "more"]
_DOWN_WORDS = ["down", "decrease", "lower", "quieter", "dimmer", "less", "reduce"]

# SAFETY (confirmed incident): shutdown/restart/sleep used to match on a
# bare substring anywhere in the sentence — "shutdown jarvis" triggered
# an ACTUAL Windows shutdown (os.system("shutdown /s /t 10")), not just
# closing the app, because "shutdown" is a substring of "shutdown
# jarvis" too. core/router.py now intercepts anything mentioning
# "jarvis" alongside shutdown/restart/exit before this function is ever
# reached, but this is a second, independent layer here: these three
# genuinely destructive OS actions now require either an explicit PC/
# computer/system reference, or the utterance being essentially JUST
# the bare command (nothing else) — never a bare keyword buried inside
# an unrelated or ambiguous sentence.
_PC_TARGET_WORDS = ("pc", "computer", "system", "laptop", "machine")
_BARE_SHUTDOWN_PHRASES = {"shutdown", "shut down", "shutdown please", "shut down please"}
_BARE_RESTART_PHRASES = {"restart", "restart please", "reboot"}
_BARE_SLEEP_PHRASES = {"sleep", "sleep please", "go to sleep"}

def _is_explicit_pc_action(text: str, action_words: tuple, bare_phrases: set) -> bool:
    if "jarvis" in text:
        return False
    if not any(w in text for w in action_words):
        return False
    stripped = text.strip().rstrip("?.!")
    if stripped in bare_phrases:
        return True
    return any(w in text for w in _PC_TARGET_WORDS)

_MUTE_STATUS_PHRASES = ("is it muted", "is muted", "am i muted", "is the volume muted", "mute status")
_VOLUME_LEVEL_PHRASES = ("what's the volume", "whats the volume", "what is the volume", "volume level", "how loud")

_WINDOW_TRIGGER_PHRASES = (
    "switch to window", "switch window to", "bring up window",
    "minimize window", "maximize window", "close window",
)

def _extract_window_query(text: str) -> str:
    q = text
    for phrase in _WINDOW_TRIGGER_PHRASES:
        q = q.replace(phrase, "")
    for word in ("window", "switch", "minimize", "maximize", "close", "to", "the"):
        q = q.replace(word, "")
    return " ".join(q.split()).strip()

def _has_word(text: str, *words) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)

_APP_FILLER_RE = re.compile(
    r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+|can\s+you\s+|could\s+you\s+|just\s+)*"
)
_APP_CMD_RE = re.compile(
    r"^(open|launch|start|run|close|quit|exit|kill)\s+(?:the\s+|my\s+|up\s+)?(.+?)"
    r"(?:\s+(?:app|application|program))?(?:\s+(?:please|for me|now))*[.!?]*$"
)
_FIND_RE = re.compile(r"^(?:find|search\s+for|locate|search\s+my\s+files\s+for)\s+(?:the\s+|my\s+|a\s+)?(?:file\s+|folder\s+|document\s+|pdf\s+)?(?:called\s+|named\s+)?(.+?)[.!?]*$")


def handle_pc_command(text) -> "FeatureResult | None":
    text = text.lower().strip()
    cmd = _APP_FILLER_RE.sub("", text)

    # "cancel shutdown" must be checked before the bare "shutdown" check
    # below — "shutdown" is a substring of "cancel shutdown".
    if "cancel shutdown" in text or "cancel the shutdown" in text or "cancel restart" in text or "cancel the restart" in text:
        ok, msg = cancel_shutdown()
    # Status QUERIES before the mute/unmute actions — "is it muted" must
    # not mute the PC.
    elif any(p in text for p in _MUTE_STATUS_PHRASES) or any(p in text for p in _VOLUME_LEVEL_PHRASES):
        ok, msg = get_volume_level()
    # Whole words only: "commute" used to contain "mute".
    elif _has_word(text, "unmute"):
        ok, msg = unmute_volume()
    elif _has_word(text, "mute"):
        ok, msg = mute_volume()
    elif _has_word(text, "volume", "sound") and _has_word(text, *_UP_WORDS):
        ok, msg = volume_up()
    elif _has_word(text, "volume", "sound") and _has_word(text, *_DOWN_WORDS):
        ok, msg = volume_down()
    elif _has_word(text, "screenshot"):
        ok, msg = take_screenshot()
    elif _has_word(text, "brightness") and _has_word(text, *_UP_WORDS):
        ok, msg = brightness_up()
    elif _has_word(text, "brightness") and _has_word(text, *_DOWN_WORDS):
        ok, msg = brightness_down()
    elif _has_word(text, "battery"):
        ok, msg = get_system_status("battery")
    elif _has_word(text, "cpu"):
        ok, msg = get_system_status("cpu")
    elif _has_word(text, "memory", "ram"):
        ok, msg = get_system_status("memory")
    elif _has_word(text, "disk space", "disk usage", "storage") or (_has_word(text, "disk") and "how full" in text):
        ok, msg = get_system_status("disk")
    elif "system status" in text or "system info" in text:
        ok, msg = get_system_status()
    elif "clipboard" in text or "what did i copy" in text or "what's copied" in text or "whats copied" in text:
        ok, msg = get_clipboard_text()
    # Process-listing queries before the generic open/close branch —
    # "what apps are open" contains "open".
    elif "running processes" in text or "what's running" in text or "whats running" in text             or "what apps are open" in text or "list processes" in text:
        ok, msg = list_processes()
    # Window management requires the literal word "window".
    elif _has_word(text, "window", "windows") and ("switch" in text or "bring up" in text):
        ok, msg = switch_to_window(_extract_window_query(text))
    elif _has_word(text, "window", "windows") and ("minimize" in text or "minimise" in text):
        ok, msg = minimize_window(_extract_window_query(text))
    elif _has_word(text, "window", "windows") and ("maximize" in text or "maximise" in text):
        ok, msg = maximize_window(_extract_window_query(text))
    elif _has_word(text, "window") and "close" in text:
        ok, msg = close_window(_extract_window_query(text))
    elif _has_word(text, "window", "windows") and ("list" in text or "what windows" in text):
        ok, msg = list_windows()
    elif _is_explicit_pc_action(text, ("shutdown", "shut down"), _BARE_SHUTDOWN_PHRASES):
        ok, msg = shutdown_pc()
    elif _is_explicit_pc_action(text, ("restart", "reboot"), _BARE_RESTART_PHRASES):
        ok, msg = restart_pc()
    elif _is_explicit_pc_action(text, ("sleep", "hibernate"), _BARE_SLEEP_PHRASES):
        ok, msg = sleep_pc()
    elif _APP_CMD_RE.match(cmd):
        # Verb must START the command and the target is taken verbatim
        # after it — "how close am I to 75%" used to reach close_app()
        # (which falls back to fuzzy-killing a running process) with
        # "how  am i to 75%" as the app name.
        verb, app = _APP_CMD_RE.match(cmd).groups()
        app = app.strip()
        if verb in ("open", "launch", "start", "run"):
            ok, msg = open_app(app)
        else:
            ok, msg = close_app(app)
    elif _FIND_RE.match(cmd):
        ok, msg = search_file(_FIND_RE.match(cmd).group(1))
    else:
        return None

    return FeatureResult(ok=ok, data={}, display=msg, spoken=msg)

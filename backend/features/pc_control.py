import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import pyautogui
import psutil
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL

from features.base import FeatureResult

# ── Volume Control (using pyautogui keys) ─
def volume_up():
    for _ in range(5):
        pyautogui.press('volumeup')
    return True, "Volume increased"

def volume_down():
    for _ in range(5):
        pyautogui.press('volumedown')
    return True, "Volume decreased"

def mute_volume():
    pyautogui.press('volumemute')
    return True, "Muted"

def unmute_volume():
    pyautogui.press('volumemute')
    return True, "Unmuted"

# ── App Control ───────────────────────────
APP_MAP = {
    "chrome":        "chrome.exe",
    "google":        "chrome.exe",
    "spotify":       "spotify.exe",
    "vs code":       "code.exe",
    "vscode":        "code.exe",
    "notepad":       "notepad.exe",
    "calculator":    "calc.exe",
    "whatsapp":      "whatsapp.exe",
    "file explorer": "explorer.exe",
    "explorer":      "explorer.exe",
    "task manager":  "taskmgr.exe",
    "discord":       "discord.exe",
    "telegram":      "telegram.exe",
}

def open_app(app_name):
    app = app_name.lower().strip()
    if app in APP_MAP:
        try:
            subprocess.Popen(APP_MAP[app])
            return True, f"Opening {app_name}"
        except Exception:
            return False, f"Could not open {app_name}"
    else:
        try:
            subprocess.Popen(app)
            return True, f"Trying to open {app_name}"
        except Exception:
            return False, f"App not found: {app_name}"

def close_app(app_name):
    app = app_name.lower().strip()
    exe = APP_MAP.get(app, app + ".exe")
    killed = False
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and exe.lower() in proc.info['name'].lower():
            proc.kill()
            killed = True
    return (True, f"Closed {app_name}") if killed else (False, f"{app_name} was not running")

# ── Screenshot ────────────────────────────
def take_screenshot():
    path = os.path.expanduser("~/Desktop/jarvis_screenshot.png")
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
def search_file(filename):
    search_paths = [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
    ]
    found = []
    for path in search_paths:
        for root, dirs, files in os.walk(path):
            for file in files:
                if filename.lower() in file.lower():
                    found.append(os.path.join(root, file))
    if found:
        os.startfile(found[0])
        return True, f"Found and opened {found[0]}"
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
_BARE_RESTART_PHRASES = {"restart", "restart please"}
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

def handle_pc_command(text) -> "FeatureResult | None":
    text = text.lower()

    # "cancel shutdown" must be checked before the bare "shutdown" check
    # below — "shutdown" is a substring of "cancel shutdown", so the
    # bare check would otherwise ALWAYS fire first and actually shut
    # the PC down instead of cancelling a pending one.
    if "cancel shutdown" in text or "cancel the shutdown" in text:
        ok, msg = cancel_shutdown()
    # Same substring trap for "unmute" containing "mute" — check it first.
    elif "unmute" in text:
        ok, msg = unmute_volume()
    elif "mute" in text:
        ok, msg = mute_volume()
    # Loosened from exact "volume up"/"increase volume" phrase matching
    # so natural insertions ("increase THE volume", "turn volume up a
    # bit") still match instead of silently falling through to chat.
    elif "volume" in text and any(w in text for w in _UP_WORDS):
        ok, msg = volume_up()
    elif "volume" in text and any(w in text for w in _DOWN_WORDS):
        ok, msg = volume_down()
    elif "screenshot" in text:
        ok, msg = take_screenshot()
    elif "brightness" in text and any(w in text for w in _UP_WORDS):
        ok, msg = brightness_up()
    elif "brightness" in text and any(w in text for w in _DOWN_WORDS):
        ok, msg = brightness_down()
    elif _is_explicit_pc_action(text, ("shutdown", "shut down"), _BARE_SHUTDOWN_PHRASES):
        ok, msg = shutdown_pc()
    elif _is_explicit_pc_action(text, ("restart",), _BARE_RESTART_PHRASES):
        ok, msg = restart_pc()
    elif _is_explicit_pc_action(text, ("sleep",), _BARE_SLEEP_PHRASES):
        ok, msg = sleep_pc()
    elif "open" in text:
        app = text.replace("open", "").strip()
        ok, msg = open_app(app)
    elif "close" in text:
        app = text.replace("close", "").strip()
        ok, msg = close_app(app)
    elif "find" in text or "search for" in text:
        filename = text.replace("find", "").replace("search for", "").strip()
        ok, msg = search_file(filename)
    else:
        return None

    return FeatureResult(ok=ok, data={}, display=msg, spoken=msg)
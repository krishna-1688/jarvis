"""
jarvis.py — Voice client entry point.

Owns audio I/O only (wake word, STT, TTS) — all command handling now
lives in server.py, reached over HTTP. Both this voice loop and any
future UI hit the same backend, so there's one brain, not two.
"""

import sys
import os
import atexit

# Same fix as server.py: Windows' default console codepage (cp1252)
# can't encode the box-drawing characters in this file's own startup
# banner (═, →, etc.) — under that codepage `python jarvis.py` crashes
# with UnicodeEncodeError before ever reaching the wake-word listener,
# i.e. before voice has any chance of working at all. Force UTF-8
# stdout/stderr regardless of the console's codepage.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import time
import threading

import requests

from core.voice import (
    speak, listen, init_microphone,
    is_shutdown_requested, _is_processing, set_expecting_confirmation
)
from core.wakeword import start_wakeword_listener, set_busy
from core.voice_bridge import push_voice_event

SERVER_URL     = "http://localhost:8000"
# Live VTOP fetches (login + captcha, sometimes a retry) have been
# observed taking 60-90s worst case — keep well above that so a slow
# but successful fetch doesn't look like a dead backend.
SERVER_TIMEOUT = 150


# ══════════════════════════════════════════
#   SERVER CLIENT
# ══════════════════════════════════════════

def call_server(text: str) -> dict:
    """POSTs to the backend's /command endpoint. Never raises — ANY
    failure (network, timeout, malformed response, or anything else
    unforeseen) becomes a spoken/displayed error instead of crashing
    the voice loop. Deliberately catches Exception broadly, not just
    requests' own exception types, since a client crash here takes
    down the entire voice assistant with it."""
    try:
        r = requests.post(
            f"{SERVER_URL}/command", json={"text": text}, timeout=SERVER_TIMEOUT
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        msg = f"Can't reach the Jarvis backend — is server.py running? ({e})"
        return {"ok": False, "display": msg, "spoken": msg, "data": {}, "expecting_confirmation": False}


def wait_for_server(timeout: float = 20.0) -> bool:
    """
    Polls /health until the backend is up, or gives up after `timeout`
    seconds.

    Per-attempt timeout is 5s, not the 2s it used to be: /health's own
    WhatsApp-reachability check (features/whatsapp.py's
    get_whatsapp_status) has a 3s timeout of its own, which is the
    NORMAL path whenever whatsapp_service isn't running — a 2s attempt
    timeout meant every single poll here hit ReadTimeout first and this
    always reported "Backend not reachable" even when the backend was
    completely healthy, just slower than 2s to answer.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{SERVER_URL}/health", timeout=5)
            if r.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
    return False


HEARTBEAT_INTERVAL_S = 8

def _heartbeat_worker():
    """
    Real voice events (wake/listening/speaking/etc.) only fire when
    something actually happens — with nobody talking, the frontend has
    no way to tell "jarvis.py just hasn't heard anything" apart from
    "jarvis.py isn't running at all" (exactly the failure mode S.1a's
    diagnostic found: server.py was up, WS was fine, but this process
    simply wasn't started). A periodic heartbeat gives the frontend a
    liveness signal independent of actual conversation.
    """
    while not is_shutdown_requested():
        push_voice_event({"type": "voice_heartbeat"})
        time.sleep(HEARTBEAT_INTERVAL_S)


def process(user_input: str) -> tuple[str, str]:
    _is_processing.set()
    push_voice_event({"type": "processing_start"})
    try:
        response = call_server(user_input)
        set_expecting_confirmation(bool(response.get("expecting_confirmation", False)))
        return response.get("display", ""), response.get("spoken", "")
    finally:
        _is_processing.clear()
        push_voice_event({"type": "processing_end"})


# ══════════════════════════════════════════
#   RESPOND
# ══════════════════════════════════════════

def respond(terminal_text: str, spoken_text: str):
    if terminal_text != spoken_text:
        print(f"\n📊 Full data:\n{terminal_text}")
    # So the Electron frontend's log shows both sides of a voice
    # conversation, not just the user's transcribed half — pushed before
    # speak() so it lands slightly ahead of the speaking_start event.
    push_voice_event({"type": "reply", "text": terminal_text})
    speak(spoken_text)


# ══════════════════════════════════════════
#   CONVERSATION LOOP
# ══════════════════════════════════════════

GOODBYE_PHRASES = [
    "goodbye jarvis", "go offline", "that's all",
    "thats all", "stop jarvis", "bye jarvis", "sleep jarvis"
]

# How many consecutive empty listen() cycles before dropping back to
# standby. Each silent cycle is one LISTEN_TIMEOUT (8s) in core/voice.py,
# so SILENCE_LIMIT=4 gives ~32s of "still thinking" tolerance instead of
# the old ~16s, which felt like it kicked you out mid-thought.
SILENCE_LIMIT = 4
EMPTY_LIMIT   = 10

def conversation_loop(first_input: str = None, typed_wake: bool = False):
    print("\n" + "─"*44)
    print("  💬 Active — speak or type + Enter")
    print(f"  Silence x{SILENCE_LIMIT} → standby  |  ESC → shutdown")
    print("─"*44)

    silence_count = 0
    empty_count   = 0

    if first_input:
        print("⚙️  ...", end="", flush=True)
        terminal_text, spoken_text = process(first_input)
        print("\r\033[K", end="", flush=True)
        respond(terminal_text, spoken_text)
        time.sleep(0.15)

    while True:
        if is_shutdown_requested():
            break

        user_input = listen()

        if user_input == "__SHUTDOWN__":
            break

        if user_input == "__SILENCE__":
            silence_count += 1
            if silence_count >= SILENCE_LIMIT:
                print("\n👁️  Back to standby...")
                break
            continue

        if not user_input or len(user_input.strip()) < 2:
            empty_count += 1
            if empty_count >= EMPTY_LIMIT:
                print("\n👁️  Back to standby...")
                break
            continue

        silence_count = 0
        empty_count   = 0

        if any(p in user_input.lower() for p in GOODBYE_PHRASES):
            speak("Standing by, boss.")
            break

        print("⚙️  ...", end="", flush=True)
        terminal_text, spoken_text = process(user_input)
        print("\r\033[K", end="", flush=True)
        respond(terminal_text, spoken_text)
        time.sleep(0.15)

    set_expecting_confirmation(False)
    set_busy(False)


# ══════════════════════════════════════════
#   WAKEWORD CALLBACK
# ══════════════════════════════════════════

def wakeword_conversation():
    conversation_loop(first_input=None, typed_wake=False)


# ══════════════════════════════════════════
#   CLEANUP
# ══════════════════════════════════════════

def _cleanup_browser_on_exit():
    try:
        from features.web_control import is_browser_open, close_browser
        if is_browser_open():
            close_browser()
    except Exception:
        pass

atexit.register(_cleanup_browser_on_exit)


# ══════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════

def main():
    print("\n" + "═"*44)
    print("   J.A.R.V.I.S — Online")
    print("═"*44)
    print("  Say 'Hey Jarvis' → voice mode")
    print("  Ctrl+C           → shutdown")
    print("  (type commands in the Electron console, not here — this")
    print("   terminal is voice-only now, so there's one conversation,")
    print("   not two out-of-sync ones)")
    print("─"*44 + "\n")

    print("🔌 Waiting for backend (server.py) at " + SERVER_URL + " ...")
    if not wait_for_server():
        print("⚠️  Backend not reachable — start server.py first (see run.py). Continuing anyway;")
        print("    commands will fail until it's up.")
    else:
        print("✅ Backend is up.")

    try:
        init_microphone()
        # Deliberately NOT starting the keyboard listener / typed-standby
        # watcher here anymore — this process is voice-only. Typing into
        # THIS terminal used to run its own independent conversation
        # (with its own local echo/history) alongside the Electron
        # frontend's command line, hitting the same backend but showing
        # two different, out-of-sync views of "the conversation." The
        # frontend already has a full command box wired to /command, and
        # every voice turn is bridged into it too (core/voice_bridge.py)
        # — so the frontend is now the single place to see or type a
        # conversation; this terminal is just the mic.
        # Pushed as a 'reply' (not just spoken locally) so this greeting
        # shows up in the Electron console's log too — previously the
        # only place "Jarvis online" appeared was this terminal, which
        # isn't where the user is meant to be watching.
        push_voice_event({"type": "reply", "text": "Jarvis online. Ready when you are, KK."})
        speak("Jarvis online. Ready when you are, KK.")
        start_wakeword_listener(wakeword_conversation)
        threading.Thread(target=_heartbeat_worker, daemon=True).start()
        print("\n👁️  Standby — say 'Hey Jarvis'\n")

        while not is_shutdown_requested():
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\n⚡ Ctrl+C — shutting down Jarvis...")

    finally:
        try:
            speak("Shutting down. Later boss.")
        except Exception:
            pass
        try:
            from features.web_control import is_browser_open, close_browser
            if is_browser_open():
                print("🌐 Closing browser...")
                close_browser()
        except Exception as e:
            print(f"[shutdown] Browser cleanup warning: {e}")

    print("\n👋 Jarvis offline.")


if __name__ == "__main__":
    main()

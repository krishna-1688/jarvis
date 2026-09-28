"""
jarvis.py — the voice process (wake word, speech in, speech out).

All command handling lives in server.py; this process only listens and
talks. It is built to sit in standby all day:
  - one always-open mic stream (core/mic.py), recovered automatically;
  - the wake model only runs while real speech is heard (core/wakeword.py);
  - speech/transcription libraries load on first use.

Run it through run.py (the supervisor), which restarts it if it ever dies.
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import re
import threading
import time

import requests

from core.voice_bridge import push_voice_event
from core import voice
from core.voice import (
    speak, record_utterance, transcribe, Offline, is_shutdown_requested, request_shutdown,
    set_expecting_confirmation,
)

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 ::1 first,
# and uvicorn only listens on IPv4 — every request paid a ~2s refused-
# connection retry before falling back (measured: 2.05s vs 0.03s).
SERVER_URL = "http://127.0.0.1:8000"
SERVER_TIMEOUT = 150
HEARTBEAT_INTERVAL_S = 8
WAKE_COOLDOWN_S = 2.0
EMPTY_LIMIT = 4
DASHBOARD_AFTER_TURNS = 2

_session = requests.Session()

GOODBYE_PHRASES = {
    "goodbye", "goodbye jarvis", "go offline", "that's all", "thats all", "that is all", "stop jarvis",
    "bye jarvis", "sleep jarvis", "go to standby", "standby mode", "back to standby", "go to sleep",
}
MIC_CHECK_PHRASES = {"can you hear me", "are you there", "mic check", "do you hear me"}
# Politeness around a command that doesn't change what it is:
# "okay, that's all for now, thanks" is still just "that's all".
_LEAD_FILLER = ("ok ", "okay ", "alright ", "all right ", "thanks ", "thank you ", "hey ", "jarvis ")
_TAIL_FILLER = (" for now", " jarvis", " boss", " thanks", " thank you", " please")


def _bare_command(text: str) -> str:
    """The utterance with punctuation and polite filler stripped, so a
    control phrase is recognized only when it's the whole utterance —
    "that's all my pending assignments" (a mis-heard "what's all...") used
    to end the conversation because "that's all" appeared inside it."""
    t = " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())
    changed = True
    while changed:
        changed = False
        for head in _LEAD_FILLER:
            if t.startswith(head):
                t, changed = t[len(head):], True
        for tail in _TAIL_FILLER:
            if t.endswith(tail):
                t, changed = t[:-len(tail)], True
    return t.strip()


def call_server(text: str) -> dict:
    """Never raises — any failure becomes a spoken error instead of
    killing the voice loop."""
    try:
        r = _session.post(f"{SERVER_URL}/command", json={"text": text, "source": "voice"}, timeout=SERVER_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        msg = "I can't reach my backend right now, boss. Give me a moment."
        return {"ok": False, "display": msg, "spoken": msg, "data": {}, "expecting_confirmation": False}


def wait_for_server(timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout and not is_shutdown_requested():
        try:
            if _session.get(f"{SERVER_URL}/health", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def _heartbeat_worker():
    while not is_shutdown_requested():
        push_voice_event({"type": "voice_heartbeat"})
        time.sleep(HEARTBEAT_INTERVAL_S)


def _maybe_open_dashboard(turns: int):
    """After a couple of exchanges, bring up the dashboard — the backend
    decides (it knows whether the lid is open and the screen is on)."""
    if turns != DASHBOARD_AFTER_TURNS:
        return
    try:
        _session.post(f"{SERVER_URL}/dashboard/auto_open", timeout=3)
    except requests.RequestException:
        pass


def conversation(mic):
    """One wake-up: keep answering until silence, "goodbye", or too many
    unusable utterances in a row."""
    turns, empty, offline_warned = 0, 0, False
    while not is_shutdown_requested():
        push_voice_event({"type": "listening_start"})
        audio = record_utterance(mic)
        if audio is None:
            push_voice_event({"type": "listening_end"})
            print("👁️  Back to standby")
            return
        if len(audio) == 0:
            push_voice_event({"type": "listening_end"})
            empty += 1
            if empty >= EMPTY_LIMIT:
                return
            continue

        push_voice_event({"type": "transcribing_start"})
        try:
            text = transcribe(audio)
        except Offline:
            text = None
            if not offline_warned:
                speak("I'm offline right now, boss — I can't understand speech without the internet.")
                mic.drain()
                offline_warned = True
        except Exception as e:
            text = None
            print(f"[voice] transcription error: {e}")
        finally:
            push_voice_event({"type": "listening_end"})

        if not text:
            empty += 1
            if empty >= EMPTY_LIMIT:
                return
            continue
        empty = 0
        print(f"🗣️  You: {text}")
        push_voice_event({"type": "transcript", "text": text})

        bare = _bare_command(text)
        if bare in GOODBYE_PHRASES:
            speak("Standing by, boss.")
            return
        if bare in MIC_CHECK_PHRASES:
            speak("Loud and clear, boss.")
            mic.drain()
            continue

        voice._is_processing.set()
        push_voice_event({"type": "processing_start"})
        try:
            response = call_server(text)
        finally:
            voice._is_processing.clear()
            push_voice_event({"type": "processing_end"})
        set_expecting_confirmation(bool(response.get("expecting_confirmation")))
        push_voice_event({"type": "reply", "text": response.get("display", "")})
        speak(response.get("spoken") or response.get("display", ""))
        mic.drain()
        turns += 1
        _maybe_open_dashboard(turns)


def _single_instance() -> bool:
    """Two voice processes would fight over the mic (this happened: after
    restarts, orphaned copies kept running with live mic access)."""
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\JarvisVoiceProcess")
    globals()["_instance_mutex"] = handle  # keep the handle alive for the process lifetime
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if not _single_instance():
        print("Another Jarvis voice process is already running — exiting.")
        return 3

    print("🔌 Waiting for backend at " + SERVER_URL + " ...")
    print("✅ Backend is up." if wait_for_server() else "⚠️  Backend not reachable yet — will keep trying per request.")

    from core.mic import open_mic
    from core.wakeword import WakeDetector, THRESHOLD
    # Cache the fixed lines now, so even the first wake answers instantly.
    voice.prewarm("Yes boss?", "Standing by, boss.", "Loud and clear, boss.",
                  "I'm offline right now, boss — I can't understand speech without the internet.")
    mic = open_mic(should_stop=is_shutdown_requested)
    detector = WakeDetector()
    threading.Thread(target=_heartbeat_worker, daemon=True).start()
    print("👁️  Standby — say 'Hey Jarvis'")

    last_wake = 0.0
    try:
        while not is_shutdown_requested():
            score = detector.process(mic.read())
            if score < THRESHOLD or time.time() - last_wake < WAKE_COOLDOWN_S:
                continue
            print(f"\n✅ Wake word (score {score:.2f})")
            push_voice_event({"type": "wake"})
            speak("Yes boss?")
            mic.drain()
            conversation(mic)
            set_expecting_confirmation(False)
            detector.reset()
            mic.drain()
            last_wake = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        request_shutdown()
        mic.close()
    print("👋 Jarvis voice offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

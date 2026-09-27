"""
core/voice_bridge.py — bridges real-time voice events from the jarvis.py
process into server.py's WebSocket broadcaster.

jarvis.py (wake word + STT + TTS, owns the microphone) and server.py
(the FastAPI backend the Electron frontend talks to) are two separate
processes. jarvis.py already talks to server.py over plain HTTP for
/command — this reuses that same channel: POST /internal/voice_event,
which server.py just re-broadcasts over its own WS to any connected
frontend clients.

Event contract (all consumed by the frontend's VU meter / teleprinter):
  {"type": "wake"}
  {"type": "listening_start"} / {"type": "listening_end"}
  {"type": "processing_start"} / {"type": "processing_end"}
  {"type": "speaking_start"} / {"type": "speaking_end"}
  {"type": "transcript", "text": str}

Fire-and-forget by design: voice timing must never stall waiting on a
slow or dead backend. A single persistent worker thread drains a small
bounded queue; if server.py isn't reachable, events are just dropped
rather than blocking the voice loop or piling up threads.
"""

import queue
import threading

import requests

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 ::1 first,
# and uvicorn only listens on IPv4 — every request paid a ~2s refused-
# connection retry before falling back (measured: 2.05s vs 0.03s).
SERVER_URL = "http://127.0.0.1:8000"
_TIMEOUT   = 1.0
_session   = requests.Session()

_queue = queue.Queue(maxsize=16)


def _worker():
    while True:
        event = _queue.get()
        try:
            _session.post(f"{SERVER_URL}/internal/voice_event", json=event, timeout=_TIMEOUT)
        except Exception:
            pass


threading.Thread(target=_worker, daemon=True).start()


def push_voice_event(event: dict):
    """Non-blocking. Drops the event instead of blocking if the queue is
    backed up (e.g. server.py isn't running) — never lets voice timing
    depend on this."""
    try:
        _queue.put_nowait(event)
    except queue.Full:
        pass

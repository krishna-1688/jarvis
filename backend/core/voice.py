"""
voice.py — Fixed voice I/O for Jarvis.

Key fixes:
- listen() checks typed queue BEFORE and DURING voice listen
- Silence count NOT incremented when typed input received
- Mic stays open across conversation (calibrated once)
- ESC shuts down cleanly
- No character doubling in keyboard worker
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import io
import queue
import threading
import time

import pygame
import speech_recognition as sr
from config import GROQ_API_KEY
from core.voice_bridge import push_voice_event

# ── pygame init ───────────────────────────
pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
pygame.mixer.init()

# ── Voice settings ────────────────────────
JARVIS_VOICE  = "en-GB-RyanNeural"
JARVIS_RATE   = "+15%"
JARVIS_VOLUME = "+0%"

# ── Global state ──────────────────────────
_text_input_queue = queue.Queue()
_stop_speaking    = threading.Event()
_shutdown_event   = threading.Event()
_is_speaking      = threading.Event()
_is_processing    = threading.Event()
_is_listening     = threading.Event()

# Set the instant the user presses any printable key while in standby
# or mid-conversation. listen() checks this every poll cycle and
# immediately abandons the microphone so typing never races against
# voice transcription.
_is_typing        = threading.Event()

# Signals an in-progress mic listen to stop NOW because typing started.
# Cleared at the start of each new listen() call.
_abort_mic_listen = threading.Event()

# ── Recognizer ────────────────────────────
_recognizer = sr.Recognizer()
_recognizer.energy_threshold         = 300
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold          = 0.7
_recognizer.non_speaking_duration    = 0.5

_mic          = None
_ambient_done = False

# ── Hallucination filter ──────────────────
_HALLUCINATIONS = {
    "thank you", "thank you.", "thanks", "thanks.", "you",
    ".", "..", "...", "okay", "ok", "bye", "goodbye",
    "♪", "♪♪", "[music]", "[applause]", "i", "hmm", "um", "uh",
    "hey", "hi", "hello", "so", "the", "a", "an", "yeah", "yep",
    "nope", "no", "yes", "sure", "okay.", "ok.", "right", "alright",
}

# Words that are valid short replies (yes/no/confirmations) but also
# happen to be in _HALLUCINATIONS because Whisper sometimes invents
# them from silence. When Jarvis is waiting on a yes/no confirmation
# (e.g. WhatsApp "did you mean X?"), these should NOT be filtered out.
_CONFIRMATION_WORDS = {
    "yes", "yeah", "yep", "yup", "no", "nope", "sure", "correct",
    "right", "okay", "ok", "confirm", "cancel", "stop"
}

# Set by jarvis.py whenever a yes/no confirmation is pending.
# When True, listen() skips hallucination filtering for short
# confirmation words so "yes"/"no" actually register.
_expecting_confirmation = threading.Event()

def set_expecting_confirmation(state: bool):
    if state:
        _expecting_confirmation.set()
    else:
        _expecting_confirmation.clear()

def _is_hallucination(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".,!?")

    # Bypass filtering for short confirmation words while Jarvis
    # is actively waiting on a yes/no reply.
    if _expecting_confirmation.is_set() and cleaned in _CONFIRMATION_WORDS:
        return False

    if not cleaned or len(cleaned) < 4:
        return True
    if cleaned in _HALLUCINATIONS:
        return True
    if len(cleaned.split()) < 2 and len(cleaned) < 6:
        return True
    return False

def _status(msg: str):
    print(f"\r\033[K{msg}", end="", flush=True)

def _status_clear():
    print(f"\r\033[K", end="", flush=True)


# ══════════════════════════════════════════
#   MIC — calibrate ONCE on startup
# ══════════════════════════════════════════

def init_microphone():
    global _mic, _ambient_done
    print("🎤 Calibrating microphone... ", end="", flush=True)
    try:
        _mic = sr.Microphone(sample_rate=16000)
        with _mic as source:
            _recognizer.adjust_for_ambient_noise(source, duration=1.0)
        _ambient_done = True
        print(f"done (threshold={_recognizer.energy_threshold:.0f})")
    except Exception as e:
        print(f"mic error: {e}")


# ══════════════════════════════════════════
#   TTS — streams into memory, no temp file
#
#   ARCHITECTURE NOTE — why this changed:
#   Playwright's sync API (used by web_control.py) spins up its OWN
#   asyncio event loop internally to drive the browser protocol, and
#   that loop stays alive in a background thread for the rest of the
#   program's life once any browser action runs. After that happens,
#   calling asyncio.run(...) here on the main thread fails outright —
#   "asyncio.run() cannot be called from a running event loop" — even
#   though THIS thread never started one itself. asyncio.run() checks
#   process-wide state, not just the calling thread.
#
#   Fix: TTS now runs on its OWN dedicated background thread with its
#   OWN persistent event loop, created exactly once at import time.
#   speak() just schedules work onto that loop via
#   run_coroutine_threadsafe() and blocks until it's done. This loop
#   never touches Playwright's loop and vice versa — fully isolated.
# ══════════════════════════════════════════

async def _tts_stream(text: str):
    import edge_tts
    _stop_speaking.clear()
    _is_speaking.set()
    push_voice_event({"type": "speaking_start"})
    audio_buf = io.BytesIO()
    try:
        communicate = edge_tts.Communicate(
            text, voice=JARVIS_VOICE,
            rate=JARVIS_RATE, volume=JARVIS_VOLUME
        )
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buf.write(chunk["data"])
            if _stop_speaking.is_set():
                break

        if _stop_speaking.is_set():
            return

        audio_buf.seek(0)
        pygame.mixer.music.load(audio_buf, "mp3")
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            if _stop_speaking.is_set():
                pygame.mixer.music.stop()
                break
            await asyncio.sleep(0.03)
    except Exception as e:
        print(f"\nTTS error: {e}")
    finally:
        _is_speaking.clear()
        push_voice_event({"type": "speaking_end"})
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass


# ── Dedicated persistent event loop for TTS, isolated from Playwright ──
_tts_loop        = None
_tts_loop_thread = None
_tts_loop_ready  = threading.Event()

def _tts_loop_worker():
    """Runs forever on its own thread, hosting only the TTS event loop."""
    global _tts_loop
    _tts_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_tts_loop)
    _tts_loop_ready.set()
    _tts_loop.run_forever()

def _ensure_tts_loop():
    global _tts_loop_thread
    if _tts_loop_thread is None or not _tts_loop_thread.is_alive():
        _tts_loop_thread = threading.Thread(target=_tts_loop_worker, daemon=True)
        _tts_loop_thread.start()
        _tts_loop_ready.wait(timeout=5)

def speak(text: str):
    print(f"\n🤖 Jarvis: {text}")
    _status_clear()

    _ensure_tts_loop()

    try:
        # Schedule the coroutine on the dedicated TTS loop (different
        # thread, different loop than Playwright's) and block this
        # calling thread until it finishes — same net effect as
        # asyncio.run() used to have, minus the process-wide collision.
        future = asyncio.run_coroutine_threadsafe(_tts_stream(text), _tts_loop)
        future.result(timeout=60)
    except Exception as e:
        print(f"[voice] TTS error (speech skipped): {e}")

def stop_speaking():
    _stop_speaking.set()
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass


# ══════════════════════════════════════════
#   KEYBOARD LISTENER
#   FIX: use sys.stdin.readline() instead of
#   msvcrt to avoid character doubling.
#   ESC still handled via a separate thread.
# ══════════════════════════════════════════

_wake_from_typing_callback = None

def _esc_watcher():
    """
    Single combined keyboard watcher using msvcrt (Windows).
    Handles: ESC shutdown, AND detects the first keystroke of typing
    so listen() can immediately abandon the microphone instead of
    racing it against your typed input.

    This replaces the old readline()-based worker. readline() is
    blocking and can't signal "user started typing" until Enter is
    pressed — by then the mic thread may have already grabbed ambient
    noise and started transcribing, eating your half-typed message.
    msvcrt gives us per-keystroke visibility so we can flag typing
    the instant the first character is pressed.
    """
    try:
        import msvcrt
    except ImportError:
        # Non-Windows fallback — use the simpler readline-based worker
        _text_input_worker_fallback()
        return

    buffer = []

    while not _shutdown_event.is_set():
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue

        ch = msvcrt.getwch()

        # ESC — shutdown
        if ch == '\x1b':
            print("\n\n⚡ ESC pressed — shutting down Jarvis...")
            _shutdown_event.set()
            stop_speaking()
            break

        # Enter — submit the typed line
        if ch in ('\r', '\n'):
            if buffer:
                text = "".join(buffer).strip()
                buffer.clear()
                _is_typing.clear()
                if text:
                    print()  # move past the echoed line
                    stop_speaking()
                    print(f"⌨️  You: {text}")
                    _text_input_queue.put(text)
            else:
                print()
            continue

        # Backspace
        if ch == '\x08':
            if buffer:
                buffer.pop()
                sys.stdout.write('\b \b')
                sys.stdout.flush()
            if not buffer:
                _is_typing.clear()
            continue

        # Arrow keys / function keys — two-byte sequences, consume and ignore
        if ch in ('\x00', '\xe0'):
            msvcrt.getwch()
            continue

        # Regular printable character — this is the critical bit:
        # the MOMENT the first character is typed, flag _is_typing
        # so any in-progress listen() call aborts the mic immediately.
        buffer.append(ch)
        sys.stdout.write(ch)
        sys.stdout.flush()

        if not _is_typing.is_set():
            _is_typing.set()
            # Stop speaking and abort any active mic listen right away
            stop_speaking()
            _abort_mic_listen.set()


def _text_input_worker_fallback():
    """Non-Windows fallback — line-based, no per-keystroke typing detection."""
    while not _shutdown_event.is_set():
        try:
            line = sys.stdin.readline()
            if not line:
                break
            text = line.strip()
            if text:
                stop_speaking()
                print(f"⌨️  You: {text}")
                _text_input_queue.put(text)
        except (EOFError, KeyboardInterrupt):
            break
        except Exception:
            time.sleep(0.1)

def start_keyboard_listener(wake_callback=None):
    global _wake_from_typing_callback
    _wake_from_typing_callback = wake_callback
    # Single combined watcher: ESC shutdown + per-keystroke typing
    # detection + line submission. Replaces the old two-thread
    # (readline + separate ESC poll) setup that raced against the mic.
    threading.Thread(target=_esc_watcher, daemon=True).start()

def is_shutdown_requested() -> bool:
    return _shutdown_event.is_set()


# ══════════════════════════════════════════
#   LISTEN — voice + typed, unified
#
#   FIX: typed input check happens BEFORE
#   opening mic AND during voice listen loop.
#   Typed input returns immediately — never
#   increments silence count in caller.
# ══════════════════════════════════════════

LISTEN_TIMEOUT    = 8
PHRASE_TIME_LIMIT = 15

def _wait_for_typed_line(timeout: float = 120.0) -> str:
    """
    Called once we know the user has started typing (or is about to
    submit something already in the queue). Waits ONLY for the queue —
    never touches the microphone.

    IMPORTANT: this function does NOT touch the status line (no
    _status() calls). The keyboard watcher thread (_esc_watcher) is
    actively writing your typed characters to stdout in real time on
    a separate thread. If this function also writes to stdout via
    _status() (which does \\r\\033[K — cursor-to-start + clear-line),
    the two writes can interleave and corrupt the very first character
    you type, making it appear doubled (e.g. "no" showing as "nno").
    Once typing has started, stdout belongs to the keyboard watcher
    until Enter is pressed.
    """
    start = time.time()
    while time.time() - start < timeout:
        if _shutdown_event.is_set():
            return "__SHUTDOWN__"
        try:
            typed = _text_input_queue.get(timeout=0.1)
            return typed
        except queue.Empty:
            continue
    return "__SILENCE__"

def listen() -> str:
    """
    Returns:
      - typed text (if user typed + Enter)
      - transcribed voice text
      - '__SILENCE__' if no input within timeout
      - '__SHUTDOWN__' if ESC pressed
      - '' if audio noise/hallucination

    Typing safety: the moment ANY key is pressed, this function stops
    waiting on the microphone and instead waits for the typed line to
    be submitted (Enter). The mic thread is abandoned (daemon thread,
    dies on its own / gets ignored) rather than raced against.
    """
    if _shutdown_event.is_set():
        return "__SHUTDOWN__"

    # Reset the abort flag for this fresh listen cycle
    _abort_mic_listen.clear()

    # ── CHECK TYPED INPUT FIRST (instant) ─
    try:
        typed = _text_input_queue.get_nowait()
        return typed
    except queue.Empty:
        pass

    # If user is already mid-typing when listen() is called, skip the
    # mic entirely and just wait for them to finish.
    if _is_typing.is_set():
        return _wait_for_typed_line()

    _is_listening.set()
    push_voice_event({"type": "listening_start"})
    _status("👂 Listening... (or type + Enter)")

    try:
        if _mic is None:
            init_microphone()

        with _mic as source:
            _audio_result = [None]
            _audio_error  = [None]

            def _do_listen():
                try:
                    _audio_result[0] = _recognizer.listen(
                        source,
                        timeout=LISTEN_TIMEOUT,
                        phrase_time_limit=PHRASE_TIME_LIMIT
                    )
                except sr.WaitTimeoutError:
                    _audio_error[0] = "timeout"
                except Exception as e:
                    _audio_error[0] = str(e)

            listen_thread = threading.Thread(target=_do_listen, daemon=True)
            listen_thread.start()

            # ── POLL every 50ms for typing, typed input, or shutdown ──
            while listen_thread.is_alive():
                if _shutdown_event.is_set():
                    _is_listening.clear()
                    _status_clear()
                    return "__SHUTDOWN__"

                # Typing started — abandon the mic listen immediately.
                # We do NOT wait for listen_thread to finish; it's a
                # daemon thread and whatever audio it eventually
                # captures (if any) is simply discarded below.
                # No _status_clear() here — it would race against the
                # keyboard watcher thread's live character echo and
                # corrupt your first typed character.
                if _is_typing.is_set() or _abort_mic_listen.is_set():
                    _is_listening.clear()
                    return _wait_for_typed_line()

                try:
                    typed = _text_input_queue.get_nowait()
                    _is_listening.clear()
                    _status_clear()
                    return typed
                except queue.Empty:
                    pass

                time.sleep(0.05)

            # Mic thread finished naturally (timeout or got audio).
            # Double check typing didn't start in the gap right as
            # the thread was finishing. No _status_clear() here for
            # the same reason as above — avoid racing the live echo.
            if _is_typing.is_set():
                _is_listening.clear()
                return _wait_for_typed_line()

            if _audio_error[0] == "timeout":
                try:
                    typed = _text_input_queue.get_nowait()
                    _is_listening.clear()
                    _status_clear()
                    return typed
                except queue.Empty:
                    _is_listening.clear()
                    _status_clear()
                    return "__SILENCE__"

            if _audio_error[0]:
                _is_listening.clear()
                _status_clear()
                return ""

            audio = _audio_result[0]

        if audio is None:
            _is_listening.clear()
            return ""

        # Skip very short audio (noise)
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        if len(raw) < 6000:
            _is_listening.clear()
            _status_clear()
            return ""

        _status("⚙️  Transcribing...")

        import groq
        client = groq.Groq(api_key=GROQ_API_KEY)
        wav_data = audio.get_wav_data()

        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=("audio.wav", wav_data, "audio/wav"),
            response_format="verbose_json",
            language="en",
        )

        text     = (transcription.text or "").strip()
        language = getattr(transcription, "language", "en")

        if language and language.lower() not in ("en", "english"):
            _is_listening.clear()
            _status_clear()
            return ""

        if _is_hallucination(text):
            _is_listening.clear()
            _status_clear()
            return ""

        _status_clear()
        print(f"🗣️  You: {text}")
        push_voice_event({"type": "transcript", "text": text})
        _is_listening.clear()
        return text

    except Exception as e:
        print(f"\nListen error: {e}")
        _is_listening.clear()
        _status_clear()
        return ""
    finally:
        # Guaranteed to fire exactly once no matter which of the many
        # return points above was hit (timeout/typing-abort/success/
        # error/etc.) — see the surrounding try's many early returns.
        push_voice_event({"type": "listening_end"})

def start_text_input_listener():
    """Legacy compat — no-op."""
    pass
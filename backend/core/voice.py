"""
voice.py — recording, transcription and speech for the voice process.

Rebuilt for all-day standby:
  - Records from the shared always-open mic (core/mic.py) using webrtcvad
    to find where speech starts and ends, instead of speech_recognition
    opening its own stream each turn.
  - Speech plays through edge-tts + miniaudio (~1 MB) instead of pygame
    (~25 MB), both loaded on first use. If edge-tts can't be reached
    (offline), Windows' built-in SAPI voice speaks instead, so Jarvis is
    never silent.
  - Whisper runs on Groq through core.llm (imported on first use).
"""

import asyncio
import io
import os
import threading
import time
import wave
from collections import deque

import numpy as np

from core.voice_bridge import push_voice_event

RATE = 16000
LISTEN_TIMEOUT = 7        # seconds of silence before the conversation ends
MAX_UTTERANCE_S = 15
END_SILENCE_S = 0.8
MIN_UTTERANCE_S = 0.35

JARVIS_VOICE = "en-GB-RyanNeural"
JARVIS_RATE = "+15%"
JARVIS_VOLUME = "+0%"

_shutdown_event = threading.Event()
_stop_speaking = threading.Event()
_expecting_confirmation = threading.Event()
_is_processing = threading.Event()


def is_shutdown_requested() -> bool:
    return _shutdown_event.is_set()


def request_shutdown():
    _shutdown_event.set()
    _stop_speaking.set()


def set_expecting_confirmation(state: bool):
    (_expecting_confirmation.set if state else _expecting_confirmation.clear)()


# ══════════════════════════════════════════
#   RECORDING
# ══════════════════════════════════════════

_vad = None


def _voiced(chunk: np.ndarray) -> bool:
    global _vad
    if _vad is None:
        import webrtcvad
        _vad = webrtcvad.Vad(2)
    raw = chunk.tobytes()
    hits = 0
    for i in range(0, len(chunk), 320):
        try:
            if _vad.is_speech(raw[i * 2:(i + 320) * 2], RATE):
                hits += 1
        except Exception:
            pass
    return hits >= 2


def record_utterance(mic, timeout_s: float = LISTEN_TIMEOUT):
    """Returns int16 audio of one utterance, None if nobody spoke within
    timeout_s, or an empty array if what was heard was too short."""
    chunk_s = 1280 / RATE
    pre = deque(maxlen=4)
    frames, started, silence, waited = [], False, 0.0, 0.0
    while not _shutdown_event.is_set():
        chunk = mic.read()
        voiced = _voiced(chunk)
        if not started:
            pre.append(chunk)
            waited += chunk_s
            if voiced:
                started, frames = True, list(pre)
            elif waited >= timeout_s:
                return None
            continue
        frames.append(chunk)
        silence = 0.0 if voiced else silence + chunk_s
        if silence >= END_SILENCE_S or len(frames) * chunk_s >= MAX_UTTERANCE_S:
            break
    if not frames:
        return None
    audio = np.concatenate(frames)
    return audio if len(audio) >= RATE * MIN_UTTERANCE_S else np.zeros(0, dtype=np.int16)


# ══════════════════════════════════════════
#   TRANSCRIPTION
# ══════════════════════════════════════════

_HALLUCINATIONS = {
    "thank you", "thanks", "you", ".", "..", "...", "okay", "ok", "bye", "goodbye",
    "♪", "♪♪", "[music]", "[applause]", "i", "hmm", "um", "uh", "hey", "hi", "hello",
    "so", "the", "a", "an", "yeah", "yep", "nope", "no", "yes", "sure", "right", "alright",
}
_CONFIRMATION_WORDS = {"yes", "yeah", "yep", "yup", "no", "nope", "sure", "correct", "right",
                       "okay", "ok", "confirm", "cancel", "stop"}

_WHISPER_VOCAB_PROMPT = (
    "Jarvis, KK, VTOP, VIT Chennai, LMS, Moodle, CGPA, GPA, CAT1, CAT2, FAT, DBMS, TOC, "
    "DAA, CN, OS, DMGT, DSA, pomodoro, Spotify, WhatsApp, attendance, timetable, bunk."
)


class Offline(Exception):
    pass


def _is_hallucination(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".,!?")
    if _expecting_confirmation.is_set() and cleaned in _CONFIRMATION_WORDS:
        return False
    if len(cleaned) < 4 or cleaned in _HALLUCINATIONS:
        return True
    return len(cleaned.split()) < 2 and len(cleaned) < 6


def _mostly_non_speech(transcription) -> bool:
    probs = []
    for seg in getattr(transcription, "segments", None) or []:
        p = seg.get("no_speech_prob") if isinstance(seg, dict) else getattr(seg, "no_speech_prob", None)
        if p is not None:
            probs.append(p)
    return bool(probs) and min(probs) > 0.6


def transcribe(audio: np.ndarray) -> str:
    """Text of the utterance, "" if it was noise. Raises Offline when the
    transcription service can't be reached."""
    import groq
    from core.llm import transcribe as _transcribe
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    try:
        result = _transcribe(buf.getvalue(), prompt=_WHISPER_VOCAB_PROMPT)
    except (groq.APIConnectionError, groq.APITimeoutError) as e:
        raise Offline(str(e)) from e
    text = (result.text or "").strip()
    language = (getattr(result, "language", "en") or "en").lower()
    if language not in ("en", "english") or _is_hallucination(text) or _mostly_non_speech(result):
        return ""
    return text


# ══════════════════════════════════════════
#   SPEECH
# ══════════════════════════════════════════

_TTS_DRYRUN = os.environ.get("JARVIS_TTS_DRYRUN") == "1"   # soak tests: synthesize, don't play
_SPEAKER_NAME = os.environ.get("JARVIS_SPEAKER_DEVICE")


async def _edge_tts_mp3(text: str) -> bytes:
    import edge_tts
    buf = bytearray()
    communicate = edge_tts.Communicate(text, voice=JARVIS_VOICE, rate=JARVIS_RATE, volume=JARVIS_VOLUME)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
        if _stop_speaking.is_set():
            break
    return bytes(buf)


def _playback_device_id():
    if not _SPEAKER_NAME:
        return None
    import miniaudio
    for dev in miniaudio.Devices().get_playbacks():
        if _SPEAKER_NAME.lower() in dev["name"].lower():
            return dev["id"]
    return None


def _play_mp3(mp3: bytes):
    import miniaudio
    decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
    samples = decoded.samples
    duration = len(samples) / 24000

    def stream():
        pos = 0
        required = yield b""
        while pos < len(samples) and not _stop_speaking.is_set():
            out = samples[pos:pos + required]
            pos += required
            required = yield out

    device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                      sample_rate=24000, device_id=_playback_device_id())
    gen = stream()
    next(gen)
    device.start(gen)
    end = time.monotonic() + duration + 0.15
    while time.monotonic() < end and not _stop_speaking.is_set():
        time.sleep(0.05)
    device.close()


def _speak_offline(text: str):
    """Windows' built-in voice — works with no internet."""
    import comtypes.client
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.Speak(text)


# Fixed phrases ("Yes boss?") are synthesized once and replayed from disk.
# Measured: edge-tts takes 1.4-2.8 s to return "Yes boss?", so every wake
# used to answer that late — and on a flaky connection it waited out the
# network timeout, then fell back to the robotic Windows voice. Cached,
# the acknowledgement starts instantly and still works offline.
_TTS_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tts_cache")
_CACHE_MAX_CHARS = 90   # short, repeatable lines only — never whole answers
_mem_cache: dict = {}


def _cache_path(text: str) -> str:
    import hashlib
    key = hashlib.sha1(f"{JARVIS_VOICE}|{JARVIS_RATE}|{JARVIS_VOLUME}|{text}".encode("utf-8")).hexdigest()[:20]
    return os.path.join(_TTS_CACHE_DIR, f"{key}.mp3")


def _cached_mp3(text: str):
    if text in _mem_cache:
        return _mem_cache[text]
    try:
        with open(_cache_path(text), "rb") as f:
            _mem_cache[text] = f.read()
            return _mem_cache[text]
    except OSError:
        return None


def _store_mp3(text: str, mp3: bytes):
    _mem_cache[text] = mp3
    try:
        os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
        with open(_cache_path(text), "wb") as f:
            f.write(mp3)
    except OSError:
        pass


def prewarm(*texts: str):
    """Synthesize fixed phrases into the cache in the background, so even
    the first wake after startup answers instantly."""
    def run():
        for t in texts:
            if _cached_mp3(t) is None:
                try:
                    mp3 = asyncio.run(_edge_tts_mp3(t))
                    if mp3:
                        _store_mp3(t, mp3)
                except Exception:
                    pass  # offline now; it's cached the first time it's spoken online
    threading.Thread(target=run, daemon=True).start()


def speak(text: str):
    if not text:
        return
    try:
        print(f"\n🤖 Jarvis: {text}")
    except Exception:
        pass
    _stop_speaking.clear()
    push_voice_event({"type": "speaking_start"})
    try:
        try:
            cacheable = len(text) <= _CACHE_MAX_CHARS
            mp3 = _cached_mp3(text) if cacheable else None
            if mp3 is None:
                mp3 = asyncio.run(_edge_tts_mp3(text))
                if not mp3:
                    raise RuntimeError("edge-tts returned no audio")
                if cacheable and not _stop_speaking.is_set():
                    _store_mp3(text, mp3)
            if not _TTS_DRYRUN:
                _play_mp3(mp3)
        except Exception as e:
            print(f"[voice] online TTS unavailable ({e}); using Windows voice")
            if not _TTS_DRYRUN:
                _speak_offline(text)
    except Exception as e:
        print(f"[voice] speech failed: {e}")
    finally:
        push_voice_event({"type": "speaking_end"})


def stop_speaking():
    _stop_speaking.set()

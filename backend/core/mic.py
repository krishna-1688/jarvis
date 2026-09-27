"""
core/mic.py — the one microphone stream the voice process keeps open.

The old voice loop opened and closed the mic around every wake-up (the
wake listener stopped its PyAudio stream, speech_recognition opened its
own, then the listener reopened). Over a long day that churn is the most
common way a mic silently dies. Here one 16 kHz mono stream stays open
for the life of the process and is shared by wake-word detection and
recording; if a read fails (headset unplugged, default device changed,
laptop resumed from sleep) it is reopened with backoff, re-probing the
device.

JARVIS_FAKE_MIC=<wav path> replays a 16 kHz mono WAV in real time
instead — used by the soak test to drive full voice conversations.
"""

import os
import time
import wave

import numpy as np

RATE = 16000
CHUNK = 1280  # 80 ms — what openWakeWord expects per prediction


class Mic:
    def __init__(self, should_stop=lambda: False):
        self._should_stop = should_stop
        self._pa = None
        self._stream = None
        self._failures = 0

    def _open(self, reprobe: bool = False):
        import pyaudio
        from core.audio_device import get_input_device_index
        self.close()
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
            input_device_index=get_input_device_index(refresh=reprobe),
            frames_per_buffer=CHUNK,
        )

    def read(self) -> np.ndarray:
        """Next 80 ms of audio. Blocks (without spinning) until the device
        works again if it has failed."""
        while not self._should_stop():
            try:
                if self._stream is None:
                    self._open(reprobe=self._failures > 0)
                data = self._stream.read(CHUNK, exception_on_overflow=False)
                self._failures = 0
                return np.frombuffer(data, dtype=np.int16)
            except Exception as e:
                self._failures += 1
                wait = min(30, 2 ** min(self._failures, 5))
                print(f"[mic] read failed ({e}); reopening in {wait}s")
                self.close()
                time.sleep(wait)
        return np.zeros(CHUNK, dtype=np.int16)

    def drain(self):
        """Drop audio buffered while Jarvis was speaking, so it doesn't
        hear (and transcribe) its own voice."""
        try:
            if self._stream is not None:
                avail = self._stream.get_read_available()
                if avail:
                    self._stream.read(avail, exception_on_overflow=False)
        except Exception:
            pass

    def close(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = None
        self._pa = None


class FakeMic:
    """Replays a WAV file in real time, looping — for automated tests."""

    def __init__(self, path: str, should_stop=lambda: False):
        with wave.open(path, "rb") as w:
            assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
            self._audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        self._pos = 0
        self._next = time.monotonic()
        self._should_stop = should_stop

    def read(self) -> np.ndarray:
        self._next += CHUNK / RATE
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self._next = time.monotonic()
        end = self._pos + CHUNK
        if end > len(self._audio):
            self._pos, end = 0, CHUNK
        chunk = self._audio[self._pos:end]
        self._pos = end
        return chunk

    def drain(self):
        pass

    def close(self):
        pass


def open_mic(should_stop=lambda: False):
    fake = os.environ.get("JARVIS_FAKE_MIC")
    return FakeMic(fake, should_stop) if fake else Mic(should_stop)

"""
wakeword.py — "Hey Jarvis" detection built for all-day standby.

Cost control, measured on this laptop:
  - openWakeWord's package __init__ imports its *training* helper, which
    pulls in scikit-learn (~100 MB) though detection never uses it. That
    import is stubbed out: the process drops from ~208 MB to ~110 MB.
  - The wake model costs ~8% of one core whenever it runs, so it only
    runs while webrtcvad (a few microseconds per frame) hears human
    speech. Fans, typing and silence never reach it. The last ~1 s of
    audio is kept and replayed when speech starts, and frames keep flowing
    for ~1.6 s after it stops, so the model always sees the whole phrase.

Reliability — benchmarked, not guessed (63 "Hey Jarvis" clips in 8 voices
incl. Indian English at 3 speeds, 28 near-miss negatives, 4 loudness
levels, plus a speaker->mic loopback on this laptop):
  - The wake model itself is strong: median score 1.00, 93-100% detected
    even at 3% of full loudness. The VAD-gated feed scores the same as a
    continuous one, so gating costs no accuracy.
  - Every real-world failure traced to the *microphone path*, not this
    file: a broken DirectSound device got auto-selected on some runs (see
    core/audio_device.py). Through a working path, loopback detection was
    6/6 at score 1.00.
  - THRESHOLD 0.3: the hardest true positives were Indian-English "Hey,
    Jarvis!" with a pause after "Hey" (0.30-0.48), which 0.5 — the model's
    trained default — would miss. The only negatives that reach 0.3 are
    "Jarvis" alone (arguably should wake it) and the sound-alike "Hey
    Travis"; ordinary phrases stay near 0.
  - VAD aggressiveness 1 (not 2) so the soft onset of "Hey" isn't dropped
    before the model hears it; the model still makes the actual decision.

JARVIS_WAKE_THRESHOLD overrides the threshold; near misses are logged so
it can be tuned per mic.
"""

import os
import sys
import time
import types
from collections import deque

import numpy as np

THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.3"))
NEAR_MISS = THRESHOLD * 0.5
PREROLL_CHUNKS = 12   # ~1 s of 80 ms chunks
HANG_CHUNKS = 20      # keep feeding ~1.6 s after the last speech frame
VAD_FRAME = 320       # 20 ms at 16 kHz; an 80 ms chunk is 4 VAD frames


def _load_model():
    if "openwakeword.custom_verifier_model" not in sys.modules:
        stub = types.ModuleType("openwakeword.custom_verifier_model")
        stub.train_custom_verifier = None
        sys.modules["openwakeword.custom_verifier_model"] = stub
    from openwakeword.model import Model
    return Model(wakeword_models=["hey_jarvis_v0.1"], inference_framework="onnx")


class WakeDetector:
    def __init__(self):
        import webrtcvad
        self._vad = webrtcvad.Vad(1)
        self._model = _load_model()
        self._preroll = deque(maxlen=PREROLL_CHUNKS)
        self._hang = 0
        self._last_near_miss = 0.0
        self.model_frames = 0  # how often the model actually ran (for stats)

    def _is_speech(self, chunk: np.ndarray) -> bool:
        raw = chunk.tobytes()
        voiced = 0
        for i in range(0, len(chunk), VAD_FRAME):
            try:
                if self._vad.is_speech(raw[i * 2:(i + VAD_FRAME) * 2], 16000):
                    voiced += 1
            except Exception:
                pass
        return voiced >= 2

    def process(self, chunk: np.ndarray) -> float:
        """Returns the wake score for this 80 ms chunk (0 when gated off)."""
        speech = self._is_speech(chunk)
        if not speech and self._hang == 0:
            self._preroll.append(chunk)
            return 0.0
        if self._hang == 0:
            for frame in self._preroll:
                self._model.predict(frame)
            self._preroll.clear()
        self._hang = HANG_CHUNKS if speech else self._hang - 1
        self.model_frames += 1
        score = max(self._model.predict(chunk).values())
        now = time.time()
        if NEAR_MISS <= score < THRESHOLD and now - self._last_near_miss > 3:
            self._last_near_miss = now
            print(f"  (wake near-miss: score {score:.2f} < threshold {THRESHOLD:.2f})")
        return score

    def reset(self):
        self._model.reset()
        self._preroll.clear()
        self._hang = 0

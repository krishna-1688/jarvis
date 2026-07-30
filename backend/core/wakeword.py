"""
wakeword.py — Wake word listener.

- No is_shutdown_requested import (removed entirely)
- set_busy()/is_busy() are the ONLY way to check busy state
  (never import _jarvis_busy directly elsewhere)
- Stream stops/starts cleanly with no audio device conflict
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model
import threading
import time

from core.voice_bridge import push_voice_event
from core.audio_device import get_input_device_index

openwakeword.utils.download_models()

oww_model = Model(
    wakeword_models=["hey_jarvis_v0.1"],
    inference_framework="onnx"
)

CHUNK     = 1280
FORMAT    = pyaudio.paInt16
CHANNELS  = 1
RATE      = 16000
THRESHOLD = 0.05
COOLDOWN  = 2.0

_jarvis_busy      = False
_listener_running = True
_stream           = None
_pa               = None
_stream_lock      = threading.Lock()
_busy_lock        = threading.Lock()


def set_busy(state: bool):
    global _jarvis_busy
    with _busy_lock:
        _jarvis_busy = state


def is_busy() -> bool:
    with _busy_lock:
        return _jarvis_busy


def stop_stream():
    global _stream, _pa
    with _stream_lock:
        try:
            if _stream:
                _stream.stop_stream()
                _stream.close()
                _stream = None
        except Exception:
            pass
        try:
            if _pa:
                _pa.terminate()
                _pa = None
        except Exception:
            pass


def start_stream():
    global _stream, _pa
    stop_stream()
    time.sleep(0.2)
    with _stream_lock:
        try:
            _pa     = pyaudio.PyAudio()
            _stream = _pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=get_input_device_index(),
                frames_per_buffer=CHUNK
            )
        except Exception as e:
            print(f"Stream start error: {e}")


def _trigger_and_wait(callback):
    """Speaks 'Yes boss?', runs callback, then conversation_loop clears busy."""
    from core.voice import speak
    speak("Yes boss?")
    callback()


def _listener_loop(callback):
    start_stream()
    last_triggered = 0

    print("👁️  Standby — say 'Hey Jarvis'")

    while _listener_running:
        try:
            if is_busy():
                time.sleep(0.05)
                continue

            with _stream_lock:
                if _stream is None:
                    time.sleep(0.1)
                    continue
                try:
                    data = _stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    time.sleep(0.05)
                    continue

            audio_data = np.frombuffer(data, dtype=np.int16)

            if np.abs(audio_data).mean() < 30:
                continue

            prediction = oww_model.predict(audio_data)

            now = time.time()
            for model_name, score in prediction.items():
                if score >= THRESHOLD and (now - last_triggered) >= COOLDOWN:
                    print(f"\n✅ Wake word! (score: {score:.2f})")
                    last_triggered = now
                    oww_model.reset()
                    push_voice_event({"type": "wake"})

                    set_busy(True)
                    stop_stream()  # release mic before speak() / conversation_loop's listen()

                    t = threading.Thread(
                        target=_trigger_and_wait,
                        args=(callback,),
                        daemon=True
                    )
                    t.start()

                    # Wait until conversation_loop clears busy (in its finally block)
                    while is_busy():
                        time.sleep(0.1)

                    start_stream()

        except Exception as e:
            if "stream" not in str(e).lower():
                print(f"Listener error: {e}")
            time.sleep(0.1)

    stop_stream()


def start_wakeword_listener(callback):
    t = threading.Thread(
        target=_listener_loop,
        args=(callback,),
        daemon=True
    )
    t.start()
    return t
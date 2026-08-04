"""
Temporary diagnostic — run this directly (python mic_diag.py) while
run.py/jarvis.py are NOT running (mic must be free). Say "Hey Jarvis"
a few times during the 12-second window, then paste the full output.

Checks the two things that could silently block wake-word detection:
1. Is your actual speech loud enough to pass the amplitude gate
   wakeword.py uses before it even runs the wake-word model
   (chunks averaging under 30 are skipped entirely)?
2. Does the wake-word model's score for "hey jarvis" ever get
   anywhere near the 0.05 trigger threshold?
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pyaudio
from core.audio_device import get_input_device_index

print("Loading wake-word model...")
import openwakeword
from openwakeword.model import Model
openwakeword.utils.download_models()
oww_model = Model(wakeword_models=["hey_jarvis_v0.1"], inference_framework="onnx")

CHUNK, RATE = 1280, 16000
idx = get_input_device_index()

pa = pyaudio.PyAudio()
info = pa.get_device_info_by_index(idx)
print(f"Using device [{idx}] {info.get('name')}")

stream = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                  input=True, input_device_index=idx, frames_per_buffer=CHUNK)

print("\nListening for 12 seconds — say 'Hey Jarvis' a few times NOW...\n")
start = time.time()
max_amp = 0.0
max_score = 0.0
tick = 0
while time.time() - start < 12:
    data = stream.read(CHUNK, exception_on_overflow=False)
    audio = np.frombuffer(data, dtype=np.int16)
    amp = float(np.abs(audio).mean())
    max_amp = max(max_amp, amp)

    if amp >= 30:
        pred = oww_model.predict(audio)
        score = max(pred.values()) if pred else 0.0
        max_score = max(max_score, score)
        if score > 0.01:
            print(f"  t={time.time()-start:4.1f}s  amp={amp:6.1f}  score={score:.4f}")
    else:
        pass

    tick += 1
    if tick % 40 == 0:
        print(f"  ...t={time.time()-start:4.1f}s  current amp={amp:.1f} (gate is 30)")

stream.stop_stream()
stream.close()
pa.terminate()

print("\n=== SUMMARY ===")
print(f"Max amplitude seen:  {max_amp:.1f}   (gate to even run wake-word model: 30)")
print(f"Max wake score seen: {max_score:.4f} (trigger threshold: 0.05)")
if max_amp < 30:
    print("-> Your mic input is too quiet to ever trigger wake-word detection.")
    print("   The selected device isn't picking up your voice loud enough.")
elif max_score < 0.05:
    print("-> Mic volume is fine, but the wake-word model never recognized")
    print("   'Hey Jarvis' in what it heard. Model/pronunciation/environment issue.")
else:
    print("-> Both amplitude and wake score look fine — wake word SHOULD trigger.")
    print("   If it still doesn't in normal use, the bug is elsewhere in the listener loop.")

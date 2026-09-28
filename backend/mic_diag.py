"""
Diagnostic — run this directly (python mic_diag.py) while run.py is NOT
running (the mic must be free). Say "Hey Jarvis" a few times during the
15-second window, then paste the full output.

Unlike a synthetic amplitude/model test, this drives the *actual*
core.wakeword.WakeDetector and core.mic.Mic (device choice + resampling)
— the exact same objects jarvis.py's main loop uses — so what this
prints is exactly what production would have done with this audio,
frame for frame. It answers three questions in order:

  1. Did mic auto-selection pick a working device? (prints the name)
  2. Does webrtcvad ever recognize your voice as speech at all? (VAD gate)
  3. When it does, what score does the wake model give "Hey Jarvis"? (vs
     the live THRESHOLD)

If (2) never fires, the problem is audio capture / mic gain, not the
wake-word model. If (2) fires but (3) stays low, it's a model/threshold
problem — try `set JARVIS_WAKE_THRESHOLD=0.2` and rerun.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.mic import Mic
from core.wakeword import WakeDetector, THRESHOLD

DURATION_S = 15

print("Loading wake-word model...")
detector = WakeDetector()
mic = Mic()
mic.read()  # opens the device exactly as jarvis.py does (prints the "Mic: ..." line)
print(f"(live THRESHOLD={THRESHOLD:.2f})")

print(f"\nListening for {DURATION_S}s — say 'Hey Jarvis' a few times NOW, "
      "at your normal distance/volume...\n")

start = time.time()
max_amp = 0.0
max_score = 0.0
any_speech_seen = False
any_model_ran = False
wakes = 0

while time.time() - start < DURATION_S:
    audio = mic.read()
    amp = float(np.abs(audio).mean())
    max_amp = max(max_amp, amp)

    speech_before = detector._is_speech(audio)  # noqa: SLF001 — diagnostic only
    if speech_before:
        any_speech_seen = True

    score = detector.process(audio)
    if detector.model_frames:
        any_model_ran = True
    if score > max_score:
        max_score = score
    if score >= THRESHOLD:
        wakes += 1
        print(f"  t={time.time()-start:4.1f}s  *** WAKE (score={score:.2f}) ***")
        detector.reset()
    elif score > 0.05:
        print(f"  t={time.time()-start:4.1f}s  amp={amp:6.1f}  vad_speech={speech_before}  score={score:.2f}")

mic.close()

print("\n=== SUMMARY ===")
print(f"Max raw amplitude seen:        {max_amp:.1f}")
print(f"webrtcvad ever detected speech: {any_speech_seen}")
print(f"Wake model ever ran:            {any_model_ran}  (model_frames={detector.model_frames})")
print(f"Max wake score seen:            {max_score:.2f}  (threshold: {THRESHOLD:.2f})")
print(f"Wakes triggered:                {wakes}")

if not any_speech_seen:
    print("\n-> webrtcvad NEVER classified anything as speech. The wake model never even ran.")
    print("   This points to the mic itself: wrong device selected, muted, or gain too low.")
    print("   Check the 'Mic: [...]' line above — is that your real, working mic?")
elif not any_model_ran:
    print("\n-> Speech was detected by VAD but the wake model never ran — unexpected; report this.")
elif max_score < THRESHOLD * 0.5:
    print("\n-> Mic and VAD are working, but the model's confidence for 'Hey Jarvis' stayed very")
    print("   low even during real speech. Possibly a pronunciation/distance/noise mismatch with")
    print("   the trained model, or you may be too far from the mic.")
elif wakes == 0:
    print(f"\n-> Scores got close (max {max_score:.2f}) but never crossed {THRESHOLD:.2f}.")
    print(f"   Try: set JARVIS_WAKE_THRESHOLD=0.2  (then rerun python run.py)")
else:
    print("\n-> Wake detection worked correctly in this isolated test.")
    print("   If it still fails when run via 'python run.py', something differs between this")
    print("   script and that process (e.g. a different mic gets auto-selected) — compare the")
    print("   'Mic: ...' line from run.py's output against the one above.")

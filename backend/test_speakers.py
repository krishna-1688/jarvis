"""
test_speakers.py — run this to find out which playback device Jarvis
should actually speak through.

Plays a beep + spoken label on each available output device in turn,
pausing between them. Whichever one you actually HEAR is the one to
put in backend/.env as:

    JARVIS_SPEAKER_DEVICE=<exact name printed below>

Run with: python test_speakers.py
"""
import time
import pygame
from pygame._sdl2 import audio as sdl2_audio

devices = sdl2_audio.get_audio_device_names(False)
print(f"Found {len(devices)} playback device(s):\n")
for name in devices:
    print(f"  - {name}")
print()

for name in devices:
    print(f"--- Playing through: {name!r} — listen now ---")
    pygame.mixer.quit()
    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.mixer.init(devicename=name)

    import numpy as np
    sr = 22050
    tone = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 12000).astype(np.int16)
    sound = pygame.sndarray.make_sound(tone)
    sound.play()
    time.sleep(1.2)
    pygame.mixer.quit()
    time.sleep(0.8)

print("\nDone. Whichever one you heard the beep from, set in backend/.env:")
print("  JARVIS_SPEAKER_DEVICE=<that exact name>")
print("Then restart python backend/run.py.")

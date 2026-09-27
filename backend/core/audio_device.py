"""
audio_device.py — picks the microphone Jarvis actually listens through.

Windows machines commonly expose several input endpoints — e.g. a
generic "Microphone (Realtek Audio)" MME entry with nothing physically
wired to it, alongside a laptop's real built-in mic array reported
under a different name/host-API. PyAudio's "default input device" is
whatever the OS currently has set as default, which is NOT guaranteed
to be a live one: on the dev machine this shipped on, the OS default
(Realtek) measured ~0 average signal in a normal room while "Microphone
Array (Intel Smart Sound Technology)" measured a real noise floor —
meaning both the wake-word listener and the STT mic were silently
listening to a dead device no matter how loud anyone talked, with no
error anywhere to indicate why.

Rather than hardcode a device index (fragile — indices shift across
reboots/driver updates), this briefly probes every input-capable
device for ambient signal once per process and picks whichever one is
actually live, caching the result so repeated calls (the wake-word
listener restarts its stream on every conversation turn) don't re-probe.

Override with the JARVIS_MIC_DEVICE_INDEX env var to pin a specific
device manually if auto-detection ever picks the wrong one.
"""

import os
import pyaudio
import numpy as np

_cached_index = None
_cache_done = False


def get_input_device_index(refresh: bool = False):
    """The PyAudio input_device_index to use everywhere in this app —
    memoized after the first call so the one-time probe cost is paid
    once per process, not on every mic-stream restart. refresh=True
    re-probes (core/mic.py does this after the stream fails, e.g. a
    headset was unplugged)."""
    global _cached_index, _cache_done
    if _cache_done and not refresh:
        return _cached_index

    override = os.environ.get("JARVIS_MIC_DEVICE_INDEX")
    if override is not None:
        _cached_index = int(override)
        _cache_done = True
        print(f"Mic device pinned via JARVIS_MIC_DEVICE_INDEX={_cached_index}")
        return _cached_index

    p = pyaudio.PyAudio()
    best_index, best_level, best_name = None, -1.0, None
    try:
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
            except Exception:
                continue
            if info.get("maxInputChannels", 0) < 1:
                continue
            name = info.get("name", "")
            # These report as "inputs" too but aren't a microphone.
            if "stereo mix" in name.lower() or "loopback" in name.lower():
                continue
            try:
                rate = int(info["defaultSampleRate"])
                stream = p.open(format=pyaudio.paInt16, channels=1, rate=rate,
                                 input=True, input_device_index=i, frames_per_buffer=1280)
                levels = [
                    np.abs(np.frombuffer(stream.read(1280, exception_on_overflow=False), dtype=np.int16)).mean()
                    for _ in range(4)
                ]
                stream.stop_stream()
                stream.close()
                level = float(np.mean(levels))
                if level > best_level:
                    best_level, best_index, best_name = level, i, name
            except Exception:
                continue
    finally:
        p.terminate()

    _cached_index, _cache_done = best_index, True
    if best_index is not None:
        print(f"Mic auto-selected: [{best_index}] {best_name} (ambient signal={best_level:.1f})")
    else:
        print("Mic auto-selection found no usable input device — falling back to system default.")
    return _cached_index

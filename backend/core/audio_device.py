"""
audio_device.py — which microphone Jarvis listens through, and how.

Measured on this laptop with a speaker->mic loopback (six spoken "Hey
Jarvis" clips — Indian, US and British voices — played out of the laptop
speakers, recorded on every input path at once, scored by the real wake
model):

  WASAPI  default mic @ native 48 kHz, resampled -> 6/6 detected, noise floor ~4
  MME     "Microsoft Sound Mapper" @ 16 kHz       -> 6/6 detected, noise floor ~30-48
  DirectSound "Primary Sound Capture Driver"      -> 0/6: reads return instantly
                                                     with clipped junk
  DirectSound "Microphone Array (Intel SST)"      -> 0/6: pure digital zeros
  WDM-KS devices                                  -> refuse to open

The old picker opened every device and kept whichever read loudest over
~80 ms. Freshly opened devices return mostly warm-up zeros for that long,
so the choice was close to random (it printed "ambient signal=0.2" for
every device) — and on some runs it landed on a broken DirectSound device,
killing wake-word detection with no error anywhere.

So the choice is by host API, never by loudness:
  1. WASAPI's default input device at its native rate — the cleanest signal,
     and it follows the microphone chosen in Windows Sound settings. Only
     rates that are an exact multiple of 16 kHz (48/32/16 kHz) qualify;
     core/mic.py downsamples them.
  2. MME's default input ("Microsoft Sound Mapper") at 16 kHz — Windows
     resamples it itself.
  DirectSound and WDM-KS are never used.

JARVIS_MIC_DEVICE_INDEX pins a device by index (opened at 16 kHz, or at its
native rate if that's a multiple of 16 kHz and 16 kHz is refused).
"""

import os

import pyaudio

TARGET_RATE = 16000

_cached = None  # (index, rate) or None


def _can_open(pa, index: int, rate: int) -> bool:
    try:
        s = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                    input_device_index=index, frames_per_buffer=rate // 50)
        s.read(rate // 50, exception_on_overflow=False)
        s.stop_stream()
        s.close()
        return True
    except Exception:
        return False


def _default_on(pa, api_type):
    try:
        idx = pa.get_host_api_info_by_type(api_type).get("defaultInputDevice", -1)
    except Exception:
        return None
    return idx if isinstance(idx, int) and idx >= 0 else None


def _native_rate(pa, index: int) -> int:
    return int(pa.get_device_info_by_index(index)["defaultSampleRate"])


def select_input(refresh: bool = False):
    """(device index, capture rate) for the mic. The capture rate is always
    a multiple of 16 kHz. Memoized; refresh=True re-selects (core/mic.py
    does after a stream fails — headset unplugged, default mic changed)."""
    global _cached
    if _cached is not None and not refresh:
        return _cached

    pa = pyaudio.PyAudio()
    choice, how = (None, TARGET_RATE), "system default"
    try:
        override = os.environ.get("JARVIS_MIC_DEVICE_INDEX")
        if override is not None:
            idx = int(override)
            native = _native_rate(pa, idx)
            if _can_open(pa, idx, TARGET_RATE):
                choice, how = (idx, TARGET_RATE), "pinned via JARVIS_MIC_DEVICE_INDEX"
            elif native % TARGET_RATE == 0 and _can_open(pa, idx, native):
                choice, how = (idx, native), "pinned via JARVIS_MIC_DEVICE_INDEX"
        else:
            wasapi = _default_on(pa, pyaudio.paWASAPI)
            if wasapi is not None:
                native = _native_rate(pa, wasapi)
                if native % TARGET_RATE == 0 and _can_open(pa, wasapi, native):
                    choice, how = (wasapi, native), "WASAPI"
            if choice[0] is None:
                mme = _default_on(pa, pyaudio.paMME)
                if mme is not None and _can_open(pa, mme, TARGET_RATE):
                    choice, how = (mme, TARGET_RATE), "MME"

        if choice[0] is not None:
            name = pa.get_device_info_by_index(choice[0]).get("name", "?")
            print(f"Mic: [{choice[0]}] {name} via {how} @ {choice[1]} Hz")
        else:
            print("Mic: no WASAPI/MME default input opened — falling back to the system default")
    finally:
        pa.terminate()

    _cached = choice
    return _cached


def get_input_device_index(refresh: bool = False):
    """Device index only (diagnostic scripts); capture code uses
    select_input() so it also gets the rate to open at."""
    return select_input(refresh)[0]

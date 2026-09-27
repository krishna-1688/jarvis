"""
core/power.py — Windows lid/display/AC state and the stay-awake toggle.

run.py uses this to:
  1. know whether the dashboard could actually be seen right now
     (lid open AND display on) before opening it automatically;
  2. keep the machine from going to Modern Standby *only* while Jarvis is
     running and plugged in, by temporarily setting "lid close action" to
     "do nothing" and setting an execution-state flag that block sleep —
     never on battery, and always restored to whatever it was when Jarvis
     started, even on a crash (see restore_lid_action's caller in run.py's
     finally block).

This deliberately cannot, and does not try to, wake the machine from real
sleep (S0 Modern Standby) — Windows suspends ordinary processes there;
only a signed low-power co-processor driver can do that.
"""

import ctypes
import json
import os
import re
import subprocess
import threading

# ── SetThreadExecutionState — keeps Windows from sleeping/turning off
# the display while held; screen-off is still allowed (no
# ES_DISPLAY_REQUIRED), just not system sleep.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_awake_active = False
_awake_lock = threading.Lock()


def set_stay_awake(enabled: bool):
    global _awake_active
    with _awake_lock:
        if enabled == _awake_active:
            return
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        _awake_active = enabled


# ── Lid-close action ────────────────────────────────────────────
# SUB_BUTTONS / LIDACTION GUIDs are fixed Windows power-setting IDs.
_SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
_LIDACTION = "5ca83367-6e45-459f-a27b-476b1d01c936"
LID_DO_NOTHING = 0

_saved_lid_index = None  # the value to restore to; None once restored

# Persisted to disk the moment the lid setting is changed, and only
# removed once it's restored — so if this process is killed with no
# chance to run cleanup code (Task Manager, a crash, a power cut), the
# NEXT startup finds this file and restores the setting before doing
# anything else, instead of leaving "lid does nothing" stuck permanently.
_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lid_override.json")


def _run_powercfg(*args) -> str:
    return subprocess.run(["powercfg", *args], capture_output=True, text=True, timeout=5).stdout


def _active_scheme_guid() -> str | None:
    out = _run_powercfg("/getactivescheme")
    m = re.search(r"GUID:\s*([0-9a-fA-F-]+)", out)
    return m.group(1) if m else None


def _current_lid_index(scheme: str) -> int | None:
    """The lid-close action is a HIDDEN power setting — confirmed live:
    `powercfg /query` never prints a value for it, even when named
    directly, so the previous text-parsing version of this function
    always returned None and enable_stay_awake_lid() silently did
    nothing. Reading the registry directly (the same place Windows' own
    Power Options UI reads from) actually works: a per-scheme user
    override if one has ever been set, else the scheme's shipped
    default."""
    import winreg
    user_path = (
        "SYSTEM\\CurrentControlSet\\Control\\Power\\User\\PowerSchemes\\" + scheme
        + "\\" + _SUB_BUTTONS + "\\" + _LIDACTION
    )
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, user_path) as k:
            return winreg.QueryValueEx(k, "ACSettingIndex")[0]
    except FileNotFoundError:
        pass
    default_path = (
        "SYSTEM\\CurrentControlSet\\Control\\Power\\PowerSettings\\" + _SUB_BUTTONS
        + "\\" + _LIDACTION + "\\DefaultPowerSchemeValues\\" + scheme
    )
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, default_path) as k:
            return winreg.QueryValueEx(k, "ACSettingIndex")[0]
    except FileNotFoundError:
        return None


def enable_stay_awake_lid() -> bool:
    """Sets lid-close (on AC) to 'do nothing', remembering the previous
    value. Returns True if it changed anything. No-op (and returns False)
    if it's already 'do nothing', or if powercfg isn't writable without
    elevation — callers should not assume this always succeeds."""
    global _saved_lid_index
    scheme = _active_scheme_guid()
    if not scheme:
        return False
    current = _current_lid_index(scheme)
    if current is None or current == LID_DO_NOTHING:
        return False
    try:
        _run_powercfg("/setacvalueindex", scheme, _SUB_BUTTONS, _LIDACTION, str(LID_DO_NOTHING))
        _run_powercfg("/setactive", scheme)
    except Exception as e:
        print(f"[power] couldn't change lid action ({e}) — normal sleep-on-lid-close stays in effect")
        return False
    _saved_lid_index = current
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"scheme": scheme, "original_index": current}, f)
    except OSError as e:
        print(f"[power] couldn't persist the original lid setting ({e}) — "
              f"it may not self-heal after a hard crash")
    print("[power] lid-close set to 'do nothing' while Jarvis runs on AC power")
    return True


def restore_lid_action():
    """Puts the lid-close setting back exactly as found. Safe to call
    even if enable_stay_awake_lid() never ran or already restored."""
    global _saved_lid_index
    if _saved_lid_index is None:
        return
    scheme = _active_scheme_guid()
    if scheme:
        try:
            _run_powercfg("/setacvalueindex", scheme, _SUB_BUTTONS, _LIDACTION, str(_saved_lid_index))
            _run_powercfg("/setactive", scheme)
            print("[power] restored your original lid-close setting")
        except Exception as e:
            print(f"[power] couldn't restore lid action ({e}) — please check Power Options if this matters")
    _saved_lid_index = None
    try:
        if os.path.exists(_STATE_PATH):
            os.remove(_STATE_PATH)
    except OSError:
        pass


def recover_from_previous_crash():
    """Call once at startup, before enable_stay_awake_lid(). If the last
    run changed the lid setting and never got to restore it (killed
    abruptly), puts it back now."""
    if not os.path.exists(_STATE_PATH):
        return
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        _run_powercfg("/setacvalueindex", saved["scheme"], _SUB_BUTTONS, _LIDACTION, str(saved["original_index"]))
        _run_powercfg("/setactive", saved["scheme"])
        print("[power] a previous run didn't shut down cleanly — restored your lid-close setting")
    except Exception as e:
        print(f"[power] couldn't recover the previous lid setting ({e}) — please check Power Options")
    finally:
        try:
            os.remove(_STATE_PATH)
        except OSError:
            pass


# ── Battery / AC state ──────────────────────────────────────────
class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def on_ac_power() -> bool | None:
    status = _SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    if status.ACLineStatus == 255:
        return None  # unknown
    return bool(status.ACLineStatus)


# ── Lid / display state via a hidden window's WM_POWERBROADCAST ──
# This is the only reliable, zero-poll way to learn "lid just closed" /
# "display just turned off" on Windows; both arrive as PBT_POWERSETTINGCHANGE
# once the process registers for the matching GUIDs.
GUID_LIDSWITCH_STATE_CHANGE = "{BA3E0F4D-B817-4094-A2D1-D56379E6A0F3}"
GUID_MONITOR_POWER_ON = "{02731015-4510-4526-99E6-E5A17EBD1AEA}"
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0


class PowerWatcher:
    """Runs a hidden message-only window on its own thread; calls
    on_change(lid_open: bool | None, display_on: bool | None) whenever
    either changes. Costs nothing between events — no polling."""

    def __init__(self, on_change):
        self._on_change = on_change
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-power-watcher")

    def start(self):
        self._thread.start()

    def _run(self):
        import uuid
        import win32api
        import win32con
        import win32gui

        # POWERBROADCAST_SETTING: GUID PowerSetting; DWORD DataLength; BYTE Data[1]
        class POWERBROADCAST_SETTING(ctypes.Structure):
            _fields_ = [("PowerSetting", ctypes.c_ubyte * 16),
                        ("DataLength", ctypes.c_ulong),
                        ("Data", ctypes.c_ubyte * 1)]

        lid_guid = uuid.UUID(GUID_LIDSWITCH_STATE_CHANGE)
        monitor_guid = uuid.UUID(GUID_MONITOR_POWER_ON)

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and lparam:
                try:
                    pbs = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    setting = uuid.UUID(bytes_le=bytes(pbs.PowerSetting))
                    value = pbs.Data[0] if pbs.DataLength else None
                    if setting == lid_guid and value is not None:
                        self._on_change(lid_open=bool(value), display_on=None)
                    elif setting == monitor_guid and value is not None:
                        self._on_change(lid_open=None, display_on=bool(value))
                except Exception as e:
                    print(f"[power] power-broadcast parse error: {e}")
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = wnd_proc
        wc.lpszClassName = "JarvisPowerWatcher"
        wc.hInstance = win32api.GetModuleHandle(None)
        try:
            atom = win32gui.RegisterClass(wc)
        except Exception:
            atom = wc.lpszClassName
        hwnd = win32gui.CreateWindow(atom, "JarvisPowerWatcher", 0, 0, 0, 0, 0,
                                     win32con.HWND_MESSAGE, 0, wc.hInstance, None)

        register = ctypes.windll.user32.RegisterPowerSettingNotification
        register.restype = ctypes.c_void_p
        register.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        for guid in (lid_guid, monitor_guid):
            buf = ctypes.create_string_buffer(guid.bytes_le, 16)
            handle = register(ctypes.c_void_p(hwnd), ctypes.cast(buf, ctypes.c_void_p), DEVICE_NOTIFY_WINDOW_HANDLE)
            if not handle:
                print(f"[power] RegisterPowerSettingNotification failed for {guid}")

        win32gui.PumpMessages()

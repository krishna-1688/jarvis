"""
run.py — supervisor. Starts server.py + jarvis.py, restarts either one if
it dies, tracks lid/display/AC state so the dashboard only opens where it
could actually be seen, and (only while running, only on AC power) tells
Windows not to sleep when the lid closes — restored the moment Jarvis
stops, including on a crash.

Ctrl+J here opens the dashboard on demand (same as saying "open
dashboard" or the backend's automatic open after two voice exchanges);
Ctrl+C shuts everything down cleanly.
"""

import atexit
import os
import subprocess
import sys
import threading
import time

import requests

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = "http://127.0.0.1:8000"

# Backoff so a genuinely broken setup (bad .env, port in use) doesn't spin
# a crash loop forever — each process gets progressively longer waits,
# capped, and resets once it's stayed up a while.
RESTART_BACKOFF_S = [1, 2, 5, 10, 30, 60]
STABLE_AFTER_S = 60


class Supervised:
    """One child process, restarted on exit with backoff."""

    def __init__(self, name: str, args: list, ready_check=None):
        self.name = name
        self.args = args
        self.ready_check = ready_check
        self.proc: subprocess.Popen | None = None
        self._stopping = False
        self._restarts = 0
        self._started_at = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"supervise-{name}")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stopping = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _run(self):
        while not self._stopping:
            print(f"[run] starting {self.name}...")
            self._started_at = time.time()
            self.proc = subprocess.Popen([sys.executable, *self.args], cwd=BACKEND_DIR)
            if sys.platform == "win32":
                from core.winjob import add_process
                add_process(self.proc.pid)
            code = self.proc.wait()
            if self._stopping:
                break
            if time.time() - self._started_at > STABLE_AFTER_S:
                self._restarts = 0
            wait = RESTART_BACKOFF_S[min(self._restarts, len(RESTART_BACKOFF_S) - 1)]
            self._restarts += 1
            print(f"[run] {self.name} exited (code {code}) — restarting in {wait}s "
                  f"(attempt {self._restarts})")
            for _ in range(int(wait * 10)):
                if self._stopping:
                    break
                time.sleep(0.1)
        print(f"[run] {self.name} stopped.")


def _single_instance() -> bool:
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\JarvisSupervisor")
    globals()["_instance_mutex"] = handle
    return kernel32.GetLastError() != 183


def _report_device_state(**state):
    try:
        requests.post(f"{SERVER_URL}/internal/device_state", json=state, timeout=2)
    except requests.RequestException:
        pass


def _stay_awake_worker(stop_event: threading.Event):
    """Enables the lid-close override only while on AC. Polls battery
    status every 30s (SetThreadExecutionState itself is cheap and this
    poll is the only recurring cost of the whole feature — negligible)."""
    from core.power import on_ac_power, set_stay_awake, enable_stay_awake_lid, restore_lid_action
    applied = False
    while not stop_event.is_set():
        ac = on_ac_power()
        should_apply = ac is True
        if should_apply and not applied:
            enable_stay_awake_lid()
            set_stay_awake(True)
            applied = True
            print("[run] on AC power — lid-close won't sleep the machine while Jarvis runs")
        elif not should_apply and applied:
            set_stay_awake(False)
            restore_lid_action()
            applied = False
            print("[run] on battery — normal sleep behavior restored")
        stop_event.wait(30)
    if applied:
        set_stay_awake(False)
        restore_lid_action()


def _power_watcher_thread():
    from core.power import PowerWatcher
    watcher = PowerWatcher(lambda lid_open=None, display_on=None: _report_device_state(
        **{k: v for k, v in (("lid_open", lid_open), ("display_on", display_on)) if v is not None}
    ))
    watcher.start()


def _dashboard_hotkey_thread(stop_event: threading.Event):
    """Ctrl+J opens the dashboard even while it isn't running (the
    Electron app's own Ctrl+J only works once its window already exists)."""
    try:
        import keyboard
    except ImportError:
        print("[run] 'keyboard' package not installed — Ctrl+J hotkey disabled (dashboard still "
              "opens via voice: \"open dashboard\")")
        return

    def _open():
        try:
            requests.post(f"{SERVER_URL}/dashboard/open", timeout=5)
        except requests.RequestException:
            from core.dashboard import launch
            launch()

    keyboard.add_hotkey("ctrl+j", _open)
    stop_event.wait()
    keyboard.remove_all_hotkeys()


def main():
    if not _single_instance():
        print("Jarvis is already running (run.py, server.py or jarvis.py). Exiting.")
        return 1

    sys.path.insert(0, BACKEND_DIR)

    print("=" * 44)
    print("  J.A.R.V.I.S — starting")
    print("  Ctrl+J  → open dashboard")
    print("  Ctrl+C  → shut down")
    print("=" * 44)

    stop_event = threading.Event()
    server = Supervised("server.py", ["server.py"])
    voice = Supervised("jarvis.py", ["jarvis.py"])

    server.start()
    time.sleep(1)  # small head start before jarvis.py's own health-poll
    voice.start()

    if sys.platform == "win32":
        from core.power import recover_from_previous_crash
        recover_from_previous_crash()
        threading.Thread(target=_stay_awake_worker, args=(stop_event,), daemon=True).start()
        threading.Thread(target=_power_watcher_thread, daemon=True).start()
        threading.Thread(target=_dashboard_hotkey_thread, args=(stop_event,), daemon=True).start()

    def _cleanup():
        stop_event.set()
        if sys.platform == "win32":
            from core.power import restore_lid_action, set_stay_awake
            set_stay_awake(False)
            restore_lid_action()
    atexit.register(_cleanup)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[run] shutting down...")
        server.stop()
        voice.stop()
        for s in (server, voice):
            for _ in range(100):
                if s.proc is None or s.proc.poll() is not None:
                    break
                time.sleep(0.1)
            if s.proc and s.proc.poll() is None:
                s.proc.kill()
        _cleanup()
        print("[run] stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

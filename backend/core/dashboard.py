"""
core/dashboard.py — start the Electron dashboard on demand.

The dashboard is not kept running (Chromium costs ~250-400 MB even
hidden). It is launched when asked for — "open dashboard", Ctrl+J
(handled by run.py), or automatically after a couple of voice exchanges
while the lid is open — and fully exits when its window is closed.

Uses the prebuilt frontend (frontend/dist, `npm run build`), not the
Vite dev server, so nothing else has to be running.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(ROOT, "frontend")
ELECTRON_EXE = os.path.join(FRONTEND, "node_modules", "electron", "dist",
                            "electron.exe" if sys.platform == "win32" else "electron")
DIST_INDEX = os.path.join(FRONTEND, "dist", "index.html")


def is_running() -> bool:
    import psutil
    target = os.path.normcase(os.path.abspath(ELECTRON_EXE))
    for proc in psutil.process_iter(["exe"]):
        try:
            if proc.info["exe"] and os.path.normcase(proc.info["exe"]) == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def launch() -> tuple[bool, str]:
    """Starts the dashboard, or focuses it if it's already open (Electron's
    single-instance lock hands the second launch to the running one)."""
    if not os.path.exists(ELECTRON_EXE):
        return False, "Electron isn't installed — run npm install in the frontend folder."
    if not os.path.exists(DIST_INDEX):
        return False, "The dashboard hasn't been built yet — run npm run build in the frontend folder."
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_START_URL"}
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([ELECTRON_EXE, FRONTEND], cwd=FRONTEND, env=env, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    return True, "Opening the dashboard."

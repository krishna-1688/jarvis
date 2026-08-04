"""
run.py — starts both the FastAPI backend (server.py) and the voice
client (jarvis.py) with one command.
"""

import subprocess
import sys
import os
import time

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    python = sys.executable

    print("Starting backend (server.py)...")
    server_proc = subprocess.Popen([python, "server.py"], cwd=BACKEND_DIR)

    # Small head start before the client's own health-poll kicks in —
    # not strictly required (jarvis.py polls /health itself) but avoids
    # a guaranteed first failed attempt.
    time.sleep(1)

    print("Starting voice client (jarvis.py)...")
    client_proc = subprocess.Popen([python, "jarvis.py"], cwd=BACKEND_DIR)

    try:
        # Whichever process exits first takes the other one down with it.
        # Previously this only ever waited on client_proc, so a backend-
        # initiated shutdown (e.g. the "shutdown jarvis" voice command,
        # which calls os._exit(0) inside server.py) left jarvis.py running
        # as an orphan indefinitely — confirmed live: after several
        # restarts during testing, multiple orphaned jarvis.py/run.py
        # processes were still running in the background, each with live
        # mic access, none of them reachable or visible as "the" running
        # instance.
        while client_proc.poll() is None and server_proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        for proc in (client_proc, server_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (client_proc, server_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()

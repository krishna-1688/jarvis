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
        client_proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down backend...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    main()

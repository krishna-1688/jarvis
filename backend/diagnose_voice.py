"""
diagnose_voice.py — one-shot operational check for "voice doesn't work
in the frontend." The pipeline itself (wakeword.py -> voice.py ->
jarvis.py -> voice_bridge.py -> server.py:/internal/voice_event ->
WS /stream -> frontend hooks) is code-correct; this checks whether the
processes/config it depends on are actually up, not the wiring.

Run: python diagnose_voice.py
"""

import asyncio
import json
import os
import sys
import time

import psutil
import requests

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/stream"


def find_python_processes():
    server_running = False
    jarvis_running = False
    server_pid = None
    jarvis_pid = None

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "python" not in name:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            if "server.py" in cmdline:
                server_running = True
                server_pid = proc.info["pid"]
            if "jarvis.py" in cmdline:
                jarvis_running = True
                jarvis_pid = proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {
        "server_running": server_running, "server_pid": server_pid,
        "jarvis_running": jarvis_running, "jarvis_pid": jarvis_pid,
    }


def check_health():
    # /health's WhatsApp-reachability check has its own internal timeout
    # (whatsapp_service not running is a normal, expected state, not a
    # backend failure) that can push this well past a "fast" endpoint's
    # usual response time — give it real room rather than false-flagging
    # the whole backend as down.
    try:
        r = requests.get(f"{SERVER_URL}/health", timeout=12)
        return {"ok": r.status_code == 200, "status_code": r.status_code, "body": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _listen_and_probe():
    import websockets

    events = []
    try:
        async with websockets.connect(WS_URL, open_timeout=5) as ws:
            async def listener():
                async for msg in ws:
                    events.append(json.loads(msg))

            task = asyncio.create_task(listener())
            await asyncio.sleep(1.0)

            probe_ok = False
            probe_error = None
            try:
                r = requests.post(f"{SERVER_URL}/internal/voice_event", json={"type": "wake"}, timeout=3)
                probe_ok = r.status_code == 200
            except Exception as e:
                probe_error = str(e)

            await asyncio.sleep(2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        return {"connected": False, "error": str(e), "events": [], "probe_ok": False, "probe_error": None}

    return {
        "connected": True, "events": events,
        "probe_ok": probe_ok, "probe_error": probe_error,
        "probe_seen": any(e.get("type") == "wake" for e in events),
    }


def check_env():
    from dotenv import dotenv_values
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return {"env_file_found": False, "missing": ["GROQ_API_KEY", "VTOP_USERNAME", "VTOP_PASSWORD"]}

    values = dotenv_values(env_path)
    required = ["GROQ_API_KEY", "VTOP_USERNAME", "VTOP_PASSWORD"]
    missing = [k for k in required if not values.get(k)]
    return {"env_file_found": True, "missing": missing}


def check_mic():
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        mic = sr.Microphone(sample_rate=16000)
        with mic as source:
            r.adjust_for_ambient_noise(source, duration=0.5)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    print("=" * 60)
    print("JARVIS VOICE DIAGNOSTIC")
    print("=" * 60)

    print("\n[1] Process check")
    procs = find_python_processes()
    print(f"  server.py running: {procs['server_running']} (pid={procs['server_pid']})")
    print(f"  jarvis.py running: {procs['jarvis_running']} (pid={procs['jarvis_pid']})")

    print("\n[2] Backend health (GET /health)")
    health = check_health()
    print(f"  {health}")

    print("\n[3+4] WS /stream — listening 3s, then POSTing a synthetic 'wake' event")
    ws_result = asyncio.run(_listen_and_probe())
    if not ws_result["connected"]:
        print(f"  WS CONNECTION FAILED: {ws_result['error']}")
    else:
        non_ping = [e for e in ws_result["events"] if e.get("type") != "ping"]
        print(f"  events received (excluding ping): {non_ping if non_ping else 'NO EVENTS'}")
        print(f"  synthetic 'wake' POST -> HTTP ok: {ws_result['probe_ok']}"
              + (f" (error: {ws_result['probe_error']})" if ws_result["probe_error"] else ""))
        print(f"  synthetic 'wake' event actually seen on WS: {ws_result['probe_seen']}")

    print("\n[5] .env check (presence only, not values)")
    env = check_env()
    print(f"  {env}")

    print("\n[6] Microphone permission/capture check (0.5s)")
    mic = check_mic()
    print(f"  {mic}")

    print("\n" + "=" * 60)
    verdict = "unknown"
    if not health.get("ok"):
        verdict = "server.py is not running/reachable — start it (see run.py) before anything else."
    elif not procs["jarvis_running"]:
        verdict = "server.py is up but jarvis.py is NOT running — nobody is emitting voice events. Run python run.py (or python jarvis.py) alongside server.py."
    elif env.get("missing"):
        verdict = f"jarvis.py is running but may be crash-looping — missing .env vars: {env['missing']}."
    elif not mic.get("ok"):
        verdict = f"Microphone capture failed: {mic.get('error')} — likely a Windows mic permission/device issue."
    elif ws_result["connected"] and not ws_result["probe_seen"]:
        verdict = "WS connects but the synthetic test event never arrived — check server.py's /internal/voice_event -> ws_hub.broadcast wiring."
    elif ws_result["connected"] and ws_result["probe_seen"]:
        verdict = "Pipeline is fully operational end-to-end (server up, jarvis.py up, .env present, mic OK, WS roundtrip confirmed). If voice still doesn't show in the frontend, the issue is in that specific Electron window's WS connection, not the backend."
    else:
        verdict = "WS could not connect at all — is server.py's /stream endpoint reachable? Check firewall/port."

    print(f"VERDICT: {verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()

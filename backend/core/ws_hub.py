"""
core/ws_hub.py — bridges sync/background code (APScheduler jobs, feature
functions called from server.py's sync handlers) to the async WebSocket
clients connected at /stream. `broadcast()` is safe to call from any
thread; it schedules the actual send onto the FastAPI event loop.
"""

import asyncio

_connections: list = []
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop


def register(websocket):
    _connections.append(websocket)


def unregister(websocket):
    if websocket in _connections:
        _connections.remove(websocket)


async def _broadcast_async(event: dict):
    dead = []
    for ws in _connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister(ws)


def broadcast(event: dict):
    """Safe to call from any thread, including APScheduler's worker threads."""
    if _loop is None or not _connections:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_async(event), _loop)

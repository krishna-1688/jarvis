"""
core/security.py — who may talk to the local server.

server.py listens on 127.0.0.1 only, but that alone doesn't keep web pages
out: any site open in the user's browser can send requests to
http://127.0.0.1:8000 and, with permissive CORS, read the answers — marks,
attendance, conversations — or send commands ("what's on my screen", "send
a WhatsApp message"). A WebSocket isn't covered by CORS at all. And DNS
rebinding (a site that points its own domain at 127.0.0.1) gets around
CORS entirely.

So every request is checked here:
  - Host must be 127.0.0.1 or localhost — defeats DNS rebinding.
  - A request made from a browser (it carries Origin or Sec-Fetch-Site,
    which pages can't remove) must present this install's API token. The
    dashboard gets it from Electron's main process (electron/main.js);
    web pages have no way to read it.
  - The Python and Node clients — voice loop, supervisor, WhatsApp
    service — send neither header and need no token.

The token is random per install, kept in backend/data/.api_token
(git-ignored), and created on first start.
"""

import hmac
import json
import os
import secrets
from urllib.parse import parse_qs

TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".api_token")
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

_token = None


def api_token() -> str:
    global _token
    if _token is None:
        try:
            with open(TOKEN_PATH, encoding="utf-8") as f:
                _token = f.read().strip()
        except OSError:
            _token = ""
        if len(_token) < 32:
            _token = secrets.token_urlsafe(32)
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(_token)
    return _token


def _host_only(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):                       # [::1]:8000
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalOnlyGuard:
    """ASGI middleware (so it also covers the /stream WebSocket)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        reason = None
        if _host_only(headers.get("host", "")) not in ALLOWED_HOSTS | {"::1"}:
            reason = "host not allowed"
        elif ("origin" in headers or "sec-fetch-site" in headers) and scope.get("method") != "OPTIONS":
            token = headers.get("x-jarvis-token") or \
                (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("token") or [""])[0]
            if not hmac.compare_digest(token.encode(), api_token().encode()):
                reason = "browser request without the dashboard token"

        if reason is None:
            return await self.app(scope, receive, send)
        print(f"[security] blocked {scope['type']} {scope.get('path')} from origin "
              f"{headers.get('origin', '-')}: {reason}")
        if scope["type"] == "websocket":
            await receive()                            # the websocket.connect event
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": "Forbidden"}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

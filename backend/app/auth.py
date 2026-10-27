"""ACCESS_TOKEN gate. Active unless the server is bound to loopback (BIND_HOST set by backend/run.py).

Accepts the token as `?token=` (then remembered in an HttpOnly cookie), a `ros_token` cookie, or
`Authorization: Bearer`. Fails closed: no BIND_HOST or an empty ACCESS_TOKEN on a non-loopback
bind rejects everything.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

COOKIE = "ros_token"


def _loopback_bind() -> bool:
    host = os.getenv("BIND_HOST", "")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _presented(scope) -> tuple[str | None, bool]:
    """Return (token, came_from_query)."""
    headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
    q = parse_qs(scope.get("query_string", b"").decode()).get("token")
    if q:
        return q[0], True
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    if "cookie" in headers:
        c = SimpleCookie(headers["cookie"]).get(COOKIE)
        if c:
            return c.value, False
    return None, False


class AccessTokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or _loopback_bind():
            return await self.app(scope, receive, send)

        expected = os.getenv("ACCESS_TOKEN", "").strip()
        got, from_query = _presented(scope)
        if not expected or got is None or not secrets.compare_digest(got.encode(), expected.encode()):
            if scope["type"] == "websocket":
                await receive()  # websocket.connect
                return await send({"type": "websocket.close", "code": 1008})
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
            return await send({"type": "http.response.body", "body": b"unauthorized: ACCESS_TOKEN required"})

        if scope["type"] == "http" and from_query:
            secure = "; Secure" if scope.get("scheme") == "https" else ""

            async def send_with_cookie(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"set-cookie", f"{COOKIE}={got}; Path=/; HttpOnly; SameSite=Strict{secure}".encode())]
                await send(message)

            return await self.app(scope, receive, send_with_cookie)
        return await self.app(scope, receive, send)

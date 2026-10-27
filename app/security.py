"""Optional API-key check, written as plain ASGI middleware so MCP streaming responses are untouched.

When the ``API_KEY`` environment variable is set, every request except the public paths
must send it in the ``x-api-key`` header (or ``Authorization: Bearer <key>``).
In Foundry, add the key as a custom header on the MCP tool connection.
WebSockets pass through: the only one is the public voice demo, which ``app.voice`` rate-limits.
"""

from __future__ import annotations

import hmac
import json

PUBLIC_PATHS = ("/", "/health", "/info", "/docs", "/openapi.json", "/redoc", "/voice/config")


class ApiKeyMiddleware:
    def __init__(self, app, api_key: str | None) -> None:
        self.app = app
        self.api_key = api_key or None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.api_key or scope.get("method") == "OPTIONS" \
                or scope.get("path", "") in PUBLIC_PATHS or scope.get("path", "").startswith(("/docs", "/static/")):
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        supplied = headers.get("x-api-key") or ""
        auth = headers.get("authorization", "")
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()

        if supplied and hmac.compare_digest(supplied, self.api_key):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"error": "unauthorized", "detail": "Missing or invalid API key."}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

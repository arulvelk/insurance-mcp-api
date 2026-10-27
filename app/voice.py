"""Public voice demo: relays the browser's WebSocket to Azure Voice Live in Foundry agent mode.

The page talks to ``/voice/ws``; this module opens the matching Voice Live session with the
container app's managed identity, so visitors never hold an Azure credential. Because anyone can
open the page, the relay only forwards the few client events the page needs and enforces a session
length, a concurrency cap, a daily cap and a per-visitor hourly cap to keep the bill predictable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

TOKEN_SCOPE = "https://ai.azure.com/.default"
MAX_AUDIO_CHUNK = 256_000  # base64 chars, about 2.6 s of 24 kHz pcm16
REDACTED_SESSION_KEYS = ("instructions", "tools")

log = logging.getLogger("app.voice")


@dataclass(frozen=True)
class VoiceConfig:
    endpoint: str = ""
    agent_name: str = ""
    project_name: str = ""
    agent_version: str = ""
    api_version: str = "2026-07-15"
    avatar_character: str = "meg"
    avatar_style: str = "business"
    max_session_seconds: int = 180
    max_concurrent_sessions: int = 2
    daily_session_limit: int = 40
    hourly_sessions_per_visitor: int = 4
    allowed_origins: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint and self.agent_name and self.project_name)

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        env = os.getenv
        origins = tuple(o.strip().rstrip("/") for o in env("VOICE_ALLOWED_ORIGINS", "").split(",") if o.strip())
        return cls(
            endpoint=env("VOICE_ENDPOINT", ""),
            agent_name=env("VOICE_AGENT_NAME", ""),
            project_name=env("VOICE_PROJECT_NAME", ""),
            agent_version=env("VOICE_AGENT_VERSION", ""),
            api_version=env("VOICE_API_VERSION", cls.api_version),
            avatar_character=env("VOICE_AVATAR_CHARACTER", cls.avatar_character),
            avatar_style=env("VOICE_AVATAR_STYLE", cls.avatar_style),
            max_session_seconds=int(env("VOICE_MAX_SESSION_SECONDS", cls.max_session_seconds)),
            max_concurrent_sessions=int(env("VOICE_MAX_CONCURRENT_SESSIONS", cls.max_concurrent_sessions)),
            daily_session_limit=int(env("VOICE_DAILY_SESSION_LIMIT", cls.daily_session_limit)),
            hourly_sessions_per_visitor=int(env("VOICE_HOURLY_SESSIONS_PER_VISITOR", cls.hourly_sessions_per_visitor)),
            allowed_origins=origins,
        )

    def upstream_url(self) -> str:
        """Voice Live WebSocket URL in agent mode (same query parameters the azure-ai-voicelive SDK sends)."""
        parsed = urlparse(self.endpoint if "://" in self.endpoint else f"https://{self.endpoint}")
        params = {"api-version": self.api_version, "agent-name": self.agent_name,
                  "agent-project-name": self.project_name}
        if self.agent_version:
            params["agent-version"] = self.agent_version
        return f"wss://{parsed.netloc}{parsed.path.rstrip('/')}/voice-live/realtime?{urlencode(params)}"

    def session_update(self, avatar: bool) -> dict[str, Any]:
        """Audio formats the page uses, plus the avatar when the visitor's browser can show video.

        The agent's voice, instructions and tools stay as configured in Foundry.
        """
        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_sampling_rate": 24000,
        }
        if avatar:
            session["avatar"] = {"character": self.avatar_character, "style": self.avatar_style}
        return {"type": "session.update", "session": session}


class SessionLimiter:
    """In-memory limits; fine because the app runs as a single replica (see infra/resources.bicep)."""

    def __init__(self, config: VoiceConfig, clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.clock = clock
        self._lock = threading.Lock()
        self._active = 0
        self._day = ""
        self._today = 0
        self._by_visitor: dict[str, deque[float]] = defaultdict(deque)

    def try_start(self, visitor: str) -> str | None:
        """Reserve a session slot, or return the reason the visitor has to wait."""
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if day != self._day:
                self._day, self._today = day, 0
            recent = self._by_visitor[visitor]
            while recent and now - recent[0] > 3600:
                recent.popleft()
            if self._today >= self.config.daily_session_limit:
                return "The demo has reached today's limit. Please come back tomorrow."
            if self._active >= self.config.max_concurrent_sessions:
                return "Sam is on another call right now. Please try again in a few minutes."
            if len(recent) >= self.config.hourly_sessions_per_visitor:
                return "You've used this hour's demo calls. Please try again later."
            self._active += 1
            self._today += 1
            recent.append(now)
            return None

    def end(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


def filter_client_event(raw: str) -> dict[str, Any] | None:
    """Return the event to forward to Voice Live, or None to drop it.

    Visitors can stream microphone audio, interrupt, and connect the avatar, but cannot change the
    agent's instructions, tools or conversation history.
    """
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind == "input_audio_buffer.append":
        audio = event.get("audio")
        if isinstance(audio, str) and 0 < len(audio) <= MAX_AUDIO_CHUNK:
            return {"type": kind, "audio": audio}
        return None
    if kind in ("input_audio_buffer.clear", "response.cancel", "response.create"):
        return {"type": kind}
    if kind == "session.avatar.connect":
        sdp = event.get("client_sdp")
        if isinstance(sdp, str) and 0 < len(sdp) <= 100_000:
            return {"type": kind, "client_sdp": sdp}
    return None


def redact_server_event(raw: str) -> str:
    """Keep the agent's prompt and tool definitions out of the visitor's browser."""
    if '"session.' not in raw[:200]:
        return raw
    try:
        event = json.loads(raw)
    except ValueError:
        return raw
    session = event.get("session") if isinstance(event, dict) else None
    if not isinstance(session, dict) or not any(k in session for k in REDACTED_SESSION_KEYS):
        return raw
    for key in REDACTED_SESSION_KEYS:
        session.pop(key, None)
    return json.dumps(event)


def visitor_id(ws: WebSocket) -> str:
    forwarded = ws.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return ws.client.host if ws.client else "unknown"


class AzureTokenProvider:
    """Entra token for Voice Live from the app's managed identity (AZURE_CLIENT_ID selects it)."""

    def __init__(self) -> None:
        self._credential = None

    async def get(self) -> str:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        token = await asyncio.to_thread(self._credential.get_token, TOKEN_SCOPE)
        return token.token


async def _open_upstream(url: str, token: str):
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                         max_size=None, open_timeout=15)


def build_voice_router(config: VoiceConfig, limiter: SessionLimiter, tokens: AzureTokenProvider,
                       open_upstream=_open_upstream) -> APIRouter:
    voice = APIRouter()

    @voice.get("/voice/config", tags=["voice demo"])
    def get_config():
        """Whether the voice demo is configured, and the call length limit shown on the page."""
        return {"enabled": config.enabled, "max_session_seconds": config.max_session_seconds}

    @voice.websocket("/voice/ws")
    async def relay(ws: WebSocket):
        origin = (ws.headers.get("origin") or "").rstrip("/")
        if config.allowed_origins and origin not in config.allowed_origins:
            await ws.close(code=1008)
            return
        await ws.accept()
        if not config.enabled:
            await _end(ws, "error", "The voice demo isn't configured on this server.")
            return
        reason = limiter.try_start(visitor_id(ws))
        if reason:
            await _end(ws, "busy", reason)
            return
        try:
            want_avatar = ws.query_params.get("avatar", "1") != "0"
            try:
                upstream = await open_upstream(config.upstream_url(), await tokens.get())
            except Exception as exc:  # noqa: BLE001 - report any connection failure to the page
                await _end(ws, "error", f"Couldn't reach the voice service ({type(exc).__name__}).")
                return
            async with upstream:
                await upstream.send(json.dumps(config.session_update(want_avatar)))
                await ws.send_json({"type": "demo.ready", "max_session_seconds": config.max_session_seconds,
                                    "avatar": want_avatar})
                outcome = await _pump(ws, upstream, config.max_session_seconds)
            if outcome == "time_limit":
                await _end(ws, "time_limit", "That's the end of the demo call. Thanks for trying Sam!")
            elif outcome == "upstream_closed":
                await _end(ws, "ended", "The call ended.")
        finally:
            limiter.end()

    return voice


async def _pump(ws: WebSocket, upstream, max_seconds: int) -> str:
    async def from_browser():
        while True:
            event = filter_client_event(await ws.receive_text())
            if event is not None:
                await upstream.send(json.dumps(event))

    async def from_azure():
        async for message in upstream:
            if isinstance(message, str):
                _log_problem(message)
                await ws.send_text(redact_server_event(message))

    tasks = {asyncio.create_task(from_browser(), name="browser"), asyncio.create_task(from_azure(), name="azure")}
    try:
        done, _ = await asyncio.wait(tasks, timeout=max_seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if not done:
        return "time_limit"
    finished = done.pop()
    if finished.get_name() == "azure":
        return "upstream_closed"
    exc = finished.exception()
    if exc is None or isinstance(exc, WebSocketDisconnect):
        return "browser_closed"
    return "upstream_closed"


def _log_problem(raw: str) -> None:
    """Record Voice Live errors and failed responses, which otherwise only reach the visitor's browser."""
    if '"error"' not in raw and '"failed"' not in raw and '"incomplete"' not in raw:
        return
    try:
        event = json.loads(raw)
    except ValueError:
        return
    kind = event.get("type")
    if kind == "error":
        log.warning("Voice Live error: %s", json.dumps(event.get("error"))[:2000])
    elif kind == "response.done":
        response = event.get("response") or {}
        if response.get("status") not in ("completed", "cancelled"):
            log.warning("Voice Live response %s: %s", response.get("status"),
                        json.dumps(response.get("status_details"))[:2000])


async def _end(ws: WebSocket, reason: str, message: str) -> None:
    try:
        await ws.send_json({"type": "demo.ended", "reason": reason, "message": message})
        await ws.close()
    except (RuntimeError, WebSocketDisconnect):
        pass

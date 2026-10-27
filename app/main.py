"""FastAPI app hosting both the REST API (/api/...) and the MCP server (/mcp)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .mcp_server import build_mcp
from .security import ApiKeyMiddleware
from .services import InsuranceService, InvalidRequestError, NotFoundError
from .telemetry import configure_telemetry
from .voice import AzureTokenProvider, SessionLimiter, VoiceConfig, build_voice_router

__version__ = "1.1.0"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(service: InsuranceService | None = None, voice_config: VoiceConfig | None = None,
               voice_tokens: AzureTokenProvider | None = None, voice_opener=None) -> FastAPI:
    service = service or InsuranceService()
    voice_config = voice_config or VoiceConfig.from_env()
    mcp = build_mcp(service)
    # Stateless Streamable HTTP: any replica can serve any request, which suits Container Apps scaling.
    mcp_app = mcp.http_app(path="/mcp", stateless_http=True)

    app = FastAPI(
        title="Contoso Insurance Policy & Claims API",
        version=__version__,
        description="Demo insurance API for policy lookup, coverage checks, billing and claims. "
                    "The same capabilities are exposed as MCP tools at `/mcp` for AI agents.",
        lifespan=mcp_app.lifespan,  # required so the MCP session manager starts
    )
    app.state.service = service

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(InvalidRequestError)
    async def _invalid(_: Request, exc: InvalidRequestError):
        return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/info", tags=["service"])
    def info():
        return {"name": app.title, "version": __version__, "rest_docs": "/docs", "mcp_endpoint": "/mcp",
                "voice_demo": "/"}

    @app.get("/health", tags=["service"])
    def health():
        return {"status": "ok"}

    app.include_router(router)
    voice_args = {"open_upstream": voice_opener} if voice_opener else {}
    app.include_router(build_voice_router(voice_config, SessionLimiter(voice_config),
                                          voice_tokens or AzureTokenProvider(), **voice_args))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/", mcp_app)  # MCP endpoint lives at /mcp; mounted last so FastAPI routes win
    app.add_middleware(ApiKeyMiddleware, api_key=os.getenv("API_KEY"))
    return app


app = create_app()
configure_telemetry(app)

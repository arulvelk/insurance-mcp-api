"""FastAPI app hosting both the REST API (/api/...) and the MCP server (/mcp)."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router
from .mcp_server import build_mcp
from .security import ApiKeyMiddleware
from .services import InsuranceService, InvalidRequestError, NotFoundError
from .telemetry import configure_telemetry

__version__ = "1.0.0"


def create_app(service: InsuranceService | None = None) -> FastAPI:
    service = service or InsuranceService()
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
    def root():
        return {"name": app.title, "version": __version__, "rest_docs": "/docs", "mcp_endpoint": "/mcp"}

    @app.get("/health", tags=["service"])
    def health():
        return {"status": "ok"}

    app.include_router(router)
    app.mount("/", mcp_app)  # MCP endpoint lives at /mcp; mounted last so FastAPI routes win
    app.add_middleware(ApiKeyMiddleware, api_key=os.getenv("API_KEY"))
    return app


app = create_app()
configure_telemetry(app)

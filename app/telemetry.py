"""Optional Azure Monitor (Application Insights) telemetry.

Enabled only when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, so local runs and tests
send nothing. Requests, dependencies and logs are collected automatically; each MCP tool call
also gets its own span so Foundry tool usage is visible in the transaction view.
"""

from __future__ import annotations

import os

from fastmcp.server.middleware import Middleware, MiddlewareContext
from opentelemetry import trace

_tracer = trace.get_tracer("contoso-insurance.mcp")


def configure_telemetry(app) -> bool:
    """Send the app's telemetry to Application Insights if a connection string is configured."""
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    os.environ.setdefault("OTEL_SERVICE_NAME", "insurance-mcp-api")
    configure_azure_monitor()
    # `app` was built from the FastAPI class imported before instrumentation, so attach explicitly.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    return True


class ToolTracingMiddleware(Middleware):
    """One span per MCP tool call, marked as failed when the tool returns an error result."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        with _tracer.start_as_current_span(f"mcp.tool {name}") as span:
            span.set_attribute("mcp.tool.name", name)
            result = await call_next(context)
            payload = getattr(result, "structured_content", None) or {}
            if isinstance(payload, dict) and "error" in payload:
                span.set_attribute("mcp.tool.error", str(payload["error"]))
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(payload.get("summary", ""))))
            return result

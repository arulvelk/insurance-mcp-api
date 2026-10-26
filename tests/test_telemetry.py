"""Telemetry is off without a connection string; MCP tool calls produce one span each."""

import asyncio

from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app
from app.mcp_server import build_mcp
from app.telemetry import configure_telemetry

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)


def test_disabled_without_connection_string(service, monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert configure_telemetry(create_app(service)) is False


def test_tool_calls_are_traced(service):
    exporter.clear()

    async def run():
        async with Client(build_mcp(service)) as c:
            await c.call_tool("get_billing_summary", {"policy_number": "AUT-100512"})
            await c.call_tool("get_policy_details", {"policy_number": "AUT-000000"})
    asyncio.run(run())

    spans = {s.name: s for s in exporter.get_finished_spans()}
    ok, failed = spans["mcp.tool get_billing_summary"], spans["mcp.tool get_policy_details"]
    assert ok.attributes["mcp.tool.name"] == "get_billing_summary"
    assert ok.status.status_code != trace.StatusCode.ERROR
    assert failed.attributes["mcp.tool.error"] == "NotFoundError"
    assert failed.status.status_code == trace.StatusCode.ERROR

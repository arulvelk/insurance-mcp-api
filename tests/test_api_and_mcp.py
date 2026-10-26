"""Integration tests for the HTTP surface (REST + MCP). Requires the packages in requirements-dev.txt."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client

from app.main import create_app
from app.mcp_server import build_mcp

EXPECTED_TOOLS = {
    "find_customer", "verify_customer_identity", "list_customer_policies", "get_policy_details",
    "check_coverage", "get_billing_summary", "list_claims", "get_claim_status", "file_claim",
    "add_claim_note", "request_callback",
}


@pytest.fixture
def client(service):
    with TestClient(create_app(service)) as c:
        yield c


def test_health_and_docs(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200


def test_rest_policy_and_errors(client):
    assert client.get("/api/policies/AUT-100234").json()["status"] == "active"
    assert client.get("/api/policies/AUT-999999").status_code == 404
    r = client.post("/api/claims", json={"policy_number": "AUT-100234", "incident_type": "collision",
                                         "incident_date": "2026-12-31", "description": "future"})
    assert r.status_code == 422


def test_rest_create_claim(client):
    r = client.post("/api/claims", json={"policy_number": "HOM-200871", "incident_type": "hail",
                                         "incident_date": "2026-10-20", "description": "Hail broke two windows"})
    assert r.status_code == 201 and r.json()["filed"] is True


def test_api_key(service, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-123")
    with TestClient(create_app(service)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/policies/AUT-100234").status_code == 401
        assert c.get("/api/policies/AUT-100234", headers={"x-api-key": "secret-123"}).status_code == 200


def _text(result) -> dict:
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


def test_mcp_tools_in_memory(service):
    async def run():
        async with Client(build_mcp(service)) as c:
            tools = {t.name for t in await c.list_tools()}
            assert EXPECTED_TOOLS <= tools
            r = _text(await c.call_tool("check_coverage", {"policy_number": "HOM-200871", "incident_type": "hail on roof"}))
            assert r["coverage"] == "wind_hail"
            r = _text(await c.call_tool("get_policy_details", {"policy_number": "AUT-000000"}))
            assert r["error"] == "NotFoundError"
    asyncio.run(run())


def test_mcp_over_http(client):
    """Initialize an MCP session over Streamable HTTP at /mcp, the way Foundry does."""
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    r = client.post("/mcp", json=init, headers=headers)
    assert r.status_code == 200, r.text
    assert "contoso-insurance" in r.text
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
    assert r.status_code == 200 and "file_claim" in r.text

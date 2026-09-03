"""'Connect your own MCP client' page (Phase 10 commit 3): GET /connect.

No network. Exercises the rendered snippets, the public-URL setting and that the
page sits behind commit 1's rate-limit middleware like every other route.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from ni_assembly_mcp.http_app import build_http_app
from ni_assembly_mcp.settings import Settings


def _client(**over) -> TestClient:
    cfg = Settings(
        http_rate_limit_per_minute=over.pop("per_ip", 1000),
        http_global_rate_limit_per_minute=over.pop("global_per_minute", 1_000_000),
        **over,
    )
    return TestClient(build_http_app(host="127.0.0.1", config=cfg))


def test_connect_page_shows_the_default_mcp_url():
    with _client() as client:
        r = client.get("/connect")
    assert r.status_code == 200
    assert "http://localhost:8000/mcp/" in r.text


def test_connect_page_carries_both_config_snippets():
    with _client() as client:
        body = client.get("/connect").text
    # native `url` form and the mcp-remote bridge form both present
    assert "mcpServers" in body
    assert "mcp-remote" in body
    assert "npx" in body


def test_connect_url_follows_the_public_url_setting(monkeypatch):
    monkeypatch.setattr(
        "ni_assembly_mcp.forms.views.settings",
        Settings(http_public_url="https://ni-assembly.example/"),
    )
    with _client() as client:
        body = client.get("/connect").text
    assert "https://ni-assembly.example/mcp/" in body
    assert "localhost" not in body


def test_index_links_to_the_connect_page():
    with _client() as client:
        assert "/connect" in client.get("/").text


def test_connect_sits_behind_the_rate_limiter():
    with _client(per_ip=3) as client:
        statuses = [client.get("/connect").status_code for _ in range(6)]
    assert 429 in statuses
    assert statuses[-1] == 429

"""Rate limiter + circuit breaker (Phase 10 commit 1).

The limiter is a pure unit (clock injected); the middleware and
``build_http_app`` wiring are exercised with Starlette's TestClient — no network.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ni_assembly_mcp.http_app import build_http_app
from ni_assembly_mcp.ratelimit import (
    ALLOW,
    OVERLOADED,
    RATE_LIMITED,
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
)
from ni_assembly_mcp.settings import Settings

# --- RateLimiter unit -------------------------------------------------------


def _cfg(**over) -> RateLimitConfig:
    base = {"per_ip_per_minute": 3, "global_per_minute": 1000, "global_cooldown_seconds": 30}
    base.update(over)
    return RateLimitConfig(**base)


def test_allows_up_to_the_per_ip_limit_then_429():
    limiter = RateLimiter(_cfg())
    assert [limiter.check("1.1.1.1", 0.0)[0] for _ in range(3)] == [ALLOW, ALLOW, ALLOW]
    outcome, retry_after = limiter.check("1.1.1.1", 0.0)
    assert outcome == RATE_LIMITED
    assert 1 <= retry_after <= 60


def test_per_ip_window_resets_after_60s():
    limiter = RateLimiter(_cfg())
    for _ in range(4):
        limiter.check("1.1.1.1", 0.0)
    assert limiter.check("1.1.1.1", 59.0)[0] == RATE_LIMITED
    assert limiter.check("1.1.1.1", 61.0)[0] == ALLOW


def test_ips_are_independent():
    limiter = RateLimiter(_cfg())
    for _ in range(4):
        limiter.check("1.1.1.1", 0.0)
    assert limiter.check("2.2.2.2", 0.0)[0] == ALLOW


def test_global_breaker_trips_for_every_ip_then_recovers_after_cooldown():
    limiter = RateLimiter(_cfg(per_ip_per_minute=1000, global_per_minute=3, global_cooldown_seconds=30))
    for ip in ("a", "b", "c"):
        assert limiter.check(ip, 0.0)[0] == ALLOW
    outcome, retry_after = limiter.check("d", 0.0)
    assert outcome == OVERLOADED and retry_after == 30
    # A different, previously-unseen IP is still refused while tripped.
    assert limiter.check("e", 5.0) == (OVERLOADED, 25)
    # After the cooldown, traffic flows again.
    assert limiter.check("f", 30.0)[0] == ALLOW


def test_disabled_config_flag_is_honoured_by_middleware():
    app = RateLimitMiddleware(_echo_app(), config=_cfg(enabled=False, per_ip_per_minute=1))
    with TestClient(app) as client:
        assert all(client.get("/").status_code == 200 for _ in range(5))


# --- middleware -------------------------------------------------------------


def _echo_app() -> Starlette:
    return Starlette(routes=[Route("/", lambda _r: PlainTextResponse("ok"))])


def test_middleware_returns_429_with_retry_after_header():
    app = RateLimitMiddleware(_echo_app(), config=_cfg(per_ip_per_minute=2))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 200
        blocked = client.get("/")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_healthz_is_never_rate_limited():
    routes = [Route("/healthz", lambda _r: PlainTextResponse("ok")), Route("/", lambda _r: PlainTextResponse("ok"))]
    app = RateLimitMiddleware(Starlette(routes=routes), config=_cfg(per_ip_per_minute=1))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 429  # non-exempt path is limited
        assert all(client.get("/healthz").status_code == 200 for _ in range(10))


# --- build_http_app wiring -------------------------------------------------


@pytest.fixture
def http_settings() -> Settings:
    return Settings(http_rate_limit_per_minute=3, http_global_rate_limit_per_minute=1000)


def test_build_http_app_serves_healthz(http_settings):
    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        r = client.get("/healthz")
        body = r.json()
        assert r.status_code == 200
        assert body["status"] == "ok"
        # No index built in this tmp-dir test settings -> reported absent, not an error.
        assert body["index_present"] is False
        assert body["index_hansard_stale"] is None


def test_rate_limit_sits_in_front_of_the_mcp_transport(http_settings):
    """A burst to /mcp is throttled -> the limiter wraps the MCP app, not just forms."""
    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        statuses = [client.get("/mcp").status_code for _ in range(5)]
    # First few reach the MCP transport (which rejects a plain GET); once the
    # per-IP window is spent every further request is a 429 from the middleware.
    assert 429 in statuses
    assert statuses[-1] == 429

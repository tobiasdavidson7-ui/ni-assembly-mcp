"""Rate limiter + circuit breaker (Phase 10 commit 1).

The limiter is a pure unit (clock injected); the middleware and
``build_http_app`` wiring are exercised with Starlette's TestClient — no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ni_assembly_mcp.http_app import build_http_app
from ni_assembly_mcp.index_db import open_index, set_state, upsert_contributions
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
    routes = [
        Route("/healthz", lambda _r: PlainTextResponse("ok")),
        Route("/healthz/index", lambda _r: PlainTextResponse("ok")),
        Route("/", lambda _r: PlainTextResponse("ok")),
    ]
    app = RateLimitMiddleware(Starlette(routes=routes), config=_cfg(per_ip_per_minute=1))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 429  # non-exempt path is limited
        assert all(client.get("/healthz").status_code == 200 for _ in range(10))
        assert all(client.get("/healthz/index").status_code == 200 for _ in range(10))


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


# --- /healthz/index: the plain "expect 200" staleness probe -----------------
#
# A separate path from /healthz on purpose (see http_app.py) -- some free
# uptime monitors make keyword/body assertions hard to find in their UI, so
# this one is watchable with nothing more than the simplest "is this URL up"
# monitor type every provider supports.


def test_healthz_index_ok_when_no_index_built(http_settings):
    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        r = client.get("/healthz/index")
        assert r.status_code == 200
        assert r.json()["index_present"] is False


def _stub_index(test_settings, *, last_refresh: str) -> None:
    """A minimally "built" index (index_health treats an empty contribution
    table the same as no index at all) with a given hansard_last_refresh."""
    conn = open_index(test_settings.index_db_path)
    upsert_contributions(
        conn,
        [
            {
                "speech_id": "s1", "debate_date": "2026-01-01", "major_heading": "Assembly Business",
                "minor_heading": None, "person_id": None, "twfy_person_id": None, "speakername": "X",
                "speech_time": None, "url": None, "body": "stub",
            }
        ],
    )
    set_state(conn, "hansard_last_refresh", last_refresh)
    conn.commit()
    conn.close()


def test_healthz_index_ok_when_fresh(http_settings, test_settings):
    _stub_index(test_settings, last_refresh=datetime.now(tz=UTC).isoformat())

    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        r = client.get("/healthz/index")
        assert r.status_code == 200
        assert r.json()["index_hansard_stale"] is False


def test_healthz_index_503_when_stale(http_settings, test_settings):
    _stub_index(test_settings, last_refresh=(datetime.now(tz=UTC) - timedelta(days=5)).isoformat())

    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        r = client.get("/healthz/index")
        assert r.status_code == 503
        assert r.json()["index_hansard_stale"] is True


def test_rate_limit_sits_in_front_of_the_mcp_transport(http_settings):
    """A burst to /mcp is throttled -> the limiter wraps the MCP app, not just forms."""
    with TestClient(build_http_app(host="127.0.0.1", config=http_settings)) as client:
        statuses = [client.get("/mcp").status_code for _ in range(5)]
    # First few reach the MCP transport (which rejects a plain GET); once the
    # per-IP window is spent every further request is a 429 from the middleware.
    assert 429 in statuses
    assert statuses[-1] == 429

"""Assemble the public HTTP application and serve it.

``ni-assembly-mcp serve --http`` (Phase 8) used to call
``MCPServer.run(transport="streamable-http")``, which builds a Starlette app and
runs uvicorn in one opaque step. Phase 10 needs to (a) mount extra routes — the
forms UI and the "connect your own LLM" page — on the *same* app as ``/mcp`` and
(b) wrap the whole thing in the rate-limit middleware so it is unavoidably in
front of both. So we build the app ourselves here:

    build_server()  ->  register custom routes  ->  streamable_http_app()
                    ->  RateLimitMiddleware(...)  ->  uvicorn

Commit 1 registers only ``/healthz``; the forms and connect routes are added in
the following commits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

from ni_assembly_mcp.ratelimit import RateLimitConfig, RateLimitMiddleware
from ni_assembly_mcp.settings import Settings, settings

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _register_routes(server: MCPServer) -> None:
    """Attach the non-MCP HTTP routes. Must run before ``streamable_http_app()``.

    Call once per server instance (``custom_route`` appends unconditionally).
    """
    server.custom_route("/healthz", methods=["GET"], include_in_schema=False)(_healthz)


def build_http_app(
    server: MCPServer | None = None,
    *,
    host: str = "127.0.0.1",
    config: Settings | None = None,
) -> ASGIApp:
    """Return the combined ASGI app: ``/mcp`` + custom routes, rate-limited.

    ``host`` is forwarded to ``streamable_http_app`` (it drives the SDK's
    localhost DNS-rebinding guard — auto-on for 127.0.0.1/localhost, off when
    bound to 0.0.0.0 for hosting).
    """
    from ni_assembly_mcp.server import build_server

    cfg = config or settings
    server = server or build_server()
    _register_routes(server)
    app: ASGIApp = server.streamable_http_app(host=host)
    if cfg.http_rate_limit_enabled:
        app = RateLimitMiddleware(app, config=RateLimitConfig.from_settings(cfg))
    return app


def serve_http(*, host: str, port: int, log_level: str = "info", config: Settings | None = None) -> None:
    """Build the app and run it under uvicorn (blocking)."""
    import uvicorn

    cfg = config or settings
    app = build_http_app(host=host, config=cfg)
    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        # Trust the platform's proxy for the real client IP only when told to —
        # otherwise X-Forwarded-For is attacker-controlled and defeats the per-IP limit.
        proxy_headers=cfg.http_trust_proxy_headers,
        forwarded_allow_ips=cfg.http_forwarded_allow_ips,
    )
    logger.info("serving HTTP on %s:%s (proxy_headers=%s)", host, port, cfg.http_trust_proxy_headers)
    uvicorn.Server(uv_config).run()

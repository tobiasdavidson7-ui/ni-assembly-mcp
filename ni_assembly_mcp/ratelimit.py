"""Per-IP rate limiting + a global circuit breaker for the public HTTP surface.

Phase 10 exposes the server publicly (forms UI + the raw streamable-HTTP MCP
transport). Even LLM-free tool calls cost CPU, upstream API calls and disk cache,
so both endpoints sit behind one ASGI middleware:

* **per-IP fixed-window limit** — ``http_rate_limit_per_minute`` requests per
  client IP per rolling 60 s window; over that → ``429`` + ``Retry-After``.
* **global circuit breaker** — if *total* traffic exceeds
  ``http_global_rate_limit_per_minute`` in a window the breaker trips and every
  request gets ``503`` + ``Retry-After`` for ``http_global_cooldown_seconds``.
  This is the protection against a script/bot hammering the host from many IPs,
  which the per-IP limit alone does not cover.

State is in-process (dict + counters, no lock — every mutation runs synchronously
between ``await`` points on the single event loop). That is fine for the
zero-cost single-container target; horizontal scaling would need a shared store
(Redis etc.) and is out of scope.

:func:`~ni_assembly_mcp.http_app.build_http_app` wraps the *combined* Starlette
app (``/mcp`` + the Phase 10 routes) in :class:`RateLimitMiddleware`, so the
limiter is unavoidably in front of both.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from ni_assembly_mcp.settings import Settings

# Paths that must never be rate limited (uptime checks hit these on a timer).
_EXEMPT_PATHS = frozenset({"/healthz", "/healthz/index"})

# Sweep stale per-IP windows once the table grows past this many entries.
_PRUNE_ABOVE = 10_000

_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateLimitConfig:
    """Tuning for :class:`RateLimiter`, all env-overridable via :class:`Settings`."""

    enabled: bool = True
    per_ip_per_minute: int = 120
    global_per_minute: int = 1200
    global_cooldown_seconds: int = 30

    @classmethod
    def from_settings(cls, settings: Settings) -> RateLimitConfig:
        return cls(
            enabled=settings.http_rate_limit_enabled,
            per_ip_per_minute=settings.http_rate_limit_per_minute,
            global_per_minute=settings.http_global_rate_limit_per_minute,
            global_cooldown_seconds=settings.http_global_cooldown_seconds,
        )


class _Window:
    __slots__ = ("count", "start")

    def __init__(self, now: float) -> None:
        self.count = 0
        self.start = now


# check() outcomes.
ALLOW = "allow"
RATE_LIMITED = "rate_limited"  # -> 429, one IP over its share
OVERLOADED = "overloaded"  # -> 503, global breaker tripped


class RateLimiter:
    """Fixed-window per-IP counter + a global breaker. Pure, clock injected.

    :meth:`check` is synchronous and non-blocking; call it once per HTTP request.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._ip_windows: dict[str, _Window] = {}
        self._global = _Window(0.0)
        self._tripped_until = 0.0

    def check(self, ip: str, now: float) -> tuple[str, int]:
        """Return ``(outcome, retry_after_seconds)`` for a request from ``ip``.

        ``retry_after_seconds`` is 0 when the outcome is :data:`ALLOW`.
        """
        # 1. Circuit breaker — while tripped, nothing else matters.
        if now < self._tripped_until:
            return OVERLOADED, math.ceil(self._tripped_until - now)

        # 2. Global window.
        g = self._global
        if now - g.start >= _WINDOW_SECONDS:
            g.count = 0
            g.start = now
        g.count += 1
        if g.count > self._config.global_per_minute:
            cooldown = self._config.global_cooldown_seconds
            self._tripped_until = now + cooldown
            # Count fresh once the cooldown elapses, so recovery is cooldown-driven
            # and predictable rather than waiting out the rest of the 60s window.
            g.count = 0
            g.start = now + cooldown
            return OVERLOADED, cooldown

        # 3. Per-IP window.
        self._maybe_prune(now)
        w = self._ip_windows.get(ip)
        if w is None or now - w.start >= _WINDOW_SECONDS:
            w = _Window(now)
            self._ip_windows[ip] = w
        w.count += 1
        if w.count > self._config.per_ip_per_minute:
            return RATE_LIMITED, max(1, math.ceil(_WINDOW_SECONDS - (now - w.start)))

        return ALLOW, 0

    def _maybe_prune(self, now: float) -> None:
        if len(self._ip_windows) <= _PRUNE_ABOVE:
            return
        stale = [ip for ip, w in self._ip_windows.items() if now - w.start >= _WINDOW_SECONDS]
        for ip in stale:
            del self._ip_windows[ip]


class RateLimitMiddleware:
    """ASGI middleware applying a shared :class:`RateLimiter` to every HTTP request.

    Non-HTTP scopes (``lifespan``, ``websocket``) pass straight through, so the
    wrapped app's startup/shutdown still runs. Client IP is read from
    ``scope["client"]`` — put ``uvicorn --proxy-headers`` (Settings
    ``http_trust_proxy_headers``) in front when deployed behind a load balancer
    so that is the real client, not the proxy.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: RateLimitConfig | None = None,
        clock: object = None,
    ) -> None:
        self.app = app
        self._config = config or RateLimitConfig()
        self._limiter = RateLimiter(self._config)
        self._clock = clock or time.monotonic

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._config.enabled:
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "-"
        outcome, retry_after = self._limiter.check(ip, self._clock())

        if outcome == ALLOW:
            await self.app(scope, receive, send)
            return

        if outcome == OVERLOADED:
            body = "Server is temporarily overloaded. Please retry shortly.\n"
            status = 503
        else:
            body = "Rate limit exceeded. Slow down and retry shortly.\n"
            status = 429
        response = PlainTextResponse(body, status_code=status, headers={"Retry-After": str(retry_after)})
        await response(scope, receive, send)

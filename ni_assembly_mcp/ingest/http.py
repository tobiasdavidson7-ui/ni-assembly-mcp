"""HTTP for the offline indexer — politeness + a circuit breaker.

Built on the shared :func:`~ni_assembly_mcp.http_client.cached_limited_get` (disk
cache + rate limiter + 5xx retry) but under a gentler :class:`Settings` (lower
rate / concurrency — PLAN.md §6.5 pt 4). A run aborts after
``index_circuit_break_failures`` consecutive failures so a dead host isn't
hammered; the ingest checkpoint means the next run resumes.
"""

from __future__ import annotations

import logging

import httpx

from ni_assembly_mcp.http_client import cached_limited_get
from ni_assembly_mcp.settings import Settings

logger = logging.getLogger(__name__)


class IngestAborted(RuntimeError):
    """Raised when the fetcher's circuit breaker trips."""


def indexer_config(base: Settings) -> Settings:
    """A copy of ``base`` with the HTTP knobs swapped for the slower indexer ones."""
    return base.model_copy(
        update={
            "http_max_rate_per_second": base.index_http_max_rate_per_second,
            "http_max_concurrency": base.index_http_max_concurrency,
        }
    )


class PoliteFetcher:
    """``await fetcher.get_bytes(url)`` — cached, rate-limited, retried, circuit-broken."""

    def __init__(self, config: Settings) -> None:
        self._config = indexer_config(config)
        self._break_after = config.index_circuit_break_failures
        self._consecutive_failures = 0

    async def get_bytes(self, url: str) -> bytes:
        if self._consecutive_failures >= self._break_after:
            msg = f"aborting ingest after {self._consecutive_failures} consecutive failures (last url: {url})"
            raise IngestAborted(msg)
        try:
            response = await cached_limited_get(url, config=self._config)
            response.raise_for_status()
        except (httpx.HTTPError, IngestAborted):
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return response.content

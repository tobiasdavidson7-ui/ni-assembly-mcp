"""Generic cached, rate-limited, retrying async GET.

Transport-agnostic — adapted from parliament-mcp's ``cached_limited_get``
(qdrant_data_loaders.py) with three changes called for in PLAN.md:

* absolute on-disk cache path (2a / 6.5 pt 3) so the cache survives a restart
  from a different working directory;
* an ``asyncio.Semaphore`` concurrency cap on top of the rate limiter (5e);
* explicit exponential backoff on HTTP 5xx / timeout — ``httpx``'s transport
  ``retries=`` only covers connection errors, not HTTP status (5e).

Pinned to ``hishel<0.2``: the 1.x line replaced ``AsyncCacheClient`` /
``AsyncFileStorage`` with ``AsyncCacheProxy`` and new storage classes. Migrating
to hishel 1.x is a separate task.
"""

from __future__ import annotations

import asyncio
import logging

import hishel
import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ni_assembly_mcp.settings import Settings, settings

logger = logging.getLogger(__name__)


def _is_server_error(response: httpx.Response) -> bool:
    return response.status_code >= 500


_limiter: AsyncLimiter | None = None
_semaphore: asyncio.Semaphore | None = None
_client: hishel.AsyncCacheClient | None = None
_config_key: tuple | None = None


def _ensure(config: Settings) -> tuple[AsyncLimiter, asyncio.Semaphore, hishel.AsyncCacheClient]:
    """Lazily build (and memoise) the limiter / semaphore / client for ``config``."""
    global _limiter, _semaphore, _client, _config_key

    key = (
        config.base_url,
        config.http_max_rate_per_second,
        config.http_max_concurrency,
        config.http_timeout_seconds,
        config.http_max_retries,
        str(config.hishel_cache_dir),
        config.hishel_ttl.total_seconds(),
        config.user_agent,
    )
    if _config_key == key and _limiter and _semaphore and _client:
        return _limiter, _semaphore, _client

    config.hishel_cache_dir.mkdir(parents=True, exist_ok=True)
    _limiter = AsyncLimiter(max_rate=config.http_max_rate_per_second, time_period=1.0)
    _semaphore = asyncio.Semaphore(config.http_max_concurrency)
    _client = hishel.AsyncCacheClient(
        timeout=config.http_timeout_seconds,
        headers={"User-Agent": config.user_agent, "Accept": "application/json"},
        storage=hishel.AsyncFileStorage(
            base_path=str(config.hishel_cache_dir),
            ttl=config.hishel_ttl.total_seconds(),
        ),
        transport=httpx.AsyncHTTPTransport(retries=config.http_max_retries),
        follow_redirects=True,
    )
    _config_key = key
    return _limiter, _semaphore, _client


async def reset_http_client() -> None:
    """Drop the memoised client (tests, or after a settings change)."""
    global _client, _config_key
    if _client is not None:
        await _client.aclose()
    _client = None
    _config_key = None


async def cached_limited_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    config: Settings | None = None,
) -> httpx.Response:
    """GET ``url`` through the shared cache, rate limiter, concurrency cap and retry policy."""
    config = config or settings
    limiter, semaphore, client = _ensure(config)

    @retry(
        retry=(
            retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)) | retry_if_result(_is_server_error)
        ),
        wait=wait_exponential_jitter(initial=config.http_retry_initial_wait, max=config.http_retry_max_wait),
        stop=stop_after_attempt(config.http_max_retries + 1),
        # On exhaustion: return the last response (5xx) so the caller converts it to
        # NIAssemblyAPIError; a transport error re-raises here via outcome.result().
        retry_error_callback=lambda retry_state: retry_state.outcome.result(),
    )
    async def _do_get() -> httpx.Response:
        async with semaphore, limiter:
            response = await client.get(url, params=params, headers=headers)
        if _is_server_error(response):
            logger.warning("GET %s -> HTTP %s", url, response.status_code)
        return response

    return await _do_get()

"""Shared helpers for the MCP tool layer."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_R = TypeVar("_R")


async def gather_sections(sections: dict[str, Awaitable]) -> dict[str, Any]:
    """Run named awaitables concurrently, returning only the ones that succeed.

    Kept from parliament-mcp (``mcp_server/utils.py``). A failed section is logged
    and dropped rather than sinking the whole result, so one flaky upstream
    endpoint can't take out an entire composite tool call.
    """
    results = await asyncio.gather(*sections.values(), return_exceptions=True)
    output: dict[str, Any] = {}
    for name, result in zip(sections, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Section %r failed: %s", name, result)
        else:
            output[name] = result
    return output


def log_tool_call(func: Callable[..., Awaitable[_R]]) -> Callable[..., Awaitable[_R]]:
    """Log an MCP tool call with its arguments and wall-clock duration.

    Kept from parliament-mcp (``mcp_server/utils.py``); ``functools.wraps``
    preserves the signature so MCP schema introspection is unaffected.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> _R:
        call_args = {k: v for k, v in kwargs.items() if v is not None}
        logger.info("Tool %s called with %s", func.__name__, json.dumps(call_args, default=str))
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
        except Exception:
            logger.exception("Tool %s failed after %.3fs", func.__name__, time.perf_counter() - start)
            raise
        logger.info("Tool %s completed in %.3fs", func.__name__, time.perf_counter() - start)
        return result

    return wrapper

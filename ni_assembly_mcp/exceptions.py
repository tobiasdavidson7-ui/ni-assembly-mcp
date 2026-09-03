"""Exception types for the NI Assembly client."""

from __future__ import annotations


class IndexNotBuiltError(RuntimeError):
    """Raised when a Hansard/questions FTS5 index is opened for reading but has
    not been built yet (missing file, or the core tables are empty).

    The MCP server never builds the index in a request path (PLAN.md §6.5 pt 2):
    the offline ``ni-assembly-mcp index`` job does. Tools catch this and return a
    short "run the index command" message rather than hanging.
    """


class NIAssemblyAPIError(RuntimeError):
    """Raised when data.niassembly.gov.uk returns something other than usable JSON.

    The NI API / IIS can return HTTP 200 with an XML or HTML error body, or a 500
    HTML page, so ``response.raise_for_status()`` alone is not enough (PLAN.md 2b).
    """

    def __init__(
        self, message: str, *, url: str | None = None, status_code: int | None = None, body: str | None = None
    ):
        self.url = url
        self.status_code = status_code
        self.body = body
        detail = message
        if status_code is not None:
            detail = f"{detail} (HTTP {status_code})"
        if url is not None:
            detail = f"{detail} [{url}]"
        super().__init__(detail)

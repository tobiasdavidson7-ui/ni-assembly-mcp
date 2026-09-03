"""Hansard tools (PLAN.md Phase 6b — the two ex-6a tools).

The NI data API has no Hansard search, so ``search_debate_titles`` reads the
local SQLite FTS5 index (built offline by ``ni-assembly-mcp index hansard`` from
TheyWorkForYou's bulk XML — PLAN.md §6.6). ``get_hansard_reports`` is a thin
wrapper over the one Hansard list endpoint and needs no index.

Phase 6c adds ``search_contributions`` / ``find_relevant_contributors`` on the
same index behind :class:`~ni_assembly_mcp.index_query.HansardSearchBackend`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.exceptions import IndexNotBuiltError
from ni_assembly_mcp.index_query import Fts5Backend
from ni_assembly_mcp.models import HansardReport, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.settings import settings
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 183  # ~6 months (PLAN.md §6.2)
_NOT_BUILT_MSG = (
    "The Hansard index has not been built. Run `ni-assembly-mcp index hansard --full` "
    "once (then periodic `ni-assembly-mcp index hansard` refreshes)."
)


def _iso_date(value: str | None) -> str:
    return (value or "")[:10]


def _default_window(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    today = datetime.now(tz=UTC).date()
    start = date_from or (today - timedelta(days=_DEFAULT_WINDOW_DAYS)).isoformat()
    return start, (date_to or today.isoformat())


@log_tool_call
async def get_hansard_reports(
    date_from: Annotated[str | None, Field(description="Only sittings on/after this date (YYYY-MM-DD).")] = None,
    date_to: Annotated[str | None, Field(description="Only sittings on/before this date (YYYY-MM-DD).")] = None,
    max_results: Annotated[int, Field(description="Maximum reports to return.", ge=1)] = 50,
) -> list[dict] | str:
    """List Assembly Official Report (Hansard) sitting days, newest first.

    Each row has ``report_doc_id``, ``plenary_date`` and the session name. There
    are no debate titles here — use ``search_debate_titles`` for those. The NI
    API returns the whole list (~2012 onward); ``date_from`` / ``date_to`` filter
    it client-side.
    """
    records = coerce_records(HansardReport, await niassembly_get("hansard", "GetAllHansardReports"))
    if date_from:
        records = [r for r in records if _iso_date(r.get("plenary_date")) >= date_from]
    if date_to:
        records = [r for r in records if _iso_date(r.get("plenary_date")) <= date_to]
    if not records:
        return "No Hansard reports found for the given dates."
    records.sort(key=lambda r: r.get("plenary_date") or "", reverse=True)
    return records[:max_results]


@log_tool_call
async def search_debate_titles(
    query: Annotated[
        str, Field(description="Keyword(s) to match in debate/section headings. Substring/stemmed, not semantic.")
    ],
    date_from: Annotated[
        str | None, Field(description="Start of the date window (YYYY-MM-DD). Default: ~6 months ago.")
    ] = None,
    date_to: Annotated[
        str | None, Field(description="End of the date window (YYYY-MM-DD). Default: today.")
    ] = None,
    max_results: Annotated[int, Field(description="Maximum headings to return.", ge=1)] = 25,
) -> list[dict] | str:
    """Search Assembly debate and section headings within a date range.

    Reads the local Hansard index. Keyword match on the headings only (Porter
    stemmed, so "funding" matches "funded"); **not** semantic — synonyms and
    paraphrases are missed. Results are distinct
    ``(debate_date, major_heading, minor_heading)`` triples, newest first.

    A date range is required conceptually; if omitted it defaults to roughly the
    last six months. If the index has not been built yet this returns a short
    instruction instead of results.
    """
    backend = Fts5Backend(settings)
    window_from, window_to = _default_window(date_from, date_to)
    try:
        rows = backend.debate_titles(
            query.strip() or None, date_from=window_from, date_to=window_to, limit=max_results
        )
    except IndexNotBuiltError:
        return _NOT_BUILT_MSG
    if not rows:
        return f"No debate headings matched {query!r} between {window_from} and {window_to}."
    return rows

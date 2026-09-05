"""Hansard tools.

The NI data API has no Hansard search, so everything except ``get_hansard_reports``
reads the local SQLite FTS5 index (built offline by ``ni-assembly-mcp index
hansard`` from TheyWorkForYou's bulk XML — PLAN.md §6.6). ``get_hansard_reports``
is a thin wrapper over the one Hansard list endpoint and needs no index.

- Phase 6b: ``get_hansard_reports``, ``search_debate_titles``.
- Phase 6c: ``search_contributions``, ``find_relevant_contributors`` — both pure
  consumers of the index behind
  :class:`~ni_assembly_mcp.index_query.HansardSearchBackend` (PLAN.md §6.3 / §6.4).
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


@log_tool_call
async def search_contributions(
    query: Annotated[
        str | None,
        Field(description="Keyword(s) to match in the spoken text. Stemmed, BM25-ranked; not semantic."),
    ] = None,
    member_id: Annotated[
        int | None, Field(description="Only contributions by this member (NI PersonId).")
    ] = None,
    date_from: Annotated[str | None, Field(description="On/after this date (YYYY-MM-DD).")] = None,
    date_to: Annotated[str | None, Field(description="On/before this date (YYYY-MM-DD).")] = None,
    max_results: Annotated[int, Field(description="Maximum contributions to return.", ge=1)] = 50,
) -> list[dict] | str:
    """Full-text search over what members actually said in the Chamber (1998 to present).

    Reads the local Hansard index. With ``query`` set, results are Porter-stemmed
    ("funding" matches "funded") and BM25-ranked, each with a ``snippet`` around
    the match and a ``relevance_score``. With no ``query``, returns the most
    recent contributions matching the filters. Still lexical, **not** semantic —
    pure-synonym queries ("school funding" vs "education budget") will miss.

    The ``member_id`` filter is reliable for debates from ~2022 onward; for
    earlier debates many speakers are matched by name only and carry no id, so a
    ``member_id`` filter under-returns there.

    If the index has not been built this returns a short instruction instead of
    results.
    """
    backend = Fts5Backend(settings)
    try:
        rows = backend.contributions(
            (query or "").strip() or None,
            member_id=member_id,
            date_from=date_from,
            date_to=date_to,
            limit=max_results,
        )
    except IndexNotBuiltError:
        return _NOT_BUILT_MSG
    if not rows:
        return "No contributions matched the given query and filters."
    return rows


@log_tool_call
async def get_contribution(
    speech_id: Annotated[str, Field(description="speech_id of the contribution (from search_contributions).")],
) -> dict | str:
    """Fetch one contribution's full spoken text by ``speech_id``.

    Use this to see the whole thing after ``search_contributions`` or
    ``find_relevant_contributors`` gave you only a snippet. Returns the same
    fields as those tools plus ``body`` (the full text, untruncated).
    """
    backend = Fts5Backend(settings)
    try:
        row = backend.contribution(speech_id)
    except IndexNotBuiltError:
        return _NOT_BUILT_MSG
    if row is None:
        return f"No contribution found with speech_id {speech_id!r}."
    return row


@log_tool_call
async def find_relevant_contributors(
    query: Annotated[
        str, Field(description="Topic keyword(s). Required. Stemmed, BM25-weighted; not semantic.")
    ],
    num_contributors: Annotated[
        int, Field(description="Maximum members to return.", ge=1)
    ] = 10,
    num_contributions: Annotated[
        int, Field(description="Maximum example contributions per member.", ge=1)
    ] = 10,
    date_from: Annotated[str | None, Field(description="On/after this date (YYYY-MM-DD).")] = None,
    date_to: Annotated[str | None, Field(description="On/before this date (YYYY-MM-DD).")] = None,
) -> list[dict] | str:
    """Members ranked by how much they spoke on the query terms (1998 to present).

    Reads the local Hansard index. Each member's score is the BM25-weighted sum
    of their matching contributions (so both relevance and volume count);
    ``contribution_count`` and per-snippet ``relevance_score`` are returned so the
    ranking is inspectable. Lexical, **not** semantic — biased toward members who
    used the exact terms.

    Per-member attribution is complete for ~2022 onward. For earlier periods,
    members whose identity could not be resolved are grouped under their spoken
    name (``person_id`` null) or, if unnamed, omitted — so historical rankings
    undercount.

    If the index has not been built this returns a short instruction instead of
    results.
    """
    if not query.strip():
        return "A query is required for find_relevant_contributors."
    backend = Fts5Backend(settings)
    try:
        groups = backend.relevant_contributors(
            query.strip(),
            date_from=date_from,
            date_to=date_to,
            num_contributors=num_contributors,
            num_contributions=num_contributions,
        )
    except IndexNotBuiltError:
        return _NOT_BUILT_MSG
    if not groups:
        return f"No contributors matched {query!r} in the given period."
    return groups

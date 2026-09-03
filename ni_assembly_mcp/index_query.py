"""Read side of the FTS5 index — status, heading search, and the search-backend
protocol.

Kept separate from :mod:`ni_assembly_mcp.index_db` (schema / writes) so the MCP
tool layer imports only what it queries. Phase 6b ships one query —
:func:`distinct_headings`, behind :class:`HansardSearchBackend` — for
``search_debate_titles``. Phase 6c grows the protocol with contribution search
and contributor ranking (PLAN.md §6.3 / §6.4).
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from ni_assembly_mcp.index_db import open_index
from ni_assembly_mcp.settings import Settings, settings

# Match against the two heading columns only (not ``body``).
_HEADING_COLUMNS = "{major_heading minor_heading}"


def build_match_query(text: str, *, columns: str | None = None) -> str | None:
    """Turn free text into a conservative FTS5 MATCH expression.

    Each whitespace token becomes a quoted phrase (so FTS5 operators/punctuation
    in user input are inert) AND-ed together, optionally column-filtered. Returns
    ``None`` for empty input — callers treat that as "no text filter".
    """
    tokens = [tok for tok in text.split() if tok] if text else []
    if not tokens:
        return None
    phrase = " ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)
    return f"{columns} : {phrase}" if columns else phrase


def index_status(conn: sqlite3.Connection) -> dict:
    """Counts and freshness markers for the index (feeds ``index status`` / tools)."""
    (contributions,) = conn.execute("SELECT count(*) FROM contribution").fetchone()
    (questions,) = conn.execute("SELECT count(*) FROM question").fetchone()
    date_lo, date_hi = conn.execute(
        "SELECT min(debate_date), max(debate_date) FROM contribution"
    ).fetchone()
    state = dict(conn.execute("SELECT key, value FROM ingest_state").fetchall())
    return {
        "contributions": contributions,
        "questions": questions,
        "debate_date_min": date_lo,
        "debate_date_max": date_hi,
        "hansard_last_refresh": state.get("hansard_last_refresh"),
        "hansard_cursor": state.get("hansard_cursor"),
    }


def distinct_headings(
    conn: sqlite3.Connection,
    query: str | None,
    *,
    date_from: str,
    date_to: str,
    limit: int = 50,
) -> list[dict]:
    """Distinct ``(debate_date, major_heading, minor_heading)`` triples in
    ``[date_from, date_to]`` whose headings match ``query`` (stemmed), newest
    first. Empty ``query`` → the headings in the window by date.

    ``date_from`` / ``date_to`` are ``YYYY-MM-DD``; ``debate_date`` is stored in
    that form so string comparison is a date comparison.
    """
    match = build_match_query(query or "", columns=_HEADING_COLUMNS)
    params: list = [date_from, date_to]
    if match is None:
        sql = (
            "SELECT DISTINCT debate_date, major_heading, minor_heading "
            "FROM contribution WHERE debate_date >= ? AND debate_date <= ? "
            "ORDER BY debate_date DESC, major_heading, minor_heading LIMIT ?"
        )
    else:
        sql = (
            "SELECT DISTINCT c.debate_date, c.major_heading, c.minor_heading "
            "FROM contribution_fts f JOIN contribution c ON c.rowid = f.rowid "
            "WHERE f.contribution_fts MATCH ? AND c.debate_date >= ? AND c.debate_date <= ? "
            "ORDER BY c.debate_date DESC, c.major_heading, c.minor_heading LIMIT ?"
        )
        params = [match, date_from, date_to]
    params.append(limit)
    return [
        {"debate_date": r["debate_date"], "major_heading": r["major_heading"], "minor_heading": r["minor_heading"]}
        for r in conn.execute(sql, params).fetchall()
    ]


class HansardSearchBackend(Protocol):
    """The swappable Hansard-search surface (PLAN.md §6.5 pt 1).

    Phase 6b: one method. Phase 6c adds ``search_contributions`` and
    ``find_relevant_contributors``. The live-walk backend was dropped with
    Phase 6a; only :class:`Fts5Backend` implements this.
    """

    def debate_titles(
        self, query: str | None, *, date_from: str, date_to: str, limit: int
    ) -> list[dict]: ...


class Fts5Backend:
    """:class:`HansardSearchBackend` backed by the local SQLite FTS5 index.

    Opens the index read-only per call; raises
    :class:`~ni_assembly_mcp.exceptions.IndexNotBuiltError` (from
    :func:`~ni_assembly_mcp.index_db.open_index`) when it is missing/empty.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or settings

    def debate_titles(
        self, query: str | None, *, date_from: str, date_to: str, limit: int
    ) -> list[dict]:
        conn = open_index(self._config.index_db_path, read_only=True)
        try:
            return distinct_headings(conn, query, date_from=date_from, date_to=date_to, limit=limit)
        finally:
            conn.close()

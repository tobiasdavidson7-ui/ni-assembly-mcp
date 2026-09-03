"""Read side of the FTS5 index — status, heading search, and the search-backend
protocol.

Kept separate from :mod:`ni_assembly_mcp.index_db` (schema / writes) so the MCP
tool layer imports only what it queries. Phase 6b shipped one query —
:func:`distinct_headings`, behind :class:`HansardSearchBackend` — for
``search_debate_titles``. Phase 6c adds :func:`search_contributions` (BM25 full-
text over spoken bodies) and :func:`rank_contributors` (PLAN.md §6.3 / §6.4).
Phase 9 adds :func:`search_questions` (BM25 over question **and answer** text).

**Contributor scoring (§6.4) is a v1 heuristic.** ``rank_contributors`` scores a
member as ``sum(w_i)`` where ``w_i = -bm25(hit_i)`` -- BM25-weighted volume,
*linear* in hit count. This deliberately drops the extra ``* hit_count``
multiplier in the PLAN text (which makes the score scale like ``count ** 2`` and
lets many weak mentions swamp a strong one). ``contribution_count`` and per-hit
``relevance_score`` are returned alongside so the ranking stays legible. Revisit
with ``mean(w) * log(1 + count)`` if results skew toward prolific speakers.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from ni_assembly_mcp.index_db import open_index
from ni_assembly_mcp.settings import Settings, settings

# Match against the two heading columns only (not ``body``).
_HEADING_COLUMNS = "{major_heading minor_heading}"

# ``contribution_fts`` column order: 0 major_heading, 1 minor_heading, 2 body.
_BODY_FTS_COL = 2
_SNIPPET_TOKENS = 24  # FTS5 snippet() window (max 64)

# BM25 column weights (major_heading, minor_heading, body) for contribution
# search. Equal for v1 — a hit in the spoken body and a hit in the debate heading
# count the same. Revisit if heading-only matches prove noisy.
_CONTRIB_BM25_WEIGHTS = (1.0, 1.0, 1.0)
_BM25_ARGS = ", ".join(str(w) for w in _CONTRIB_BM25_WEIGHTS)

# Upper bound on how many top-ranked hits feed ``rank_contributors``' group-by.
# A broad term ("health") matches many thousands of speeches; ranking off the
# most-relevant slice keeps memory bounded and the result BM25-led.
_CONTRIBUTOR_SCAN_CAP = 4000

# ``contribution`` columns returned by :func:`search_contributions`.
_CONTRIB_COLUMNS = (
    "speech_id",
    "debate_date",
    "major_heading",
    "minor_heading",
    "person_id",
    "speakername",
    "speech_time",
    "url",
)
_NOQUERY_SNIPPET_CHARS = 400  # leading-text fallback when there is no MATCH

# ``question`` columns returned by :func:`search_questions` (answer_text is
# excluded — it can be long; the ``snippet`` covers it and get_question_details
# has the full text). ``question_fts`` column order: 0 question_text, 1 answer_text.
_QUESTION_COLUMNS = (
    "document_id",
    "reference",
    "document_type",
    "tabled_date",
    "answered_on_date",
    "question_text",
    "tabler_person_id",
    "department_name",
)
# -1 = snippet from the leftmost column that has a phrase match (question_text or
# answer_text), so a question-only hit still yields a snippet.
_QUESTION_SNIPPET_COL = -1
_QUESTION_ORAL_TYPE = "Question for Oral Answer"
_QUESTION_WRITTEN_TYPE = "Question for Written Answer"


def _date_clause(date_from: str | None, date_to: str | None, params: list, *, col: str = "c.debate_date") -> str:
    sql = ""
    if date_from:
        sql += f" AND {col} >= ?"
        params.append(date_from)
    if date_to:
        sql += f" AND {col} <= ?"
        params.append(date_to)
    return sql


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
    q_lo, q_hi = conn.execute("SELECT min(tabled_date), max(tabled_date) FROM question").fetchone()
    state = dict(conn.execute("SELECT key, value FROM ingest_state").fetchall())
    return {
        "contributions": contributions,
        "questions": questions,
        "debate_date_min": date_lo,
        "debate_date_max": date_hi,
        "question_tabled_min": q_lo,
        "question_tabled_max": q_hi,
        "hansard_last_refresh": state.get("hansard_last_refresh"),
        "hansard_cursor": state.get("hansard_cursor"),
        "questions_last_refresh": state.get("questions_last_refresh"),
        "questions_cursor": state.get("questions_cursor"),
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


def _contribution_row(r: sqlite3.Row, *, scored: bool) -> dict:
    row = {col: r[col] for col in _CONTRIB_COLUMNS}
    row["snippet"] = (r["snippet"] or "").strip()
    row["relevance_score"] = round(-r["rank"], 4) if scored else None
    return row


def search_contributions(
    conn: sqlite3.Connection,
    query: str | None,
    *,
    member_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Full-text search over spoken contributions (PLAN.md §6.3).

    With ``query`` set: BM25-ranked (Porter-stemmed) match over the headings and
    the spoken ``body``, best first, each row carrying a ``snippet`` around the
    hit and a ``relevance_score`` (``-bm25``). With no ``query``: the rows
    matching the filters, newest first, ``snippet`` = leading text,
    ``relevance_score`` = ``None``.

    ``member_id`` filters on ``contribution.person_id`` (reliably populated only
    from ~2022 on — older speeches keep ``speakername`` but no id, so a
    ``member_id`` filter under-returns for historical debates). ``date_from`` /
    ``date_to`` are ``YYYY-MM-DD``.
    """
    match = build_match_query(query or "")
    select_cols = ", ".join(f"c.{col}" for col in _CONTRIB_COLUMNS)

    if match is None:
        params: list = []
        sql = (
            f"SELECT {select_cols}, "
            f"substr(c.body, 1, {_NOQUERY_SNIPPET_CHARS}) AS snippet, 0 AS rank "
            "FROM contribution c WHERE 1=1"
        )
        sql += _date_clause(date_from, date_to, params)
        if member_id is not None:
            sql += " AND c.person_id = ?"
            params.append(member_id)
        sql += " ORDER BY c.debate_date DESC, c.speech_time DESC LIMIT ?"
        params.append(limit)
        return [_contribution_row(r, scored=False) for r in conn.execute(sql, params).fetchall()]

    params = [match]
    sql = (
        f"SELECT {select_cols}, "
        f"snippet(contribution_fts, {_BODY_FTS_COL}, '', '', '…', {_SNIPPET_TOKENS}) AS snippet, "
        f"bm25(contribution_fts, {_BM25_ARGS}) AS rank "
        "FROM contribution_fts f JOIN contribution c ON c.rowid = f.rowid "
        "WHERE f.contribution_fts MATCH ?"
    )
    sql += _date_clause(date_from, date_to, params)
    if member_id is not None:
        sql += " AND c.person_id = ?"
        params.append(member_id)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return [_contribution_row(r, scored=True) for r in conn.execute(sql, params).fetchall()]


def rank_contributors(
    conn: sqlite3.Connection,
    query: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    num_contributors: int = 10,
    num_contributions: int = 10,
) -> list[dict]:
    """Rank members by BM25-weighted volume on ``query`` (PLAN.md §6.4).

    FTS5 match → group hits by ``person_id`` (falling back to a casefolded
    ``speakername`` when the speaker is unmapped) → ``score = Σ(-bm25(hit))`` →
    top ``num_contributors``, each with its ``num_contributions`` strongest
    snippets. See the module docstring on why the score is linear (not ``count²``)
    in hit count. Empty ``query`` → ``[]`` (the tool requires one).

    Per-member attribution is complete only from ~2022 on; earlier speakers that
    ``people.json`` did not map are grouped under their spoken name, so historical
    rankings undercount.
    """
    match = build_match_query(query or "")
    if match is None:
        return []

    params: list = [match]
    sql = (
        "SELECT c.speech_id, c.debate_date, c.major_heading, c.minor_heading, "
        "c.person_id, c.speakername, c.url, "
        f"snippet(contribution_fts, {_BODY_FTS_COL}, '', '', '…', {_SNIPPET_TOKENS}) AS snippet, "
        f"bm25(contribution_fts, {_BM25_ARGS}) AS rank "
        "FROM contribution_fts f JOIN contribution c ON c.rowid = f.rowid "
        "WHERE f.contribution_fts MATCH ?"
    )
    sql += _date_clause(date_from, date_to, params)
    sql += " ORDER BY rank LIMIT ?"
    params.append(_CONTRIBUTOR_SCAN_CAP)

    groups: dict = {}
    for r in conn.execute(sql, params).fetchall():
        weight = -r["rank"]
        pid = r["person_id"]
        key = ("id", pid) if pid is not None else ("name", (r["speakername"] or "").casefold())
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "person_id": pid,
                "speakername": r["speakername"],
                "contribution_count": 0,
                "score": 0.0,
                "hits": [],
            }
        group["contribution_count"] += 1
        group["score"] += weight
        group["hits"].append(
            {
                "speech_id": r["speech_id"],
                "debate_date": r["debate_date"],
                "major_heading": r["major_heading"],
                "minor_heading": r["minor_heading"],
                "url": r["url"],
                "snippet": (r["snippet"] or "").strip(),
                "relevance_score": round(weight, 4),
            }
        )

    ranked = sorted(groups.values(), key=lambda g: g["score"], reverse=True)[:num_contributors]
    return [
        {
            "person_id": g["person_id"],
            "speakername": g["speakername"],
            "contribution_count": g["contribution_count"],
            "score": round(g["score"], 4),
            "contributions": sorted(g["hits"], key=lambda h: h["relevance_score"], reverse=True)[:num_contributions],
        }
        for g in ranked
    ]


def _question_row(r: sqlite3.Row, *, scored: bool) -> dict:
    row = {col: r[col] for col in _QUESTION_COLUMNS}
    row["snippet"] = (r["snippet"] or "").strip()
    row["relevance_score"] = round(-r["rank"], 4) if scored else None
    return row


def search_questions(
    conn: sqlite3.Connection,
    query: str | None,
    *,
    member_id: int | None = None,
    department: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    oral: bool | None = None,
    answered_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Full-text search over parliamentary questions (PLAN.md Phase 9 / §6.1).

    With ``query`` set: BM25-ranked (Porter-stemmed) match over **question text
    and answer text** — the live ``GetQuestionsBySearchText`` endpoint searches
    the question text only. With no ``query``: rows matching the filters,
    newest-tabled first, ``relevance_score = None``.

    All filters are plain SQL and — unlike the live tool — combine freely and
    apply across the whole 2007→present corpus (``tabler_person_id`` is the NI
    PersonId in every source endpoint): ``member_id`` → tabler, ``department`` →
    substring on the answering department, ``date_from`` / ``date_to`` → tabled
    date (``YYYY-MM-DD``), ``oral`` → written/oral split, ``answered_only`` →
    has an answer date. ``answer_text`` is not returned (it can be long — the
    ``snippet`` covers it, ``get_question_details`` has the full text).
    """
    match = build_match_query(query or "")
    select_cols = ", ".join(f"q.{c}" for c in _QUESTION_COLUMNS)

    fparams: list = []
    filters = _date_clause(date_from, date_to, fparams, col="q.tabled_date")
    if member_id is not None:
        filters += " AND q.tabler_person_id = ?"
        fparams.append(member_id)
    if department:
        filters += " AND q.department_name LIKE '%' || ? || '%'"
        fparams.append(department)
    if oral is True:
        filters += " AND q.document_type = ?"
        fparams.append(_QUESTION_ORAL_TYPE)
    elif oral is False:
        filters += " AND q.document_type = ?"
        fparams.append(_QUESTION_WRITTEN_TYPE)
    if answered_only:
        filters += " AND q.answered_on_date IS NOT NULL"

    if match is None:
        sql = (
            f"SELECT {select_cols}, "
            f"substr(q.answer_text, 1, {_NOQUERY_SNIPPET_CHARS}) AS snippet, 0 AS rank "
            f"FROM question q WHERE 1=1{filters} ORDER BY q.tabled_date DESC LIMIT ?"
        )
        return [_question_row(r, scored=False) for r in conn.execute(sql, [*fparams, limit]).fetchall()]

    sql = (
        f"SELECT {select_cols}, "
        f"snippet(question_fts, {_QUESTION_SNIPPET_COL}, '', '', '…', {_SNIPPET_TOKENS}) AS snippet, "
        "bm25(question_fts, 1.0, 1.0) AS rank "
        "FROM question_fts f JOIN question q ON q.rowid = f.rowid "
        f"WHERE f.question_fts MATCH ?{filters} ORDER BY rank LIMIT ?"
    )
    return [_question_row(r, scored=True) for r in conn.execute(sql, [match, *fparams, limit]).fetchall()]


class HansardSearchBackend(Protocol):
    """The swappable Hansard-search surface (PLAN.md §6.5 pt 1).

    Phase 6b defined ``debate_titles``; Phase 6c adds ``contributions`` and
    ``relevant_contributors``. The live-walk backend was dropped with Phase 6a;
    only :class:`Fts5Backend` implements this.
    """

    def debate_titles(
        self, query: str | None, *, date_from: str, date_to: str, limit: int
    ) -> list[dict]: ...

    def contributions(
        self,
        query: str | None,
        *,
        member_id: int | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
    ) -> list[dict]: ...

    def relevant_contributors(
        self,
        query: str,
        *,
        date_from: str | None,
        date_to: str | None,
        num_contributors: int,
        num_contributions: int,
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

    def contributions(
        self,
        query: str | None,
        *,
        member_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conn = open_index(self._config.index_db_path, read_only=True)
        try:
            return search_contributions(
                conn, query, member_id=member_id, date_from=date_from, date_to=date_to, limit=limit
            )
        finally:
            conn.close()

    def relevant_contributors(
        self,
        query: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        num_contributors: int = 10,
        num_contributions: int = 10,
    ) -> list[dict]:
        conn = open_index(self._config.index_db_path, read_only=True)
        try:
            return rank_contributors(
                conn,
                query,
                date_from=date_from,
                date_to=date_to,
                num_contributors=num_contributors,
                num_contributions=num_contributions,
            )
        finally:
            conn.close()

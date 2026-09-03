"""SQLite FTS5 index — schema, connection helper and write helpers.

The NI Assembly data API has **no Hansard search** (PLAN.md §6 / §6.5 pt 1), so
Hansard tools are served from a local SQLite FTS5 index built offline by
``ni-assembly-mcp index`` (Phase 6b). This module owns *what the index looks
like* and the low-level read/write primitives; :mod:`ni_assembly_mcp.index_query`
owns the query surface, and ``ni_assembly_mcp.ingest`` (Phase 6b commit 2) fills
it.

Design notes:

* ``contribution`` is one row per ``<speech>`` from TheyWorkForYou's bulk XML,
  plus a handful of ``niapi:``-prefixed rows from the NI data API freshness
  top-up (PLAN.md §6.6). ``speech_id`` is the natural key from both sources.
* ``contribution_fts`` / ``question_fts`` are external-content FTS5 tables kept in
  sync by triggers (Porter stemming + BM25).
* ``question`` / ``question_fts`` exist in the schema but nothing writes them
  yet — the PQ index-backed path is a later phase.
* No data ships in the repo (CC BY-SA 2.5 ShareAlike on the TWFY identifier data
  — builder only; PLAN.md §5d).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from ni_assembly_mcp.exceptions import IndexNotBuiltError

SCHEMA = """
CREATE TABLE IF NOT EXISTS contribution (
    speech_id       TEXT PRIMARY KEY,
    debate_date     TEXT NOT NULL,
    major_heading   TEXT,
    minor_heading   TEXT,
    person_id       INTEGER,
    twfy_person_id  TEXT,
    speakername     TEXT,
    speech_time     TEXT,
    url             TEXT,
    body            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_contribution_date ON contribution(debate_date);
CREATE INDEX IF NOT EXISTS ix_contribution_person ON contribution(person_id);

CREATE VIRTUAL TABLE IF NOT EXISTS contribution_fts USING fts5(
    major_heading, minor_heading, body,
    content='contribution', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS contribution_ai AFTER INSERT ON contribution BEGIN
    INSERT INTO contribution_fts(rowid, major_heading, minor_heading, body)
    VALUES (new.rowid, new.major_heading, new.minor_heading, new.body);
END;
CREATE TRIGGER IF NOT EXISTS contribution_ad AFTER DELETE ON contribution BEGIN
    INSERT INTO contribution_fts(contribution_fts, rowid, major_heading, minor_heading, body)
    VALUES ('delete', old.rowid, old.major_heading, old.minor_heading, old.body);
END;
CREATE TRIGGER IF NOT EXISTS contribution_au AFTER UPDATE ON contribution BEGIN
    INSERT INTO contribution_fts(contribution_fts, rowid, major_heading, minor_heading, body)
    VALUES ('delete', old.rowid, old.major_heading, old.minor_heading, old.body);
    INSERT INTO contribution_fts(rowid, major_heading, minor_heading, body)
    VALUES (new.rowid, new.major_heading, new.minor_heading, new.body);
END;

CREATE TABLE IF NOT EXISTS question (
    document_id       INTEGER PRIMARY KEY,
    reference         TEXT,
    tabled_date       TEXT,
    answered_on_date  TEXT,
    question_text     TEXT,
    answer_text       TEXT,
    tabler_person_id  INTEGER,
    department_name   TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS question_fts USING fts5(
    question_text, answer_text,
    content='question', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS question_ai AFTER INSERT ON question BEGIN
    INSERT INTO question_fts(rowid, question_text, answer_text)
    VALUES (new.rowid, new.question_text, new.answer_text);
END;
CREATE TRIGGER IF NOT EXISTS question_ad AFTER DELETE ON question BEGIN
    INSERT INTO question_fts(question_fts, rowid, question_text, answer_text)
    VALUES ('delete', old.rowid, old.question_text, old.answer_text);
END;
CREATE TRIGGER IF NOT EXISTS question_au AFTER UPDATE ON question BEGIN
    INSERT INTO question_fts(question_fts, rowid, question_text, answer_text)
    VALUES ('delete', old.rowid, old.question_text, old.answer_text);
    INSERT INTO question_fts(rowid, question_text, answer_text)
    VALUES (new.rowid, new.question_text, new.answer_text);
END;

CREATE TABLE IF NOT EXISTS ingest_state (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""

# Columns of ``contribution`` in the order accepted by :func:`upsert_contributions`.
_CONTRIBUTION_COLUMNS = (
    "speech_id",
    "debate_date",
    "major_heading",
    "minor_heading",
    "person_id",
    "twfy_person_id",
    "speakername",
    "speech_time",
    "url",
    "body",
)


def open_index(path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open (and, in write mode, create) the FTS5 index at ``path``.

    ``read_only=True`` opens with ``mode=ro`` and raises
    :class:`~ni_assembly_mcp.exceptions.IndexNotBuiltError` if the index file is
    missing or the ``contribution`` table has no rows — the caller (an MCP tool)
    turns that into a "run the index command" message.
    """
    path = Path(path)

    if read_only:
        if not path.exists():
            msg = f"No index at {path}"
            raise IndexNotBuiltError(msg)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            (count,) = conn.execute("SELECT count(*) FROM contribution").fetchone()
        except sqlite3.OperationalError as exc:
            conn.close()
            msg = f"Index at {path} is missing its tables — rebuild it"
            raise IndexNotBuiltError(msg) from exc
        if not count:
            conn.close()
            msg = f"Index at {path} has no contributions — run: ni-assembly-mcp index hansard"
            raise IndexNotBuiltError(msg)
        return conn

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_contributions(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Insert-or-replace ``contribution`` rows by ``speech_id``. Returns the count.

    Missing keys default to ``None`` (``body`` and ``debate_date`` must be
    present — they are ``NOT NULL``). The FTS mirror is maintained by triggers.
    """
    placeholders = ", ".join("?" for _ in _CONTRIBUTION_COLUMNS)
    updates = ", ".join(f"{col}=excluded.{col}" for col in _CONTRIBUTION_COLUMNS if col != "speech_id")
    sql = (
        f"INSERT INTO contribution ({', '.join(_CONTRIBUTION_COLUMNS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(speech_id) DO UPDATE SET {updates}"
    )
    payload = [tuple(row.get(col) for col in _CONTRIBUTION_COLUMNS) for row in rows]
    if not payload:
        return 0
    conn.executemany(sql, payload)
    return len(payload)


def delete_contributions_for_date(conn: sqlite3.Connection, debate_date: str, *, prefix: str | None = None) -> int:
    """Delete rows for ``debate_date``; with ``prefix`` only those whose
    ``speech_id`` starts with it (used to evict ``niapi:`` top-up stand-ins when
    TheyWorkForYou catches up — PLAN.md dedup rules 1 & 3)."""
    if prefix is None:
        cur = conn.execute("DELETE FROM contribution WHERE debate_date = ?", (debate_date,))
    else:
        cur = conn.execute(
            "DELETE FROM contribution WHERE debate_date = ? AND speech_id LIKE ? || '%'",
            (debate_date, prefix),
        )
    return cur.rowcount


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM ingest_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO ingest_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )

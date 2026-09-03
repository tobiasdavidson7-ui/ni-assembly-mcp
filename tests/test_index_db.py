from __future__ import annotations

import sqlite3

import pytest

from ni_assembly_mcp.exceptions import IndexNotBuiltError
from ni_assembly_mcp.index_db import (
    delete_contributions_for_date,
    get_state,
    open_index,
    set_state,
    upsert_contributions,
    upsert_questions,
)


def _row(speech_id: str, date: str, **over) -> dict:
    base = {
        "speech_id": speech_id,
        "debate_date": date,
        "major_heading": "Executive Committee Business",
        "minor_heading": "Arts Funding",
        "person_id": 90,
        "twfy_person_id": "uk.org.publicwhip/person/13853",
        "speakername": "Edwin Poots",
        "speech_time": "10:30",
        "url": None,
        "body": "The Minister has funded the arts programme.",
    }
    base.update(over)
    return base


@pytest.fixture
def conn(tmp_path):
    c = open_index(tmp_path / "index.db")
    yield c
    c.close()


def _fts_count(conn: sqlite3.Connection, match: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM contribution_fts WHERE contribution_fts MATCH ?", (match,)
    ).fetchone()[0]


def test_open_index_creates_schema(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"contribution", "question", "ingest_state"} <= tables


def test_upsert_inserts_and_syncs_fts(conn):
    assert upsert_contributions(conn, [_row("s1", "2026-06-30")]) == 1
    assert _fts_count(conn, "funded") == 1  # porter stemming: funded -> fund


def test_upsert_is_idempotent_on_speech_id(conn):
    upsert_contributions(conn, [_row("s1", "2026-06-30", body="first")])
    upsert_contributions(conn, [_row("s1", "2026-06-30", body="second version")])
    (count,) = conn.execute("SELECT count(*) FROM contribution").fetchone()
    assert count == 1
    assert _fts_count(conn, "second") == 1
    assert _fts_count(conn, "first") == 0  # update trigger removed the stale term


def test_delete_keeps_fts_in_sync(conn):
    upsert_contributions(conn, [_row("s1", "2026-06-30")])
    conn.execute("DELETE FROM contribution WHERE speech_id = 's1'")
    assert _fts_count(conn, "funded") == 0


def test_delete_contributions_for_date_prefix_only(conn):
    upsert_contributions(
        conn,
        [
            _row("uk.org.publicwhip/ni/2026-07-10.1.1", "2026-07-10"),
            _row("niapi:99", "2026-07-10"),
        ],
    )
    assert delete_contributions_for_date(conn, "2026-07-10", prefix="niapi:") == 1
    remaining = {r[0] for r in conn.execute("SELECT speech_id FROM contribution")}
    assert remaining == {"uk.org.publicwhip/ni/2026-07-10.1.1"}


def test_question_fts_triggers_fire(conn):
    conn.execute(
        "INSERT INTO question (document_id, question_text, answer_text) VALUES (1, 'school funding', ?)",
        ("budget allocated",),
    )
    (hits,) = conn.execute("SELECT count(*) FROM question_fts WHERE question_fts MATCH 'budget'").fetchone()
    assert hits == 1


def _q(document_id: int, **over) -> dict:
    base = {
        "document_id": document_id,
        "reference": "AQW 1/22-27",
        "document_type": "Question for Written Answer",
        "tabled_date": "2025-05-01",
        "answered_on_date": None,
        "question_text": "To ask about school funding in the Western Trust area.",
        "answer_text": None,
        "tabler_person_id": 5793,
        "department_name": "Department of Education",
    }
    base.update(over)
    return base


def test_upsert_questions_inserts_and_syncs_fts(conn):
    assert upsert_questions(conn, [_q(1, answer_text="the budget was allocated in full")]) == 1
    hits = conn.execute("SELECT count(*) FROM question_fts WHERE question_fts MATCH 'allocated'").fetchone()[0]
    assert hits == 1


def test_upsert_questions_skips_rows_without_document_id(conn):
    assert upsert_questions(conn, [_q(None), _q(2)]) == 1
    assert conn.execute("SELECT count(*) FROM question").fetchone()[0] == 1


def test_upsert_questions_answer_wins_tabled_then_answered(conn):
    # TabledInRange first (no answer), AnsweredInRange later (with answer).
    upsert_questions(conn, [_q(10, answered_on_date=None, answer_text=None)])
    upsert_questions(conn, [_q(10, answered_on_date="2025-05-20", answer_text="Funding rose by 4%.")])
    row = conn.execute("SELECT answer_text, answered_on_date FROM question WHERE document_id = 10").fetchone()
    assert row["answer_text"] == "Funding rose by 4%."
    assert row["answered_on_date"] == "2025-05-20"


def test_upsert_questions_answer_wins_answered_then_tabled(conn):
    # Same DocumentId seen the other way round (adjacent-window re-scan): the
    # answer-less TabledInRange row must NOT clobber the stored answer.
    upsert_questions(conn, [_q(11, answered_on_date="2025-05-20", answer_text="Funding rose by 4%.")])
    upsert_questions(conn, [_q(11, answered_on_date=None, answer_text=None)])
    row = conn.execute("SELECT answer_text, answered_on_date FROM question WHERE document_id = 11").fetchone()
    assert row["answer_text"] == "Funding rose by 4%."
    assert row["answered_on_date"] == "2025-05-20"
    # and the FTS mirror reflects the retained answer
    hits = conn.execute("SELECT count(*) FROM question_fts WHERE question_fts MATCH 'Funding'").fetchone()[0]
    assert hits == 1


def test_read_only_open_requires_named_table(tmp_path):
    w = open_index(tmp_path / "index.db")
    upsert_contributions(w, [_row("s1", "2026-06-30")])  # hansard rows only
    w.commit()
    w.close()
    # contribution-backed readiness passes; question-backed does not
    open_index(tmp_path / "index.db", read_only=True, require_table="contribution").close()
    with pytest.raises(IndexNotBuiltError, match="no questions"):
        open_index(tmp_path / "index.db", read_only=True, require_table="question")


def test_state_round_trip(conn):
    assert get_state(conn, "hansard_cursor") is None
    set_state(conn, "hansard_cursor", "1782969317")
    set_state(conn, "hansard_cursor", "1782969999")
    assert get_state(conn, "hansard_cursor") == "1782969999"


def test_read_only_open_missing_file(tmp_path):
    with pytest.raises(IndexNotBuiltError):
        open_index(tmp_path / "nope.db", read_only=True)


def test_read_only_open_empty_index(tmp_path):
    open_index(tmp_path / "index.db").close()  # schema only, no rows
    with pytest.raises(IndexNotBuiltError, match="no contributions"):
        open_index(tmp_path / "index.db", read_only=True)


def test_read_only_open_populated_index(tmp_path):
    w = open_index(tmp_path / "index.db")
    upsert_contributions(w, [_row("s1", "2026-06-30")])
    w.commit()
    w.close()
    r = open_index(tmp_path / "index.db", read_only=True)
    try:
        assert r.execute("SELECT count(*) FROM contribution").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            r.execute("INSERT INTO ingest_state VALUES ('k', 'v')")
    finally:
        r.close()

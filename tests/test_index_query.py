from __future__ import annotations

import pytest

from ni_assembly_mcp.exceptions import IndexNotBuiltError
from ni_assembly_mcp.index_db import open_index, set_state, upsert_contributions
from ni_assembly_mcp.index_query import (
    Fts5Backend,
    build_match_query,
    distinct_headings,
    index_status,
)


def _row(speech_id: str, date: str, major: str, minor: str, body: str) -> dict:
    return {
        "speech_id": speech_id, "debate_date": date, "major_heading": major, "minor_heading": minor,
        "person_id": None, "twfy_person_id": None, "speakername": "X", "speech_time": None, "url": None, "body": body,
    }


_ROWS = [
    _row("d1.1", "2026-06-30", "Executive Committee Business", "Arts Funding: North/South Disparity",
         "artists gathered in Dublin"),
    _row("d1.2", "2026-06-30", "Executive Committee Business", "Arts Funding: North/South Disparity",
         "the Minister funded projects"),
    _row("d2.1", "2026-03-04", "Assembly Business", "Health Waiting Lists", "waiting lists are too long"),
]


@pytest.fixture
def conn(tmp_path):
    c = open_index(tmp_path / "index.db")
    upsert_contributions(c, _ROWS)
    c.commit()
    yield c
    c.close()


def test_build_match_query_quotes_tokens():
    assert build_match_query("arts funding") == '"arts" "funding"'
    assert build_match_query("  ") is None
    assert build_match_query('a "b') == '"a" """b"'
    assert build_match_query("x", columns="{major_heading minor_heading}") == '{major_heading minor_heading} : "x"'


def test_distinct_headings_dedupes(conn):
    rows = distinct_headings(conn, "arts", date_from="2026-01-01", date_to="2026-12-31")
    assert rows == [
        {"debate_date": "2026-06-30", "major_heading": "Executive Committee Business",
         "minor_heading": "Arts Funding: North/South Disparity"}
    ]


def test_distinct_headings_matches_headings_not_body(conn):
    assert distinct_headings(conn, "Dublin", date_from="2026-01-01", date_to="2026-12-31") == []


def test_distinct_headings_is_stemmed(conn):
    assert distinct_headings(conn, "fund", date_from="2026-01-01", date_to="2026-12-31")


def test_distinct_headings_date_window(conn):
    rows = distinct_headings(conn, None, date_from="2026-01-01", date_to="2026-04-01")
    assert [r["minor_heading"] for r in rows] == ["Health Waiting Lists"]


def test_distinct_headings_newest_first(conn):
    rows = distinct_headings(conn, None, date_from="2026-01-01", date_to="2026-12-31")
    assert [r["debate_date"] for r in rows] == ["2026-06-30", "2026-03-04"]


def test_index_status(conn):
    set_state(conn, "hansard_last_refresh", "2026-09-03T00:00:00Z")
    st = index_status(conn)
    assert st["contributions"] == 3
    assert st["debate_date_min"] == "2026-03-04"
    assert st["debate_date_max"] == "2026-06-30"
    assert st["hansard_last_refresh"] == "2026-09-03T00:00:00Z"


def test_fts5_backend_reads_configured_path(test_settings):
    w = open_index(test_settings.index_db_path)
    upsert_contributions(w, [_row("s1", "2026-05-01", "Assembly Business", "Budget", "the budget statement")])
    w.commit()
    w.close()
    backend = Fts5Backend(test_settings)
    rows = backend.debate_titles("budget", date_from="2026-01-01", date_to="2026-12-31", limit=10)
    assert rows == [{"debate_date": "2026-05-01", "major_heading": "Assembly Business", "minor_heading": "Budget"}]


def test_fts5_backend_raises_when_not_built(test_settings):
    with pytest.raises(IndexNotBuiltError):
        Fts5Backend(test_settings).debate_titles("x", date_from="2026-01-01", date_to="2026-12-31", limit=10)

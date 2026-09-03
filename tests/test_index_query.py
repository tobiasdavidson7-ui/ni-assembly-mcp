from __future__ import annotations

import pytest

from ni_assembly_mcp.exceptions import IndexNotBuiltError
from ni_assembly_mcp.index_db import open_index, set_state, upsert_contributions
from ni_assembly_mcp.index_query import (
    Fts5Backend,
    build_match_query,
    distinct_headings,
    index_status,
    rank_contributors,
    search_contributions,
)


def _row(
    speech_id: str,
    date: str,
    major: str,
    minor: str,
    body: str,
    *,
    person_id: int | None = None,
    speakername: str = "X",
    speech_time: str | None = None,
) -> dict:
    return {
        "speech_id": speech_id, "debate_date": date, "major_heading": major, "minor_heading": minor,
        "person_id": person_id, "twfy_person_id": None, "speakername": speakername,
        "speech_time": speech_time, "url": None, "body": body,
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


# --- search_contributions --------------------------------------------------

_CONTRIB_ROWS = [
    _row("c1", "2026-06-30", "Executive Committee Business", "Climate Action Plan",
         "we must cut emissions and invest in renewable energy across every sector",
         person_id=90, speakername="Edwin Poots", speech_time="10:30"),
    _row("c2", "2026-06-30", "Executive Committee Business", "Climate Action Plan",
         "the climate crisis demands a serious emissions target from this Executive",
         person_id=80, speakername="Conor Murphy", speech_time="10:45"),
    _row("c3", "2026-02-10", "Assembly Business", "Health Waiting Lists",
         "waiting lists for elective care remain unacceptably long", person_id=80,
         speakername="Conor Murphy", speech_time="14:00"),
    _row("c4", "2020-05-12", "Committee Business", "Environment",
         "a brief remark on climate", speakername="Historic Member", speech_time="09:00"),
]


@pytest.fixture
def contrib_conn(tmp_path):
    c = open_index(tmp_path / "contrib.db")
    upsert_contributions(c, _CONTRIB_ROWS)
    c.commit()
    yield c
    c.close()


def test_search_contributions_ranks_and_snippets(contrib_conn):
    # tokens are AND-ed: only c2 has both "emissions" and "target"
    rows = search_contributions(contrib_conn, "emissions target")
    assert [r["speech_id"] for r in rows] == ["c2"]
    assert rows[0]["relevance_score"] is not None
    assert "emissions" in rows[0]["snippet"] or "target" in rows[0]["snippet"]


def test_search_contributions_single_token_returns_all_matches(contrib_conn):
    rows = search_contributions(contrib_conn, "emissions")
    assert {r["speech_id"] for r in rows} == {"c1", "c2"}


def test_search_contributions_member_filter(contrib_conn):
    rows = search_contributions(contrib_conn, "climate", member_id=80)
    assert {r["speech_id"] for r in rows} == {"c2"}


def test_search_contributions_date_window(contrib_conn):
    rows = search_contributions(contrib_conn, "climate", date_from="2026-01-01", date_to="2026-12-31")
    assert {r["speech_id"] for r in rows} == {"c1", "c2"}


def test_search_contributions_stemmed(contrib_conn):
    assert search_contributions(contrib_conn, "invest")  # matches "invest in renewable"


def test_search_contributions_no_query_newest_first(contrib_conn):
    rows = search_contributions(contrib_conn, None, member_id=80)
    assert [r["speech_id"] for r in rows] == ["c2", "c3"]
    assert rows[0]["relevance_score"] is None
    assert rows[0]["snippet"]


# --- rank_contributors ---------------------------------------------------------


def test_rank_contributors_groups_by_person(contrib_conn):
    groups = rank_contributors(contrib_conn, "climate emissions")
    by_id = {g["person_id"]: g for g in groups}
    assert set(by_id) >= {80, 90}
    assert by_id[80]["contribution_count"] == 1
    assert by_id[80]["speakername"] == "Conor Murphy"
    assert by_id[80]["contributions"][0]["speech_id"] == "c2"


def test_rank_contributors_name_fallback_when_unmapped(contrib_conn):
    groups = rank_contributors(contrib_conn, "climate")
    unmapped = [g for g in groups if g["person_id"] is None]
    assert unmapped and unmapped[0]["speakername"] == "Historic Member"


def test_rank_contributors_orders_by_weighted_score(contrib_conn):
    groups = rank_contributors(contrib_conn, "climate emissions renewable energy")
    assert groups[0]["score"] >= groups[-1]["score"]
    # c1 has the densest match -> Poots (90) outranks the one-line historic member
    assert groups[0]["person_id"] == 90


def test_rank_contributors_empty_query(contrib_conn):
    assert rank_contributors(contrib_conn, "   ") == []


def test_rank_contributors_limits(contrib_conn):
    groups = rank_contributors(contrib_conn, "climate", num_contributors=1, num_contributions=1)
    assert len(groups) == 1
    assert len(groups[0]["contributions"]) == 1


def test_fts5_backend_contributions_and_contributors(test_settings):
    w = open_index(test_settings.index_db_path)
    upsert_contributions(w, _CONTRIB_ROWS)
    w.commit()
    w.close()
    backend = Fts5Backend(test_settings)
    assert backend.contributions("emissions", member_id=None, date_from=None, date_to=None, limit=5)
    groups = backend.relevant_contributors(
        "climate", date_from=None, date_to=None, num_contributors=5, num_contributions=5
    )
    assert groups


def test_fts5_backend_contributions_raises_when_not_built(test_settings):
    with pytest.raises(IndexNotBuiltError):
        Fts5Backend(test_settings).contributions(
            "x", member_id=None, date_from=None, date_to=None, limit=5
        )

from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.index_db import open_index, upsert_contributions
from ni_assembly_mcp.tools.hansard import get_hansard_reports, search_debate_titles

BASE = "https://data.niassembly.gov.uk"


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


def _reports_route(load_fixture):
    return respx.get(f"{BASE}/hansard.asmx/GetAllHansardReports_JSON").mock(
        return_value=_json(load_fixture("hansard_GetAllHansardReports.json"))
    )


def _seed_index(test_settings):
    conn = open_index(test_settings.index_db_path)
    rows = [
        {
            "speech_id": "d1.1", "debate_date": "2026-06-30", "major_heading": "Executive Committee Business",
            "minor_heading": "Arts Funding: North/South Disparity", "person_id": 90, "twfy_person_id": None,
            "speakername": "Edwin Poots", "speech_time": None, "url": None, "body": "the arts were funded",
        },
        {
            "speech_id": "d2.1", "debate_date": "2026-01-15", "major_heading": "Oral Answers to Questions",
            "minor_heading": "Health Waiting Lists", "person_id": 80, "twfy_person_id": None,
            "speakername": "Conor Murphy", "speech_time": None, "url": None, "body": "waiting lists remain long",
        },
    ]
    upsert_contributions(conn, rows)
    conn.commit()
    conn.close()


# --- get_hansard_reports ------------------------------------------------------


@respx.mock
async def test_get_hansard_reports_newest_first(load_fixture):
    _reports_route(load_fixture)
    rows = await get_hansard_reports()
    assert [r["report_doc_id"] for r in rows] == [492567, 492100, 480000]
    assert rows[0]["plenary_session_name"] == "2025-2026"


@respx.mock
async def test_get_hansard_reports_date_filter(load_fixture):
    _reports_route(load_fixture)
    rows = await get_hansard_reports(date_from="2026-01-01", date_to="2026-06-25")
    assert [r["report_doc_id"] for r in rows] == [492100]


@respx.mock
async def test_get_hansard_reports_empty_after_filter(load_fixture):
    _reports_route(load_fixture)
    result = await get_hansard_reports(date_from="2030-01-01")
    assert isinstance(result, str)


# --- search_debate_titles ----------------------------------------------------


async def test_search_debate_titles_not_built_message(test_settings):
    result = await search_debate_titles("funding")
    assert isinstance(result, str)
    assert "index hansard" in result


async def test_search_debate_titles_hit(test_settings):
    _seed_index(test_settings)
    rows = await search_debate_titles("arts funding", date_from="2026-01-01", date_to="2026-12-31")
    assert rows == [
        {"debate_date": "2026-06-30", "major_heading": "Executive Committee Business",
         "minor_heading": "Arts Funding: North/South Disparity"}
    ]


async def test_search_debate_titles_stemmed(test_settings):
    _seed_index(test_settings)
    # "wait" stems to match "Waiting" in the heading
    rows = await search_debate_titles("waiting", date_from="2026-01-01", date_to="2026-12-31")
    assert [r["minor_heading"] for r in rows] == ["Health Waiting Lists"]


async def test_search_debate_titles_respects_date_window(test_settings):
    _seed_index(test_settings)
    result = await search_debate_titles("waiting", date_from="2026-06-01", date_to="2026-06-30")
    assert isinstance(result, str)  # the health debate is in January -> outside the window


async def test_search_debate_titles_no_match_message(test_settings):
    _seed_index(test_settings)
    result = await search_debate_titles("brexit", date_from="2026-01-01", date_to="2026-12-31")
    assert isinstance(result, str) and "brexit" in result

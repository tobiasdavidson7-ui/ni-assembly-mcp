from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from ni_assembly_mcp.index_db import get_state, open_index, upsert_contributions
from ni_assembly_mcp.ingest.runner import _components_to_rows, _freshness_topup, run_hansard_index

_FIX = Path(__file__).parent / "fixtures" / "twfy"
TWFY = "https://www.theyworkforyou.com/pwdata/scrapedxml/ni"
NIAPI = "https://data.niassembly.gov.uk/hansard.asmx"


def _text(name: str) -> httpx.Response:
    return httpx.Response(200, content=(_FIX / name).read_bytes())


def _fixture_response(name: str) -> httpx.Response:
    return httpx.Response(200, text=(_FIX / name).read_text())


def _json(payload) -> httpx.Response:
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


def _c(cid: str, ctype: str, text: str, **extra) -> dict:
    return {"ComponentId": cid, "ComponentType": ctype, "ComponentText": text, **extra}


@pytest.fixture
def conn(tmp_path):
    c = open_index(tmp_path / "index.db")
    yield c
    c.close()


def _mock_twfy_bulk(*, reports=None):
    respx.get("https://raw.githubusercontent.com/mysociety/parlparse/master/members/people.json").mock(
        return_value=_text("people.json")
    )
    respx.get(f"{TWFY}/").mock(return_value=_fixture_response("index.html"))
    respx.get(f"{TWFY}/changedates.txt").mock(return_value=_fixture_response("changedates.txt"))
    for name in ("ni1999-01-01.xml", "ni2098-01-01.xml", "ni2099-01-01.xml"):
        respx.get(f"{TWFY}/{name}").mock(return_value=_text(name))
    respx.get(f"{NIAPI}/GetAllHansardReports_JSON").mock(
        return_value=_json({"AllHansardReports": {"HansardReport": reports or []}})
    )


@respx.mock
async def test_full_run_ingests_all_files(conn, test_settings):
    _mock_twfy_bulk()
    result = await run_hansard_index(conn, full=True, config=test_settings)

    assert result["files"] == 3
    (count,) = conn.execute("SELECT count(*) FROM contribution").fetchone()
    assert count == 4  # 3 modern speeches + 1 old speech + 0 from the broken file
    assert get_state(conn, "hansard_cursor") == "4070995200"
    assert get_state(conn, "hansard_last_refresh") is not None

    poots = conn.execute(
        "SELECT person_id, minor_heading FROM contribution WHERE speech_id LIKE '%2099-01-01.2.2'"
    ).fetchone()
    assert poots["person_id"] == 90
    assert poots["minor_heading"] == "Arts Funding: North/South Disparity"


@respx.mock
async def test_incremental_run_after_full_is_a_noop(conn, test_settings):
    _mock_twfy_bulk()
    await run_hansard_index(conn, full=True, config=test_settings)
    before = conn.execute("SELECT count(*) FROM contribution").fetchone()[0]

    result = await run_hansard_index(conn, full=False, config=test_settings)
    assert result["files"] == 0
    assert conn.execute("SELECT count(*) FROM contribution").fetchone()[0] == before


@respx.mock
async def test_twfy_ingest_evicts_niapi_stand_in_for_the_date(conn, test_settings):
    _mock_twfy_bulk()
    upsert_contributions(
        conn,
        [{
            "speech_id": "niapi:555", "debate_date": "2099-01-01", "major_heading": "Old API Heading",
            "minor_heading": None, "person_id": None, "twfy_person_id": None, "speakername": None,
            "speech_time": None, "url": None, "body": "stale api text",
        }],
    )
    conn.commit()

    await run_hansard_index(conn, full=True, config=test_settings)

    assert conn.execute(
        "SELECT count(*) FROM contribution WHERE speech_id LIKE 'niapi:%' AND debate_date = '2099-01-01'"
    ).fetchone()[0] == 0


def test_components_to_rows_mapping():
    components = [
        _c("1", "Header", "Assembly Business", ComponentHeader="level 1"),
        _c("2", "Header", "Budget Statement", ComponentHeader="level 2"),
        _c("3", "Speaker (Speaker)", "Mr Speaker:", RelatedItemId="90"),
        _c("4", "Spoken Text", "I call the Minister."),
        _c("5", "Time", "10:30"),
    ]
    rows = _components_to_rows(components, "2026-08-20")
    by_id = {r["speech_id"]: r for r in rows}

    assert by_id["niapi:1"]["major_heading"] == "Assembly Business"
    assert by_id["niapi:2"]["minor_heading"] == "Budget Statement"
    spoken = by_id["niapi:4"]
    assert spoken["major_heading"] == "Assembly Business"
    assert spoken["minor_heading"] == "Budget Statement"
    assert spoken["person_id"] == 90
    assert spoken["speakername"] == "Mr Speaker"
    assert spoken["body"] == "I call the Minister."
    assert "niapi:3" not in by_id  # speaker rows don't become contributions
    assert "niapi:5" not in by_id  # time rows carry no body


@respx.mock
async def test_freshness_topup_fills_uncovered_recent_dates(conn, test_settings):
    # A TWFY-sourced row far in the past sets the frontier; a recent report is not covered.
    upsert_contributions(
        conn,
        [{
            "speech_id": "uk.org.publicwhip/ni/2000-01-01.1.1", "debate_date": "2000-01-01",
            "major_heading": "Old", "minor_heading": None, "person_id": None, "twfy_person_id": None,
            "speakername": None, "speech_time": None, "url": None, "body": "old business",
        }],
    )
    conn.commit()

    recent = "2099-06-30"
    respx.get(f"{NIAPI}/GetAllHansardReports_JSON").mock(
        return_value=_json({"AllHansardReports": {"HansardReport": [
            {"ReportDocId": "1", "PlenaryDate": f"{recent}T00:00:00+01:00"},
        ]}})
    )
    respx.get(f"{NIAPI}/GetHansardComponentsByPlenaryDate_JSON").mock(
        return_value=_json({"HansardComponentsList": {"HansardComponent": [
            _c("9001", "Header", "Question Time", ComponentHeader="level 1"),
            _c("9002", "Spoken Text", "The waiting list figures are improving."),
        ]}})
    )

    written = await _freshness_topup(conn, test_settings)
    assert written == 2
    rows = conn.execute(
        "SELECT major_heading FROM contribution WHERE speech_id LIKE 'niapi:%' AND debate_date = ?", (recent,)
    ).fetchall()
    assert {r["major_heading"] for r in rows} == {"Question Time"}


@respx.mock
async def test_freshness_topup_skips_dates_twfy_already_covers(conn, test_settings):
    covered = "2099-06-30"
    upsert_contributions(
        conn,
        [{
            "speech_id": f"uk.org.publicwhip/ni/{covered}.1.1", "debate_date": covered, "major_heading": "Covered",
            "minor_heading": None, "person_id": None, "twfy_person_id": None, "speakername": None,
            "speech_time": None, "url": None, "body": "already have this day",
        }],
    )
    conn.commit()

    reports_route = respx.get(f"{NIAPI}/GetAllHansardReports_JSON").mock(
        return_value=_json({"AllHansardReports": {"HansardReport": [
            {"ReportDocId": "1", "PlenaryDate": f"{covered}T00:00:00+01:00"},
        ]}})
    )
    components_route = respx.get(f"{NIAPI}/GetHansardComponentsByPlenaryDate_JSON").mock(return_value=_json({}))

    written = await _freshness_topup(conn, test_settings)
    assert written == 0
    assert reports_route.called
    assert not components_route.called

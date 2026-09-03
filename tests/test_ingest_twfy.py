from __future__ import annotations

from pathlib import Path

import httpx
import respx

from ni_assembly_mcp.ingest.http import PoliteFetcher
from ni_assembly_mcp.ingest.people_map import parse_people_json
from ni_assembly_mcp.ingest.twfy import changed_since, date_of, list_scrape_files, parse_scrape_file

_FIX = Path(__file__).parent / "fixtures" / "twfy"
_PEOPLE = parse_people_json((_FIX / "people.json").read_bytes())
TWFY = "https://www.theyworkforyou.com/pwdata/scrapedxml/ni"


def _xml(name: str) -> bytes:
    return (_FIX / name).read_bytes()


def test_date_of():
    assert date_of("ni2026-06-30.xml") == "2026-06-30"


def test_parse_modern_file_tracks_headings_and_maps_person():
    rows = list(parse_scrape_file(_xml("ni2099-01-01.xml"), "ni2099-01-01.xml", _PEOPLE))
    assert [r["speech_id"] for r in rows] == [
        "uk.org.publicwhip/ni/2099-01-01.2.2",
        "uk.org.publicwhip/ni/2099-01-01.2.3",
        "uk.org.publicwhip/ni/2099-01-01.3.2",
    ]

    poots, unmapped, murphy = rows
    assert poots["major_heading"] == "Executive Committee Business"
    assert poots["minor_heading"] == "Arts Funding: North/South Disparity"
    assert poots["person_id"] == 90
    assert poots["twfy_person_id"] == "uk.org.publicwhip/person/1001"
    assert poots["body"] == "The Minister funded the arts to the tune of £5 million.\nSecond paragraph about funding."
    assert poots["speech_time"] == "10:31"
    assert poots["url"] == "http://example/1"

    # unmapped speaker is kept, just without a person_id
    assert unmapped["person_id"] is None
    assert unmapped["speakername"] == "Jane Unmapped"
    assert unmapped["twfy_person_id"] == "uk.org.publicwhip/person/9999"

    # minor heading advanced; major unchanged
    assert murphy["major_heading"] == "Executive Committee Business"
    assert murphy["minor_heading"] == "Health Waiting Lists"
    assert murphy["person_id"] == 80


def test_parse_old_file_maps_via_speakerid():
    (row,) = list(parse_scrape_file(_xml("ni1999-01-01.xml"), "ni1999-01-01.xml", _PEOPLE))
    assert row["person_id"] == 90
    assert row["twfy_person_id"] == "uk.org.publicwhip/member/5001"
    assert row["major_heading"] == "Assembly: Preliminary Matters"
    assert row["minor_heading"] is None


def test_parse_broken_file_yields_nothing_without_raising():
    rows = list(parse_scrape_file(_xml("ni2098-01-01.xml"), "ni2098-01-01.xml", _PEOPLE))
    assert rows == []


@respx.mock
async def test_list_scrape_files(test_settings):
    respx.get(f"{TWFY}/").mock(return_value=httpx.Response(200, text=(_FIX / "index.html").read_text()))
    files = await list_scrape_files(PoliteFetcher(test_settings), test_settings)
    assert files == ["ni1999-01-01.xml", "ni2098-01-01.xml", "ni2099-01-01.xml"]


@respx.mock
async def test_changed_since_filters_and_reports_max_ts(test_settings):
    respx.get(f"{TWFY}/changedates.txt").mock(
        return_value=httpx.Response(200, text=(_FIX / "changedates.txt").read_text())
    )
    files, max_ts = await changed_since(PoliteFetcher(test_settings), test_settings, 2_000_000_000)
    # only the two 4-billion-ts entries are newer; ordered by ts asc
    assert files == ["ni2099-01-01.xml", "ni2098-01-01.xml"]
    assert max_ts == 4_070_995_200


@respx.mock
async def test_changed_since_empty_still_advances_cursor(test_settings):
    respx.get(f"{TWFY}/changedates.txt").mock(
        return_value=httpx.Response(200, text=(_FIX / "changedates.txt").read_text())
    )
    files, max_ts = await changed_since(PoliteFetcher(test_settings), test_settings, 5_000_000_000)
    assert files == []
    assert max_ts == 5_000_000_000

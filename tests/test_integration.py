"""Live smoke tests against data.niassembly.gov.uk.

Deselected by default (see pyproject ``addopts``). Run with:
    pytest -m integration
"""

from __future__ import annotations

import pytest

from ni_assembly_mcp.index_db import open_index
from ni_assembly_mcp.index_query import distinct_headings
from ni_assembly_mcp.ingest.http import PoliteFetcher
from ni_assembly_mcp.ingest.people_map import load_person_map
from ni_assembly_mcp.ingest.twfy import parse_scrape_file
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools.committees import get_committee_agenda
from ni_assembly_mcp.tools.hansard import get_hansard_reports, search_debate_titles
from ni_assembly_mcp.tools.member_detail import (
    get_detailed_member_information,
    get_registered_interests,
    get_state_of_the_parties,
    list_ministerial_roles,
)
from ni_assembly_mcp.tools.members import search_members
from ni_assembly_mcp.tools.plenary import (
    get_business_diary,
    get_divisions,
    get_motion_context,
    search_plenary_business,
)
from ni_assembly_mcp.tools.reference import (
    get_constituencies,
    get_departments,
    list_all_committees,
)

pytestmark = pytest.mark.integration


async def test_get_all_current_members_live(test_settings):
    records = await niassembly_get("members", "GetAllCurrentMembers", config=test_settings)
    assert len(records) > 50
    assert all("PersonId" in r for r in records)


async def test_single_result_endpoint_live(test_settings):
    # GetMemberRolesByPersonId for Edwin Poots (PersonId 90).
    records = await niassembly_get("members", "GetMemberRolesByPersonId", personId=90, config=test_settings)
    assert isinstance(records, list)
    assert records


async def test_reference_tools_live():
    departments = await get_departments()
    assert any(d["organisation_abbreviation"] == "DoF" for d in departments)

    constituencies = await get_constituencies()
    assert len(constituencies) == 18

    committees = await list_all_committees()
    assert len(committees) > 10
    assert {c["organisation_type"] for c in committees} > {"Statutory Committee"}


async def test_search_members_live():
    # Surname search is a literal substring on the stored surname ("O'Neill"),
    # so "neill" matches but "oneill" would not.
    by_name = await search_members(name="neill")
    assert any("O'Neill" in m["member_name"] for m in by_name)

    foyle = await search_members(constituency_id=5, max_results=10)
    assert foyle and all(m["constituency_name"] == "Foyle" for m in foyle)


async def test_detailed_member_information_live():
    # Edwin Poots (PersonId 90), currently Speaker.
    result = await get_detailed_member_information(
        member_id=90, include_contact=True, include_registered_interests=True
    )
    assert result["member"]["person_id"] == 90
    assert any(r["role_type"] == "Assembly Membership Role" for r in result["roles"])
    assert result["contact"]


async def test_registered_interests_live():
    rows = await get_registered_interests(max_results=20)
    assert rows and all("register_entry" in r for r in rows)


async def test_list_ministerial_roles_live():
    rows = await list_ministerial_roles()
    assert rows and all(r["role_type"] == "Ministerial Role" for r in rows)
    assert any("Health" in (r.get("affiliation_title") or "") for r in rows)


async def test_state_of_the_parties_live():
    result = await get_state_of_the_parties()
    assert result["total_seats"] >= 85
    assert result["parties"][0]["seats"] >= result["parties"][-1]["seats"]


async def test_business_diary_live():
    rows = await get_business_diary(start_date="2024-09-01", end_date="2024-09-30")
    assert rows and any(r["event_type"].startswith("Sitting") for r in rows)


async def test_search_plenary_business_live():
    rows = await search_plenary_business(
        date_from="2024-09-01", date_to="2024-09-30", plenary_type="Motion", include_tablers=True
    )
    assert rows and all("Motion" in r["plenary_type"] for r in rows)
    assert any(r.get("tablers") for r in rows)


async def test_get_divisions_live():
    rows = await get_divisions(date_from="2024-09-01", date_to="2024-09-30")
    assert rows and all("outcome" in r.get("result", {}) for r in rows)

    detail = await get_divisions(document_id=rows[0]["document_id"])
    assert detail["division_result"]["document_id"] == rows[0]["document_id"]
    assert detail["member_voting"]


async def test_get_motion_context_live():
    # 409547 — Final Stage of the Budget (No. 2) Bill; has a linked Bill.
    result = await get_motion_context(document_id=409547)
    assert result["motion"]["document_id"] == 409547
    assert result["bill"]["reference_number"].startswith("NIA Bill")


async def test_get_hansard_reports_live():
    rows = await get_hansard_reports(date_from="2026-01-01")
    assert rows and all(r["plenary_date"] >= "2026-01-01" for r in rows)
    assert rows[0]["plenary_date"] >= rows[-1]["plenary_date"]  # newest first


async def test_search_debate_titles_live(test_settings):
    """Build a one-day index, then drive the tool (settings patched by conftest)."""
    fetcher = PoliteFetcher(test_settings)
    people = await load_person_map(fetcher, test_settings)
    xml_bytes = await fetcher.get_bytes(f"{test_settings.twfy_base_url}/ni2026-06-30.xml")
    conn = open_index(test_settings.index_db_path)
    from ni_assembly_mcp.index_db import upsert_contributions

    upsert_contributions(conn, list(parse_scrape_file(xml_bytes, "ni2026-06-30.xml", people)))
    conn.commit()
    conn.close()

    browse = await search_debate_titles("", date_from="2026-06-01", date_to="2026-07-01")
    assert isinstance(browse, list) and browse
    term = next(r["major_heading"] for r in browse if r["major_heading"]).split()[0]
    result = await search_debate_titles(term, date_from="2026-06-01", date_to="2026-07-01")
    assert isinstance(result, list) and result
    assert all(r["debate_date"] == "2026-06-30" for r in result)


async def test_hansard_index_one_real_scrape_file(test_settings):
    """Fetch one real TWFY scrape file, parse it, index it, and search headings."""
    fetcher = PoliteFetcher(test_settings)
    people = await load_person_map(fetcher, test_settings)
    assert people.coverage > 50  # NI PersonIds present in parlparse

    filename = "ni2026-06-30.xml"
    xml_bytes = await fetcher.get_bytes(f"{test_settings.twfy_base_url}/{filename}")
    rows = list(parse_scrape_file(xml_bytes, filename, people))
    assert len(rows) > 100
    assert any(r["person_id"] for r in rows)  # current-mandate speakers map

    conn = open_index(test_settings.index_db_path)
    try:
        from ni_assembly_mcp.index_db import upsert_contributions

        upsert_contributions(conn, rows)
        conn.commit()
        all_headings = distinct_headings(conn, None, date_from="2026-06-01", date_to="2026-07-01", limit=200)
        assert all_headings and all(h["debate_date"] == "2026-06-30" for h in all_headings)
        # a word taken from a real heading must be findable (stemmed)
        term = next(h["major_heading"] for h in all_headings if h["major_heading"]).split()[0]
        assert distinct_headings(conn, term, date_from="2026-06-01", date_to="2026-07-01", limit=20)
    finally:
        conn.close()


async def test_get_committee_agenda_live():
    # 2025-01-14 — Committee for Education (OrganisationId 118), EventId 17502.
    by_date = await get_committee_agenda(meeting_date="2025-01-14")
    assert isinstance(by_date, list) and by_date
    assert any(r["committee_name"] == "Committee for Education" for r in by_date)

    by_committee = await get_committee_agenda(meeting_date="2025-01-14", committee_id=118)
    assert by_committee and all(r["committee_name"] == "Committee for Education" for r in by_committee)

    by_event = await get_committee_agenda(event_id=17502)
    assert by_event and {r["event_id"] for r in by_event} == {17502}


async def test_get_motion_context_petition_of_concern_live():
    # 242152 — the Nov-2015 Marriage Equality motion, blocked by a Petition of
    # Concern (doc 247608). No PoC exists in the current mandate, so this is the
    # only live check of that shape — see MotionPetitionOfConcern's docstring.
    result = await get_motion_context(document_id=242152)
    poc = result["petition_of_concern"]
    assert poc["parent_document_id"] == 242152
    assert poc["document_id"] == 247608

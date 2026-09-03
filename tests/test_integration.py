"""Live smoke tests against data.niassembly.gov.uk.

Deselected by default (see pyproject ``addopts``). Run with:
    pytest -m integration
"""

from __future__ import annotations

import pytest

from ni_assembly_mcp.niassembly_client import niassembly_get
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


async def test_get_motion_context_petition_of_concern_live():
    # 242152 — the Nov-2015 Marriage Equality motion, blocked by a Petition of
    # Concern (doc 247608). No PoC exists in the current mandate, so this is the
    # only live check of that shape — see MotionPetitionOfConcern's docstring.
    result = await get_motion_context(document_id=242152)
    poc = result["petition_of_concern"]
    assert poc["parent_document_id"] == 242152
    assert poc["document_id"] == 247608

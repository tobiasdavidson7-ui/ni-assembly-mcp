"""Live smoke tests against data.niassembly.gov.uk.

Deselected by default (see pyproject ``addopts``). Run with:
    pytest -m integration
"""

from __future__ import annotations

import pytest

from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools.members import search_members
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

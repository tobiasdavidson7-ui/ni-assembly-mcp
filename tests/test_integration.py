"""Live smoke tests against data.niassembly.gov.uk.

Deselected by default (see pyproject ``addopts``). Run with:
    pytest -m integration
"""

from __future__ import annotations

import pytest

from ni_assembly_mcp.niassembly_client import niassembly_get

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

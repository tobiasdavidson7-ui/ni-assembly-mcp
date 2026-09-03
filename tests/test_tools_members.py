from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.tools.members import search_members

BASE = "https://data.niassembly.gov.uk"


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


@respx.mock
async def test_no_filter_lists_current_members(load_fixture):
    route = respx.get(f"{BASE}/members.asmx/GetAllCurrentMembers_JSON").mock(
        return_value=_json(load_fixture("members_GetAllCurrentMembers.json"))
    )
    rows = await search_members()
    assert route.called
    # sorted by last name: Murphy before Poots
    assert [r["member_last_name"] for r in rows] == ["Murphy", "Poots"]
    assert rows[0]["person_id"] == 80


@respx.mock
async def test_current_only_false_uses_all_members():
    route = respx.get(f"{BASE}/members.asmx/GetAllMembers_JSON").mock(
        return_value=_json({"AllMembersList": {"Member": []}})
    )
    await search_members(current_only=False)
    assert route.called


@respx.mock
async def test_name_search_single_result(load_fixture):
    route = respx.get(f"{BASE}/members.asmx/GetAllCurrentMembersBySurnameSearch_JSON").mock(
        return_value=_json(load_fixture("members_GetAllCurrentMembersBySurnameSearch_single.json"))
    )
    rows = await search_members(name="neill")
    assert route.calls.last.request.url.params["searchText"] == "neill"
    assert len(rows) == 1
    assert rows[0]["member_full_display_name"] == "Ms Michelle O'Neill"


async def test_name_too_short_returns_message():
    result = await search_members(name="ne")
    assert isinstance(result, str)
    assert "at least 3" in result


@respx.mock
async def test_constituency_selector(load_fixture):
    route = respx.get(f"{BASE}/members.asmx/GetAllCurrentMembersByGivenConstituencyId_JSON").mock(
        return_value=_json({"AllMembersList": {"Member": []}})
    )
    await search_members(constituency_id=5)
    assert route.calls.last.request.url.params["constituencyId"] == "5"


@respx.mock
async def test_party_selector():
    route = respx.get(f"{BASE}/members.asmx/GetAllCurrentMembersByGivenPartyId_JSON").mock(
        return_value=_json({"AllMembersList": {"Member": []}})
    )
    await search_members(party_id=24)
    assert route.calls.last.request.url.params["partyId"] == "24"


@respx.mock
async def test_as_of_date_takes_precedence_over_name(load_fixture):
    route = respx.get(f"{BASE}/members.asmx/GetAllMembersByGivenDate_JSON").mock(
        return_value=_json(load_fixture("members_GetAllCurrentMembers.json"))
    )
    await search_members(name="poots", as_of_date="2018-06-01", constituency_id=1)
    assert route.called
    assert route.calls.last.request.url.params["specificDate"] == "2018-06-01"


@respx.mock
async def test_max_results_slices(load_fixture):
    respx.get(f"{BASE}/members.asmx/GetAllCurrentMembers_JSON").mock(
        return_value=_json(load_fixture("members_GetAllCurrentMembers.json"))
    )
    rows = await search_members(max_results=1)
    assert len(rows) == 1

from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.tools.member_detail import (
    get_detailed_member_information,
    get_registered_interests,
    get_state_of_the_parties,
    list_ministerial_roles,
)

BASE = "https://data.niassembly.gov.uk"


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


def _route(op, service="members"):
    return respx.get(f"{BASE}/{service}.asmx/{op}_JSON")


# --- get_detailed_member_information ---------------------------------------------


@respx.mock
async def test_detailed_member_core_only(load_fixture):
    _route("GetAllCurrentMembers").mock(return_value=_json(load_fixture("members_GetAllCurrentMembers.json")))
    result = await get_detailed_member_information(member_id=90, include_roles=False)
    assert result["member"]["member_last_name"] == "Poots"
    assert "roles" not in result


@respx.mock
async def test_detailed_member_unknown_id_returns_message(load_fixture):
    _route("GetAllCurrentMembers").mock(return_value=_json(load_fixture("members_GetAllCurrentMembers.json")))
    _route("GetAllMembers").mock(return_value=_json({"AllMembersList": {"Member": []}}))
    result = await get_detailed_member_information(member_id=999999)
    assert isinstance(result, str) and "999999" in result


@respx.mock
async def test_detailed_member_composes_sections(load_fixture):
    _route("GetAllCurrentMembers").mock(return_value=_json(load_fixture("members_GetAllCurrentMembers.json")))
    roles = _route("GetMemberRolesByPersonId").mock(
        return_value=_json(load_fixture("members_GetMemberRolesByPersonId.json"))
    )
    contact = _route("GetMemberContactDetailsByPersonId").mock(
        return_value=_json(load_fixture("members_GetMemberContactDetailsByPersonId.json"))
    )
    interests = _route("GetAllRegisteredInterests", service="register").mock(
        return_value=_json(load_fixture("register_GetAllRegisteredInterests.json"))
    )

    result = await get_detailed_member_information(
        member_id=90, include_contact=True, include_registered_interests=True
    )

    assert roles.calls.last.request.url.params["personId"] == "90"
    assert contact.called and interests.called
    # roles sorted newest-first
    assert result["roles"][0]["affiliation_start"].startswith("2024-02-03")
    assert result["contact"][0]["email"] == "edwin.poots@co.niassembly.gov.uk"
    assert {i["person_id"] for i in result["registered_interests"]} == {90}


@respx.mock
async def test_detailed_member_drops_failed_section(load_fixture):
    _route("GetAllCurrentMembers").mock(return_value=_json(load_fixture("members_GetAllCurrentMembers.json")))
    _route("GetMemberRolesByPersonId").mock(return_value=httpx.Response(500, text="boom"))
    result = await get_detailed_member_information(member_id=90, include_roles=True)
    assert "roles" not in result
    assert result["member"]["person_id"] == 90


# --- get_registered_interests --------------------------------------------------


@respx.mock
async def test_registered_interests_filter_by_member(load_fixture):
    _route("GetAllRegisteredInterests", service="register").mock(
        return_value=_json(load_fixture("register_GetAllRegisteredInterests.json"))
    )
    rows = await get_registered_interests(member_id=5797)
    assert {r["person_id"] for r in rows} == {5797}
    # newest first
    assert rows[0]["register_entry_start_date"].startswith("2023-04-19")


@respx.mock
async def test_registered_interests_filter_by_category(load_fixture):
    _route("GetAllRegisteredInterests", service="register").mock(
        return_value=_json(load_fixture("register_GetAllRegisteredInterests.json"))
    )
    rows = await get_registered_interests(category="donations")
    assert len(rows) == 1
    assert rows[0]["register_category"] == "Donations and other support"


# --- list_ministerial_roles --------------------------------------------------


@respx.mock
async def test_list_ministerial_roles_filters_and_sorts(load_fixture):
    _route("GetAllMemberRoles").mock(return_value=_json(load_fixture("members_GetAllMemberRoles.json")))
    rows = await list_ministerial_roles()
    assert all(r["role_type"] == "Ministerial Role" for r in rows)
    assert [r["organisation"] for r in rows] == [
        "Department of Education",
        "Department of Finance",
        "The Executive Office",
    ]


@respx.mock
async def test_list_ministerial_roles_excludes_juniors(load_fixture):
    _route("GetAllMemberRoles").mock(return_value=_json(load_fixture("members_GetAllMemberRoles.json")))
    rows = await list_ministerial_roles(include_junior_ministers=False)
    assert not any("junior" in r["role"].lower() for r in rows)
    assert len(rows) == 2


# --- get_state_of_the_parties ------------------------------------------------


@respx.mock
async def test_state_of_the_parties_current(load_fixture):
    _route("GetAllCurrentMembers").mock(return_value=_json(load_fixture("members_GetAllCurrentMembers.json")))
    _route("GetPartiesListCurrent", service="organisations").mock(
        return_value=_json(load_fixture("organisations_GetPartiesListCurrent.json"))
    )
    result = await get_state_of_the_parties()
    assert result["total_seats"] == 2
    assert result["as_of_date"] == "current"
    names = {p["party_name"] for p in result["parties"]}
    assert names == {"Democratic Unionist Party", "Sinn Fein"}


@respx.mock
async def test_state_of_the_parties_as_of_date_uses_by_date_op(load_fixture):
    route = _route("GetAllMembersByGivenDate").mock(
        return_value=_json(load_fixture("members_GetAllCurrentMembers.json"))
    )
    _route("GetPartiesListCurrent", service="organisations").mock(
        return_value=_json({"OrganisationsList": {"Organisation": []}})
    )
    result = await get_state_of_the_parties(as_of_date="2018-06-01")
    assert route.calls.last.request.url.params["specificDate"] == "2018-06-01"
    assert result["as_of_date"] == "2018-06-01"

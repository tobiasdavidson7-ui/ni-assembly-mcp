from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.tools.reference import (
    get_constituencies,
    get_departments,
    get_parties,
    list_all_committees,
    list_all_party_groups,
    list_organisations,
)

BASE = "https://data.niassembly.gov.uk"


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


@respx.mock
async def test_get_departments_coerces_and_sorts(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetDepartmentListCurrent_JSON").mock(
        return_value=_json(load_fixture("organisations_GetDepartmentListCurrent.json"))
    )
    rows = await get_departments()
    assert [r["organisation_abbreviation"] for r in rows] == ["DAERA", "DoF"]  # sorted by name
    assert rows[0]["organisation_id"] == 76  # string -> int coercion
    assert "OrganisationId" not in rows[0]  # aliased to snake_case


@respx.mock
async def test_get_parties(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetPartiesListCurrent_JSON").mock(
        return_value=_json(load_fixture("organisations_GetPartiesListCurrent.json"))
    )
    rows = await get_parties()
    assert {r["organisation_name"] for r in rows} == {"Alliance Party", "Sinn Féin"}


@respx.mock
async def test_list_all_party_groups():
    apg = {"OrganisationId": "898", "OrganisationName": "APG on Active Travel"}
    route = respx.get(f"{BASE}/organisations.asmx/GetAllPartyGroupsListCurrent_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": [apg]}})
    )
    rows = await list_all_party_groups()
    assert route.called
    assert rows == [{"organisation_id": 898, "organisation_name": "APG on Active Travel"}]


@respx.mock
async def test_list_organisations():
    org = {"OrganisationId": "21", "OrganisationName": "Independent", "OrganisationType": "Political Party"}
    route = respx.get(f"{BASE}/organisations.asmx/GetOrganisationListCurrent_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": [org]}})
    )
    rows = await list_organisations()
    assert route.called
    assert rows[0]["organisation_type"] == "Political Party"


@respx.mock
async def test_get_constituencies_sorted_by_name(load_fixture):
    respx.get(f"{BASE}/members.asmx/GetAllConstituencies_JSON").mock(
        return_value=_json(load_fixture("members_GetAllConstituencies.json"))
    )
    rows = await get_constituencies()
    assert [r["constituency_name"] for r in rows] == ["East Antrim", "Foyle"]
    assert rows[0]["constituency_id"] == 1


@respx.mock
async def test_list_all_committees_merges_categories(load_fixture):
    standing = respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Standing_JSON").mock(
        return_value=_json(load_fixture("organisations_GetCommitteesListCurrent_Standing.json"))
    )
    statutory = respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Statutory_JSON").mock(
        return_value=_json(load_fixture("organisations_GetCommitteesListCurrent_Statutory.json"))
    )
    adhoc = respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_AdHoc_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": None}})
    )
    other = respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Other_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": None}})
    )

    rows = await list_all_committees()
    assert standing.called and statutory.called and adhoc.called and other.called
    names = [r["organisation_name"] for r in rows]
    assert names == sorted(names)
    assert len(rows) == 3


@respx.mock
async def test_list_all_committees_single_category_hits_one_endpoint():
    route = respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Statutory_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": [{"OrganisationId": "3", "OrganisationName": "X"}]}})
    )
    rows = await list_all_committees(committee_type="statutory")
    assert route.called
    assert rows == [{"organisation_id": 3, "organisation_name": "X"}]


@respx.mock
async def test_list_all_committees_tolerates_one_failed_category(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Standing_JSON").mock(
        return_value=_json(load_fixture("organisations_GetCommitteesListCurrent_Standing.json"))
    )
    respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Statutory_JSON").mock(
        return_value=httpx.Response(500, text="boom", headers={"content-type": "text/html"})
    )
    respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_AdHoc_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": None}})
    )
    respx.get(f"{BASE}/organisations.asmx/GetCommitteesListCurrent_Other_JSON").mock(
        return_value=_json({"OrganisationsList": {"Organisation": None}})
    )
    rows = await list_all_committees()
    assert [r["organisation_abbreviation"] for r in rows] == ["BUS", "PAC"]

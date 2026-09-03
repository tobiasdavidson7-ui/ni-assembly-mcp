from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.tools.plenary import (
    get_business_diary,
    get_divisions,
    get_motion_context,
    get_no_day_named_motions,
    search_plenary_business,
)

BASE = "https://data.niassembly.gov.uk"


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


def _p(op):
    return respx.get(f"{BASE}/plenary.asmx/{op}_JSON")


_NO_TABLERS = {"TablerList": None}
_NO_ADDRESSEES = {"AddresseeList": None}


# --- search_plenary_business --------------------------------------------------


@respx.mock
async def test_plenary_tabled_date_selector_and_keyword(load_fixture):
    route = _p("GetPlenaryItemsTabledDate").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    _p("GetPlenaryTablers").mock(return_value=_json(_NO_TABLERS))
    rows = await search_plenary_business(query="deaf children", include_tablers=False)
    assert route.called
    assert [r["document_id"] for r in rows] == [412048]
    assert rows[0]["plenary_type"] == "Motion"


@respx.mock
async def test_plenary_scheduled_basis_uses_plenary_date_op(load_fixture):
    route = _p("GetPlenaryItemsPlenaryDate").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    await search_plenary_business(date_basis="scheduled", include_tablers=False)
    assert route.called


@respx.mock
async def test_plenary_member_selector(load_fixture):
    route = _p("GetPlenaryItemsTabledByMember").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    await search_plenary_business(member_id=5808, include_tablers=False)
    assert route.calls.last.request.url.params["personId"] == "5808"
    assert "startDate" in route.calls.last.request.url.params


@respx.mock
async def test_plenary_type_filter(load_fixture):
    _p("GetPlenaryItemsTabledDate").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    rows = await search_plenary_business(plenary_type="urgent oral question", include_tablers=False)
    assert [r["document_id"] for r in rows] == [412135]


@respx.mock
async def test_plenary_hydrates_tablers(load_fixture):
    _p("GetPlenaryItemsTabledDate").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    tablers = _p("GetPlenaryTablers").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryTablers.json"))
    )
    rows = await search_plenary_business(query="deaf children")
    assert tablers.called
    # sorted by TablerSequence
    assert [t["tabler_person_id"] for t in rows[0]["tablers"]] == [5808, 6157]


@respx.mock
async def test_plenary_no_results_message():
    _p("GetPlenaryItemsTabledDate").mock(return_value=_json({"PlenaryList": None}))
    result = await search_plenary_business()
    assert isinstance(result, str) and "No plenary business" in result


# --- get_business_diary -----------------------------------------------------


@respx.mock
async def test_business_diary_sorted_chronologically(load_fixture):
    route = _p("GetBusinessDiary").mock(
        return_value=_json(load_fixture("plenary_GetBusinessDiary.json"))
    )
    rows = await get_business_diary(start_date="2024-09-01", end_date="2024-09-30")
    assert route.calls.last.request.url.params["startDate"] == "2024-09-01"
    assert [r["event_id"] for r in rows] == [17042, 17087, 17088]


@respx.mock
async def test_business_diary_type_filter(load_fixture):
    _p("GetBusinessDiary").mock(return_value=_json(load_fixture("plenary_GetBusinessDiary.json")))
    rows = await get_business_diary(
        start_date="2024-09-01", end_date="2024-09-30", event_type="committee meeting"
    )
    assert [r["event_id"] for r in rows] == [17042]


@respx.mock
async def test_business_diary_organisation_filter(load_fixture):
    _p("GetBusinessDiary").mock(return_value=_json(load_fixture("plenary_GetBusinessDiary.json")))
    rows = await get_business_diary(
        start_date="2024-09-01", end_date="2024-09-30", organisation="plenary"
    )
    assert {r["event_id"] for r in rows} == {17087, 17088}


# --- get_divisions --------------------------------------------------------


@respx.mock
async def test_divisions_single_document(load_fixture):
    result = _p("GetDivisionResult").mock(
        return_value=_json(load_fixture("plenary_GetDivisionResult.json"))
    )
    votes = _p("GetDivisionMemberVoting").mock(
        return_value=_json(load_fixture("plenary_GetDivisionMemberVoting.json"))
    )
    out = await get_divisions(document_id=409547)
    assert result.calls.last.request.url.params["documentId"] == "409547"
    assert votes.called
    assert out["division_result"]["outcome"].startswith("The Motion Was Carried")
    assert out["division_result"]["total_ayes"] == 58
    # votes sorted by MemberSortName -> Aiken before Archibald
    assert [v["person_id"] for v in out["member_voting"]] == [5797, 5800]


@respx.mock
async def test_divisions_single_not_found():
    _p("GetDivisionResult").mock(return_value=_json({"DivisionDetails": None}))
    _p("GetDivisionMemberVoting").mock(return_value=_json({"MemberVoting": None}))
    out = await get_divisions(document_id=999999)
    assert isinstance(out, str) and "999999" in out


@respx.mock
async def test_divisions_list_with_results(load_fixture):
    _p("GetVotesOnDivision").mock(
        return_value=_json(load_fixture("plenary_GetVotesOnDivision.json"))
    )
    _p("GetDivisionResult").mock(
        return_value=_json(load_fixture("plenary_GetDivisionResult.json"))
    )
    rows = await get_divisions(date_from="2024-09-01", date_to="2024-09-30")
    # newest first
    assert [r["document_id"] for r in rows] == [409547, 409950]
    assert rows[0]["division_type"] == "Cross-Community"
    assert rows[0]["result"]["total_ayes"] == 58


@respx.mock
async def test_divisions_member_filter(load_fixture):
    _p("GetVotesOnDivision").mock(
        return_value=_json(load_fixture("plenary_GetVotesOnDivision.json"))
    )
    _p("GetDivisionMemberVoting").mock(
        return_value=_json(load_fixture("plenary_GetDivisionMemberVoting.json"))
    )
    _p("GetDivisionResult").mock(
        return_value=_json(load_fixture("plenary_GetDivisionResult.json"))
    )
    rows = await get_divisions(date_from="2024-09-01", date_to="2024-09-30", member_id=5797)
    # member 5797 (Aiken) appears in the mocked voting for both divisions
    assert all(r["member_vote"] == "NO" for r in rows)


@respx.mock
async def test_divisions_member_voted_in_none():
    _p("GetVotesOnDivision").mock(
        return_value=_json(
            {"DivisionList": {"Division": [
                {"DocumentID": "1", "DivisionSubject": "X", "DivisionDate": "2024-09-01T10:00:00+01:00"}
            ]}}
        )
    )
    _p("GetDivisionMemberVoting").mock(return_value=_json({"MemberVoting": {"Member": []}}))
    out = await get_divisions(date_from="2024-09-01", date_to="2024-09-30", member_id=42)
    assert isinstance(out, str) and "42" in out


# --- get_motion_context -------------------------------------------------


@respx.mock
async def test_motion_context_composes_sections(load_fixture):
    _p("GetPlenaryDetails").mock(return_value=_json(load_fixture("plenary_GetPlenaryDetails.json")))
    _p("GetPlenaryTablers").mock(return_value=_json(load_fixture("plenary_GetPlenaryTablers.json")))
    _p("GetPlenaryAddressees").mock(return_value=_json(_NO_ADDRESSEES))
    _p("GetMotionAmendments").mock(return_value=_json(load_fixture("plenary_GetMotionAmendments.json")))
    _p("GetMotionBill").mock(return_value=_json(load_fixture("plenary_GetMotionBill.json")))
    _p("GetMotionPetitionOfConcern").mock(return_value=_json({"MotionPetitionOfConcern": None}))

    out = await get_motion_context(document_id=409547)
    assert out["motion"]["title"].startswith("Final Stage")
    assert out["bill"]["reference_number"] == "NIA Bill 6/22-27"
    assert out["amendments"][0]["parent_document_id"] == 415521
    assert [t["tabler_person_id"] for t in out["tablers"]] == [5808, 6157]
    # empty sections omitted
    assert "addressees" not in out
    assert "petition_of_concern" not in out


@respx.mock
async def test_motion_context_not_found():
    _p("GetPlenaryDetails").mock(return_value=_json({"PlenaryList": None}))
    out = await get_motion_context(document_id=1)
    assert isinstance(out, str) and "document_id 1" in out


# --- get_no_day_named_motions -----------------------------------------


@respx.mock
async def test_no_day_named_empty():
    _p("GetNoDayNamedMotions").mock(return_value=_json({"PlenaryList": None}))
    out = await get_no_day_named_motions()
    assert isinstance(out, str)


@respx.mock
async def test_no_day_named_query_filter(load_fixture):
    _p("GetNoDayNamedMotions").mock(
        return_value=_json(load_fixture("plenary_GetPlenaryItemsTabledDate.json"))
    )
    rows = await get_no_day_named_motions(query="just eat")
    assert [r["document_id"] for r in rows] == [412135]

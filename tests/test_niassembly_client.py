from __future__ import annotations

import httpx
import pytest
import respx

from ni_assembly_mcp.exceptions import NIAssemblyAPIError
from ni_assembly_mcp.niassembly_client import niassembly_get, niassembly_get_xml

BASE = "https://data.niassembly.gov.uk"


@respx.mock
async def test_niassembly_get_happy_path(test_settings, load_fixture):
    route = respx.get(f"{BASE}/members.asmx/GetAllCurrentMembers_JSON").mock(
        return_value=httpx.Response(
            200,
            json=load_fixture("members_GetAllCurrentMembers.json"),
            headers={"content-type": "application/json"},
        )
    )
    records = await niassembly_get("members", "GetAllCurrentMembers", config=test_settings)
    assert route.called
    assert [r["PersonId"] for r in records] == ["90", "80"]


@respx.mock
async def test_params_are_query_string_and_blanks_dropped(test_settings):
    route = respx.get(f"{BASE}/members.asmx/GetMemberRolesByPersonId_JSON").mock(
        return_value=httpx.Response(200, json={"X": {"Y": []}}, headers={"content-type": "application/json"})
    )
    await niassembly_get(
        "members", "GetMemberRolesByPersonId", personId=90, unused=None, blank="", config=test_settings
    )
    request = route.calls.last.request
    assert request.url.params["personId"] == "90"
    assert "unused" not in request.url.params
    assert "blank" not in request.url.params


@respx.mock
async def test_html_error_body_with_200_raises(test_settings):
    respx.get(f"{BASE}/questions.asmx/GetQuestionDetails_JSON").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Runtime Error</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    with pytest.raises(NIAssemblyAPIError) as exc:
        await niassembly_get("questions", "GetQuestionDetails", documentId=1, config=test_settings)
    assert "non-JSON" in str(exc.value)


@respx.mock
async def test_500_is_retried_then_raises(test_settings):
    route = respx.get(f"{BASE}/plenary.asmx/GetBusinessDiary_JSON").mock(
        return_value=httpx.Response(500, text="server error", headers={"content-type": "text/html"})
    )
    with pytest.raises(NIAssemblyAPIError):
        await niassembly_get("plenary", "GetBusinessDiary", config=test_settings)
    # http_max_retries=2 in test_settings -> 1 initial + 2 retries
    assert route.call_count == 3


@respx.mock
async def test_500_then_200_recovers(test_settings):
    route = respx.get(f"{BASE}/register.asmx/GetAllRegisteredInterests_JSON").mock(
        side_effect=[
            httpx.Response(503, text="try later"),
            httpx.Response(200, json={"R": {"I": [{"PersonId": "1"}]}}, headers={"content-type": "application/json"}),
        ]
    )
    records = await niassembly_get("register", "GetAllRegisteredInterests", config=test_settings)
    assert records == [{"PersonId": "1"}]
    assert route.call_count == 2


async def test_unknown_service_rejected(test_settings):
    with pytest.raises(ValueError, match="Unknown NI Assembly service"):
        await niassembly_get("nope", "Whatever", config=test_settings)


# --- niassembly_get_xml (PLAN.md §3 — no _JSON variant) --------------------------


@respx.mock
async def test_niassembly_get_xml_flattens_rows(test_settings):
    route = respx.get(f"{BASE}/plenary.asmx/GetCommitteeAgendaItemsMeetingDate").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<?xml version="1.0" encoding="utf-8"?>'
                "<ItemList>"
                "<Committee><EventId>17502</EventId><ItemOrder>1</ItemOrder>"
                "<ItemOfBusiness>Apologies</ItemOfBusiness><Blank></Blank></Committee>"
                "<Committee><EventId>17517</EventId><ItemOrder>1</ItemOrder>"
                "<ItemOfBusiness>Committee Business</ItemOfBusiness></Committee>"
                "</ItemList>"
            ),
            headers={"content-type": "text/xml; charset=utf-8"},
        )
    )
    rows = await niassembly_get_xml(
        "plenary", "GetCommitteeAgendaItemsMeetingDate", meetingDate="2025-01-14", config=test_settings
    )
    assert route.calls.last.request.url.params["meetingDate"] == "2025-01-14"
    assert rows == [
        {"EventId": "17502", "ItemOrder": "1", "ItemOfBusiness": "Apologies", "Blank": ""},
        {"EventId": "17517", "ItemOrder": "1", "ItemOfBusiness": "Committee Business"},
    ]


@respx.mock
async def test_niassembly_get_xml_empty_container(test_settings):
    respx.get(f"{BASE}/plenary.asmx/GetCommitteeAgendaItemsMeetingDate").mock(
        return_value=httpx.Response(
            200, text='<?xml version="1.0"?><ItemList />', headers={"content-type": "text/xml"}
        )
    )
    rows = await niassembly_get_xml(
        "plenary", "GetCommitteeAgendaItemsMeetingDate", meetingDate="2025-01-01", config=test_settings
    )
    assert rows == []


@respx.mock
async def test_niassembly_get_xml_bad_body_raises(test_settings):
    respx.get(f"{BASE}/plenary.asmx/GetCommitteeAgendaItemsMeetingDate").mock(
        return_value=httpx.Response(200, text="<html>Runtime Error", headers={"content-type": "text/html"})
    )
    with pytest.raises(NIAssemblyAPIError):
        await niassembly_get_xml(
            "plenary", "GetCommitteeAgendaItemsMeetingDate", meetingDate="x", config=test_settings
        )

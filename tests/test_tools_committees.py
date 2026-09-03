from __future__ import annotations

from pathlib import Path

import httpx
import respx

from ni_assembly_mcp.tools.committees import get_committee_agenda

BASE = "https://data.niassembly.gov.uk"
FIXTURES = Path(__file__).parent / "fixtures"

_AGENDA_XML = (FIXTURES / "plenary_GetCommitteeAgendaItemsMeetingDate.xml").read_text(encoding="utf-8")
_EMPTY_XML = '<?xml version="1.0" encoding="utf-8"?><ItemList />'


def _xml(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/xml; charset=utf-8"})


def _p(op: str):
    return respx.get(f"{BASE}/plenary.asmx/{op}")


@respx.mock
async def test_agenda_by_meeting_date_sorted():
    route = _p("GetCommitteeAgendaItemsMeetingDate").mock(return_value=_xml(_AGENDA_XML))
    rows = await get_committee_agenda(meeting_date="2025-01-14")
    assert route.calls.last.request.url.params["meetingDate"] == "2025-01-14"
    # ordered by committee name, then item order
    assert [(r["committee_name"], r["item_order"]) for r in rows] == [
        ("Business Committee", 1),
        ("Committee for Education", 1),
        ("Committee for Education", 2),
    ]
    assert rows[1]["item_of_business"] == "Apologies"
    assert rows[0]["event_id"] == 17517


@respx.mock
async def test_agenda_by_committee_and_date_uses_organisation_id():
    route = _p("GetCommitteeAgendaItemsCommitteeMeetingDate").mock(return_value=_xml(_AGENDA_XML))
    await get_committee_agenda(meeting_date="2025-01-14", committee_id=118)
    params = route.calls.last.request.url.params
    assert params["meetingDate"] == "2025-01-14"
    assert params["organisationId"] == "118"


@respx.mock
async def test_agenda_by_event_id_takes_precedence():
    route = _p("GetCommitteeAgendaItemsCommitteeMeetingId").mock(return_value=_xml(_AGENDA_XML))
    await get_committee_agenda(meeting_date="2025-01-14", committee_id=118, event_id=17502)
    assert route.called
    assert route.calls.last.request.url.params["eventId"] == "17502"


@respx.mock
async def test_agenda_empty_itemlist_message():
    _p("GetCommitteeAgendaItemsMeetingDate").mock(return_value=_xml(_EMPTY_XML))
    result = await get_committee_agenda(meeting_date="2025-01-01")
    assert isinstance(result, str) and "2025-01-01" in result


async def test_agenda_requires_a_selector():
    result = await get_committee_agenda()
    assert isinstance(result, str) and "meeting_date" in result

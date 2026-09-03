"""Committee-agenda tools (PLAN.md Phase 7 / §3 — XML-only extras).

The NI API has **no JSON committee-agenda or committee-calendar endpoint**. The
only agenda data comes from the three XML-only ``plenary.asmx`` operations:

* ``GetCommitteeAgendaItemsMeetingDate`` — every committee meeting on a date;
* ``GetCommitteeAgendaItemsCommitteeMeetingDate`` — one committee on a date
  (``organisationId``);
* ``GetCommitteeAgendaItemsCommitteeMeetingId`` — one meeting (``eventId``, which
  matches the ``get_business_diary`` event id).

They are wrapped by :func:`~ni_assembly_mcp.niassembly_client.niassembly_get_xml`.
Meeting *scheduling* (which committee meets when) stays with ``get_business_diary``
— filter it to ``event_type="Committee Meeting"`` — this tool then lists what each
meeting is about.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.models import CommitteeAgendaItem, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get_xml
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)


@log_tool_call
async def get_committee_agenda(
    meeting_date: Annotated[
        str | None,
        Field(description="Meeting date (YYYY-MM-DD). Required unless event_id is given."),
    ] = None,
    committee_id: Annotated[
        int | None,
        Field(
            description="OrganisationId of one committee (from list_all_committees). "
            "Narrows a meeting_date lookup to that committee."
        ),
    ] = None,
    event_id: Annotated[
        int | None,
        Field(
            description="EventId of a single meeting (from get_business_diary). "
            "Takes precedence — meeting_date / committee_id are ignored."
        ),
    ] = None,
) -> list[dict] | str:
    """Agenda / order of business for Assembly committee meetings.

    Backed by the XML-only ``GetCommitteeAgendaItems*`` operations — this API has
    no JSON committee-agenda endpoint, and no committee inquiry / evidence /
    publications data at all. Selector precedence:

    - ``event_id`` → that one meeting's agenda;
    - ``meeting_date`` + ``committee_id`` → that committee's meeting that day;
    - ``meeting_date`` alone → every committee meeting that day.

    Each row has ``event_id`` (feed it back here, or match it against
    ``get_business_diary``), ``committee_name``, ``item_order``,
    ``item_of_business``, ``item_type``, ``session`` (public/closed + time span)
    and ``meeting_date``. Rows are ordered by committee then item order.
    """
    if event_id is not None:
        raw = await niassembly_get_xml(
            "plenary", "GetCommitteeAgendaItemsCommitteeMeetingId", eventId=event_id
        )
        selector = f"event_id {event_id}"
    elif meeting_date and committee_id is not None:
        raw = await niassembly_get_xml(
            "plenary",
            "GetCommitteeAgendaItemsCommitteeMeetingDate",
            meetingDate=meeting_date,
            organisationId=committee_id,
        )
        selector = f"committee {committee_id} on {meeting_date}"
    elif meeting_date:
        raw = await niassembly_get_xml(
            "plenary", "GetCommitteeAgendaItemsMeetingDate", meetingDate=meeting_date
        )
        selector = meeting_date
    else:
        return "Provide either event_id, or meeting_date (optionally with committee_id)."

    if not raw:
        return f"No committee agenda items found for {selector}."

    items = coerce_records(CommitteeAgendaItem, raw)
    items.sort(key=lambda i: ((i.get("committee_name") or ""), (i.get("item_order") or 0)))
    return items

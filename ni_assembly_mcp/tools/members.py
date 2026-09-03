"""Member search (PLAN.md Phase 2 / §1a).

There is no combined member-search endpoint on the NI API, so this tool is an
*operation selector*: whichever argument is set picks the operation. Only one
filter dimension is applied server-side; ``max_results`` is a client-side slice.
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.models import Member, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)

_MIN_SURNAME_CHARS = 3


@log_tool_call
async def search_members(
    name: Annotated[
        str | None,
        Field(description="Surname or partial surname (case-insensitive, min 3 chars). CURRENT members only."),
    ] = None,
    constituency_id: Annotated[
        int | None,
        Field(description="Constituency id (see get_constituencies). Current members only."),
    ] = None,
    party_id: Annotated[
        int | None,
        Field(description="Party organisation id (see get_parties). Current members only."),
    ] = None,
    as_of_date: Annotated[
        str | None,
        Field(description="Return the membership as it stood on this date (YYYY-MM-DD); includes historical members."),
    ] = None,
    current_only: Annotated[
        bool,
        Field(description="When no other filter is given: current members only (True) or every member ever (False)."),
    ] = True,
    max_results: Annotated[int, Field(description="Maximum members to return.", ge=1)] = 25,
) -> list[dict] | str:
    """Search Assembly members (MLAs).

    Exactly one search dimension is used, in this precedence order:
    ``as_of_date`` > ``name`` > ``constituency_id`` > ``party_id`` > (none).
    Pass no filter to list all current members (or all members ever with
    ``current_only=False``).

    Notes / limitations:
    - ``name``, ``constituency_id`` and ``party_id`` match CURRENT members only —
      there is no historical equivalent. For a past composition use ``as_of_date``.
    - There is no postcode or location search on this API.
    - Constituency and party arguments take numeric ids, not names — resolve them
      via ``get_constituencies`` / ``get_parties`` first.

    Each result has ``person_id`` (the id used by member-detail tools),
    ``member_name``, ``party_name``, ``constituency_name`` and more.
    """
    if as_of_date:
        records = await niassembly_get("members", "GetAllMembersByGivenDate", specificDate=as_of_date)
    elif name:
        if len(name.strip()) < _MIN_SURNAME_CHARS:
            return f"'name' must be at least {_MIN_SURNAME_CHARS} characters for a surname search."
        records = await niassembly_get(
            "members", "GetAllCurrentMembersBySurnameSearch", searchText=name.strip()
        )
    elif constituency_id is not None:
        records = await niassembly_get(
            "members", "GetAllCurrentMembersByGivenConstituencyId", constituencyId=constituency_id
        )
    elif party_id is not None:
        records = await niassembly_get(
            "members", "GetAllCurrentMembersByGivenPartyId", partyId=party_id
        )
    else:
        operation = "GetAllCurrentMembers" if current_only else "GetAllMembers"
        records = await niassembly_get("members", operation)

    coerced = coerce_records(Member, records)
    coerced.sort(key=lambda r: (r.get("member_last_name") or r.get("member_name") or "").lower())
    return coerced[:max_results]

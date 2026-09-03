"""Member detail, roles, register and party-standing tools (PLAN.md Phase 3).

None of these map to a single NI operation:

* ``get_detailed_member_information`` composes a core record + roles + contact +
  registered interests via :func:`gather_sections`.
* ``get_registered_interests`` filters the whole-register dump client-side.
* ``list_ministerial_roles`` filters ``GetAllMemberRoles`` (avoids the XML-only
  ``GetAllCurrentMinisters`` — PLAN.md §3).
* ``get_state_of_the_parties`` aggregates the member list by party.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.exceptions import NIAssemblyAPIError
from ni_assembly_mcp.models import (
    Member,
    MemberContact,
    MemberRole,
    Organisation,
    RegisteredInterest,
    coerce_records,
)
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools._base import gather_sections, log_tool_call

logger = logging.getLogger(__name__)

_MINISTERIAL_ROLE_TYPE = "Ministerial Role"


def _normalise_party(name: str) -> str:
    """Fold a party name for loose matching (strip accents, punctuation, case).

    The member list carries ``"Sinn Fein"`` while the party list has
    ``"Sinn Féin"``; this makes them compare equal.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch for ch in folded.lower() if ch.isalnum())


async def _find_member_record(member_id: int) -> dict | None:
    """Locate a member's core record by PersonId (current list first, then all)."""
    for operation in ("GetAllCurrentMembers", "GetAllMembers"):
        records = coerce_records(Member, await niassembly_get("members", operation))
        for record in records:
            if record.get("person_id") == member_id:
                return record
    return None


async def _member_roles(member_id: int) -> list[dict]:
    records = await niassembly_get("members", "GetMemberRolesByPersonId", personId=member_id)
    roles = coerce_records(MemberRole, records)
    roles.sort(key=lambda r: (r.get("affiliation_start") or "", r.get("role_type") or ""), reverse=True)
    return roles


async def _member_contact(member_id: int) -> list[dict]:
    records = await niassembly_get("members", "GetMemberContactDetailsByPersonId", personId=member_id)
    return coerce_records(MemberContact, records)


async def _member_interests(member_id: int) -> list[dict]:
    records = await niassembly_get("register", "GetAllRegisteredInterests")
    interests = coerce_records(RegisteredInterest, records)
    return [i for i in interests if i.get("person_id") == member_id]


@log_tool_call
async def get_detailed_member_information(
    member_id: Annotated[int, Field(description="PersonId of the member (from search_members).")],
    include_roles: Annotated[
        bool,
        Field(description="Include every role/affiliation (committees, ministerial, APGs) with start/end dates."),
    ] = True,
    include_contact: Annotated[
        bool,
        Field(description="Include constituency- and office-address contact details."),
    ] = False,
    include_registered_interests: Annotated[
        bool,
        Field(description="Include declared financial interests (employment, donations, gifts, property)."),
    ] = False,
) -> dict | str:
    """Compose a detailed profile for one Assembly member.

    The core record (name, party, constituency) always comes back under
    ``member``; the optional sections are fetched concurrently and a section that
    fails upstream is dropped rather than failing the whole call.

    Not available from this API (unlike the UK Parliament original):
    - prose biography / synopsis — no NI endpoint;
    - per-member voting record — divisions are only queryable one at a time
      (see the Phase 5 ``get_divisions`` tool).
    """
    member = await _find_member_record(member_id)
    if member is None:
        return f"No member found with person_id {member_id}."

    sections: dict[str, object] = {}
    if include_roles:
        sections["roles"] = _member_roles(member_id)
    if include_contact:
        sections["contact"] = _member_contact(member_id)
    if include_registered_interests:
        sections["registered_interests"] = _member_interests(member_id)

    return {"member": member, **await gather_sections(sections)}


@log_tool_call
async def get_registered_interests(
    member_id: Annotated[
        int | None,
        Field(description="Only interests declared by this member (PersonId from search_members)."),
    ] = None,
    category: Annotated[
        str | None,
        Field(description="Case-insensitive substring match on the register category, e.g. 'donations'."),
    ] = None,
    max_results: Annotated[int, Field(description="Maximum entries to return.", ge=1)] = 100,
) -> list[dict]:
    """List entries from the Assembly's Register of Members' Interests.

    The NI API only exposes the whole register in one call, so ``member_id`` and
    ``category`` are applied client-side. Entries are returned newest first.
    Each row has ``person_id``, ``member_name``, ``register_category``,
    ``register_entry`` (free text) and ``register_entry_start_date``.
    """
    interests = coerce_records(RegisteredInterest, await niassembly_get("register", "GetAllRegisteredInterests"))

    if member_id is not None:
        interests = [i for i in interests if i.get("person_id") == member_id]
    if category:
        needle = category.lower()
        interests = [i for i in interests if needle in (i.get("register_category") or "").lower()]

    interests.sort(key=lambda i: i.get("register_entry_start_date") or "", reverse=True)
    return interests[:max_results]


@log_tool_call
async def list_ministerial_roles(
    include_junior_ministers: Annotated[
        bool,
        Field(description="Include junior Ministers as well as departmental Ministers and the FM/dFM."),
    ] = True,
) -> list[dict]:
    """List the current ministerial roles and who holds them.

    Derived from ``GetAllMemberRoles`` filtered to ``RoleType = 'Ministerial
    Role'`` (the dedicated ministers endpoint is XML-only). ``GetAllMemberRoles``
    returns current roles only, so this is the present Executive. There is no
    "opposition posts" concept in NI's power-sharing model.

    Each row has the holder's ``person_id`` / ``member_full_display_name``, the
    ``organisation`` (department), ``affiliation_title`` (e.g. "Minister of
    Health") and ``affiliation_start``.
    """
    roles = coerce_records(MemberRole, await niassembly_get("members", "GetAllMemberRoles"))
    ministers = [r for r in roles if r.get("role_type") == _MINISTERIAL_ROLE_TYPE]
    if not include_junior_ministers:
        ministers = [r for r in ministers if "junior" not in (r.get("role") or "").lower()]
    ministers.sort(key=lambda r: (r.get("organisation") or "", r.get("affiliation_title") or ""))
    return ministers


@log_tool_call
async def get_state_of_the_parties(
    as_of_date: Annotated[
        str | None,
        Field(description="Party standing as it stood on this date (YYYY-MM-DD). Omit for the current Assembly."),
    ] = None,
) -> dict:
    """Seat counts by party in the Assembly.

    Aggregates ``GetAllCurrentMembers`` (or ``GetAllMembersByGivenDate`` when
    ``as_of_date`` is given) by ``PartyName``. Party abbreviations are merged in
    on a best-effort basis from the current party list. Returns
    ``{total_seats, as_of_date, parties: [{party_name, party_abbreviation?, seats}]}``
    ordered by seats descending.
    """
    if as_of_date:
        records = await niassembly_get("members", "GetAllMembersByGivenDate", specificDate=as_of_date)
    else:
        records = await niassembly_get("members", "GetAllCurrentMembers")
    members = coerce_records(Member, records)

    counts = Counter((m.get("party_name") or "Unknown") for m in members)

    abbreviations: dict[str, str] = {}
    try:
        parties = coerce_records(Organisation, await niassembly_get("organisations", "GetPartiesListCurrent"))
        abbreviations = {
            _normalise_party(p["organisation_name"]): p["organisation_abbreviation"]
            for p in parties
            if p.get("organisation_name") and p.get("organisation_abbreviation")
        }
    except NIAssemblyAPIError as exc:  # pragma: no cover - best-effort enrichment
        logger.warning("party abbreviation lookup failed: %s", exc)

    parties_out: list[dict] = []
    for name, seats in counts.most_common():
        row = {"party_name": name, "seats": seats}
        abbr = abbreviations.get(_normalise_party(name))
        if abbr:
            row["party_abbreviation"] = abbr
        parties_out.append(row)

    return {
        "total_seats": sum(counts.values()),
        "as_of_date": as_of_date or "current",
        "parties": parties_out,
    }

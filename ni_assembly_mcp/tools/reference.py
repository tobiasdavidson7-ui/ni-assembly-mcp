"""Reference & list-domain tools (PLAN.md Phase 2).

Thin wrappers over the ``organisations.asmx`` list operations plus the
constituency list. Every operation here returns its whole list in one call
(no pagination); ``max_results`` is a client-side slice where offered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from pydantic import Field

from ni_assembly_mcp.models import Constituency, Organisation, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)

_COMMITTEE_OPS: dict[str, str] = {
    "standing": "GetCommitteesListCurrent_Standing",
    "statutory": "GetCommitteesListCurrent_Statutory",
    "adhoc": "GetCommitteesListCurrent_AdHoc",
    "other": "GetCommitteesListCurrent_Other",
}


def _by_name(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (r.get("organisation_name") or "").lower())


@log_tool_call
async def get_departments() -> list[dict]:
    """List the current Northern Ireland Executive departments (NICS departments).

    Returns one row per department with its id, name, abbreviation (e.g. "DoF",
    "DAERA") and type. Use the ids to look up ministerial responsibility or to
    filter questions by answering department.
    """
    records = await niassembly_get("organisations", "GetDepartmentListCurrent")
    return _by_name(coerce_records(Organisation, records))


@log_tool_call
async def get_parties() -> list[dict]:
    """List the political parties currently represented in the Assembly.

    Each row carries the party's ``organisation_id`` (the "party id" used by
    ``search_members``), full name and abbreviation (e.g. "DUP", "SF", "APNI").
    """
    records = await niassembly_get("organisations", "GetPartiesListCurrent")
    return _by_name(coerce_records(Organisation, records))


@log_tool_call
async def list_all_party_groups() -> list[dict]:
    """List the current All-Party Groups (APGs) — cross-party interest groups such
    as the "All-Party Group on Cancer".

    Reference data only; membership is not included here.
    """
    records = await niassembly_get("organisations", "GetAllPartyGroupsListCurrent")
    return _by_name(coerce_records(Organisation, records))


@log_tool_call
async def list_organisations() -> list[dict]:
    """List every current organisation the Assembly tracks — committees, All-Party
    Groups, parties, ad-hoc bodies and more, in one combined list.

    Broad reference lookup. For a specific kind, prefer ``get_departments``,
    ``get_parties``, ``list_all_party_groups`` or ``list_all_committees``.
    """
    records = await niassembly_get("organisations", "GetOrganisationListCurrent")
    return _by_name(coerce_records(Organisation, records))


@log_tool_call
async def list_all_committees(
    committee_type: Annotated[
        Literal["all", "standing", "statutory", "adhoc", "other"],
        Field(description="Which committee category to return. 'all' merges the four categories."),
    ] = "all",
) -> list[dict]:
    """List the Assembly's current committees.

    The NI API splits committees across four operations — Standing, Statutory
    (the departmental scrutiny committees), Ad Hoc and Other. This tool returns
    the union by default. Former committees are not available from this API.

    Each row has the committee's ``organisation_id``, name, abbreviation and type.
    """
    wanted = list(_COMMITTEE_OPS) if committee_type == "all" else [committee_type]
    results = await asyncio.gather(
        *(niassembly_get("organisations", _COMMITTEE_OPS[key]) for key in wanted),
        return_exceptions=True,
    )

    merged: dict[object, dict] = {}
    for key, result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("committee list %r failed: %s", key, result)
            continue
        for record in result:
            merged[record.get("OrganisationId", id(record))] = record

    return _by_name(coerce_records(Organisation, list(merged.values())))


@log_tool_call
async def get_constituencies() -> list[dict]:
    """List the 18 Northern Ireland Assembly constituencies.

    Each row has the ``constituency_id`` (used by ``search_members``), the name
    and the ONS code. Constituencies are shared with Westminster.
    """
    records = await niassembly_get("members", "GetAllConstituencies")
    coerced = coerce_records(Constituency, records)
    return sorted(coerced, key=lambda r: (r.get("constituency_name") or "").lower())

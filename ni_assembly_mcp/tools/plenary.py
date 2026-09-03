"""Plenary business & divisions tools (PLAN.md Phase 5 / §1e).

New capability with no UK-Parliament-MCP analogue. All five tools sit on the
``plenary.asmx`` service:

* ``search_plenary_business`` — operation selector over the three
  ``GetPlenaryItems*`` list endpoints (tabled-date / plenary-date / by-member),
  then client-side keyword match + filter, optionally hydrating the top slice
  with its tablers.
* ``get_business_diary`` — thin wrapper over ``GetBusinessDiary`` (sittings,
  committee meetings, events) with client-side type/organisation filters.
* ``get_divisions`` — recorded votes: a date-range list, or one division's full
  result + per-member voting, or "how did member X vote" across a range.
* ``get_motion_context`` — composes a motion's details + tablers + amendments +
  linked Bill + Petition of Concern.
* ``get_no_day_named_motions`` — the standing list of motions with no scheduled
  debate date.

Every list endpoint returns its whole result in one call (no pagination);
``max_results`` is a client-side slice. The ``GetPlenaryItems*`` and
``GetVotesOnDivision`` endpoints all *require* a ``startDate``/``endDate`` window.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field

from ni_assembly_mcp.models import (
    BusinessDiaryItem,
    Division,
    DivisionMemberVote,
    DivisionResult,
    MotionAmendment,
    MotionBill,
    MotionPetitionOfConcern,
    PlenaryAddressee,
    PlenaryItem,
    PlenaryTabler,
    coerce_records,
)
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools._base import gather_sections, log_tool_call

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 90
_PHRASE_BONUS = 5


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _date_window(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Fill in a start/end pair (both endpoints below require them)."""
    if not date_from and not date_to:
        start = (datetime.now(tz=UTC).date() - timedelta(days=_DEFAULT_WINDOW_DAYS)).isoformat()
        return start, _today()
    return date_from or "2007-01-01", date_to or _today()


def _iso_date(value: str | None) -> str:
    return (value or "")[:10]


def _score(item: dict, tokens: list[str], phrase: str, *fields: str) -> int:
    """Token-overlap score with a whole-phrase bonus over the named fields.

    Same literal-substring approach as ``search_parliamentary_questions`` — no
    stemming, no synonyms.
    """
    text = " ".join((item.get(f) or "") for f in fields).lower()
    hits = sum(1 for token in tokens if token in text)
    bonus = _PHRASE_BONUS if phrase and phrase in text else 0
    return hits + bonus


# --- search_plenary_business --------------------------------------------------


async def _tablers(document_id: int) -> list[dict]:
    records = await niassembly_get("plenary", "GetPlenaryTablers", documentId=document_id)
    tablers = coerce_records(PlenaryTabler, records)
    tablers.sort(key=lambda t: t.get("tabler_sequence") or 0)
    return tablers


async def _addressees(document_id: int) -> list[dict]:
    records = await niassembly_get("plenary", "GetPlenaryAddressees", documentId=document_id)
    addressees = coerce_records(PlenaryAddressee, records)
    addressees.sort(key=lambda a: a.get("addressee_sequence") or 0)
    return addressees


@log_tool_call
async def search_plenary_business(
    query: Annotated[
        str | None,
        Field(description="Literal keyword(s) to match in the item TITLE and TEXT. Substring, not semantic."),
    ] = None,
    date_from: Annotated[
        str | None, Field(description="Start of the date window (YYYY-MM-DD). Defaults to 90 days ago.")
    ] = None,
    date_to: Annotated[
        str | None, Field(description="End of the date window (YYYY-MM-DD). Defaults to today.")
    ] = None,
    date_basis: Annotated[
        Literal["tabled", "scheduled"],
        Field(description="Whether the date window filters on when items were TABLED or when they are SCHEDULED."),
    ] = "tabled",
    member_id: Annotated[
        int | None,
        Field(description="PersonId of the tabling member (see search_members). Uses the tabled-date window."),
    ] = None,
    plenary_type: Annotated[
        str | None,
        Field(description="Case-insensitive substring on the item type, e.g. 'Motion', 'Urgent Oral Question'."),
    ] = None,
    include_tablers: Annotated[
        bool, Field(description="Fetch the list of tabling members for each returned item.")
    ] = True,
    max_results: Annotated[int, Field(description="Maximum items to return.", ge=1)] = 25,
) -> list[dict] | str:
    """Search tabled Assembly plenary business — motions, amendments, ministerial
    statements, urgent oral questions and no-day-named motions.

    Selector precedence: ``member_id`` > ``date_basis`` (``GetPlenaryItemsTabledDate``
    vs ``GetPlenaryItemsPlenaryDate``). Only that one dimension is server-side; a
    date window is always applied. ``query`` and ``plenary_type`` are matched
    client-side (literal substring — synonyms/paraphrases are missed), and results
    are ordered by relevance then date (newest first).

    Each row has ``document_id`` (feed it to ``get_motion_context`` for amendments,
    the linked Bill and any Petition of Concern), ``title``, ``text`` (the motion
    wording), ``tabled_date``, ``plenary_date`` and ``plenary_type``. With
    ``include_tablers`` (default), each row also gets a ``tablers`` list.
    """
    query = (query or "").strip() or None
    window_from, window_to = _date_window(date_from, date_to)

    if member_id is not None:
        raw = await niassembly_get(
            "plenary", "GetPlenaryItemsTabledByMember",
            personId=member_id, startDate=window_from, endDate=window_to,
        )
        date_field = "tabled_date"
    elif date_basis == "scheduled":
        raw = await niassembly_get(
            "plenary", "GetPlenaryItemsPlenaryDate", startDate=window_from, endDate=window_to
        )
        date_field = "plenary_date"
    else:
        raw = await niassembly_get(
            "plenary", "GetPlenaryItemsTabledDate", startDate=window_from, endDate=window_to
        )
        date_field = "tabled_date"

    if not raw:
        return "No plenary business found for the given window."

    items = coerce_records(PlenaryItem, raw)

    if plenary_type:
        needle = plenary_type.lower()
        items = [i for i in items if needle in (i.get("plenary_type") or "").lower()]

    if query:
        tokens = list(dict.fromkeys(query.lower().split()))
        phrase = query.lower()
        items = [i for i in items if _score(i, tokens, phrase, "title", "text") > 0]
        items.sort(key=lambda i: i.get(date_field) or i.get("tabled_date") or "", reverse=True)
        items.sort(key=lambda i: _score(i, tokens, phrase, "title", "text"), reverse=True)
    else:
        items.sort(key=lambda i: i.get(date_field) or i.get("tabled_date") or "", reverse=True)

    if not items:
        return "No plenary business matched after applying filters."

    items = items[:max_results]

    if include_tablers:
        results = await asyncio.gather(
            *(_tablers(i["document_id"]) for i in items if i.get("document_id") is not None),
            return_exceptions=True,
        )
        by_doc = {
            i["document_id"]: r
            for i, r in zip([i for i in items if i.get("document_id") is not None], results, strict=True)
            if not isinstance(r, BaseException)
        }
        for item in items:
            tablers = by_doc.get(item.get("document_id"))
            if tablers:
                item["tablers"] = tablers

    return items


# --- get_business_diary ------------------------------------------------------


@log_tool_call
async def get_business_diary(
    start_date: Annotated[str, Field(description="Start of the window (YYYY-MM-DD).")],
    end_date: Annotated[str, Field(description="End of the window (YYYY-MM-DD).")],
    event_type: Annotated[
        str | None,
        Field(description="Case-insensitive substring on the event type, e.g. 'Sitting', 'Committee Meeting'."),
    ] = None,
    organisation: Annotated[
        str | None,
        Field(description="Case-insensitive substring on the organisation, e.g. 'Committee for Health', 'Plenary'."),
    ] = None,
    max_results: Annotated[int, Field(description="Maximum events to return.", ge=1)] = 100,
) -> list[dict] | str:
    """The Assembly's business diary — plenary sittings, committee meetings and
    events in Parliament Buildings — between two dates.

    ``GetBusinessDiary`` requires both dates. ``event_type`` and ``organisation``
    are literal-substring filters applied client-side. Events are returned in
    chronological order. Each row has ``event_date``, ``start_time`` / ``end_time``,
    ``event_type``, ``organisation_name`` and the room.
    """
    raw = await niassembly_get(
        "plenary", "GetBusinessDiary", startDate=start_date, endDate=end_date
    )
    if not raw:
        return "No diary events found for the given window."

    events = coerce_records(BusinessDiaryItem, raw)

    if event_type:
        needle = event_type.lower()
        events = [e for e in events if needle in (e.get("event_type") or "").lower()]
    if organisation:
        needle = organisation.lower()
        events = [e for e in events if needle in (e.get("organisation_name") or "").lower()]

    events.sort(key=lambda e: (e.get("start_time") or e.get("event_date") or ""))

    if not events:
        return "No diary events matched after applying filters."
    return events[:max_results]


# --- get_divisions ----------------------------------------------------------


async def _division_result(document_id: int) -> dict | None:
    records = await niassembly_get("plenary", "GetDivisionResult", documentId=document_id)
    results = coerce_records(DivisionResult, records)
    return results[0] if results else None


async def _division_votes(document_id: int) -> list[dict]:
    records = await niassembly_get("plenary", "GetDivisionMemberVoting", documentId=document_id)
    votes = coerce_records(DivisionMemberVote, records)
    votes.sort(key=lambda v: v.get("member_sort_name") or v.get("member_name") or "")
    return votes


@log_tool_call
async def get_divisions(
    document_id: Annotated[
        int | None,
        Field(description="DocumentId of one division. Returns its result + per-member voting; date args ignored."),
    ] = None,
    date_from: Annotated[
        str | None,
        Field(description="Start of the date window (YYYY-MM-DD). Defaults to 90 days ago."),
    ] = None,
    date_to: Annotated[
        str | None, Field(description="End of the date window (YYYY-MM-DD). Defaults to today.")
    ] = None,
    member_id: Annotated[
        int | None,
        Field(description="PersonId (see search_members): keep only divisions this member voted in, plus their vote."),
    ] = None,
    include_results: Annotated[
        bool, Field(description="Hydrate each listed division with its outcome and aye/no tallies.")
    ] = True,
    max_results: Annotated[int, Field(description="Maximum divisions to return.", ge=1)] = 25,
) -> dict | list[dict] | str:
    """Recorded votes (divisions) in the Assembly.

    Two modes:
    - ``document_id`` given → one division: ``{division_result, member_voting}``
      (every MLA's AYE/NO/ABSTAINED with community designation).
    - otherwise → divisions in a date window (newest first). With
      ``include_results`` each carries its ``outcome`` and tallies; with
      ``member_id`` the list is filtered to divisions that member voted in and
      each row gets a ``member_vote``.

    NI divisions can be simple-majority or cross-community (the ``division_type``
    / ``decision_type`` field), the latter requiring concurrent unionist and
    nationalist majorities.
    """
    if document_id is not None:
        result, votes = await asyncio.gather(
            _division_result(document_id), _division_votes(document_id)
        )
        if result is None and not votes:
            return f"No division found with document_id {document_id}."
        return {"division_result": result, "member_voting": votes}

    window_from, window_to = _date_window(date_from, date_to)
    raw = await niassembly_get(
        "plenary", "GetVotesOnDivision", startDate=window_from, endDate=window_to
    )
    if not raw:
        return "No divisions found for the given window."

    divisions = coerce_records(Division, raw)
    divisions.sort(key=lambda d: d.get("division_date") or "", reverse=True)
    divisions = divisions[:max_results]

    doc_ids = [d["document_id"] for d in divisions if d.get("document_id") is not None]

    if member_id is not None:
        vote_lists = await asyncio.gather(
            *(_division_votes(doc_id) for doc_id in doc_ids), return_exceptions=True
        )
        member_votes: dict[int, str] = {}
        for doc_id, vote_list in zip(doc_ids, vote_lists, strict=True):
            if isinstance(vote_list, BaseException):
                logger.warning("member voting fetch failed for division %s: %s", doc_id, vote_list)
                continue
            for vote in vote_list:
                if vote.get("person_id") == member_id and vote.get("vote"):
                    member_votes[doc_id] = vote["vote"]
        divisions = [d for d in divisions if d.get("document_id") in member_votes]
        for division in divisions:
            division["member_vote"] = member_votes[division["document_id"]]
        doc_ids = [d["document_id"] for d in divisions]
        if not divisions:
            return f"Member {member_id} voted in no divisions in the given window."

    if include_results:
        results = await asyncio.gather(
            *(_division_result(doc_id) for doc_id in doc_ids), return_exceptions=True
        )
        by_doc = {
            doc_id: r
            for doc_id, r in zip(doc_ids, results, strict=True)
            if not isinstance(r, BaseException) and r is not None
        }
        for division in divisions:
            result = by_doc.get(division.get("document_id"))
            if result:
                division["result"] = result

    return divisions


# --- get_motion_context ----------------------------------------------------


@log_tool_call
async def get_motion_context(
    document_id: Annotated[
        int,
        Field(description="DocumentId of the motion / plenary item (from search_plenary_business)."),
    ],
) -> dict | str:
    """Assemble the full context around a motion: its own details, who tabled it,
    any amendments, a linked Bill, and any Petition of Concern.

    Composes ``GetPlenaryDetails`` + ``GetPlenaryTablers`` + ``GetMotionAmendments``
    + ``GetMotionBill`` + ``GetMotionPetitionOfConcern``. Sections that are empty
    or fail upstream are simply omitted. A Petition of Concern is an NI-specific
    mechanism letting 30 MLAs force a cross-community vote on a motion.

    Returns ``{motion, tablers?, addressees?, amendments?, bill?, petition_of_concern?}``.
    """
    details_records = await niassembly_get("plenary", "GetPlenaryDetails", documentId=document_id)
    motion = coerce_records(PlenaryItem, details_records)
    if not motion:
        return f"No plenary item found with document_id {document_id}."

    async def _amendments() -> list[dict]:
        records = await niassembly_get("plenary", "GetMotionAmendments", documentId=document_id)
        amendments = coerce_records(MotionAmendment, records)
        amendments.sort(key=lambda a: a.get("tabled_date") or "")
        return amendments

    async def _bill() -> dict | None:
        records = await niassembly_get("plenary", "GetMotionBill", documentId=document_id)
        bills = coerce_records(MotionBill, records)
        return bills[0] if bills else None

    async def _petition() -> dict | None:
        records = await niassembly_get("plenary", "GetMotionPetitionOfConcern", documentId=document_id)
        petitions = coerce_records(MotionPetitionOfConcern, records)
        return petitions[0] if petitions else None

    sections = await gather_sections(
        {
            "tablers": _tablers(document_id),
            "addressees": _addressees(document_id),
            "amendments": _amendments(),
            "bill": _bill(),
            "petition_of_concern": _petition(),
        }
    )

    out: dict = {"motion": motion[0]}
    for key, value in sections.items():
        if value:
            out[key] = value
    return out


# --- get_no_day_named_motions --------------------------------------------------


@log_tool_call
async def get_no_day_named_motions(
    query: Annotated[
        str | None,
        Field(description="Literal keyword(s) to match in the motion title/text. Substring, not semantic."),
    ] = None,
    max_results: Annotated[int, Field(description="Maximum motions to return.", ge=1)] = 100,
) -> list[dict] | str:
    """List "no day named" motions — motions that have been tabled and signed but
    have no scheduled debate date.

    ``GetNoDayNamedMotions`` takes no parameters and returns the whole standing
    list. ``query`` is an optional literal-substring filter on title/text.
    Results are newest-tabled first; each has ``document_id`` (feed to
    ``get_motion_context``), ``title``, ``text`` and ``tabled_date``.
    """
    raw = await niassembly_get("plenary", "GetNoDayNamedMotions")
    if not raw:
        return "There are currently no 'no day named' motions."

    motions = coerce_records(PlenaryItem, raw)

    query = (query or "").strip() or None
    if query:
        tokens = list(dict.fromkeys(query.lower().split()))
        phrase = query.lower()
        motions = [m for m in motions if _score(m, tokens, phrase, "title", "text") > 0]

    motions.sort(key=lambda m: m.get("tabled_date") or "", reverse=True)

    if not motions:
        return "No 'no day named' motions matched the query."
    return motions[:max_results]

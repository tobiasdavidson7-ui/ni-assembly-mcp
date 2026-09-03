"""Parliamentary-questions tools (PLAN.md Phase 4 / §6.1).

The NI ``questions.asmx`` service has **no combined-filter endpoint**: keyword
XOR member XOR department XOR date-range, never together, and each operation
returns a *different* subset of fields (PLAN.md §6.0). So
``search_parliamentary_questions`` is an operation selector that then merges,
ranks, hydrates and filters client-side. ``get_question_details`` is the rich
single-record lookup (the only op carrying the answer text).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.exceptions import NIAssemblyAPIError
from ni_assembly_mcp.models import Organisation, Question, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)

# The four date-range operations (written/oral, tabled/answered). Merged and
# de-duped on DocumentId when a bare date range drives the search.
_RANGE_OPS = (
    "GetQuestionsForWrittenAnswer_TabledInRange",
    "GetQuestionsForWrittenAnswer_AnsweredInRange",
    "GetQuestionsForOralAnswer_TabledInRange",
    "GetQuestionsForOralAnswer_AnsweredInRange",
)
_DEFAULT_WINDOW_DAYS = 90
# GetQuestionsBySearchText is server-side substring over ALL time; a broad term
# ("health") can return >10 MB. Above this many raw matches, refuse to rank
# rather than hydrate thousands of records (PLAN.md 6.1 pt 4).
_MAX_RANKABLE = 2000
_HYDRATE_FACTOR = 3
_PHRASE_BONUS = 5


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _date_window(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Fill in a start/end pair for the range endpoints (both are required)."""
    if not date_from and not date_to:
        start = (datetime.now(tz=UTC).date() - timedelta(days=_DEFAULT_WINDOW_DAYS)).isoformat()
        return start, _today()
    return date_from or "2007-01-01", date_to or _today()


def _iso_date(value: str | None) -> str:
    """Date portion of an NI datetime string ('2026-06-01T00:00:00+01:00' -> '2026-06-01')."""
    return (value or "")[:10]


def _score_question(question: dict, tokens: list[str], phrase: str) -> int:
    """Token-overlap score with a whole-phrase bonus (PLAN.md §6.1 pt 3).

    No stemming, no synonyms — literal substring on the question text only.
    """
    text = (question.get("question_text") or "").lower()
    hits = sum(1 for token in tokens if token in text)
    bonus = _PHRASE_BONUS if phrase and phrase in text else 0
    return hits + bonus


async def _fetch_range(date_from: str, date_to: str) -> list[dict]:
    results = await asyncio.gather(
        *(niassembly_get("questions", op, startDate=date_from, endDate=date_to) for op in _RANGE_OPS),
        return_exceptions=True,
    )
    merged: dict[object, dict] = {}
    for op, result in zip(_RANGE_OPS, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("range op %s failed: %s", op, result)
            continue
        for record in result:
            merged.setdefault(record.get("DocumentId", id(record)), record)
    return list(merged.values())


async def _resolve_department_id(name: str) -> int | None:
    needle = name.strip().lower()
    departments = coerce_records(
        Organisation, await niassembly_get("organisations", "GetDepartmentListCurrent")
    )
    for department in departments:
        full = (department.get("organisation_name") or "").lower()
        abbr = (department.get("organisation_abbreviation") or "").lower()
        if needle and (needle in full or needle == abbr):
            return department.get("organisation_id")
    return None


async def _hydrate(questions: list[dict]) -> list[dict]:
    """Fill in tabler/department/answer fields via GetQuestionDetails.

    The keyword endpoint's records omit ``tabler_person_id`` / ``department_name``,
    so member/party/department filters on a keyword search need this (PLAN.md §6.1
    pt 3). A per-record failure keeps the lean record rather than sinking the call.
    """

    async def _one(question: dict) -> dict:
        document_id = question.get("document_id")
        if document_id is None:
            return question
        try:
            records = await niassembly_get("questions", "GetQuestionDetails", documentId=document_id)
        except NIAssemblyAPIError as exc:  # pragma: no cover - best-effort enrichment
            logger.warning("hydration failed for document %s: %s", document_id, exc)
            return question
        detailed = coerce_records(Question, records)
        return {**question, **detailed[0]} if detailed else question

    return list(await asyncio.gather(*(_one(question) for question in questions)))


async def _person_party_map() -> dict[int, str]:
    from ni_assembly_mcp.models import Member

    members = coerce_records(Member, await niassembly_get("members", "GetAllMembers"))
    return {m["person_id"]: m["party_name"] for m in members if m.get("person_id") and m.get("party_name")}


def _party_matches(member_party: str | None, wanted: str) -> bool:
    if not member_party:
        return False
    a, b = member_party.lower(), wanted.strip().lower()
    return b in a or a in b


@log_tool_call
async def search_parliamentary_questions(
    query: Annotated[
        str | None,
        Field(description="Literal keyword(s) to match in the QUESTION TEXT (not answers). Substring, not semantic."),
    ] = None,
    date_from: Annotated[
        str | None, Field(description="Only questions tabled on/after this date (YYYY-MM-DD).")
    ] = None,
    date_to: Annotated[
        str | None, Field(description="Only questions tabled on/before this date (YYYY-MM-DD).")
    ] = None,
    party: Annotated[
        str | None, Field(description="Asking member's party (name or abbreviation). Applied client-side.")
    ] = None,
    asking_member_id: Annotated[
        int | None, Field(description="PersonId of the asking (tabling) member (see search_members).")
    ] = None,
    answering_body_name: Annotated[
        str | None, Field(description="Answering department, e.g. 'Health' or 'DoF' (see get_departments).")
    ] = None,
    max_results: Annotated[int, Field(description="Maximum questions to return.", ge=1)] = 25,
) -> list[dict] | str:
    """Search Assembly parliamentary questions (written and oral).

    Exactly one dimension is searched server-side, in this precedence:
    ``query`` > ``asking_member_id`` > ``answering_body_name`` > date range >
    (none → last 90 days). Every other argument is then applied **client-side**.

    Limitations (worse than the UK Parliament original, which had semantic search):
    - ``query`` is a case-insensitive **substring on the question text only** —
      answers are not searched, and synonyms/paraphrases are missed
      ("school funding" will not match "budget for education").
    - Ranking is token-overlap + a whole-phrase bonus + recency tie-break, not
      learned relevance.
    - Combining a very broad ``query`` with member/party/department filters
      hydrates only the top candidates, so some matches may be dropped. Narrow
      the term or add a date range.

    Returns questions newest-/most-relevant-first. Each row has ``document_id``
    (feed it to ``get_question_details`` for the answer text), ``reference``,
    ``tabled_date``, ``question_text`` and — depending on the endpoint that
    served it — tabler and department fields.
    """
    query = (query or "").strip() or None

    if query:
        raw = await niassembly_get("questions", "GetQuestionsBySearchText", searchText=query)
        selector = "keyword"
    elif asking_member_id is not None:
        raw = await niassembly_get("questions", "GetQuestionsByMember", personId=asking_member_id)
        selector = "member"
    elif answering_body_name:
        department_id = await _resolve_department_id(answering_body_name)
        if department_id is None:
            return f"No department matched {answering_body_name!r}. See get_departments for valid names."
        raw = await niassembly_get("questions", "GetQuestionsByDepartment", departmentId=department_id)
        selector = "department"
    else:
        window_from, window_to = _date_window(date_from, date_to)
        raw = await _fetch_range(window_from, window_to)
        selector = "range"

    if not raw:
        return "No questions found for the given filters."

    if selector == "keyword" and len(raw) > _MAX_RANKABLE and not (date_from or date_to):
        return (
            f"{query!r} matched {len(raw)} questions — too many to rank reliably. "
            "Add date_from/date_to or use a more specific term."
        )

    questions = coerce_records(Question, raw)

    if query:
        tokens = list(dict.fromkeys(query.lower().split()))
        phrase = query.lower()
        questions = [q for q in questions if _score_question(q, tokens, phrase) > 0]
        questions.sort(key=lambda q: q.get("tabled_date") or "", reverse=True)
        questions.sort(key=lambda q: _score_question(q, tokens, phrase), reverse=True)
    else:
        questions.sort(key=lambda q: q.get("tabled_date") or "", reverse=True)

    need_hydration = selector == "keyword" and (
        asking_member_id is not None or party is not None or answering_body_name is not None
    )
    if need_hydration:
        questions = await _hydrate(questions[: max_results * _HYDRATE_FACTOR])

    if date_from:
        questions = [q for q in questions if _iso_date(q.get("tabled_date")) >= date_from]
    if date_to:
        questions = [q for q in questions if _iso_date(q.get("tabled_date")) <= date_to]
    if asking_member_id is not None:
        questions = [q for q in questions if q.get("tabler_person_id") == asking_member_id]
    if answering_body_name:
        needle = answering_body_name.strip().lower()
        questions = [q for q in questions if needle in (q.get("department_name") or "").lower()]
    if party is not None:
        party_map = await _person_party_map()
        questions = [q for q in questions if _party_matches(party_map.get(q.get("tabler_person_id")), party)]

    if not questions:
        return "No questions matched after applying filters."
    return questions[:max_results]


@log_tool_call
async def get_question_details(
    document_id: Annotated[
        int, Field(description="DocumentId of the question (from search_parliamentary_questions).")
    ],
) -> dict | str:
    """Fetch the full record for one parliamentary question, including the answer.

    ``GetQuestionDetails`` is the only questions operation that returns
    ``answer_plain_text`` / ``answer_html`` and ``answered_on_date``, plus the
    tabler and minister PersonIds. Use it to hydrate a row from
    ``search_parliamentary_questions``.
    """
    records = await niassembly_get("questions", "GetQuestionDetails", documentId=document_id)
    detailed = coerce_records(Question, records)
    if not detailed:
        return f"No question found with document_id {document_id}."
    return detailed[0]

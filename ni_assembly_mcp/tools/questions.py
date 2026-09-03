"""Parliamentary-questions tools (PLAN.md Phase 4 / Phase 9 / §6.1).

The NI ``questions.asmx`` service has **no combined-filter endpoint**: keyword
XOR member XOR department XOR date-range, never together, and each operation
returns a *different* subset of fields (PLAN.md §6.0). So the live path
(:class:`~ni_assembly_mcp.index_query.LiveQuestionBackend`) is an operation
selector that then merges, ranks, hydrates and filters client-side.

Phase 9 adds a local FTS5 index
(:class:`~ni_assembly_mcp.index_query.Fts5QuestionBackend`): when built it gives
Porter-stemmed BM25 ranking, **answer-text** search, and freely-combining
filters across the whole 2007→present corpus. ``search_parliamentary_questions``
tries the index and falls back to the live selector when it is not built, so
users who never run ``ni-assembly-mcp index questions`` see no change.

``get_question_details`` is the rich single-record lookup (the only op carrying
the answer text).
"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field

from ni_assembly_mcp.exceptions import IndexNotBuiltError
from ni_assembly_mcp.index_query import Fts5QuestionBackend, LiveQuestionBackend, QuestionSearchBackend
from ni_assembly_mcp.models import Question, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.settings import settings
from ni_assembly_mcp.tools._base import log_tool_call

logger = logging.getLogger(__name__)


@log_tool_call
async def search_parliamentary_questions(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Keyword(s) to search. With the local index this covers question AND answer "
                "text; without it, question text only."
            )
        ),
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

    **With the local index built** (``ni-assembly-mcp index questions``): ``query``
    is Porter-stemmed and BM25-ranked over **question text and answer text**, so
    "budget for education" can surface a question answered in terms of school
    funding. Every other argument is a plain filter that combines freely, with no
    hydration cap, and ``asking_member_id`` / ``answering_body_name`` /
    ``date_from`` / ``date_to`` are reliable across the whole 2007→present corpus
    (``TablerPersonId`` is present in every source endpoint — there is **no**
    ~2022-onward mapping caveat here; that one is Hansard-only). Each row carries
    ``document_id`` (feed it to ``get_question_details`` for the full answer),
    ``reference``, ``document_type``, ``tabled_date``, ``answered_on_date``,
    ``question_text``, ``tabler_person_id``, ``department_name``, a ``snippet``
    around the hit and a ``relevance_score``.

    **Without the index** (live fallback): exactly one dimension is searched
    server-side, in this precedence — ``query`` > ``asking_member_id`` >
    ``answering_body_name`` > date range > (none → last 90 days) — and every other
    argument is applied client-side. In this mode ``query`` is a case-insensitive
    **substring on the question text only** (answers and synonyms are missed),
    ranking is token-overlap + a whole-phrase bonus + recency, and combining a
    broad ``query`` with member/party/department filters hydrates only the top
    candidates so some matches may be dropped — narrow the term or add a date
    range.

    Returns questions newest-/most-relevant-first, or a short string when nothing
    matches.
    """
    kwargs = {
        "query": query,
        "date_from": date_from,
        "date_to": date_to,
        "party": party,
        "asking_member_id": asking_member_id,
        "answering_body_name": answering_body_name,
        "max_results": max_results,
    }
    index_backend: QuestionSearchBackend = Fts5QuestionBackend(settings)
    try:
        return await index_backend.search(**kwargs)
    except IndexNotBuiltError:
        return await LiveQuestionBackend().search(**kwargs)


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

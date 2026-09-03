"""``questions.asmx`` range endpoints → the ``question`` FTS5 table (PLAN.md Phase 9).

Unlike Hansard (TheyWorkForYou bulk XML + a person-id map), parliamentary
questions come straight from the NI data API: the four range endpoints already
carry the answer text and the tabler's NI ``PersonId``, so there is **no
hydration and no id mapping**. Requests are windowed at 6 months because a
multi-year range call is a ~100 MB body on a legacy IIS backend (verified
2026-09-03), not because the server refuses one.
"""

from __future__ import annotations

import logging
from datetime import date

from dateutil.relativedelta import relativedelta

from ni_assembly_mcp.models import Question, coerce_records
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.settings import Settings

logger = logging.getLogger(__name__)

# Devolution restored in May 2007; windows before this return empty (1998-2002 too).
QUESTIONS_FLOOR = date(2007, 1, 1)
_WINDOW_MONTHS = 6

# written/oral, tabled/answered. ``AnsweredInRange`` is the one carrying answer
# text + AnsweredOnDate; the COALESCE upsert (index_db.upsert_questions) merges
# each DocumentId across all four regardless of ingest order.
_RANGE_OPS = (
    "GetQuestionsForWrittenAnswer_TabledInRange",
    "GetQuestionsForWrittenAnswer_AnsweredInRange",
    "GetQuestionsForOralAnswer_TabledInRange",
    "GetQuestionsForOralAnswer_AnsweredInRange",
)


def question_windows(start: date, end: date, *, months: int = _WINDOW_MONTHS) -> list[tuple[str, str]]:
    """``[(startDate, endDate), …]`` half-open-ish 6-month windows covering
    ``[start, end]``. Adjacent windows share their boundary day; the upsert
    de-dupes on ``document_id`` so the overlap is harmless."""
    if start > end:
        return []
    out: list[tuple[str, str]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + relativedelta(months=months), end)
        out.append((cursor.isoformat(), nxt.isoformat()))
        cursor = nxt
    return out or [(start.isoformat(), end.isoformat())]


def _date_only(value: str | None) -> str | None:
    """``'2007-05-15T00:00:00+01:00'`` → ``'2007-05-15'`` (stored form, matches
    ``contribution.debate_date`` so string compare is a date compare)."""
    return value[:10] if value else None


def _to_question_rows(raw: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for rec in coerce_records(Question, raw):
        document_id = rec.get("document_id")
        if document_id is None:
            continue
        rows.append(
            {
                "document_id": document_id,
                "reference": rec.get("reference"),
                "document_type": rec.get("document_type"),
                "tabled_date": _date_only(rec.get("tabled_date")),
                "answered_on_date": _date_only(rec.get("answered_on_date")),
                "question_text": rec.get("question_text"),
                "answer_text": rec.get("answer_plain_text"),
                "tabler_person_id": rec.get("tabler_person_id"),
                "department_name": rec.get("department_name"),
            }
        )
    return rows


async def fetch_window(start_iso: str, end_iso: str, *, config: Settings) -> list[dict]:
    """All four range endpoints for one window, mapped to ``question`` rows
    (not yet de-duped — the upsert does that)."""
    rows: list[dict] = []
    for op in _RANGE_OPS:
        raw = await niassembly_get("questions", op, startDate=start_iso, endDate=end_iso, config=config)
        mapped = _to_question_rows(raw)
        if raw and not mapped:
            logger.warning(
                "questions: %s %s..%s returned %d record(s) with no DocumentId", op, start_iso, end_iso, len(raw)
            )
        rows.extend(mapped)
    return rows

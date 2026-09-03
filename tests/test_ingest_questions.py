from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from ni_assembly_mcp.index_db import get_state, open_index
from ni_assembly_mcp.ingest.questions import _date_only, _to_question_rows, question_windows
from ni_assembly_mcp.ingest.runner import run_questions_index

Q = "https://data.niassembly.gov.uk/questions.asmx"


@pytest.fixture
def conn(tmp_path):
    c = open_index(tmp_path / "index.db")
    yield c
    c.close()


def _json(rows: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"QuestionsList": {"Question": rows}}, headers={"content-type": "application/json"}
    )


def _rec(document_id, **over) -> dict:
    base = {
        "DocumentId": str(document_id),
        "DocumentType": "Question for Written Answer",
        "Reference": f"AQW {document_id}/22-27",
        "QuestionText": "To ask the Minister about school funding.",
        "TabledDate": "2025-05-15T00:00:00+01:00",
        "TablerPersonId": "5793",
        "Department": "Department of Education",
    }
    base.update(over)
    return base


# --- pure helpers ------------------------------------------------------------

def test_question_windows_six_month_steps():
    w = question_windows(date(2007, 1, 1), date(2008, 6, 1))
    assert w == [
        ("2007-01-01", "2007-07-01"),
        ("2007-07-01", "2008-01-01"),
        ("2008-01-01", "2008-06-01"),
    ]


def test_question_windows_short_and_empty_ranges():
    assert question_windows(date(2025, 1, 1), date(2025, 3, 1)) == [("2025-01-01", "2025-03-01")]
    assert question_windows(date(2025, 3, 1), date(2025, 1, 1)) == []


def test_date_only():
    assert _date_only("2007-05-15T00:00:00+01:00") == "2007-05-15"
    assert _date_only(None) is None


def test_to_question_rows_maps_and_drops_idless():
    rows = _to_question_rows(
        [
            _rec(1, AnswerPlainText="Funding rose.", AnsweredOnDate="2025-05-20T00:00:00+01:00"),
            {"QuestionText": "no id here"},
        ]
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["document_id"] == 1
    assert r["answer_text"] == "Funding rose."
    assert r["tabled_date"] == "2025-05-15"
    assert r["answered_on_date"] == "2025-05-20"
    assert r["tabler_person_id"] == 5793


# --- run_questions_index ----------------------------------------------------

@respx.mock
async def test_run_questions_index_dedupes_and_prefers_the_answer(conn, test_settings):
    respx.get(f"{Q}/GetQuestionsForWrittenAnswer_TabledInRange_JSON").mock(
        return_value=_json([_rec(10), _rec(11)])  # no answers here
    )
    respx.get(f"{Q}/GetQuestionsForWrittenAnswer_AnsweredInRange_JSON").mock(
        return_value=_json([_rec(10, AnswerPlainText="Answer to 10.", AnsweredOnDate="2025-06-01T00:00:00+01:00")])
    )
    respx.get(f"{Q}/GetQuestionsForOralAnswer_TabledInRange_JSON").mock(
        return_value=_json([_rec(12, DocumentType="Question for Oral Answer")])
    )
    respx.get(f"{Q}/GetQuestionsForOralAnswer_AnsweredInRange_JSON").mock(return_value=_json([]))

    result = await run_questions_index(conn, full=False, since="2025-01-01", config=test_settings)

    assert result["questions"] > 0
    rows = {r["document_id"]: r for r in conn.execute(
        "SELECT document_id, answer_text, document_type FROM question"
    )}
    assert set(rows) == {10, 11, 12}
    assert rows[10]["answer_text"] == "Answer to 10."   # AnsweredInRange won
    assert rows[11]["answer_text"] is None
    assert rows[12]["document_type"] == "Question for Oral Answer"
    assert get_state(conn, "questions_cursor") is not None
    assert get_state(conn, "questions_last_refresh") is not None


@respx.mock
async def test_run_questions_index_incremental_resumes_from_cursor(conn, test_settings):
    routes = [
        respx.get(f"{Q}/{op}_JSON").mock(return_value=_json([]))
        for op in (
            "GetQuestionsForWrittenAnswer_TabledInRange",
            "GetQuestionsForWrittenAnswer_AnsweredInRange",
            "GetQuestionsForOralAnswer_TabledInRange",
            "GetQuestionsForOralAnswer_AnsweredInRange",
        )
    ]
    from ni_assembly_mcp.index_db import set_state

    set_state(conn, "questions_cursor", "2099-01-01")
    conn.commit()

    result = await run_questions_index(conn, full=False, config=test_settings)
    # cursor far in the future -> only the trailing 60-day rescan window remains
    assert result["windows"] == 1
    assert all(r.called for r in routes)

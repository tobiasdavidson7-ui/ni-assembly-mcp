from __future__ import annotations

import httpx
import respx

from ni_assembly_mcp.index_db import open_index, upsert_questions
from ni_assembly_mcp.tools.questions import get_question_details, search_parliamentary_questions

BASE = "https://data.niassembly.gov.uk"


def _seed_questions(test_settings):
    conn = open_index(test_settings.index_db_path)
    upsert_questions(
        conn,
        [
            {
                "document_id": 1, "reference": "AQW 1/22-27", "document_type": "Question for Written Answer",
                "tabled_date": "2025-05-01", "answered_on_date": "2025-05-20",
                "question_text": "To ask about school funding shortfalls",
                "answer_text": "the education budget rose slightly", "tabler_person_id": 100,
                "department_name": "Department of Education",
            },
            {
                "document_id": 2, "reference": "AQW 2/22-27", "document_type": "Question for Written Answer",
                "tabled_date": "2025-06-15", "answered_on_date": "2025-06-30",
                "question_text": "To ask about hospital waiting lists",
                "answer_text": "waiting times for surgery fell", "tabler_person_id": 200,
                "department_name": "Department of Health",
            },
        ],
    )
    conn.commit()
    conn.close()


def _json(payload):
    return httpx.Response(200, json=payload, headers={"content-type": "application/json"})


def _q(op):
    return respx.get(f"{BASE}/questions.asmx/{op}_JSON")


_EMPTY = {"QuestionsList": {"Question": []}}


# --- keyword search ------------------------------------------------------------


@respx.mock
async def test_keyword_search_ranks_and_drops_non_matches(load_fixture):
    _q("GetQuestionsBySearchText").mock(return_value=_json(load_fixture("questions_GetQuestionsBySearchText.json")))
    rows = await search_parliamentary_questions(query="hospital")
    # doc 102 (school funding) has no "hospital" -> dropped
    assert [r["document_id"] for r in rows] == [101, 103]
    assert rows[0]["reference"] == "AQW 100/22-27"


@respx.mock
async def test_keyword_phrase_bonus_outranks_recency(load_fixture):
    _q("GetQuestionsBySearchText").mock(return_value=_json(load_fixture("questions_GetQuestionsBySearchText.json")))
    rows = await search_parliamentary_questions(query="hospital transport")
    # doc 103 contains the whole phrase; doc 101 only "hospital" -> 103 first despite being older
    assert [r["document_id"] for r in rows] == [103, 101]


@respx.mock
async def test_keyword_over_threshold_returns_warning():
    many = {"QuestionsList": {"Question": [
        {"DocumentId": str(i), "QuestionText": "health", "TabledDate": "2026-01-01T00:00:00+00:00"}
        for i in range(2001)
    ]}}
    _q("GetQuestionsBySearchText").mock(return_value=_json(many))
    result = await search_parliamentary_questions(query="health")
    assert isinstance(result, str)
    assert "2001" in result and "date_from" in result


@respx.mock
async def test_keyword_over_threshold_allowed_with_date_range():
    many = {"QuestionsList": {"Question": [
        {"DocumentId": str(i), "QuestionText": "health matters", "TabledDate": "2026-06-01T00:00:00+01:00"}
        for i in range(2001)
    ]}}
    _q("GetQuestionsBySearchText").mock(return_value=_json(many))
    rows = await search_parliamentary_questions(query="health", date_from="2026-01-01", max_results=5)
    assert isinstance(rows, list) and len(rows) == 5


# --- selectors ----------------------------------------------------------------


@respx.mock
async def test_member_selector(load_fixture):
    route = _q("GetQuestionsByMember").mock(return_value=_json(load_fixture("questions_GetQuestionsByMember.json")))
    rows = await search_parliamentary_questions(asking_member_id=80)
    assert route.calls.last.request.url.params["personId"] == "80"
    # newest first
    assert [r["document_id"] for r in rows] == [201, 202]


@respx.mock
async def test_department_selector_resolves_name(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetDepartmentListCurrent_JSON").mock(
        return_value=_json(load_fixture("organisations_GetDepartmentListCurrent.json"))
    )
    route = _q("GetQuestionsByDepartment").mock(return_value=_json(_EMPTY))
    result = await search_parliamentary_questions(answering_body_name="Finance")
    assert route.called
    assert isinstance(result, str)  # empty payload -> message


@respx.mock
async def test_department_selector_unmatched_returns_message(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetDepartmentListCurrent_JSON").mock(
        return_value=_json(load_fixture("organisations_GetDepartmentListCurrent.json"))
    )
    result = await search_parliamentary_questions(answering_body_name="Ministry of Magic")
    assert isinstance(result, str) and "No department matched" in result


@respx.mock
async def test_range_selector_merges_and_dedupes(load_fixture):
    _q("GetQuestionsForWrittenAnswer_TabledInRange").mock(
        return_value=_json(load_fixture("questions_GetQuestionsForWrittenAnswer_TabledInRange.json"))
    )
    _q("GetQuestionsForWrittenAnswer_AnsweredInRange").mock(
        return_value=_json(load_fixture("questions_GetQuestionsForWrittenAnswer_TabledInRange.json"))
    )
    _q("GetQuestionsForOralAnswer_TabledInRange").mock(return_value=_json(_EMPTY))
    _q("GetQuestionsForOralAnswer_AnsweredInRange").mock(return_value=_json(_EMPTY))
    rows = await search_parliamentary_questions()
    # two distinct DocumentIds despite overlap across ops
    assert [r["document_id"] for r in rows] == [302, 301]


# --- client-side filters & hydration ----------------------------------------


@respx.mock
async def test_keyword_with_member_filter_hydrates(load_fixture):
    _q("GetQuestionsBySearchText").mock(return_value=_json(load_fixture("questions_GetQuestionsBySearchText.json")))
    details = _q("GetQuestionDetails").mock(
        return_value=_json(load_fixture("questions_GetQuestionDetails.json"))
    )
    rows = await search_parliamentary_questions(query="hospital", asking_member_id=123)
    assert details.called  # lean keyword rows lack tabler_person_id -> hydration
    assert rows and all(r["tabler_person_id"] == 123 for r in rows)


@respx.mock
async def test_client_side_date_filter(load_fixture):
    _q("GetQuestionsByMember").mock(return_value=_json(load_fixture("questions_GetQuestionsByMember.json")))
    rows = await search_parliamentary_questions(asking_member_id=80, date_from="2026-05-01")
    assert [r["document_id"] for r in rows] == [201]


# --- get_question_details ----------------------------------------------------


# --- index-backed path (Phase 9 commit 2) -----------------------------------


@respx.mock
async def test_index_path_searches_answer_text(test_settings):
    _seed_questions(test_settings)
    # "budget" appears only in q1's answer text -> live substring-on-question would miss it.
    rows = await search_parliamentary_questions(query="budget")
    assert [r["document_id"] for r in rows] == [1]
    assert rows[0]["relevance_score"] is not None
    assert rows[0]["reference"] == "AQW 1/22-27"
    assert "answer_text" not in rows[0]
    assert not respx.calls  # no live call when the index serves it


@respx.mock
async def test_index_path_stemmed_and_filtered(test_settings):
    _seed_questions(test_settings)
    rows = await search_parliamentary_questions(query="wait", asking_member_id=200)
    assert [r["document_id"] for r in rows] == [2]
    rows = await search_parliamentary_questions(query="wait", asking_member_id=100)
    assert isinstance(rows, str) and "No questions matched" in rows


@respx.mock
async def test_index_path_party_filter_hits_members(test_settings):
    _seed_questions(test_settings)
    respx.get(f"{BASE}/members.asmx/GetAllMembers_JSON").mock(
        return_value=_json({"AllMembersList": {"Member": [
            {"PersonId": "100", "PartyName": "Alliance"},
            {"PersonId": "200", "PartyName": "Sinn Fein"},
        ]}})
    )
    rows = await search_parliamentary_questions(query="school funding", party="Alliance")
    assert [r["document_id"] for r in rows] == [1]
    rows = await search_parliamentary_questions(query="school funding", party="Sinn Fein")
    assert isinstance(rows, str)


@respx.mock
async def test_falls_back_to_live_when_index_absent(load_fixture):
    # No index seeded -> IndexNotBuiltError -> live keyword selector.
    _q("GetQuestionsBySearchText").mock(
        return_value=_json(load_fixture("questions_GetQuestionsBySearchText.json"))
    )
    rows = await search_parliamentary_questions(query="hospital")
    assert [r["document_id"] for r in rows] == [101, 103]


@respx.mock
async def test_get_question_details(load_fixture):
    route = _q("GetQuestionDetails").mock(return_value=_json(load_fixture("questions_GetQuestionDetails.json")))
    result = await get_question_details(document_id=30000)
    assert route.calls.last.request.url.params["documentId"] == "30000"
    assert result["answer_plain_text"] == "The Department has restored 1,000 hectares."
    assert result["answered_on_date"].startswith("2026-06-10")
    assert result["department_name"] == "Department of Agriculture, Environment and Rural Affairs"


@respx.mock
async def test_get_question_details_not_found():
    _q("GetQuestionDetails").mock(return_value=_json({"QuestionsList": {"Question": []}}))
    result = await get_question_details(document_id=999999)
    assert isinstance(result, str) and "999999" in result

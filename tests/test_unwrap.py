from __future__ import annotations

import pytest

from ni_assembly_mcp.exceptions import NIAssemblyAPIError
from ni_assembly_mcp.niassembly_client import sanitize_params, unwrap_niassembly


def test_unwrap_list_envelope(load_fixture):
    records = unwrap_niassembly(load_fixture("members_GetAllCurrentMembers.json"))
    assert isinstance(records, list)
    assert len(records) == 2
    assert records[0]["PersonId"] == "90"


def test_unwrap_single_result_becomes_one_element_list(load_fixture):
    records = unwrap_niassembly(load_fixture("questions_GetQuestionDetails.json"))
    assert len(records) == 1
    assert records[0]["DocumentId"] == "30000"


def test_unwrap_empty_inner_null(load_fixture):
    assert unwrap_niassembly(load_fixture("members_empty.json")) == []


@pytest.mark.parametrize("payload", [None, "", {}, {"AllMembersList": {"Member": ""}}])
def test_unwrap_empty_variants(payload):
    assert unwrap_niassembly(payload) == []


def test_unwrap_single_item_list_dict_form():
    # ASP.NET XML->JSON sometimes emits a lone item as a dict, not a 1-list.
    payload = {"AllMembersList": {"Member": {"PersonId": "1"}}}
    assert unwrap_niassembly(payload) == [{"PersonId": "1"}]


def test_unwrap_already_a_list():
    assert unwrap_niassembly([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_unwrap_scalar_raises():
    with pytest.raises(NIAssemblyAPIError):
        unwrap_niassembly(5)


def test_unwrap_does_not_over_descend_into_single_field_record():
    # A single-result endpoint whose record happens to have one field.
    payload = {"Root": {"Item": {"OnlyField": "value"}}}
    assert unwrap_niassembly(payload) == [{"OnlyField": "value"}]


def test_sanitize_params_drops_none_and_blank():
    assert sanitize_params(a=1, b=None, c="", d="  ", e="x", self="drop") == {"a": 1, "e": "x"}

"""No-LLM forms UI (Phase 10 commit 2): specs, rendering, routes, safety.

Network is avoided except for one respx-mocked dispatch test; the rest exercise
the spec list, the coercion/render helpers and the Starlette wiring.
"""

from __future__ import annotations

import httpx
import respx
from starlette.testclient import TestClient

from ni_assembly_mcp.forms.render import normalise
from ni_assembly_mcp.forms.specs import FORMS, FORMS_BY_NAME, HARD_RESULT_CAP, clamp_counts
from ni_assembly_mcp.http_app import build_http_app
from ni_assembly_mcp.settings import Settings
from ni_assembly_mcp.tools import ALL_TOOLS

BASE = "https://data.niassembly.gov.uk"


def _client(**over) -> TestClient:
    cfg = Settings(
        http_rate_limit_per_minute=over.pop("per_ip", 1000),
        http_global_rate_limit_per_minute=over.pop("global_per_minute", 1_000_000),
        **over,
    )
    return TestClient(build_http_app(host="127.0.0.1", config=cfg))


# --- spec safety ----------------------------------------------------------


def test_every_form_targets_an_already_registered_tool():
    registered = set(ALL_TOOLS)
    assert {spec.tool for spec in FORMS} <= registered


def test_no_form_can_reach_the_offline_indexer():
    # The `index …` builders are a CLI subcommand, never tool functions. Pin that
    # nothing in forms/ dispatches into the ingest package or a builder-shaped name.
    for spec in FORMS:
        assert spec.tool.__module__.startswith("ni_assembly_mcp.tools."), spec.name
        assert not any(bad in spec.tool.__name__ for bad in ("index", "ingest", "build", "run_", "refresh"))


def test_form_names_are_unique():
    assert len(FORMS_BY_NAME) == len(FORMS)


# --- coercion / clamp / render helpers -----------------------------------


def test_clamp_counts_bounds_every_count_arg():
    out = clamp_counts({"max_results": 10_000, "num_contributors": 0, "num_contributions": 5, "query": "x"})
    assert out == {"max_results": HARD_RESULT_CAP, "num_contributors": 1, "num_contributions": 5, "query": "x"}


def test_normalise_message():
    assert normalise("nothing found") == {"kind": "message", "text": "nothing found"}


def test_normalise_record_flattens_nested_values():
    out = normalise({"name": "X", "roles": [{"a": 1}]})
    assert out["kind"] == "record"
    pairs = dict(out["pairs"])
    assert pairs["name"] == "X"
    assert '"a": 1' in pairs["roles"]


def test_normalise_table_unions_keys_in_first_seen_order():
    out = normalise([{"a": 1, "b": 2}, {"b": 3, "c": 4}])
    assert out["kind"] == "table"
    assert out["columns"] == ["a", "b", "c"]
    assert out["rows"] == [["1", "2", ""], ["", "3", "4"]]
    assert out["count"] == 2


# --- routes --------------------------------------------------------------


def test_index_page_lists_every_form():
    with _client() as client:
        body = client.get("/").text
    for spec in FORMS:
        assert f"/forms/{spec.name}" in body


def test_every_form_renders_blank():
    with _client() as client:
        for spec in FORMS:
            r = client.get(f"/forms/{spec.name}")
            assert r.status_code == 200, spec.name
            assert f'action="/forms/{spec.name}"' in r.text


def test_unknown_form_is_404():
    with _client() as client:
        assert client.get("/forms/does-not-exist").status_code == 404


def test_missing_required_field_is_reported_without_calling_the_tool():
    with _client() as client:
        r = client.post("/forms/question-detail", data={})
    assert r.status_code == 200
    assert "is required" in r.text


def test_non_numeric_integer_is_reported():
    with _client() as client:
        r = client.post("/forms/question-detail", data={"document_id": "not-a-number"})
    assert "not a whole number" in r.text


def test_submitted_values_are_html_escaped_on_the_way_back():
    # search_debate_titles returns the "index not built" string here (no network),
    # so this isolates the echo of the user's raw input into the form.
    with _client() as client:
        r = client.post("/forms/debate-titles", data={"query": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "<script>alert(1)" not in r.text
    assert "&lt;script&gt;" in r.text


@respx.mock
def test_dispatch_renders_a_result_table(load_fixture):
    respx.get(f"{BASE}/organisations.asmx/GetDepartmentListCurrent_JSON").mock(
        return_value=httpx.Response(
            200,
            json=load_fixture("organisations_GetDepartmentListCurrent.json"),
            headers={"content-type": "application/json"},
        )
    )
    with _client() as client:
        r = client.post("/forms/departments", data={})
    assert r.status_code == 200
    assert "<table>" in r.text
    assert "organisation_abbreviation" in r.text
    assert "DAERA" in r.text


def test_upstream_failure_is_a_message_not_a_500():
    with _client() as client:
        # committee-agenda with no args returns its own selector-guidance string.
        r = client.post("/forms/committee-agenda", data={})
    assert r.status_code == 200
    assert "event_id" in r.text


# --- rate limiting covers the forms routes too ---------------------------


def test_forms_routes_sit_behind_the_rate_limiter():
    with _client(per_ip=3) as client:
        statuses = [client.get("/forms/parties").status_code for _ in range(6)]
    assert 429 in statuses
    assert statuses[-1] == 429


def test_healthz_still_exempt_alongside_the_forms_routes():
    with _client(per_ip=1) as client:
        client.get("/forms/parties")
        client.get("/forms/parties")
        assert all(client.get("/healthz").status_code == 200 for _ in range(5))

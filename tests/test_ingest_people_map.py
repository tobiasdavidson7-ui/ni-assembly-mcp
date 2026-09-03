from __future__ import annotations

from pathlib import Path

from ni_assembly_mcp.ingest.people_map import parse_people_json

_PEOPLE = (Path(__file__).parent / "fixtures" / "twfy" / "people.json").read_bytes()


def test_parses_ni_identifiers_and_membership_links():
    pm = parse_people_json(_PEOPLE)
    assert pm.person_uri_to_ni == {
        "uk.org.publicwhip/person/1001": 90,
        "uk.org.publicwhip/person/1002": 80,
    }
    assert pm.member_uri_to_person_uri == {
        "uk.org.publicwhip/member/5001": "uk.org.publicwhip/person/1001",
    }


def test_resolve_direct_person_uri():
    pm = parse_people_json(_PEOPLE)
    assert pm.resolve("uk.org.publicwhip/person/1002", None) == 80


def test_resolve_via_speakerid_membership():
    pm = parse_people_json(_PEOPLE)
    assert pm.resolve(None, "uk.org.publicwhip/member/5001") == 90


def test_resolve_unmapped_returns_none():
    pm = parse_people_json(_PEOPLE)
    assert pm.resolve("uk.org.publicwhip/person/9999", None) is None
    assert pm.resolve(None, None) is None
    assert pm.resolve(None, "uk.org.publicwhip/member/does-not-exist") is None

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from ni_assembly_mcp.models import NIABaseModel, NIADateTime, parse_ni_datetime


class _Record(NIABaseModel):
    person_id: int
    oral: bool
    tabled: NIADateTime | None = None
    note: str | None = None


def test_string_scalars_coerced():
    rec = _Record(person_id="5797", oral="false", tabled="2026-06-30T00:00:00+01:00")
    assert rec.person_id == 5797
    assert rec.oral is False
    assert isinstance(rec.tabled, datetime)
    assert rec.tabled.year == 2026 and rec.tabled.day == 30


def test_blank_string_becomes_none():
    rec = _Record(person_id="1", oral="true", tabled="", note="   ")
    assert rec.tabled is None
    assert rec.note is None


def test_extra_fields_are_kept():
    rec = _Record(person_id="1", oral="0", ConstituencyName="Foyle")
    assert rec.model_extra["ConstituencyName"] == "Foyle"


def test_bad_int_still_raises():
    with pytest.raises(ValidationError):
        _Record(person_id="not-a-number", oral="true")


@pytest.mark.parametrize(
    ("value", "expected_year"),
    [
        ("2026-06-30T00:00:00+01:00", 2026),
        ("2020-01-01", 2020),
        ("/Date(1750000000000)/", 2025),
    ],
)
def test_parse_ni_datetime_forms(value, expected_year):
    parsed = parse_ni_datetime(value)
    assert isinstance(parsed, datetime)
    assert parsed.year == expected_year


def test_parse_ni_datetime_passthrough_and_blank():
    assert parse_ni_datetime("") is None
    assert parse_ni_datetime(None) is None
    assert parse_ni_datetime("not a date") == "not a date"

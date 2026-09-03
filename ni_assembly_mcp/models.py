"""Type-coercion layer for NI Assembly records.

Every scalar in an NI ``_JSON`` response is a string -- ``"PersonId": "5797"``,
``"QOralAnswerRequested": "false"``, ``"TabledDate": "2026-06-30T00:00:00+01:00"``
(PLAN.md 0 / 2b). Pydantic v2's lax coercion already turns ``"5797"`` into ``int``
and ``"false"`` into ``bool``; this module adds:

* blank strings -> ``None`` (NI uses ``""`` for "no value");
* a tolerant datetime parser (ISO-8601 with offset, plain dates, and the legacy
  ``/Date(ms)/`` form just in case).

Per-domain models (members, questions, ...) subclass :class:`NIABaseModel` and are
added in their own phases.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from dateutil import parser as _dateparser
from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator


def parse_ni_datetime(value: Any) -> Any:
    """Best-effort parse of an NI date/datetime string. Passthrough for non-strings."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return None

    # Legacy Microsoft AJAX format: /Date(1750000000000)/ or /Date(...+0100)/
    if text.startswith("/Date(") and text.endswith(")/"):
        inner = text[6:-2].split("+")[0].split("-")[0] if "+" in text[6:-2] else text[6:-2]
        try:
            return datetime.fromtimestamp(int(inner) / 1000, tz=UTC)
        except ValueError:
            return value

    try:
        return _dateparser.isoparse(text)
    except ValueError:
        try:
            return _dateparser.parse(text)
        except (ValueError, OverflowError):
            return value


NIADateTime = Annotated[datetime, BeforeValidator(parse_ni_datetime)]
NIADate = Annotated[date, BeforeValidator(parse_ni_datetime)]


class NIABaseModel(BaseModel):
    """Base for NI Assembly record models.

    * ``extra="allow"`` -- the API adds fields over time; keep unknowns rather
      than dropping data.
    * ``populate_by_name`` -- fields are declared snake_case with ``alias`` set to
      the API's PascalCase key.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_string_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

"""Turn a tool's return value into something the template can iterate.

Tools return ``list[dict] | dict | str`` (see the tool docstrings). This module
flattens that into one of three shapes so ``form.html`` stays dumb:

* ``{"kind": "message", "text": str}``      — the tool returned a string
* ``{"kind": "record", "pairs": [(k, v)]}`` — the tool returned one dict
* ``{"kind": "table", "columns": [...], "rows": [[cell, ...]]}``

Nested values (a row's ``tablers`` list, a division ``result`` dict) are rendered
as pretty JSON in the cell — legible without a second round-trip.
"""

from __future__ import annotations

import json
from typing import Any

_SCALARS = (str, int, float, bool)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, _SCALARS):
        return str(value)
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def normalise(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        return {"kind": "message", "text": result}

    if isinstance(result, dict):
        return {"kind": "record", "pairs": [(k, _cell(v)) for k, v in result.items()]}

    if isinstance(result, list):
        rows = [r for r in result if isinstance(r, dict)]
        if not rows:
            return {"kind": "message", "text": "(no results)" if not result else _cell(result)}
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return {
            "kind": "table",
            "count": len(rows),
            "columns": columns,
            "rows": [[_cell(row.get(col)) for col in columns] for row in rows],
        }

    return {"kind": "message", "text": _cell(result)}

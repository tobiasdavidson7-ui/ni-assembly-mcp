"""MCP tools for the NI Assembly Open Data API.

``ALL_TOOLS`` is the registration list consumed by :mod:`ni_assembly_mcp.server`.
Each phase appends its tools here.
"""

from __future__ import annotations

from collections.abc import Callable

from ni_assembly_mcp.tools.members import search_members
from ni_assembly_mcp.tools.reference import (
    get_constituencies,
    get_departments,
    get_parties,
    list_all_committees,
    list_all_party_groups,
    list_organisations,
)

# Phase 2 — reference & list domains
ALL_TOOLS: list[Callable] = [
    get_departments,
    get_parties,
    list_all_party_groups,
    list_organisations,
    list_all_committees,
    get_constituencies,
    search_members,
]

__all__ = ["ALL_TOOLS"]

"""No-LLM public forms UI over the registered MCP tools (PLAN.md Phase 10).

:func:`register_form_routes` attaches the routes to an :class:`MCPServer` before
its Starlette app is built; :data:`FORMS` is the declarative spec list.
"""

from __future__ import annotations

from ni_assembly_mcp.forms.specs import FORMS, FORMS_BY_NAME, GROUP_GLOSS
from ni_assembly_mcp.forms.views import register_form_routes

__all__ = ["FORMS", "FORMS_BY_NAME", "GROUP_GLOSS", "register_form_routes"]

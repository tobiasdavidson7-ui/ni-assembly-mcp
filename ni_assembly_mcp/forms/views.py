"""Starlette handlers for the no-LLM public UI (PLAN.md Phase 10, commits 2 and 3).

``GET /``            — index: every form grouped by domain.
``GET /forms/<name>`` — the blank form.
``POST /forms/<name>`` — coerce the submitted strings, call the bound tool
                         function directly, render the result as a table/record.
``GET /connect``      — static "point your own MCP client here" page: the ``/mcp/``
                         URL and two copy-paste client-config snippets.

The routes are attached with :meth:`MCPServer.custom_route`, so they land on the
*same* Starlette app as ``/mcp`` and sit behind commit 1's rate-limit middleware
(:func:`ni_assembly_mcp.http_app.build_http_app`).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.responses import FileResponse, HTMLResponse

from ni_assembly_mcp.forms.render import normalise
from ni_assembly_mcp.forms.specs import FORMS, FORMS_BY_NAME, GROUP_GLOSS, GROUP_ORDER, FormField, FormSpec, clamp_counts
from ni_assembly_mcp.settings import settings

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from starlette.requests import Request

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_LOGO_PATH = _STATIC_DIR / "logo.png"

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),  # public input — never trust it raw
)


def _grouped() -> list[tuple[str, list[FormSpec]]]:
    groups: dict[str, list[FormSpec]] = {}
    for spec in FORMS:
        groups.setdefault(spec.group, []).append(spec)
    # Present groups in GROUP_ORDER (content-search first), not FORMS declaration order.
    return [(name, groups[name]) for name in GROUP_ORDER if name in groups]


def _render(template: str, **ctx: object) -> HTMLResponse:
    return HTMLResponse(_env.get_template(template).render(**ctx))


def _coerce(fieldspec: FormField, raw: str) -> tuple[object | None, str | None]:
    """Return ``(value, error)``. An empty value coerces to ``None`` (⇒ tool default)."""
    raw = raw.strip()
    if raw == "":
        return None, None
    if fieldspec.kind == "int":
        try:
            return int(raw), None
        except ValueError:
            return None, f"{fieldspec.label}: '{raw}' is not a whole number."
    if fieldspec.kind == "bool":
        if raw not in ("true", "false"):
            return None, f"{fieldspec.label}: invalid value."
        return raw == "true", None
    if fieldspec.kind == "choice" and fieldspec.choices and raw not in fieldspec.choices:
        return None, f"{fieldspec.label}: invalid choice."
    return raw, None


async def _run_form(spec: FormSpec, submitted: dict[str, str]) -> HTMLResponse:
    kwargs: dict[str, object] = {}
    errors: list[str] = []
    for fieldspec in spec.fields:
        value, error = _coerce(fieldspec, submitted.get(fieldspec.name, ""))
        if error:
            errors.append(error)
        elif value is not None:
            kwargs[fieldspec.name] = value
        elif fieldspec.required:
            errors.append(f"{fieldspec.label} is required.")

    if errors:
        return _render("form.html", spec=spec, values=submitted, errors=errors, result=None)

    clamp_counts(kwargs)
    logger.info("form %s dispatched with %s", spec.name, sorted(kwargs))
    try:
        raw_result = await spec.tool(**kwargs)
    except Exception as exc:  # surface any upstream failure as a message, not a 500
        logger.exception("form %s failed", spec.name)
        result = {"kind": "message", "text": f"The request failed: {exc}"}
    else:
        result = normalise(raw_result)

    return _render("form.html", spec=spec, values=submitted, errors=[], result=result)


async def index(_request: Request) -> HTMLResponse:
    return _render("index.html", groups=_grouped(), group_gloss=GROUP_GLOSS)


async def logo(_request: Request) -> FileResponse:
    """Serve the site logo (fixed path — never user input)."""
    return FileResponse(_LOGO_PATH, headers={"Cache-Control": "public, max-age=604800, immutable"})


def _mcp_url() -> str:
    return settings.http_public_url.rstrip("/") + "/mcp/"


async def connect(_request: Request) -> HTMLResponse:
    """Static page: the hosted ``/mcp/`` URL + two client-config snippets."""
    mcp_url = _mcp_url()
    native = {"mcpServers": {"ni-assembly": {"url": mcp_url}}}
    mcp_remote = {"mcpServers": {"ni-assembly": {"command": "npx", "args": ["-y", "mcp-remote", mcp_url]}}}
    return _render(
        "connect.html",
        mcp_url=mcp_url,
        native_snippet=json.dumps(native, indent=2),
        mcp_remote_snippet=json.dumps(mcp_remote, indent=2),
    )


async def form_view(request: Request) -> HTMLResponse:
    spec = FORMS_BY_NAME.get(request.path_params["name"])
    if spec is None:
        return HTMLResponse("Unknown form.", status_code=404)
    if request.method == "POST":
        form = await request.form()
        submitted = {k: str(v) for k, v in form.items()}
        return await _run_form(spec, submitted)
    return _render("form.html", spec=spec, values={}, errors=[], result=None)


def register_form_routes(server: MCPServer) -> None:
    """Attach ``/`` and ``/forms/{name}``. Must run before ``streamable_http_app()``."""
    server.custom_route("/", methods=["GET"], include_in_schema=False)(index)
    server.custom_route("/static/logo.png", methods=["GET"], include_in_schema=False)(logo)
    server.custom_route("/connect", methods=["GET"], include_in_schema=False)(connect)
    server.custom_route("/forms/{name}", methods=["GET", "POST"], include_in_schema=False)(form_view)

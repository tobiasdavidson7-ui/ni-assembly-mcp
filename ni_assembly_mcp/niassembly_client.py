"""NI Assembly Open Data API adapter.

Replaces parliament-mcp's REST helpers (``request_members_api`` etc.). The NI API
is one host, six ``.asmx`` files, and GET ``<Operation>_JSON?param=value`` with a
query string only -- no path parameters, no pagination (PLAN.md 2b).
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ni_assembly_mcp.exceptions import NIAssemblyAPIError
from ni_assembly_mcp.http_client import cached_limited_get
from ni_assembly_mcp.settings import Settings, settings

logger = logging.getLogger(__name__)

Service = str  # one of: members, organisations, plenary, hansard, register, questions

_KNOWN_SERVICES = frozenset({"members", "organisations", "plenary", "hansard", "register", "questions"})


def sanitize_params(**kwargs: Any) -> dict[str, Any]:
    """Drop ``None`` / blank / ``self`` params (generic; kept from parliament-mcp)."""
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key == "self" or value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        out[key] = value
    return out


def unwrap_niassembly(payload: Any) -> list[dict]:
    """Normalise an NI ``_JSON`` body to a list of record dicts.

    NI wraps results as ``{"<RootName>": {"<ItemName>": [ ... ]}}`` for lists and
    ``{"<RootName>": {"<ItemName>": { ... }}}`` for single-result calls
    (e.g. ``GetQuestionDetails``). Both collapse to a list here; a single record
    becomes a one-element list (PLAN.md Appendix B).

    Empty results (``None`` / ``""`` / ``{"...": {"...": null}}``) become ``[]``.
    """
    obj = payload
    # Descend through single-key container wrappers (2 levels in practice; allow 3).
    for _ in range(3):
        if isinstance(obj, dict) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, (dict, list)) or inner is None or inner == "":
                obj = inner
                continue
        break

    if obj is None or obj == "":
        return []
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]
    if isinstance(obj, dict):
        return [obj] if obj else []

    raise NIAssemblyAPIError(f"Unexpected JSON shape after unwrap: {type(obj).__name__}")


def _looks_like_json(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        return True
    # NI sometimes serves JSON as text/plain; sniff the body.
    stripped = response.text.lstrip()
    return stripped[:1] in ("{", "[")


async def niassembly_get(
    service: Service,
    operation: str,
    *,
    config: Settings | None = None,
    **params: Any,
) -> list[dict]:
    """Call ``<base>/<service>.asmx/<operation>_JSON`` and return unwrapped records.

    ``params`` are sent as the query string verbatim (after dropping blanks).
    Raises :class:`NIAssemblyAPIError` on a non-JSON / error body.
    """
    config = config or settings
    if service not in _KNOWN_SERVICES:
        msg = f"Unknown NI Assembly service {service!r} (expected one of {sorted(_KNOWN_SERVICES)})"
        raise ValueError(msg)

    url = f"{config.base_url}/{service}.asmx/{operation}_JSON"
    query = sanitize_params(**params)
    logger.info("niassembly_get %s %s", url, query)

    response = await cached_limited_get(url, params=query, config=config)

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise NIAssemblyAPIError(
            "NI Assembly API returned an error status",
            url=str(response.url),
            status_code=response.status_code,
            body=response.text[:500],
        ) from exc

    if not _looks_like_json(response):
        raise NIAssemblyAPIError(
            "NI Assembly API returned a non-JSON body (likely an XML/HTML error page)",
            url=str(response.url),
            status_code=response.status_code,
            body=response.text[:500],
        )

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise NIAssemblyAPIError(
            "NI Assembly API body did not parse as JSON",
            url=str(response.url),
            status_code=response.status_code,
            body=response.text[:500],
        ) from exc

    return unwrap_niassembly(data)


async def niassembly_get_xml(
    service: Service,
    operation: str,
    *,
    config: Settings | None = None,
    **params: Any,
) -> list[dict]:
    """Call ``<base>/<service>.asmx/<operation>`` (no ``_JSON`` suffix) and parse
    the plain-XML body into a list of record dicts.

    For the handful of NI operations with no ``_JSON`` variant (PLAN.md §3) --
    currently the three ``plenary.asmx/GetCommitteeAgendaItems*`` operations. Their
    response is a flat ``<Container><Row><Field>text</Field>...</Row>...</Container>``
    (an empty result is ``<Container />``). Each repeated child element becomes a
    dict of ``{child tag: text}``, keyed by the API's PascalCase tags so
    :func:`~ni_assembly_mcp.models.coerce_records` can alias them as usual.
    """
    config = config or settings
    if service not in _KNOWN_SERVICES:
        msg = f"Unknown NI Assembly service {service!r} (expected one of {sorted(_KNOWN_SERVICES)})"
        raise ValueError(msg)

    url = f"{config.base_url}/{service}.asmx/{operation}"
    query = sanitize_params(**params)
    logger.info("niassembly_get_xml %s %s", url, query)

    response = await cached_limited_get(url, params=query, headers={"Accept": "application/xml"}, config=config)

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise NIAssemblyAPIError(
            "NI Assembly API returned an error status",
            url=str(response.url),
            status_code=response.status_code,
            body=response.text[:500],
        ) from exc

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise NIAssemblyAPIError(
            "NI Assembly API body did not parse as XML",
            url=str(response.url),
            status_code=response.status_code,
            body=response.text[:500],
        ) from exc

    return [_xml_row_to_dict(row) for row in root]


def _xml_row_to_dict(element: ET.Element) -> dict:
    """Flatten one repeated XML element to ``{tag: text}`` (blanks kept as ``""``;
    the model layer maps them to ``None``)."""
    return {child.tag: (child.text or "").strip() for child in element}

"""MCP server assembly.

stdio is the default transport (PLAN.md §5b); the ``--http`` path in
:mod:`ni_assembly_mcp.cli` opts into streamable HTTP. The MCP SDK (``mcp>=2``)
bundles the ASGI/uvicorn stack, so no separate extra is needed for HTTP.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ni_assembly_mcp import __version__
from ni_assembly_mcp.tools import ALL_TOOLS

_INSTRUCTIONS = (
    "Tools for the Northern Ireland Assembly Open Data API (data.niassembly.gov.uk). "
    "Reference lists (departments, parties, committees, constituencies) return whole "
    "lists; ids from them feed the search tools. This server is unaffiliated with the "
    "Northern Ireland Assembly."
)


def build_server() -> MCPServer:
    server: MCPServer = MCPServer(name="NI Assembly MCP", instructions=_INSTRUCTIONS, version=__version__)
    for tool in ALL_TOOLS:
        server.add_tool(tool)
    return server

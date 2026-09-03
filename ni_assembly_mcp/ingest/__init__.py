"""Offline ingestion for the FTS5 search index (PLAN.md Phase 6b).

Not imported by the MCP server — only by ``ni-assembly-mcp index``. Hansard comes
from TheyWorkForYou's bulk XML (:mod:`~ni_assembly_mcp.ingest.twfy`) mapped to NI
PersonIds via ``parlparse`` (:mod:`~ni_assembly_mcp.ingest.people_map`), with a
short freshness top-up from the NI data API; :mod:`~ni_assembly_mcp.ingest.runner`
orchestrates it.
"""

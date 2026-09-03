"""Command-line entry point: ``ni-assembly-mcp serve`` / ``ni-assembly-mcp index``."""

from __future__ import annotations

import argparse
import asyncio
import logging

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ni-assembly-mcp", description="NI Assembly Open Data MCP server.")
    parser.add_argument("--log-level", default="WARNING", choices=_LOG_LEVELS)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP server (stdio by default).")
    serve.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio.")
    serve.add_argument("--host", default="127.0.0.1", help="Host for --http (default: 127.0.0.1).")
    serve.add_argument("--port", type=int, default=8000, help="Port for --http (default: 8000).")

    index = sub.add_parser("index", help="Build or refresh the local search index (offline).")
    # 'hansard' / 'questions' build/refresh their table; 'status' just prints counts.
    index.add_argument("source", choices=["hansard", "questions", "status"])
    index.add_argument(
        "--full",
        action="store_true",
        help="Full rebuild (Hansard from 1998, questions from 2007; default: incremental).",
    )
    index.add_argument("--since", help="Override the incremental cursor: a YYYY-MM-DD date or a unix timestamp.")
    index.add_argument("--db", help="Index DB path (default: $NI_ASSEMBLY_MCP_INDEX_DB_PATH or the XDG data dir).")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        if args.http:
            from ni_assembly_mcp.http_app import serve_http

            serve_http(host=args.host, port=args.port, log_level=args.log_level)
        else:
            from ni_assembly_mcp.server import build_server

            build_server().run(transport="stdio")
    elif args.command == "index":
        from ni_assembly_mcp.ingest.runner import run_index_cli

        asyncio.run(run_index_cli(args.source, full=args.full, since=args.since, db=args.db))


if __name__ == "__main__":
    main()

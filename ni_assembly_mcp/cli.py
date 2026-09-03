"""Command-line entry point: ``ni-assembly-mcp serve``."""

from __future__ import annotations

import argparse
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        from ni_assembly_mcp.server import build_server

        server = build_server()
        if args.http:
            server.run(transport="streamable-http", host=args.host, port=args.port)
        else:
            server.run(transport="stdio")


if __name__ == "__main__":
    main()

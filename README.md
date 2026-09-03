# ni-assembly-mcp

An MCP server for the **Northern Ireland Assembly Open Data API**
(`data.niassembly.gov.uk`).

Ported from [`i-dot-ai/parliament-mcp`](https://github.com/i-dot-ai/parliament-mcp)
(MIT, © 2025 i.AI). See [`PLAN.md`](PLAN.md) for the porting design and phase plan,
and [`NOTICE`](NOTICE) for attribution.

> Status: **early development.** Phase 1 (HTTP adapter) only. Not yet usable as an
> MCP server.

This project is **not affiliated with the Northern Ireland Assembly** or with
mySociety / TheyWorkForYou.

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[http]"
.venv/Scripts/python -m pip install pytest pytest-asyncio respx ruff

.venv/Scripts/python -m pytest              # unit tests
.venv/Scripts/python -m pytest -m integration   # live API smoke tests
```

## Licensing

Code: MIT (see [`LICENSE`](LICENSE)). Data retrieved at runtime is subject to the
NI Assembly's and (Phase 6b) mySociety/TheyWorkForYou's terms — see `PLAN.md` §5d.

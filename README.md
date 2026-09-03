# ni-assembly-mcp

An MCP server for the **Northern Ireland Assembly Open Data API**
(`data.niassembly.gov.uk`).

Ported from [`i-dot-ai/parliament-mcp`](https://github.com/i-dot-ai/parliament-mcp)
(MIT, © 2025 i.AI). See [`PLAN.md`](PLAN.md) for the porting design and phase plan,
and [`NOTICE`](NOTICE) for attribution.

> Status: **early development.** Phase 3 (member detail, roles, register). The
> server runs and exposes the tools below; questions, plenary and Hansard tools
> are still to come (see [`PLAN.md`](PLAN.md) §4).

This project is **not affiliated with the Northern Ireland Assembly** or with
mySociety / TheyWorkForYou.

## Tools

| Tool | What it returns |
|---|---|
| `get_departments` | Current NI Executive departments (id, name, abbreviation) |
| `get_parties` | Parties currently represented (with the party id used by `search_members`) |
| `list_all_party_groups` | Current All-Party Groups |
| `list_organisations` | Every current organisation the Assembly tracks, combined |
| `list_all_committees` | Current committees — Standing / Statutory / Ad Hoc / Other, merged by default |
| `get_constituencies` | The 18 Assembly constituencies (id, name, ONS code) |
| `search_members` | MLAs by surname, constituency id, party id, or as-of date |
| `get_detailed_member_information` | One member's core record + roles, contact and registered interests (composed) |
| `get_registered_interests` | Register of Members' Interests, filterable by member or category |
| `list_ministerial_roles` | Current ministerial roles and their holders (the present Executive) |
| `get_state_of_the_parties` | Seat counts by party, now or on a given date |

## Running the server

```bash
ni-assembly-mcp serve            # stdio (default)
ni-assembly-mcp serve --http --port 8000   # streamable HTTP
```

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m pip install pytest pytest-asyncio respx ruff

.venv/Scripts/python -m pytest              # unit tests
.venv/Scripts/python -m pytest -m integration   # live API smoke tests
```

## Licensing

Code: MIT (see [`LICENSE`](LICENSE)). Data retrieved at runtime is subject to the
NI Assembly's and (Phase 6b) mySociety/TheyWorkForYou's terms — see `PLAN.md` §5d.

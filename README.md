# ni-assembly-mcp

An MCP server for the **Northern Ireland Assembly Open Data API**
(`data.niassembly.gov.uk`).

Ported from [`i-dot-ai/parliament-mcp`](https://github.com/i-dot-ai/parliament-mcp)
(MIT, © 2025 i.AI). See [`PLAN.md`](PLAN.md) for the porting design and phase plan,
and [`NOTICE`](NOTICE) for attribution.

> Status: **early development.** Phases 0–8 implemented (all core tools, the
> Hansard index, and packaging). Still deferred: the questions FTS5 index +
> index-backed `search_parliamentary_questions` path, and the 4b full
> written-answer fetch — see [`PLAN.md`](PLAN.md) §4.

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
| `search_parliamentary_questions` | Written/oral questions by keyword, member, department or date (substring, not semantic) |
| `get_question_details` | One question's full record, including the answer text |
| `search_plenary_business` | Tabled plenary items — motions, amendments, statements, urgent oral questions |
| `get_business_diary` | Sittings, committee meetings and events between two dates |
| `get_divisions` | Recorded votes: a date-range list, or one division's result + per-member voting |
| `get_motion_context` | A motion's details + tablers + amendments + linked Bill + Petition of Concern |
| `get_no_day_named_motions` | Motions tabled with no scheduled debate date |
| `get_hansard_reports` | Official Report (Hansard) sitting days, newest first |
| `search_debate_titles` | Debate/section headings matching a keyword in a date range (local index; stemmed, not semantic) |
| `search_contributions` | Full-text search over spoken contributions, 1998→present (local index; stemmed, BM25-ranked) |
| `find_relevant_contributors` | Members ranked by how much they spoke on the query terms (local index; BM25-weighted) |
| `get_committee_agenda` | ⚠ Order of business for committee meetings, by date / committee / meeting event id (XML-only endpoint) |

### Committee data — what is *not* available

This API has **no JSON committee endpoint** and no committee inquiry / evidence /
publications / minutes data at all. `get_committee_agenda` (Phase 7) wraps the
three XML-only `GetCommitteeAgendaItems*` operations to give the order of
business for a meeting; committee *scheduling* comes from `get_business_diary`
(filter `event_type="Committee Meeting"`). There is no `get_committee_details`
with membership/chairs beyond what `get_detailed_member_information` and
`list_ministerial_roles` derive from the roles data.

## Installing

```bash
pip install .            # or: pipx install .   /   uv tool install .
```

This puts `ni-assembly-mcp` on your PATH. Python 3.11+; the Hansard index needs a
SQLite build with FTS5 (standard on CPython for Windows/macOS and most Linux
distros).

## Running the server

```bash
ni-assembly-mcp serve                       # stdio (default)
ni-assembly-mcp serve --http --port 8000    # streamable HTTP
```

## Configuring an MCP client

The default transport is **stdio** — the client launches the server as a
subprocess, so nothing needs to be running in advance. [`claude_config.json`](claude_config.json):

```json
{
  "mcpServers": {
    "ni-assembly": {
      "command": "ni-assembly-mcp",
      "args": ["serve"]
    }
  }
}
```

- **Claude Desktop** — merge that into `claude_desktop_config.json`
  (`%APPDATA%\Claude\` on Windows, `~/Library/Application Support/Claude/` on macOS).
- **Claude Code** — `claude mcp add ni-assembly -- ni-assembly-mcp serve`.

If `ni-assembly-mcp` is not on the client's PATH, use an absolute path (e.g. the
`Scripts/`/`bin/` entry of the environment you installed it into). No
`mcp-remote` proxy is needed for stdio.

## Persistent paths

Two directories hold state that should survive restarts and upgrades. Both
default to XDG locations and are overridable by environment variable:

| Env var | Default | Holds |
|---|---|---|
| `NI_ASSEMBLY_MCP_INDEX_DB_PATH` | `~/.local/share/ni-assembly-mcp/index.db` | the Hansard FTS5 index (see below) |
| `NI_ASSEMBLY_MCP_HISHEL_CACHE_DIR` | `~/.cache/ni-assembly-mcp/http` | the on-disk HTTP cache (1-day TTL) for `data.niassembly.gov.uk` responses |

(`XDG_DATA_HOME` / `XDG_CACHE_HOME` are honoured for the base directory.) In
Docker these live under `/data` and are backed by named volumes — see below.

## Hosting over HTTP (Docker)

The default local setup is stdio and needs no container. Use
[`docker-compose.yaml`](docker-compose.yaml) when you want to *host* the server
for other clients. It runs two services on shared `index` / `http-cache`
volumes: `mcp-server` (streamable HTTP on port 8000) and `index-refresh` (a
daily **incremental** Hansard refresh — never `--full`, per the indexer
politeness limits).

```bash
docker compose run --rm mcp-server index hansard --full   # one-time full build
docker compose up -d
```

Point HTTP-capable MCP clients at `http://<host>:8000/mcp/`. Override the
published port with `NI_ASSEMBLY_MCP_PORT` and the refresh cadence with
`NI_ASSEMBLY_MCP_REFRESH_INTERVAL_SECONDS` (default 86400).

## Building the Hansard index

`search_debate_titles`, `search_contributions` and `find_relevant_contributors`
read a local SQLite FTS5 index — the NI Assembly API has no Hansard search. The
index is built **offline**; the server never builds it in a request.

```bash
ni-assembly-mcp index hansard --full   # first build: TheyWorkForYou bulk XML, 1998->present
ni-assembly-mcp index hansard          # incremental refresh (run periodically, e.g. daily)
ni-assembly-mcp index status           # row counts + last refresh
```

Hansard is ingested from [TheyWorkForYou's bulk XML](https://www.theyworkforyou.com/pwdata/scrapedxml/ni/)
(no API key), with speaker→`PersonId` mapping from mySociety's `parlparse`, plus a
short freshness top-up from the NI data API. The index lives at
`NI_ASSEMBLY_MCP_INDEX_DB_PATH` (see [Persistent paths](#persistent-paths)).

The incremental refresh only fetches TWFY scrape files changed since the last
run, so it is cheap — schedule it, don't re-run `--full`.

**Do not redistribute a built `index.db`** — it embeds TWFY-derived identifiers
that are CC BY-SA 2.5 (ShareAlike). Distribute the builder only.

### Scheduling the refresh

- **Docker:** the `index-refresh` service in `docker-compose.yaml` already does this.
- **Linux (always-on host):** the user units in [`deploy/systemd/`](deploy/systemd/) —
  a `oneshot` service plus a `daily` timer:

  ```bash
  mkdir -p ~/.config/systemd/user
  cp deploy/systemd/ni-assembly-mcp-index.* ~/.config/systemd/user/
  loginctl enable-linger "$USER"
  systemctl --user enable --now ni-assembly-mcp-index.timer
  ```

- **Windows:** a daily Task Scheduler entry —

  ```powershell
  schtasks /Create /TN "ni-assembly-mcp index" /SC DAILY /ST 04:00 ^
    /TR "ni-assembly-mcp index hansard"
  ```

- **macOS:** a `launchd` `StartCalendarInterval` agent running the same command.

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
NI Assembly's terms; the Hansard index additionally builds on
mySociety/TheyWorkForYou data — see `PLAN.md` §5d.

The Hansard index is derived from TheyWorkForYou:

> Data service provided by [TheyWorkForYou](https://www.theyworkforyou.com)

with the debate text under the Open Parliament Licence / the NI Assembly's own
Official Report reuse terms, and the speaker↔person identifiers under CC BY-SA 2.5.
This project is unaffiliated with the Northern Ireland Assembly and with
mySociety / TheyWorkForYou.

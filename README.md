# ni-assembly-mcp

An MCP server for the **Northern Ireland Assembly Open Data API**
(`data.niassembly.gov.uk`).

Ported from [`i-dot-ai/parliament-mcp`](https://github.com/i-dot-ai/parliament-mcp)
(MIT, © 2025 i.AI). See [`PLAN.md`](PLAN.md) for the porting design and phase plan,
and [`NOTICE`](NOTICE) for attribution.

> Status: **early development.** Phases 0–9 implemented (all core tools, the
> Hansard and questions FTS5 indexes, index-backed `search_parliamentary_questions`,
> and packaging). Still deferred: the 4b full written-answer fetch — see
> [`PLAN.md`](PLAN.md) §4.

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
| `search_parliamentary_questions` | Written/oral questions by keyword, member, department or date. With the `index questions` index built: BM25-ranked, Porter-stemmed, searches answer text too; otherwise substring on question text only |
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
| `NI_ASSEMBLY_MCP_INDEX_DB_PATH` | `~/.local/share/ni-assembly-mcp/index.db` | the FTS5 search index — Hansard and/or questions (see below) |
| `NI_ASSEMBLY_MCP_HISHEL_CACHE_DIR` | `~/.cache/ni-assembly-mcp/http` | the on-disk HTTP cache (1-day TTL) for `data.niassembly.gov.uk` responses |

(`XDG_DATA_HOME` / `XDG_CACHE_HOME` are honoured for the base directory.) In
Docker these live under `/data` and are backed by named volumes — see below.

**Sizing:** a full Hansard index is roughly 0.3–0.5 GB; adding the questions
index (`index questions`, which stores answer text) adds a further **~0.4–0.6 GB**
to `index.db`. Size the `index` volume for both if you build both.

## Hosting over HTTP (Docker)

> [!WARNING]
> **Deploying behind a reverse proxy or load balancer (Render, Fly.io, Railway,
> Cloudflare, nginx, Caddy, an ingress controller — anything that is not the app
> facing the internet directly)?** You **must** set
> `NI_ASSEMBLY_MCP_HTTP_TRUST_PROXY_HEADERS=true` (and, if your proxy is not on a
> loopback/private hop, `NI_ASSEMBLY_MCP_HTTP_FORWARDED_ALLOW_IPS` to that hop).
> Otherwise every request appears to come from the proxy's single IP, and
> **per-IP rate limiting silently collapses into one shared global bucket for all
> visitors** — one busy user then rate-limits everybody, and a single abuser is
> indistinguishable from normal traffic. There is no error and no log line when
> this happens; it just quietly stops working. Conversely, only turn it on when a
> proxy really is in front — with the app at the edge, `X-Forwarded-For` is
> attacker-controlled and would defeat the per-IP limit the other way.

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
`NI_ASSEMBLY_MCP_REFRESH_INTERVAL_SECONDS` (default 86400). `GET /healthz`
returns `{"status": "ok"}` for uptime checks (never rate limited).

### Rate limiting

`serve --http` is a public surface — the raw MCP transport **and** (from Phase
10) the forms UI — so one in-process middleware sits in front of everything on
the app: a per-IP fixed-window limit plus a global circuit breaker that returns
`503` for a short cooldown if total traffic spikes far past normal. All limits
are env-configurable; the defaults keep an abusive client from running up
hosting cost without getting in the way of a normal MCP session.

| Env var | Default | Meaning |
|---|---|---|
| `NI_ASSEMBLY_MCP_HTTP_RATE_LIMIT_ENABLED` | `true` | master switch for the middleware |
| `NI_ASSEMBLY_MCP_HTTP_RATE_LIMIT_PER_MINUTE` | `120` | requests per client IP per 60 s → `429` |
| `NI_ASSEMBLY_MCP_HTTP_GLOBAL_RATE_LIMIT_PER_MINUTE` | `1200` | all IPs combined; exceeding it trips the breaker |
| `NI_ASSEMBLY_MCP_HTTP_GLOBAL_COOLDOWN_SECONDS` | `30` | how long every request gets `503` once the breaker trips |
| `NI_ASSEMBLY_MCP_HTTP_TRUST_PROXY_HEADERS` | `false` | behind a load balancer, set `true` so the per-IP limit keys on `X-Forwarded-For` and not the proxy |
| `NI_ASSEMBLY_MCP_HTTP_FORWARDED_ALLOW_IPS` | `*` | which upstream hops may set forwarding headers (only read when the above is `true`) |

The per-IP limit only works if the server sees the real client IP. Behind any
proxy (see the warning at the top of this section) that means
`NI_ASSEMBLY_MCP_HTTP_TRUST_PROXY_HEADERS=true` — without it all traffic shares
the proxy's IP and only the global breaker still does anything useful. Quick
check after deploying: hit `/mcp` from two different machines faster than the
limit; if the *second* machine gets `429` immediately, proxy headers are not
being trusted and you need to fix the setting.

State is per-process. A single container (the zero-cost target) is fine as-is;
running several replicas would need a shared store and is not supported yet.

### Public web UI

A hosted `serve --http` deployment also serves two plain HTML pages on the same
app (same rate limits; no language model involved at any point):

| Path | What it is |
|---|---|
| `GET /` | a no-LLM forms UI — one HTML form per tool, results rendered as a table. Nothing you type is ever sent to a language model. |
| `GET /connect` | copy-paste config for pointing your *own* MCP client + LLM at this server's `/mcp/` endpoint. |

`/connect` renders the endpoint URL from `NI_ASSEMBLY_MCP_HTTP_PUBLIC_URL`
(default `http://localhost:8000` — set it to your real public origin, no trailing
`/mcp/`). It offers both a native streamable-HTTP snippet and an
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) stdio-bridge snippet.

For a client on the same machine as the container, [`claude_config.http.json`](claude_config.http.json)
is the native form:

```json
{
  "mcpServers": {
    "ni-assembly": {
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

Claude Code: `claude mcp add --transport http ni-assembly http://localhost:8000/mcp/`.

### Deploying to Oracle Cloud (Always Free)

Oracle Cloud's Always Free tier includes an Ampere A1 (ARM64) allowance — up to
4 OCPUs / 24 GB RAM / 200 GB block storage — which runs this stack comfortably at
no cost. The image is `linux/arm64`-clean: the `python:3.12-slim` base,
`pydantic-core` and `MarkupSafe` all publish aarch64 wheels, and Debian's bundled
SQLite has FTS5 enabled on ARM exactly as on x86. Nothing in the `Dockerfile` or
`docker-compose.yaml` changes for ARM.

> [!NOTE]
> **ARM capacity.** "Out of host capacity" errors when creating the A1 instance
> are common in busy regions — an Oracle capacity constraint, not a problem with
> this project. Retry (Oracle releases capacity continuously), try another
> availability domain or region, or script the retry. Once the instance exists it
> stays yours.

1. **Create the VM.** Follow Oracle's guide to launch an Always Free
   [Ampere A1 compute instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm)
   with an **Ubuntu 22.04/24.04 (aarch64)** image. In the VCN security list, add
   ingress rules for **TCP 80 and 443** from `0.0.0.0/0`.

2. **Open the host firewall.** Oracle's Ubuntu images ship restrictive `iptables`
   rules, so the security-list opening alone is not enough:

   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```

   Leave TCP 8000 closed — only Caddy (on the host) should reach the app.

3. **Install Docker + Compose** from Docker's
   [apt repository](https://docs.docker.com/engine/install/ubuntu/) (arm64 is
   first-class; the `docker compose` plugin is included). Add your user to the
   `docker` group and re-login.

4. **Install Caddy** from its
   [apt repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).

5. **Clone and configure.**

   ```bash
   git clone https://github.com/tobiasdavidson7-ui/ni-assembly-mcp.git
   cd ni-assembly-mcp
   printf 'NI_ASSEMBLY_MCP_HTTP_PUBLIC_URL=https://mcp.example.com\n' > .env
   ```

   The trust-proxy flag must also reach the container. Leaving
   `docker-compose.yaml` untouched, add a `docker-compose.override.yaml` beside
   it (Compose merges it automatically):

   ```yaml
   services:
     mcp-server:
       environment:
         - NI_ASSEMBLY_MCP_HTTP_TRUST_PROXY_HEADERS=true
   ```

   This is **required** behind Caddy — see the warning at the top of
   [Hosting over HTTP (Docker)](#hosting-over-http-docker). Leave
   `NI_ASSEMBLY_MCP_HTTP_FORWARDED_ALLOW_IPS` at its default (`*`): the app is
   only reachable through Caddy because port 8000 is firewalled.

6. **Build the index once, then start.**

   ```bash
   docker compose run --rm mcp-server index hansard --full
   docker compose run --rm mcp-server index questions --full   # optional
   docker compose up -d
   ```

   Both full builds are network-bound and run for roughly **15–30 minutes each**
   (Hansard walks ~2 400 bulk-XML files; questions crawls ~150 range windows).
   Progress is logged line by line — a long quiet-looking stretch is the crawl
   working through its politeness rate limit, not a hang. Only `index hansard`
   is needed for the Hansard search tools; `index questions` just upgrades
   `search_parliamentary_questions` from its live fallback to the local index.

7. **Point Caddy at it.** Copy [`deploy/Caddyfile`](deploy/Caddyfile) to
   `/etc/caddy/Caddyfile`, replace the domain and email placeholders, set the
   domain's A/AAAA records to the VM's public IP, then `sudo systemctl reload
   caddy`. Caddy obtains a Let's Encrypt certificate on the first request.

Verify: `curl https://mcp.example.com/healthz` returns `{"status": "ok"}`, and
`https://mcp.example.com/connect` shows your public endpoint.

**Persistent state needs no special handling.** Oracle's boot and block volumes
are ordinary block storage, so the `index` and `http-cache` named volumes in
`docker-compose.yaml` work as-is and survive `docker compose down` and reboots.
Back them up by snapshotting the block volume, or:

```bash
docker run --rm -v ni-assembly-mcp_index:/v -v "$PWD":/b alpine \
  tar czf /b/index-backup.tgz -C /v .
```

## Building the search index

Several tools read a local SQLite FTS5 index, built **offline** — the server
never builds it in a request. Two independent parts share one `index.db`:

| Command | Feeds | Source |
|---|---|---|
| `ni-assembly-mcp index hansard` | `search_debate_titles`, `search_contributions`, `find_relevant_contributors` | TheyWorkForYou bulk XML (1998→present) + a NI-API freshness top-up |
| `ni-assembly-mcp index questions` | a stemmed, BM25-ranked, answer-text-aware path for `search_parliamentary_questions` | the four `questions.asmx` range endpoints (2007→present) |

```bash
ni-assembly-mcp index hansard --full     # first build
ni-assembly-mcp index questions --full   # first build
ni-assembly-mcp index hansard            # incremental refresh (schedule it; cheap)
ni-assembly-mcp index questions          # incremental refresh (re-scans a trailing 60-day window)
ni-assembly-mcp index status             # row counts + last refresh for both
```

Hansard's speaker→`PersonId` mapping comes from mySociety's `parlparse`. The
questions index needs no mapping — `TablerPersonId` is the NI PersonId in every
source endpoint. The index lives at `NI_ASSEMBLY_MCP_INDEX_DB_PATH` (see
[Persistent paths](#persistent-paths)).

Both incremental refreshes are cheap (changed files / a 60-day window) — schedule
them, don't re-run `--full`.

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

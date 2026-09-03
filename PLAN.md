# NI Assembly MCP — Implementation Plan

Porting [`i-dot-ai/parliament-mcp`](https://github.com/i-dot-ai/parliament-mcp) (UK Parliament,
`developer.parliament.uk` REST APIs) to the **Northern Ireland Assembly Open Data API**
(`data.niassembly.gov.uk`, legacy ASP.NET `.asmx` web services).

Reference clone: [`reference/parliament-mcp/`](reference/parliament-mcp/) (pinned, read-only).

---

## 0. TL;DR of the gap

| | Parliament MCP (source) | NI Assembly (target) |
|---|---|---|
| Transport | RESTful JSON, `members-api.parliament.uk` etc. | One host `data.niassembly.gov.uk`, six `.asmx` files, GET `Operation_JSON?param=value` |
| Params | Path segments (`/api/Members/{id}/Biography`) + query | Query string only; no path params |
| Pagination | `take`/`skip` + `totalResults`/`TotalResultCount` | **None** — an operation returns its whole list |
| Auth | none | none |
| Envelope | HAL-ish `{value, links, items}` | `{ "<RootName>": { "<ItemName>": [ … ] } }` |
| Types | native JSON (ints, bools, ISO dates) | **everything is a string** (`"PersonId":"5797"`) |
| Search | **semantic** (Qdrant + Azure OpenAI embeddings) over Hansard + PQs | server-side **substring** on question *text* only (`GetQuestionsBySearchText`); Hansard has **no search at all** — per-day/per-report fetch + client-side match. See §6. |
| Format switch | `?format=json` | operation-name suffix `_JSON` (clean) / `_JSONP` (wrapped) |
| Missing `_JSON` | n/a | 7 operations are XML-only (see §3) — none block a core tool |

The single biggest design decision: **Parliament MCP's core value-add is embeddings-backed
search, and the NI API cannot provide it.** §6 pins down the concrete fallback for each of the
four business-search tools; §4 Phase 6 sets the order. Short version: questions get a real
server-side substring endpoint (ship now); Hansard gets nothing from the data API and needs a
local SQLite FTS5 index (Phase 6b) — **built from TheyWorkForYou's bulk XML** (1998→present,
no API key), not the data API's shorter, slower report walk (§6.6).

HTTPS is available and presents a valid certificate — prefer `https://data.niassembly.gov.uk`.

---

## 1. Tool map — Parliament MCP → NI Assembly

Parliament MCP exposes **13 tools** in 4 groups. Below, each is mapped to NI Assembly
operations across the six services (`members`, `organisations`, `plenary`, `hansard`,
`register`, `questions`). Legend: ✅ direct · 🟡 compose/degrade · 🔴 no equivalent · ➕ new NI-only tool.

### 1a. Members & elections

| PMCP tool | Verdict | NI Assembly mapping |
|---|---|---|
| `search_members` | 🟡 | No universal search endpoint. Adapter selects operation by which arg is set (same pattern as PMCP's own `get_election_results`):<br>• name → `members.asmx/GetAllCurrentMembersBySurnameSearch_JSON?searchText=` (min 3 chars, current only)<br>• constituency → `GetAllCurrentMembersByGivenConstituencyId_JSON?constituencyId=`<br>• party → `GetAllCurrentMembersByGivenPartyId_JSON?partyId=`<br>• as-of date → `GetAllMembersByGivenDate_JSON?specificDate=`<br>• none → `GetAllCurrentMembers_JSON` / `GetAllMembers_JSON` (current + historical)<br>Constituency/party args take **IDs**; add `GetAllConstituencies_JSON` + `organisations.asmx/GetPartiesListCurrent_JSON` as lookup helpers. **No postcode/location search** (no NI equivalent). `max_results` becomes client-side slicing. |
| `get_detailed_member_information` | 🟡 | Compose from:<br>• core record → `GetAllMembers_JSON` (or `…ByGivenDate`) filtered by `PersonId`<br>• `include_biography` / `include_committee_membership` / ministerial posts → `members.asmx/GetMemberRolesByPersonId_JSON?personId=` (one call yields every role: constituency affiliation, committee memberships, ministerial/APG roles, with start/end dates)<br>• `include_contact` → `GetMemberContactDetailsByPersonId_JSON?personId=`<br>• `include_registered_interests` → `register.asmx/GetAllRegisteredInterests_JSON` filtered client-side by `PersonId`<br>• `include_voting_record` → 🟡 no per-member endpoint; `plenary.asmx/GetDivisionMemberVoting_JSON` is per-division. Either omit or build by iterating recent divisions (expensive).<br>• `include_synopsis` / prose biography → 🔴 none. |
| `get_election_results` | 🔴 | The NI Assembly data API has **no elections dataset**. Election results live with EONI, not here. Drop the tool (or stub returning a pointer to `eoni.org.uk`). |

### 1b. Parliamentary structure

| PMCP tool | Verdict | NI Assembly mapping |
|---|---|---|
| `get_departments` | ✅ | `organisations.asmx/GetDepartmentListCurrent_JSON`. |
| `list_ministerial_roles` | 🟡 | Preferred: derive from `members.asmx/GetAllMemberRoles_JSON`, filter `RoleType`/`Role` to ministerial (avoids XML). `members.asmx/GetAllCurrentMinisters` exists but is **XML-only** (§3). Opposition posts: N/A in NI's power-sharing model. |
| `get_state_of_the_parties` | 🟡 | Aggregate `GetAllCurrentMembers_JSON` by `PartyName` (+ `GetPartiesListCurrent_JSON` for colours/abbreviations). Historical "as of `forDate`" → aggregate `GetAllMembersByGivenDate_JSON?specificDate=`. |

### 1c. Committees

| PMCP tool | Verdict | NI Assembly mapping |
|---|---|---|
| `list_all_committees` | 🟡 | Union of `organisations.asmx/GetCommitteesListCurrent_Standing_JSON` + `_Statutory_JSON` + `_AdHoc_JSON` + `_Other_JSON`. **"Former" committees unavailable**; the `house`/`joint` dimension does not apply. |
| `get_committee_details` | 🟡 | Thin. No rich committee endpoint in this API.<br>• members → `GetAllMemberRoles_JSON` filtered by `OrganisationId` + committee `RoleType`<br>• chairs → `members.asmx/GetAllCurrentCommitteeChairs` (**XML-only**, §3) or derive from roles<br>• meetings/agenda → `plenary.asmx/GetCommitteeAgendaItems*` (**XML-only**, §3)<br>• publications / oral evidence / written evidence / inquiries → 🔴 **not in this API at all**. |
| `get_committee_document` | 🔴 | No evidence/publications dataset. Drop. |

### 1d. Parliamentary business

| PMCP tool | Verdict | NI Assembly mapping |
|---|---|---|
| `search_parliamentary_questions` | 🟡 | Operation selector over `questions.asmx` (keyword / member / department / date-range) + client-side merge, filter, hydrate top-N via `GetQuestionDetails_JSON`, rank, slice. **Full concrete design + tool-description rewrite in §6.1.** Ships in Phase 4. |
| `search_debate_titles` | 🟡 | **Ships Phase 6a** (degraded output is still correct). `GetAllHansardReports_JSON` → per-report component fetch → keep `ComponentType=Header`; substring-match client-side. **Date range required**; persistent disk cache. Re-pointed at the index in 6c. See §6.2 / §6.5 pt 1. |
| `search_contributions` | 🟡 | **Ships Phase 6c only** — FTS5-backed, no labelled-degraded interim. Live component walk is the *indexer's* fetch path (§6.5 pt 1). See §6.3. |
| `find_relevant_contributors` | 🔴→🟡 | **Ships Phase 6c only.** Pure consumer of the FTS5 index; BM25-weighted contributor ranking. See §6.4 / §6.5 pt 1. |

### 1e. New NI-only tools (no PMCP analogue) ➕

| Tool | NI Assembly operations | Why |
|---|---|---|
| `search_plenary_business` | `plenary.asmx/GetPlenaryItemsTabledDate_JSON`, `GetPlenaryItemsTabledByMember_JSON`, `GetPlenaryItemsPlenaryDate_JSON`, `GetPlenaryDetails_JSON`, `GetPlenaryTablers_JSON`, `GetPlenaryAddressees_JSON` | Motions, statements, tabled business — core Assembly-monitoring need. |
| `get_business_diary` | `plenary.asmx/GetBusinessDiary_JSON?startDate=&endDate=` | Directly feeds a "Key Dates & Deadlines" view. |
| `get_divisions` | `plenary.asmx/GetVotesOnDivision_JSON`, `GetDivisionResult_JSON`, `GetDivisionMemberVoting_JSON` | How members voted — no PMCP equivalent exposed. |
| `get_motion_context` | `plenary.asmx/GetMotionAmendments_JSON`, `GetMotionBill_JSON`, `GetMotionPetitionOfConcern_JSON` | Amendments, linked bill, petition of concern (NI-specific mechanism). |
| `get_no_day_named_motions` | `plenary.asmx/GetNoDayNamedMotions_JSON` | Standing list of un-scheduled motions. |
| `get_registered_interests` | `register.asmx/GetAllRegisteredInterests_JSON` | Standalone + backs `get_detailed_member_information`. |
| `list_organisations` / `list_all_party_groups` | `organisations.asmx/GetOrganisationListCurrent_JSON`, `GetAllPartyGroupsListCurrent_JSON` | Reference data. |

### 1f. Coverage summary

- **Keep & remap:** `search_members`, `get_detailed_member_information`, `get_departments`, `list_ministerial_roles`, `get_state_of_the_parties`, `list_all_committees`, `search_parliamentary_questions`, `search_debate_titles`, `search_contributions` (9)
- **Degrade heavily:** `get_committee_details`, `find_relevant_contributors` (2)
- **Drop:** `get_election_results`, `get_committee_document` (2)
- **Add:** ~7 NI-only tools, mostly plenary/divisions/register

---

## 2. HTTP client — what is REST-specific and must be replaced

Source lives in [`reference/parliament-mcp/parliament_mcp/mcp_server/utils.py`](reference/parliament-mcp/parliament_mcp/mcp_server/utils.py)
and [`reference/parliament-mcp/parliament_mcp/qdrant_data_loaders.py`](reference/parliament-mcp/parliament_mcp/qdrant_data_loaders.py).

### 2a. Keep as-is (transport-agnostic)

| Component | Location | Note |
|---|---|---|
| `cached_limited_get()` | `qdrant_data_loaders.py:52` | hishel file cache + `aiolimiter` rate limit + `httpx` retries. Generic GET. **Keep.** Tighten: ASP.NET likely sends no `Cache-Control`, so rely on hishel's explicit `ttl` (already `timedelta(days=1)`); consider `force_cache`. **Fix the cache path** — it is `".cache/hishel"` relative to CWD; pin to absolute `HISHEL_CACHE_DIR` (default `~/.cache/ni-assembly-mcp/http`) so it survives restarts-from-elsewhere (§6.5 pt 3). Drop the Lambda `/tmp` branch. Add explicit 5xx backoff (transport `retries=` doesn't cover HTTP errors). |
| `sanitize_params()` | `utils.py:19` | Generic None/blank stripping. Keep. |
| `log_tool_call` | `utils.py:36` | Generic. Keep. |
| `gather_sections()` | `utils.py:223` | Generic concurrent-section helper for composite tools. Keep — heavily used by the composed `get_detailed_member_information`. |
| `recursive_remove_null_values()` | `utils.py:72` | Generic. Keep. |
| `markdownify` / `markitdown` deps | `pyproject.toml`, `committees.py` | Reuse for HTML/OpenXML answer bodies (§3). |

### 2b. Replace / rewrite

| REST-specific thing | Where | Replacement |
|---|---|---|
| Base-URL constants `MEMBERS_API_BASE_URL`, `COMMITTEES_API_BASE_URL` + `HANSARD_BASE_URL`, `PQS_BASE_URL` | `utils.py:15`, `qdrant_data_loaders.py:45` | Single `DATA_NIASSEMBLY_BASE_URL = "https://data.niassembly.gov.uk"`. |
| `request_members_api(endpoint, params)` / `request_committees_api(...)` — take a **REST path** with interpolated ids, append `?format=json` | `utils.py:130`, `utils.py:164` | New `niassembly_get(service, operation, **params)`:<br>1. `url = f"{BASE}/{service}.asmx/{operation}_JSON"`<br>2. `params` → query string (no path interpolation anywhere)<br>3. `cached_limited_get(url, params=params, headers={"User-Agent": ...})`<br>4. detect non-JSON error body (§2c)<br>5. `unwrap_niassembly(response.json())`<br>6. hand to a per-domain Pydantic model for type coercion |
| `?format=json` query param | `utils.py:137,171` | Delete — format is the `_JSON` suffix on the operation name. |
| `recursive_flatten_links_and_values()` — unwraps Parliament's `{value|items|links}` HAL envelope | `utils.py:84`, called at `utils.py:152` | Replace with `unwrap_niassembly(payload)`: drop the outer single-key dict → drop the inner single-key dict → if the remaining value is a dict (single-result call, e.g. `GetQuestionDetails`), wrap as `[dict]`; if list, return as-is. |
| `remap_values()` — maps `house` `1→Commons/2→Lords` | `utils.py:107`, called `utils.py:158` | Delete (NI is unicameral). Optionally add NI remaps (`PlenaryTypeID`, `RegisterCategoryId`) if useful. |
| **String-typed everything** | new | No PMCP equivalent — Parliament returns native types. Add a coercion layer: per-domain Pydantic models (extend the pattern already in [`models.py`](reference/parliament-mcp/parliament_mcp/models.py)) with validators for `int`/`bool`/`datetime` from strings, plus tolerant date parsing for `"2026-06-30T00:00:00+01:00"`. |
| `get_total_results()` + `take`/`skip` pagination loops | `qdrant_data_loaders.py:130`, `:296`, `:408`; `committees.py:219` (`Take=256`) | **Delete all pagination.** NI ops return the full list in one response. Replace with a single call + client-side `max_results` slice. Remove `TotalResultCount` / `totalResults` count-key logic. |
| `search_members` → one `/api/Members/Search` call with `sanitize_params(**locals())` | `members.py:93` | Rewrite as an operation selector (arg present → operation), since there is no combined search endpoint. |
| Path-based sub-resource fetches (`/api/Members/{id}/Synopsis`, `/Biography`, `/Contact`, `/Voting`, …) | `members.py:52-163` | Re-express as distinct operations with `personId=` query param; several collapse into the single `GetMemberRolesByPersonId_JSON`. |
| `response.raise_for_status()` as the only error check | `utils.py:149` etc. | Insufficient: NI/IIS can return **HTTP 200 with an XML/HTML error body**, or a 500 HTML page. Add: check `Content-Type`; attempt `response.json()`; on failure raise `NIAssemblyAPIError` with the first 500 chars. Keep `raise_for_status()` too. |
| `_JSONP` handling | not in PMCP | Not needed — always call `_JSON`. If ever forced onto `_JSONP`, strip leading `callbackName(` / trailing `);` before parsing (per the paddycarey gist). |
| Hard dependency on `openai` / Azure / `qdrant-client` / `fastembed` / `chonkie` for the *query path* | `api.py`, `qdrant_query_handler.py`, `settings.py` | For a keyword MVP, remove entirely. The four business-search tools call `niassembly_get` directly instead of `QdrantQueryHandler`. (Revisit in Phase 6.) |
| `settings.py` SSM/`boto3` + Azure OpenAI + Qdrant config + `AUTH_PROVIDER_PUBLIC_KEY` / `DISABLE_AUTH_SIGNATURE_VERIFICATION` | `settings.py` | Strip to: base URL, rate limit, concurrency cap, cache dir/TTL, transport (`stdio`/`http`), and — only under the `http` extra — host/port/allowed-hosts. Drop all `AUTH_*` (§5b). Keep the `pydantic-settings` structure. |
| `HTTP_MAX_RATE_PER_SECOND: float = 10` (tuned for `parliament.uk`'s modern API) | `settings.py:129` | Legacy IIS/ASP.NET backend — lower the default hard: `HTTP_MAX_RATE_PER_SECOND = 3`, add `HTTP_MAX_CONCURRENCY = 4` (an `asyncio.Semaphore` around `cached_limited_get`), keep `httpx` retries with exponential backoff, and lean on hishel's 1-day `ttl` so repeat runs barely touch the origin. The composed tools (`get_detailed_member_information`, the questions merge) fan out several ops at once — the semaphore matters more than the rate here. |

### 2c. New helpers to add

- `niassembly_get(service, operation, **params) -> list[dict]` (JSON path, described above)
- `unwrap_niassembly(payload) -> list[dict]`
- `niassembly_get_xml(service, operation, **params) -> dict` — `httpx` GET + `xmltodict.parse` (new dep) for §3 operations
- `niassembly_get_answer_html(document_id) -> str` — GET `GetWrittenAnswerHtml`, parse the single `<string>` element, HTML-unescape, `markdownify`
- `NIAssemblyAPIError` exception
- param-name verification: casing is `lowerCamel` (`constituencyId`, `partyId`, `searchText`, `personId`, `documentId`, `specificDate`, `startDate`, `endDate`, `reportId`, `plenaryDate`, `departmentId`). **Verify each against `…/<service>.asmx?op=<Operation>` help page during implementation** — the WSDL is the source of truth.

---

## 3. Operations with **no `_JSON` variant** — need SOAP/XML handling

| Service | Operation | Returns | Needed for | Mitigation |
|---|---|---|---|---|
| `members.asmx` | `GetAllCurrentMinisters` | XML | `list_ministerial_roles` | **Avoid** — derive ministers from `GetAllMemberRoles_JSON` (`RoleType`/`Role`). XML fallback only if role data proves insufficient. |
| `members.asmx` | `GetAllCurrentCommitteeChairs` | XML | `get_committee_details` (chairs), committee-chair lookups | **Avoid** — derive from `GetAllMemberRoles_JSON` filtered to chair roles. |
| `questions.asmx` | `GetWrittenAnswerHtml` | `<string xmlns="…">…&lt;html&gt;…</string>` (XML-wrapped, entity-encoded HTML) | Full / formatted written-answer body when `GetQuestionDetails_JSON` answer text is truncated or needs layout | `niassembly_get_answer_html()`: parse `<string>`, `html.unescape`, `markdownify` (dep already present). |
| `questions.asmx` | `GetWrittenAnswerOpenXml` | OpenXML (WordprocessingML) | Rare — answers distributed as documents | `markitdown` (dep already present) converts the OpenXML blob. Low priority. |
| `plenary.asmx` | `GetCommitteeAgendaItemsCommitteeMeetingDate` | XML | committee-meeting tooling (optional) | Defer. When built, `xmltodict`. |
| `plenary.asmx` | `GetCommitteeAgendaItemsCommitteeMeetingId` | XML | ″ | Defer. |
| `plenary.asmx` | `GetCommitteeAgendaItemsMeetingDate` | XML | ″ | Defer. |

Notes:
- **Re-verified 2026-09-03** against the six live `.asmx` service-description pages + sample `_JSON` calls. `organisations` (8/8), `hansard` (4/4) and `register` (1/1) expose `_JSON` for **every** operation. `members` is XML-only for exactly the two rows above; `questions` for two; `plenary` for three. No other gaps.
- Every `*_JSON` operation also has a plain-XML sibling; XML handling is **only mandatory** for the rows above.
- Even `_JSON` operations can emit an XML/HTML **error** document — the §2c content-type guard covers this.
- Add `xmltodict` to `pyproject.toml` (Parliament MCP does not depend on it).
- No operation requires composing a SOAP **request** envelope — all are GET-accessible. "SOAP handling" here means parsing SOAP/ASMX **responses** only.

---

## 4. Implementation order

### Phase 0 — Scaffold (fork & strip)
- Copy repo layout from `reference/parliament-mcp/`. Keep: `FastMCP` server (`mcp_server/main.py`, `api.py`), `cli.py` shape, `cached_limited_get`, `log_tool_call`, `sanitize_params`, `gather_sections`, `markdownify`/`markitdown`, `pydantic-settings`.
- Remove: `qdrant_*`, `openai_helpers`, `fastembed`, `chonkie`, Azure/SSM/`boto3` settings, `terraform/`, `Dockerfile.lambda`, `lambda_handler.py`, `scripts/es_to_qdrant_etl.py`, the Qdrant service in `docker-compose.yaml`, `shared_utils/auth.py` + `pyjwt` + `cryptography` (§5b — dead code, imported nowhere), `sentry-sdk` + `sentry_sdk.init` in `api.py` (optional; keep only if you have a Sentry DSN).
- Add stdio transport as the default (§5b): `cli.py serve --http` opts into the current uvicorn/streamable-HTTP path; bare `serve` runs `mcp_server.run(transport="stdio")`. This deletes the need for `MCP_ALLOWED_HOSTS`, the session-cleanup task, and `fastapi`/`uvicorn` in the default install path.
- Rename package `parliament_mcp` → `ni_assembly_mcp`. Update `pyproject.toml` (`+xmltodict`; `-qdrant-client`, `-openai`, `-fastembed`, `-chonkie`, `-boto3`, `-pyjwt`, `-cryptography`, `-aiohttp` if unused; move `fastapi`/`uvicorn`/`sentry-sdk` to an optional `[project.optional-dependencies] http` extra). Keep the i.AI MIT copyright line, append your own (§5d).
- Save the source endpoint list to `reference/ni-assembly-endpoints.md` (§5a) so Appendix A can be diffed against it.

### Phase 1 — HTTP adapter (foundation, blocks everything)
- `ni_assembly_mcp/niassembly_client.py`: `niassembly_get`, `unwrap_niassembly`, `NIAssemblyAPIError`, JSON-error guard.
- `ni_assembly_mcp/models.py`: base model with string→`int`/`bool`/`datetime` coercion; tolerant date parser.
- Tests: record real responses as fixtures (`tests/fixtures/*.json`), assert unwrap + coercion. One live smoke test behind `--with-integration`.
- Eval/benchmark harness (§5c): retarget `tests/mcp_server/test_agent.py` + `test_benchmarks.py` prompts/assertions to NI tools & data, or replace with a lightweight `list_tools()` contract test. Keep the `openai-agents` eval as an opt-in `@pytest.mark.integration` dev-only path — don't add `openai` to runtime deps.

### Phase 2 — Reference & list domains (quick wins, validate the adapter)
- `get_departments` ✅, `list_organisations` ➕, `list_all_party_groups` ➕, `get_parties` (→ `get_state_of_the_parties` groundwork)
- `list_all_committees` 🟡 (4-op union)
- `search_members` 🟡 (operation selector) + `get_constituencies` helper

### Phase 3 — Member detail, roles, register
- `get_detailed_member_information` 🟡 (compose roles + contact + interests via `gather_sections`)
- `get_registered_interests` ➕
- `list_ministerial_roles` 🟡 (from `GetAllMemberRoles_JSON`)
- `get_state_of_the_parties` 🟡 (aggregate current / by-date)

### Phase 4 — Questions (highest value for Voice for Change "Assembly Monitoring")
- `search_parliamentary_questions` 🟡 — operation selector + client-side merge/filter/hydrate/rank/slice, per **§6.1**. Ship the revised keyword-only tool description.
- `get_question_details` ➕ (`GetQuestionDetails_JSON` — the rich record: `AnswerPlainText`, `AnsweredOnDate`, tabler/minister PersonIds)
- **4b:** `niassembly_get_answer_html()` — full written-answer body → markdown (§3). Note `GetQuestionDetails_JSON` already returns `AnswerHtml`/`AnswerPlainText`, so 4b is only for cases where the inline HTML is truncated.

### Phase 5 — Plenary & divisions (new capability, high value, self-contained)
- `search_plenary_business` ➕, `get_business_diary` ➕
- `get_divisions` ➕, `get_motion_context` ➕, `get_no_day_named_motions` ➕

### Phase 6 — Hansard (sequencing is fixed, not a "decide later" — see §6.5)

**Phase 6a — `get_hansard_reports` + `search_debate_titles`** (no index needed)
- `get_hansard_reports` ➕ — thin wrapper over `GetAllHansardReports_JSON`.
- `search_debate_titles` 🟡 — bounded live walk (§6.2), **`date_from`/`date_to` required**, backed by the
  **persistent on-disk HTTP cache** (§6.5 pt 3). Ships with the §6.2 description rewrite.
- Define the `HansardSearchBackend` protocol here (query in → ranked components out) so 6b swaps
  cleanly. `search_debate_titles` gets a `LiveWalkBackend`; 6c re-points it at the index, no
  interface change.
- **Optional shortcut (§6.6):** now that TWFY de-risks ingestion, 6a may be skipped and
  `search_debate_titles` shipped straight from the 6b index (headings are already parsed out of the
  scrape). Do 6a standalone only if Phase 6b is not going to land soon.

**Phase 6b — Hansard + PQ FTS5 index + ingestion job** (prerequisite for the two weak tools)
- **Ingestion source decided in §6.6:** Hansard from **TheyWorkForYou bulk XML**
  (`theyworkforyou.com/pwdata/scrapedxml/ni/*.xml`, 1998→present) + `parlparse/members/people.json`
  for `twfy_person_id` → NI `PersonId`; **not** the data API's per-report component walk. PQs from
  the 4 range endpoints as before.
- `ni-assembly-mcp index {hansard,questions}` CLI (mirrors upstream `cli.py load-data`): offline,
  resumable, checkpointed by scrape-file date / PQ date window → SQLite FTS5 (Porter stemming +
  BM25 + phrase/NEAR). **Never built lazily on first query** (§6.5 pt 2).
- **Freshness top-up:** after the bulk load, pull the last ~30 days from
  `GetHansardComponentsByPlenaryDate_JSON` to cover TWFY's multi-day scrape lag.
- Incremental refresh: startup + ≤ once/24 h opportunistic background job for stdio; real cron for
  the `http` extra (§6.5 pt 2). Hansard refresh = new `scrapedxml/ni/` files since last run + the
  30-day API top-up; PQ refresh = trailing 30-day window.
- Once the PQ index exists, `search_parliamentary_questions` (Phase 4) gains an index-backed path —
  stemmed/BM25 ranking **and** answer-text search (`AnswerPlainText`), which the live
  `GetQuestionsBySearchText` endpoint cannot do. Keep the live path as the no-index fallback behind
  the same backend protocol.

**Phase 6c — `search_contributions` + `find_relevant_contributors`** (gated on 6b)
- Both are **FTS5-backed only** — no live-walk version ships, even labelled (§6.5 pt 1).
- `Fts5Backend` implements `HansardSearchBackend`; `find_relevant_contributors` is a pure consumer
  (group FTS5 hits by PersonId, rank by BM25-sum × hit-count).
- Ship with the §6.3 / §6.4 description rewrites.

**Search-backend options behind the protocol** (unchanged): (a) live walk — 6a only; **(b) SQLite
FTS5 — the target, 6b**; (c) Qdrant + embeddings — only if lexical recall proves insufficient,
re-adds Azure OpenAI + a container.

### Phase 7 — XML-only extras (optional)
- `xmltodict` helper + committee-agenda tools if committee-meeting monitoring is wanted.
- `get_committee_events` replacement (§5f): PMCP's committee events/calendar has no direct NI JSON source. Compose a partial from `plenary.asmx/GetBusinessDiary_JSON` filtered to committee rows, and add the three `GetCommitteeAgendaItems*` XML operations here for agendas. Ships disabled until this phase — call that out in the README so the capability gap is explicit, not silent.

### Phase 8 — Packaging
- `claude_config.json` (stdio command form — `ni-assembly-mcp serve`, no `mcp-remote` proxy), README (incl. §5d licence/attribution, the §5f capability-gap note, and the §6 tool-description rewrites), `docker-compose.yaml` (only needed for the `http` extra or persistent volumes; the default stdio server needs no compose), deploy target.
- Persistent paths documented + volume-mounted: `HISHEL_CACHE_DIR` (§6.5 pt 3) and `INDEX_DB_PATH` (§6.5 pt 2).
- Index bootstrap + refresh: README covers `ni-assembly-mcp index {hansard,questions}` for first build; ship an OS-scheduler snippet (Task Scheduler / launchd / systemd timer) for local always-on, and a CronJob/cron-container for the `http` extra (§6.5 pt 2).
- `NOTICE` file + retained `LICENSE` (§5d).

### Dependency graph
```
Phase 0 ─► Phase 1 ─► Phase 2 ─► Phase 3
                    └► Phase 4 ─► 4b
                    └► Phase 5
                    └► Phase 6a ─► 6b (index + ingestion job) ─► 6c ─► Phase 7
                                                                Phase 8 (after any shippable subset)
```
`search_contributions` / `find_relevant_contributors` (6c) do **not** ship before 6b.
`search_debate_titles` (6a) ships without the index.

---

## 5. Cross-cutting concerns (packaging, licence, deployment)

Not part of the tool/transport port, but each blocks a clean release.

### 5a. Persist the source endpoint list

The endpoint list this plan was built from lives only in the originating chat. Appendix A is a
reconstruction verified against the live `.asmx` service-description pages on 2026-09-03.

- **Action:** save the original list verbatim to `reference/ni-assembly-endpoints.md`.
- Diff Appendix A against it; reconcile any operation that appears in one but not the other.
- Treat the live `…/<service>.asmx?op=<Operation>` help pages (and the WSDL) as the tie-breaker —
  operation names and param casing there are authoritative.

### 5b. Auth — drop it

`parliament_mcp/shared_utils/auth.py` (Keycloak JWT validation, `AUTH_PROVIDER_PUBLIC_KEY`,
`is_authorised_user`) is **imported nowhere in the codebase** — it's wired in at the infra layer for
i.AI's hosted deployment only.

- Delete `shared_utils/auth.py`; drop `pyjwt` + `cryptography` deps and the `AUTH_*` settings.
- NI Assembly's API is unauthenticated and the target is a single-user local MCP → **no auth layer**.
- **Transport decision (related):** upstream runs HTTP-only (`streamable_http_app()` on uvicorn) with
  a DNS-rebinding guard (`MCP_ALLOWED_HOSTS`) and a session-cleanup task. For local use, default to
  **stdio** (`mcp_server.run(transport="stdio")`) — no network surface, no `MCP_ALLOWED_HOSTS`, no
  session GC, no `fastapi`/`uvicorn`. Keep the HTTP path behind a `serve --http` flag + `[http]` extra
  for anyone who wants to host it; that's where a reverse-proxy/auth story would be re-added.

### 5c. Eval & benchmark harness

`tests/mcp_server/test_agent.py` and `test_benchmarks.py` drive the server with `openai-agents`
(OpenAI API) and assert on tool selection / latency. All prompts and expected tool names are
UK-Parliament-specific (`"current Chancellor"` → `list_ministerial_roles`, member id `5239`, etc.).

- **Keep** the harness — it's the only end-to-end check that tool descriptions steer a model correctly.
- Retarget: NI prompts (`"current Health Minister"`, a real `PersonId`), NI tool-name assertions,
  drop the Qdrant-container fixtures from `conftest.py`.
- Gate behind `@pytest.mark.integration`; `openai-agents` stays a **dev** dependency, never runtime.
- Minimum viable substitute if the OpenAI dependency is unwanted: a `list_tools()` contract test that
  pins tool names + JSON schemas, plus the Phase 1 fixture tests.

### 5d. Licence & attribution

Upstream is **MIT, © 2025 i.AI** (`LICENSE`). This is a derivative work.

- **Code:** keep `LICENSE` and the i.AI copyright line; append `Copyright (c) 2026 <you>`. Add a
  `NOTICE` recording the port from `i-dot-ai/parliament-mcp` and the upstream commit hash.
- **Ship no data in the repo/package.** The `index` command and all tools fetch data at the user's
  site at runtime. That keeps us out of every redistribution obligation below — we distribute code
  that *retrieves* the data, not the data.
- **NI Assembly data API** (`data.niassembly.gov.uk`): has its **own terms** — do **not** assume UK
  OGL. Read the copyright/reuse statement on `data.niassembly.gov.uk` / `niassembly.gov.uk` before
  release; record the licence + required attribution in the README.
- **TheyWorkForYou / mySociety** (§6.6 — `scrapedxml/ni/` + `parlparse/members/people.json`). Actual
  terms, from <https://www.theyworkforyou.com/api/terms> (verified 2026-09-03):
  - **parlparse *software*** — AGPL-3.0 (`parlparse/LICENSE.txt`, "the software in this directory").
    We consume its published `people.json` *output*, don't run or redistribute its code → AGPL does
    not reach this project. (An `http`-extra deployment that modifies and serves TWFY code would be
    a different question; we don't.)
  - **TWFY "own data" — lists of members, constituencies, and the speaker↔person identifiers
    (`people.json`, and the `person_id` attributes inside the scrape)** — **CC BY-SA 2.5**.
    Attribution **and ShareAlike**. This is the licence that governs our PersonId↔TWFY-id mapping
    and per-speaker attribution.
  - **The debate text itself ("parliamentary material")** — reusable under the **Open Parliament
    Licence** per TWFY's terms; for NI Assembly proceedings the Assembly's own Official Report
    reuse terms also apply — confirm alongside the data-API terms above.
  - **Required attribution string** (TWFY's wording): *"Data service provided by TheyWorkForYou"*
    with a link to <https://www.theyworkforyou.com>, **plus** the attributions required by the
    underlying licences (OPL / NI Assembly for the text). Put this in the README and in a
    `--version`/`about` output.
  - **ShareAlike consequence:** if anyone redistributes a **pre-built index** (it embeds the
    TWFY-derived identifiers/attribution), that redistribution must be CC BY-SA 2.5-compatible.
    **Recommendation: distribute only the builder, never a built `index.db`.** Document this so
    downstream users don't ship indexes casually.
- **`getHansard` API** additionally forbids storing/redistributing API responses — moot, we don't
  use it (§6.6), but note it so no one wires it in later without re-checking.
- README must state it is **unaffiliated with the Northern Ireland Assembly and with mySociety /
  TheyWorkForYou**.

### 5e. Concrete rate-limit / politeness settings

See §2b new row. Summary: `HTTP_MAX_RATE_PER_SECOND = 3`, `HTTP_MAX_CONCURRENCY = 4` (semaphore),
explicit exponential backoff on **5xx/timeout** (upstream's `AsyncHTTPTransport(retries=3)` covers
only transport errors), hishel `ttl = 1 day` on a **pinned absolute cache path** (§6.5 pt 3), a
descriptive `User-Agent` with a contact URL. The legacy backend is the constraint, not our
throughput needs. The **bulk indexer** (Phase 6b) runs on a separate, slower limiter — 1–2 req/s,
concurrency 2–3, `Retry-After` honoured, circuit-break after N consecutive failures, resumable from
a checkpoint (§6.5 pt 4).

### 5f. Committee events / calendar capability

PMCP's `get_committee_details` surfaces `upcoming_events` and there's a `get_committee_events`-style
path; NI has **no JSON committee-calendar operation**. This capability silently disappears in Phases
2–3 unless flagged.

- Phase 3 `get_committee_details`: return committee membership/roles only; document that
  meetings/agenda/events are **not populated yet**.
- Phase 7: partial restore via `plenary.asmx/GetBusinessDiary_JSON` (filter to committee rows) for
  scheduling + the `GetCommitteeAgendaItems*` XML operations for agendas.
- Call the gap out explicitly in the README's tool table (a "⚠ partial" column), not just in code.

---

## 6. Business-search fallback — concrete design

Semantic search is gone. This section pins down exactly what each of the four business-search
tools does instead. **All endpoint behaviour below verified against live calls 2026-09-03.**

### 6.0 What the NI API actually gives us

| Endpoint | Server-side params | Scope | Record shape |
|---|---|---|---|
| `questions/GetQuestionsBySearchText_JSON` | `searchText` (≥3 chars) — **case-insensitive substring on `QuestionText` only**, answers *not* searched | ALL matches, all-time | **lean**: `DocumentId, Reference, TabledDate, QuestionText, QOralAnswerRequested, QuestionDetails`(url) — **no member/party/department/answer** |
| `questions/GetQuestionsByMember_JSON` | `personId` | all Qs by member, all-time | mid: `+ TablerPersonId, DepartmentId, DepartmentName, MinisterPersonId` |
| `questions/GetQuestionsByDepartment_JSON` | `departmentId` | all Qs to dept, all-time | mid |
| `questions/GetQuestionsForWrittenAnswer_TabledInRange_JSON` (+ `_AnsweredInRange`, + 2× `GetQuestionsForOralAnswer_*`) | `startDate`, `endDate` (dateTime) | Qs tabled/answered in window | fuller: `+ TablerName, TablerTitle, TablerAffiliationId, TablerPersonId, MinisterTitle, MinisterPersonId, Department, DepartmentID, AnswerByDate, PriorityRequest` — still **no `AnsweredOnDate`, no answer text** |
| `questions/GetQuestionDetails_JSON` | `documentId` | one question | **rich**: `+ AnsweredOnDate, AnswerPlainText, AnswerHtml, AnswerOpenXml` |
| `hansard/GetAllHansardReports_JSON` | none | ~800 sitting-day stubs (≈ back to 2014; 2017–2020 gap) | `ReportDocId, PlenaryDate, PlenarySessionId, PlenarySessionName` — **no titles** |
| `hansard/GetHansardComponentsByPlenaryDate_JSON` | `plenaryDate` (one date) | ~140 components/day | `ComponentId, ComponentText, ComponentType, ComponentTypeId, ParentComponentId, RelatedItemId, ComponentHeaderId` |
| `hansard/GetHansardComponentsByReportId_JSON` | `reportId` | components for one report | same |
| `hansard/GetHansardComponentsByReportIdAndPersonId_JSON` | `reportId` + `personId` | one report, one speaker | same |

Hard limits:
- **No `questions` endpoint combines filters** — keyword XOR member XOR department XOR date-range, never together.
- **No Hansard keyword or global-search endpoint** — every Hansard query is scoped to one day / one report.
- `ComponentType` seen: `Header` (`ComponentTypeId=0`), spoken (`ComponentTypeId=-1`), `Speaker (MlaName)`, `Procedure Line`, `Time`, `Quote`. Speaker rows carry `RelatedItemId`→PersonId; `ParentComponentId` chains a speech to its `Header`.
- The three question endpoints return **different field sets** — the keyword one is the leanest, which is what forces hydration (6.1).

### 6.1 `search_parliamentary_questions` — viable now

1. **Candidates** (operation selector):
   - `query` → `GetQuestionsBySearchText_JSON?searchText=<query>`
   - no `query`, `asking_member_id` → `GetQuestionsByMember_JSON?personId=`
   - no `query`, `answering_body_name` → resolve via `GetDepartmentListCurrent_JSON` → `GetQuestionsByDepartment_JSON?departmentId=`
   - no `query`, dates only → 4 range endpoints, merge + de-dupe on `DocumentId`
   - nothing → last-90-days range window
2. **Server-side filtering:** only the one dimension that selected the endpoint. `searchText` = case-insensitive substring on question text; answers not searched.
3. **Client-side:** `date_from/to` vs `TabledDate`; `asking_member_id` vs `TablerPersonId`; `answering_body_name` vs `Department`; `party` via a cached `GetAllMembers_JSON` person→party map.
   *Because `GetQuestionsBySearchText` records omit `TablerPersonId`/`Department`,* applying member/party/department filters to a keyword search requires hydrating candidates with `GetQuestionDetails_JSON`. Flow: substring-score → take top `max_results×3` → hydrate those → apply remaining filters → trim.
   Matching: (a) # distinct query tokens in `QuestionText`, (b) whole-phrase bonus, (c) tie-break `TabledDate` desc. No stemming, no synonyms.
4. **Performance:** server-side, so payload ∝ match count. Broad term (`education`, `health`) returned **> 10 MB** in testing; narrow term (`peatland`) 87 rows. Mitigate: stream-parse, and when row count > ~2 000 return a warning asking for a narrower term or date range; hydrate only the top slice. No full-corpus download.
5. **Relevance vs PMCP: materially worse.** PMCP = dense-embedding + BM25 over question **and answer** text. Here = literal substring over question text only, so `"school funding"` misses `"budget for education"`; ranking is token-overlap/recency, not learned. **Tool-description change:** "Keyword (substring) search over question *text* only — not answers. Use short literal terms. Filters (member, party, department, date) are applied after the search; combining a very broad term with filters may truncate. Not semantic — synonyms/paraphrases will be missed."

### 6.2 `search_debate_titles` — date-window tool

1. **Candidates:** `GetAllHansardReports_JSON` → for each report whose `PlenaryDate` is in range, `GetHansardComponentsByReportId_JSON`; keep `ComponentType=Header` rows.
2. **Server-side:** none. Report list has no date param; per-report fetch is the only scoping. `house` dropped (unicameral).
3. **Client-side:** substring / token-overlap of `query` vs `Header` `ComponentText`; date from parent report; order by date desc.
4. **Performance:** there is **no header-only endpoint** — you fetch every component (~140/report, tens of KB) to extract headers. Unbounded = **~800 fetches ≈ 4–5 min cold**, then served from the **persistent on-disk HTTP cache** for ~24 h (§6.5 pt 3 — one-off per TTL window, not per restart). Bounded to a quarter (~20 sitting days) ≈ 6–8 s. **`date_from`/`date_to` required** (default last ~6 months). Phase 6c re-points this at the FTS5 index and the cold walk disappears.
5. **Relevance vs PMCP: worse, least badly.** Section headings are short and formulaic; substring works for known topics, fails on paraphrase. **Tool-description change:** "Keyword match on debate/section headings within a date range; not semantic; ordered by date."

### 6.3 `search_contributions` — index-only (Phase 6c)

1. **Query path (6c):** FTS5 lookup over the contributions table; optional `member_id` / `date_from` / `date_to` as SQL filters; BM25 ranking.
2. **Indexer fetch path (6b, per §6.6):** parse TWFY `scrapedxml/ni/*.xml` — each `<speech person_id speakername time>` under `<major-heading>`/`<minor-heading>`; map `person_id` → NI `PersonId` via `people.json`; store `(speech_id, date, major_heading, minor_heading, person_id, twfy_person_id, speakername, text)` in FTS5. Plus a rolling 30-day top-up from `GetHansardComponentsByPlenaryDate_JSON` for the scrape lag.
3. **Historical member mapping gap:** ~30% all-time / 96% current-mandate PersonId coverage (§6.6). Unmapped speakers keep `speakername` + `twfy_person_id` and fall back to name match; member-filtered queries on old debates may miss.
4. **Performance:** full speech text — ~100–500 KB JSON/day. **Full history ≈ 800 calls ≈ 100–400 MB parsed — not viable per query.** This is why it does not ship before the index (§6.5 pt 1): the live walk is the *indexer's* fetch path only. Query-time = FTS5 lookup, sub-second.
5. **Relevance vs PMCP: much worse without the index; acceptable with it.** Embeddings served this best (what was *said about* a concept across debates); FTS5 (Porter stemming + BM25 + phrase/NEAR) recovers most lexical recall and gives real ranking. **Tool-description (6c):** "Full-text search (stemmed, BM25-ranked) over spoken contributions from 1998 to present; requires the local index; date filters optional; still lexical, not semantic — pure-synonym queries may miss. The `member_id` filter is reliable for debates from ~2022 onward; for earlier debates some speakers are matched by name only and a `member_id` filter may under-return."

### 6.4 `find_relevant_contributors` — index-only (Phase 6c)

1. **Candidates:** FTS5 query over the contributions table (`query` mandatory).
2. **Server-side:** n/a — reads the local index.
3. **Logic:** FTS5 match → group hits by PersonId → score contributor = Σ(BM25) × (# matching contributions) → top `num_contributors`, each with top `num_contributions` snippets.
4. **Performance:** index lookup + in-memory group-by. No live fetch at query time.
5. **Relevance vs PMCP: still weaker** (BM25 aggregate vs semantic aggregate — biased toward members who used the exact terms), but defensible. **Never ships pre-index** (§6.5 pt 1); the live-walk version ranked by raw hit-count is misleading enough that a labelled-degraded interim was rejected. **Tool-description (6c):** "Members ranked by how much they spoke on the query terms (BM25-weighted) in the given period; lexical, not semantic. Per-member attribution is complete for ~2022 onward; for earlier periods members whose identity could not be resolved are grouped under their spoken name or omitted from the ranking, so historical results undercount."

### 6.5 Sequencing & infrastructure decisions (locked before implementation)

These are the four decisions that turn §6 from a quality discussion into buildable phases.
They are **decided here**, not deferred.

#### pt 1 — FTS5 sequencing: build-first for the two weak tools, ship-now for the one that's OK

Split the three Hansard tools by whether the degraded version is honestly usable:

| Tool | Degraded (live-walk) version | Decision |
|---|---|---|
| `search_debate_titles` | Headings are short/formulaic; a date-bounded substring match is genuinely useful. | **Ship in Phase 6a** without the index. Behind the `HansardSearchBackend` protocol so 6c re-points it at FTS5 with no interface change. |
| `search_contributions` | Substring over a date window: poor recall, no ranking, heavy fetch. Known-bad. | **Do not ship until 6b.** No labelled-degraded interim version — the live walk is used *only* as the indexer's fetch path, never as a query path. |
| `find_relevant_contributors` | Ranks by literal-hit frequency → biased to frequent speakers. Worse than useless for its stated purpose. | **Do not ship until 6b.** Pure consumer of the FTS5 index. |

So the ingestion pipeline is **Phase 6b, a hard prerequisite for Phase 6c** — not a "fast follow".
The plan's phase numbers now reflect this (6a → 6b → 6c). "Later" is not on the table for 6b; it
gates two tools.

Rejected alternative (ship all three labelled-degraded, swap backend later): the interface is
already swap-safe, so the only thing an interim `search_contributions` buys is a tool that returns
misleading results for weeks. Not worth it. `search_debate_titles` is the exception because its
degraded output is *correct*, just slower on cold cache.

#### pt 2 — the index is built by an offline job, never lazily on first query

- **`ni-assembly-mcp index {hansard,questions}`** — a dedicated CLI subcommand (mirrors upstream
  `cli.py`'s `load-data`). Runs the TWFY bulk-XML load (§6.6) + 30-day API top-up for Hansard, and
  the four PQ range endpoints for questions, into SQLite FTS5. **The MCP server never triggers a
  full build in a request path.** If a query arrives and the index is missing/empty, the tool
  returns `"Index not built — run `ni-assembly-mcp index hansard`"`, not a multi-minute hang.
- **Resumable:** checkpoint a cursor (last processed `scrapedxml` file date for Hansard, `endDate`
  for the PQ windows) so a killed run resumes instead of restarting. Re-runs are cheap anyway — the
  HTTP cache (pt 3) absorbs unchanged files.
- **Incremental refresh:**
  - *Hansard:* fetch `GetAllHansardReports_JSON` (one call), diff `ReportDocId` against the index,
    fetch only new/changed reports.
  - *Questions:* re-run the range endpoints for a trailing window (last ~30 days) to catch
    newly-tabled questions **and** answers that landed after the question was first indexed
    (`AnsweredOnDate` / `AnswerPlainText` fill in late).
- **Where the refresh runs:**
  - *Default (stdio / local):* no daemon exists, so — (i) on server start, and (ii) at most once
    per 24 h — spawn a **non-blocking background refresh task**; queries run against the current
    (possibly slightly stale) index meanwhile. Document a manual `index` invocation and an OS
    scheduler entry (Task Scheduler / launchd / systemd timer) for always-on setups.
  - *`http` extra:* a real scheduled job (cron container / k8s CronJob) running `index` daily.
- Index lives at a stable, configurable path — `INDEX_DB_PATH`, default
  `~/.local/share/ni-assembly-mcp/index.db` (XDG). Docker: a named volume.

#### pt 3 — HTTP cache is on disk and survives restarts (fix the path)

Upstream `cached_limited_get` already uses `hishel.AsyncFileStorage` with `ttl = 1 day` — it **is**
disk-backed and restart-safe. Two fixes needed:

- The base path is `".cache/hishel"` **relative to the process CWD** — fragile (a restart from a
  different directory misses the whole cache). Pin it: `HISHEL_CACHE_DIR`, default
  `~/.cache/ni-assembly-mcp/http`. Drop the `AWS_LAMBDA_*` `/tmp` branch (§2b).
- Docker: mount the cache dir as a volume, or a redeploy pays the cold walk again.

With this, `search_debate_titles`'s 4–5 min cold cost is a genuine one-off per ~24 h TTL window,
not per restart. (Once the 6b index exists, `search_debate_titles` reads the index and the cold
walk disappears entirely.)

#### pt 4 — politeness (bulk pull now mostly hits TheyWorkForYou, not the NI server)

Per §6.6 the Hansard bulk load reads **static files from `theyworkforyou.com`** (a robust host —
standard rate limiting suffices) and the NI server (`data.niassembly.gov.uk`, legacy IIS/ASP.NET)
takes only the ~30-day freshness top-up + the PQ range calls. Beyond the §2b / §5e client defaults
(`HTTP_MAX_RATE_PER_SECOND = 3`, `HTTP_MAX_CONCURRENCY = 4`), the **indexer runs gentler** and
defensively **against both hosts**:

- Dedicated slower limiter for bulk ingestion: **1–2 req/s, concurrency 2–3** (per host).
- **Exponential backoff with jitter on 5xx / timeout / connection reset** — upstream's
  `httpx.AsyncHTTPTransport(retries=3)` only retries transport errors, *not* HTTP 5xx, so this is
  added explicitly (e.g. `tenacity`, already an upstream dep). Honour `Retry-After`.
- **Circuit-break:** abort the run (don't thrash) after N consecutive failures; the checkpoint means
  the next run resumes.
- Descriptive `User-Agent` with a contact URL (§5e) so the Assembly can identify / reach us.
- Cache-first: a re-run or refresh mostly hits the local cache, so steady-state load on the origin
  is a handful of requests/day, not 800.

### 6.6 TheyWorkForYou as an alternative Hansard source — investigated 2026-09-03

**Question:** can TWFY's `getHansard` API replace the client-side substring search, making the
Phase 6b FTS5 build redundant?

**Verdict: no — but it changes what Phase 6b ingests.** Build FTS5, sourced from TWFY's *bulk
XML*, not from the NI data API's component walk. Do **not** take the `getHansard` API as a
runtime dependency.

#### Findings

| Question | Finding |
|---|---|
| **(1) NI coverage / history** | TWFY `scrapedxml/ni/` holds **2 430 debate files, `ni1998-07-01` → present** (verified by directory listing). That is the **full Assembly record since 1998** — roughly **3× the history** of the data API's `GetAllHansardReports_JSON` (~800 reports, ≈2014+). TWFY's scrape lags real sittings by a few days to ~2 weeks. `getHansard` API covers the same corpus. |
| **(2) API key & rate limits** | `getHansard` **requires an API key** — confirmed live (`{"error":"No API key provided"}`). **No anonymous/free tier.** Plans from **£20/mth**; "reduced/free for non-profit or charitable projects" but **by application, not self-serve**. Quota is per-call and `getHansard` search results are **paginated** (`num`/`page`), so one topic sweep = many calls. Their terms also constrain storing/redistributing API responses. **Unsuitable as a live backend for a tool other people install and run.** The **bulk `scrapedxml/` files need no key** and carry no per-request quota. |
| **(3) TWFY → NI `PersonId` mapping** | mySociety's `parlparse/members/people.json` carries a **`data.niassembly.gov.uk` identifier scheme whose value *is* the NI `PersonId`** (verified: `person/11734` → `"80"` = Conor Murphy). Speeches in the scrape carry `person_id="uk.org.publicwhip/person/N"` (302/307 attributed in a sample day). **Mapping coverage: 96 % of the current 2022–27 mandate (98/102 MLAs); only ~30 % all-time (98/332).** Recent debates map cleanly; older mandates would need a name+constituency fallback match — **not built, see recommendation 6**. |
| **(4) Does it kill Phase 6b?** | **No.** An MCP tool still needs a local index for: fast repeat queries, offline operation, no third-party key, no quota/ToS exposure, and stable ranking. TWFY replaces the **ingestion source**, not the index. |

#### Recommendation

1. **Keep Phase 6b (FTS5), re-spec its ingestion:** primary source = `https://www.theyworkforyou.com/pwdata/scrapedxml/ni/*.xml` (static files) + `parlparse/members/people.json` for `person_id` → `PersonId`. Store `(speech_id, date, major_heading, minor_heading, person_id_niassembly, twfy_person_id, speakername, text)` in FTS5.
   - Gains: 1998→present history, clean `major/minor-heading` + speaker attribution already parsed, **no legacy-IIS rate-limit exposure** (static file host), simpler than the component-tree walk.
2. **Keep the NI data API Hansard endpoints as a freshness top-up** — after the bulk load, pull the last ~30 days from `GetHansardComponentsByPlenaryDate_JSON` to cover TWFY's scrape lag. Also still the source for `search_debate_titles` in Phase 6a (before the index exists).
3. **Do not adopt `getHansard`.** Re-evaluate only if a charity key is granted *and* an online relevance-ranked fallback is wanted on top of the index.
4. **Licensing:** TWFY/parlparse data is published by mySociety and derived from Assembly material — check the `scrapedxml` reuse terms and mySociety's data licence, and attribute both TWFY and the NI Assembly in the README (ties into §5d).
5. **§6.5 pt 4 politeness** now mostly applies to `theyworkforyou.com` for the bulk pull (a robust static host — normal rate limiting is fine) and only to `data.niassembly.gov.uk` for the 30-day top-up.
6. **Historical PersonId fallback matching is *not* in Phase 6b v1 — decided.** It is currently
   only proposed; nothing is implemented or tested (we are pre-Phase-1). 6b v1 behaviour:
   - Every speech row stores `speakername` (verbatim) + `twfy_person_id` **unconditionally**, so
     full-text search over the whole 1998→present corpus works regardless of mapping.
   - `person_id` (NI `PersonId`) is filled **only where `people.json` maps it** (~96% current
     mandate, ~30% all-time). Unmapped speeches are **kept, not dropped** — they just aren't
     reachable by a `member_id` filter or counted in `find_relevant_contributors`' per-member
     aggregation.
   - So member-attributed features are **reliable from ~2022 onward**; older debates under-count.
     Tool descriptions say this explicitly (§6.3, §6.4).
   - Fuzzy fallback (unmapped `twfy_person_id` → `PersonId` via name + constituency + mandate
     against `GetAllMembersByGivenDate_JSON`) is a **documented later enhancement**, not a 6b
     blocker. Revisit only if historical member analysis becomes a real requirement.

#### Blocker status

This **is** the resolution of "don't build FTS5 until TWFY is investigated": FTS5 is confirmed
needed, ingestion source is decided. Phase 6b can be greenlit with the revised source.

---

## Appendix A — NI Assembly operation inventory (JSON unless noted)

**members.asmx:** GetAllConstituencies · GetAllCurrentMembers · GetAllMembers · GetAllMembersByGivenDate ·
GetAllCurrentMembersBySurnameSearch · GetAllCurrentMembersByGivenConstituencyId ·
GetAllCurrentMembersByGivenPartyId · GetMemberRolesByPersonId · GetAllMemberRoles ·
GetMemberContactDetailsByPersonId · GetAllMemberContactDetails · *GetAllCurrentMinisters (XML)* ·
*GetAllCurrentCommitteeChairs (XML)*

**organisations.asmx:** GetDepartmentListCurrent · GetPartiesListCurrent · GetOrganisationListCurrent ·
GetAllPartyGroupsListCurrent · GetCommitteesListCurrent_Standing · _Statutory · _AdHoc · _Other

**questions.asmx:** GetQuestionDetails · GetQuestionsByDepartment · GetQuestionsByMember ·
GetQuestionsBySearchText · GetQuestionsForOralAnswer_AnsweredInRange · _TabledInRange ·
GetQuestionsForWrittenAnswer_AnsweredInRange · _TabledInRange · *GetWrittenAnswerHtml (XML string)* ·
*GetWrittenAnswerOpenXml (OpenXML)*

**plenary.asmx:** GetBusinessDiary · GetDivisionMemberVoting · GetDivisionResult · GetMotionAmendments ·
GetMotionBill · GetMotionPetitionOfConcern · GetNoDayNamedMotions · GetPlenaryAddressees ·
GetPlenaryDetails · GetPlenaryItemsPlenaryDate · GetPlenaryItemsTabledByMember · GetPlenaryItemsTabledDate ·
GetPlenaryTablers · GetVotesOnDivision · *GetCommitteeAgendaItemsCommitteeMeetingDate (XML)* ·
*…CommitteeMeetingId (XML)* · *…MeetingDate (XML)*

**hansard.asmx:** GetAllHansardReports · GetHansardComponentsByPlenaryDate · GetHansardComponentsByReportId ·
GetHansardComponentsByReportIdAndPersonId

**register.asmx:** GetAllRegisteredInterests

## Appendix B — URL patterns

```
https://data.niassembly.gov.uk/<service>.asmx/<Operation>_JSON?<param>=<value>&<param>=<value>

members.asmx/GetAllCurrentMembersBySurnameSearch_JSON?searchText=oneill
members.asmx/GetAllCurrentMembersByGivenConstituencyId_JSON?constituencyId=12
members.asmx/GetMemberRolesByPersonId_JSON?personId=5797
organisations.asmx/GetDepartmentListCurrent_JSON
questions.asmx/GetQuestionDetails_JSON?documentId=2675
questions.asmx/GetQuestionsBySearchText_JSON?searchText=housing
questions.asmx/GetQuestionsForWrittenAnswer_TabledInRange_JSON?startDate=2026-06-01&endDate=2026-06-15
questions.asmx/GetWrittenAnswerHtml?documentId=2675                 (XML-wrapped HTML)
plenary.asmx/GetPlenaryItemsTabledDate_JSON?startDate=2026-06-01&endDate=2026-06-15
plenary.asmx/GetBusinessDiary_JSON?startDate=2026-06-01&endDate=2026-06-30
plenary.asmx/GetDivisionResult_JSON?divisionId=<id>
hansard.asmx/GetAllHansardReports_JSON
hansard.asmx/GetHansardComponentsByReportId_JSON?reportId=492567
hansard.asmx/GetHansardComponentsByReportIdAndPersonId_JSON?reportId=492567&personId=5797
register.asmx/GetAllRegisteredInterests_JSON
```

Response envelope (list): `{"AllMembersList":{"Member":[ {…}, {…} ]}}`
Response envelope (single): `{"QuestionsList":{"Question":{…}}}`  ← normalise to a 1-element list.
All scalar values are JSON strings. Dates: `"2026-06-30T00:00:00+01:00"`.
`_JSONP` variants wrap the body as `callback( … );` — do not use.

## Appendix C — Sources
- Endpoint semantics: live `…asmx` service-description pages on `data.niassembly.gov.uk` (verified 2026-09-03).
- Original endpoint list from the source prompt: `reference/ni-assembly-endpoints.md` (§5a — to be saved).
- URL/param patterns & JSONP note: <https://gist.github.com/paddycarey/3752817>
- Source project: <https://github.com/i-dot-ai/parliament-mcp> @ `reference/parliament-mcp/` (MIT, © 2025 i.AI; upstream commit `2db3fe4`).
- NI Assembly Open Data reuse terms: to be confirmed against `data.niassembly.gov.uk` / `niassembly.gov.uk` before release (§5d).
- TheyWorkForYou (§6.6): API docs <https://www.theyworkforyou.com/api/docs/getHansard> (key required, plans from £20/mth — not used); bulk scrape <https://www.theyworkforyou.com/pwdata/scrapedxml/ni/> (`ni1998-07-01` → present, no key); person mapping <https://github.com/mysociety/parlparse> `members/people.json` (`data.niassembly.gov.uk` identifier scheme = NI `PersonId`).
- TWFY/mySociety licence terms: <https://www.theyworkforyou.com/api/terms> (verified 2026-09-03) — parlparse software AGPL-3.0; TWFY member/identifier data **CC BY-SA 2.5**; debate text under the **Open Parliament Licence**; attribution "Data service provided by TheyWorkForYou". See §5d.

"""Orchestration for ``ni-assembly-mcp index`` (PLAN.md Phase 6b commit 2 / Phase 9).

Questions flow (:func:`run_questions_index`, Phase 9): walk the four
``questions.asmx`` range endpoints in 6-month windows into the ``question`` table;
checkpoint ``questions_cursor``; incremental runs re-scan a trailing 60-day
window for late-arriving answers.

Hansard flow:

1. **Bulk / incremental** — TheyWorkForYou scrape files into ``contribution``
   (``--full`` = the whole directory listing; otherwise files changed since the
   ``hansard_cursor`` checkpoint).
2. **Freshness top-up** — TWFY lags real sittings by days-to-weeks, so pull recent
   sitting days from ``GetHansardComponentsByPlenaryDate_JSON`` as
   ``niapi:``-prefixed rows.

Dedup (PLAN.md "speech_id namespacing" section), TWFY always authoritative:
- before writing a scrape file for date D, delete ``niapi:%`` rows for D (rule 3);
- the top-up delete-and-replaces its own window (rule 1) and skips any date TWFY
  already covers (rule 2).

Everything keys off ``ingest_state`` so a killed run resumes.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta

from ni_assembly_mcp.index_db import (
    delete_contributions_for_date,
    get_state,
    open_index,
    set_state,
    upsert_contributions,
    upsert_questions,
)
from ni_assembly_mcp.ingest.http import PoliteFetcher, indexer_config
from ni_assembly_mcp.ingest.people_map import load_person_map
from ni_assembly_mcp.ingest.questions import QUESTIONS_FLOOR, fetch_window, question_windows
from ni_assembly_mcp.ingest.twfy import changed_since, date_of, list_scrape_files, parse_scrape_file
from ni_assembly_mcp.niassembly_client import niassembly_get
from ni_assembly_mcp.settings import Settings, settings

logger = logging.getLogger(__name__)

_NIAPI = "niapi:"

# NI Hansard component types that carry indexable prose (the rest — Time, Division,
# Document Title, … — are skipped in the freshness top-up).
_CONTENT_COMPONENT_TYPES = frozenset(
    {"Spoken Text", "Bill Text", "Quote", "Question", "Plenary Item Text", "Procedure Line"}
)


def _iso_date(value: str | None) -> str:
    return (value or "")[:10]


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _cursor_from_since(since: str | None) -> int | None:
    """`--since` accepts a unix ts or a YYYY-MM-DD date (converted to midnight UTC)."""
    if not since:
        return None
    if since.isdigit():
        return int(since)
    return int(datetime.fromisoformat(since).replace(tzinfo=UTC).timestamp())


async def run_hansard_index(
    conn: sqlite3.Connection, *, full: bool, since: str | None = None, config: Settings | None = None
) -> dict:
    config = config or settings
    fetcher = PoliteFetcher(config)
    people = await load_person_map(fetcher, config)

    if full:
        filenames = await list_scrape_files(fetcher, config)
        _, new_cursor = await changed_since(fetcher, config, 0)
    else:
        cursor = _cursor_from_since(since)
        if cursor is None:
            cursor = int(get_state(conn, "hansard_cursor") or 0)
        filenames, new_cursor = await changed_since(fetcher, config, cursor)

    logger.info("hansard: %d scrape file(s) to ingest", len(filenames))
    files_done = speeches = 0
    for filename in filenames:
        xml_bytes = await fetcher.get_bytes(f"{config.twfy_base_url}/{filename}")
        debate_date = date_of(filename)
        rows = list(parse_scrape_file(xml_bytes, filename, people))
        with conn:
            delete_contributions_for_date(conn, debate_date, prefix=_NIAPI)
            speeches += upsert_contributions(conn, rows)
        files_done += 1

    with conn:
        set_state(conn, "hansard_cursor", str(new_cursor))

    topped_up = await _freshness_topup(conn, config)

    with conn:
        set_state(conn, "hansard_last_refresh", _now())

    return {"files": files_done, "speeches": speeches, "topup_rows": topped_up}


async def _freshness_topup(conn: sqlite3.Connection, config: Settings) -> int:
    """Cover the gap between TWFY's frontier and today from the NI data API."""
    (latest_twfy,) = conn.execute(
        f"SELECT max(debate_date) FROM contribution WHERE speech_id NOT LIKE '{_NIAPI}%'"
    ).fetchone()

    today = datetime.now(tz=UTC).date()
    window_start = today - timedelta(days=config.hansard_topup_days)
    if latest_twfy:
        window_start = min(window_start, date.fromisoformat(latest_twfy) + timedelta(days=1))
    start_iso = window_start.isoformat()

    with conn:
        conn.execute(
            f"DELETE FROM contribution WHERE speech_id LIKE '{_NIAPI}%' AND debate_date >= ?", (start_iso,)
        )

    reports = await niassembly_get("hansard", "GetAllHansardReports", config=config)
    dates = sorted({_iso_date(r.get("PlenaryDate")) for r in reports if _iso_date(r.get("PlenaryDate")) >= start_iso})

    written = 0
    for sitting in dates:
        covered = conn.execute(
            f"SELECT 1 FROM contribution WHERE debate_date = ? AND speech_id NOT LIKE '{_NIAPI}%' LIMIT 1",
            (sitting,),
        ).fetchone()
        if covered:
            continue
        components = await niassembly_get(
            "hansard", "GetHansardComponentsByPlenaryDate", plenaryDate=sitting, config=config
        )
        rows = _components_to_rows(components, sitting)
        with conn:
            written += upsert_contributions(conn, rows)
    if written:
        logger.info("hansard top-up: %d rows across %d recent sitting day(s)", written, len(dates))
    return written


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _components_to_rows(components: list[dict], sitting: str) -> list[dict]:
    """Map NI Hansard components to the ``contribution`` shape (best-effort).

    Header components become their own rows (so the heading is searchable even
    with no spoken text); ``Speaker (*)`` rows set the active speaker; spoken
    components (Spoken/Bill/Quote/Question/Plenary Item Text) become contribution
    rows under the current heading + speaker. This is a ~30-day stopgap for the
    heading-only ``search_debate_titles``; Phase 6c refines it.
    """
    major: str | None = None
    minor: str | None = None
    speaker_person: int | None = None
    speaker_name: str | None = None
    rows: list[dict] = []

    for comp in components:
        ctype = comp.get("ComponentType") or ""
        cid = comp.get("ComponentId")
        text = _clean_component(comp.get("ComponentText"))
        if not cid:
            continue

        if ctype == "Header":
            level = (comp.get("ComponentHeader") or "").lower()
            if "2" in level:
                minor = text
            else:
                major, minor = text, None
            speaker_person = speaker_name = None
            rows.append(_niapi_row(cid, sitting, major, minor, text, None, None))
        elif ctype.startswith("Speaker"):
            speaker_person = _int(comp.get("RelatedItemId"))
            speaker_name = (text or "").rstrip(":") or None
        elif text and ctype in _CONTENT_COMPONENT_TYPES:
            rows.append(_niapi_row(cid, sitting, major, minor, text, speaker_person, speaker_name))

    return rows


def _clean_component(text: str | None) -> str | None:
    return text.strip() if text and text.strip() else None


def _niapi_row(
    cid: str, sitting: str, major: str | None, minor: str | None, body: str | None,
    person_id: int | None, speakername: str | None,
) -> dict:
    return {
        "speech_id": f"{_NIAPI}{cid}",
        "debate_date": sitting,
        "major_heading": major,
        "minor_heading": minor,
        "person_id": person_id,
        "twfy_person_id": None,
        "speakername": speakername,
        "speech_time": None,
        "url": None,
        "body": body or "",
    }


# Questions: every incremental run also re-scans this trailing window, because an
# answer (and AnsweredOnDate) lands days-to-weeks after the question is tabled.
_QUESTIONS_REFRESH_DAYS = 60


def _date_from_since(since: str | None) -> date | None:
    """``--since`` as a date: a ``YYYY-MM-DD`` string, or a unix ts (UTC date)."""
    if not since:
        return None
    if since.isdigit():
        return datetime.fromtimestamp(int(since), tz=UTC).date()
    return date.fromisoformat(since)


async def run_questions_index(
    conn: sqlite3.Connection, *, full: bool, since: str | None = None, config: Settings | None = None
) -> dict:
    """Fill the ``question`` table from the four ``questions.asmx`` range endpoints.

    ``full`` (or ``--since``) walks 6-month windows from the 2007 floor; an
    incremental run resumes from the ``questions_cursor`` checkpoint but always
    re-scans the trailing 60 days for late-arriving answers. A window failure
    raises (``NIAssemblyAPIError``) and aborts — the checkpoint means the next run
    resumes rather than restarting.
    """
    config = config or settings
    qconfig = indexer_config(config)
    today = datetime.now(tz=UTC).date()

    since_date = _date_from_since(since)
    if full or since_date is not None:
        start = since_date or QUESTIONS_FLOOR
    else:
        cursor = get_state(conn, "questions_cursor")
        start = date.fromisoformat(cursor) if cursor else QUESTIONS_FLOOR
        start = min(start, today - timedelta(days=_QUESTIONS_REFRESH_DAYS))

    windows = question_windows(start, today)
    logger.info("questions: %d window(s) from %s", len(windows), start)
    total = 0
    for window_from, window_to in windows:
        rows = await fetch_window(window_from, window_to, config=qconfig)
        with conn:
            total += upsert_questions(conn, rows)
            set_state(conn, "questions_cursor", window_to)

    with conn:
        set_state(conn, "questions_last_refresh", _now())

    return {"windows": len(windows), "questions": total}


async def run_index_cli(source: str, *, full: bool, since: str | None, db: str | None) -> None:
    from pathlib import Path

    config = settings if not db else settings.model_copy(update={"index_db_path": Path(db)})
    conn = open_index(config.index_db_path)
    try:
        if source == "status":
            from ni_assembly_mcp.index_query import index_status

            for key, value in index_status(conn).items():
                print(f"{key:24} {value}")
            return
        if source == "questions":
            result = await run_questions_index(conn, full=full, since=since, config=config)
            print(
                f"questions index: {result['windows']} window(s), {result['questions']} row(s) "
                f"-> {config.index_db_path}"
            )
            return
        result = await run_hansard_index(conn, full=full, since=since, config=config)
        print(
            f"hansard index: {result['files']} file(s), {result['speeches']} speech row(s), "
            f"{result['topup_rows']} top-up row(s) -> {config.index_db_path}"
        )
    finally:
        conn.close()

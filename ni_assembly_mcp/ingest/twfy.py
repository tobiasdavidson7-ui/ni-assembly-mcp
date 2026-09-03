"""TheyWorkForYou bulk-XML source for the Hansard index (PLAN.md §6.6).

``theyworkforyou.com/pwdata/scrapedxml/ni/`` holds one ``niYYYY-MM-DD.xml`` per
sitting day, ``ni1998-07-01`` → present, no API key. ``changedates.txt``
(``unix_ts,filename`` lines, append-only, re-listed on re-scrape) drives
incremental refresh.
"""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ni_assembly_mcp.ingest.http import PoliteFetcher
from ni_assembly_mcp.ingest.people_map import PersonMap
from ni_assembly_mcp.settings import Settings

logger = logging.getLogger(__name__)

_FILE_RE = re.compile(r"ni(\d{4}-\d{2}-\d{2})\.xml")
_HREF_RE = re.compile(r'href="(ni\d{4}-\d{2}-\d{2}\.xml)"')
_HEADING_TAGS = {"major-heading", "minor-heading", "oral-heading"}
_WS_RE = re.compile(r"\s+")


def date_of(filename: str) -> str:
    match = _FILE_RE.search(filename)
    if not match:
        msg = f"not a scrape filename: {filename!r}"
        raise ValueError(msg)
    return match.group(1)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = _WS_RE.sub(" ", text).strip()
    return collapsed or None


async def list_scrape_files(fetcher: PoliteFetcher, config: Settings) -> list[str]:
    """Every ``niYYYY-MM-DD.xml`` in the directory listing, chronological."""
    html = (await fetcher.get_bytes(config.twfy_base_url + "/")).decode("utf-8", "replace")
    return sorted(set(_HREF_RE.findall(html)))


async def changed_since(fetcher: PoliteFetcher, config: Settings, since_ts: int) -> tuple[list[str], int]:
    """``(filenames_changed_after_since_ts, max_ts_seen)`` from ``changedates.txt``.

    ``max_ts_seen`` becomes the next cursor even when the file list is empty, so a
    quiet refresh still advances past already-seen entries.
    """
    text = (await fetcher.get_bytes(config.twfy_base_url + "/changedates.txt")).decode("utf-8", "replace")
    latest_ts: dict[str, int] = {}
    max_ts = since_ts
    for line in text.splitlines():
        line = line.strip()
        if "," not in line:
            continue
        ts_str, name = line.split(",", 1)
        try:
            ts = int(ts_str)
        except ValueError:
            continue
        if not _FILE_RE.fullmatch(name):
            continue
        max_ts = max(max_ts, ts)
        if ts > since_ts:
            latest_ts[name] = max(latest_ts.get(name, 0), ts)
    ordered = [name for name, _ in sorted(latest_ts.items(), key=lambda kv: kv[1])]
    return ordered, max_ts


def _speech_row(elem: ET.Element, debate_date: str, major: str | None, minor: str | None, people: PersonMap) -> dict:
    person_uri = elem.get("person_id") or None
    speaker_uri = elem.get("speakerid") or None
    paragraphs = ("".join(p.itertext()).strip() for p in elem.iter("p"))
    body = "\n".join(_WS_RE.sub(" ", para).strip() for para in paragraphs if para.strip())
    return {
        "speech_id": elem.get("id"),
        "debate_date": debate_date,
        "major_heading": major,
        "minor_heading": minor,
        "person_id": people.resolve(person_uri, speaker_uri),
        "twfy_person_id": person_uri or speaker_uri,
        "speakername": _clean(elem.get("speakername")),
        "speech_time": elem.get("time") or None,
        "url": elem.get("url") or None,
        "body": body,
    }


def parse_scrape_file(xml_bytes: bytes, filename: str, people: PersonMap) -> Iterator[dict]:
    """Yield one contribution dict per ``<speech>``, carrying the current heading.

    A file that fails to parse is logged and skipped (an empty iterator) so the
    ingest checkpoint holds and the run continues.
    """
    debate_date = date_of(filename)
    major: str | None = None
    minor: str | None = None
    try:
        for _event, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
            if elem.tag in _HEADING_TAGS:
                heading = _clean("".join(elem.itertext()))
                if elem.tag == "minor-heading":
                    minor = heading
                else:
                    major, minor = heading, None
                elem.clear()
            elif elem.tag == "speech":
                if elem.get("id"):
                    yield _speech_row(elem, debate_date, major, minor, people)
                elem.clear()
    except ET.ParseError as exc:
        logger.warning("skipping %s — XML parse error: %s", filename, exc)

"""TheyWorkForYou / parlparse person id → NI Assembly ``PersonId`` mapping.

``parlparse/members/people.json`` carries, per person, a ``data.niassembly.gov.uk``
identifier whose value *is* the NI ``PersonId`` (PLAN.md §6.6). Scrape files
attribute a ``<speech>`` either directly (``person_id="…/person/N"``, modern
files) or via a membership (``speakerid="…/member/N"``, older files), so we build
both hops.

Coverage: ~96 % of the current mandate, ~30 % all-time. Unmapped speeches keep
``speakername`` + ``twfy_person_id`` and are still indexed — they just aren't
reachable by a ``member_id`` filter (PLAN.md §6.6 pt 6). No fuzzy fallback in v1.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ni_assembly_mcp.ingest.http import PoliteFetcher
from ni_assembly_mcp.settings import Settings

logger = logging.getLogger(__name__)

_NI_SCHEME = "data.niassembly.gov.uk"


@dataclass(frozen=True)
class PersonMap:
    person_uri_to_ni: dict[str, int]
    member_uri_to_person_uri: dict[str, str]

    def resolve(self, person_uri: str | None, speaker_uri: str | None) -> int | None:
        uri = person_uri or (self.member_uri_to_person_uri.get(speaker_uri) if speaker_uri else None)
        return self.person_uri_to_ni.get(uri) if uri else None

    @property
    def coverage(self) -> int:
        return len(self.person_uri_to_ni)


def parse_people_json(raw: bytes) -> PersonMap:
    data = json.loads(raw)

    person_uri_to_ni: dict[str, int] = {}
    for person in data.get("persons", []):
        pid = person.get("id")
        if not pid:
            continue
        for ident in person.get("identifiers") or []:
            if ident.get("scheme") != _NI_SCHEME:
                continue
            try:
                person_uri_to_ni[pid] = int(ident["identifier"])
            except (KeyError, TypeError, ValueError):
                logger.debug("bad NI identifier on %s: %r", pid, ident)

    member_uri_to_person_uri: dict[str, str] = {}
    for membership in data.get("memberships", []):
        mid, pid = membership.get("id"), membership.get("person_id")
        if mid and pid:
            member_uri_to_person_uri[mid] = pid

    return PersonMap(person_uri_to_ni, member_uri_to_person_uri)


async def load_person_map(fetcher: PoliteFetcher, config: Settings) -> PersonMap:
    raw = await fetcher.get_bytes(config.people_json_url)
    person_map = parse_people_json(raw)
    logger.info(
        "person map: %d NI PersonIds, %d member→person links",
        len(person_map.person_uri_to_ni),
        len(person_map.member_uri_to_person_uri),
    )
    return person_map

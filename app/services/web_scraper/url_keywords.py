"""Shared meeting-URL keyword detection for the school scraper.

Two consumers must agree on what "looks like a meeting page":

* :mod:`schema_driven_crawler` boosts LLM link confidence for these paths so
  obviously-relevant pages jump the frontier queue.
* :mod:`school_scraper_service` enqueues same-domain *sibling* links (not
  only sub-paths of the hub URL) when they look meeting-related, so a hub at
  ``/school-committee`` still reaches ``/agendas/2024/``.

Keeping the keyword sets in one place prevents the two from drifting.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Unambiguous path segments — a single hit means "this is a meeting page".
_STRONG_KEYWORDS: tuple[str, ...] = (
    "meeting-minutes",
    "meeting_minutes",
    "meetingminutes",
    "agendas-minutes",
    "agendas_minutes",
    "minutes-agendas",
    "minutes-archive",
    "minutes_archive",
    "meeting-packets",
    "meeting_packets",
    "board-packets",
    "archived-agendas",
    "archived_agendas",
    "document-archives",
    "document_archives",
    "board-of-trustees",
    "board_of_trustees",
    "school-committee",
    "school_committee",
    "supervisory_committee",
    "supervisory-committee",
    # A path segment that is just "agenda" / "agendas" is unambiguous on a
    # school site — there is no non-meeting "agendas" page. Treating it as
    # strong (rather than weak) lets a hub at /school-committee reach a
    # sibling /agendas/2024/ without needing a second weak keyword to co-occur.
    "/agenda",
    "/agendas",
)

# Generic terms — meaningful only when at least two co-occur, otherwise they
# fire on unrelated pages (a "/staff/board-of-directors" page is not minutes).
_WEAK_KEYWORDS: tuple[str, ...] = (
    "minutes",
    "committee",
    "board",
    "trustees",
    "archive",
    "packets",
)

_WEAK_MIN_HITS = 2


def _path_lower(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:  # noqa: BLE001 — defensive; a bad URL should not crash a crawl
        return ""


def is_meeting_related_url(url: str) -> bool:
    """True when a URL's path looks like a meeting-minutes / agenda page.

    A single STRONG keyword is enough; otherwise at least ``_WEAK_MIN_HITS``
    WEAK keywords must co-occur. Used by the media crawl to decide whether a
    same-domain *sibling* link (one not under the hub URL prefix) should be
    enqueued for depth crawling.
    """
    path = _path_lower(url)
    if not path:
        return False
    if any(k in path for k in _STRONG_KEYWORDS):
        return True
    return sum(1 for k in _WEAK_KEYWORDS if k in path) >= _WEAK_MIN_HITS

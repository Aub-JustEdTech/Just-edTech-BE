"""
BoardDocs public-portal document expander (HTTP-first, no Playwright).

BoardDocs (``go.boarddocs.com`` / ``www.boarddocs.com``) is a Lotus/IBM Notes
Domino NSF application fronted by a JS SPA. The shell renders blank under
headless Chromium (the server detects headless and withholds the SPA), so the
Diligent / BoardOnTrack / Granicus "click the calendar with Playwright"
strategy does not work here.

What does work is the *unofficial* NSF agent surface that the SPA itself
calls over XHR. A small, stable set of agents exposes the same data the
SPA renders, as JSON / HTML, and the attachment PDFs are served from
plain (session-less) ``/files/{UNID}/$file/{name}`` URLs:

    GET  /{st}/{org}/Board.nsf/Public
      → parse ``committeeid`` (and committee labels) from shell HTML

    POST …/BD-GetMeetingsList?open
      body: current_committee_id=…
      → JSON meetings [{unique, numberdate, name, ...}]

    POST …/PRINT-AgendaDetailed
      body: id={meeting.unique}&current_committee_id=…
      → HTML; extract ``a.public-file`` / ``a[href*="/files/"]`` links

    GET  https://go.boarddocs.com/files/{id}/$file/….pdf
      → application/pdf (plain HTTP + UA/Referer)

This module implements that flow as ``expand_boarddocs_meetings`` and a
matching download helper (``fetch_boarddocs_document``) that prefers a
direct HTTP GET and only falls back to the Playwright session path when the
server returns an HTML login/error page.

The expander is HTTP-only on purpose — see
``scripts/school_data/BOARD_PLATFORMS_FINAL_SUMMARY.md`` for the
headless-detection failure that motivated this approach.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.services.web_scraper.board_platforms import (
    boarddocs_portal_base,
    fetch_document_via_playwright_session,
    is_boarddocs_private_url,
    is_boarddocs_url,
    normalize_boarddocs_url,
)
from app.services.web_scraper.year_filter import allowed_calendar_years

logger = logging.getLogger(__name__)

# Re-exported so callers can import everything board-docs related from one
# place (mirrors how Diligent/BoardOnTrack helpers are re-exported through
# playwright_interactions.py).
__all__ = [
    "expand_boarddocs_meetings",
    "fetch_boarddocs_document",
    "discover_boarddocs_committees",
    "parse_boarddocs_meetings_list",
    "parse_boarddocs_agenda_files",
]

# 4-digit year extractor shared with the other expanders (kept local so this
# module stays import-light and avoids a Playwright-interactions dependency).
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# Committee-name keywords used to pick the board/school-committee out of a
# portal that exposes many committees (some portals publish a "Policy
# Library" or sub-committees alongside the main board). Match is case- and
# word-boundary insensitive; the first matching committee wins.
_COMMITTEE_PRIORITY_KEYWORDS: tuple[str, ...] = (
    "school committee",
    "school board",
    "board of education",
    "board of selectmen",
    "board of trustees",
    "board of directors",
    "board",
    "committee",
)

# Cap on how many committees we'll enumerate meetings from when no name
# matches the priority keywords — keeps the cap meaningful (max_meetings is
# *per committee* by convention, but we still bound the committee count so
# a portal with dozens of sub-committees can't explode the budget).
_MAX_FALLBACK_COMMITTEES = 3


def _client_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Headers that mimic the BoardDocs SPA's own XHR requests.

    ``X-Requested-With`` is what the NSF agents key on to return JSON/HTML
    instead of a redirect to the shell; ``Referer`` keeps the download
    endpoint happy on the few tenants that check it. The User-Agent is the
    configured scraper UA (defaults to a curl-style string elsewhere) —
    BoardDocs doesn't gate on a browser UA, but using the same one as the
    rest of the scraper keeps the traffic fingerprint consistent.
    """
    headers: dict[str, str] = {
        "User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT,
        "Accept": (
            "application/json, text/javascript, text/html, */*; q=0.01"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }
    if extra:
        headers.update(extra)
    return headers


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.WEB_SCRAPER_TIMEOUT_SECONDS, connect=10.0)


# ---------------------------------------------------------------------------
# Shell parsing — committee discovery
# ---------------------------------------------------------------------------

# Matches the ``committeeid="…"`` / ``committee_id="…"`` / ``data-committee-id="…"``
# attributes the BoardDocs shell embeds inline, plus the
# ``current_committee_id`` JS assignments the SPA boots from. The first
# capture group is the committee id; we tolerate quoted (single/double) or
# bare-numeric forms.
_COMMITTEE_ID_RE = re.compile(
    r"""(?:committeeid|committee_id|data-committee-id|current_committee_id)
        \s*[:=]\s*["']?([A-F0-9]{16,32}|[0-9]{1,12})["']?""",
    re.IGNORECASE | re.VERBOSE,
)

# Matches a committee <option> row: ``<option value="ID">Committee Name</option>``
# (the shell usually has a <select> of committees). We grab value + label.
_COMMITTEE_OPTION_RE = re.compile(
    r"""<option[^>]*value\s*=\s*["']([^"']+)["'][^>]*>([^<]+)</option>""",
    re.IGNORECASE,
)


def discover_boarddocs_committees(shell_html: str) -> list[tuple[str, str]]:
    """Extract ``(committee_id, committee_name)`` pairs from a BoardDocs shell.

    Order is by *priority*: committees whose label matches
    :data:`_COMMITTEE_PRIORITY_KEYWORDS` come first (in the order they appear
    in the HTML), so the expander can pick the main board committee out of a
    multi-committee portal without scraping every sub-committee.

    Returns ``[]`` when no committee ids can be parsed — the caller treats
    that as "expander can't proceed for this portal" rather than as an error.
    """
    if not shell_html:
        return []

    # Primary path: <option> rows from a committee <select>. These carry both
    # the id and a human label, which lets us apply the priority keywords.
    options: list[tuple[str, str]] = []
    for match in _COMMITTEE_OPTION_RE.finditer(shell_html):
        cid = match.group(1).strip()
        name = match.group(2).strip()
        if cid:
            options.append((cid, name))

    # Fallback: bare committeeid="…" attributes (no label). These are
    # deduped against ids already seen in the <option> path.
    if not options:
        seen: set[str] = set()
        for match in _COMMITTEE_ID_RE.finditer(shell_html):
            cid = match.group(1).strip()
            if cid and cid not in seen:
                seen.add(cid)
                options.append((cid, ""))

    if not options:
        return []

    def _priority(name: str) -> int:
        lowered = name.lower()
        for idx, kw in enumerate(_COMMITTEE_PRIORITY_KEYWORDS):
            if kw in lowered:
                return idx
        return len(_COMMITTEE_PRIORITY_KEYWORDS)

    # Stable sort: preserves HTML order within the same priority bucket so
    # the first matching committee in the document wins.
    return sorted(options, key=lambda pair: _priority(pair[1]))


# ---------------------------------------------------------------------------
# Meeting list parsing
# ---------------------------------------------------------------------------


def _parse_boarddocs_numberdate(raw: str | None) -> datetime | None:
    """Parse a BoardDocs ``numberdate`` (NSF Lotus date) into a datetime.

    Live portals return ``numberdate`` as compact ``YYYYMMDD`` (e.g.
    ``"20260813"``); ISO-ish and US formats are also accepted. Anything
    unparseable yields ``None`` and the year gate falls back to the regex
    extractor in :func:`_meeting_year`.
    """
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in (
        "%Y%m%d",  # real BoardDocs format, e.g. "20260813"
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_boarddocs_meetings_list(payload: Any) -> list[dict]:
    """Normalise the ``BD-GetMeetingsList`` response into meeting dicts.

    The agent returns either a bare JSON list or an object wrapping a list
    (``{"meetings": [...]}`` / ``{"data": [...]}``); some tenants wrap it
    in a top-level string that needs JSON-parsing twice. We tolerate all
    three and return a list of ``{unique, numberdate, name, meeting_date}``
    dicts, dropping rows without a usable meeting id.
    """
    import json

    data = payload
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        for key in ("meetings", "data", "items", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        unique = row.get("unique") or row.get("id") or row.get("unid")
        if not unique:
            continue
        name = str(row.get("name") or row.get("title") or "").strip()
        numberdate = row.get("numberdate") or row.get("date") or row.get("meetingdate")
        meeting_date = _parse_boarddocs_numberdate(str(numberdate) if numberdate else None)
        out.append(
            {
                "unique": str(unique),
                "name": name,
                "numberdate": str(numberdate) if numberdate else "",
                "meeting_date": meeting_date,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Agenda detail parsing
# ---------------------------------------------------------------------------

# Selectors that mark a downloadable attachment link on the
# ``PRINT-AgendaDetailed`` page. BoardDocs variants seen in the wild:
#   <a class="public-file" href="…/files/{UNID}/$file/name.pdf">…</a>
#   <a href="…/files/{UNID}/$file/name.pdf">Agenda Packet</a>
#   <a href="…/Board.nsf/files/{UNID}/$file/name.pdf">…</a>
_FILE_PATH_MARKERS = ("/files/", "$file")


def parse_boarddocs_agenda_files(
    agenda_html: str,
    *,
    base_url: str,
) -> list[dict]:
    """Extract attachment file dicts from a ``PRINT-AgendaDetailed`` HTML page.

    Returns ``[{url, name, media_type}]`` with absolute URLs resolved
    against ``base_url`` (the portal's NSF base, e.g.
    ``https://go.boarddocs.com/ma/nrsd/Board.nsf``). ``media_type`` is
    always ``"document"`` — the downstream pipeline routes by file
    extension, not by this label.
    """
    if not agenda_html:
        return []

    soup = BeautifulSoup(agenda_html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"] or ""
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        if not all(marker in href for marker in _FILE_PATH_MARKERS):
            continue

        absolute = urljoin(base_url + "/", href)
        if absolute in seen:
            continue
        seen.add(absolute)

        name = (anchor.get_text(strip=True) or "").strip()
        if not name:
            # Fall back to the last path segment as the file name.
            tail = href.rstrip("/").rsplit("/", 1)[-1]
            name = tail or "document"

        out.append({"url": absolute, "name": name, "media_type": "document"})

    return out


# ---------------------------------------------------------------------------
# Expander — the public entry point
# ---------------------------------------------------------------------------


async def expand_boarddocs_meetings(
    *,
    page_url: str,
    max_meetings: int = 24,
    timeout_ms: int | None = None,
    # The Playwright ``page`` arg is accepted for signature parity with the
    # Diligent / BoardOnTrack expanders but is intentionally unused — this
    # expander is HTTP-first and never drives a browser. Keeping the param
    # lets the dispatcher call all four expanders the same way.
    page: Any = None,  # noqa: ARG001
) -> list[dict]:
    """Collect published agenda/attachment documents from a BoardDocs portal.

    Returns a list of media dicts in the same shape as the Diligent /
    BoardOnTrack expanders::

        {
            "url": <absolute attachment URL>,
            "media_type": "document",
            "source_page_url": <normalised portal URL>,
            "doc_year": <meeting year or None>,
            "name": <file label>,
        }

    The list is year-gated against ``SCHOOL_SCRAPER_ALLOWED_YEARS`` and
    capped at ``max_meetings`` (per committee, applied before attachments
    are collected so a single meeting with many attachments doesn't
    exhaust the cap). Private portals are skipped outright (see
    :func:`app.services.web_scraper.board_platforms.is_boarddocs_private_url`).
    """
    if not is_boarddocs_url(page_url):
        logger.debug("boarddocs: not a BoardDocs URL, skipping: %s", page_url)
        return []
    if is_boarddocs_private_url(page_url):
        logger.info("boarddocs: skipping private (login-walled) portal %s", page_url)
        return []

    portal_url = normalize_boarddocs_url(page_url) or page_url
    base_url = boarddocs_portal_base(portal_url)
    if not base_url:
        logger.warning(
            "boarddocs: could not derive Board.nsf base from %s; skipping",
            portal_url,
        )
        return []

    timeout = (
        httpx.Timeout(timeout_ms / 1000, connect=10.0)
        if timeout_ms
        else _default_timeout()
    )

    allowed_years = allowed_calendar_years()
    collected: dict[str, dict] = {}

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT},
    ) as client:
        # 1. Fetch the shell to discover committee ids. The shell is a real
        # HTML page (unlike the SPA-rendered meetings list), so a plain GET
        # returns the markup with embedded committeeid attributes / <option>
        # rows.
        try:
            shell_resp = await client.get(portal_url)
            shell_resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "boarddocs: shell fetch failed for %s (%s): %s",
                portal_url,
                type(exc).__name__,
                exc,
            )
            return []

        committees = discover_boarddocs_committees(shell_resp.text)
        if not committees:
            logger.info(
                "boarddocs: no committees parsed from shell %s; cannot enumerate",
                portal_url,
            )
            return []

        # Apply the multi-committee budget. Priority sort means the first
        # entries are the most-likely-main board committee; if none of the
        # labels matched a priority keyword, we still try up to
        # _MAX_FALLBACK_COMMITTEES to cover small portals with one unnamed
        # committee.
        priority_committees = [
            c for c in committees
            if any(kw in c[1].lower() for kw in _COMMITTEE_PRIORITY_KEYWORDS)
        ]
        committees_to_try = priority_committees or committees[:_MAX_FALLBACK_COMMITTEES]

        # 2. For each committee, list meetings, year-gate, and collect
        # agenda attachments.
        meetings_budget = max_meetings
        for committee_id, committee_name in committees_to_try:
            if meetings_budget <= 0:
                break

            meetings = await _fetch_meetings_list(
                client, base_url, committee_id
            )
            if not meetings:
                continue

            # Year-gate and cap per committee.
            kept: list[dict] = []
            for meeting in meetings:
                meeting_year = _meeting_year(meeting)
                if meeting_year is not None and meeting_year not in allowed_years:
                    logger.debug(
                        "boarddocs: skipping meeting %r (year=%d not in %s)",
                        meeting.get("name"),
                        meeting_year,
                        sorted(allowed_years),
                    )
                    continue
                kept.append(meeting)
                if len(kept) >= meetings_budget:
                    break

            if not kept:
                continue

            logger.info(
                "boarddocs: committee %r (%s) -> %d meetings within budget on %s",
                committee_name or committee_id,
                committee_id,
                len(kept),
                portal_url,
            )

            # 3. For each kept meeting, fetch PRINT-AgendaDetailed and
            # collect attachment file URLs.
            for meeting in kept:
                files = await _fetch_agenda_files(
                    client, base_url, committee_id, meeting
                )
                meeting_year = _meeting_year(meeting)
                for f in files:
                    key = f["url"]
                    if key in collected:
                        continue
                    collected[key] = {
                        "url": f["url"],
                        "media_type": "document",
                        "source_page_url": portal_url,
                        "doc_year": meeting_year,
                        "name": f.get("name") or "",
                    }

            # Deduct this committee's meetings from the shared budget so the
            # portal-wide cap (SCHOOL_SCRAPER_BOARD_PORTAL_MAX_MEETINGS) is
            # respected across committees, not per-committee.
            meetings_budget -= len(kept)

    logger.info(
        "boarddocs: collected %d documents from %s",
        len(collected),
        portal_url,
    )
    return list(collected.values())


def _meeting_year(meeting: dict) -> int | None:
    """Best-effort 4-digit year for a parsed meeting dict."""
    meeting_date = meeting.get("meeting_date")
    if meeting_date is not None:
        return meeting_date.year
    match = _YEAR_RE.search(str(meeting.get("numberdate") or meeting.get("name") or ""))
    return int(match.group(1)) if match else None


async def _fetch_meetings_list(
    client: httpx.AsyncClient,
    base_url: str,
    committee_id: str,
) -> list[dict]:
    """POST ``BD-GetMeetingsList`` and return parsed meetings.

    Soft-fails on any HTTP/parse error: returns ``[]`` and logs at debug so
    the expander loop can try the next committee without raising.
    """
    endpoint = urljoin(base_url + "/", "BD-GetMeetingsList?open")
    body = f"current_committee_id={committee_id}"
    try:
        resp = await client.post(
            endpoint,
            content=body,
            headers=_client_headers(
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": base_url,
                }
            ),
        )
        if resp.status_code != 200:
            logger.debug(
                "boarddocs: BD-GetMeetingsList %s returned HTTP %d for committee %s",
                endpoint,
                resp.status_code,
                committee_id,
            )
            return []
        return parse_boarddocs_meetings_list(resp.text)
    except httpx.HTTPError as exc:
        logger.debug(
            "boarddocs: BD-GetMeetingsList error for %s (%s): %s",
            endpoint,
            type(exc).__name__,
            exc,
        )
        return []


async def _fetch_agenda_files(
    client: httpx.AsyncClient,
    base_url: str,
    committee_id: str,
    meeting: dict,
) -> list[dict]:
    """POST ``PRINT-AgendaDetailed`` for one meeting and parse attachments."""
    meeting_id = meeting.get("unique")
    if not meeting_id:
        return []
    endpoint = urljoin(base_url + "/", "PRINT-AgendaDetailed?open")
    body = f"id={meeting_id}&current_committee_id={committee_id}"
    try:
        resp = await client.post(
            endpoint,
            content=body,
            headers=_client_headers(
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": base_url,
                }
            ),
        )
        if resp.status_code != 200:
            logger.debug(
                "boarddocs: PRINT-AgendaDetailed %s returned HTTP %d for meeting %s",
                endpoint,
                resp.status_code,
                meeting_id,
            )
            return []
        return parse_boarddocs_agenda_files(resp.text, base_url=base_url)
    except httpx.HTTPError as exc:
        logger.debug(
            "boarddocs: PRINT-AgendaDetailed error for %s (%s): %s",
            endpoint,
            type(exc).__name__,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Download helper — prefer direct HTTP, fall back to Playwright session
# ---------------------------------------------------------------------------

async def fetch_boarddocs_document(
    source_page_url: str,
    media_url: str,
    *,
    timeout_ms: int | None = None,
) -> bytes:
    """Download a BoardDocs attachment, preferring a direct HTTP GET.

    BoardDocs attachment URLs (``…/files/{UNID}/$file/{name}``) are usually
    session-less — a plain GET with a browser-like UA + the portal as
    Referer returns the real PDF. Only when the server insists on a live
    session (returns HTML instead of a document content-type) do we fall
    back to :func:`fetch_document_via_playwright_session`, which re-opens
    Chromium and downloads through its cookie jar.

    Raises ``RuntimeError`` on any failure path so the caller's existing
    Celery retry wrapper (``ingest_scraped_media``) surfaces it as a
    retryable ``status="failed"`` row.
    """
    timeout = (
        httpx.Timeout(timeout_ms / 1000, connect=10.0)
        if timeout_ms
        else _default_timeout()
    )

    headers = {
        "User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT,
        "Referer": source_page_url,
        "Accept": "application/pdf, application/octet-stream, */*; q=0.01",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        try:
            resp = await client.get(media_url)
        except httpx.HTTPError as exc:
            logger.warning(
                "boarddocs: direct GET failed for %s (%s): %s — "
                "falling back to Playwright session",
                media_url,
                type(exc).__name__,
                exc,
            )
            return await fetch_document_via_playwright_session(
                source_page_url, media_url, timeout_ms=timeout_ms
            )

        if resp.status_code != 200:
            logger.warning(
                "boarddocs: direct GET returned HTTP %d for %s — "
                "falling back to Playwright session",
                resp.status_code,
                media_url,
            )
            return await fetch_document_via_playwright_session(
                source_page_url, media_url, timeout_ms=timeout_ms
            )

        content_type = (
            (resp.headers.get("content-type") or "").lower().split(";")[0].strip()
        )
        if content_type.startswith("text/html"):
            logger.info(
                "boarddocs: direct GET for %s returned HTML "
                "(content-type=%r) — server wants a session; "
                "falling back to Playwright",
                media_url,
                content_type,
            )
            return await fetch_document_via_playwright_session(
                source_page_url, media_url, timeout_ms=timeout_ms
            )

        raw = resp.content
        if not raw:
            logger.warning(
                "boarddocs: empty body from direct GET for %s — "
                "falling back to Playwright session",
                media_url,
            )
            return await fetch_document_via_playwright_session(
                source_page_url, media_url, timeout_ms=timeout_ms
            )

        logger.info(
            "boarddocs: downloaded %d bytes (content-type=%s) from %s",
            len(raw),
            content_type or "unknown",
            media_url,
        )
        return raw

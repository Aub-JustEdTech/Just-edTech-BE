"""
Simbli / eBoard Solutions public-portal document expander.

Simbli (``simbli.eboardsolutions.com``, hosted by eBOARDsolutions / GSBA) is an
ASP.NET WebForms shell fronting an Angular agenda UI and a small set of JSON
APIs. The public meeting archive lives at::

    /SB_Meetings/SB_MeetingListing.aspx?S={siteId}

Listing rows render ``javascript:void(0)`` links with ``onclick`` handlers that
call ``ViewMeeting("site","mid",...)`` — there are no real ``href``s to follow.
The shell HTML exposes a ``SecurityToken``, ``ConnectionString`` (``constr``)
and ``SchoolID`` that a JSON endpoint keys on::

    POST /Services/api/GetMeetingListing
      body: { SchoolID, SecurityToken, ConnectionString, FilterExp, ... }
      -> [{ Master_MeetingID, MM_MeetingTitle, MM_DateTime, MinutesStatus, ... }]

Each meeting opens a hybrid Angular page::

    /SB_Meetings/ViewMeeting.aspx?S={site}&MID={mid}      (agenda)
    /SB_Meetings/ViewMeeting.aspx?S={site}&MID={mid}&T=1   (published minutes)

Supporting documents are served from a session-sensitive handler::

    /Meetings/Attachment.aspx?AID={aid}&MID={mid}&S={site}

This module implements the flow as ``expand_simbli_meetings`` and a matching
download helper (``fetch_simbli_document``). It is a *hybrid*: Playwright warms
the Imperva/ASP.NET session cookies and drives the Angular ``ViewMeeting``
pages (cold httpx often gets a ~1KB Imperva challenge HTML), while the listing
is fetched over the JSON API using the warmed cookies. This mirrors the
BoardDocs structured-listing + Diligent-session-download patterns.

Soft-fail only: any per-meeting or listing error logs and continues so a single
bad tenant can't abort the whole scrape.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings
from app.services.web_scraper.board_platforms import (
    fetch_document_via_playwright_session,
    is_simbli_url,
    normalize_simbli_listing_url,
    simbli_site_id,
)
from app.services.web_scraper.year_filter import allowed_calendar_years

logger = logging.getLogger(__name__)

__all__ = [
    "expand_simbli_meetings",
    "fetch_simbli_document",
    "parse_listing_tokens",
    "build_view_meeting_url",
    "extract_view_meeting_attachments",
]

# 4-digit year extractor (shared convention with the other expanders).
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# Meetings returned per GetMeetingListing page (the shell pre-renders 50; the
# API honours the same chunk size when paginating via RecordStart).
_PAGE_SIZE = 50

# Endpoint paths relative to the Simbli host root.
_LISTING_API_PATH = "/Services/api/GetMeetingListing"
_VIEW_MEETING_PATH = "/SB_Meetings/ViewMeeting.aspx"
_ATTACHMENT_PATH = "/Meetings/Attachment.aspx"

# Heuristics for the Imperva/Incapsula challenge HTML returned to cold clients.
_CHALLENGE_MARKERS = ("incap_ses", "visid_incap", "Incapsula incident", "Imperva")


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        float(getattr(settings, "WEB_SCRAPER_TIMEOUT_SECONDS", 30)),
        connect=10.0,
    )


def _is_challenge_html(text: str) -> bool:
    """True when ``text`` looks like an Imperva/Incapsula challenge page."""
    if not text or len(text) > 4096:
        # Real challenge pages are tiny; full pages are far larger.
        return False
    low = text.lower()
    return any(m.lower() in low for m in _CHALLENGE_MARKERS)


# ---------------------------------------------------------------------------
# Token parsing from the listing shell HTML
# ---------------------------------------------------------------------------

# The shell embeds configuration into client-side JS / hidden inputs. These
# regexes tolerate either single or double quoting and surrounding whitespace.
_SECURITY_TOKEN_RE = re.compile(
    r"""["']?SecurityToken["']?\s*[:=]\s*["']([A-Za-z0-9+\-/=_]+)["']""",
    re.IGNORECASE,
)
_CONNECTION_STRING_RE = re.compile(
    r"""["']?(?:ConnectionString|constr)["']?\s*[:=]\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
#
# NOTE: do NOT add a bare ``S`` alternative here. An earlier version did, to
# catch a hypothetical ``S: "123"`` key, but it false-matched arbitrary CSS/JS
# substrings ending in the letter "s" followed by a colon and digits (e.g.
# ``border-radius:5px`` -> "s:5"), silently overriding the correct site id
# with "5" and breaking every listing API call for a real tenant. The ``S=``
# query param on the URL (``simbli_site_id``) is the reliable source of
# truth; this regex only looks for the fully-spelled ``SchoolID`` key, with a
# word boundary before it, as a page-embedded bonus/cross-check.
_SCHOOL_ID_RE = re.compile(
    r"""\bschoolid["']?\s*[:=]\s*["']?(\d+)["']?""",
    re.IGNORECASE,
)


def parse_listing_tokens(html: str, url: str | None = None) -> dict[str, str | None]:
    """Extract ``SecurityToken``, ``ConnectionString`` and ``SchoolID`` from HTML.

    Falls back to the ``S=`` query param on ``url`` when no SchoolID is
    embedded in the page. Returns a dict whose values are ``None`` when not
    found — callers should treat any missing token as "cannot list, skip".
    """
    tokens: dict[str, str | None] = {
        "security_token": None,
        "connection_string": None,
        "school_id": None,
    }
    if html:
        m = _SECURITY_TOKEN_RE.search(html)
        if m:
            tokens["security_token"] = m.group(1)

        m = _CONNECTION_STRING_RE.search(html)
        if m:
            tokens["connection_string"] = m.group(1)

        m = _SCHOOL_ID_RE.search(html)
        if m:
            tokens["school_id"] = m.group(1)

    # Fall back to the URL's S= query param when the page didn't embed a
    # SchoolID (or the page HTML is empty/unavailable).
    if not tokens["school_id"] and url:
        tokens["school_id"] = simbli_site_id(url)

    return tokens


# ---------------------------------------------------------------------------
# URL synthesis
# ---------------------------------------------------------------------------

def _host_root(url: str) -> str:
    """Return scheme + host (no path/query) for a Simbli URL."""
    split = urlsplit(url)
    return urlunsplit((split.scheme or "https", split.netloc, "", "", ""))


def build_view_meeting_url(
    base_url: str,
    site_id: str,
    meeting_id: str,
    *,
    minutes: bool = False,
) -> str:
    """Synthesize a ``ViewMeeting.aspx`` URL from a meeting id.

    ``minutes=True`` appends ``&T=1`` to load the published-minutes tab.
    """
    sep = "&" if "?" in _VIEW_MEETING_PATH else "?"
    url = f"{base_url}{_VIEW_MEETING_PATH}{sep}S={site_id}&MID={meeting_id}"
    if minutes:
        url += "&T=1"
    return url


def _build_attachment_url(
    base_url: str,
    *,
    aid: str,
    mid: str,
    site_id: str,
) -> str:
    """Synthesize a canonical ``Attachment.aspx`` URL."""
    return (
        f"{base_url}{_ATTACHMENT_PATH}?AID={aid}&MID={mid}&S={site_id}"
    )


# ---------------------------------------------------------------------------
# Meeting date / year parsing
# ---------------------------------------------------------------------------

def _meeting_year(meeting: dict) -> int | None:
    """Best-effort year extraction from a GetMeetingListing meeting row."""
    dt = meeting.get("MM_DateTime")
    if dt:
        year = _parse_year(dt)
        if year:
            return year
    # Fallbacks: some tenants surface MeetingDate / DisplayDate instead.
    for key in ("MeetingDate", "DisplayDate", "MM_Date"):
        v = meeting.get(key)
        if v:
            year = _parse_year(v)
            if year:
                return year
    title = meeting.get("MM_MeetingTitle") or meeting.get("Title") or ""
    m = _YEAR_RE.search(title)
    if m:
        return int(m.group(1))
    return None


def _parse_year(value: Any) -> int | None:
    """Extract a 4-digit year from an ISO/.NET date string or epoch."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        # ASP.NET often serialises dates as /Date(epoch_ms)/.
        return None
    if not isinstance(value, str):
        return None
    s = value.strip().strip("/")
    # /Date(1696118400000)/ form
    m = re.search(r"Date\((-?\d+)", s)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000.0, tz=UTC).year
        except (OverflowError, OSError, ValueError):
            pass
    # ISO 8601 / .NET "2025-09-08T18:00:00" or "09/08/2025 6:00 PM"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).year
        except ValueError:
            continue
    m = _YEAR_RE.search(s)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# GetMeetingListing API
# ---------------------------------------------------------------------------

def _filter_exp_for_year(year: int) -> str:
    """Build a Simbli FilterExp clause restricting meetings to one year."""
    return f"DateTime >= '01/01/{year}' AND DateTime <= '12/31/{year}'"


def _listing_request_body(
    *,
    site_id: str,
    security_token: str | None,
    connection_string: str | None,
    record_start: int,
    record_count: int = _PAGE_SIZE,
    filter_exp: str | None = None,
) -> dict:
    """Build the GetMeetingListing POST body.

    Mirrors the shape the SPA sends (``meetingCustGrd``). ``FilterExp`` is
    applied server-side; omitting it lists everything (used only as a
    fallback).
    """
    body: dict[str, Any] = {
        "ListingType": 0,  # 0 = All (incl. past)
        "RecordStart": record_start,
        "RecordCount": record_count,
        "SortColName": "DateTime",
        "IsSortDesc": True,
        "FilterExp": filter_exp,
        "IsUserLoggedIn": False,
        "SchoolID": site_id,
        "TimeZone": "-180",
    }
    if security_token:
        body["SecurityToken"] = security_token
    if connection_string:
        body["ConnectionString"] = connection_string
    return body


async def _post_meeting_listing(
    client: httpx.AsyncClient,
    base_url: str,
    body: dict,
) -> list[dict]:
    """Call GetMeetingListing and return the meeting rows (empty on soft-fail)."""
    url = f"{base_url}{_LISTING_API_PATH}"
    try:
        resp = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "simbli: GetMeetingListing request failed for %s (%s): %s",
            url,
            type(exc).__name__,
            exc,
        )
        return []
    if resp.status_code != 200:
        # Imperva/Incapsula sometimes blocks the API POST outright with a
        # 403 + a small "Incapsula incident ID" HTML body (distinct from the
        # 200+challenge-page case the Playwright listing warm-up guards
        # against) rather than passing the request through. Recognize that
        # shape specifically so logs point at the real cause instead of a
        # generic HTTP error.
        if _is_challenge_html(resp.text or ""):
            logger.warning(
                "simbli: GetMeetingListing blocked by Imperva/Incapsula "
                "(HTTP %d) for %s -- likely IP/fingerprint-based; retry from "
                "a different egress or the production scraper host",
                resp.status_code,
                url,
            )
        else:
            logger.warning(
                "simbli: GetMeetingListing returned HTTP %d for %s",
                resp.status_code,
                url,
            )
        return []
    text = resp.text or ""
    if _is_challenge_html(text):
        logger.warning("simbli: GetMeetingListing returned Imperva challenge for %s", url)
        return []
    try:
        data = resp.json()
    except json.JSONDecodeError:
        logger.warning("simbli: GetMeetingListing returned non-JSON for %s", url)
        return []
    # Response is either a bare list of meetings or an object wrapping one.
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict):
        for key in ("data", "Data", "Meetings", "meetings", "Result"):
            v = data.get(key)
            if isinstance(v, list):
                return [m for m in v if isinstance(m, dict)]
        # Single meeting dict?
        if any(k in data for k in ("Master_MeetingID", "MM_DateTime")):
            return [data]
    return []


async def _list_meetings(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    site_id: str,
    security_token: str | None,
    connection_string: str | None,
    allowed_years: set[int],
    max_meetings: int,
) -> list[dict]:
    """Page GetMeetingListing across each allowed year, year-gated + capped."""
    collected: list[dict] = []
    seen_ids: set[str] = set()
    for year in sorted(allowed_years, reverse=True):
        if len(collected) >= max_meetings:
            break
        record_start = 1
        # Per-year safety cap so one huge year can't starve the others.
        per_year = max_meetings - len(collected)
        year_collected = 0
        while year_collected < per_year:
            budget = min(_PAGE_SIZE, per_year - year_collected)
            body = _listing_request_body(
                site_id=site_id,
                security_token=security_token,
                connection_string=connection_string,
                record_start=record_start,
                record_count=budget,
                filter_exp=_filter_exp_for_year(year),
            )
            rows = await _post_meeting_listing(client, base_url, body)
            if not rows:
                break
            added = 0
            for meeting in rows:
                mid = str(
                    meeting.get("Master_MeetingID")
                    or meeting.get("MeetingID")
                    or ""
                )
                if not mid or mid in seen_ids:
                    continue
                # Server-side FilterExp should already restrict, but trust
                # the parsed year when present and re-gate defensively.
                myear = _meeting_year(meeting)
                if myear is not None and myear not in allowed_years:
                    continue
                seen_ids.add(mid)
                collected.append(meeting)
                added += 1
                year_collected += 1
                if len(collected) >= max_meetings:
                    break
            if added < budget:
                # Fewer rows than requested -> last page for this year.
                break
            record_start += budget
        if year_collected:
            logger.info(
                "simbli: site %s year %d -> %d meetings (total %d/%d)",
                site_id,
                year,
                year_collected,
                len(collected),
                max_meetings,
            )
    return collected


# ---------------------------------------------------------------------------
# ViewMeeting attachment harvest (Playwright)
# ---------------------------------------------------------------------------

# Selectors used to find supporting-document links inside the Angular agenda.
# Order matters: Attachment.aspx links are the canonical binary handler; the
# remaining patterns catch direct PDFs and print/export links.
_ATTACHMENT_HREF_RE = re.compile(r"Attachment\.aspx\?[^\"' ]+", re.IGNORECASE)


async def extract_view_meeting_attachments(
    page: Any,
    *,
    view_url: str,
    timeout_ms: int,
    settle_ms: int = 2000,
) -> list[dict]:
    """Navigate to a ViewMeeting page and harvest its document links.

    Returns dicts of shape ``{"url": abs_url, "name": label,
    "file_extension": ".pdf" | None}``. Empty list on soft-fail.

    The Angular agenda is JS-rendered, so we wait for networkidle and then
    scan the DOM for ``Attachment.aspx`` hrefs and direct file links.
    """
    try:
        await page.goto(view_url, wait_until="networkidle", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        # Angular SPAs sometimes keep a long-poll open; fall back to "load".
        logger.debug(
            "simbli: networkidle failed for %s (%s); retrying with load",
            view_url,
            type(exc).__name__,
        )
        try:
            await page.goto(view_url, wait_until="load", timeout=timeout_ms)
        except Exception as exc2:  # noqa: BLE001
            logger.warning(
                "simbli: ViewMeeting navigation failed for %s (%s): %s",
                view_url,
                type(exc2).__name__,
                exc2,
            )
            return []
    try:
        await page.wait_for_timeout(settle_ms)
    except Exception:  # noqa: BLE001
        pass

    # Collect hrefs + visible text via a single evaluate so the Angular
    # rendering doesn't race the DOM scan.
    try:
        links = await page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const push = (href, text) => {
                    if (!href) return;
                    try { href = new URL(href, document.baseURI).href; } catch (e) { return; }
                    if (seen.has(href)) return;
                    seen.add(href);
                    out.push({ url: href, name: (text || '').trim().slice(0, 200) });
                };
                document.querySelectorAll('a[href]').forEach(a => push(a.href, a.innerText || a.textContent));
                // Angular sometimes binds href via attributes rather than the property.
                document.querySelectorAll('a').forEach(a => {
                    const href = a.getAttribute('href') || a.getAttribute('data-href');
                    if (href) push(href, a.innerText || a.textContent);
                });
                return out;
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "simbli: link extraction failed for %s (%s): %s",
            view_url,
            type(exc).__name__,
            exc,
        )
        return []

    results: list[dict] = []
    for link in links or []:
        href = link.get("url") or ""
        if not href:
            continue
        low = href.lower()
        is_attachment = "attachment.aspx" in low
        is_file = (
            low.endswith(".pdf")
            or low.endswith(".doc")
            or low.endswith(".docx")
            or low.endswith(".xls")
            or low.endswith(".xlsx")
            or low.endswith(".ppt")
            or low.endswith(".pptx")
        )
        if not (is_attachment or is_file):
            continue
        ext = None
        for candidate in (
            ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ):
            if low.endswith(candidate):
                ext = candidate
                break
        if is_attachment and not ext:
            ext = ".pdf"  # Attachment.aspx defaults to PDF/Office; refined at download
        results.append(
            {
                "url": href,
                "name": link.get("name") or "",
                "file_extension": ext,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Expander — public entry point
# ---------------------------------------------------------------------------

async def expand_simbli_meetings(
    page: Any,
    *,
    page_url: str,
    timeout_ms: int = 60_000,
    max_meetings: int = 24,
    settle_ms: int = 2000,
) -> list[dict]:
    """Collect published agenda/minutes/attachment documents from a Simbli portal.

    Returns a list of media dicts in the same shape as the BoardDocs /
    Diligent / BoardOnTrack expanders::

        {
            "url": <absolute attachment / view URL>,
            "media_type": "document",
            "source_page_url": <ViewMeeting URL>,
            "doc_year": <meeting year or None>,
            "name": <file label>,
            "file_extension": ".pdf" | ".html" | None,
        }

    The list is year-gated against ``SCHOOL_SCRAPER_ALLOWED_YEARS`` and capped
    at ``max_meetings``. Login-walled / empty / Imperva-challenge portals
    soft-fail to ``[]``.
    """
    if not is_simbli_url(page_url):
        logger.debug("simbli: not a Simbli URL, skipping: %s", page_url)
        return []

    listing_url = normalize_simbli_listing_url(page_url) or page_url
    site_id = simbli_site_id(listing_url)
    if not site_id:
        logger.warning("simbli: no S= site id in %s; cannot list meetings", listing_url)
        return []

    base_url = _host_root(listing_url)
    allowed_years = allowed_calendar_years()
    if not allowed_years:
        logger.info("simbli: no allowed years configured; skipping %s", listing_url)
        return []

    # Step 1: warm the session in Playwright and harvest the listing shell
    # tokens (SecurityToken / ConnectionString) the API keys on.
    try:
        await page.goto(listing_url, wait_until="networkidle", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "simbli: listing networkidle failed for %s (%s); retrying with load",
            listing_url,
            type(exc).__name__,
        )
        try:
            await page.goto(listing_url, wait_until="load", timeout=timeout_ms)
        except Exception as exc2:  # noqa: BLE001
            logger.warning(
                "simbli: listing navigation failed for %s (%s): %s",
                listing_url,
                type(exc2).__name__,
                exc2,
            )
            return []
    try:
        await page.wait_for_timeout(settle_ms)
    except Exception:  # noqa: BLE001
        pass

    shell_html = ""
    try:
        shell_html = await page.content()
    except Exception as exc:  # noqa: BLE001
        logger.warning("simbli: could not read listing HTML for %s: %s", listing_url, exc)

    if _is_challenge_html(shell_html):
        logger.warning(
            "simbli: listing returned Imperva challenge for %s; aborting expander",
            listing_url,
        )
        return []

    tokens = parse_listing_tokens(shell_html, url=listing_url)
    security_token = tokens["security_token"]
    connection_string = tokens["connection_string"]
    parsed_site_id = tokens["school_id"] or site_id

    # Harvest cookies from the Playwright context for the API call.
    try:
        context = page.context
        cookies = await context.cookies(listing_url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("simbli: could not read cookies (%s); proceeding header-only", exc)
        cookies = []

    cookie_header = "; ".join(
        f"{c.get('name')}={c.get('value')}"
        for c in cookies
        if c.get("name") and c.get("value")
    )

    headers = {
        "User-Agent": settings.SCHOOL_SCRAPER_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": listing_url,
        "Origin": base_url,
    }
    if cookie_header:
        headers["Cookie"] = cookie_header

    # Step 2: list meetings via the JSON API (server-side year filtering).
    timeout = _default_timeout()
    meetings: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        meetings = await _list_meetings(
            client,
            base_url,
            site_id=parsed_site_id,
            security_token=security_token,
            connection_string=connection_string,
            allowed_years=allowed_years,
            max_meetings=max_meetings,
        )

    if not meetings:
        logger.info("simbli: no meetings found for site %s on %s", parsed_site_id, listing_url)
        return []

    logger.info(
        "simbli: site %s -> %d meetings to expand on %s",
        parsed_site_id,
        len(meetings),
        listing_url,
    )

    # Step 3: expand each meeting — agenda page + (optionally) minutes page.
    collected: dict[str, dict] = {}
    for meeting in meetings:
        mid = str(
            meeting.get("Master_MeetingID")
            or meeting.get("MeetingID")
            or ""
        )
        if not mid:
            continue
        meeting_year = _meeting_year(meeting)
        title = (
            meeting.get("MM_MeetingTitle")
            or meeting.get("Title")
            or "Simbli meeting"
        )
        minutes_published = (
            str(meeting.get("MinutesStatus") or "").upper() == "PUBLISHED"
        )

        for minutes_tab in (False, True):
            if minutes_tab and not minutes_published:
                continue
            view_url = build_view_meeting_url(
                base_url, parsed_site_id, mid, minutes=minutes_tab
            )
            label = "minutes" if minutes_tab else "agenda"
            docs = await extract_view_meeting_attachments(
                page,
                view_url=view_url,
                timeout_ms=timeout_ms,
                settle_ms=settle_ms,
            )
            if not docs:
                # HTML fallback: no binary attachments found. Emit the
                # ViewMeeting page itself as an .html document so the meeting
                # body is still ingestible (mirrors open-navigator's approach).
                key = view_url
                if key not in collected:
                    collected[key] = {
                        "url": view_url,
                        "media_type": "document",
                        "source_page_url": view_url,
                        "doc_year": meeting_year,
                        "name": f"{title} ({label})",
                        "file_extension": ".html",
                    }
                continue
            for doc in docs:
                key = doc["url"]
                if key in collected:
                    continue
                collected[key] = {
                    "url": doc["url"],
                    "media_type": "document",
                    "source_page_url": view_url,
                    "doc_year": meeting_year,
                    "name": doc.get("name") or f"{title} ({label})",
                    "file_extension": doc.get("file_extension"),
                }

    logger.info(
        "simbli: site %s -> %d documents collected on %s",
        parsed_site_id,
        len(collected),
        listing_url,
    )
    return list(collected.values())


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

async def fetch_simbli_document(
    source_page_url: str,
    media_url: str,
    *,
    timeout_ms: int | None = None,
) -> bytes:
    """Download a Simbli document using a Playwright-warmed session.

    Simbli's ``Attachment.aspx`` handler is session-sensitive (Imperva +
    ASP.NET cookies), so a cold httpx GET frequently returns the Imperva
    challenge page instead of the file. This helper establishes a real
    browser session against ``source_page_url`` (a ``ViewMeeting`` page) and
    then fetches the document through the same context — identical to the
    generic ``fetch_document_via_playwright_session`` path, but kept as a
    dedicated entry point so ingest routing can special-case Simbli later.
    """
    return await fetch_document_via_playwright_session(
        source_page_url,
        media_url,
        timeout_ms=timeout_ms,
    )

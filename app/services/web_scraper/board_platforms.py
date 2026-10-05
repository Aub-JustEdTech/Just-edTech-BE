"""
Board-meeting platform support.

Some school districts publish meeting minutes/agendas/packets on hosted
board-meeting platforms that live on a *different* domain than the school
site itself (e.g. ``go.boarddocs.com``, ``*.diligentoneplatform.com``,
``app2.boardontrack.com``). These platforms are JS/iframe-heavy SPAs and
their document download links are frequently session-bound.

This module centralizes:

* The configurable allowlist of recognized board-platform domains
  (``is_board_platform_url``) used to relax the crawler's otherwise strict
  same-domain filter for a single off-domain hop.
* ``fetch_document_via_playwright_session`` — a download helper that
  re-establishes a real browser session (cookies/referrer) before fetching
  a document URL, since a cold ``httpx.GET`` from the Celery worker often
  gets redirected to a login/error page on these platforms.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlsplit, urlunsplit

from app.core.config import settings

logger = logging.getLogger(__name__)


def _board_platform_domains() -> tuple[str, ...]:
    """Return the configured board-platform domain suffixes, lowercased."""
    raw = getattr(settings, "SCHOOL_SCRAPER_BOARD_PLATFORM_DOMAINS", None) or []
    return tuple(str(d).strip().lower().lstrip(".") for d in raw if str(d).strip())


def is_board_platform_url(url: str | None) -> bool:
    """True when ``url``'s host ends with any configured board-platform domain.

    Suffix-matched so subdomains qualify (e.g. ``acushnetschools.community.
    diligentoneplatform.com`` matches the ``diligentoneplatform.com`` entry).
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    domains = _board_platform_domains()
    if not domains:
        return False
    return any(host == d or host.endswith(f".{d}") for d in domains)


def board_platform_kind(url: str | None) -> str | None:
    """Return which configured board-platform family a URL belongs to.

    Returns one of: "boarddocs", "diligent", "boardontrack", "granicus",
    "simbli", or None.

    Maps each configured domain suffix to its platform kind:
    - diligentoneplatform.com -> "diligent"
    - boardontrack.com -> "boardontrack"
    - boarddocs.com -> "boarddocs"
    - granicus.com -> "granicus"
    - eboardsolutions.com -> "simbli"
    """
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return None
    if not host:
        return None

    # Map domain suffix -> platform kind
    domain_to_kind = {
        "diligentoneplatform.com": "diligent",
        "boardontrack.com": "boardontrack",
        "boarddocs.com": "boarddocs",
        "granicus.com": "granicus",
        "eboardsolutions.com": "simbli",
    }

    for domain, kind in domain_to_kind.items():
        if host == domain or host.endswith(f".{domain}"):
            return kind

    return None


# ---------------------------------------------------------------------------
# BoardDocs URL hygiene
# ---------------------------------------------------------------------------

# Path segments that mark a BoardDocs public SPA entry. Order matters: we
# prefer the canonical ``/Public`` form when normalising.
_BOARDDOCS_PUBLIC_ENTRIES: tuple[str, ...] = ("public", "vpublic")
_BOARDDOCS_PRIVATE_ENTRIES: tuple[str, ...] = ("private", "vprivate")


def is_boarddocs_url(url: str | None) -> bool:
    """True when ``url`` is on a BoardDocs host (any subdomain of boarddocs.com)."""
    return board_platform_kind(url) == "boarddocs"


def is_boarddocs_private_url(url: str | None) -> bool:
    """True when ``url`` points at a BoardDocs *private* (login-walled) portal.

    BoardDocs public portals live under ``.../Board.nsf/Public`` (or
    ``.../vpublic?open``). The ``/Private`` / ``/vprivate`` variants are the
    authenticated board portal and are never scrapeable without credentials —
    callers must detect and skip them so ENS-style confirmed rows don't
    enqueue unfetchable work.
    """
    if not is_boarddocs_url(url):
        return False
    try:
        path = (urlsplit(url).path or "").lower().strip()
    except Exception:  # noqa: BLE001
        return False
    if not path:
        return False
    # Match the trailing entry segment, e.g. ``/Board.nsf/Private``.
    parts = [p for p in path.split("/") if p]
    if not parts:
        return False
    last = parts[-1]
    return last in _BOARDDOCS_PRIVATE_ENTRIES


def normalize_boarddocs_url(url: str | None) -> str | None:
    """Normalise a BoardDocs portal URL to a canonical public entry.

    - Drop client-only fragments (``#tab-meetings`` and friends) — the SPA
      tab is unreachable for an HTTP client and the crawler already strips
      fragments on crawl-normalize; this keeps the expander's view consistent.
    - Map ``vpublic?open`` onto the canonical ``/Public`` entry so the same
      NSF agents resolve regardless of which entry the school linked to.
    - Leave non-BoardDocs URLs untouched (returns the input unchanged) and
      return ``None`` for empty/invalid input.

    ``/Private`` URLs are returned unchanged — callers should gate them with
    :func:`is_boarddocs_private_url` before invoking the expander.
    """
    if not url:
        return None
    if not is_boarddocs_url(url):
        return url

    split = urlsplit(url)
    scheme = split.scheme or "https"
    netloc = split.netloc
    path = split.path or ""
    query = split.query or ""

    # Strip client-only fragment entirely.
    fragment = ""

    # Map vpublic?open -> Public (same NSF app, canonical entry).
    path_lower = path.lower()
    if path_lower.endswith("/vpublic"):
        path = path[: -len("vpublic")] + "Public"
        # ``?open`` is a no-op cue for the NSF agent; drop it once we've
        # mapped to the canonical Public entry.
        query = ""
    elif path_lower.endswith("/vprivate"):
        # Don't rewrite Private variants — keep the signal for the skip check.
        pass

    return urlunsplit((scheme, netloc, path, query, fragment))


def boarddocs_portal_base(url: str | None) -> str | None:
    """Return the NSF base URL (``.../Board.nsf``) for a BoardDocs portal.

    The expander posts NSF agents (``BD-GetMeetingsList``, ``PRINT-AgendaDetailed``)
    relative to this base. Returns ``None`` for non-BoardDocs URLs or URLs that
    don't carry a ``Board.nsf`` (case-insensitive) segment — those are
    malformed and the expander cannot safely derive an agent URL from them.
    """
    if not is_boarddocs_url(url):
        return None
    try:
        split = urlsplit(url)
    except Exception:  # noqa: BLE001
        return None
    parts = [p for p in (split.path or "").split("/") if p]
    base_idx = None
    for i, p in enumerate(parts):
        if p.lower() == "board.nsf":
            base_idx = i
            break
    if base_idx is None:
        return None
    base_path = "/" + "/".join(parts[: base_idx + 1])
    return urlunsplit((split.scheme or "https", split.netloc, base_path, "", ""))


# ---------------------------------------------------------------------------
# Simbli / eBoard Solutions URL hygiene
# ---------------------------------------------------------------------------

# Path segments that mark a Simbli public meeting-listing entry. The canonical
# archive URL is ``/SB_Meetings/SB_MeetingListing.aspx?S={siteId}``; schools
# sometimes link to the site home (``/Index.aspx?S=``) or directly to a
# ``ViewMeeting`` detail page. The expander always works from the listing,
# so we normalise those variants.
_SIMBLI_LISTING_PATH = "/sb_meetings/sb_meetinglisting.aspx"


def is_simbli_url(url: str | None) -> bool:
    """True when ``url`` is on a Simbli / eBoard Solutions host."""
    return board_platform_kind(url) == "simbli"


def _query_param(query: str, name: str) -> str | None:
    """Return the first value of ``name`` from a query string, case-insensitively."""
    if not query:
        return None
    name_lower = name.lower()
    for pair in query.split("&"):
        if "=" in pair:
            key, _, value = pair.partition("=")
            if key.lower() == name_lower:
                return value
    return None


def simbli_site_id(url: str | None) -> str | None:
    """Extract the ``S`` (site id) query param from a Simbli URL.

    The site id is the district key the listing/detail APIs key on (e.g.
    ``36030338``). Returns ``None`` for non-Simbli URLs or URLs without ``S``.
    """
    if not is_simbli_url(url):
        return None
    try:
        query = urlsplit(url).query or ""
    except Exception:  # noqa: BLE001
        return None
    return _query_param(query, "S")


def normalize_simbli_listing_url(url: str | None) -> str | None:
    """Normalise a Simbli URL to the canonical meeting-listing entry.

    - ``/Index.aspx?S=`` -> ``/SB_Meetings/SB_MeetingListing.aspx?S=``
    - ``/SB_Meetings/ViewMeeting.aspx?S=&MID=`` -> listing (keeps ``S``,
      drops ``MID`` — the listing enumerates meetings; ``MID`` is only
      relevant for a single detail page).
    - Bare host with ``?S=`` -> listing.
    - Non-Simbli URLs are returned unchanged. Returns ``None`` for empty
      input. URLs without ``S`` are returned unchanged so the expander can
      log and skip them.
    """
    if not url:
        return None
    if not is_simbli_url(url):
        return url
    try:
        split = urlsplit(url)
    except Exception:  # noqa: BLE001
        return url
    scheme = split.scheme or "https"
    netloc = split.netloc
    query = split.query or ""
    site_id = _query_param(query, "S")
    if not site_id:
        # Without a site id the listing API cannot be called; leave URL as-is
        # so the caller can log/skip rather than silently emit a bad URL.
        return url
    # Always point at the canonical listing path, preserving the host.
    return urlunsplit(
        (scheme, netloc, _SIMBLI_LISTING_PATH, f"S={site_id}", "")
    )


# ---------------------------------------------------------------------------
# Session-aware document download for board platforms
# ---------------------------------------------------------------------------

# Content-Type prefixes we treat as "this is an actual document, not an HTML
# error/login page returned in lieu of the file". Board platforms sometimes
# answer a session-less/cookie-less request with a 200 HTML login page, so we
# explicitly reject text/html responses on the document path.
_DOCUMENT_CONTENT_TYPE_PREFIXES: tuple[str, ...] = (
    "application/pdf",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/zip",
    "text/plain",
)


async def fetch_document_via_playwright_session(
    source_page_url: str,
    media_url: str,
    *,
    timeout_ms: int | None = None,
) -> bytes:
    """Download ``media_url`` after first establishing a browser session.

    Board-meeting platforms (BoardDocs, Diligent Community, BoardOnTrack)
    frequently serve document download endpoints that require cookies /
    referrer set during an earlier page navigation. A cold ``httpx.GET``
    from the Celery ingest worker (whose browser context is long gone by
    the time ``ingest_scraped_media`` runs) therefore often gets redirected
    to a login/error page instead of the real file.

    This helper:

    1. Launches a short-lived headless Chromium (same launch-kwargs pattern
       the scraper services use, including ``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH``
       for the Docker system-Chromium path).
    2. Navigates to ``source_page_url`` with ``wait_until="networkidle"`` so
       any session cookies the platform sets on first visit are captured
       in the browser context.
    3. Issues ``context.request.get(media_url)`` — the context's request
       API automatically attaches the cookies set in step 2 and sends the
       referrer of the last navigated page, so the download endpoint sees
       a request indistinguishable from one issued by the page itself.
    4. Verifies the response is 2xx AND not an HTML error page, then returns
       the raw bytes.

    Raises ``RuntimeError`` on any failure (non-2xx, HTML error page,
    navigation error, Playwright unavailable). The caller
    (``_materialize_media`` in ``app/tasks/school_scraper_tasks.py``) is
    already wrapped by ``ingest_scraped_media``'s try/except + Celery
    ``max_retries=3``, so failures surface as ``status="failed"`` rows
    that get retried automatically.
    """
    if timeout_ms is None:
        timeout_ms = int(getattr(settings, "WEB_SCRAPER_TIMEOUT_SECONDS", 30) * 1000)

    launch_kwargs = _chromium_launch_kwargs()

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()

        # Step 1: navigate to the source page to establish the session.
        # networkidle waits until no network activity for ~500ms — these are
        # SPAs that set session cookies via XHR during initial load.
        try:
            await page.goto(
                source_page_url, wait_until="networkidle", timeout=timeout_ms
            )
        except Exception as exc:  # noqa: BLE001
            # networkidle can time out on long-polling platforms; fall back to
            # "load" so we still get whatever cookies the initial navigation
            # set rather than aborting the whole download.
            logger.warning(
                "board_platforms: networkidle navigation failed for %s (%s); "
                "retrying with wait_until='load'",
                source_page_url,
                type(exc).__name__,
            )
            await page.goto(source_page_url, wait_until="load", timeout=timeout_ms)

        # Brief settle so any post-load cookie/XHR rounds complete.
        try:
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

        # Step 2: fetch the document via the same browser context. The
        # context.request API reuses the cookies + shared connection pool.
        response = await context.request.get(media_url, timeout=timeout_ms)
        status = response.status
        if not 200 <= status < 300:
            body_snippet = (await response.text())[:300]
            raise RuntimeError(
                f"board_platforms: document fetch returned HTTP {status} for "
                f"{media_url}. Body: {body_snippet!r}"
            )

        content_type = (
            (response.headers.get("content-type") or "").lower().split(";")[0].strip()
        )
        if content_type and content_type.startswith("text/html"):
            body_snippet = (await response.text())[:300]
            raise RuntimeError(
                f"board_platforms: document fetch for {media_url} returned HTML "
                f"(content-type={content_type!r}) — likely a login/error page, "
                f"not the real file. Body: {body_snippet!r}"
            )

        # When content-type is present but not a known document type, still
        # accept it — platforms sometimes serve binary via generic
        # application/octet-stream or unusual vendor types. The downstream
        # processor (PDF/DOCX parser) will reject malformed bytes anyway.
        raw = await response.body()
        if not raw:
            raise RuntimeError(f"board_platforms: empty response body for {media_url}")

        logger.info(
            "board_platforms: downloaded %d bytes (content-type=%s) from %s "
            "after establishing session via %s",
            len(raw),
            content_type or "unknown",
            media_url,
            source_page_url,
        )
        return raw
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass


def _chromium_launch_kwargs() -> dict:
    """Build kwargs for ``chromium.launch()``.

    Mirrors ``SchoolScraperService._chromium_launch_kwargs`` /
    ``SchemaDrivenCrawler._chromium_launch_kwargs`` so the Docker
    system-Chromium path (``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH``) is
    honoured. Duplicated here to avoid a circular import between
    ``board_platforms`` and ``school_scraper_service`` (the latter imports
    from the former for ``is_board_platform_url``).
    """
    kwargs: dict = {"headless": True}
    executable_path = getattr(settings, "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", None)
    if executable_path:
        kwargs["executable_path"] = executable_path
        kwargs["args"] = ["--no-sandbox"]
    return kwargs

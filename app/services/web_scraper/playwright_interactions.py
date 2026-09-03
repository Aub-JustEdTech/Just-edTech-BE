"""
Playwright post-load interactions for CMS widgets that hide documents
behind same-URL clicks (no navigation).

Currently handles SharpSchool / Blackboard ``ContentItemModern`` document
explorers (``#documentList`` with ``content_folder`` / ``GetFile.ashx``).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.services.web_scraper.year_filter import (
    allowed_calendar_years,
    evaluate_media_year,
)

logger = logging.getLogger(__name__)

_SHARPSCHOOL_LIST_SELECTOR = "#documentList"
_GETFILE_PATH = "getfile.ashx"
_DEFAULT_MAX_FOLDERS = 50
_DEFAULT_SETTLE_MS = 1500
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def looks_like_sharpschool_document_list(html: str) -> bool:
    """True when HTML contains a SharpSchool ContentItem document explorer."""
    if not html:
        return False
    lower = html.lower()
    return (
        'id="documentlist"' in lower
        or "content_folder" in lower
        or "contentitemmodern" in lower
        or _GETFILE_PATH in lower
    )


def folder_may_contain_allowed_years(folder_name: str) -> bool:
    """
    Decide whether a folder name is worth opening under the year filter.

    School-year folders like ``2022-2023`` keep both years; open the folder
    when any extracted year is allowed, or when no year is present (mixed /
    archive indexes — individual files are filtered later).
    """
    years = [int(m.group(1)) for m in _YEAR_RE.finditer(folder_name or "")]
    if not years:
        return True
    allowed = allowed_calendar_years()
    return any(y in allowed for y in years)


def _extension_from_name(name: str | None) -> str:
    if not name:
        return ".pdf"
    lower = name.lower()
    for ext in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt"):
        if lower.endswith(ext):
            return ext
    return ".pdf"


def is_sharpschool_getfile_url(url: str) -> bool:
    """True for SharpSchool ``GetFile.ashx`` download endpoints."""
    try:
        return _GETFILE_PATH in urlparse(url).path.lower()
    except Exception:  # noqa: BLE001
        return False


async def expand_sharpschool_document_list(
    page: Any,
    *,
    page_url: str,
    timeout_ms: int = 60_000,
    max_folders: int = _DEFAULT_MAX_FOLDERS,
    settle_ms: int = _DEFAULT_SETTLE_MS,
) -> list[dict]:
    """
    BFS through SharpSchool folder widgets and collect ``GetFile.ashx`` links.

    Uses folder deep-links from the widget's ``copyLink`` clipboard URLs so
    each folder is opened via ``page.goto`` (stable) rather than click + back.
    """
    collected: dict[str, dict] = {}
    visited_folder_ids: set[str] = set()
    queue: list[str] = [page_url]
    pages_opened = 0

    while queue and pages_opened < max_folders:
        current = queue.pop(0)
        try:
            await page.goto(current, wait_until="load", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "SharpSchool folder goto failed for %s (%s): %s",
                current,
                type(exc).__name__,
                exc,
            )
            continue

        try:
            await page.wait_for_selector(
                _SHARPSCHOOL_LIST_SELECTOR, timeout=min(15_000, timeout_ms)
            )
        except Exception:  # noqa: BLE001
            # Root page may still have GetFile links outside a ready list.
            pass

        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)

        pages_opened += 1

        files = await page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const nodes = document.querySelectorAll(
                    'a.content_item[href*="GetFile.ashx"], a[href*="GetFile.ashx"]'
                );
                for (const e of nodes) {
                    const href = e.href;
                    if (!href || seen.has(href)) continue;
                    seen.add(href);
                    const nameEl = e.querySelector('.docTitle') || e;
                    const name = (nameEl.innerText || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    out.push({ url: href, name });
                }
                return out;
            }"""
        )
        for item in files or []:
            url = (item.get("url") or "").strip()
            if not url or not is_sharpschool_getfile_url(url):
                continue
            if url in collected:
                continue
            name = (item.get("name") or "").strip() or None
            # Hard gate: only calendar years in SCHOOL_SCRAPER_ALLOWED_YEARS
            # (currently 2023–2026). Unknown-year docs follow
            # SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR.
            _year, should_keep, skip_reason = evaluate_media_year(
                url=url,
                filename=name,
                source_page_url=page_url,
            )
            if not should_keep:
                logger.debug(
                    "Skipping SharpSchool file %r (%s)",
                    name or url,
                    skip_reason,
                )
                continue
            collected[url] = {
                "name": name,
                "url": url,
                "file_extension": _extension_from_name(name),
                "media_type": "document",
                "size_bytes": None,
                "source_page_url": page_url,
                "doc_year": _year,
            }

        folders = await page.evaluate(
            """() => {
                return [...document.querySelectorAll(
                    'a.content_folder[data-type="content_folder"]'
                )].map((e) => {
                    const id = e.getAttribute('data') || '';
                    const name = (
                        (e.querySelector('.docTitle') || e).innerText || ''
                    ).replace(/\\s+/g, ' ').trim();
                    const copy = document.getElementById('lnkCopy_' + id);
                    const deep = copy
                        ? copy.getAttribute('data-clipboard-text')
                        : null;
                    return { id, name, deep };
                });
            }"""
        )

        for folder in folders or []:
            folder_id = str(folder.get("id") or "")
            name = str(folder.get("name") or "")
            deep = (folder.get("deep") or "").strip()
            if not folder_id or folder_id in visited_folder_ids:
                continue
            if not deep or is_sharpschool_getfile_url(deep):
                continue
            if not folder_may_contain_allowed_years(name):
                logger.debug("Skipping out-of-range SharpSchool folder %r", name)
                continue
            visited_folder_ids.add(folder_id)
            queue.append(deep)

    logger.info(
        "SharpSchool expand on %s: %d folder pages, %d GetFile docs",
        page_url,
        pages_opened,
        len(collected),
    )
    return list(collected.values())


# ---------------------------------------------------------------------------
# Google Drive embedded folders + Google Sheets agenda tables
# ---------------------------------------------------------------------------

_DRIVE_FOLDER_RE = re.compile(
    r"drive\.google\.com/(?:embeddedfolderview\?id=|drive/folders/)([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DRIVE_FOLDER_URL_RE = re.compile(
    r"drive\.google\.com/(?:drive/folders/|folderview\?id=|embeddedfolderview\?id=)([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DRIVE_FILE_RE = re.compile(
    r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DRIVE_OPEN_RE = re.compile(
    r"drive\.google\.com/open(?:\?|&)(?:[^#\"']*&)?id=([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DOCS_FILE_RE = re.compile(
    r"docs\.google\.com/document/d/(?:e/)?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DEFAULT_MAX_DRIVE_FOLDERS = 50
_GOOGLE_URL_Q_RE = re.compile(
    r"https?://www\.google\.com/url\?[^\"\s]*?[?&]q=([^&\"\s]+)",
    re.IGNORECASE,
)
_DOC_LINK_LABEL_RE = re.compile(
    r"^(agenda|minutes|supporting\s+documents?|agenda\s*&\s*supporting\s+documents?)$",
    re.IGNORECASE,
)


def drive_uc_download_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def docs_export_pdf_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"


def unwrap_google_redirect(url: str) -> str:
    """Unwrap ``google.com/url?q=...`` redirects used inside Sheets embeds."""
    from urllib.parse import unquote, parse_qs, urlsplit

    if "google.com/url" not in url:
        return url
    qs = parse_qs(urlsplit(url).query)
    target = (qs.get("q") or [None])[0]
    return unquote(target) if target else url


def _keep_media(
    *,
    url: str,
    name: str | None,
    page_url: str,
    file_extension: str,
    allow_unknown_year: bool = False,
) -> dict | None:
    year, should_keep, skip_reason = evaluate_media_year(
        url=url,
        filename=name,
        source_page_url=page_url,
    )
    if not should_keep:
        if allow_unknown_year:
            year = None
        else:
            logger.debug("Skipping Google embed file %r (%s)", name or url, skip_reason)
            return None
    return {
        "name": name,
        "url": url,
        "file_extension": file_extension,
        "media_type": "document",
        "size_bytes": None,
        "source_page_url": page_url,
        "doc_year": year,
    }


def classify_google_url(url: str | None) -> tuple[str | None, str | None]:
    """Classify a Google Drive/Docs URL.

    Returns ``(kind, resource_id)`` where *kind* is one of:
    ``folder``, ``file``, ``open`` (Drive file via ``open?id=``), ``gdoc``,
    or ``None`` when the URL is not a supported Google resource.
    """
    if not url:
        return None, None
    lowered = url.lower()

    if "docs.google.com/document" in lowered:
        m = _DOCS_FILE_RE.search(url)
        if m:
            return "gdoc", m.group(1)

    m = _DRIVE_FOLDER_URL_RE.search(url)
    if m:
        return "folder", m.group(1)

    m = _DRIVE_FILE_RE.search(url)
    if m:
        return "file", m.group(1)

    m = _DRIVE_OPEN_RE.search(url)
    if m:
        return "open", m.group(1)

    return None, None


def parse_drive_subfolders(folder_html: str) -> list[tuple[str, str]]:
    """Return ``(folder_id, folder_name)`` pairs from embeddedfolderview HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(folder_html or "", "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in soup.find_all(class_=re.compile(r"flip-entry", re.I)):
        anchor = entry.find("a", href=True)
        if not anchor:
            continue
        href = str(anchor.get("href") or "")
        match = _DRIVE_FOLDER_RE.search(href)
        if not match:
            continue
        folder_id = match.group(1)
        if folder_id in seen:
            continue
        seen.add(folder_id)
        title_el = entry.find(class_=re.compile(r"flip-entry-title", re.I))
        if title_el:
            name = title_el.get_text(" ", strip=True)
        else:
            name = anchor.get_text(" ", strip=True)
        out.append((folder_id, name or folder_id))
    return out


def parse_drive_folder_html(folder_html: str, *, page_url: str) -> list[dict]:
    """Parse ``embeddedfolderview`` HTML into downloadable Drive file media."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(folder_html or "", "html.parser")
    collected: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        m = _DRIVE_FILE_RE.search(href)
        if not m:
            continue
        file_id = m.group(1)
        name = a.get_text(" ", strip=True) or None
        title = a.find(class_=re.compile(r"title", re.I))
        if title:
            name = title.get_text(" ", strip=True) or name
        download = drive_uc_download_url(file_id)
        item = _keep_media(
            url=download,
            name=name,
            page_url=page_url,
            file_extension=_extension_from_name(name),
        )
        if item:
            collected[download] = item
    return list(collected.values())


def media_from_google_drive_file(
    file_id: str,
    *,
    page_url: str,
    name: str | None = None,
    allow_unknown_year: bool = False,
) -> dict | None:
    """Build a media dict for a single Drive file id."""
    download = drive_uc_download_url(file_id)
    return _keep_media(
        url=download,
        name=name,
        page_url=page_url,
        file_extension=_extension_from_name(name),
        allow_unknown_year=allow_unknown_year,
    )


def media_from_google_doc(
    doc_id: str,
    *,
    page_url: str,
    name: str | None = None,
    allow_unknown_year: bool = False,
) -> dict | None:
    """Build a media dict for a Google Doc exported as PDF."""
    export_url = docs_export_pdf_url(doc_id)
    doc_name = name or f"google-doc-{doc_id}.pdf"
    return _keep_media(
        url=export_url,
        name=doc_name,
        page_url=page_url,
        file_extension=".pdf",
        allow_unknown_year=allow_unknown_year,
    )


async def crawl_google_drive_folder(
    folder_id: str,
    *,
    page_url: str,
    fetch_text,
    max_folders: int = _DEFAULT_MAX_DRIVE_FOLDERS,
) -> list[dict]:
    """Walk a Drive folder tree via ``embeddedfolderview`` and collect files."""
    queue: list[tuple[str, str | None]] = [(folder_id, None)]
    visited: set[str] = set()
    collected: dict[str, dict] = {}

    while queue and len(visited) < max_folders:
        current_id, folder_name = queue.pop(0)
        if current_id in visited:
            continue
        if folder_name and not folder_may_contain_allowed_years(folder_name):
            logger.debug(
                "Skipping Drive subfolder %r (no allowed years in name)",
                folder_name,
            )
            continue

        visited.add(current_id)
        folder_url = (
            f"https://drive.google.com/embeddedfolderview?id={current_id}#list"
        )
        try:
            folder_html = await fetch_text(folder_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Drive folder fetch failed for %s (%s): %s",
                current_id,
                type(exc).__name__,
                exc,
            )
            continue
        if not folder_html:
            continue

        for item in parse_drive_folder_html(folder_html, page_url=page_url):
            collected[item["url"]] = item

        for sub_id, sub_name in parse_drive_subfolders(folder_html):
            if sub_id not in visited:
                queue.append((sub_id, sub_name))

    logger.info(
        "Google Drive folder walk for %s: visited=%d folders, docs=%d",
        page_url,
        len(visited),
        len(collected),
    )
    return list(collected.values())


async def extract_google_drive_folder_media(
    html: str,
    *,
    page_url: str,
    fetch_text,
) -> list[dict]:
    """
    Find ``embeddedfolderview`` / Drive folder iframes in ``html``, fetch each
    folder listing (recursively), and return downloadable document media.
    """
    folder_ids = list(dict.fromkeys(_DRIVE_FOLDER_RE.findall(html or "")))
    if not folder_ids:
        return []

    collected: dict[str, dict] = {}
    for folder_id in folder_ids:
        items = await crawl_google_drive_folder(
            folder_id,
            page_url=page_url,
            fetch_text=fetch_text,
        )
        for item in items:
            collected[item["url"]] = item

    logger.info(
        "Google Drive folders on %s: %d roots, %d docs",
        page_url,
        len(folder_ids),
        len(collected),
    )
    return list(collected.values())


async def extract_google_sheets_embed_media(
    page: Any,
    *,
    page_url: str,
    settle_ms: int = 3000,
    ready_timeout_ms: int = 20_000,
) -> list[dict]:
    """
    Pull Agenda/Minutes/Supporting-Document links out of embedded Google Sheets.

    Finalsite/Apptegy pages (e.g. Nauset/Brewster) embed a published sheet in an
    iframe; link text is visible in the parent page but the real ``href``s live
    only inside the spreadsheet frame.
    """
    # Wait until at least one Sheets iframe has populated hyperlinks.
    # Early frames often load a chrome-only shell with 0 anchors.
    deadline_slices = max(1, ready_timeout_ms // 500)
    for _ in range(deadline_slices):
        ready = False
        for frame in page.frames:
            frame_url = getattr(frame, "url", "") or ""
            if "docs.google.com/spreadsheets" not in frame_url:
                continue
            try:
                count = await frame.locator("a[href]").count()
            except Exception:  # noqa: BLE001
                count = 0
            if count > 0:
                ready = True
                break
        if ready:
            break
        try:
            await page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            break

    if settle_ms > 0:
        try:
            await page.wait_for_timeout(settle_ms)
        except Exception:  # noqa: BLE001
            pass

    collected: dict[str, dict] = {}

    for frame in page.frames:
        frame_url = getattr(frame, "url", "") or ""
        if "docs.google.com/spreadsheets" not in frame_url:
            continue
        try:
            rows = await frame.evaluate(
                """() => {
                  const dateRe = /\\d{1,2}[-/.]\\d{1,2}[-/.]\\d{2,4}/;
                  const out = [];
                  const rows = document.querySelectorAll('table tr');
                  for (const tr of rows) {
                    const cells = [...tr.querySelectorAll('td, th')];
                    if (!cells.length) continue;
                    const cellTexts = cells.map(c =>
                      (c.innerText || '').replace(/\\s+/g, ' ').trim()
                    );
                    const dateText = cellTexts.find(t => dateRe.test(t)) || '';
                    for (const a of tr.querySelectorAll('a[href]')) {
                      const label = (a.innerText || '').replace(/\\s+/g,' ').trim();
                      out.push({ dateText, label, href: a.href });
                    }
                  }
                  if (!out.length) {
                    for (const a of document.querySelectorAll('a[href]')) {
                      out.push({
                        dateText: '',
                        label: (a.innerText || '').replace(/\\s+/g,' ').trim(),
                        href: a.href,
                      });
                    }
                  }
                  return out;
                }"""
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Sheets frame scrape failed (%s): %s", type(exc).__name__, exc)
            continue

        for row in rows or []:
            label = (row.get("label") or "").strip()
            if not _DOC_LINK_LABEL_RE.match(label):
                continue
            raw_href = unwrap_google_redirect((row.get("href") or "").strip())
            if not raw_href:
                continue
            date_text = (row.get("dateText") or "").strip()
            name = f"{date_text} {label}".strip() if date_text else label

            drive_m = _DRIVE_FILE_RE.search(raw_href)
            docs_m = _DOCS_FILE_RE.search(raw_href)
            if drive_m:
                url = drive_uc_download_url(drive_m.group(1))
                ext = ".pdf"
            elif docs_m:
                url = docs_export_pdf_url(docs_m.group(1))
                ext = ".pdf"
            else:
                continue

            item = _keep_media(
                url=url,
                name=name,
                page_url=page_url,
                file_extension=ext,
            )
            if item:
                collected[url] = item

    logger.info(
        "Google Sheets embeds on %s: %d docs",
        page_url,
        len(collected),
    )
    return list(collected.values())


# ---------------------------------------------------------------------------
# Iframe content merging for board-meeting platforms
# ---------------------------------------------------------------------------


# Playwright exposes the top page itself as page.main_frame, plus an entry in
# page.frames. To avoid duplicating the parent document's HTML when merging,
# we skip the frame whose URL matches the top page URL.
def _frame_is_top_frame(frame: Any, top_url: str) -> bool:
    """True when ``frame`` is the top-level frame (not a nested iframe)."""
    parent = getattr(frame, "parent_frame", None)
    if parent is not None:
        return False
    # Cross-check: about:blank / data: frames sometimes report parent_frame=None
    # on some Playwright builds; match URL too as a safety net.
    frame_url = (getattr(frame, "url", "") or "").strip()
    return not frame_url or frame_url == top_url


async def merge_iframe_content(page: Any, *, top_url: str) -> str:
    """Return the parent page HTML concatenated with the HTML of each iframe.

    Board-meeting platforms (BoardDocs, Diligent Community, BoardOnTrack)
    inject the real meeting/agenda content into nested ``<iframe>``s rather
    than the top document. ``page.content()`` only returns the parent
    document, so any links / text inside those iframes are invisible to the
    downstream extractor.

    This helper mirrors the existing per-frame content-reading pattern used
    for Google Sheets embeds (see ``extract_google_sheets_embed_media``) but
    is generic: it concatenates the parent HTML with the inner HTML of every
    non-top frame that has a URL, skipping frames that fail to serialize
    (cross-origin or detached frames raise on ``content()``).

    The merged HTML is wrapped in ``<div data-frame-url="...">`` markers so
    the downstream BeautifulSoup-based extractors treat each iframe's
    anchors/links as ordinary document content.
    """
    try:
        parent_html = await page.content()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "merge_iframe_content: parent page.content() failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        parent_html = ""

    parts: list[str] = [parent_html] if parent_html else []

    try:
        frames = list(page.frames)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "merge_iframe_content: page.frames access failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return parent_html

    for frame in frames:
        if _frame_is_top_frame(frame, top_url):
            continue
        frame_url = (getattr(frame, "url", "") or "").strip()
        if not frame_url:
            continue
        try:
            frame_html = await frame.content()
        except Exception as exc:  # noqa: BLE001
            # Cross-origin iframes throw on content() access; that's expected
            # for genuinely third-party embeds (e.g. YouTube) and we silently
            # skip them — same as the Sheets embed path does.
            logger.debug(
                "merge_iframe_content: frame.content() failed for %s (%s): %s",
                frame_url,
                type(exc).__name__,
                exc,
            )
            continue
        if not frame_html or not frame_html.strip():
            continue
        parts.append(f'\n<div data-frame-url="{frame_url}">\n{frame_html}\n</div>\n')

    merged = "\n".join(parts)
    if len(parts) > 1:
        logger.info(
            "merge_iframe_content: merged %d frames into parent HTML for %s "
            "(parent=%d bytes, merged=%d bytes)",
            len(parts) - 1,
            top_url,
            len(parent_html),
            len(merged),
        )
    return merged


# ---------------------------------------------------------------------------
# Board platform interaction layers (Diligent + BoardOnTrack)
# ---------------------------------------------------------------------------


async def expand_diligent_meetings(
    page: Any,
    *,
    page_url: str,
    timeout_ms: int = 60_000,
    max_meetings: int = 24,
    settle_ms: int = 1500,
) -> list[dict]:
    """
    Navigate Diligent Community portal to collect published agendas/minutes.

    Flow:
    1. Navigate to MeetingSchedule.aspx (calendar widget)
    2. Extract meeting entries (name, date, detail link)
    3. Year-gate: skip meetings outside SCHOOL_SCRAPER_ALLOWED_YEARS
    4. Visit each meeting's detail page (MeetingInformation.aspx?Id=...)
    5. Extract published agenda/packet/minutes links
    6. Return media dicts (year-gated, capped at max_meetings)
    """
    from datetime import datetime
    from urllib.parse import urljoin

    from app.core.config import settings

    collected: dict[str, dict] = {}
    meetings_visited = 0

    # Derive MeetingSchedule URL
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(page_url)
    schedule_path = "/Portal/MeetingSchedule.aspx"
    schedule_url = urlunparse(
        (parsed.scheme, parsed.netloc, schedule_path, "", "", "")
    )

    try:
        await page.goto(schedule_url, wait_until="load", timeout=timeout_ms)
        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Diligent: failed to navigate to MeetingSchedule.aspx (%s): %s",
            type(exc).__name__,
            exc,
        )
        return []

    # Extract meeting list from calendar widget
    try:
        meetings = await page.evaluate(
            """() => {
                const out = [];
                // Diligent's calendar widget typically uses table rows or divs
                // with meeting info + links to detail pages
                const rows = document.querySelectorAll('tr[data-meeting-id], .meeting-row, a[href*="MeetingInformation.aspx"]');
                for (const el of rows) {
                    let link = el;
                    if (el.tagName !== 'A') {
                        link = el.querySelector('a[href*="MeetingInformation.aspx"]');
                    }
                    if (!link || !link.href) continue;
                    
                    const dateText = el.textContent || '';
                    const name = (link.textContent || '').trim();
                    out.push({
                        url: link.href,
                        name: name,
                        dateText: dateText
                    });
                }
                return out;
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Diligent: meeting list extraction failed (%s): %s", type(exc).__name__, exc)
        return []

    if not meetings:
        logger.info("Diligent: no meetings found on %s", schedule_url)
        return []

    allowed = allowed_calendar_years()

    for meeting in meetings:
        if meetings_visited >= max_meetings:
            break

        # Parse date and year-gate
        date_text = str(meeting.get("dateText") or "")
        meeting_year = None
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y"):
            try:
                parsed_date = datetime.strptime(date_text.strip()[:20], fmt)
                meeting_year = parsed_date.year
                break
            except Exception:  # noqa: BLE001
                continue

        # Also try to extract 4-digit year directly
        if not meeting_year:
            match = _YEAR_RE.search(date_text)
            if match:
                meeting_year = int(match.group(1))

        if meeting_year and meeting_year not in allowed:
            logger.debug(
                "Diligent: skipping meeting %r (year=%d not in %s)",
                meeting.get("name"),
                meeting_year,
                sorted(allowed),
            )
            continue

        detail_url = meeting.get("url")
        if not detail_url:
            continue

        meetings_visited += 1

        # Navigate to meeting detail page
        try:
            await page.goto(detail_url, wait_until="load", timeout=timeout_ms)
            if settle_ms > 0:
                await page.wait_for_timeout(settle_ms)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Diligent: failed to load meeting detail %s (%s): %s",
                detail_url,
                type(exc).__name__,
                exc,
            )
            continue

        # Extract document links (agenda, packet, minutes)
        try:
            doc_links = await page.evaluate(
                """() => {
                    const out = [];
                    // Diligent uses /document/{id} URLs for agenda/packets/minutes
                    // Look for these specific patterns
                    const links = document.querySelectorAll('a[href*="/document/"]');
                    
                    for (const a of links) {
                        // Skip hidden links (not yet published) and javascript/empty hrefs
                        if (!a.href || 
                            a.href.startsWith('javascript:') || 
                            a.href.endsWith('#') ||
                            a.classList.contains('hidden') ||
                            a.style.display === 'none') {
                            continue;
                        }
                        
                        const text = (a.textContent || a.title || '').trim();
                        const href = a.href;
                        
                        // Only collect document links that look like agenda/minutes/packet
                        // Skip navigation/splitscreen links
                        if (text && (
                            text.toLowerCase().includes('agenda') ||
                            text.toLowerCase().includes('minutes') ||
                            text.toLowerCase().includes('packet')
                        ) && !text.toLowerCase().includes('splitscreen')) {
                            out.push({ url: href, name: text });
                        }
                    }
                    return out;
                }"""
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Diligent: document link extraction failed for %s (%s): %s",
                detail_url,
                type(exc).__name__,
                exc,
            )
            continue

        for doc in doc_links:
            doc_url = str(doc.get("url") or "").strip()
            if not doc_url or doc_url in collected:
                continue

            name = str(doc.get("name") or "").strip() or None
            
            # Build media dict directly with the meeting_year we already know
            # (instead of using _keep_media which tries to infer year from URL/name)
            item = {
                "name": name,
                "url": doc_url,
                "file_extension": _extension_from_name(name) or ".pdf",
                "media_type": "document",
                "size_bytes": None,
                "source_page_url": detail_url,
                "doc_year": meeting_year,  # Use the meeting year directly
            }
            collected[doc_url] = item

    logger.info(
        "Diligent: visited %d meetings on %s, collected %d documents",
        meetings_visited,
        page_url,
        len(collected),
    )
    return list(collected.values())


async def expand_boardontrack_meetings(
    page: Any,
    *,
    page_url: str,
    timeout_ms: int = 60_000,
    max_meetings: int = 24,
    settle_ms: int = 1500,
) -> list[dict]:
    """
    Navigate BoardOnTrack public portal to collect published agendas/minutes.

    Flow:
    1. From portal home (/public/{id}/home), navigate to Meetings tab
    2. Extract meeting list (name, date, agenda/minutes links)
    3. Year-gate: skip meetings outside SCHOOL_SCRAPER_ALLOWED_YEARS
    4. Collect agenda/minutes links directly from list (or navigate to detail if needed)
    5. Return media dicts (year-gated, capped at max_meetings)
    """
    from datetime import datetime
    from urllib.parse import urljoin

    from app.core.config import settings

    collected: dict[str, dict] = {}
    meetings_visited = 0

    # Navigate to meetings/year page (similar to Diligent calendar view)
    try:
        # BoardOnTrack structure: /public/{org}/home -> /public/{org}/year
        year_url = page_url.replace('/home', '/year')
        await page.goto(year_url, wait_until="load", timeout=timeout_ms)
        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "BoardOnTrack: failed to navigate to year view (%s): %s",
            type(exc).__name__,
            exc,
        )

    # Extract meeting detail URLs (not agenda/minutes yet - those are on detail pages)
    try:
        meetings = await page.evaluate(
            """() => {
                const out = [];
                // BoardOnTrack uses a[href*="/meeting/"] for meeting detail links
                const meetingLinks = document.querySelectorAll('a[href*="/meeting/"]');
                const seen = new Set();
                
                for (const link of meetingLinks) {
                    const href = link.href;
                    // Avoid duplicates (same meeting linked multiple times)
                    if (seen.has(href)) continue;
                    seen.add(href);
                    
                    // Try to find date in link text or nearby
                    let dateText = (link.textContent || '').trim();
                    
                    // If link text doesn't have a date, look in parent container
                    if (!dateText.match(/\\d{4}/)) {
                        const container = link.closest('div, li, tr');
                        if (container) {
                            dateText = container.textContent.trim();
                        }
                    }
                    
                    out.push({
                        meetingUrl: href,
                        dateText: dateText
                    });
                }
                
                return out;
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "BoardOnTrack: meeting list extraction failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return []

    if not meetings:
        logger.info("BoardOnTrack: no meetings found on %s", page_url)
        return []

    allowed = allowed_calendar_years()

    # Now navigate to each meeting detail page to get agenda/minutes links
    for meeting in meetings:
        if meetings_visited >= max_meetings:
            break

        # Parse date and year-gate
        date_text = str(meeting.get("dateText") or "")
        meeting_year = None
        
        # Try common date formats (BoardOnTrack uses "Aug 12 2026" format)
        for fmt in ("%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                # Clean up whitespace/newlines in date text
                date_clean = " ".join(date_text.split())
                parsed_date = datetime.strptime(date_clean, fmt)
                meeting_year = parsed_date.year
                break
            except Exception:  # noqa: BLE001
                continue

        # Also try to extract 4-digit year directly
        if not meeting_year:
            match = _YEAR_RE.search(date_text)
            if match:
                meeting_year = int(match.group(1))

        if meeting_year and meeting_year not in allowed:
            logger.debug(
                "BoardOnTrack: skipping meeting (year=%d not in %s)",
                meeting_year,
                sorted(allowed),
            )
            continue

        meeting_url = meeting.get("meetingUrl")
        if not meeting_url:
            continue

        meetings_visited += 1

        # Navigate to meeting detail page to get agenda/minutes
        try:
            await page.goto(meeting_url, wait_until="load", timeout=timeout_ms)
            if settle_ms > 0:
                await page.wait_for_timeout(settle_ms)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "BoardOnTrack: failed to load meeting detail %s (%s): %s",
                meeting_url,
                type(exc).__name__,
                exc,
            )
            continue

        # First, extract agenda/minutes VIEWER URLs from meeting detail page
        try:
            viewer_links = await page.evaluate(
                """() => {
                    const out = [];
                    const allLinks = document.querySelectorAll('a');
                    
                    for (const link of allLinks) {
                        const href = link.href || '';
                        const text = (link.textContent || '').trim().toLowerCase();
                        
                        // Skip non-document links
                        if (!href || href.startsWith('javascript:') || href.endsWith('#')) {
                            continue;
                        }
                        
                        // Look for viewer page links (agenda/minutes)
                        if ((href.includes('/agenda/') || text.includes('agenda')) && 
                            !href.includes('download')) {
                            out.push({
                                url: href,
                                type: 'agenda'
                            });
                        } else if ((href.includes('/minutes/') || text.includes('minutes')) && 
                                   !href.includes('download')) {
                            out.push({
                                url: href,
                                type: 'minutes'
                            });
                        }
                    }
                    
                    return out;
                }"""
            )
            
            # Now navigate to each viewer page and extract download links
            doc_links = []
            for viewer in viewer_links:
                viewer_url = viewer.get('url')
                if not viewer_url:
                    continue
                    
                try:
                    await page.goto(viewer_url, wait_until="load", timeout=timeout_ms)
                    await page.wait_for_timeout(1000)
                    
                    # Extract download PDF links from viewer page
                    download_links = await page.evaluate(
                        """() => {
                            const out = [];
                            const allLinks = document.querySelectorAll('a');
                            
                            for (const link of allLinks) {
                                const href = link.href || '';
                                const text = (link.textContent || '').trim().toLowerCase();
                                
                                if (!href) continue;
                                
                                // Look for download PDF links
                                if (text.includes('download') && text.includes('pdf')) {
                                    let docName = link.textContent.trim();
                                    
                                    // Clean up name
                                    if (text.includes('agenda') && text.includes('packet')) {
                                        docName = 'Agenda Packet';
                                    } else if (text.includes('agenda')) {
                                        docName = 'Agenda';
                                    } else if (text.includes('minutes')) {
                                        docName = 'Minutes';
                                    }
                                    
                                    out.push({
                                        url: href,
                                        name: docName
                                    });
                                }
                            }
                            
                            return out;
                        }"""
                    )
                    
                    doc_links.extend(download_links)
                    
                except Exception as inner_exc:  # noqa: BLE001
                    logger.debug(
                        "BoardOnTrack: failed to extract download links from viewer %s (%s): %s",
                        viewer_url,
                        type(inner_exc).__name__,
                        inner_exc,
                    )
                    continue
                    
        except Exception as exc:  # noqa: BLE001
            doc_links = []
            logger.debug(
                "BoardOnTrack: viewer link extraction failed for %s (%s): %s",
                meeting_url,
                type(exc).__name__,
                exc,
            )

        # Collect agenda and minutes links
        for doc in doc_links:
            doc_url = str(doc.get("url") or "").strip()
            if not doc_url or doc_url in collected:
                continue

            name = str(doc.get("name") or "").strip() or "Document"
            
            # Build media dict directly with the meeting_year we already know
            item = {
                "name": name,
                "url": doc_url,
                "file_extension": _extension_from_name(name) or ".pdf",
                "media_type": "document",
                "size_bytes": None,
                "source_page_url": meeting_url,
                "doc_year": meeting_year,  # Use the meeting year directly
            }
            collected[doc_url] = item

    logger.info(
        "BoardOnTrack: visited %d meetings on %s, collected %d documents",
        meetings_visited,
        page_url,
        len(collected),
    )
    return list(collected.values())


async def expand_granicus_meetings(
    page: Any,
    *,
    page_url: str,
    timeout_ms: int = 60_000,
    max_meetings: int = 24,
    settle_ms: int = 1500,
) -> list[dict]:
    """
    Navigate Granicus public portal (or iframe) to collect published agendas/minutes PDFs.

    Flow:
    1. Navigate to Granicus ViewPublisher page (may be embedded iframe URL)
    2. Extract meeting list from publisher page (meetings with dates and PDF links)
    3. Year-gate: skip meetings outside SCHOOL_SCRAPER_ALLOWED_YEARS
    4. Collect CloudFront PDF links directly (don't navigate to HTML viewers)
    5. Return media dicts (year-gated, capped at max_meetings)
    
    Note: Granicus uses two types of links:
    - HTML viewers (AgendaViewer.php, MinutesViewer.php) - display HTML content
    - CloudFront PDFs (d3n9y02raazwpg.cloudfront.net) - actual downloadable PDFs
    We extract the CloudFront PDFs directly from the publisher page.
    """
    from datetime import datetime
    from urllib.parse import urljoin

    from app.core.config import settings

    collected: dict[str, dict] = {}
    meetings_visited = 0

    # Navigate to Granicus publisher page (may already be there, or navigate to iframe URL)
    try:
        # If page_url is a school site with Granicus iframe, we need to extract iframe URL
        # For now, assume we're given the Granicus URL directly
        # TODO: Handle iframe extraction if needed
        if "ViewPublisher.php" not in page_url:
            logger.debug("Granicus: URL does not contain ViewPublisher.php, may need iframe extraction")
        
        await page.goto(page_url, wait_until="load", timeout=timeout_ms)
        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Granicus: failed to navigate to %s (%s): %s",
            page_url,
            type(exc).__name__,
            exc,
        )
        return []

    # Extract meetings with dates and PDF links
    try:
        meetings = await page.evaluate(
            """() => {
                const out = [];
                
                // Look for meeting containers (typically table rows or divs)
                const containers = document.querySelectorAll('tr, div[class*="meeting"], div[class*="event"]');
                
                for (const container of containers) {
                    const text = container.textContent || '';
                    
                    // Look for date patterns
                    const dateMatch = text.match(/\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},?\\s+\\d{4}\\b/i);
                    
                    if (!dateMatch) continue;
                    
                    const dateText = dateMatch[0];
                    const links = container.querySelectorAll('a');
                    const pdfUrls = [];
                    
                    for (const link of links) {
                        const href = link.href || '';
                        const linkText = (link.textContent || '').trim();
                        
                        if (!href || href.startsWith('javascript:')) continue;
                        
                        // CloudFront PDFs (actual downloadable files)
                        if (href.includes('cloudfront.net') || href.toLowerCase().endsWith('.pdf')) {
                            pdfUrls.push({
                                url: href,
                                name: linkText || 'Document'
                            });
                        }
                    }
                    
                    if (pdfUrls.length > 0) {
                        out.push({
                            dateText: dateText,
                            pdfUrls: pdfUrls
                        });
                    }
                }
                
                return out;
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Granicus: meeting list extraction failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return []

    if not meetings:
        logger.info("Granicus: no meetings with PDFs found on %s", page_url)
        return []

    allowed = allowed_calendar_years()

    for meeting in meetings:
        if meetings_visited >= max_meetings:
            break

        # Parse date and year-gate
        date_text = str(meeting.get("dateText") or "")
        meeting_year = None
        
        # Try common date formats (Granicus uses "Aug 11, 2026" format)
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
            try:
                # Clean up whitespace
                date_clean = " ".join(date_text.split())
                parsed_date = datetime.strptime(date_clean, fmt)
                meeting_year = parsed_date.year
                break
            except Exception:  # noqa: BLE001
                continue

        # Also try to extract 4-digit year directly
        if not meeting_year:
            match = _YEAR_RE.search(date_text)
            if match:
                meeting_year = int(match.group(1))

        if meeting_year and meeting_year not in allowed:
            logger.debug(
                "Granicus: skipping meeting (year=%d not in %s)",
                meeting_year,
                sorted(allowed),
            )
            continue

        meetings_visited += 1

        # Collect PDF links from this meeting
        pdf_urls = meeting.get("pdfUrls", [])
        for pdf in pdf_urls:
            doc_url = str(pdf.get("url") or "").strip()
            if not doc_url or doc_url in collected:
                continue

            name = str(pdf.get("name") or "").strip() or "Document"
            
            # Build media dict directly with the meeting_year we already know
            item = {
                "name": name,
                "url": doc_url,
                "file_extension": ".pdf",  # CloudFront PDFs
                "media_type": "document",
                "size_bytes": None,
                "source_page_url": page_url,
                "doc_year": meeting_year,  # Use the meeting year directly
            }
            collected[doc_url] = item

    logger.info(
        "Granicus: visited %d meetings on %s, collected %d documents",
        meetings_visited,
        page_url,
        len(collected),
    )
    return list(collected.values())

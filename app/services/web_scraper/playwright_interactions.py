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
                logger.debug(
                    "Skipping out-of-range SharpSchool folder %r", name
                )
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
_DRIVE_FILE_RE = re.compile(
    r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_DOCS_FILE_RE = re.compile(
    r"docs\.google\.com/document/d/(?:e/)?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
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
) -> dict | None:
    year, should_keep, skip_reason = evaluate_media_year(
        url=url,
        filename=name,
        source_page_url=page_url,
    )
    if not should_keep:
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


async def extract_google_drive_folder_media(
    html: str,
    *,
    page_url: str,
    fetch_text,
) -> list[dict]:
    """
    Find ``embeddedfolderview`` / Drive folder iframes in ``html``, fetch each
    folder listing, and return downloadable document media.
    """
    folder_ids = list(dict.fromkeys(_DRIVE_FOLDER_RE.findall(html or "")))
    if not folder_ids:
        return []

    collected: dict[str, dict] = {}
    for folder_id in folder_ids:
        folder_url = (
            f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
        )
        try:
            folder_html = await fetch_text(folder_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Drive folder fetch failed for %s (%s): %s",
                folder_id,
                type(exc).__name__,
                exc,
            )
            continue
        if not folder_html:
            continue
        for item in parse_drive_folder_html(folder_html, page_url=page_url):
            collected[item["url"]] = item

    logger.info(
        "Google Drive folders on %s: %d folders, %d docs",
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
            logger.debug(
                "Sheets frame scrape failed (%s): %s", type(exc).__name__, exc
            )
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

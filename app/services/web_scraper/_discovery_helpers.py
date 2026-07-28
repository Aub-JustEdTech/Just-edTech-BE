"""
Shared discovery helpers used by both SchoolScraperService (keyword-based)
and SchemaDrivenCrawler (LLM-based).

Extracted so the two services don't duplicate the sitemap / robots.txt /
nav-crawl / Playwright-auto-detect logic. Each sitemap/nav function takes a
`fetch_text` callable (and, for nav-crawl, optional Playwright callables) so
it is decoupled from any specific service's httpx client or browser state.

SchoolScraperService delegates its static helpers here and its instance
sitmap/nav collectors call the module-level functions passing `self._fetch_text`.
SchemaDrivenCrawler does the same with its own fetch.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# HTML fingerprints that indicate JavaScript-rendered navigation.
# When any of these strings appear in the raw httpx response body, the
# in-page navigation (sidebars, section menus, etc.) is likely injected by
# client-side JS and httpx alone will miss links.
_JS_RENDER_SIGNALS: tuple[str, ...] = (
    "finalsitestatic.com",   # Finalsite CMS  (e.g. Boston Public Schools)
    'id="__next"',           # Next.js
    'id="__nuxt"',           # Nuxt.js
    'ng-version="',          # Angular
    "data-reactroot",        # React (legacy attr)
    "_next/static/",         # Next.js static asset path
    "__nuxt_island",         # Nuxt.js islands
    "blackboard.com/",       # Blackboard LMS
    "eschoolsolutions.com",  # eSchool Solutions CMS
    "ccms_documentlinklisting",  # Catapult CMS "Document Link Listing" widget
    "catapultcms.com",       # Catapult CMS (edu2.catapultcms.com utilities)
    "ccms-contentelement",   # Catapult CMS generic content element wrapper
    "apptegy.net",           # Apptegy / Thrillshare CMS (Nuxt SSR)
    "thrillshare.com",       # Apptegy document CDN / API host
)

# Type alias: an async callable that takes a URL and returns the response body
# text (or None on failure). Both services implement this as a thin wrapper
# around their httpx client.
FetchText = Callable[[str], Awaitable[str | None]]


def html_needs_playwright(html: str) -> bool:
    """
    Return True when the raw HTML contains fingerprints of a JavaScript-heavy
    CMS or SPA framework whose navigation is injected client-side.

    Checked against _JS_RENDER_SIGNALS (case-sensitive substring match —
    the signals are lowercase/mixed-case literals that appear verbatim in
    real pages, so a full lower() pass is unnecessary and avoids false
    positives on content text).
    """
    return any(signal in html for signal in _JS_RENDER_SIGNALS)


def parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """
    Parse a sitemap XML string.

    Returns:
        (page_urls, child_sitemap_urls) — child_sitemap_urls is non-empty
        only when the document is a <sitemapindex>.
    """
    page_urls: list[str] = []
    child_sitemaps: list[str] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("XML parse error: %s", exc)
        return page_urls, child_sitemaps

    # Strip namespace prefix to get bare tag name
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    ns = {"sm": _SITEMAP_NS}

    if tag == "sitemapindex":
        for sitemap_elem in root.findall("sm:sitemap", ns):
            loc = sitemap_elem.findtext("sm:loc", namespaces=ns)
            if loc:
                child_sitemaps.append(loc.strip())
    elif tag == "urlset":
        for url_elem in root.findall("sm:url", ns):
            loc = url_elem.findtext("sm:loc", namespaces=ns)
            if loc:
                page_urls.append(loc.strip())

    return page_urls, child_sitemaps


async def collect_urls_from_sitemap(
    sitemap_url: str, fetch_text: FetchText
) -> list[str]:
    """
    Fetch a sitemap URL and recursively fetch any child sitemaps
    (one extra level for sitemap-index files).
    """
    all_urls: list[str] = []
    xml_text = await fetch_text(sitemap_url)
    if not xml_text:
        return all_urls

    page_urls, child_sitemaps = parse_sitemap_xml(xml_text)
    all_urls.extend(page_urls)

    for child_url in child_sitemaps:
        child_text = await fetch_text(child_url)
        if child_text:
            child_page_urls, _ = parse_sitemap_xml(child_text)
            all_urls.extend(child_page_urls)

    return all_urls


async def get_sitemap_url_from_robots(
    base_url: str, fetch_text: FetchText
) -> str | None:
    """Inspect robots.txt for a `Sitemap:` directive."""
    robots_text = await fetch_text(f"{base_url}/robots.txt")
    if not robots_text:
        return None
    for line in robots_text.splitlines():
        if line.lower().startswith("sitemap:"):
            return line.split(":", 1)[1].strip()
    return None


async def collect_urls_from_nav(
    base_url: str,
    fetch_text: FetchText,
    *,
    fetch_text_rendered: FetchText | None = None,
    ensure_playwright: Callable[[], Awaitable[None]] | None = None,
    has_browser: bool = False,
) -> list[str]:
    """
    Fetch the homepage and extract same-domain links found inside
    <nav> / <header> / <ul> elements, then fall back to all <a> tags.

    Fetch strategy:
    - Browser already available (has_browser=True): use fetch_text_rendered
      directly so that JS-rendered navigation menus are visible.
    - No browser yet: fetch with plain httpx; if the raw HTML contains known
      JS-framework fingerprints, call ensure_playwright() and re-fetch via
      fetch_text_rendered (if provided).

    Both Playwright callables are optional so this function degrades to
    plain-httpx nav-crawl when no browser is configured (e.g. the schema
    crawler's sitemap-seeding path).
    """
    if has_browser and fetch_text_rendered is not None:
        html = await fetch_text_rendered(base_url)
    else:
        html = await fetch_text(base_url)
        if html and html_needs_playwright(html):
            if ensure_playwright is not None and fetch_text_rendered is not None:
                await ensure_playwright()
                rendered = await fetch_text_rendered(base_url)
                if rendered:
                    html = rendered

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc

    seen: set[str] = set()
    urls: list[str] = []

    containers = soup.find_all(["nav", "header", "ul"]) or [soup]
    for container in containers:
        for a_tag in container.find_all("a", href=True):
            href = str(a_tag["href"]).strip()
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc == base_domain and full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)

    return urls

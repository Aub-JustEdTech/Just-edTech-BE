"""
Schema-driven crawler with a ranked link frontier.

This is the blog's core heuristic:
  1. Start on a seed page.
  2. Ask the LLM to classify it into a RelevantPage (has_data? has_data_links?
     is_archive? which sub-links look promising?).
  3. Push the promising sub-links onto a `to_visit` stack, ranked by confidence.
  4. Pop the highest-confidence link next. Repeat until the frontier is empty
     or a budget (max_pages) is hit.

All decision logic lives here — the LLM only does structured extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.services.web_scraper.markdown_converter import MarkdownConverter
from scripts.school_data.schema_crawl_poc.classifier import PageClassifier
from scripts.school_data.schema_crawl_poc.schemas import RelevantPage

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    """Result of crawling one seed URL."""

    seed_url: str
    pages_crawled: int
    data_pages: list[RelevantPage] = field(default_factory=list)
    visited_pages: list[RelevantPage] = field(default_factory=list)
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_url": self.seed_url,
            "pages_crawled": self.pages_crawled,
            "llm_calls": self.llm_calls,
            "data_pages": [p.model_dump() for p in self.data_pages],
            "visited_pages": [p.model_dump() for p in self.visited_pages],
            "errors": self.errors,
        }


class SchemaDrivenCrawler:
    """Crawl a school site using LLM page classifications instead of keyword URL matching."""

    def __init__(
        self,
        classifier: PageClassifier | None = None,
        markdown_converter: MarkdownConverter | None = None,
        *,
        max_pages: int = 10,
        confidence_threshold: float = 0.5,
        fetch_timeout_s: int | None = None,
        user_agent: str | None = None,
        skip_archival: bool = True,
    ):
        self.classifier = classifier or PageClassifier()
        self.md_converter = markdown_converter or MarkdownConverter()
        self.max_pages = max_pages
        self.confidence_threshold = confidence_threshold
        self.fetch_timeout = fetch_timeout_s or settings.WEB_SCRAPER_TIMEOUT_SECONDS
        self.user_agent = user_agent or settings.SCHOOL_SCRAPER_USER_AGENT
        self.skip_archival = skip_archival

    async def crawl(self, seed_url: str, today: date | None = None) -> CrawlResult:
        """Crawl from a seed URL, returning all discovered data pages."""
        today = today or date.today()
        seed_url = self._normalize_url(seed_url)
        base_domain = urlparse(seed_url).netloc

        result = CrawlResult(seed_url=seed_url, pages_crawled=0)

        # Ranked frontier: list of (url, confidence). We pop the highest
        # confidence first by sorting on each iteration — the frontier is
        # small (bounded by max_pages * N candidate links per page), so an
        # O(n log n) sort per step is negligible vs. the LLM call cost.
        frontier: list[tuple[str, float]] = [(seed_url, 1.0)]
        visited: set[str] = set()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.fetch_timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        ) as client:
            while frontier and result.pages_crawled < self.max_pages:
                # Pop the highest-confidence URL.
                frontier.sort(key=lambda x: x[1], reverse=True)
                current_url, _ = frontier.pop(0)

                if current_url in visited:
                    continue
                # Stay on the same domain.
                if urlparse(current_url).netloc != base_domain:
                    continue

                visited.add(current_url)
                result.pages_crawled += 1

                html = await self._fetch(client, current_url)
                if not html:
                    result.errors.append(f"fetch_failed: {current_url}")
                    continue

                markdown = self._render_markdown(html, current_url)
                if not markdown.strip():
                    result.errors.append(f"empty_markdown: {current_url}")
                    continue

                try:
                    page = await self.classifier.classify(current_url, markdown, today)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Classify failed for %s: %s", current_url, exc)
                    result.errors.append(f"classify_failed: {current_url}: {exc}")
                    continue
                result.llm_calls += 1
                result.visited_pages.append(page)

                # Record data pages (respecting archival skip if enabled).
                if page.has_data and page.data_page_info:
                    if self.skip_archival and page.data_page_info.is_archive:
                        logger.info(
                            "Skipping archival data page: %s (years=%s)",
                            current_url,
                            page.data_page_info.data_years_available,
                        )
                    else:
                        result.data_pages.append(page)

                # Expand the frontier with the model's suggested links.
                for candidate in page.possible_relevant_pages:
                    if candidate.confidence < self.confidence_threshold:
                        continue
                    abs_url = self._normalize_url(urljoin(current_url, candidate.url))
                    # Strip fragments — they don't change the page content.
                    abs_url = abs_url.split("#", 1)[0]
                    if abs_url in visited:
                        continue
                    if urlparse(abs_url).netloc != base_domain:
                        continue
                    frontier.append((abs_url, candidate.confidence))

        return result

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str | None:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug("Non-200 (%s) for %s", resp.status_code, url)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fetch failed for %s: %s", url, exc)
            return None

    def _render_markdown(self, html: str, url: str) -> str:
        """Render page HTML as markdown-with-links, preserving link text + href.

        We deliberately keep <nav>/<header>/<aside> in the markdown (the
        MarkdownConverter strips them), because the LLM needs to see the
        navigation links to suggest `possible_relevant_pages`. We only strip
        <script>/<style>.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        # html2text preserves <a href> as [text](url) — exactly the format
        # the blog uses for "page_text with links as markdown".
        return self.md_converter.converter.handle(str(soup)).strip()

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return url

"""
LLM (schema-driven) variant of SchoolScraperService.discover_candidate_urls.

Wraps SchemaDrivenCrawler and exposes the same `discover_candidate_urls`
contract (same return-dict shape) so the discovery endpoints can switch
between keyword and LLM ranking via the SCHOOL_SCRAPER_RANKING_MODE setting
without changing their call site. Candidate dicts carry schema-crawler fields
(data_type, is_archive, data_years_available) for offline scripts and APIs.

Additive — the keyword SchoolScraperService remains the default and is
untouched. Switching back to `keyword` is a zero-code rollback.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.schema_driven_crawler import (
    SchemaDrivenCrawler,
    _keyword_boost,
)

logger = logging.getLogger(__name__)

# Fallback candidate score for hub pages (e.g. "/school-committee",
# "/district-depts/school-committee") that link to meeting minutes/agendas
# but don't directly host them. Lower than any real data-page score (which is
# derived from LLM confidence * 100) so hub pages never outrank a page that
# actually hosts documents.
_HUB_FALLBACK_SCORE = 40


class SchemaDrivenSchoolScraperService:
    """Discovery service backed by the LLM page classifier.

    Mirrors SchoolScraperService.discover_candidate_urls' return shape so the
    discovery endpoints can swap implementations behind SCHOOL_SCRAPER_RANKING_MODE.
    """

    def __init__(
        self,
        classifier: PageClassifier | None = None,
        *,
        max_pages: int | None = None,
        max_depth: int | None = None,
        confidence_threshold: float | None = None,
        skip_archival: bool | None = None,
    ):
        self.classifier = classifier or PageClassifier()
        self.max_pages = max_pages or settings.SCHOOL_SCRAPER_LLM_MAX_PAGES
        self.max_depth = max_depth or settings.SCHOOL_SCRAPER_LLM_MAX_DEPTH
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.SCHOOL_SCRAPER_LLM_CONFIDENCE_THRESHOLD
        )
        self.skip_archival = (
            skip_archival if skip_archival is not None else settings.SCHOOL_SCRAPER_LLM_SKIP_ARCHIVAL
        )

    async def discover_candidate_urls(
        self,
        base_url: str,
        max_candidates: int | None = None,
    ) -> dict[str, Any]:
        """Run the schema-driven crawler and format results like the keyword service.

        Returns a dict with keys: base_url, discovery_method, total_urls_scanned,
        candidates. Each candidate carries the schema-crawler fields
        (data_type, is_archive, data_years_available) so they flow through
        dedupe_and_rank_candidates for API responses and offline JSON export.
        """
        base_url = (base_url or "").strip().rstrip("/")
        max_candidates = max_candidates or settings.SCHOOL_SCRAPER_MAX_CANDIDATES
        crawler = SchemaDrivenCrawler(
            classifier=self.classifier,
            max_pages=self.max_pages,
            max_depth=self.max_depth,
            confidence_threshold=self.confidence_threshold,
            skip_archival=self.skip_archival,
        )
        try:
            result = await crawler.crawl(base_url)
        finally:
            await crawler.close()

        # Map crawler data_pages → candidate dicts in the keyword service's shape.
        # Score by LLM confidence on the data_page_info (fallback to 1 for ranking).
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in result.data_pages:
            url = (page.url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            info = page.data_page_info
            candidates.append(
                {
                    "url": url,
                    "matched_keywords": [],  # LLM path doesn't keyword-match
                    "score": int((info.confidence * 100) if info else 100),
                    "data_type": info.data_type if info else None,
                    "is_archive": bool(info.is_archive) if info else False,
                    "data_years_available": list(info.data_years_available) if info else [],
                }
            )

        # Hub-page fallback: some districts only expose a navigation hub
        # (e.g. "/school-committee", "/district-depts/school-committee")
        # whose subpages the crawler didn't reach within max_pages, or whose
        # children the LLM classified as has_data=false. If we found no (or
        # very few) direct data pages, surface visited hub pages whose URL
        # path itself looks like a meeting-minutes page — better to return a
        # plausible hub than nothing.
        if len(candidates) < 1:
            for page in result.visited_pages:
                url = (page.url or "").strip()
                if not url or url in seen:
                    continue
                if not page.has_data_links:
                    continue
                if _keyword_boost(url) <= 0:
                    continue
                seen.add(url)
                candidates.append(
                    {
                        "url": url,
                        "matched_keywords": [],
                        "score": _HUB_FALLBACK_SCORE,
                        "data_type": "hub",
                        "is_archive": False,
                        "data_years_available": [],
                    }
                )

        # Rank by score descending and cap at max_candidates.
        candidates.sort(key=lambda c: c["score"], reverse=True)
        candidates = candidates[:max_candidates]

        # Re-rank so the returned list has a 1-based implicit rank order.
        return {
            "base_url": base_url,
            "discovery_method": "schema-driven-llm",
            "total_urls_scanned": result.pages_crawled,
            "candidates": candidates,
            "max_pages_limit_reached": result.max_pages_limit_reached,
        }

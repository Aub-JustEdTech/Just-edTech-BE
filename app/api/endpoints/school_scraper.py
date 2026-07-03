"""
Generalised school website scraper endpoints.

Two-step workflow:
  POST /school-scraper/discover    — finds candidate meeting-archive URLs
  POST /school-scraper/scrape-media — scrapes audio/video files from a confirmed URL
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, status

from app.schemas.school_scraper import (
    CandidateUrl,
    DiscoverRequest,
    DiscoverResponse,
    MediaFileResult,
    MediaTypeSummary,
    ScrapeMediaRequest,
    ScrapeMediaResponse,
)
from app.services.web_scraper.school_scraper_service import SchoolScraperService
from app.utils.dependencies import get_current_tenant_user
from app.schemas.users import User
from fastapi import Depends

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/discover",
    response_model=DiscoverResponse,
    status_code=status.HTTP_200_OK,
    summary="Discover candidate meeting-archive URLs from a school website",
)
async def discover_urls(
    request: DiscoverRequest,
    current_user: User = Depends(get_current_tenant_user),
) -> DiscoverResponse:
    """
    Step 1 — URL Discovery.

    Given a school website's base URL, this endpoint tries to find URLs that
    are likely to contain meeting minutes or board archives.

    **Discovery order:**
    1. `/wp-sitemap.xml` (WordPress sitemap index + child sitemaps)
    2. `/sitemap.xml` (generic sitemap or sitemap index)
    3. `robots.txt` (`Sitemap:` directive)
    4. Homepage navigation crawl (fallback)

    All collected URLs are then filtered by keywords such as *meeting*,
    *minutes*, *board*, *archives*, *agenda*, etc.

    **Response:** a ranked list of up to `max_candidates` candidate URLs.
    The frontend shows these to the user who picks one and calls `/scrape-media`.

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/school-scraper/discover" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"base_url": "https://www.akfcs.org"}'
    ```
    """
    async with SchoolScraperService(use_playwright=request.use_playwright) as scraper:
        try:
            result = await scraper.discover_candidate_urls(
                base_url=request.base_url,
                max_candidates=request.max_candidates,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Request timed out while accessing {request.base_url}",
            )
        except httpx.NetworkError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error while accessing {request.base_url}: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during URL discovery for %s", request.base_url)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {exc}",
            )

    candidates = [
        CandidateUrl(
            url=c["url"],
            matched_keywords=c["matched_keywords"],
            score=c["score"],
        )
        for c in result["candidates"]
    ]

    return DiscoverResponse(
        base_url=result["base_url"],
        discovery_method=result["discovery_method"],
        total_urls_scanned=result["total_urls_scanned"],
        total_candidates=len(candidates),
        candidates=candidates,
    )


@router.post(
    "/scrape-media",
    response_model=ScrapeMediaResponse,
    status_code=status.HTTP_200_OK,
    summary="Scrape audio/video files from a school meeting-archive page",
)
async def scrape_media(
    request: ScrapeMediaRequest,
    current_user: User = Depends(get_current_tenant_user),
) -> ScrapeMediaResponse:
    """
    Step 2 — Media Scraping.

    Given the confirmed meeting-archive URL (chosen by the user from `/discover`
    results), this endpoint scrapes the page and returns all audio and video
    file links found on it.

    It also follows same-domain sub-page links (e.g. year-archive pages like
    `/meeting-archives/2024/`) up to `crawl_depth` levels deep (default 1,
    max 3).

    **Supported media types (current):**
    - Video: `.mp4`, `.mov`, `.avi`, `.webm`, `.m4v`, `.mkv`
    - Audio: `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac`

    The returned file list can be displayed in the frontend. The user selects
    specific files which are then passed to the extraction pipeline.

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/school-scraper/scrape-media" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"url": "https://www.akfcs.org/meeting-archives/", "crawl_depth": 1}'
    ```
    """
    async with SchoolScraperService() as scraper:
        try:
            result = await scraper.scrape_media_files(
                page_url=request.url,
                crawl_depth=request.crawl_depth,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Request timed out while accessing {request.url}",
            )
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code if exc.response else "unknown"
            if http_status == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Page not found (404): {request.url}",
                )
            elif http_status == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access forbidden (403): {request.url}",
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"HTTP error {http_status} fetching {request.url}",
            )
        except httpx.NetworkError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error while accessing {request.url}: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during media scrape for %s", request.url)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error: {exc}",
            )

    media_files = [
        MediaFileResult(
            name=m["name"],
            url=m["url"],
            file_extension=m["file_extension"],
            media_type=m["media_type"],
            size_bytes=m["size_bytes"],
            source_page_url=m["source_page_url"],
        )
        for m in result["media_files"]
    ]

    type_counts: dict[str, int] = {"video": 0, "audio": 0, "document": 0}
    for m in media_files:
        type_counts[m.media_type] = type_counts.get(m.media_type, 0) + 1

    return ScrapeMediaResponse(
        source_url=result["source_url"],
        pages_crawled=result["pages_crawled"],
        total_media_found=len(media_files),
        media_type_summary=MediaTypeSummary(**type_counts),
        media_files=media_files,
    )

"""Tests for individual Google Drive/Docs link discovery in the school scraper.

Before this, ``_extract_media_from_page`` only recognised Drive links when
the *whole scrape URL* was a Drive folder (``classify_google_url`` on the
page itself) or when a Google Sheets iframe embed was present. A plain HTML
page — e.g. a "School Committee Document Archives" table whose Agenda/Minutes
cells link straight to ``drive.google.com/file/d/...`` — produced zero media
even though the documents were right there in the page's own HTML.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.web_scraper.school_scraper_service import SchoolScraperService

PAGE_URL = "https://example-district.org/school-committee-document-archives"


async def _extract(html: str):
    service = SchoolScraperService()
    media_files, _sub_pages = await service._extract_media_from_page(
        html,
        PAGE_URL,
        settings.SCHOOL_SCRAPER_VIDEO_EXTENSIONS,
        settings.SCHOOL_SCRAPER_AUDIO_EXTENSIONS,
        settings.SCHOOL_SCRAPER_DOCUMENT_EXTENSIONS,
    )
    return media_files


async def test_table_row_drive_file_links_are_discovered():
    html = """
    <html><body>
      <table>
        <tr>
          <th>Meeting Date</th><th>Agenda</th><th>Minutes</th>
        </tr>
        <tr>
          <td>July 29, 2025</td>
          <td><a href="https://drive.google.com/file/d/1gTqKX5FFy8Hw_jUMLY3YpbAEUDbu1afq/view?usp=drive_link">Agenda</a></td>
          <td><a href="https://drive.google.com/file/d/1LFGWlU8KW1pYpBK0D-fkvTMgtA_C9wYm/view?usp=drive_link">Minutes</a></td>
        </tr>
      </table>
    </body></html>
    """
    media_files = await _extract(html)

    assert len(media_files) == 2
    urls = {m["url"] for m in media_files}
    assert all("drive.google.com/uc?export=download&id=" in u for u in urls)
    names = {m["name"] for m in media_files}
    assert any("2025" in (n or "") for n in names)
    assert all(m["media_type"] == "document" for m in media_files)


async def test_google_doc_link_is_discovered_as_pdf_export():
    html = """
    <html><body>
      <p>September 8, 2025 —
        <a href="https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit">Minutes</a>
      </p>
    </body></html>
    """
    media_files = await _extract(html)

    assert len(media_files) == 1
    assert media_files[0]["file_extension"] == ".pdf"
    assert "docs.google.com/document/d/1AbCdEfGhIjKlMnOp/export?format=pdf" in media_files[0]["url"]


async def test_drive_file_link_with_no_year_context_is_dropped_by_default():
    """No date anywhere (row, filename, URL) => unknown year => filtered out,
    since SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR defaults to False."""
    html = """
    <html><body>
      <a href="https://drive.google.com/file/d/1NoDateAnywhereInThisRow999/view">Agenda</a>
    </body></html>
    """
    media_files = await _extract(html)
    assert media_files == []


async def test_drive_folder_link_inside_page_is_not_treated_as_a_file():
    """Folder links found inline (not as the scrape URL itself) are left for
    the dedicated folder-crawl path and must not be misclassified as files."""
    html = """
    <html><body>
      <a href="https://drive.google.com/drive/folders/1SomeFolderId">Older Minutes</a>
    </body></html>
    """
    media_files = await _extract(html)
    assert media_files == []

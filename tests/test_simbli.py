"""Tests for Simbli / eBoard Solutions URL hygiene, listing API, and expander."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.services.web_scraper import simbli_client as sb
from app.services.web_scraper.board_platforms import (
    board_platform_kind,
    is_board_platform_url,
    is_simbli_url,
    normalize_simbli_listing_url,
    simbli_site_id,
)

LISTING = "https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=36030338"
INDEX = "https://simbli.eboardsolutions.com/Index.aspx?S=36030338"
VIEW = "https://simbli.eboardsolutions.com/SB_Meetings/ViewMeeting.aspx?S=36030338&MID=57692"
ROOT = "https://simbli.eboardsolutions.com"

# Shell HTML embedding the tokens the GetMeetingListing API keys on. The
# ConnectionString value intentionally contains characters the regex must
# tolerate (spaces, semicolons).
SHELL_HTML = """
<script>
  var SecurityToken = "ABCdef123==";
  var constr = "Server=.;Database=EB;User=sa;Pwd=x";
  var schoolID = "36030338";
</script>
"""

# Minimal meeting rows the API returns for a single year. Dates use the
# /Date(epoch)/ .NET JSON form to exercise that parser path.
MEETINGS_2026 = [
    {
        "Master_MeetingID": "57692",
        "MM_MeetingTitle": "Board of Education Regular Meeting",
        "MM_DateTime": "/Date(1757371200000)/",  # 2025-09-08 in UTC
        "MinutesStatus": "PUBLISHED",
    },
    {
        "Master_MeetingID": "58100",
        "MM_MeetingTitle": "Special Meeting 2026",
        "MM_DateTime": "2026-03-11T18:00:00",
        "MinutesStatus": "UNPUBLISHED",
    },
]

# A ViewMeeting page with one Attachment.aspx link and one direct PDF. The
# Angular chrome is irrelevant to the DOM scan so we keep it minimal.
VIEW_HTML_AGENDA = """
<div>
  <a href="/Meetings/Attachment.aspx?AID=111&MID=57692&S=36030338">Agenda Packet</a>
  <a href="https://simbli.eboardsolutions.com/Files/extra.pdf">Supplemental PDF</a>
  <a href="/SB_Meetings/ViewMeeting.aspx?S=36030338&MID=57692">self link (not a file)</a>
</div>
"""
VIEW_HTML_EMPTY = "<div><p>No documents posted.</p></div>"


# ---------------------------------------------------------------- URL hygiene


def test_kind_detected():
    assert board_platform_kind(LISTING) == "simbli"


def test_is_board_platform_url_for_simbli():
    assert is_board_platform_url(LISTING)
    assert is_board_platform_url(INDEX)
    assert not is_board_platform_url("https://example.org/")


def test_is_simbli_url_helper():
    assert is_simbli_url(LISTING)
    assert not is_simbli_url("https://go.boarddocs.com/ma/x/Board.nsf/Public")


@pytest.mark.parametrize(
    "url,expected",
    [
        (INDEX, "https://simbli.eboardsolutions.com/sb_meetings/sb_meetinglisting.aspx?S=36030338"),
        (VIEW, "https://simbli.eboardsolutions.com/sb_meetings/sb_meetinglisting.aspx?S=36030338"),
        (LISTING, "https://simbli.eboardsolutions.com/sb_meetings/sb_meetinglisting.aspx?S=36030338"),
    ],
)
def test_normalize_to_listing(url, expected):
    assert normalize_simbli_listing_url(url) == expected


def test_normalize_passthrough_non_simbli():
    assert normalize_simbli_listing_url("https://example.org/x?S=1") == "https://example.org/x?S=1"
    assert normalize_simbli_listing_url(None) is None


def test_normalize_without_site_id_kept_as_is():
    # No S= -> cannot call the API; leave URL so caller can log/skip.
    assert normalize_simbli_listing_url("https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx") == "https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx"


def test_site_id_extraction():
    assert simbli_site_id(LISTING) == "36030338"
    assert simbli_site_id(VIEW) == "36030338"
    assert simbli_site_id("https://example.org/?S=1") is None


def test_build_view_meeting_url_agenda_and_minutes():
    assert sb.build_view_meeting_url(ROOT, "36030338", "57692") == VIEW
    assert sb.build_view_meeting_url(ROOT, "36030338", "57692", minutes=True) == VIEW + "&T=1"


# ------------------------------------------------------------------- parsing


def test_parse_listing_tokens_extracts_all_three():
    tokens = sb.parse_listing_tokens(SHELL_HTML, url=LISTING)
    assert tokens["security_token"] == "ABCdef123=="
    assert tokens["connection_string"] == "Server=.;Database=EB;User=sa;Pwd=x"
    assert tokens["school_id"] == "36030338"


def test_parse_listing_tokens_falls_back_to_url_for_site_id():
    html = '<script>var SecurityToken = "T";</script>'
    tokens = sb.parse_listing_tokens(html, url=LISTING)
    assert tokens["security_token"] == "T"
    assert tokens["school_id"] == "36030338"


def test_parse_listing_tokens_empty_html():
    tokens = sb.parse_listing_tokens("", url=LISTING)
    assert tokens["security_token"] is None
    assert tokens["school_id"] == "36030338"


def test_parse_listing_tokens_ignores_css_false_positive():
    """Regression: a bare ``S`` alternative in the school-id regex used to
    match arbitrary CSS like ``border-radius:5px`` (-> "s:5"), silently
    returning site id "5" instead of falling back to the URL's ``S=``
    param. Confirmed live against the real Murrieta Simbli shell HTML.
    """
    html = (
        "<style>.box{padding:1px 5px;border-radius:5px;float:left}</style>"
        '<script>var SecurityToken = "T";</script>'
    )
    tokens = sb.parse_listing_tokens(html, url=LISTING)
    assert tokens["school_id"] == "36030338"


def test_parse_listing_tokens_finds_full_schoolid_keyword():
    html = '<script>var schoolID = "36030338";</script>'
    tokens = sb.parse_listing_tokens(html, url="https://simbli.eboardsolutions.com/x?S=99")
    # Full "schoolID" keyword wins over the (different) URL fallback.
    assert tokens["school_id"] == "36030338"


def test_meeting_year_from_dotnet_date():
    assert sb._meeting_year(MEETINGS_2026[0]) == 2025


def test_meeting_year_from_iso():
    assert sb._meeting_year(MEETINGS_2026[1]) == 2026


def test_meeting_year_from_title_fallback():
    assert sb._meeting_year({"MM_MeetingTitle": "Notes from 2024 retreat"}) == 2024


def test_parse_year_handles_us_date_string():
    assert sb._parse_year("09/08/2025 6:00 PM") == 2025


def test_filter_exp_for_year():
    assert sb._filter_exp_for_year(2025) == "DateTime >= '01/01/2025' AND DateTime <= '12/31/2025'"


# ------------------------------------------------------- ViewMeeting harvest


class _FakePage:
    """Minimal async Playwright page double used by the expander tests."""

    def __init__(self, *, html: str, view_html_map: dict[str, str] | None = None):
        self._html = html
        self._view_html_map = view_html_map or {}
        self.goto_calls: list[str] = []

    @property
    def context(self):
        class _Ctx:
            async def cookies(self, url):
                return [{"name": "ASP.NET_SessionId", "value": "abc"}]

        return _Ctx()

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)

    async def wait_for_timeout(self, ms):
        return None

    async def content(self):
        return self._html

    async def evaluate(self, script):
        # Return the view-meeting link list built from the configured map.
        # Tests set _view_html_map keyed by URL; the evaluate returns links
        # parsed from the matching HTML so extract_view_meeting_attachments
        # can exercise its DOM-scan logic.
        return self._links_for_current()


def _links_from_html(html: str, base: str) -> list[dict]:
    """Parse <a href> pairs from HTML, absolutizing against ``base``."""
    import re
    from urllib.parse import urljoin

    links: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>', html, re.IGNORECASE):
        href, text = m.group(1), m.group(2)
        abs_href = urljoin(base, href)
        if abs_href in seen:
            continue
        seen.add(abs_href)
        links.append({"url": abs_href, "name": text.strip()})
    return links


class _ViewPage(_FakePage):
    """FakePage whose evaluate() returns links for the *last goto* URL."""

    def __init__(self, *, listing_html: str, view_html_map: dict[str, str]):
        super().__init__(html=listing_html, view_html_map=view_html_map)
        self._current_url = listing_html

    async def goto(self, url, **kwargs):
        self._current_url = url
        self.goto_calls.append(url)

    async def content(self):
        # content() is only called for the listing URL during token harvest.
        return self._html

    async def evaluate(self, script):
        html = self._view_html_map.get(self._current_url, "")
        return _links_from_html(html, self._current_url)


async def test_extract_view_meeting_attachments_finds_attachment_and_pdf():
    page = _ViewPage(listing_html=SHELL_HTML, view_html_map={VIEW: VIEW_HTML_AGENDA})
    docs = await sb.extract_view_meeting_attachments(
        page, view_url=VIEW, timeout_ms=1000, settle_ms=0
    )
    urls = [d["url"] for d in docs]
    assert any("Attachment.aspx" in u for u in urls)
    assert any(u.endswith("extra.pdf") for u in urls)
    # Self-link to ViewMeeting is not a file -> excluded.
    assert not any(u == VIEW for u in urls)


async def test_extract_view_meeting_attachments_empty_on_nav_failure():
    class _BoomPage(_ViewPage):
        async def goto(self, url, **kwargs):
            raise RuntimeError("net error")

    page = _BoomPage(listing_html=SHELL_HTML, view_html_map={})
    assert await sb.extract_view_meeting_attachments(
        page, view_url=VIEW, timeout_ms=100, settle_ms=0
    ) == []


# ------------------------------------------------------------------- expander


def _install_mock(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(sb.httpx, "AsyncClient", factory)


def _listing_api_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path == "/Services/api/GetMeetingListing":
        try:
            body = json.loads(request.content.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        # Honour the FilterExp year: return meetings for that year only.
        filter_exp = body.get("FilterExp") or ""
        if "2025" in filter_exp:
            return httpx.Response(200, text=json.dumps(MEETINGS_2026[:1]))
        if "2026" in filter_exp:
            return httpx.Response(200, text=json.dumps(MEETINGS_2026[1:]))
        return httpx.Response(200, text=json.dumps(MEETINGS_2026))
    return httpx.Response(404)


async def test_expander_returns_media_for_allowed_year(monkeypatch):
    # Restrict to the year that resolves to a single meeting (MID 57692) so
    # the fixture's view_html_map (keyed on that meeting's URLs) is hit.
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2025])
    _install_mock(monkeypatch, _listing_api_handler)

    page = _ViewPage(
        listing_html=SHELL_HTML,
        view_html_map={
            VIEW: VIEW_HTML_AGENDA,
            VIEW + "&T=1": VIEW_HTML_AGENDA,
        },
    )

    media = await sb.expand_simbli_meetings(
        page, page_url=LISTING, timeout_ms=1000, settle_ms=0, max_meetings=4
    )

    assert media, "expander returned no media"
    # Every item is a document with a source_page_url and a doc_year.
    for item in media:
        assert item["media_type"] == "document"
        assert item["source_page_url"].startswith(VIEW)
        assert item["doc_year"] in (2025, 2026)
    urls = [m["url"] for m in media]
    # Attachment.aspx link from the agenda page is captured.
    assert any("Attachment.aspx" in u for u in urls)
    # Minutes tab is visited (PUBLISHED) -> agenda page visited twice but
    # deduped by URL.
    assert any(u.endswith("extra.pdf") for u in urls)


async def test_expander_html_fallback_when_no_attachments(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2025])
    _install_mock(monkeypatch, _listing_api_handler)

    page = _ViewPage(
        listing_html=SHELL_HTML,
        view_html_map={VIEW: VIEW_HTML_EMPTY, VIEW + "&T=1": VIEW_HTML_EMPTY},
    )
    media = await sb.expand_simbli_meetings(
        page, page_url=LISTING, timeout_ms=1000, settle_ms=0, max_meetings=2
    )
    # Agenda + minutes ViewMeeting URLs become .html documents.
    assert len(media) == 2
    assert all(m["file_extension"] == ".html" for m in media)
    assert all(m["media_type"] == "document" for m in media)


async def test_expander_respects_meeting_cap(monkeypatch):
    # Single allowed year so the cap is exercised against one deterministic
    # meeting (MID 57692) rather than racing against which year is visited
    # first.
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2025])
    _install_mock(monkeypatch, _listing_api_handler)
    page = _ViewPage(
        listing_html=SHELL_HTML,
        view_html_map={VIEW: VIEW_HTML_AGENDA, VIEW + "&T=1": VIEW_HTML_AGENDA},
    )
    media = await sb.expand_simbli_meetings(
        page, page_url=LISTING, timeout_ms=1000, settle_ms=0, max_meetings=1
    )
    # One meeting -> agenda (+minutes, same URL set deduped) only.
    sources = {m["source_page_url"] for m in media}
    assert all(s == VIEW or s == VIEW + "&T=1" for s in sources)


async def test_expander_skips_non_simbli_url():
    assert await sb.expand_simbli_meetings(
        page=None, page_url="https://example.org/"
    ) == []


async def test_expander_soft_fails_on_imperva_challenge(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2025])

    challenge_html = (
        "<html><body>Incapsula incident ID: 123456"
        "<script>var incap_ses=1;</script></body></html>"
    )

    class _ChallengePage(_ViewPage):
        async def content(self):
            return challenge_html

    _install_mock(monkeypatch, _listing_api_handler)
    page = _ChallengePage(listing_html=challenge_html, view_html_map={})
    assert await sb.expand_simbli_meetings(
        page, page_url=LISTING, timeout_ms=1000, settle_ms=0
    ) == []


async def test_expander_soft_fails_without_site_id(monkeypatch):
    _install_mock(monkeypatch, lambda r: httpx.Response(404))
    # URL has no S= -> parse_listing_tokens can't recover a site id either.
    page = _ViewPage(listing_html="", view_html_map={})
    assert await sb.expand_simbli_meetings(
        page, page_url="https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx",
        timeout_ms=100,
    ) == []


# --------------------------------------------------------------- API soft-fail


async def test_post_meeting_listing_returns_empty_on_403_incapsula_block():
    """Regression: live-confirmed shape where Incapsula blocks the API POST
    itself with HTTP 403 + a small Incapsula-incident HTML body, distinct
    from the 200+challenge-page case. Must still soft-fail to [].
    """
    incapsula_403_body = (
        '<html><body><iframe src="/_Incapsula_Resource?...">'
        "Request unsuccessful. Incapsula incident ID: 937000370083026624-1"
        "</iframe></body></html>"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(403, text=incapsula_403_body)
        )
    ) as client:
        rows = await sb._post_meeting_listing(client, ROOT, {"SchoolID": "1"})
    assert rows == []


async def test_post_meeting_listing_returns_empty_on_challenge():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="<html>Incapsula incident</html>")
        )
    ) as client:
        rows = await sb._post_meeting_listing(
            client, ROOT, {"SchoolID": "1"}
        )
    assert rows == []


async def test_post_meeting_listing_returns_empty_on_non_json():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))
    ) as client:
        rows = await sb._post_meeting_listing(client, ROOT, {})
    assert rows == []


async def test_post_meeting_listing_parses_bare_list():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=json.dumps(MEETINGS_2026))
        )
    ) as client:
        rows = await sb._post_meeting_listing(client, ROOT, {})
    assert len(rows) == 2


# ----------------------------------------------------------------- download


async def test_fetch_simbli_document_delegates_to_session(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_session(source, media, *, timeout_ms=None):
        captured["source"] = source
        captured["media"] = media
        return b"session-bytes"

    monkeypatch.setattr(sb, "fetch_document_via_playwright_session", fake_session)
    raw = await sb.fetch_simbli_document(VIEW, VIEW + "/Attachment.aspx?AID=1&MID=2&S=3")
    assert raw == b"session-bytes"
    assert captured["source"] == VIEW


# ----------------------------------------------------------------- live smoke


@pytest.mark.live
async def test_live_murrieta_returns_documents():
    """Live probe against the Murrieta Valley USD Simbli portal.

    Requires network + a non-challenged IP + a real Playwright browser (the
    expander needs a live page to warm the Imperva/ASP.NET session, unlike
    the HTTP-only BoardDocs expander). Run with ``-m live``.

    NOTE: headless Chromium from a datacenter/CI IP is frequently served the
    Imperva challenge page rather than the real portal (the same class of
    anti-bot issue BoardDocs has from headless detection). The expander's
    soft-fail path (log + return ``[]``) is the correct behaviour in that
    case; this assertion only passes from an IP/browser combination Imperva
    doesn't challenge. Treat a failure here as inconclusive, not a regression,
    unless the warning log shows something other than an Imperva challenge.
    """
    from playwright.async_api import async_playwright

    from app.services.web_scraper.simbli_client import expand_simbli_meetings as _expand

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(user_agent=settings.SCHOOL_SCRAPER_USER_AGENT)
            try:
                media = await _expand(
                    page, page_url=LISTING, max_meetings=3, timeout_ms=30_000
                )
            finally:
                await page.close()
        finally:
            await browser.close()

    assert media, "expected documents from Murrieta Simbli portal"

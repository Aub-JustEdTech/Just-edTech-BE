"""Tests for BoardDocs URL hygiene, NSF expander, and download helper."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services.web_scraper import boarddocs_client as bd
from app.services.web_scraper.board_platforms import (
    board_platform_kind,
    boarddocs_portal_base,
    is_boarddocs_private_url,
    normalize_boarddocs_url,
)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(bd, "_REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(bd, "_BACKOFF_SECONDS", 0)


PUBLIC = "https://go.boarddocs.com/ma/nrsd/Board.nsf/Public"
BASE = "https://go.boarddocs.com/ma/nrsd/Board.nsf"

SHELL_HTML = """
<select id="committees">
  <option value="LIB111111111111">Policy Library</option>
  <option value="BRD222222222222">School Committee</option>
</select>
"""

MEETINGS = [
    {"unique": "M1", "name": "Regular", "numberdate": "2026-08-13T19:00:00"},
    {"unique": "M2", "name": "Old", "numberdate": "2019-01-10T19:00:00"},
]

AGENDA_HTML = """
<div>
  <a class="public-file" href="/ma/nrsd/Board.nsf/files/ABC/$file/agenda.pdf">Agenda</a>
  <a href="/ma/nrsd/Board.nsf/files/ABC/$file/agenda.pdf">dup</a>
  <a href="/other/page">not a file</a>
</div>
"""


# ---------------------------------------------------------------- URL hygiene


def test_kind_detected():
    assert board_platform_kind(PUBLIC) == "boarddocs"


@pytest.mark.parametrize(
    "url,expected",
    [
        (PUBLIC + "#tab-meetings", PUBLIC),
        (BASE + "/vpublic?open", PUBLIC),
        (PUBLIC, PUBLIC),
    ],
)
def test_normalize(url, expected):
    assert normalize_boarddocs_url(url) == expected


def test_normalize_passthrough_non_boarddocs():
    assert normalize_boarddocs_url("https://example.org/x#y") == "https://example.org/x#y"
    assert normalize_boarddocs_url(None) is None


def test_private_detection():
    assert is_boarddocs_private_url(BASE + "/Private")
    assert not is_boarddocs_private_url(PUBLIC)
    assert not is_boarddocs_private_url("https://example.org/Private")


def test_portal_base():
    assert boarddocs_portal_base(PUBLIC) == BASE
    assert boarddocs_portal_base("https://go.boarddocs.com/ma/nrsd/") is None
    assert boarddocs_portal_base("https://example.org/Board.nsf/Public") is None


# -------------------------------------------------------------------- parsing


def test_committee_priority_order():
    committees = bd.discover_boarddocs_committees(SHELL_HTML)
    assert committees[0] == ("BRD222222222222", "School Committee")


def test_committee_bare_ids_fallback():
    html = '<div committeeid="ABCDEF0123456789"></div>'
    assert bd.discover_boarddocs_committees(html) == [("ABCDEF0123456789", "")]


def test_meetings_list_parsing_handles_string_and_wrapped():
    assert len(bd.parse_boarddocs_meetings_list(json.dumps(MEETINGS))) == 2
    assert len(bd.parse_boarddocs_meetings_list({"meetings": MEETINGS})) == 2
    assert bd.parse_boarddocs_meetings_list("not json") == []


def test_meetings_list_parses_real_compact_numberdate():
    rows = bd.parse_boarddocs_meetings_list(
        [{"unique": "M1", "name": "x", "numberdate": "20260813"}]
    )
    assert rows[0]["meeting_date"].year == 2026
    assert bd._meeting_year(rows[0]) == 2026


def test_agenda_files_absolutized_and_deduped():
    files = bd.parse_boarddocs_agenda_files(AGENDA_HTML, base_url=BASE)
    assert [f["url"] for f in files] == [
        "https://go.boarddocs.com/ma/nrsd/Board.nsf/files/ABC/$file/agenda.pdf"
    ]


# ------------------------------------------------------------------- expander


def _install_mock(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(bd.httpx, "AsyncClient", factory)


def _nsf_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "GET" and path.endswith("/Public"):
        return httpx.Response(200, text=SHELL_HTML)
    if path.endswith("BD-GetMeetingsList"):
        assert b"BRD222222222222" in request.content
        return httpx.Response(200, text=json.dumps(MEETINGS))
    if path.endswith("PRINT-AgendaDetailed"):
        return httpx.Response(200, text=AGENDA_HTML)
    return httpx.Response(404)


async def test_expander_year_gates_and_returns_media(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2026])
    _install_mock(monkeypatch, _nsf_handler)

    media = await bd.expand_boarddocs_meetings(page_url=PUBLIC + "#tab-meetings")

    assert len(media) == 1
    item = media[0]
    assert item["media_type"] == "document"
    assert item["doc_year"] == 2026
    assert item["source_page_url"] == PUBLIC
    assert item["url"].endswith("/$file/agenda.pdf")


async def test_expander_skips_private_without_http(monkeypatch):
    def boom(request):
        raise AssertionError("no HTTP expected for /Private")

    _install_mock(monkeypatch, boom)
    assert await bd.expand_boarddocs_meetings(page_url=BASE + "/Private") == []


async def test_expander_soft_fails_on_shell_error(monkeypatch):
    _install_mock(monkeypatch, lambda r: httpx.Response(500))
    assert await bd.expand_boarddocs_meetings(page_url=PUBLIC) == []


async def test_expander_respects_meeting_cap(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2026])
    many = [
        {"unique": f"M{i}", "name": "m", "numberdate": "2026-01-01T00:00:00"}
        for i in range(5)
    ]
    calls = {"agenda": 0}

    def handler(request):
        if request.url.path.endswith("BD-GetMeetingsList"):
            return httpx.Response(200, text=json.dumps(many))
        if request.url.path.endswith("PRINT-AgendaDetailed"):
            calls["agenda"] += 1
            return httpx.Response(200, text=AGENDA_HTML)
        return httpx.Response(200, text=SHELL_HTML)

    _install_mock(monkeypatch, handler)
    await bd.expand_boarddocs_meetings(page_url=PUBLIC, max_meetings=2)
    assert calls["agenda"] == 2


# ------------------------------------------------- agenda / minutes only scope

LONG = "Call to order. " * 30  # > minimum visible-text threshold

AGENDA_WITH_MIXED_FILES = (
    f"<div>{LONG}</div>"
    '<a href="/ma/nrsd/Board.nsf/files/A1/$file/Warrant%20Report.pdf">Warrant Report</a>'
    '<a href="/ma/nrsd/Board.nsf/files/A2/$file/Contract.pdf">Vendor Contract</a>'
    '<a href="/ma/nrsd/Board.nsf/files/A3/$file/Approved%20Minutes%20June.pdf">Approved Minutes June</a>'
    '<a href="/ma/nrsd/Board.nsf/files/A4/$file/Financials.pdf">Financials</a>'
)


def _scoped_handler(minutes_html: str):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/Public"):
            return httpx.Response(200, text=SHELL_HTML)
        if path.endswith("BD-GetMeetingsList"):
            return httpx.Response(200, text=json.dumps(MEETINGS[:1]))
        if path.endswith("PRINT-AgendaDetailed"):
            return httpx.Response(200, text=AGENDA_WITH_MIXED_FILES)
        if path.endswith("BD-GetMinutes"):
            return httpx.Response(200, text=minutes_html)
        return httpx.Response(404)

    return handler


async def test_only_agenda_minutes_and_labelled_attachments(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2026])
    _install_mock(monkeypatch, _scoped_handler(f"<p>{LONG}</p>"))

    media = await bd.expand_boarddocs_meetings(page_url=PUBLIC)
    names = sorted(m["name"] for m in media)

    assert any(n.endswith("Agenda 2026-08-13.txt") for n in names)
    assert any(n.endswith("Minutes 2026-08-13.txt") for n in names)
    assert any("Approved Minutes June" in n for n in names)
    # Out of scope: everything else attached to the meeting.
    assert not any("Warrant" in n or "Contract" in n or "Financials" in n for n in names)
    assert len(media) == 3
    assert all(m["doc_year"] == 2026 for m in media)


async def test_minutes_skipped_when_not_published(monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_SCRAPER_ALLOWED_YEARS", [2026])
    _install_mock(monkeypatch, _scoped_handler("<p>  </p>"))

    media = await bd.expand_boarddocs_meetings(page_url=PUBLIC)
    assert not any("Minutes 2026" in m["name"] for m in media)


def test_agent_url_roundtrip():
    url = bd.build_agent_url(
        BASE, bd.MINUTES_AGENT, meeting_id="M1", committee_id="C1", meeting_date="2026-08-13"
    )
    parsed = bd.parse_agent_url(url)
    assert parsed["kind"] == "minutes"
    assert parsed["meeting_id"] == "M1"
    assert parsed["committee_id"] == "C1"
    assert parsed["meeting_date"] == "2026-08-13"
    assert parsed["base_url"] == BASE
    assert bd.parse_agent_url(BASE + "/files/A/$file/a.pdf") is None


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Approved Minutes June", True),
        ("Board_Agenda_Final.pdf", True),
        ("School%20Committee%20Minutes%2020Nov2025.pdf", True),
        ("Warrant Report", False),
        ("Vendor Contract", False),
        (None, False),
    ],
)
def test_agenda_minutes_label(label, expected):
    assert bd.is_agenda_or_minutes_label(label) is expected


async def test_download_renders_agent_url_to_text(monkeypatch):
    _install_mock(monkeypatch, lambda r: httpx.Response(200, text=f"<p>{LONG}</p>"))
    url = bd.build_agent_url(
        BASE, bd.AGENDA_AGENT, meeting_id="M1", committee_id="C1", meeting_date="2026-08-13"
    )
    raw = await bd.fetch_boarddocs_document(PUBLIC, url)
    text = raw.decode()
    assert text.startswith("Agenda - meeting date 2026-08-13")
    assert "Call to order." in text


async def test_download_agent_url_with_no_text_raises(monkeypatch):
    _install_mock(monkeypatch, lambda r: httpx.Response(200, text="<p></p>"))
    url = bd.build_agent_url(BASE, bd.MINUTES_AGENT, meeting_id="M1", committee_id="C1")
    with pytest.raises(RuntimeError):
        await bd.fetch_boarddocs_document(PUBLIC, url)


async def test_throttled_request_is_retried_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, text=f"<p>{LONG}</p>")

    _install_mock(monkeypatch, handler)
    url = bd.build_agent_url(BASE, bd.AGENDA_AGENT, meeting_id="M1", committee_id="C1")
    raw = await bd.fetch_boarddocs_document(PUBLIC, url)
    assert calls["n"] == 2 and b"Call to order." in raw


async def test_block_is_logged_as_warning_not_silent(monkeypatch, caplog):
    import logging

    _install_mock(monkeypatch, lambda r: httpx.Response(403))
    url = bd.build_agent_url(BASE, bd.MINUTES_AGENT, meeting_id="M1", committee_id="C1")
    with caplog.at_level(logging.WARNING, logger=bd.logger.name):
        with pytest.raises(RuntimeError):
            await bd.fetch_boarddocs_document(PUBLIC, url)
    assert any("HTTP 403" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------- download


async def test_download_prefers_direct_http(monkeypatch):
    _install_mock(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
        ),
    )

    async def no_fallback(*a, **k):
        raise AssertionError("Playwright fallback should not be used")

    monkeypatch.setattr(bd, "fetch_document_via_playwright_session", no_fallback)
    raw = await bd.fetch_boarddocs_document(PUBLIC, BASE + "/files/A/$file/a.pdf")
    assert raw == b"%PDF-1.4"


async def test_download_falls_back_on_html(monkeypatch):
    _install_mock(
        monkeypatch,
        lambda r: httpx.Response(
            200, text="<html>login</html>", headers={"content-type": "text/html"}
        ),
    )

    async def fallback(source, media, **k):
        return b"session-bytes"

    monkeypatch.setattr(bd, "fetch_document_via_playwright_session", fallback)
    raw = await bd.fetch_boarddocs_document(PUBLIC, BASE + "/files/A/$file/a.pdf")
    assert raw == b"session-bytes"


# ----------------------------------------------------------------- live smoke


@pytest.mark.live
async def test_live_nrsd_public_returns_documents():
    media = await bd.expand_boarddocs_meetings(page_url=PUBLIC, max_meetings=2)
    assert media, "expected documents from NRSD public portal"


@pytest.mark.live
async def test_live_private_is_skipped():
    url = "https://go.boarddocs.com/ma/ens/Board.nsf/Private"
    assert await bd.expand_boarddocs_meetings(page_url=url) == []

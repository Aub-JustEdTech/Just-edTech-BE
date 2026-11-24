"""Tests for shared WeasyPrint report branding."""

from io import BytesIO

from app.services.conversation_report_service import ConversationReportService
from app.services.report_branding import (
    BRAND_FOOTER,
    BRAND_NAME,
    LOGO_PATH,
    branded_header_html,
    branded_report_css,
    logo_data_uri,
    wrap_branded_document,
)


def test_logo_asset_exists_and_loads():
    assert LOGO_PATH.is_file()
    uri = logo_data_uri()
    assert uri is not None
    assert uri.startswith("data:image/jpeg;base64,")


def test_branded_header_contains_logo_and_copy():
    html = branded_header_html()
    assert BRAND_NAME in html
    assert "Education Justice Academy" in html
    assert "logo-frame" in html
    assert "accent-rule" in html
    assert "position: running(continuation-accent)" in html


def test_branded_css_matches_spec():
    css = branded_report_css()
    assert "size: A4" in css
    assert "#111614" in css
    assert "#0B3C8A" in css
    assert "#374151" in css
    assert "#7A64EB" in css
    assert "#52C4AA" in css
    assert "#EE5064" in css
    assert "#F5CD50" in css
    assert BRAND_FOOTER in css
    assert 'content: "Page " counter(page) " of " counter(pages)' in css


def test_conversation_report_pdf_is_branded():
    service = ConversationReportService()
    # Minimal Conversation-like stand-in; render_pdf_weasyprint only uses title/text.
    buf = service.render_pdf_weasyprint(
        report_text="EXECUTIVE SUMMARY\n\n- Point one\n\nKEY TOPICS\n\nDetails here.",
        conversation=None,  # type: ignore[arg-type]
        citations=[],
        report_title="Sample Conversation Report",
    )
    assert isinstance(buf, BytesIO)
    data = buf.read()
    assert data.startswith(b"%PDF")
    assert len(data) > 1000


def test_wrap_branded_document_structure():
    html = wrap_branded_document("<h1>Title</h1><p>Body</p>")
    assert html.index("brand-header") < html.index("report-content")
    assert "<h1>Title</h1>" in html

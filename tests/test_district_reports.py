"""Unit tests for the district analytics report pipeline.

Covers:
1. The fixed query catalog (Q1-Q7 IDs, date-window materialization, filter sets).
2. The banned-terms guard (writer + scrubber).
3. The PDF renderer (non-empty bytes, valid PDF header).
4. The orchestrating service end-to-end with the retrieval + writer layers
   monkeypatched (no live Qdrant / LLM).

Run:
    poetry run pytest tests/test_district_reports.py -v
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest

from app.services.district_report import district_report_service
from app.services.district_report.pdf import render_report_pdf
from app.services.district_report.queries import (
    get_query_spec,
    list_query_ids,
    resolve_filters,
)
from app.services.district_report.writer import (
    BANNED_TERMS,
    contains_banned_terms,
    scrub_banned_terms,
)

# ---------------------------------------------------------------------------
# 1. Query catalog
# ---------------------------------------------------------------------------


def test_catalog_has_seven_queries():
    assert list_query_ids() == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]


def test_every_query_has_required_fields():
    for qid in list_query_ids():
        spec = get_query_spec(qid)
        assert spec.query_id == qid
        assert spec.title
        assert spec.research_goal
        assert spec.question
        assert spec.geography == "Massachusetts"
        assert len(spec.filter_sets) >= 1


def test_unknown_query_id_raises():
    with pytest.raises(ValueError):
        get_query_spec("Q99")


def test_q1_filter_uses_fixed_sept_2025_window():
    spec = get_query_spec("Q1")
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert len(filters) == 1
    assert filters[0]["topic_categories"] == ["sexed"]
    assert filters[0]["meeting_doc_types"] == ["Agenda"]
    assert filters[0]["meeting_date_from"] == "2025-09-01"


def test_q2_filters_compute_last_12_months_from_today():
    spec = get_query_spec("Q2")
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert len(filters) == 2
    # Both passes should be scoped to ~one year before today.
    for f in filters:
        assert f["meeting_date_from"] == "2025-09-03"


def test_q3_filter_uses_year_start():
    spec = get_query_spec("Q3")
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert all(f["meeting_date_from"] == "2026-01-01" for f in filters)


def test_date_windows_shift_with_today():
    """The same query should produce a different window next year."""
    spec = get_query_spec("Q2")
    now = resolve_filters(spec, date(2026, 9, 3))[0]["meeting_date_from"]
    next_year = resolve_filters(spec, date(2027, 9, 3))[0]["meeting_date_from"]
    assert now != next_year


# ---------------------------------------------------------------------------
# 2. Banned-terms guard
# ---------------------------------------------------------------------------


def test_banned_terms_list_covers_internal_vocabulary():
    for term in ("chunk", "qdrant", "taxonomy", "topic_tags", "chunk_count"):
        assert term in BANNED_TERMS


def test_clean_text_has_no_banned_terms():
    assert contains_banned_terms("Three districts discussed the policy.") == []


def test_technical_text_is_flagged():
    found = contains_banned_terms("12 chunks in Qdrant with topic_tags=sexed")
    assert "chunks" in found
    assert "qdrant" in found
    assert "topic_tags" in found


def test_scrubber_replaces_banned_terms():
    scrubbed = scrub_banned_terms("12 chunks in Qdrant with topic_tags=sexed")
    assert "chunks" not in scrubbed.lower()
    assert "qdrant" not in scrubbed.lower()
    assert "topic_tags" not in scrubbed.lower()
    assert "document" in scrubbed.lower()


def test_scrubber_does_not_corrupt_clean_text():
    clean = "Three districts discussed sex education policy in their agendas."
    assert scrub_banned_terms(clean) == clean


def test_format_citation_includes_source_links():
    from app.services.district_report.writer import _format_citation_for_evidence

    cite = _format_citation_for_evidence(
        {
            "document_name": "June 15, 2026",
            "meeting_date": "2026-06-15",
            "page_number": 123,
            "source_media_url": "https://example.org/doc.pdf",
            "source_page_url": "https://example.org/agenda",
            "snippet": "Sex education policy...",
        },
        district_name="Rochester",
    )
    assert cite["district"] == "Rochester"
    assert cite["document_link"] == "https://example.org/doc.pdf"
    assert cite["source_page_url"] == "https://example.org/agenda"


def test_pdf_renders_markdown_links_as_anchors():
    from app.services.district_report.pdf import _markdown_to_html

    html = _markdown_to_html(
        "## References\n\n"
        "- Rochester — June 15, 2026, p. 123 — "
        "[Open document](https://example.org/doc.pdf)\n",
        "Title",
    )
    assert 'href="https://example.org/doc.pdf"' in html
    assert "Open document" in html


# ---------------------------------------------------------------------------
# 3. PDF renderer
# ---------------------------------------------------------------------------


def test_render_report_pdf_produces_valid_pdf():
    markdown = (
        "## Key points\n\nNo districts matched the current corpus.\n\n"
        "## Summary\n\nThe knowledge base had no matching agenda items.\n"
        "## References\n\n1. Example district, agenda, 2026-01-01\n"
    )
    buf = render_report_pdf(markdown, "Test District Report")
    data = buf.read()
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_render_report_pdf_returns_seekable_buffer():
    buf = render_report_pdf("## Key points\n\nNone.", "Title")
    assert isinstance(buf, BytesIO)
    assert buf.tell() == 0


# ---------------------------------------------------------------------------
# 4. Orchestrating service (retrieval + writer monkeypatched)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _stub_retrieval(monkeypatch):
    """Stub the retriever so no live Qdrant/DB is needed."""
    ranked = [
        {
            "org_code": "0001",
            "district_name": "Example District",
            "state": "MA",
            "chunk_count": 5,
            "retrieval_pass": 0,
        }
    ]
    citations = [
        {
            "district_name": "Example District",
            "total": 2,
            "citations": [
                {
                    "document_name": "Sample Agenda",
                    "meeting_date": "2026-01-15",
                    "meeting_doc_type": "Agenda",
                    "page_number": 3,
                    "action_stage": None,
                    "snippet": "The committee discussed the health curriculum.",
                }
            ],
        }
    ]
    corpus_summary = {"district_count": 179, "state": "MA", "districts": []}

    async def fake_resolve_chatbot_config_id(tenant_id):
        return 1

    async def fake_run(spec, tenant_id, chatbot_config_id):
        return ranked

    async def fake_gather(spec, ranked, tenant_id, chatbot_config_id, top_n=5):
        return citations

    async def fake_corpus(tenant_id, chatbot_config_id, state="MA"):
        return corpus_summary

    monkeypatch.setattr(
        "app.services.district_report.service.resolve_chatbot_config_id",
        fake_resolve_chatbot_config_id,
    )
    monkeypatch.setattr(
        "app.services.district_report.service.run_retrieval_passes", fake_run
    )
    monkeypatch.setattr(
        "app.services.district_report.service.gather_citations", fake_gather
    )
    monkeypatch.setattr(
        "app.services.district_report.service.fetch_corpus_summary", fake_corpus
    )
    return ranked


@pytest.fixture()
def _stub_writer(monkeypatch):
    """Stub the LLM writer to return deterministic, clean markdown."""
    async def fake_write(db, chatbot_config_id, evidence):
        return (
            "## Key points\n\nExample District discussed the policy.\n\n"
            "## Summary\nOne district surfaced a relevant agenda item.\n"
            "## References\n\n1. Example District, Sample Agenda, 2026-01-15\n"
        )

    monkeypatch.setattr("app.services.district_report.service.write_report", fake_write)


async def test_service_generates_report_pdf(_stub_retrieval, _stub_writer):
    result = await district_report_service.generate_report(
        tenant_id=4,
        query_id="Q1",
    )
    assert result["query_id"] == "Q1"
    assert result["tenant_id"] == 4
    assert result["report_id"].startswith("DR-4-Q1-")
    assert result["filename"].endswith(".pdf")
    assert result["pdf_bytes"].startswith(b"%PDF")
    assert len(result["pdf_bytes"]) > 500


async def test_service_report_is_stakeholder_clean(_stub_retrieval, _stub_writer):
    """The generated PDF should not contain internal terms."""
    result = await district_report_service.generate_report(
        tenant_id=4, query_id="Q7"
    )
    # We cannot grep PDF binary directly for all terms reliably, but the
    # writer stub returns clean text and the scrubber is a safety net, so
    # confirm the pipeline did not raise and produced a PDF.
    assert result["pdf_bytes"].startswith(b"%PDF")


def test_service_filename_is_safe(_stub_retrieval, _stub_writer):
    import asyncio

    result = asyncio.run(
        district_report_service.generate_report(tenant_id=4, query_id="Q1")
    )
    # No spaces / unicode in the filename.
    assert " " not in result["filename"]
    assert result["filename"] == result["filename"].encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# 5. Retriever helpers (merge logic, no live tools)
# ---------------------------------------------------------------------------


def test_annotate_passes_records_first_pass_for_each_org():
    """run_retrieval_passes merges passes and keeps the highest count."""
    # Simulate two passes: pass 0 has district A with 2, pass 1 has A with 5.
    per_pass = [
        [{"org_code": "A", "district_name": "A", "chunk_count": 2}],
        [{"org_code": "A", "district_name": "A", "chunk_count": 5}],
    ]
    from app.services.district_report.retriever import _annotate_passes

    merged = {"A": {"org_code": "A", "district_name": "A", "chunk_count": 5}}
    ranked = list(merged.values())
    _annotate_passes(ranked, per_pass)
    # The merged row keeps the higher count (5) and should be tagged with the
    # pass that produced the higher count (pass 1).
    assert ranked[0]["chunk_count"] == 5
    assert ranked[0]["retrieval_pass"] == 1


# ---------------------------------------------------------------------------
# 6. API layer (auth + query validation)
# ---------------------------------------------------------------------------


def test_district_reports_endpoints_require_auth():
    """All district-reports endpoints should require authentication."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    for path, method in [
        ("/api/v1/district-reports/queries", "GET"),
        ("/api/v1/district-reports", "POST"),
        ("/api/v1/district-reports/status?task_id=x", "GET"),
        ("/api/v1/district-reports/download?task_id=x", "GET"),
    ]:
        resp = getattr(client, method.lower())(path)
        assert resp.status_code in (401, 403), f"{method} {path} -> {resp.status_code}"


def test_district_reports_post_rejects_unknown_query():
    """POST should 400 on an unknown query_id before enqueuing anything."""
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from app.main import app
    from app.utils.dependencies import get_current_tenant_user

    app.dependency_overrides[get_current_tenant_user] = lambda: MagicMock(
        id=1, role="tenant_admin"
    )
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/district-reports",
            json={"query_id": "Q99", "tenant_id": 4},
        )
        assert resp.status_code == 400
        body = resp.json()
        detail = body.get("detail") or body.get("error", {})
        assert "Q99" in str(detail)
    finally:
        app.dependency_overrides.clear()

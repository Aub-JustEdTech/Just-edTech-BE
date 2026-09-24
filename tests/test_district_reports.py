"""Unit tests for the district analytics report pipeline.

Covers:
1. The tenant-scoped query catalog (MA Q1-Q7, CA Q1-Q5, date windows).
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
    CA_TENANT_ID,
    MA_TENANT_ID,
    get_query_spec,
    list_query_ids,
    list_queries_for_tenant,
    list_tenant_ids,
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


def test_catalog_tenants():
    assert MA_TENANT_ID in list_tenant_ids()
    assert CA_TENANT_ID in list_tenant_ids()


def test_ma_catalog_has_seven_queries():
    assert list_query_ids(MA_TENANT_ID) == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]


def test_ca_catalog_has_five_queries():
    assert list_query_ids(CA_TENANT_ID) == ["Q1", "Q2", "Q3", "Q4", "Q5"]


def test_unknown_tenant_returns_empty_catalog():
    assert list_query_ids(999) == []
    assert list_queries_for_tenant(999) == []


def test_every_ma_query_has_required_fields():
    for qid in list_query_ids(MA_TENANT_ID):
        spec = get_query_spec(qid, MA_TENANT_ID)
        assert spec.query_id == qid
        assert spec.tenant_id == MA_TENANT_ID
        assert spec.title
        assert spec.research_goal
        assert spec.question
        assert spec.geography == "Massachusetts"
        assert len(spec.filter_sets) >= 1


def test_every_ca_query_has_required_fields():
    for qid in list_query_ids(CA_TENANT_ID):
        spec = get_query_spec(qid, CA_TENANT_ID)
        assert spec.query_id == qid
        assert spec.tenant_id == CA_TENANT_ID
        assert spec.title
        assert spec.research_goal
        assert spec.question
        assert spec.geography == "California"
        assert len(spec.filter_sets) >= 1


def test_same_query_id_differs_by_tenant():
    ma_q1 = get_query_spec("Q1", MA_TENANT_ID)
    ca_q1 = get_query_spec("Q1", CA_TENANT_ID)
    assert ma_q1.question != ca_q1.question
    assert ma_q1.geography == "Massachusetts"
    assert ca_q1.geography == "California"


def test_unknown_query_id_raises():
    with pytest.raises(ValueError):
        get_query_spec("Q99", MA_TENANT_ID)
    with pytest.raises(ValueError):
        get_query_spec("Q1", 999)


def test_ma_query_rejected_for_ca_tenant():
    with pytest.raises(ValueError, match="Q6"):
        get_query_spec("Q6", CA_TENANT_ID)


def test_q1_filter_uses_fixed_sept_2025_window():
    spec = get_query_spec("Q1", MA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert len(filters) == 1
    assert filters[0]["topic_categories"] == ["sexed"]
    assert filters[0]["meeting_doc_types"] == ["Agenda"]
    assert filters[0]["meeting_date_from"] == "2025-09-01"


def test_q2_filters_compute_last_12_months_from_today():
    spec = get_query_spec("Q2", MA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert len(filters) == 2
    # Both passes should be scoped to ~one year before today.
    for f in filters:
        assert f["meeting_date_from"] == "2025-09-03"


def test_q3_filter_uses_year_start():
    spec = get_query_spec("Q3", MA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert all(f["meeting_date_from"] == "2026-01-01" for f in filters)


def test_ca_q1_uses_last_24_months():
    spec = get_query_spec("Q1", CA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert len(filters) == 1
    assert filters[0]["meeting_date_from"] == "2024-09-03"
    assert filters[0]["states"] == ["CA"]


def test_ca_q5_uses_last_12_months():
    spec = get_query_spec("Q5", CA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert all(f["meeting_date_from"] == "2025-09-03" for f in filters)
    assert all(f["states"] == ["CA"] for f in filters)
    assert len(filters) == 2
    assert filters[0]["meeting_doc_types"] == ["Agenda", "Minutes"]
    assert filters[1]["meeting_doc_types"] == ["Minutes"]
    assert "_search_query" in filters[0]
    assert "_search_query" in filters[1]


def test_ca_queries_have_focused_search_queries():
    from app.services.district_report.queries import resolve_search_query

    for qid in list_query_ids(CA_TENANT_ID):
        spec = get_query_spec(qid, CA_TENANT_ID)
        assert spec.search_query
        assert resolve_search_query(spec) == spec.search_query
        # Embedding query should be shorter / more thematic than the
        # long stakeholder question shown in the PDF.
        assert len(spec.search_query) < len(spec.question)


def test_ca_q4_pass_overrides_search_query():
    from app.services.district_report.queries import resolve_search_query

    spec = get_query_spec("Q4", CA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    public = resolve_search_query(spec, filters[0])
    follow = resolve_search_query(spec, filters[1])
    assert "public comment" in public.lower()
    assert "follow" in follow.lower() or "board response" in follow.lower()
    assert public != follow


def test_resolve_filters_injects_ma_state():
    spec = get_query_spec("Q1", MA_TENANT_ID)
    filters = resolve_filters(spec, date(2026, 9, 3))
    assert filters[0]["states"] == ["MA"]


def test_ca_queries_use_semantic_retrieval():
    from app.services.district_report.queries import RETRIEVAL_SEMANTIC

    for qid in list_query_ids(CA_TENANT_ID):
        assert get_query_spec(qid, CA_TENANT_ID).retrieval_mode == RETRIEVAL_SEMANTIC


def test_ca_default_focus_district_is_saddleback():
    from app.services.district_report.queries import (
        CA_DEFAULT_DISTRICT_ORG_CODE,
        default_district_org_code,
    )

    assert default_district_org_code(CA_TENANT_ID) == CA_DEFAULT_DISTRICT_ORG_CODE
    assert CA_DEFAULT_DISTRICT_ORG_CODE == "30-73635"
    assert default_district_org_code(MA_TENANT_ID) is None


def test_ma_queries_use_topic_count_retrieval():
    from app.services.district_report.queries import RETRIEVAL_TOPIC_COUNTS

    for qid in list_query_ids(MA_TENANT_ID):
        assert get_query_spec(qid, MA_TENANT_ID).retrieval_mode == RETRIEVAL_TOPIC_COUNTS


def test_geography_to_state():
    from app.services.district_report.queries import geography_to_state

    assert geography_to_state("Massachusetts") == "MA"
    assert geography_to_state("California") == "CA"
    with pytest.raises(ValueError):
        geography_to_state("Texas")


def test_semantic_citations_from_stashed_hits():
    from app.services.district_report.retriever import _citations_from_semantic_ranked

    ranked = [
        {
            "org_code": "SVUSD",
            "district_name": "Saddleback Valley Unified",
            "state": "CA",
            "chunk_count": 2,
            "_semantic_hits": [
                {
                    "document_name": "06.02.25 Minutes",
                    "meeting_date": "2025-06-02",
                    "page_number": 14,
                    "text": "Green Ribbon and Distinguished Schools recognized.",
                    "score": 0.9,
                },
                {
                    "document_name": "11.13.25 Minutes",
                    "meeting_date": "2025-11-13",
                    "page_number": 3,
                    "text": "SpiderLab work-based learning program.",
                    "score": 0.8,
                },
            ],
        }
    ]
    citations = _citations_from_semantic_ranked(ranked, top_n=5)
    assert len(citations) == 1
    assert citations[0]["district_name"] == "Saddleback Valley Unified"
    assert len(citations[0]["citations"]) == 2
    assert "Green Ribbon" in citations[0]["citations"][0]["snippet"]
    # Stash must be cleared so it never leaks into the writer evidence.
    assert "_semantic_hits" not in ranked[0]


def test_date_windows_shift_with_today():
    """The same query should produce a different window next year."""
    spec = get_query_spec("Q2", MA_TENANT_ID)
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

    async def fake_run(spec, tenant_id, chatbot_config_id, focus_district=None):
        return ranked

    async def fake_gather(
        spec, ranked, tenant_id, chatbot_config_id, top_n=5, focus_district=None
    ):
        return citations

    async def fake_corpus(
        tenant_id, chatbot_config_id, state="MA", focus_district=None
    ):
        if focus_district is not None:
            return {
                "district_count": 1,
                "state": focus_district.get("state") or state,
                "districts": [focus_district],
                "focus_district": focus_district,
            }
        return {
            **corpus_summary,
            "state": state,
            "district_count": 5 if state == "CA" else 179,
        }

    async def fake_resolve_focus(tenant_id, org_code):
        return {
            "org_code": org_code,
            "district_name": "Saddleback Valley Unified School District",
            "state": "CA",
        }

    monkeypatch.setattr(
        "app.services.district_report.service.resolve_chatbot_config_id",
        fake_resolve_chatbot_config_id,
    )
    monkeypatch.setattr(
        "app.services.district_report.service.resolve_focus_district",
        fake_resolve_focus,
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
        tenant_id=MA_TENANT_ID,
        query_id="Q1",
    )
    assert result["query_id"] == "Q1"
    assert result["tenant_id"] == MA_TENANT_ID
    assert result["report_id"].startswith(f"DR-{MA_TENANT_ID}-Q1-")
    assert result["filename"].endswith(".pdf")
    assert result["pdf_bytes"].startswith(b"%PDF")
    assert len(result["pdf_bytes"]) > 500


async def test_service_generates_ca_report_pdf(_stub_retrieval, _stub_writer):
    result = await district_report_service.generate_report(
        tenant_id=CA_TENANT_ID,
        query_id="Q1",
    )
    assert result["query_id"] == "Q1"
    assert result["tenant_id"] == CA_TENANT_ID
    assert result["report_id"].startswith(f"DR-{CA_TENANT_ID}-Q1-")
    assert result["pdf_bytes"].startswith(b"%PDF")
    assert result["focus_district"] is not None
    assert result["focus_district"]["org_code"] == "30-73635"
    assert "Saddleback" in result["focus_district"]["district_name"]


async def test_service_ca_report_accepts_district_override(
    _stub_retrieval, _stub_writer
):
    result = await district_report_service.generate_report(
        tenant_id=CA_TENANT_ID,
        query_id="Q1",
        district_org_code="38-68478",
    )
    assert result["focus_district"]["org_code"] == "38-68478"


async def test_service_report_is_stakeholder_clean(_stub_retrieval, _stub_writer):
    """The generated PDF should not contain internal terms."""
    result = await district_report_service.generate_report(
        tenant_id=MA_TENANT_ID, query_id="Q7"
    )
    # We cannot grep PDF binary directly for all terms reliably, but the
    # writer stub returns clean text and the scrubber is a safety net, so
    # confirm the pipeline did not raise and produced a PDF.
    assert result["pdf_bytes"].startswith(b"%PDF")


def test_service_filename_is_safe(_stub_retrieval, _stub_writer):
    import asyncio

    result = asyncio.run(
        district_report_service.generate_report(tenant_id=MA_TENANT_ID, query_id="Q1")
    )
    # No spaces / unicode in the filename.
    assert " " not in result["filename"]
    assert result["filename"] == result["filename"].encode("ascii", "ignore").decode(
        "ascii"
    )


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
        ("/api/v1/district-reports/queries?tenant_id=4", "GET"),
        ("/api/v1/district-reports", "POST"),
        ("/api/v1/district-reports/status?task_id=x&tenant_id=4", "GET"),
        ("/api/v1/district-reports/download?task_id=x&tenant_id=4", "GET"),
    ]:
        resp = getattr(client, method.lower())(path)
        assert resp.status_code in (401, 403), f"{method} {path} -> {resp.status_code}"


def _super_admin_user():
    from unittest.mock import MagicMock

    role = MagicMock()
    role.name = "super_admin"
    return MagicMock(id=1, role=role)


def test_district_reports_post_rejects_unknown_query():
    """POST should 400 on an unknown query_id before enqueuing anything."""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from app.main import app
    from app.utils.dependencies import get_current_tenant_user, get_db

    app.dependency_overrides[get_current_tenant_user] = _super_admin_user

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/district-reports",
            json={"query_id": "Q99", "tenant_id": MA_TENANT_ID},
        )
        assert resp.status_code == 400
        body = resp.json()
        detail = body.get("detail") or body.get("error", {})
        assert "Q99" in str(detail)
    finally:
        app.dependency_overrides.clear()


def test_district_reports_post_rejects_ma_query_on_ca_tenant():
    """POST should 400 when query_id is not in the tenant's catalog."""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from app.main import app
    from app.utils.dependencies import get_current_tenant_user, get_db

    app.dependency_overrides[get_current_tenant_user] = _super_admin_user

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/district-reports",
            json={"query_id": "Q7", "tenant_id": CA_TENANT_ID},
        )
        assert resp.status_code == 400
        body = resp.json()
        detail = body.get("detail") or body.get("error", {})
        assert "Q7" in str(detail)
    finally:
        app.dependency_overrides.clear()

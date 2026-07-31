"""Unit tests for the `breakdown=true` mode of count_by_district.

No I/O — the vector store and school lookup are both stubbed, so these run
entirely offline. Cover:
  - breakdown=False leaves top_category/top_category_count untouched
  - top_category is the argmax and top_category_count matches
  - zero-count districts are never probed
  - a single selected category needs no probes at all
  - the saturation short-circuit skips the remaining categories
  - ties resolve to the earliest-declared category

Run:
    poetry run pytest tests/test_heatmap_breakdown.py -v
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.schemas.heatmap_engine import TimeframePreset, TopicCategory
from app.services.heatmap_engine import service as service_module
from app.services.heatmap_engine.service import HeatmapEngineService

ALL_CATEGORIES = list(TopicCategory)


def _school(name: str, org_code: str, district_type: str = "public"):
    return SimpleNamespace(
        name=name,
        org_code=org_code,
        district_type=district_type,
        state="MA",
    )


class FakeVectorStore:
    """Counts chunks from a `{district_name: {category: count}}` script.

    A district's combined count is the max over its per-category counts
    rather than their sum, mirroring the real payload shape: `topic_tags` is
    an array, so one chunk can carry several categories at once.
    """

    def __init__(self, script: dict[str, dict[TopicCategory, int]]):
        self.script = script
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def count_chunks(
        self,
        tenant_id: int,
        *,
        must_match=None,
        must_match_any=None,
        nested_match_any=None,
    ) -> int:
        district = (must_match or {}).get("district_name", "")
        requested = tuple((nested_match_any or {}).get("topic_tags", ()))
        self.calls.append((district, requested))

        per_category = self.script.get(district, {})
        if not requested:
            requested = tuple(c.value for c in ALL_CATEGORIES)
        counts = [per_category.get(TopicCategory(v), 0) for v in requested]
        return max(counts) if counts else 0

    def probe_calls(self) -> int:
        """Single-category calls, i.e. breakdown probes.

        Only meaningful when more than one category is selected — with a
        single selected category the combined phase-1 call is itself
        single-category and indistinguishable from a probe. Assert on
        `len(calls)` in that case instead.
        """
        return sum(1 for _, requested in self.calls if len(requested) == 1)

    def districts_probed(self) -> set[str]:
        return {d for d, requested in self.calls if len(requested) == 1}


@pytest.fixture
def stub_service(monkeypatch):
    """Build a service whose schools and vector store are scripted."""

    @asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(service_module, "AsyncSessionLocal", _no_db)

    def _build(schools, script):
        svc = HeatmapEngineService()
        store = FakeVectorStore(script)
        monkeypatch.setattr(svc, "_get_vector_store", lambda: store)

        async def _list_schools(db, tenant_id, state):
            return schools

        monkeypatch.setattr(svc, "_list_schools", _list_schools)
        return svc, store

    return _build


async def _run(svc, categories, *, breakdown=True, include_zero=True):
    return await svc.count_by_district(
        tenant_id=1,
        timeframe=TimeframePreset.YEAR,
        categories=categories,
        state="MA",
        include_zero=include_zero,
        breakdown=breakdown,
    )


def _by_name(response):
    return {d.district_name: d for d in response.districts}


async def test_breakdown_off_leaves_fields_unset(stub_service):
    svc, store = stub_service(
        [_school("Alpha", "001")],
        {"Alpha": {TopicCategory.LGBTQ: 7, TopicCategory.SEXED: 3}},
    )

    response = await _run(svc, ALL_CATEGORIES, breakdown=False)

    row = response.districts[0]
    assert row.chunk_count == 7
    assert row.top_category is None
    assert row.top_category_count == 0
    # No probes at all — the map path must stay exactly as cheap as before.
    assert store.probe_calls() == 0


async def test_top_category_is_the_argmax(stub_service):
    svc, _ = stub_service(
        [_school("Alpha", "001")],
        {
            "Alpha": {
                TopicCategory.SEXED: 3,
                TopicCategory.LGBTQ: 11,
                TopicCategory.CENSORSHIP: 5,
            }
        },
    )

    response = await _run(svc, ALL_CATEGORIES)

    row = response.districts[0]
    assert row.top_category == TopicCategory.LGBTQ
    assert row.top_category_count == 11
    # A single category can never out-count the combined total.
    assert row.top_category_count <= row.chunk_count


async def test_zero_count_districts_are_not_probed(stub_service):
    svc, store = stub_service(
        [_school("Alpha", "001"), _school("Quiet", "002")],
        {
            "Alpha": {TopicCategory.LGBTQ: 4, TopicCategory.SEXED: 1},
            "Quiet": {},
        },
    )

    response = await _run(svc, ALL_CATEGORIES)

    rows = _by_name(response)
    assert rows["Quiet"].chunk_count == 0
    assert rows["Quiet"].top_category is None
    assert rows["Quiet"].top_category_count == 0
    assert store.districts_probed() == {"Alpha"}


async def test_single_category_issues_no_probes(stub_service):
    schools = [_school("Alpha", "001"), _school("Beta", "002")]
    svc, store = stub_service(
        schools,
        {
            "Alpha": {TopicCategory.LGBTQ: 4},
            "Beta": {TopicCategory.LGBTQ: 9},
        },
    )

    response = await _run(svc, [TopicCategory.LGBTQ])

    # One combined count per school and nothing more.
    assert len(store.calls) == len(schools)
    for row in response.districts:
        assert row.top_category == TopicCategory.LGBTQ
        assert row.top_category_count == row.chunk_count


async def test_saturation_short_circuit_stops_probing(stub_service):
    # SEXED is first in enum order and already equals the combined total, so
    # the remaining four categories must never be probed.
    svc, store = stub_service(
        [_school("Alpha", "001")],
        {
            "Alpha": {
                TopicCategory.SEXED: 6,
                TopicCategory.LGBTQ: 2,
                TopicCategory.CENSORSHIP: 1,
            }
        },
    )

    response = await _run(svc, ALL_CATEGORIES)

    assert response.districts[0].top_category == TopicCategory.SEXED
    assert store.probe_calls() == 1


async def test_tie_resolves_to_earliest_declared_category(stub_service):
    # LGBTQ precedes CENSORSHIP in TopicCategory, so it wins an equal count.
    svc, _ = stub_service(
        [_school("Alpha", "001")],
        {
            "Alpha": {
                TopicCategory.LGBTQ: 5,
                TopicCategory.CENSORSHIP: 5,
                TopicCategory.GOVERNANCE: 8,
            }
        },
    )

    response = await _run(svc, [TopicCategory.LGBTQ, TopicCategory.CENSORSHIP])

    row = response.districts[0]
    assert row.top_category == TopicCategory.LGBTQ
    assert row.top_category_count == 5


async def test_empty_categories_means_all_categories(stub_service):
    svc, _ = stub_service(
        [_school("Alpha", "001")],
        {"Alpha": {TopicCategory.ADVOCACY: 4}},
    )

    response = await _run(svc, [])

    row = response.districts[0]
    assert row.top_category == TopicCategory.ADVOCACY
    assert row.top_category_count == 4


async def test_include_zero_false_still_reports_top_category(stub_service):
    svc, _ = stub_service(
        [_school("Alpha", "001"), _school("Quiet", "002")],
        {
            "Alpha": {TopicCategory.GOVERNANCE: 5, TopicCategory.SEXED: 2},
            "Quiet": {},
        },
    )

    response = await _run(svc, ALL_CATEGORIES, include_zero=False)

    assert [d.district_name for d in response.districts] == ["Alpha"]
    assert response.districts[0].top_category == TopicCategory.GOVERNANCE

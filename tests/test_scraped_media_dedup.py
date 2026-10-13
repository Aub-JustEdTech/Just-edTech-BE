"""Tests for ScrapedMedia content-hash dedup in ingest_scraped_media.

Two failure modes around the (school_id, content_hash) unique constraint
`uq_scraped_media_school_content`:

1. **Detected duplicate**: a prior row already owns the hash. The old code
   marked the current row `skipped_duplicate` *while writing content_hash* —
   i.e. it wrote the duplicate key it had just detected, hitting the very
   constraint it found. Status alone is enough; the hash must NOT be set.

2. **Concurrent race**: two workers processing two different rows with the
   same file content pass the dedup check (neither has committed yet), both
   try to write content_hash at the `ingesting` transition. The second
   commit raises IntegrityError. That must be caught and the loser marked
   `skipped_duplicate` (again, without re-writing the colliding hash).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.tasks.school_scraper_tasks import MediaPayload, _ingest_scraped_media_async


def _fake_sm(
    *,
    id: int = 100,
    school_id: int = 1,
    media_type: str = "document",
) -> MagicMock:
    sm = MagicMock()
    sm.id = id
    sm.school_id = school_id
    sm.media_type = media_type
    sm.source_media_url = "https://example.org/doc.pdf"
    sm.original_name = "doc.pdf"
    sm.source_page_url = "https://example.org/board"
    return sm


class _FakeDB:
    """A minimal async-session mock that supports `await db.get(...)` and
    `await db.flush()` without needing real SQLAlchemy machinery."""

    def __init__(self, sm):
        self._sm = sm
        self.get = AsyncMock(return_value=sm)
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


async def _run_ingest(
    *,
    sm,
    existing_hash_row=None,
    update_side_effect=None,
    update_capture: list | None = None,
):
    """Invoke _ingest_scraped_media_async with everything mocked."""
    db = _FakeDB(sm)

    async def _default_update(db_arg, sm_id, **fields):
        if update_capture is not None:
            update_capture.append({"sm_id": sm_id, **fields})
        return MagicMock()

    if update_side_effect is None:
        update_side_effect = _default_update

    update_mock = AsyncMock(side_effect=update_side_effect)
    get_hash_mock = AsyncMock(return_value=existing_hash_row)
    materialize_mock = AsyncMock(
        return_value=MediaPayload(text="hello", content_hash="HASH_A")
    )
    create_doc_mock = AsyncMock(return_value=999)
    year_filter_mock = AsyncMock(return_value=(2025, True, None))

    with (
        patch(
            "app.tasks.school_scraper_tasks.AsyncSessionLocal",
            return_value=MagicMock(__aenter__=AsyncMock(return_value=db)),
        ),
        patch(
            "app.crud.schools.update_scraped_media",
            new=update_mock,
        ),
        patch(
            "app.crud.schools.get_scraped_media_by_content_hash",
            new=get_hash_mock,
        ),
        patch(
            "app.tasks.school_scraper_tasks._materialize_media",
            new=materialize_mock,
        ),
        patch(
            "app.tasks.school_scraper_tasks._create_document_and_enqueue",
            new=create_doc_mock,
        ),
        patch(
            "app.services.web_scraper.year_filter.evaluate_media_year_async",
            new=year_filter_mock,
        ),
    ):
        return await _ingest_scraped_media_async(sm.id)


async def test_detected_duplicate_does_not_write_colliding_hash():
    """A prior row owns HASH_A -> current row marked skipped_duplicate
    WITHOUT setting content_hash (which would re-violate the constraint)."""
    sm = _fake_sm(id=100, school_id=1)
    existing = MagicMock()
    existing.id = 200  # different row already owns HASH_A

    updates: list[dict] = []

    result = await _run_ingest(
        sm=sm,
        existing_hash_row=existing,
        update_capture=updates,
    )

    assert result["status"] == "skipped_duplicate"
    skip_calls = [c for c in updates if c.get("status") == "skipped_duplicate"]
    assert skip_calls, "expected a skipped_duplicate update"
    assert all(
        "content_hash" not in c for c in skip_calls
    ), f"skipped_duplicate must not write content_hash: {skip_calls}"


async def test_concurrent_race_on_ingesting_transition_is_caught():
    """Both rows pass the dedup check; the loser's `ingesting` write raises
    IntegrityError. It must be rolled back and marked skipped_duplicate
    without re-raising (which would trigger a 240s retry)."""
    sm = _fake_sm(id=100, school_id=1)
    updates: list[dict] = []

    async def _update(db_arg, sm_id, **fields):
        updates.append({"sm_id": sm_id, **fields})
        if fields.get("status") == "ingesting" and "content_hash" in fields:
            raise IntegrityError(
                "duplicate key value violates unique constraint "
                "uq_scraped_media_school_content",
                params=fields,
                orig=Exception("simulated"),
            )
        return MagicMock()

    result = await _run_ingest(
        sm=sm,
        existing_hash_row=None,
        update_side_effect=_update,
        update_capture=updates,
    )

    assert result["status"] == "skipped_duplicate"
    skip_calls = [c for c in updates if c.get("status") == "skipped_duplicate"]
    assert skip_calls, "expected a recovery skipped_duplicate update"
    assert all(
        "content_hash" not in c for c in skip_calls
    ), f"recovery must not write content_hash: {skip_calls}"


async def test_no_duplicate_marks_ingesting_normally():
    """Sanity: when there's no duplicate and no race, the flow reaches the
    `ingesting` write with content_hash and proceeds to create a document."""
    sm = _fake_sm(id=100, school_id=1)
    updates: list[dict] = []

    result = await _run_ingest(
        sm=sm,
        existing_hash_row=None,
        update_capture=updates,
    )

    assert result["status"] == "completed"
    assert result["document_id"] == 999
    ingesting = [c for c in updates if c.get("status") == "ingesting"]
    assert ingesting, "expected an ingesting update"
    assert ingesting[0].get("content_hash") == "HASH_A"

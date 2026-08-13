"""Celery tasks package."""

# Importing each task module here is what actually registers its @celery_app.task
# decorators on the worker. `celery_app.autodiscover_tasks(["app.tasks"])` only
# looks for a submodule literally named `tasks` (i.e. `app.tasks.tasks`), which
# does not exist, so it does NOT discover the individual modules in this
# package. Any new task module MUST be imported below or its tasks will not be
# registered and beat-sent messages will be discarded with KeyError.

# Document processing pipeline tasks
from app.tasks.document_pipeline import (
    process_document_pipeline,
    step1_download_from_s3,
    step2_extract_text,
    step3_chunk_text,
    step4_generate_embeddings,
    step5_store_vectors,
)
from app.tasks.document_tasks import process_document_task

# Media transcription — runs BEFORE the pipeline for audio/video/links
from app.tasks.media_transcription_tasks import (  # noqa: F401
    transcribe_media_task,
)

# Token aggregation + billing tasks
from app.tasks.token_aggregation_tasks import (
    aggregate_daily_token_usage_task,
    backfill_daily_token_usage_task,
)

# School scraper ingest + sweep tasks (scraping queue)
from app.tasks.school_scraper_tasks import (  # noqa: F401
    ingest_scraped_media,
    sweep_school_media,
)

# Heatmap batch classification tasks (default queue)
from app.tasks.batch_classification_tasks import (  # noqa: F401
    apply_batch_results_task,
    poll_batch_classification_task,
    submit_pending_batch_classification_task,
)

# Heatmap reconciliation tasks (default queue)
from app.tasks.heatmap_reconciliation_tasks import (  # noqa: F401
    reconcile_heatmap_aggregate_task,
)

__all__ = [
    # Document pipeline
    "process_document_task",
    "process_document_pipeline",
    "step1_download_from_s3",
    "step2_extract_text",
    "step3_chunk_text",
    "step4_generate_embeddings",
    "step5_store_vectors",
    # Media transcription
    "transcribe_media_task",
    # Token aggregation
    "aggregate_daily_token_usage_task",
    "backfill_daily_token_usage_task",
    # School scraper ingest
    "ingest_scraped_media",
    "sweep_school_media",
    # Heatmap batch classification
    "submit_pending_batch_classification_task",
    "poll_batch_classification_task",
    "apply_batch_results_task",
    # Heatmap reconciliation
    "reconcile_heatmap_aggregate_task",
]

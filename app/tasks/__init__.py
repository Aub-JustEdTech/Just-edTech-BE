"""Celery tasks package."""

# Import old tasks (for backward compatibility)
# Import new pipeline tasks
from app.tasks.document_pipeline import (
    process_document_pipeline,
    step1_download_from_s3,
    step2_extract_text,
    step3_chunk_text,
    step4_generate_embeddings,
    step5_store_vectors,
)
from app.tasks.document_tasks import process_document_task
from app.tasks.token_aggregation_tasks import (
    aggregate_daily_token_usage_task,
    backfill_daily_token_usage_task,
)

__all__ = [
    # Old task (kept for backward compatibility)
    "process_document_task",
    # New pipeline tasks
    "process_document_pipeline",
    "step1_download_from_s3",
    "step2_extract_text",
    "step3_chunk_text",
    "step4_generate_embeddings",
    "step5_store_vectors",
    # Token aggregation tasks
    "aggregate_daily_token_usage_task",
    "backfill_daily_token_usage_task",
]

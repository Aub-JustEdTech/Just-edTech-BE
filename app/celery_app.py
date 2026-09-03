"""Celery application configuration for background task processing."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "just-edtech",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_BACKEND_URL,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task tracking
    task_track_started=True,
    # Time limits
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3000,  # 50 minutes soft limit
    # Prefetch settings (OPTIMIZED for low-memory instances)
    worker_prefetch_multiplier=1,  # Prefetch 1 task per worker to minimize memory usage
    # For high-memory instances, increase to 4 for better throughput
    # Task result settings
    result_expires=3600,  # Results expire after 1 hour
    # Task routing
    task_routes={
        "app.tasks.document_tasks.process_document_task": {"queue": "documents"},
        "pipeline.process_document": {"queue": "documents"},
        "pipeline.download_from_s3": {"queue": "documents"},
        "pipeline.extract_text": {"queue": "documents"},
        "pipeline.summarize_document": {"queue": "documents"},
        "pipeline.classify_document": {"queue": "documents"},
        "pipeline.chunk_text": {"queue": "documents"},
        "pipeline.contextualize_chunks": {"queue": "documents"},
        "pipeline.generate_embeddings": {"queue": "documents"},
        "pipeline.store_vectors": {"queue": "documents"},
        "pipeline.accumulate_batch": {"queue": "documents"},
        "app.tasks.school_scraper_tasks.ingest_scraped_media": {
            "queue": "scraping"
        },
        "app.tasks.school_scraper_tasks.sweep_school_media": {
            "queue": "scraping"
        },
        "app.tasks.school_scraper_tasks.scrape_media_batch": {
            "queue": "scraping"
        },
        # Transcription is minutes-long and I/O-bound — the same workload shape
        # the scraping queue already carries, and that queue runs with a 6000s
        # soft limit. The documents queue is sized for second-scale parses; a
        # long provider poll parked there would starve ordinary uploads.
        "pipeline.transcribe_media": {"queue": "scraping"},
        # District analytics reports — retrieval + LLM writer + PDF render.
        # Same shape as the documents queue (DB + LLM), not scraping.
        "generate_district_report": {"queue": "documents"},
    },
    # Retry settings
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    # Performance optimization (NEW)
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks (prevent memory leaks)
    worker_disable_rate_limits=True,  # Disable rate limits for better performance
    broker_pool_limit=10,  # Increase Redis connection pool
    broker_connection_retry_on_startup=True,  # Retry connection on startup
    # Periodic task schedule (beat schedule).
    #
    # Removed entirely (not wanted, not just paused):
    #   - aggregate-daily-token-usage (aggregate_daily_token_usage)
    #   - aggregate-monthly-billing (aggregate_monthly_billing)
    #
    # All other entries below are PAUSED (commented out) while the tenant-4
    # heatmap backfill is under manual control -- re-enable by uncommenting
    # the relevant block(s) once the manual backfill work is done. No task
    # code was removed; only these beat entries are inactive, so any of them
    # can still be triggered manually via `<task>.delay(...)`.
    beat_schedule={
        # "submit-pending-batch-classification": {
        #     "task": "submit_pending_batch_classification",
        #     "schedule": crontab(hour=4, minute=0),  # Daily at 4:00 AM UTC
        #     "options": {"expires": 3600},
        # },
        # "poll-batch-classification": {
        #     "task": "poll_batch_classification",
        #     "schedule": crontab(minute="*/15"),  # Every 15 minutes
        #     "options": {"expires": 900},
        # },
        # # Nightly reconciliation: recompute heatmap_aggregate from Qdrant
        # # to catch drift from failed set_payload calls or manual edits.
        # "reconcile-heatmap-aggregate": {
        #     "task": "reconcile_heatmap_aggregate",
        #     "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM UTC
        #     "options": {"expires": 2 * 3600},
        # },
        # # Hourly reconciliation: re-enqueue documents stuck at PROCESSING or
        # # PENDING past the staleness threshold. Catches the silent orphan
        # # failure mode where a Celery chain continuation was lost (broker
        # # eviction under allkeys-lru, or OOM rejection under noeviction)
        # # and no _mark_stage_failed ever ran.
        # "reconcile-stuck-documents": {
        #     "task": "reconcile_stuck_documents",
        #     "schedule": crontab(minute=15),  # Hourly at :15
        #     "options": {"expires": 1800},
        # },
        # # Weekly sweep of every active school source URL, tenant-agnostic
        # # (no school_ids filter = all schools). Runs Monday 1:00 AM UTC,
        # # ahead of the 2:00-4:00 AM jobs above so they don't compete for the DB.
        # "sweep-school-media": {
        #     "task": "app.tasks.school_scraper_tasks.sweep_school_media",
        #     "schedule": crontab(
        #         hour=1, minute=0, day_of_week=1
        #     ),  # Weekly, Monday 1:00 AM UTC
        #     "options": {"expires": 3 * 3600},
        # },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])

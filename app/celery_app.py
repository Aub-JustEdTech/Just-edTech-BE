"""Celery application configuration for background task processing."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "just-edtech",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
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
    },
    # Retry settings
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    # Performance optimization (NEW)
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks (prevent memory leaks)
    worker_disable_rate_limits=True,  # Disable rate limits for better performance
    broker_pool_limit=10,  # Increase Redis connection pool
    broker_connection_retry_on_startup=True,  # Retry connection on startup
    # Periodic task schedule (beat schedule)
    beat_schedule={
        "aggregate-daily-token-usage": {
            "task": "aggregate_daily_token_usage",
            "schedule": crontab(hour=2, minute=0),  # Run daily at 2:00 AM UTC
            "options": {
                "expires": 3600,  # Task expires after 1 hour if not picked up
            },
        },
        "aggregate-monthly-billing": {
            "task": "aggregate_monthly_billing",
            "schedule": crontab(
                hour=3, minute=0, day_of_month=1
            ),  # Run on 1st of each month at 3:00 AM UTC
            "options": {
                "expires": 7200,  # Task expires after 2 hours if not picked up
            },
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])

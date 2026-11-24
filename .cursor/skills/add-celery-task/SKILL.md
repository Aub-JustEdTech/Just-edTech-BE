---
name: add-celery-task
description: Adds a background Celery task to the backend. Use when the user asks to run something in the background, add a scheduled job, create a periodic task, process something asynchronously outside the request cycle, queue work for a Celery worker, or add a recurring cron-style task.
---

When adding a Celery background task:

## 1. Define the Task

Create or add to `app/tasks/{domain}_tasks.py`:

```python
import logging
from app.celery_app import celery_app
from app.tasks.loop_utils import get_event_loop

logger = logging.getLogger(__name__)

@celery_app.task(name="process_{domain}", bind=True, max_retries=3)
def process_{domain}_task(self, resource_id: int, extra_param: str | None = None) -> None:
    """
    Background task to process a {domain} resource.
    Retries up to 3 times with exponential backoff.
    """
    try:
        loop = get_event_loop()
        loop.run_until_complete(_process_{domain}_async(resource_id, extra_param))
    except Exception as exc:
        logger.error("Task process_{domain} failed for resource %s: %s", resource_id, str(exc))
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _process_{domain}_async(resource_id: int, extra_param: str | None) -> None:
    """Async implementation — runs inside the Celery worker event loop."""
    from app.db.connector import AsyncSessionLocal
    from app.crud.{domain} import {domain}_crud

    async with AsyncSessionLocal() as db:
        resource = await {domain}_crud.get(db, resource_id)
        if not resource:
            logger.warning("Resource %s not found, skipping", resource_id)
            return

        # ... business logic here ...
        logger.info("Successfully processed resource %s", resource_id)
```

## 2. Register for Celery Discovery

Add to `app/tasks/__init__.py`:

```python
from app.tasks.{domain}_tasks import process_{domain}_task  # noqa: F401
```

## 3. For Periodic (Scheduled) Tasks

Add to the beat schedule in `app/celery_app.py`:

```python
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    # ... existing tasks ...
    "process-{domain}-daily": {
        "task": "process_{domain}",
        "schedule": crontab(hour=3, minute=0),  # 3:00 AM UTC daily
        "args": [],
    },
}
```

Common crontab patterns:
```python
crontab(hour=2, minute=0)             # 2:00 AM UTC daily
crontab(day_of_month=1, hour=3)       # 1st of each month at 3:00 AM
crontab(minute="*/15")                # every 15 minutes
crontab(day_of_week=1, hour=9)        # every Monday at 9:00 AM
```

## 4. Enqueue the Task from an Endpoint

```python
from app.tasks.{domain}_tasks import process_{domain}_task

# Fire and forget
process_{domain}_task.delay(resource_id=resource.id)

# With countdown (delay in seconds)
process_{domain}_task.apply_async(args=[resource.id], countdown=30)
```

## 5. Test Locally

```bash
# Start worker
poetry run celery -A app.celery_app worker --loglevel=info --concurrency=2

# Start beat scheduler (for periodic tasks)
poetry run celery -A app.celery_app beat --loglevel=info

# Monitor via Flower
poetry run celery -A app.celery_app flower
```

## Constraints

- Always `bind=True` and `max_retries=3` on tasks that can fail transiently
- Exponential backoff: `countdown=60 * (2 ** self.request.retries)`
- Bridge async code via `loop_utils.get_event_loop()` — Celery workers are synchronous
- Use `logging`, never `print()`
- Open a new `AsyncSessionLocal()` inside the async impl — never pass a session from outside
- Task names must be unique strings — use `"process_{domain}"` convention

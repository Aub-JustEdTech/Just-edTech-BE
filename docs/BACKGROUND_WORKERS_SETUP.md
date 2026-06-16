# Background Workers Setup Guide

## Overview

Currently, document processing happens synchronously. To enable production-ready async processing, you need to set up background workers using either **Celery** or **RQ (Redis Queue)**.

## Option 1: Celery (Recommended for Production)

### 1. Install Dependencies

```bash
poetry add celery[redis] redis
```

### 2. Create Celery App

Create `app/celery_app.py`:

```python
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "just-edtech",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/2",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3000,  # 50 minutes soft limit
)
```

### 3. Create Tasks

Create `app/tasks/document_tasks.py`:

```python
import asyncio
from app.celery_app import celery_app
from app.db.connector import get_session
from app.services.document_service import DocumentService

document_service = DocumentService()

@celery_app.task(name="process_document", bind=True, max_retries=3)
def process_document_task(self, document_id: int, job_id: int):
    """Celery task to process a document."""
    try:
        # Get database session
        async def process():
            async for db in get_session():
                await document_service.process_document_background(
                    db=db,
                    document_id=document_id,
                    job_id=job_id,
                )
                break
        
        # Run async function in sync context
        asyncio.run(process())
        
    except Exception as exc:
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
```

### 4. Update Document Endpoint

Modify `app/api/endpoints/documents.py`:

```python
from app.tasks.document_tasks import process_document_task

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(...):
    # ... existing code ...
    
    # Queue background processing with Celery
    process_document_task.delay(document.id, job.id)
    
    return document
```

### 5. Start Celery Worker

```bash
# In development
celery -A app.celery_app worker --loglevel=info

# In production with multiple workers
celery -A app.celery_app worker --loglevel=info --concurrency=4

# With autoscaling
celery -A app.celery_app worker --loglevel=info --autoscale=10,3
```

### 6. Monitor with Flower (Optional)

```bash
poetry add flower

# Start Flower dashboard
celery -A app.celery_app flower --port=5555
```

Visit `http://localhost:5555` to monitor tasks.

---

## Option 2: RQ (Redis Queue) - Simpler Alternative

### 1. Install Dependencies

```bash
poetry add rq redis
```

### 2. Create Worker Script

Create `app/workers/document_worker.py`:

```python
import asyncio
from rq import Worker, Queue, Connection
from redis import Redis
from app.core.config import settings
from app.db.connector import get_session
from app.services.document_service import DocumentService

redis_conn = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=2,  # Use different DB than cache
)

document_service = DocumentService()

async def process_document_job(document_id: int, job_id: int):
    """Process a document (async)."""
    async for db in get_session():
        await document_service.process_document_background(
            db=db,
            document_id=document_id,
            job_id=job_id,
        )
        break

def process_document_sync(document_id: int, job_id: int):
    """Sync wrapper for async processing."""
    asyncio.run(process_document_job(document_id, job_id))

if __name__ == "__main__":
    with Connection(redis_conn):
        worker = Worker(["documents"], connection=redis_conn)
        worker.work()
```

### 3. Create Queue Manager

Create `app/utils/queue.py`:

```python
from rq import Queue
from redis import Redis
from app.core.config import settings

redis_conn = Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=2,
)

document_queue = Queue("documents", connection=redis_conn)

def enqueue_document_processing(document_id: int, job_id: int):
    """Enqueue a document for processing."""
    from app.workers.document_worker import process_document_sync
    
    job = document_queue.enqueue(
        process_document_sync,
        document_id,
        job_id,
        job_timeout=3600,  # 1 hour
        result_ttl=86400,  # Keep results for 24 hours
    )
    return job.id
```

### 4. Update Document Endpoint

```python
from app.utils.queue import enqueue_document_processing

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(...):
    # ... existing code ...
    
    # Queue background processing with RQ
    job_id = enqueue_document_processing(document.id, job.id)
    
    return document
```

### 5. Start RQ Worker

```bash
# In development
python -m app.workers.document_worker

# In production with multiple workers (use supervisord or systemd)
python -m app.workers.document_worker &
python -m app.workers.document_worker &
```

### 6. Monitor with RQ Dashboard (Optional)

```bash
poetry add rq-dashboard

# Start dashboard
rq-dashboard --redis-host localhost --redis-port 6379 --redis-db 2
```

Visit `http://localhost:9181` to monitor jobs.

---

## Docker Configuration

### docker-compose.yml

Add worker services:

```yaml
services:
  # ... existing services ...
  
  celery-worker:
    build: .
    command: celery -A app.celery_app worker --loglevel=info --concurrency=4
    depends_on:
      - postgres
      - redis
    env_file:
      - .env
    volumes:
      - ./temp_uploads:/app/temp_uploads
      - ./chroma_db:/app/chroma_db
    networks:
      - app-network

  celery-beat:  # For scheduled tasks
    build: .
    command: celery -A app.celery_app beat --loglevel=info
    depends_on:
      - postgres
      - redis
    env_file:
      - .env
    networks:
      - app-network

  flower:  # Monitoring
    build: .
    command: celery -A app.celery_app flower --port=5555
    ports:
      - "5555:5555"
    depends_on:
      - redis
    env_file:
      - .env
    networks:
      - app-network
```

---

## Production Deployment

### Using Supervisor (Celery)

Create `/etc/supervisor/conf.d/celery-worker.conf`:

```ini
[program:celery-worker]
command=/path/to/venv/bin/celery -A app.celery_app worker --loglevel=info --concurrency=4
directory=/path/to/project
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/celery/worker.log
```

Start:
```bash
supervisorctl reread
supervisorctl update
supervisorctl start celery-worker
```

### Using Systemd (RQ)

Create `/etc/systemd/system/rq-worker@.service`:

```ini
[Unit]
Description=RQ Worker %i
After=network.target redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/python -m app.workers.document_worker
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Start multiple workers:
```bash
systemctl enable rq-worker@1
systemctl enable rq-worker@2
systemctl start rq-worker@1
systemctl start rq-worker@2
```

---

## Monitoring and Logging

### 1. Add Logging to Tasks

```python
import logging

logger = logging.getLogger(__name__)

@celery_app.task
def process_document_task(document_id: int, job_id: int):
    logger.info(f"Starting processing for document {document_id}")
    try:
        # Process document
        logger.info(f"Successfully processed document {document_id}")
    except Exception as e:
        logger.error(f"Failed to process document {document_id}: {e}")
        raise
```

### 2. Set Up Sentry (Optional)

```bash
poetry add sentry-sdk

# In app/celery_app.py
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn="your-sentry-dsn",
    integrations=[CeleryIntegration()],
)
```

---

## Health Checks

### Celery Health Check Endpoint

Add to `app/api/endpoints/admin.py`:

```python
from app.celery_app import celery_app

@router.get("/health/celery")
async def celery_health():
    """Check Celery worker health."""
    inspector = celery_app.control.inspect()
    active = inspector.active()
    stats = inspector.stats()
    
    if not active or not stats:
        raise HTTPException(
            status_code=503,
            detail="No active Celery workers"
        )
    
    return {
        "status": "healthy",
        "workers": len(active),
        "active_tasks": sum(len(tasks) for tasks in active.values()),
    }
```

---

## Performance Tuning

### Celery Configuration

```python
# app/celery_app.py
celery_app.conf.update(
    # Prefetch settings
    worker_prefetch_multiplier=1,  # Process one task at a time
    
    # Task result settings
    result_expires=3600,  # Results expire after 1 hour
    
    # Task routing
    task_routes={
        "process_document": {"queue": "documents"},
        "batch_process": {"queue": "bulk"},
    },
    
    # Resource limits
    task_time_limit=3600,  # Hard limit: 1 hour
    task_soft_time_limit=3000,  # Soft limit: 50 minutes
    
    # Retry settings
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
)
```

---

## Troubleshooting

### Common Issues

1. **Tasks not being picked up**
   - Check Redis connection
   - Verify worker is running
   - Check queue name matches

2. **Tasks timing out**
   - Increase `task_time_limit`
   - Optimize document processing
   - Split large documents

3. **Memory issues**
   - Reduce `worker_concurrency`
   - Implement chunked processing
   - Add memory limits to workers

4. **Failed tasks**
   - Check logs for errors
   - Verify S3/OpenAI credentials
   - Ensure tenant config exists

---

## Recommendation

For **Just-EdTech-BE**, I recommend:

1. **Start with RQ** if you want simplicity and are getting started
2. **Migrate to Celery** when you need:
   - Task scheduling (periodic tasks)
   - Complex task workflows
   - Advanced monitoring
   - Higher scale (1000+ tasks/minute)

Both are production-ready. Celery has more features, RQ is simpler to set up and maintain.


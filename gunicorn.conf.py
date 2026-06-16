import multiprocessing
import os

bind = "0.0.0.0:8000"

# Configurable workers via env var (default 2 for t4g.medium with 2 vCPU, 4GB RAM)
# For larger instances, set GUNICORN_WORKERS env var
workers = int(os.environ.get("GUNICORN_WORKERS", 2))
worker_class = "uvicorn.workers.UvicornWorker"

timeout = 300
keepalive = 5
graceful_timeout = 30

# Recycle workers after N requests to prevent slow memory leaks
max_requests = 2000
max_requests_jitter = 200

accesslog = "-"
errorlog = "-"
loglevel = "info"

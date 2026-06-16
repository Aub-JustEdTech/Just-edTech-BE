"""
Redis Pipeline Tracker
Provides fast, real-time status tracking for document processing pipeline.

This module handles:
- Document status tracking (current stage, progress)
- Stage-level tracking (downloading, extracting, chunking, embedding, storing)
- Active job queue management
- Batch progress aggregation
- Error logging
- Rate limiting
- Pub/Sub for real-time updates
"""

import json
import logging
from datetime import datetime
from typing import Any

from redis import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class RedisPipelineTracker:
    """
    Redis-based pipeline status tracker for fast, real-time updates.

    Uses Redis DB 2 (separate from Celery's DB 0 and refresh tokens DB 0)
    to avoid conflicts and provide clean separation of concerns.

    Data structures used:
    - Hashes: For document status and stage tracking
    - Sorted Sets: For active job queue (sorted by timestamp)
    - Lists: For error history
    - Strings: For rate limiting counters
    - Pub/Sub: For real-time UI updates

    All keys have a 24-hour TTL to auto-cleanup completed/failed jobs.
    """

    def __init__(self):
        """Initialize Redis connection for pipeline tracking"""
        try:
            self.redis = Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                db=2,  # Use separate DB for pipeline tracking
                decode_responses=True,  # Automatically decode bytes to strings
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # Test connection
            self.redis.ping()
            logger.info("Redis Pipeline Tracker initialized successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

        self.ttl = 86400  # 24 hours TTL for all keys

    # ==================== Document Status ====================

    def set_document_status(
        self,
        doc_id: int,
        stage: str,
        status: str,
        progress: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Update document processing status in Redis.

        Args:
            doc_id: Document ID
            stage: Current stage (downloading, extracting, chunking, embedding, storing, completed, failed)
            status: Status (pending, in_progress, completed, failed, retrying)
            progress: Progress percentage (0-100)
            metadata: Additional metadata (e.g., chunks_created, embeddings_generated)

        Example:
            tracker.set_document_status(
                doc_id=123,
                stage="embedding",
                status="in_progress",
                progress=65.5,
                metadata={"chunks_processed": 30, "total_chunks": 45}
            )
        """
        try:
            key = f"doc:status:{doc_id}"
            data = {
                "current_stage": stage,
                "status": status,
                "progress_percentage": str(progress),
                "last_updated": datetime.utcnow().isoformat(),
            }
            if metadata:
                # Flatten metadata into the hash
                for k, v in metadata.items():
                    data[k] = str(v)

            self.redis.hset(key, mapping=data)
            self.redis.expire(key, self.ttl)

            logger.debug(
                f"Updated status for doc {doc_id}: {stage} - {status} ({progress}%)"
            )
        except Exception as e:
            logger.error(f"Failed to set document status for doc {doc_id}: {e}")

    def get_document_status(self, doc_id: int) -> dict[str, Any] | None:
        """
        Get document status from Redis.

        Args:
            doc_id: Document ID

        Returns:
            Dictionary with status data or None if not found

        Example:
            status = tracker.get_document_status(123)
            # Returns: {
            #     "current_stage": "embedding",
            #     "status": "in_progress",
            #     "progress_percentage": "65.5",
            #     "last_updated": "2024-01-01T10:05:30.123456",
            #     "chunks_processed": "30",
            #     "total_chunks": "45"
            # }
        """
        try:
            key = f"doc:status:{doc_id}"
            data = self.redis.hgetall(key)
            return data if data else None
        except Exception as e:
            logger.error(f"Failed to get document status for doc {doc_id}: {e}")
            return None

    def delete_document_status(self, doc_id: int) -> None:
        """Delete document status from Redis (cleanup)"""
        try:
            key = f"doc:status:{doc_id}"
            self.redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete document status for doc {doc_id}: {e}")

    # ==================== Stage Tracking ====================

    def update_stage(self, doc_id: int, stage: str, status: str) -> None:
        """
        Update specific stage status.

        Args:
            doc_id: Document ID
            stage: Stage name (downloading, extracting, chunking, embedding, storing)
            status: Status (pending, in_progress, completed, failed, retrying)

        Example:
            tracker.update_stage(123, "downloading", "completed")
            tracker.update_stage(123, "extracting", "in_progress")
        """
        try:
            key = f"doc:stage:{doc_id}"
            self.redis.hset(key, stage, status)
            self.redis.expire(key, self.ttl)
            logger.debug(f"Updated stage for doc {doc_id}: {stage} -> {status}")
        except Exception as e:
            logger.error(f"Failed to update stage for doc {doc_id}: {e}")

    def get_all_stages(self, doc_id: int) -> dict[str, str]:
        """
        Get all stage statuses for a document.

        Args:
            doc_id: Document ID

        Returns:
            Dictionary mapping stage names to statuses

        Example:
            stages = tracker.get_all_stages(123)
            # Returns: {
            #     "downloading": "completed",
            #     "extracting": "completed",
            #     "chunking": "completed",
            #     "embedding": "in_progress",
            #     "storing": "pending"
            # }
        """
        try:
            key = f"doc:stage:{doc_id}"
            return self.redis.hgetall(key)
        except Exception as e:
            logger.error(f"Failed to get stages for doc {doc_id}: {e}")
            return {}

    def delete_stage_tracking(self, doc_id: int) -> None:
        """Delete stage tracking from Redis (cleanup)"""
        try:
            key = f"doc:stage:{doc_id}"
            self.redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete stage tracking for doc {doc_id}: {e}")

    # ==================== Active Jobs Queue ====================

    def add_active_job(self, doc_id: int) -> None:
        """
        Add document to active jobs queue.
        Uses sorted set with timestamp as score for chronological ordering.

        Args:
            doc_id: Document ID
        """
        try:
            timestamp = datetime.utcnow().timestamp()
            self.redis.zadd("active:jobs", {str(doc_id): timestamp})
            logger.debug(f"Added doc {doc_id} to active jobs queue")
        except Exception as e:
            logger.error(f"Failed to add doc {doc_id} to active jobs: {e}")

    def remove_active_job(self, doc_id: int) -> None:
        """
        Remove document from active jobs queue.

        Args:
            doc_id: Document ID
        """
        try:
            self.redis.zrem("active:jobs", str(doc_id))
            logger.debug(f"Removed doc {doc_id} from active jobs queue")
        except Exception as e:
            logger.error(f"Failed to remove doc {doc_id} from active jobs: {e}")

    def get_active_jobs(self, limit: int = 100) -> list[str]:
        """
        Get list of active job IDs (oldest first).

        Args:
            limit: Maximum number of jobs to return

        Returns:
            List of document IDs as strings
        """
        try:
            return self.redis.zrange("active:jobs", 0, limit - 1)
        except Exception as e:
            logger.error(f"Failed to get active jobs: {e}")
            return []

    def get_active_job_count(self) -> int:
        """
        Get count of active jobs.

        Returns:
            Number of active jobs
        """
        try:
            return self.redis.zcard("active:jobs")
        except Exception as e:
            logger.error(f"Failed to get active job count: {e}")
            return 0

    # ==================== Batch Progress ====================

    def update_batch_progress(
        self, batch_id: int, stage_counts: dict[str, int]
    ) -> None:
        """
        Update batch progress counters.

        Args:
            batch_id: Batch ID
            stage_counts: Dictionary mapping stage names to counts

        Example:
            tracker.update_batch_progress(
                batch_id=456,
                stage_counts={
                    "total": 100,
                    "completed": 75,
                    "failed": 5,
                    "downloading": 5,
                    "extracting": 10,
                    "chunking": 15,
                    "embedding": 20,
                    "storing": 25
                }
            )
        """
        try:
            key = f"batch:progress:{batch_id}"
            # Convert all values to strings for Redis
            data = {k: str(v) for k, v in stage_counts.items()}
            self.redis.hset(key, mapping=data)
            self.redis.expire(key, self.ttl)
            logger.debug(f"Updated batch progress for batch {batch_id}")
        except Exception as e:
            logger.error(f"Failed to update batch progress for batch {batch_id}: {e}")

    def get_batch_progress(self, batch_id: int) -> dict[str, int]:
        """
        Get batch progress.

        Args:
            batch_id: Batch ID

        Returns:
            Dictionary mapping stage names to counts (as integers)
        """
        try:
            key = f"batch:progress:{batch_id}"
            data = self.redis.hgetall(key)
            # Convert string values back to int
            return {k: int(v) for k, v in data.items()} if data else {}
        except Exception as e:
            logger.error(f"Failed to get batch progress for batch {batch_id}: {e}")
            return {}

    def increment_batch_stage(self, batch_id: int, stage: str, amount: int = 1) -> None:
        """
        Increment counter for specific stage in a batch.

        Args:
            batch_id: Batch ID
            stage: Stage name
            amount: Amount to increment (default 1)
        """
        try:
            key = f"batch:progress:{batch_id}"
            self.redis.hincrby(key, stage, amount)
            self.redis.expire(key, self.ttl)
        except Exception as e:
            logger.error(f"Failed to increment batch stage for batch {batch_id}: {e}")

    def delete_batch_progress(self, batch_id: int) -> None:
        """Delete batch progress from Redis (cleanup)"""
        try:
            key = f"batch:progress:{batch_id}"
            self.redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete batch progress for batch {batch_id}: {e}")

    # ==================== Error Tracking ====================

    def log_error(
        self, doc_id: int, stage: str, error: str, retry_count: int = 0
    ) -> None:
        """
        Log error for document (keeps last 10 errors).

        Args:
            doc_id: Document ID
            stage: Stage where error occurred
            error: Error message
            retry_count: Number of retries attempted
        """
        try:
            key = f"doc:errors:{doc_id}"
            error_data = json.dumps(
                {
                    "stage": stage,
                    "error": error,
                    "timestamp": datetime.utcnow().isoformat(),
                    "retry": retry_count,
                }
            )
            self.redis.lpush(key, error_data)
            self.redis.ltrim(key, 0, 9)  # Keep last 10 errors
            self.redis.expire(key, self.ttl)
            logger.debug(f"Logged error for doc {doc_id} at stage {stage}")
        except Exception as e:
            logger.error(f"Failed to log error for doc {doc_id}: {e}")

    def get_errors(self, doc_id: int) -> list[dict[str, Any]]:
        """
        Get error history for document.

        Args:
            doc_id: Document ID

        Returns:
            List of error dictionaries (most recent first)
        """
        try:
            key = f"doc:errors:{doc_id}"
            errors = self.redis.lrange(key, 0, -1)
            return [json.loads(e) for e in errors]
        except Exception as e:
            logger.error(f"Failed to get errors for doc {doc_id}: {e}")
            return []

    def delete_errors(self, doc_id: int) -> None:
        """Delete error history from Redis (cleanup)"""
        try:
            key = f"doc:errors:{doc_id}"
            self.redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete errors for doc {doc_id}: {e}")

    # ==================== Rate Limiting ====================

    def check_rate_limit(
        self, key: str, max_calls: int, window_seconds: int = 60
    ) -> bool:
        """
        Check if rate limit is exceeded.

        Args:
            key: Rate limit key (e.g., "openai:tenant-1")
            max_calls: Maximum calls allowed in window
            window_seconds: Time window in seconds

        Returns:
            True if within limit, False if exceeded

        Example:
            if tracker.check_rate_limit("openai:tenant-1", max_calls=50, window_seconds=60):
                # Make API call
                pass
            else:
                # Wait or queue for later
                pass
        """
        try:
            full_key = f"ratelimit:{key}"
            count = self.redis.get(full_key)

            if count and int(count) >= max_calls:
                return False  # Rate limit exceeded

            # Increment counter and set expiry
            pipe = self.redis.pipeline()
            pipe.incr(full_key)
            pipe.expire(full_key, window_seconds)
            pipe.execute()

            return True
        except Exception as e:
            logger.error(f"Failed to check rate limit for {key}: {e}")
            return True  # Allow on error to avoid blocking

    def get_rate_limit_count(self, key: str) -> int:
        """Get current rate limit count"""
        try:
            full_key = f"ratelimit:{key}"
            count = self.redis.get(full_key)
            return int(count) if count else 0
        except Exception as e:
            logger.error(f"Failed to get rate limit count for {key}: {e}")
            return 0

    # ==================== Pub/Sub ====================

    def publish_update(self, doc_id: int, data: dict[str, Any]) -> None:
        """
        Publish real-time update for document.

        Args:
            doc_id: Document ID
            data: Update data to publish

        Example:
            tracker.publish_update(123, {
                "stage": "embedding",
                "progress": 65.5,
                "status": "in_progress"
            })
        """
        try:
            channel = f"doc:updates:{doc_id}"
            self.redis.publish(channel, json.dumps(data))
            logger.debug(f"Published update for doc {doc_id}")
        except Exception as e:
            logger.error(f"Failed to publish update for doc {doc_id}: {e}")

    def publish_batch_update(self, batch_id: int, data: dict[str, Any]) -> None:
        """
        Publish real-time update for batch.

        Args:
            batch_id: Batch ID
            data: Update data to publish
        """
        try:
            channel = f"batch:updates:{batch_id}"
            self.redis.publish(channel, json.dumps(data))
            logger.debug(f"Published batch update for batch {batch_id}")
        except Exception as e:
            logger.error(f"Failed to publish batch update for batch {batch_id}: {e}")

    # ==================== Cleanup ====================

    def cleanup_document(self, doc_id: int) -> None:
        """
        Clean up all Redis data for a document.
        Useful when document is deleted or processing is complete.

        Args:
            doc_id: Document ID
        """
        try:
            self.delete_document_status(doc_id)
            self.delete_stage_tracking(doc_id)
            self.delete_errors(doc_id)
            self.remove_active_job(doc_id)
            logger.info(f"Cleaned up Redis data for doc {doc_id}")
        except Exception as e:
            logger.error(f"Failed to cleanup doc {doc_id}: {e}")

    def cleanup_batch(self, batch_id: int) -> None:
        """
        Clean up all Redis data for a batch.

        Args:
            batch_id: Batch ID
        """
        try:
            self.delete_batch_progress(batch_id)
            logger.info(f"Cleaned up Redis data for batch {batch_id}")
        except Exception as e:
            logger.error(f"Failed to cleanup batch {batch_id}: {e}")

    # ==================== Health Check ====================

    def health_check(self) -> bool:
        """
        Check if Redis connection is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            self.redis.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


# Global instance (singleton pattern)
_redis_tracker_instance = None


def get_redis_tracker() -> RedisPipelineTracker:
    """
    Get global Redis tracker instance (singleton).

    Returns:
        RedisPipelineTracker instance

    Usage:
        from app.utils.redis_pipeline import get_redis_tracker

        tracker = get_redis_tracker()
        tracker.set_document_status(123, "embedding", "in_progress", 65.5)
    """
    global _redis_tracker_instance
    if _redis_tracker_instance is None:
        _redis_tracker_instance = RedisPipelineTracker()
    return _redis_tracker_instance

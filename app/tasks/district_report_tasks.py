"""Celery task for generating district analytics PDF reports.

POST /api/v1/district-reports enqueues this task on the `documents` queue.
It runs the report pipeline (retrieval + LLM writer + PDF render), uploads
the PDF to S3, and returns a metadata dict the status endpoint reads.
"""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.core.config import settings
from app.tasks.loop_utils import get_event_loop
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)


def _get_s3_manager() -> S3Manager:
    return S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


@celery_app.task(
    name="generate_district_report",
    bind=True,
    max_retries=3,
)
def generate_district_report_task(
    self,
    tenant_id: int,
    query_id: str,
    chatbot_config_id: int | None = None,
) -> dict:
    """Generate a district analytics report PDF and upload it to S3.

    Returns a dict with `tenant_id`, `query_id`, `filename`, `s3_key`,
    `report_id`, and `compiled_at` — read by the status/download endpoints.
    """
    try:
        loop = get_event_loop()
        return loop.run_until_complete(
            _generate_report_async(tenant_id, query_id, chatbot_config_id)
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "generate_district_report failed for tenant=%s query=%s: %s",
            tenant_id,
            query_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries)) from exc


async def _generate_report_async(
    tenant_id: int,
    query_id: str,
    chatbot_config_id: int | None,
) -> dict:
    # Import inside the async impl to avoid the tasks -> services import cycle.
    from app.services.district_report import district_report_service

    result = await district_report_service.generate_report(
        tenant_id=tenant_id,
        query_id=query_id,
        chatbot_config_id=chatbot_config_id,
    )

    pdf_bytes: bytes = result["pdf_bytes"]
    s3_key = (
        f"district-reports/{tenant_id}/{result['report_id']}/{result['filename']}"
    )

    s3 = _get_s3_manager()
    await s3.upload_file_object(pdf_bytes, s3_key)
    logger.info(
        "Uploaded district report %s for tenant %s to s3://%s/%s",
        result["report_id"],
        tenant_id,
        settings.S3_BUCKET_NAME,
        s3_key,
    )

    return {
        "tenant_id": tenant_id,
        "query_id": result["query_id"],
        "title": result["title"],
        "report_id": result["report_id"],
        "compiled_at": result["compiled_at"],
        "filename": result["filename"],
        "s3_key": s3_key,
    }

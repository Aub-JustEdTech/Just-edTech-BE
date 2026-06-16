"""
Celery tasks for token usage aggregation and monthly billing.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.celery_app import celery_app
from app.crud.daily_token_usage import daily_token_usage
from app.crud.monthly_billing import monthly_billing
from app.db.connector import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _get_event_loop():
    """Get or create event loop for the current thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


@celery_app.task(name="aggregate_daily_token_usage", bind=True)
def aggregate_daily_token_usage_task(self, target_date_str: str | None = None):
    """
    Celery task to aggregate token usage for a specific date.

    This task runs daily at 2 AM to aggregate the previous day's token usage.

    Args:
        target_date_str: Optional date string in ISO format (YYYY-MM-DD).
                        If not provided, aggregates for yesterday's date.
    """
    try:
        # Determine target date
        if target_date_str:
            target_date = date.fromisoformat(target_date_str)
        else:
            # Default to yesterday (since we run at 2 AM, we aggregate previous day)
            target_date = (datetime.utcnow() - timedelta(days=1)).date()

        logger.info(f"Starting token usage aggregation for date: {target_date}")

        # Get or create event loop for this worker
        loop = _get_event_loop()

        # Run async aggregation function
        result = loop.run_until_complete(_aggregate_daily_usage_async(target_date))

        logger.info(
            f"Token aggregation completed for {target_date}: "
            f"{result['total_records']} records "
            f"({result['records_created']} created, {result['records_updated']} updated), "
            f"{result['total_tokens_processed']} total tokens"
        )

        return result

    except Exception as exc:
        logger.error(
            f"Failed to aggregate token usage for {target_date_str or 'yesterday'}: {exc}",
            exc_info=True,
        )

        # Retry with exponential backoff (max 3 retries)
        raise self.retry(
            exc=exc, countdown=60 * (2**self.request.retries), max_retries=3
        ) from exc


async def _aggregate_daily_usage_async(target_date: date) -> dict:
    """
    Aggregate token usage data asynchronously for a specific date.

    Args:
        target_date: The date to aggregate data for

    Returns:
        Dictionary with aggregation results and statistics
    """
    async with AsyncSessionLocal() as db:
        try:
            # Use the CRUD method to aggregate messages into daily usage
            result = await daily_token_usage.aggregate_messages_to_daily_usage(
                db=db,
                target_date=target_date,
            )

            logger.info(f"Aggregation result for {target_date}: {result}")
            return result

        except Exception as e:
            logger.error(
                f"Error during aggregation for {target_date}: {e}", exc_info=True
            )
            await db.rollback()
            raise


@celery_app.task(name="backfill_daily_token_usage", bind=True)
def backfill_daily_token_usage_task(self, start_date_str: str, end_date_str: str):
    """
    Celery task to backfill token usage data for a date range.

    This is useful for populating historical data or fixing missing aggregations.

    Args:
        start_date_str: Start date in ISO format (YYYY-MM-DD)
        end_date_str: End date in ISO format (YYYY-MM-DD)
    """
    try:
        start_date = date.fromisoformat(start_date_str)
        end_date = date.fromisoformat(end_date_str)

        if start_date > end_date:
            raise ValueError("Start date must be before or equal to end date")

        logger.info(f"Starting token usage backfill from {start_date} to {end_date}")

        # Get or create event loop for this worker
        loop = _get_event_loop()

        # Run async backfill function
        result = loop.run_until_complete(_backfill_usage_async(start_date, end_date))

        logger.info(
            f"Backfill completed: {result['days_processed']} days processed, "
            f"{result['total_records_created']} records created, "
            f"{result['total_records_updated']} records updated"
        )

        return result

    except Exception as exc:
        logger.error(
            f"Failed to backfill token usage from {start_date_str} to {end_date_str}: {exc}",
            exc_info=True,
        )

        # Don't retry backfill tasks automatically (they can be triggered manually)
        raise


async def _backfill_usage_async(start_date: date, end_date: date) -> dict:
    """
    Backfill token usage data for a date range.

    Args:
        start_date: First date to backfill
        end_date: Last date to backfill

    Returns:
        Dictionary with backfill statistics
    """
    async with AsyncSessionLocal() as db:
        days_processed = 0
        total_records_created = 0
        total_records_updated = 0
        total_tokens_processed = 0
        errors = []

        current_date = start_date
        while current_date <= end_date:
            try:
                logger.info(f"Backfilling data for {current_date}")

                result = await daily_token_usage.aggregate_messages_to_daily_usage(
                    db=db,
                    target_date=current_date,
                )

                days_processed += 1
                total_records_created += result["records_created"]
                total_records_updated += result["records_updated"]
                total_tokens_processed += result["total_tokens_processed"]

            except Exception as e:
                error_msg = f"Error backfilling {current_date}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)

            # Move to next day
            current_date += timedelta(days=1)

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days_processed": days_processed,
            "total_records_created": total_records_created,
            "total_records_updated": total_records_updated,
            "total_tokens_processed": total_tokens_processed,
            "errors": errors,
        }


@celery_app.task(name="aggregate_monthly_billing", bind=True)
def aggregate_monthly_billing_task(
    self, year: int | None = None, month: int | None = None
):
    """
    Celery task to aggregate monthly billing for a specific month.

    This task runs at the end of each month (or can be run manually) to calculate
    the monthly bill per tenant per model.

    Args:
        year: Billing year. If not provided, uses previous month.
        month: Billing month (1-12). If not provided, uses previous month.
    """
    try:
        # Determine target month
        if year is None or month is None:
            # Default to previous month
            today = datetime.utcnow().date()
            if today.month == 1:
                target_year = today.year - 1
                target_month = 12
            else:
                target_year = today.year
                target_month = today.month - 1
        else:
            target_year = year
            target_month = month

        logger.info(
            f"Starting monthly billing aggregation for {target_year}-{target_month:02d}"
        )

        # Get or create event loop for this worker
        loop = _get_event_loop()

        # Run async aggregation function
        result = loop.run_until_complete(
            _aggregate_monthly_billing_async(target_year, target_month)
        )

        logger.info(
            f"Monthly billing aggregation completed for {target_year}-{target_month:02d}: "
            f"{result['total_records']} records "
            f"({result['records_created']} created, {result['records_updated']} updated), "
            f"total cost: ${result['total_monthly_cost']:.2f}"
        )

        return result

    except Exception as exc:
        logger.error(
            f"Failed to aggregate monthly billing for {year or 'auto'}-{month or 'auto'}: {exc}",
            exc_info=True,
        )

        # Retry with exponential backoff (max 3 retries)
        raise self.retry(
            exc=exc, countdown=60 * (2**self.request.retries), max_retries=3
        ) from exc


async def _aggregate_monthly_billing_async(year: int, month: int) -> dict:
    """
    Aggregate monthly billing data asynchronously for a specific month.

    Args:
        year: Billing year
        month: Billing month (1-12)

    Returns:
        Dictionary with aggregation results and statistics
    """
    async with AsyncSessionLocal() as db:
        try:
            # Use the CRUD method to aggregate daily usage into monthly billing
            result = await monthly_billing.aggregate_monthly_billing(
                db=db,
                year=year,
                month=month,
            )

            logger.info(
                f"Monthly billing aggregation result for {year}-{month:02d}: {result}"
            )
            return result

        except Exception as e:
            logger.error(
                f"Error during monthly billing aggregation for {year}-{month:02d}: {e}",
                exc_info=True,
            )
            await db.rollback()
            raise

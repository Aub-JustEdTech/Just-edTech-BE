"""
CRUD operations for DailyTokenUsage model.
"""

import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_token_usage import DailyTokenUsage
from app.models.llm_models import LLMModel


def _normalize_model_name(model_name: str) -> str:
    """Normalize model name by removing version suffixes."""
    if not model_name or model_name == "unknown":
        return model_name
    normalized = re.sub(r"-?\d{4}-?\d{2}-?\d{2}", "", model_name)
    normalized = re.sub(r"-\d{4}$", "", normalized)
    return normalized.rstrip("-")


class DailyTokenUsageCRUD:
    """CRUD operations for DailyTokenUsage model"""

    async def create_or_update_daily_usage(
        self,
        db: AsyncSession,
        tenant_id: int,
        model_name: str,
        usage_date: date,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        message_count: int = 1,
    ) -> DailyTokenUsage:
        """
        Create or update daily token usage record.
        If a record exists for the same tenant, model, and date, it will be updated.
        Otherwise, a new record will be created.

        Note: Cost calculation is done during aggregation, not in real-time,
        to avoid performance overhead on every message.
        """
        # Check if record exists
        query = select(DailyTokenUsage).where(
            and_(
                DailyTokenUsage.tenant_id == tenant_id,
                DailyTokenUsage.model_name == model_name,
                DailyTokenUsage.usage_date == usage_date,
            )
        )
        result = await db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing record - add to existing values
            existing.total_input_tokens += input_tokens
            existing.total_output_tokens += output_tokens
            existing.total_tokens += total_tokens
            existing.message_count += message_count
            existing.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(existing)
            return existing
        else:
            # Create new record (costs will be calculated during aggregation)
            new_usage = DailyTokenUsage(
                tenant_id=tenant_id,
                model_name=model_name,
                usage_date=usage_date,
                total_input_tokens=input_tokens,
                total_output_tokens=output_tokens,
                total_tokens=total_tokens,
                total_cache_tokens=0,
                message_count=message_count,
            )
            db.add(new_usage)
            await db.commit()
            await db.refresh(new_usage)
            return new_usage

    async def get_daily_usage_by_tenant(
        self,
        db: AsyncSession,
        tenant_id: int,
        start_date: date,
        end_date: date,
        model_name: str | None = None,
    ) -> list[DailyTokenUsage]:
        """
        Get daily token usage for a tenant within a date range.
        Optionally filter by model_name.
        """
        query = select(DailyTokenUsage).where(
            and_(
                DailyTokenUsage.tenant_id == tenant_id,
                DailyTokenUsage.usage_date >= start_date,
                DailyTokenUsage.usage_date <= end_date,
            )
        )

        if model_name:
            query = query.where(DailyTokenUsage.model_name == model_name)

        query = query.order_by(DailyTokenUsage.usage_date.desc())

        result = await db.execute(query)
        return result.scalars().all()

    async def get_daily_usage_by_date(
        self,
        db: AsyncSession,
        usage_date: date,
        tenant_id: int | None = None,
    ) -> list[DailyTokenUsage]:
        """
        Get all daily token usage records for a specific date.
        Optionally filter by tenant_id.
        """
        query = select(DailyTokenUsage).where(DailyTokenUsage.usage_date == usage_date)

        if tenant_id:
            query = query.where(DailyTokenUsage.tenant_id == tenant_id)

        query = query.order_by(DailyTokenUsage.tenant_id, DailyTokenUsage.model_name)

        result = await db.execute(query)
        return result.scalars().all()

    async def get_usage_summary(
        self,
        db: AsyncSession,
        tenant_id: int,
        start_date: date,
        end_date: date,
    ) -> dict:
        """
        Get aggregated usage summary for a tenant within a date range.
        Returns total tokens by model and overall totals.
        """
        query = (
            select(
                DailyTokenUsage.model_name,
                func.sum(DailyTokenUsage.total_input_tokens).label("total_input"),
                func.sum(DailyTokenUsage.total_output_tokens).label("total_output"),
                func.sum(DailyTokenUsage.total_tokens).label("total"),
                func.sum(DailyTokenUsage.message_count).label("messages"),
            )
            .where(
                and_(
                    DailyTokenUsage.tenant_id == tenant_id,
                    DailyTokenUsage.usage_date >= start_date,
                    DailyTokenUsage.usage_date <= end_date,
                )
            )
            .group_by(DailyTokenUsage.model_name)
        )

        result = await db.execute(query)
        rows = result.all()

        by_model = {}
        total_input = 0
        total_output = 0
        total_tokens = 0
        total_messages = 0

        for row in rows:
            by_model[row.model_name] = {
                "input_tokens": row.total_input or 0,
                "output_tokens": row.total_output or 0,
                "total_tokens": row.total or 0,
                "message_count": row.messages or 0,
            }
            total_input += row.total_input or 0
            total_output += row.total_output or 0
            total_tokens += row.total or 0
            total_messages += row.messages or 0

        return {
            "by_model": by_model,
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_tokens,
                "message_count": total_messages,
            },
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        }

    async def aggregate_messages_to_daily_usage(
        self,
        db: AsyncSession,
        target_date: date,
    ) -> dict:
        """
        Aggregate message token data for a specific date into daily_token_usage table.
        This processes all messages from the target date that haven't been aggregated yet.
        Also calculates costs based on model pricing.

        Returns a summary of what was aggregated.
        """
        from app.models.conversations import Conversation, Message

        # Define the start and end of the target date
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())

        # Query to aggregate messages by tenant and model for the target date
        # Only process assistant messages (which have token tracking)
        query = (
            select(
                Conversation.tenant_id,
                Message.model_used,
                func.sum(Message.input_tokens).label("total_input"),
                func.sum(Message.output_tokens).label("total_output"),
                func.sum(Message.total_tokens).label("total"),
                func.count(Message.id).label("message_count"),
            )
            .join(Message.conversation)
            .where(
                and_(
                    Message.role == "assistant",
                    Message.created_at >= start_datetime,
                    Message.created_at < end_datetime,
                    Message.model_used.isnot(None),
                    Message.total_tokens.isnot(None),
                )
            )
            .group_by(Conversation.tenant_id, Message.model_used)
        )

        result = await db.execute(query)
        aggregates = result.all()

        records_created = 0
        records_updated = 0
        total_tokens_processed = 0
        total_cost_calculated = Decimal("0.00")

        for agg in aggregates:
            tenant_id = agg.tenant_id
            model_name = agg.model_used or "unknown"
            input_tokens = agg.total_input or 0
            output_tokens = agg.total_output or 0
            total_tokens = agg.total or 0
            message_count = agg.message_count or 0

            # Normalize model name for pricing lookup
            normalized_model = _normalize_model_name(model_name)

            # Get model pricing (Global lookup)
            pricing_query = select(LLMModel).where(LLMModel.name == normalized_model)
            pricing_result = await db.execute(pricing_query)
            model_pricing = pricing_result.scalar_one_or_none()

            # Calculate costs (prices are per 1M tokens)
            input_cost = Decimal("0.00")
            output_cost = Decimal("0.00")
            cache_cost = Decimal("0.00")

            if model_pricing:
                if model_pricing.input_token_price:
                    input_cost = (
                        Decimal(str(input_tokens)) / Decimal("1000000")
                    ) * model_pricing.input_token_price
                if model_pricing.output_token_price:
                    output_cost = (
                        Decimal(str(output_tokens)) / Decimal("1000000")
                    ) * model_pricing.output_token_price
                # Cache tokens calculation can be added here if tracked separately

            total_cost = input_cost + output_cost + cache_cost

            # Check if record exists for this combination
            check_query = select(DailyTokenUsage).where(
                and_(
                    DailyTokenUsage.tenant_id == tenant_id,
                    DailyTokenUsage.model_name == model_name,
                    DailyTokenUsage.usage_date == target_date,
                )
            )
            check_result = await db.execute(check_query)
            existing = check_result.scalar_one_or_none()

            if existing:
                # Record exists - this means we're re-running aggregation
                # Update with the new aggregated values (replace, not add)
                existing.total_input_tokens = input_tokens
                existing.total_output_tokens = output_tokens
                existing.total_tokens = total_tokens
                existing.total_cache_tokens = 0  # Can be updated if tracked
                existing.message_count = message_count
                existing.input_token_cost = input_cost
                existing.output_token_cost = output_cost
                existing.cache_token_cost = cache_cost
                existing.total_cost = total_cost
                existing.updated_at = datetime.utcnow()
                records_updated += 1
            else:
                # Create new record
                new_usage = DailyTokenUsage(
                    tenant_id=tenant_id,
                    model_name=model_name,
                    usage_date=target_date,
                    total_input_tokens=input_tokens,
                    total_output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    total_cache_tokens=0,  # Can be updated if tracked
                    message_count=message_count,
                    input_token_cost=input_cost,
                    output_token_cost=output_cost,
                    cache_token_cost=cache_cost,
                    total_cost=total_cost,
                )
                db.add(new_usage)
                records_created += 1

            total_tokens_processed += total_tokens
            total_cost_calculated += total_cost

        await db.commit()

        return {
            "target_date": target_date.isoformat(),
            "records_created": records_created,
            "records_updated": records_updated,
            "total_records": records_created + records_updated,
            "total_tokens_processed": total_tokens_processed,
            "total_cost_calculated": float(total_cost_calculated),
        }

    async def delete_daily_usage(
        self,
        db: AsyncSession,
        tenant_id: int,
        usage_date: date,
        model_name: str | None = None,
    ) -> int:
        """
        Delete daily usage records for a tenant and date.
        Optionally filter by model_name.
        Returns the number of records deleted.
        """
        query = select(DailyTokenUsage).where(
            and_(
                DailyTokenUsage.tenant_id == tenant_id,
                DailyTokenUsage.usage_date == usage_date,
            )
        )

        if model_name:
            query = query.where(DailyTokenUsage.model_name == model_name)

        result = await db.execute(query)
        records = result.scalars().all()

        count = len(records)
        for record in records:
            db.delete(record)

        await db.commit()
        return count


# Singleton instance
daily_token_usage = DailyTokenUsageCRUD()

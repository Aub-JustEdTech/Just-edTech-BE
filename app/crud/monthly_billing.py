"""
CRUD operations for MonthlyBilling model.
"""

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_token_usage import DailyTokenUsage
from app.models.monthly_billing import MonthlyBilling


class MonthlyBillingCRUD:
    """CRUD operations for MonthlyBilling model"""

    async def get_monthly_billing_by_tenant(
        self,
        db: AsyncSession,
        tenant_id: int,
        year: int,
        month: int,
        model_name: str | None = None,
    ) -> list[MonthlyBilling]:
        """
        Get monthly billing records for a tenant for a specific month.
        Optionally filter by model_name.
        """
        query = select(MonthlyBilling).where(
            and_(
                MonthlyBilling.tenant_id == tenant_id,
                MonthlyBilling.billing_year == year,
                MonthlyBilling.billing_month == month,
            )
        )

        if model_name:
            query = query.where(MonthlyBilling.model_name == model_name)

        query = query.order_by(MonthlyBilling.model_name)

        result = await db.execute(query)
        return result.scalars().all()

    async def get_billing_summary_by_tenant(
        self,
        db: AsyncSession,
        tenant_id: int,
        year: int,
        month: int,
    ) -> dict:
        """
        Get aggregated billing summary for a tenant for a specific month.
        Returns total costs by model and overall totals.
        """
        query = (
            select(
                MonthlyBilling.model_name,
                MonthlyBilling.total_input_tokens,
                MonthlyBilling.total_output_tokens,
                MonthlyBilling.total_cache_tokens,
                MonthlyBilling.total_tokens,
                MonthlyBilling.message_count,
                MonthlyBilling.input_token_cost,
                MonthlyBilling.output_token_cost,
                MonthlyBilling.cache_token_cost,
                MonthlyBilling.total_cost,
            )
            .where(
                and_(
                    MonthlyBilling.tenant_id == tenant_id,
                    MonthlyBilling.billing_year == year,
                    MonthlyBilling.billing_month == month,
                )
            )
            .order_by(MonthlyBilling.total_cost.desc())
        )

        result = await db.execute(query)
        rows = result.all()

        by_model = {}
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_tokens = 0
        total_tokens = 0
        total_messages = 0
        total_input_cost = Decimal("0.00")
        total_output_cost = Decimal("0.00")
        total_cache_cost = Decimal("0.00")
        total_cost = Decimal("0.00")

        for row in rows:
            by_model[row.model_name] = {
                "input_tokens": row.total_input_tokens or 0,
                "output_tokens": row.total_output_tokens or 0,
                "cache_tokens": row.total_cache_tokens or 0,
                "total_tokens": row.total_tokens or 0,
                "message_count": row.message_count or 0,
                "input_cost": float(row.input_token_cost or 0),
                "output_cost": float(row.output_token_cost or 0),
                "cache_cost": float(row.cache_token_cost or 0),
                "total_cost": float(row.total_cost or 0),
            }
            total_input_tokens += row.total_input_tokens or 0
            total_output_tokens += row.total_output_tokens or 0
            total_cache_tokens += row.total_cache_tokens or 0
            total_tokens += row.total_tokens or 0
            total_messages += row.message_count or 0
            total_input_cost += row.input_token_cost or Decimal("0.00")
            total_output_cost += row.output_token_cost or Decimal("0.00")
            total_cache_cost += row.cache_token_cost or Decimal("0.00")
            total_cost += row.total_cost or Decimal("0.00")

        return {
            "by_model": by_model,
            "totals": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cache_tokens": total_cache_tokens,
                "total_tokens": total_tokens,
                "message_count": total_messages,
                "input_cost": float(total_input_cost),
                "output_cost": float(total_output_cost),
                "cache_cost": float(total_cache_cost),
                "total_cost": float(total_cost),
            },
            "billing_period": {
                "year": year,
                "month": month,
            },
        }

    async def aggregate_monthly_billing(
        self,
        db: AsyncSession,
        year: int,
        month: int,
    ) -> dict:
        """
        Aggregate daily token usage data into monthly billing records.
        This should be run at the end of each month to calculate the monthly bill.

        Args:
            db: Database session
            year: Billing year
            month: Billing month (1-12)

        Returns:
            Dictionary with aggregation statistics
        """
        # Calculate date range for the month
        first_day = date(year, month, 1)
        last_day_num = monthrange(year, month)[1]
        last_day = date(year, month, last_day_num)

        # Query to aggregate daily usage by tenant and model for the month
        query = (
            select(
                DailyTokenUsage.tenant_id,
                DailyTokenUsage.model_name,
                func.sum(DailyTokenUsage.total_input_tokens).label("total_input"),
                func.sum(DailyTokenUsage.total_output_tokens).label("total_output"),
                func.sum(DailyTokenUsage.total_tokens).label("total"),
                func.sum(DailyTokenUsage.total_cache_tokens).label("total_cache"),
                func.sum(DailyTokenUsage.message_count).label("messages"),
                func.sum(DailyTokenUsage.input_token_cost).label("input_cost"),
                func.sum(DailyTokenUsage.output_token_cost).label("output_cost"),
                func.sum(DailyTokenUsage.cache_token_cost).label("cache_cost"),
                func.sum(DailyTokenUsage.total_cost).label("total_cost"),
                func.avg(
                    DailyTokenUsage.input_token_cost
                    / func.nullif(DailyTokenUsage.total_input_tokens, 0)
                    * 1000000
                ).label("avg_input_price"),
                func.avg(
                    DailyTokenUsage.output_token_cost
                    / func.nullif(DailyTokenUsage.total_output_tokens, 0)
                    * 1000000
                ).label("avg_output_price"),
                func.avg(
                    DailyTokenUsage.cache_token_cost
                    / func.nullif(DailyTokenUsage.total_cache_tokens, 0)
                    * 1000000
                ).label("avg_cache_price"),
            )
            .where(
                and_(
                    DailyTokenUsage.usage_date >= first_day,
                    DailyTokenUsage.usage_date <= last_day,
                )
            )
            .group_by(DailyTokenUsage.tenant_id, DailyTokenUsage.model_name)
        )

        result = await db.execute(query)
        aggregates = result.all()

        records_created = 0
        records_updated = 0
        total_monthly_cost = Decimal("0.00")

        for agg in aggregates:
            tenant_id = agg.tenant_id
            model_name = agg.model_name
            total_input_tokens = agg.total_input or 0
            total_output_tokens = agg.total_output or 0
            total_tokens = agg.total or 0
            total_cache_tokens = agg.total_cache or 0
            message_count = agg.messages or 0
            input_cost = agg.input_cost or Decimal("0.00")
            output_cost = agg.output_cost or Decimal("0.00")
            cache_cost = agg.cache_cost or Decimal("0.00")
            total_cost = agg.total_cost or Decimal("0.00")
            avg_input_price = agg.avg_input_price
            avg_output_price = agg.avg_output_price
            avg_cache_price = agg.avg_cache_price

            # Check if record exists
            check_query = select(MonthlyBilling).where(
                and_(
                    MonthlyBilling.tenant_id == tenant_id,
                    MonthlyBilling.model_name == model_name,
                    MonthlyBilling.billing_year == year,
                    MonthlyBilling.billing_month == month,
                )
            )
            check_result = await db.execute(check_query)
            existing = check_result.scalar_one_or_none()

            if existing:
                # Update existing record
                existing.total_input_tokens = total_input_tokens
                existing.total_output_tokens = total_output_tokens
                existing.total_tokens = total_tokens
                existing.total_cache_tokens = total_cache_tokens
                existing.message_count = message_count
                existing.input_token_cost = input_cost
                existing.output_token_cost = output_cost
                existing.cache_token_cost = cache_cost
                existing.total_cost = total_cost
                existing.avg_input_token_price = avg_input_price
                existing.avg_output_token_price = avg_output_price
                existing.avg_cache_token_price = avg_cache_price
                existing.period_start_date = first_day
                existing.period_end_date = last_day
                existing.updated_at = datetime.utcnow()
                records_updated += 1
            else:
                # Create new record
                new_billing = MonthlyBilling(
                    tenant_id=tenant_id,
                    model_name=model_name,
                    billing_year=year,
                    billing_month=month,
                    period_start_date=first_day,
                    period_end_date=last_day,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    total_tokens=total_tokens,
                    total_cache_tokens=total_cache_tokens,
                    message_count=message_count,
                    input_token_cost=input_cost,
                    output_token_cost=output_cost,
                    cache_token_cost=cache_cost,
                    total_cost=total_cost,
                    avg_input_token_price=avg_input_price,
                    avg_output_token_price=avg_output_price,
                    avg_cache_token_price=avg_cache_price,
                )
                db.add(new_billing)
                records_created += 1

            total_monthly_cost += total_cost

        await db.commit()

        return {
            "billing_year": year,
            "billing_month": month,
            "period_start": first_day.isoformat(),
            "period_end": last_day.isoformat(),
            "records_created": records_created,
            "records_updated": records_updated,
            "total_records": records_created + records_updated,
            "total_monthly_cost": float(total_monthly_cost),
        }

    async def get_yearly_billing_summary(
        self,
        db: AsyncSession,
        tenant_id: int,
        year: int,
    ) -> dict:
        """
        Get yearly billing summary for a tenant.
        Returns monthly breakdown and yearly totals.
        """
        query = (
            select(
                MonthlyBilling.billing_month,
                func.sum(MonthlyBilling.total_tokens).label("total_tokens"),
                func.sum(MonthlyBilling.total_cost).label("total_cost"),
            )
            .where(
                and_(
                    MonthlyBilling.tenant_id == tenant_id,
                    MonthlyBilling.billing_year == year,
                )
            )
            .group_by(MonthlyBilling.billing_month)
            .order_by(MonthlyBilling.billing_month)
        )

        result = await db.execute(query)
        rows = result.all()

        monthly_breakdown = {}
        yearly_total_tokens = 0
        yearly_total_cost = Decimal("0.00")

        for row in rows:
            monthly_breakdown[row.billing_month] = {
                "month": row.billing_month,
                "total_tokens": row.total_tokens or 0,
                "total_cost": float(row.total_cost or 0),
            }
            yearly_total_tokens += row.total_tokens or 0
            yearly_total_cost += row.total_cost or Decimal("0.00")

        return {
            "tenant_id": tenant_id,
            "year": year,
            "monthly_breakdown": monthly_breakdown,
            "yearly_totals": {
                "total_tokens": yearly_total_tokens,
                "total_cost": float(yearly_total_cost),
            },
        }

    async def delete_monthly_billing(
        self,
        db: AsyncSession,
        tenant_id: int,
        year: int,
        month: int,
        model_name: str | None = None,
    ) -> int:
        """
        Delete monthly billing records for a tenant and month.
        Optionally filter by model_name.
        Returns the number of records deleted.
        """
        query = select(MonthlyBilling).where(
            and_(
                MonthlyBilling.tenant_id == tenant_id,
                MonthlyBilling.billing_year == year,
                MonthlyBilling.billing_month == month,
            )
        )

        if model_name:
            query = query.where(MonthlyBilling.model_name == model_name)

        result = await db.execute(query)
        records = result.scalars().all()

        count = len(records)
        for record in records:
            db.delete(record)

        await db.commit()
        return count


# Singleton instance
monthly_billing = MonthlyBillingCRUD()

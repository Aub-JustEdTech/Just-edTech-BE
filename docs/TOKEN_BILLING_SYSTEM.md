# Token Billing and Metering System

## Overview

This document describes the comprehensive token billing and metering system implemented in the Just-EdTech application. The system tracks token usage, calculates costs based on model pricing, and generates monthly billing reports.

## Architecture

The billing system consists of three main layers:

1. **Daily Token Aggregation** - Aggregates token usage from individual messages into daily summaries
2. **Cost Calculation** - Calculates costs based on model pricing (per 1M tokens)
3. **Monthly Billing** - Aggregates daily data into monthly billing reports

## Database Schema

### LLM Models Table (`llm_models`)

New pricing columns added:
- `input_token_price` (DECIMAL 10,6) - Price per 1M input tokens (USD)
- `output_token_price` (DECIMAL 10,6) - Price per 1M output tokens (USD)
- `cache_token_price` (DECIMAL 10,6) - Price per 1M cache tokens (USD)

### Daily Token Usage Table (`daily_token_usage`)

New columns added:
- `total_cache_tokens` (BIGINT) - Total cache tokens used
- `input_token_cost` (DECIMAL 12,6) - Cost for input tokens (USD)
- `output_token_cost` (DECIMAL 12,6) - Cost for output tokens (USD)
- `cache_token_cost` (DECIMAL 12,6) - Cost for cache tokens (USD)
- `total_cost` (DECIMAL 12,6) - Total cost (USD)

### Monthly Billing Table (`monthly_billing`)

New table for monthly billing aggregations:
- `billing_year`, `billing_month` - Billing period
- `period_start_date`, `period_end_date` - Date range
- `tenant_id`, `model_name` - Tenant and model identification
- `total_input_tokens`, `total_output_tokens`, `total_cache_tokens`, `total_tokens` - Token counts
- `message_count` - Number of messages processed
- `input_token_cost`, `output_token_cost`, `cache_token_cost`, `total_cost` - Cost breakdown
- `avg_input_token_price`, `avg_output_token_price`, `avg_cache_token_price` - Average pricing

## Automated Tasks

### Daily Token Aggregation

**Schedule:** Daily at 2:00 AM UTC  
**Task:** `aggregate_daily_token_usage`  
**Celery Task Name:** `aggregate_daily_token_usage`

This task:
1. Aggregates token usage from messages created the previous day
2. Groups by tenant and model
3. Calculates costs using model pricing from `llm_models` table
4. Stores results in `daily_token_usage` table

### Monthly Billing Aggregation

**Schedule:** 1st of each month at 3:00 AM UTC  
**Task:** `aggregate_monthly_billing`  
**Celery Task Name:** `aggregate_monthly_billing`

This task:
1. Aggregates daily token usage for the previous month
2. Groups by tenant and model
3. Calculates total costs and average pricing
4. Stores results in `monthly_billing` table

## API Endpoints

### Token Usage Endpoints

#### Get Daily Token Usage
```
GET /api/token-usage/daily/{tenant_id}
```
Query parameters:
- `start_date` - Start date (YYYY-MM-DD)
- `end_date` - End date (YYYY-MM-DD)
- `model_name` - Optional filter by model

#### Get Usage Summary
```
GET /api/token-usage/summary/{tenant_id}
```
Returns aggregated token usage and costs by model.

#### Trigger Daily Aggregation
```
POST /api/token-usage/aggregate
```
Manually trigger daily token aggregation for a specific date.

### Monthly Billing Endpoints

#### Get Monthly Billing Records
```
GET /api/billing/monthly-billing/{tenant_id}
```
Query parameters:
- `year` - Billing year
- `month` - Billing month (1-12)
- `model_name` - Optional filter by model

Returns detailed monthly billing records.

#### Get Monthly Billing Summary
```
GET /api/billing/monthly-billing/{tenant_id}/summary
```
Query parameters:
- `year` - Billing year
- `month` - Billing month (1-12)

Returns aggregated billing summary with breakdown by model:
```json
{
  "by_model": {
    "gpt-4": {
      "input_tokens": 1000000,
      "output_tokens": 500000,
      "total_tokens": 1500000,
      "input_cost": 30.00,
      "output_cost": 60.00,
      "total_cost": 90.00
    }
  },
  "totals": {
    "input_tokens": 1000000,
    "output_tokens": 500000,
    "total_tokens": 1500000,
    "total_cost": 90.00
  },
  "billing_period": {
    "year": 2024,
    "month": 10
  }
}
```

#### Get Yearly Billing Summary
```
GET /api/billing/yearly-billing/{tenant_id}
```
Query parameters:
- `year` - Billing year

Returns monthly breakdown and yearly totals:
```json
{
  "tenant_id": 1,
  "year": 2024,
  "monthly_breakdown": {
    "1": {"month": 1, "total_tokens": 5000000, "total_cost": 150.00},
    "2": {"month": 2, "total_tokens": 6000000, "total_cost": 180.00}
  },
  "yearly_totals": {
    "total_tokens": 11000000,
    "total_cost": 330.00
  }
}
```

#### Trigger Monthly Billing Aggregation
```
POST /api/billing/monthly-billing/aggregate
```
Request body:
```json
{
  "year": 2024,  // optional, defaults to previous month
  "month": 10    // optional, defaults to previous month
}
```

Returns task ID for tracking:
```json
{
  "task_id": "abc123...",
  "status": "Task submitted successfully"
}
```

#### Check Aggregation Task Status
```
GET /api/billing/monthly-billing/task/{task_id}
```

Returns task status and result.

## Setting Up Model Pricing

To enable cost calculation, you need to set pricing for each model in the `llm_models` table:

```sql
-- Example: Set pricing for GPT-4
UPDATE llm_models 
SET 
  input_token_price = 30.00,   -- $30 per 1M input tokens
  output_token_price = 60.00,  -- $60 per 1M output tokens
  cache_token_price = 15.00    -- $15 per 1M cache tokens
WHERE name = 'gpt-4' AND tenant_id = 1;

-- Example: Set pricing for GPT-3.5-turbo
UPDATE llm_models 
SET 
  input_token_price = 0.50,    -- $0.50 per 1M input tokens
  output_token_price = 1.50,   -- $1.50 per 1M output tokens
  cache_token_price = 0.25     -- $0.25 per 1M cache tokens
WHERE name = 'gpt-3.5-turbo' AND tenant_id = 1;
```

## Running Migrations

To apply the new database schema:

```bash
cd /home/aubergine/Desktop/Just-EdTech-BE

# Run migrations
python3 -m alembic upgrade head
```

## Manual Task Execution

### Aggregate Daily Usage for a Specific Date

```python
from app.tasks.token_aggregation_tasks import aggregate_daily_token_usage_task

# Aggregate for a specific date
task = aggregate_daily_token_usage_task.apply_async(
    kwargs={"target_date_str": "2024-10-15"}
)
```

### Backfill Historical Data

```python
from app.tasks.token_aggregation_tasks import backfill_daily_token_usage_task

# Backfill date range
task = backfill_daily_token_usage_task.apply_async(
    kwargs={
        "start_date_str": "2024-01-01",
        "end_date_str": "2024-10-31"
    }
)
```

### Generate Monthly Bill

```python
from app.tasks.token_aggregation_tasks import aggregate_monthly_billing_task

# Generate bill for specific month
task = aggregate_monthly_billing_task.apply_async(
    kwargs={"year": 2024, "month": 10}
)

# Generate bill for previous month (automatic)
task = aggregate_monthly_billing_task.apply_async()
```

## Cost Calculation Formula

The cost calculation is performed as follows:

```
Input Cost = (Total Input Tokens / 1,000,000) × Input Token Price
Output Cost = (Total Output Tokens / 1,000,000) × Output Token Price
Cache Cost = (Total Cache Tokens / 1,000,000) × Cache Token Price
Total Cost = Input Cost + Output Cost + Cache Cost
```

## Monitoring and Alerts

### Key Metrics to Monitor

1. **Daily aggregation success rate** - Ensure daily tasks complete successfully
2. **Monthly billing generation** - Verify monthly bills are generated on time
3. **Cost anomalies** - Monitor for unexpected cost spikes
4. **Missing pricing data** - Alert when models don't have pricing configured

### Celery Beat Status

Check that Celery Beat is running and tasks are scheduled:

```bash
# View scheduled tasks
celery -A app.celery_app inspect scheduled

# Check active tasks
celery -A app.celery_app inspect active

# View beat schedule
celery -A app.celery_app inspect scheduled
```

## Troubleshooting

### Daily Aggregation Not Running

1. Check Celery Beat is running:
   ```bash
   ps aux | grep celery
   ```

2. Check Redis connection:
   ```bash
   redis-cli ping
   ```

3. Review Celery logs:
   ```bash
   tail -f celery.log
   ```

### Missing Cost Data

If costs are showing as 0 or NULL:

1. Verify model pricing is configured in `llm_models` table
2. Check that model names match exactly between `messages` and `llm_models`
3. Ensure the daily aggregation task ran successfully

### Incorrect Billing Amounts

1. Check model pricing values are correct (they should be per 1M tokens, not per token)
2. Verify daily token usage records have correct token counts
3. Re-run the monthly billing aggregation for the affected month

## Performance Considerations

### Database Indexes

The system includes optimized indexes:
- `idx_daily_usage_lookup` on (tenant_id, usage_date, model_name)
- `idx_monthly_billing_lookup` on (tenant_id, billing_year, billing_month)

### Query Performance

For large datasets:
- Daily aggregations use efficient GROUP BY queries
- Monthly aggregations sum pre-aggregated daily data
- Pagination is recommended for API responses with large date ranges

## Future Enhancements

Potential improvements:
1. **Real-time cost tracking** - Update costs in real-time instead of daily batches
2. **Budget alerts** - Notify tenants when approaching cost thresholds
3. **Cost forecasting** - Predict monthly costs based on usage trends
4. **Detailed cost breakdown** - Track costs per conversation or user
5. **Invoice generation** - Automatically generate PDF invoices
6. **Payment integration** - Integrate with payment processors

## Support

For questions or issues with the billing system:
1. Check the logs in `/var/log/celery/`
2. Review the API documentation at `/docs`
3. Contact the development team


#!/usr/bin/env python3
"""
Script to setup model pricing for billing calculations.

Usage:
    python scripts/setup_model_pricing.py

This script sets up common model pricing based on current OpenAI pricing.
Update the PRICING dictionary with your actual pricing.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db.connector import AsyncSessionLocal
from app.models.llm_models import LLMModel

sys.path.insert(0, str(Path(__file__).parent.parent))

# Pricing per 1M tokens in USD (update these values as needed)
# Source: https://openai.com/pricing
PRICING = {
    "gpt-4o": {
        "input": Decimal("2.50"),
        "output": Decimal("10.00"),
        "cache": Decimal("1.25"),
    },
    "gpt-4o-mini": {
        "input": Decimal("0.150"),
        "output": Decimal("0.600"),
        "cache": Decimal("0.075"),
    },
    # GPT-4 models
    "gpt-4": {
        "input": Decimal("30.00"),
        "output": Decimal("60.00"),
        "cache": Decimal("15.00"),
    },
    "gpt-4-turbo": {
        "input": Decimal("10.00"),
        "output": Decimal("30.00"),
        "cache": Decimal("5.00"),
    },
    "gpt-4-turbo-preview": {
        "input": Decimal("10.00"),
        "output": Decimal("30.00"),
        "cache": Decimal("5.00"),
    },
    "gpt-3.5-turbo": {
        "input": Decimal("0.50"),
        "output": Decimal("1.50"),
        "cache": Decimal("0.25"),
    },
    "gpt-3.5-turbo-16k": {
        "input": Decimal("3.00"),
        "output": Decimal("4.00"),
        "cache": Decimal("1.50"),
    },
    # Claude models (Anthropic)
    "claude-3-opus": {
        "input": Decimal("15.00"),
        "output": Decimal("75.00"),
        "cache": Decimal("7.50"),
    },
    "claude-3-sonnet": {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
        "cache": Decimal("1.50"),
    },
    "claude-3-haiku": {
        "input": Decimal("0.25"),
        "output": Decimal("1.25"),
        "cache": Decimal("0.125"),
    },
}


async def update_model_pricing():
    """Update pricing for all models in the database."""
    async with AsyncSessionLocal() as db:
        try:
            # Get all models
            query = select(LLMModel)
            result = await db.execute(query)
            models = result.scalars().all()

            if not models:
                print("⚠️  No models found in database. Please create models first.")
                return

            print(f"Found {len(models)} models in database")
            print("-" * 60)

            updated_count = 0
            skipped_count = 0

            for model in models:
                model_name = model.name.lower()

                # Try to find pricing for this model
                pricing = None
                for price_key, price_data in PRICING.items():
                    if (
                        price_key.lower() in model_name
                        or model_name in price_key.lower()
                    ):
                        pricing = price_data
                        break

                if pricing:
                    # Update the model
                    model.input_token_price = pricing["input"]
                    model.output_token_price = pricing["output"]
                    model.cache_token_price = pricing["cache"]

                    print(f"✅ Updated {model.name} (Tenant {model.tenant_id}):")
                    print(f"   Input:  ${pricing['input']}/1M tokens")
                    print(f"   Output: ${pricing['output']}/1M tokens")
                    print(f"   Cache:  ${pricing['cache']}/1M tokens")

                    updated_count += 1
                else:
                    print(
                        f"⚠️  Skipped {model.name} (Tenant {model.tenant_id}) - No pricing found"
                    )
                    skipped_count += 1

                print()

            # Commit changes
            await db.commit()

            print("-" * 60)
            print(f"✅ Updated {updated_count} models")
            print(f"⚠️  Skipped {skipped_count} models (no pricing found)")

            if skipped_count > 0:
                print("\n💡 To add pricing for skipped models, update the PRICING")
                print("   dictionary in this script and run again.")

        except Exception as e:
            print(f"❌ Error updating pricing: {e}")
            await db.rollback()
            raise


async def show_current_pricing():
    """Show current pricing for all models."""
    async with AsyncSessionLocal() as db:
        try:
            query = select(LLMModel)
            result = await db.execute(query)
            models = result.scalars().all()

            if not models:
                print("⚠️  No models found in database.")
                return

            print("\n" + "=" * 80)
            print("CURRENT MODEL PRICING")
            print("=" * 80)

            for model in models:
                print(f"\nModel: {model.name} (Tenant {model.tenant_id})")
                print(f"  Provider: {model.provider}")

                if model.input_token_price is not None:
                    print(f"  Input Price:  ${model.input_token_price}/1M tokens")
                else:
                    print("  Input Price:  Not set ❌")

                if model.output_token_price is not None:
                    print(f"  Output Price: ${model.output_token_price}/1M tokens")
                else:
                    print("  Output Price: Not set ❌")

                if model.cache_token_price is not None:
                    print(f"  Cache Price:  ${model.cache_token_price}/1M tokens")
                else:
                    print("  Cache Price:  Not set ❌")

            print("\n" + "=" * 80)

        except Exception as e:
            print(f"❌ Error fetching pricing: {e}")
            raise


def main():
    """Main entry point."""
    print("=" * 80)
    print("MODEL PRICING SETUP")
    print("=" * 80)
    print()

    # Show current pricing
    asyncio.run(show_current_pricing())

    # Ask for confirmation
    print("\n⚠️  This will update pricing for all matching models in the database.")
    response = input("Continue? (yes/no): ").strip().lower()

    if response not in ["yes", "y"]:
        print("❌ Cancelled by user")
        return

    print()
    # Update pricing
    asyncio.run(update_model_pricing())

    # Show updated pricing
    asyncio.run(show_current_pricing())

    print("\n✅ Pricing setup complete!")
    print("\n💡 Next steps:")
    print("   1. Verify pricing is correct")
    print("   2. Ensure Celery workers are running")
    print("   3. Check daily aggregation runs successfully")
    print("   4. Review billing reports via API")


if __name__ == "__main__":
    main()

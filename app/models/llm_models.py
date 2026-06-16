"""
LLMModel separated from tenant_configs for clarity per ERD.
"""

from sqlalchemy import DECIMAL, JSON, BigInteger, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class LLMModel(BaseModel):
    __tablename__ = "llm_models"

    id = Column(BigInteger, primary_key=True, index=True)
    # Tenant ID removed to make models global
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    config = Column(JSON, nullable=True)

    # Pricing columns (price per 1 million tokens)
    input_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Price per 1M input tokens"
    )
    output_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Price per 1M output tokens"
    )
    cache_token_price = Column(
        DECIMAL(10, 6), nullable=True, comment="Price per 1M cache tokens"
    )

    # Note: Reverse relationships to ChatbotConfig removed since FK columns are now in JSON
    # Model IDs are stored in config_version_history JSON, not as FK columns

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, String

from app.models.base import BaseModel


class HeatmapKeyword(BaseModel):
    __tablename__ = "heatmap_keywords"

    tenant_id = Column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label = Column(String(100), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

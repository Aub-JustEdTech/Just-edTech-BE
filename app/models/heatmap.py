"""
HeatMap models: county-district mapping lookup table and per-tenant keyword chips.
"""

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, Integer, String

from app.models.base import BaseModel


class CountyDistrictMapping(BaseModel):
    """Maps Massachusetts school district names to their county."""

    __tablename__ = "county_district_mapping"

    district_name = Column(String, nullable=False)
    county_name = Column(String, nullable=False)
    state = Column(String(2), nullable=False, default="MA")
    fips_code = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_county_district_mapping_district_state", "district_name", "state"),
    )


class HeatmapKeyword(BaseModel):
    """Pre-defined keyword chips shown on the HeatMap View, per tenant."""

    __tablename__ = "heatmap_keywords"

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "ix_heatmap_keywords_tenant_active_order",
            "tenant_id",
            "is_active",
            "sort_order",
        ),
    )

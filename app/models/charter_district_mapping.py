"""
Charter district → parent public district mapping.

User-loaded lookup table that lets the heatmap layer roll up charter school
pins to the public district they sit inside geographically. Seeded from a
user-provided Excel file via scripts/school_data/seed_charter_mapping.py.

Both org_code columns reference schools via (tenant_id, org_code) so the mapping
is stable across re-seeds — org_code is the public DESE identifier and
survives tenant repopulation.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import relationship

from app.models.base import Base


class CharterDistrictMapping(Base):
    """One row per charter school → its parent public school district."""

    __tablename__ = "charter_district_mapping"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "charter_org_code"],
            ["schools.tenant_id", "schools.org_code"],
            ondelete="CASCADE",
            name="fk_charter_mapping_charter_school",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_district_org_code"],
            ["schools.tenant_id", "schools.org_code"],
            ondelete="CASCADE",
            name="fk_charter_mapping_parent_district",
        ),
        Index(
            "ix_charter_district_mapping_parent",
            "tenant_id",
            "parent_district_org_code",
        ),
    )

    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    charter_org_code = Column(String(16), primary_key=True)
    parent_district_org_code = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    charter_school = relationship(
        "School",
        primaryjoin=(
            "and_(CharterDistrictMapping.tenant_id == School.tenant_id, "
            "CharterDistrictMapping.charter_org_code == School.org_code)"
        ),
        foreign_keys="[CharterDistrictMapping.tenant_id, CharterDistrictMapping.charter_org_code]",
        viewonly=True,
    )
    parent_district = relationship(
        "School",
        primaryjoin=(
            "and_(CharterDistrictMapping.tenant_id == School.tenant_id, "
            "CharterDistrictMapping.parent_district_org_code == School.org_code)"
        ),
        foreign_keys="[CharterDistrictMapping.tenant_id, CharterDistrictMapping.parent_district_org_code]",
        viewonly=True,
    )

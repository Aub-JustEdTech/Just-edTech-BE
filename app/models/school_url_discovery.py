"""
Stored URL-discovery results for school districts.

One `SchoolUrlDiscovery` row holds metadata per school; ranked candidate
URLs live in `SchoolUrlCandidate` child rows (deduplicated, top-N at seed time).
"""

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class SchoolUrlDiscovery(BaseModel):
    """Latest stored discovery snapshot for a school district."""

    __tablename__ = "school_url_discoveries"
    __table_args__ = (
        UniqueConstraint("school_id", name="uq_school_url_discovery_school"),
        Index("ix_school_url_discoveries_tenant_id", "tenant_id"),
    )

    school_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    discovery_method = Column(String(32), nullable=True)
    total_urls_scanned = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)

    school = relationship("School", back_populates="url_discovery")
    candidates = relationship(
        "SchoolUrlCandidate",
        back_populates="discovery",
        cascade="all, delete-orphan",
        order_by="SchoolUrlCandidate.rank",
    )


class SchoolUrlCandidate(BaseModel):
    """One deduplicated candidate URL from a stored discovery run."""

    __tablename__ = "school_url_candidates"
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "url_hash",
            name="uq_school_url_candidate_school_hash",
        ),
        Index("ix_school_url_candidates_school_rank", "school_id", "rank"),
    )

    school_id = Column(
        BigInteger,
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    discovery_id = Column(
        BigInteger,
        ForeignKey("school_url_discoveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False)
    matched_keywords = Column(JSONB, nullable=False, default=list)
    score = Column(SmallInteger, nullable=False, default=0)
    rank = Column(SmallInteger, nullable=False)

    school = relationship("School", back_populates="url_candidates")
    discovery = relationship("SchoolUrlDiscovery", back_populates="candidates")

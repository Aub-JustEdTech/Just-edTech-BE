"""
HeatMap schemas for request validation and response serialization.
"""

from pydantic import BaseModel, Field, field_validator

from app.utils.geography import MA_COUNTY_NAMES


# ── Request schemas ──────────────────────────────────────────────────────────

class HeatmapSummaryParams(BaseModel):
    query: str = Field(..., description="Search keyword")
    state: str = Field(..., description="State abbreviation — must be 'MA'")

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()

    @field_validator("state")
    @classmethod
    def state_must_be_ma(cls, v: str) -> str:
        if v.upper() != "MA":
            raise ValueError("state must be 'MA'")
        return v.upper()


class CountyCitationsParams(BaseModel):
    county: str = Field(..., description="One of the 14 valid MA county names")
    query: str = Field(..., description="Search keyword")
    page: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1)

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()

    @field_validator("page_size")
    @classmethod
    def clamp_page_size(cls, v: int) -> int:
        return min(v, 25)


class CountyExportParams(BaseModel):
    county: str = Field(..., description="One of the 14 valid MA county names")
    query: str = Field(..., description="Search keyword")

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()


# ── Response schemas ─────────────────────────────────────────────────────────

class CountyScoreItem(BaseModel):
    county_name: str
    intensity_score: int
    conversation_count: int
    source_count: int

    class Config:
        from_attributes = True


class HeatmapSummaryResponse(BaseModel):
    data: list[CountyScoreItem]


class CitationItem(BaseModel):
    document_id: str = ""
    document_title: str
    school_district: str
    date: str
    snippet: str
    source_url: str
    relevance_score: float

    class Config:
        from_attributes = True


class CountyCitationsData(BaseModel):
    county_name: str
    keyword: str
    conversation_count: int
    source_count: int
    citations: list[CitationItem]

    class Config:
        from_attributes = True


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class CountyCitationsResponse(BaseModel):
    data: CountyCitationsData
    meta: PaginationMeta


class KeywordItem(BaseModel):
    id: int
    label: str

    class Config:
        from_attributes = True


class HeatmapKeywordsResponse(BaseModel):
    data: list[KeywordItem]


# ── District-level schemas ────────────────────────────────────────────────────

class DistrictCitationsParams(BaseModel):
    district: str = Field(..., description="School district name")
    query: str = Field(..., description="Search keyword")
    page: int = Field(1, ge=1)
    page_size: int = Field(10, ge=1)

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()

    @field_validator("page_size")
    @classmethod
    def clamp_page_size(cls, v: int) -> int:
        return min(v, 25)


class DistrictExportParams(BaseModel):
    district: str = Field(..., description="School district name")
    query: str = Field(..., description="Search keyword")

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()


class DistrictScoreItem(BaseModel):
    district_name: str
    intensity_score: int
    conversation_count: int
    source_count: int

    class Config:
        from_attributes = True


class DistrictCitationsData(BaseModel):
    district_name: str
    keyword: str
    conversation_count: int
    source_count: int
    citations: list[CitationItem]

    class Config:
        from_attributes = True


class DistrictCitationsResponse(BaseModel):
    data: DistrictCitationsData
    meta: PaginationMeta

from pydantic import BaseModel


MA_SCHOOL_DISTRICTS = [
    "Boston School District",
    "Cambridge School District",
    "Salem School District",
    "Framingham School District",
    "Quincy School District",
    "Newton School District",
    "Lowell School District",
    "Worcester School District",
    "Springfield School District",
    "Lynn School District",
    "Brockton School District",
    "New Bedford School District",
    "Somerville School District",
    "Malden School District",
    "Medford School District",
]


class DistrictScoreItem(BaseModel):
    district_name: str
    intensity_score: int
    conversation_count: int
    source_count: int


class CitationItem(BaseModel):
    document_id: str | None = None
    document_title: str
    date: str | None
    snippet: str
    source_url: str
    relevance_score: float
    page_number: int | None = None


class DistrictCitationsResponse(BaseModel):
    district_name: str
    keyword: str
    conversation_count: int
    source_count: int
    citations: list[CitationItem]


class KeywordItem(BaseModel):
    id: int
    label: str

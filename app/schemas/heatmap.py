from typing import Literal

from pydantic import BaseModel

MA_SCHOOL_DISTRICTS = [
    "Abington", "Acton-Boxborough", "Acushnet", "Agawam", "Amesbury",
    "Amherst", "Amherst-Pelham", "Andover", "Arlington", "Ashburnham-Westminster",
    "Ashland", "Athol-Royalston", "Attleboro", "Auburn", "Avon",
    "Ayer Shirley School District", "Barnstable", "Bedford", "Belchertown", "Bellingham",
    "Belmont", "Berkley", "Berkshire Hills", "Berlin-Boylston", "Beverly",
    "Billerica", "Blackstone-Millville", "Boston", "Bourne", "Boxford",
    "Braintree", "Brewster", "Bridgewater-Raynham", "Brimfield", "Brockton",
    "Brookfield", "Brookline", "Burlington", "Cambridge", "Canton",
    "Carlisle", "Carver", "Central Berkshire", "Chelmsford", "Chelsea",
    "Chesterfield-Goshen", "Chicopee", "Clarksburg", "Clinton", "Cohasset",
    "Concord", "Conway", "Danvers", "Dartmouth", "Dedham",
    "Deerfield", "Dennis-Yarmouth", "Dighton-Rehoboth", "Douglas", "Dover",
    "Dracut", "Dudley-Charlton Reg", "Duxbury", "East Bridgewater", "East Longmeadow",
    "Eastham", "Easthampton", "Easton", "Edgartown", "Erving",
    "Everett", "Fairhaven", "Fall River", "Falmouth", "Farmington River Reg",
    "Fitchburg", "Florida", "Foxborough", "Framingham", "Franklin",
    "Freetown-Lakeville", "Gardner", "Gateway", "Georgetown", "Gill-Montague",
    "Gloucester", "Gosnold", "Grafton", "Granby", "Greenfield",
    "Groton-Dunstable", "Hadley", "Halifax", "Hamilton-Wenham", "Hampden-Wilbraham",
    "Hancock", "Hanover", "Harvard", "Hatfield", "Haverhill",
    "Hawlemont", "Hingham", "Holbrook", "Holland", "Holliston",
    "Holyoke", "Hoosac Valley Regional", "Hopedale", "Hopkinton", "Hudson",
    "Hull", "Ipswich", "Kingston", "Lawrence", "Lee",
    "Leicester", "Lenox", "Leominster", "Leverett", "Lexington",
    "Lincoln", "Littleton", "Longmeadow", "Lowell", "Ludlow",
    "Lunenburg", "Lynn", "Lynnfield", "Malden", "Manchester Essex Regional",
    "Mansfield", "Marblehead", "Marion", "Marlborough", "Marshfield",
    "Mashpee", "Mattapoisett", "Maynard", "Medfield", "Medford",
    "Medway", "Melrose", "Mendon-Upton", "Methuen", "Middleborough",
    "Middleton", "Milford", "Millbury", "Millis", "Milton",
    "Mohawk Trail", "Monomoy Regional School District", "Monson", "Mount Greylock", "Nahant",
    "Nantucket", "Narragansett", "Nashoba", "Natick", "Needham",
    "New Bedford", "New Salem-Wendell", "Newburyport", "Newton", "Norfolk",
    "North Adams", "North Andover", "North Attleborough", "North Brookfield", "North Middlesex",
    "North Reading", "Northampton", "Northborough", "Northbridge", "Norton",
    "Norwell", "Norwood", "Oak Bluffs", "Orange", "Orleans",
    "Oxford", "Palmer", "Peabody", "Pelham", "Pembroke",
    "Pentucket", "Petersham", "Pioneer Valley", "Pittsfield", "Plainville",
    "Plymouth", "Plympton", "Provincetown", "Quabbin", "Quaboag Regional",
    "Quincy", "Randolph", "Reading", "Revere", "Richmond",
    "Rochester", "Rockland", "Rockport", "Rowe", "Salem",
    "Sandwich", "Saugus", "Savoy", "Scituate", "Seekonk",
    "Sharon", "Sherborn", "Shrewsbury", "Shutesbury", "Somerset",
    "Somerville", "South Hadley", "Southampton", "Southborough", "Southbridge",
    "Southern Berkshire", "Southwick-Tolland-Granville Regional School District", "Spencer-E Brookfield",
    "Springfield", "Stoneham", "Stoughton", "Sturbridge", "Sudbury",
    "Sunderland", "Sutton", "Swampscott", "Swansea", "Taunton",
    "Tewksbury", "Tisbury", "Topsfield", "Triton", "Truro",
    "Tyngsborough", "Up-Island Regional", "Uxbridge", "Wachusett", "Wakefield",
    "Wales", "Walpole", "Waltham", "Ware", "Wareham",
    "Watertown", "Wayland", "Webster", "Wellesley", "Wellfleet",
    "West Boylston", "West Bridgewater", "West Springfield", "Westborough", "Westfield",
    "Westford", "Westhampton", "Weston", "Westport", "Westwood",
    "Weymouth", "Whately", "Whitman-Hanson", "Williamsburg", "Wilmington",
    "Winchendon", "Winchester", "Winthrop", "Woburn", "Worcester",
    "Worthington", "Wrentham",
]


class DistrictScoreItem(BaseModel):
    district_name: str
    intensity_score: int
    conversation_count: int
    source_count: int
    district_type: Literal["public", "charter"] = "public"


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

"""
Geography utilities: load US county names from addfips bundled Census 2020 data.
No hardcoded values — data comes from the official US Census Bureau dataset
packaged inside the addfips library.
"""

import csv
from functools import lru_cache
from pathlib import Path
import addfips



# MA state FIPS code (Federal Information Processing Standard)
_STATE_FIPS = {"MA": "25"}


@lru_cache(maxsize=None)
def get_county_names(state_abbr: str) -> list[str]:
    """
    Return sorted county names for a US state using addfips bundled Census 2020 data.

    Args:
        state_abbr: Two-letter state abbreviation e.g. "MA"

    Returns:
        Sorted list of county names without the word "County"
        e.g. ["Barnstable", "Berkshire", ..., "Worcester"]
    """
    fips = _STATE_FIPS.get(state_abbr.upper())
    if not fips:
        raise ValueError(f"State '{state_abbr}' is not supported. Add its FIPS code to _STATE_FIPS.")

    data_file = Path(addfips.__file__).parent / "data" / "counties_2020.csv"

    with open(data_file, newline="") as f:
        reader = csv.DictReader(f)
        names = [
            row["name"].replace(" County", "")
            for row in reader
            if row["statefp"] == fips
        ]

    return sorted(names)


MA_COUNTY_NAMES: list[str] = get_county_names("MA")

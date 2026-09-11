"""
State vocabulary pack loader.

Single entry point for retrieving the merged (universal core + state) pack
for a given state. Callers don't need to know the merge rules.
"""

from __future__ import annotations

from app.services.heatmap_ingest.vocabulary_packs.core import (
    CORE_TOPIC_TAXONOMY,
    VocabPack,
)
from app.services.heatmap_ingest.vocabulary_packs.ma import (
    MA_CURRICULA,
    MA_KEYWORD_FLAGS,
    MA_ORGS,
)

# State registry. Keys are 2-letter state abbreviations (uppercase).
_STATE_PACKS: dict[str, tuple[tuple, tuple, tuple]] = {
    "MA": (MA_CURRICULA, MA_ORGS, MA_KEYWORD_FLAGS),
}


def _normalize_state(state: str | None) -> str:
    if not state:
        return "MA"
    s = state.strip().upper()
    return s if s in _STATE_PACKS else "MA"


def get_pack(state: str | None) -> VocabPack:
    """
    Return the merged vocabulary pack for a state.

    The universal core taxonomy is always present. State-specific curricula,
    named orgs, and keyword flags come from the state registry. Unknown
    states fall back to the MA pack (V1 corpus is MA-only).
    """
    s = _normalize_state(state)
    curricula, orgs, flags = _STATE_PACKS[s]
    return VocabPack(
        state=s,
        topic_taxonomy=CORE_TOPIC_TAXONOMY,
        state_curricula=curricula,
        state_orgs=orgs,
        keyword_flags=flags,
    )


def get_keyword_flags_for_state(state: str | None) -> tuple[str, ...]:
    """Convenience accessor for just the keyword flag list."""
    return get_pack(state).keyword_flags

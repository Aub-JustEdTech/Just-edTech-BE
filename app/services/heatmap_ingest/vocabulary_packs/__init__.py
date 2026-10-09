"""
State-swappable vocabulary packs for the heatmap ingest classifier.

Structure (per spec A3 / A4):
  - Universal core topic taxonomy (state-agnostic). See `core.py`.
  - State vocabulary packs (curricula, named advocacy orgs). See `ma.py`.
  - Per-state lexical keyword recall flags (A4). See `keyword_flags.py`.
  - Loader: `get_pack(state) -> VocabPack` returns the merged (core + state)
    topic vocabulary and keyword list for a given state.

A pack is injected into the chunk classification prompt at runtime (per A3b
the state pack is NOT hardcoded into the taxonomy). The loader is the single
entry point so callers don't need to know the merge rules.
"""

from app.services.heatmap_ingest.vocabulary_packs.core import (
    CORE_TOPIC_TAXONOMY,
    TopicCategory,
    TopicSubtopic,
    VocabPack,
)
from app.services.heatmap_ingest.vocabulary_packs.keyword_flags import (
    KeywordFlagSpec,
    match_keyword_flags,
)
from app.services.heatmap_ingest.vocabulary_packs.loader import (
    get_pack,
    get_keyword_flags_for_state,
)

__all__ = [
    "CORE_TOPIC_TAXONOMY",
    "KeywordFlagSpec",
    "TopicCategory",
    "TopicSubtopic",
    "VocabPack",
    "get_keyword_flags_for_state",
    "get_pack",
    "match_keyword_flags",
]

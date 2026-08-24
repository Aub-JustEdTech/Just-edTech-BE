"""
Massachusetts state vocabulary pack, per spec A3b.

Contains MA-specific curricula subtopics and named advocacy orgs. The MA pack
is injected into the classification prompt — NOT merged into the universal
core taxonomy data layer.

Scope rule (spec A3b): national-scope orgs belong in a shared cross-state
list, not the MA pack. `EducateUS` is flagged below pending confirmation of
its scope; it is NOT shipped in the MA pack until that's resolved.
"""

from __future__ import annotations

from app.services.heatmap_ingest.vocabulary_packs.core import TopicSubtopic

# MA-specific curricula. These appear under the `sexed` category but are
# state-scoped, so they are surfaced to the classifier via the state pack
# rather than the universal core.
MA_CURRICULA: tuple[TopicSubtopic, ...] = (
    TopicSubtopic(
        category="sexed",
        # Dotted names match prompt.py TOPIC_TAGS (literal strings).
        subtopic="curriculum.3rs",
        description="3 Rs curriculum (MA sex ed framework).",
    ),
    TopicSubtopic(
        category="sexed",
        subtopic="curriculum.get_real",
        description="Get Real curriculum (MA sex ed framework).",
    ),
    TopicSubtopic(
        category="sexed",
        subtopic="curriculum.chpe_framework",
        description="CHPE Framework (Comprehensive Health & PE, MA).",
    ),
)

# MA-specific named advocacy orgs. Used to populate org_id / org_name when
# the `advocacy.external_org_mentioned` tag is assigned.
#
# TODO: confirm whether EducateUS is national-scope or MA-scope before
# adding it here. If national, it belongs in a shared cross-state org list,
# not this pack. Left out of V1 until confirmed.
MA_ORGS: tuple[str, ...] = (
    "Massachusetts Family Institute",
)

MA_KEYWORD_FLAGS: tuple[str, ...] = (
    "pornography",
    "indoctrination",
    "opt-in",
    "opt-out",
    "sexual risk avoidance",
    "abstinence-only",
    "abstinence-plus",
    "success sequencing",
    "3 Rs",
    "Get Real",
    "CHPE Framework",
)
"""
Per-state lexical recall flags from spec A4.

Lexical, deliberately over-inclusive. Reconciled against classified tags
during spot-check — `keyword_flag = true` with `topic_tags = []` is the
expected "safety net" for classifier misses (per A9).
"""

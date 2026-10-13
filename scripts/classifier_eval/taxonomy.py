"""
Canonical taxonomy for the heatmap ingestion classifier.

This file is kept for backwards compatibility with the eval harness CLI
invocation (`python -m scripts.classifier_eval.runner`). The canonical
source of truth lives in app/services/heatmap_ingest/taxonomy.py and is
imported by the production pipeline; this thin re-export keeps the eval
harness in sync.
"""

from app.services.heatmap_ingest.taxonomy import (  # noqa: F401
    ACTION_TYPES,
    ALL_CHUNK_LABELS,
    ChunkClassification,
    DocClassification,
    ENTITY_TYPES,
    SEX_ED_SUBTOPICS,
    TOPICS,
)

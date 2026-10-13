"""Heatmap ingestion services (doc-level classifier, batch classifier, taxonomy).

This package is the production home for the classification pipeline that the
Phase 1 eval harness (scripts/classifier_eval/) validated. The eval
harness's taxonomy and prompt are the canonical source of truth and are
re-exported here so the pipeline imports them from a stable location.
"""

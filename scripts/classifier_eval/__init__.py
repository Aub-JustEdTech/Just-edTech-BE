"""Phase 1 classifier eval harness for the heatmap ingest pipeline.

Canonical modules:
  - taxonomy: frozen label sets + response schema (imported by the pipeline too)
  - prompt:    system prompt + per-chunk message + Batch API JSONL builder
  - runner:    CLI that classifies labeled_chunks.yaml and prints per-label metrics
  - labeled_chunks.yaml: 100 hand-labeled chunks the classifier is evaluated against
"""

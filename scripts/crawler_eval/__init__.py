"""Crawler eval harness for the schema-driven page classifier.

Mirrors scripts/classifier_eval/ (the heatmap chunk-classifier harness) but
evaluates PageClassifier.classify — the LLM-dependent component of the
schema-driven crawler — instead of the chunk classifier.

Canonical modules:
  - labeled_pages.yaml: hand-labeled pages pulled from the 20-school POC run
    (scripts/school_data/output/schema_crawl_results.json) plus hand-picked
    negatives and archives. Each row carries ground truth AND a `predicted:`
    snapshot of the POC's actual output so --offline mode can compute per-field
    deltas without re-running the classifier.
  - runner.py: CLI that classifies (or replays) labeled_pages.yaml and prints
    per-field precision / recall / F1 plus a gate decision (PASS/FAIL).
  - fixtures/: cached page markdown for live-mode stability (one <id>.md per
    row, populated on first live run, reused thereafter unless --refresh).

Reuses PageClassifier and RelevantPage unchanged from the POC
(scripts/school_data/schema_crawl_poc/), and app.services.llm.client.
"""

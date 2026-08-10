# Schema crawl run — 2026-08-05

Full re-crawl of `doe_school_urls.json` (394 districts) via
`scripts.school_data.schema_crawl_poc.run_poc`.

## Settings
- max_pages=10
- concurrency=3
- confidence=0.5
- include_archival=true (skip_archival=false)

## Batches (sequential)
| File | Offset | Limit |
|------|--------|-------|
| schema_crawl_results_batch_001_100.json | 0 | 100 |
| schema_crawl_results_batch_101_200.json | 100 | 100 |
| schema_crawl_results_batch_201_300.json | 200 | 100 |
| schema_crawl_results_batch_301_394.json | 300 | 94 |

Final merge: `schema_crawl_results.json`

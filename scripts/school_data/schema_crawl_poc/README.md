# Schema-Driven Crawler POC (experiment branch)

Experiment-only implementation of the approach described in
[Schema-Driven Crawling is Cheap and Effective](https://noosphereanalytics.com/blog/posts/schema-driven-crawling-is-cheap-and-effective/).

This lives on the `experiment/schema-driven-crawl-poc` branch. It does **not**
modify the production `SchoolScraperService` — that keyword-based flow remains
the default and is untouched. If the experiment pans out, the winning pieces
get promoted to `app/services/web_scraper/` on a separate `feat_` branch behind
a setting.

## What it does

Instead of ranking candidate URLs by counting keyword hits in the URL path
(the current `SchoolScraperService._filter_candidates` approach), the POC:

1. Fetches a page as HTML and renders it to **markdown-with-links**.
2. Sends the markdown to a cheap LLM (`gpt-4o-mini` by default) with a
   **Pydantic schema** (`RelevantPage`) that doubles as the prompt.
3. The LLM returns:
   - `has_data` — does this page directly host board documents/media?
   - `has_data_links` — does it link to subpages that do?
   - `data_page_info` — if `has_data`, what `data_type` (mirrors the heatmap
     taxonomy), is it `is_archive`, which `data_years_available`?
   - `possible_relevant_pages` — same-domain links with a `confidence` in
     `[0, 1]`.
4. The crawler pushes high-confidence links onto a ranked frontier and pops
   the highest-confidence next — all decision logic lives in the crawler, the
   LLM only does structured extraction. (Small models are bad at tool use but
   good at schema extraction.)
5. Archival pages (`is_archive=true`) are skipped by default so routine
   scrape cycles don't ingest stale past-year documents.

## Layout

```
scripts/school_data/schema_crawl_poc/
├── __init__.py          # package exports
├── schemas.py           # RelevantPage, DataPageInfo, PossibleRelevantPage (Pydantic)
├── classifier.py        # PageClassifier — one LLM call per page (reuses app.services.llm.client)
├── crawler.py           # SchemaDrivenCrawler — ranked frontier, fetches + classifies pages
├── run_poc.py           # CLI: crawl every school in selected_schools.json
└── compare.py           # CLI: diff POC output vs the keyword baseline
```

No files under `app/` are modified except `app/core/config.py` (one additive
setting: `SCHOOL_SCRAPER_LLM_PAGE_CLASSIFIER_MODEL`) and `.env.example`.

## Usage

```bash
# 1. Run the schema-driven crawler over the same 123 schools the keyword
#    baseline ran on. Writes schema_crawl_results.json.
python -m scripts.school_data.schema_crawl_poc.run_poc \
    --max-pages 8 --concurrency 3

# Include archival pages in the result set (default skips them):
python -m scripts.school_data.schema_crawl_poc.run_poc --include-archival

# Override the model (defaults to settings.HEATMAP_INGEST_DOC_CLASSIFIER_MODEL,
# i.e. openai/gpt-4o-mini):
python -m scripts.school_data.schema_crawl_poc.run_poc \
    --model openai/gpt-4o-mini

# Verbose logging:
python -m scripts.school_data.schema_crawl_poc.run_poc -v

# 2. Compare against the keyword baseline in school_url_candidates.json.
python -m scripts.school_data.schema_crawl_poc.compare
# -> writes scripts/school_data/output/schema_vs_keyword.md
```

## Inputs / outputs

| File | Role |
|---|---|
| `scripts/school_data/output/selected_schools.json` | Input: 123 MA school districts (name, org_code, website) |
| `scripts/school_data/output/school_url_candidates.json` | Keyword baseline (from `discover_school_candidates.py`) |
| `scripts/school_data/output/schema_crawl_results.json` | POC output — per-school `data_pages`, `visited_pages`, `llm_calls`, `errors` |
| `scripts/school_data/output/schema_vs_keyword.md` | Comparison report — per-school overlap, schema-only finds, keyword-only misses |

## Cost estimate

One LLM call per crawled page. With `max_pages=10` and 123 schools that's at
most ~1,230 calls. At ~12k input chars (~3k tokens) and ~500 output tokens on
`gpt-4o-mini` ($0.15 / 1M in, $0.60 / 1M out):

```
1230 * (3000/1e6 * 0.15 + 500/1e6 * 0.60) ≈ $0.64
```

~$0.005 per school — matches the blog's "just under a penny per district" claim.

## Decision criteria for promoting to `app/`

The POC is worth promoting if the comparison report shows, over a meaningful
sample of schools:

1. **Recall** — `schema_only` finds ≥ 10% more true data pages than the
   keyword scorer (the keyword scorer misses pages whose paths don't contain
   `meeting`/`minutes`/`board`).
2. **Precision** — `schema_data_pages` is mostly real (manual spot-check of
   ~20 `schema_only_urls`).
3. **Archival detection** — `n_schema_archival > 0` on at least a few
   schools, proving the `is_archive` signal fires on real archive pages.
4. **Cost** — `llm_calls` stays within budget and per-page token usage is
   close to the estimate above.

If those hold, the promotion path is:

- Open `feat_schema-driven-page-classification` from this branch.
- Add a `SchemaDrivenSchoolScraperService` next to
  `school_scraper_service.py` (additive, not replacing).
- Add `SCHOOL_SCRAPER_RANKING_MODE` (`keyword` | `llm` | `both`) defaulting
  to `keyword` so the new path is opt-in via env.
- Wire `is_archive` + `data_years_available` into new columns on
  `SchoolUrlCandidate` via an Alembic migration.

## Rollback

This whole branch is disposable. To abandon:

```bash
git checkout feat_data-scrapper-categorize
git branch -D experiment/schema-driven-crawl-poc
```

`feat_data-scrapper-categorize` (the commit before the experiment) is the
unchanged production baseline.

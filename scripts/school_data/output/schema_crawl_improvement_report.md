# Schema-Driven Crawler Improvement Report

Comparison of `schema_crawl_results.json` (crawler output) against
`finalised_20_disticts.json` (expected correct URL per district), across two
rounds of fixes.

## Changes made

### Round 1 — vocabulary, ranking, archives, hub fallback

1. **Expanded LLM prompt vocabulary** (`app/services/web_scraper/page_classifier.py`,
   `app/services/web_scraper/page_schemas.py`) — added "Board of Trustees",
   "Joint Supervisory Committee", "meeting packets", "document archives",
   "archived agendas" as equivalent signals to "school board minutes/agendas".
   Archive pages are now explicitly described as VALID `has_data=true` pages.
2. **URL-path keyword boosting** (`app/services/web_scraper/schema_driven_crawler.py`)
   — `_keyword_boost()`, a deterministic confidence bump for candidate URLs
   whose path contains strong signals (`meeting-minutes`, `school-committee`,
   `board-of-trustees`, `document-archives`, etc.) or 2+ weak signals.
   Applied to both LLM-suggested links and sitemap/nav-seeded frontier entries.
3. **Archives kept by default** (`app/core/config.py`) — flipped
   `SCHOOL_SCRAPER_LLM_SKIP_ARCHIVAL` default from `True` to `False`.
4. **Hub-page fallback** (`app/services/web_scraper/schema_driven_school_scraper_service.py`)
   — when the crawler finds zero direct data pages, visited pages with
   `has_data_links=true` and a keyword-matching URL path are surfaced as
   low-score fallback candidates instead of returning nothing.

### Round 2 — fetch reliability + URL correctness

5. **UA rotation + forced Playwright escalation on blocked/failed fetches**
   (`app/services/web_scraper/schema_driven_crawler.py`) — `_fetch()` now
   retries a failed request with alternate browser-style User-Agents, then
   escalates to a real Playwright Chromium browser if still failing. Applies
   both to WAF-style blocks (403/429/503) **and** connection-level failures
   (`status=None`: DNS, TLS/SSL trust errors, timeouts) — the latter turned
   out to be the actual cause of the Leominster homepage failure (a Let's
   Encrypt intermediate cert not recognized by the local Python/certifi trust
   store, but recognized by Chromium's own trust store).
6. **Percent-encoding of resolved candidate URLs** (`app/services/web_scraper/schema_driven_crawler.py`,
   `_percent_encode_url()`) — LLM/nav-extracted link hrefs are often relative
   and contain literal unencoded characters (most commonly spaces in PDF
   filenames, e.g. `.../Meeting Minutes/22-23/SC Minutes 7-19-22.pdf`). These
   were being written straight into `possible_relevant_pages[].url` in the
   JSON output — not independently clickable, and also causing spurious
   `fetch_failed` errors when the crawler tried to `GET` the raw unencoded
   URL. Candidate URLs are now resolved to absolute + percent-encoded form
   (idempotent — doesn't double-encode already-encoded segments) before
   being persisted or queued for fetch.

Re-run command:

```bash
poetry run python -m scripts.school_data.schema_crawl_poc.run_poc \
    --max-pages 10 --concurrency 3 --include-archival
```

## Results: exact-URL hit rate

| Metric | Original | Final |
|---|---|---|
| Exact match (scheme/www-normalized) | 5 / 20 (25%) | 15 / 20 (75%) |
| Districts with zero data pages found | 12 / 20 | 2 / 20 |

`Leominster` counts as a hit via the **hub-page fallback** (service-layer
behavior) — the raw crawler correctly reaches
`/district/school-committee` and classifies it `has_data_links=true`, but it's
a navigation hub rather than a document page, so `has_data=false`; the
fallback in `schema_driven_school_scraper_service.py` surfaces it anyway.

## Per-district results

| District | Original | Final | Notes |
|---|---|---|---|
| Quabbin | miss | **HIT** | Finds the CMS deep-link `/cms/One.aspx?portalId=...` |
| Wachusett | HIT | HIT | Unchanged |
| Leicester | HIT | HIT | Unchanged |
| Ware | miss | miss | Target archive page never entered the frontier within 10 pages |
| Wareham | miss | miss | Real content lives on a different domain (`warehamps.org`) than the seed (`warehamps.schoolblocks.com`) — cross-domain, not yet handled |
| Plymouth | miss | **HIT** | Finds `/page/school-committee` |
| Bourne | HIT | HIT | Unchanged |
| Worcester | miss | **HIT** | Finds `/documents/school-committee/agenda-and-minutes/559835` |
| Shutesbury | HIT | HIT | "Joint Supervisory Committee" vocabulary fix |
| Leominster | miss | **HIT (via hub fallback)** | Homepage `fetch_failed` was an SSL-trust-store issue, not a WAF block — fixed by the connection-error escalation to Playwright |
| New Salem-Wendell | miss | **HIT** | Shares `union28.org` site with Shutesbury |
| Belchertown | miss | **HIT** | Archive page now kept — `/school-committee-document-archives/` |
| Quaboag Regional | miss | **HIT** | Hub-page fallback surfaces `/district-depts/school-committee` |
| Palmer | miss (fetch_failed) | **HIT** | Homepage now fetches; finds CMS deep-link `/apps/pages/index.jsp?uREC_ID=293361` |
| Bristol County Agricultural | miss | miss* | "Board of Trustees" vocabulary works — finds 2 real board-of-trustees documents, not the exact hub URL |
| Bristol-Plymouth Vocational | miss | **HIT** | Finds `/about/school-committee/meeting-minutes-archive` |
| Brimfield (Tantasqua) | miss | miss* | Finds 7 real CMS committee-minutes/agenda documents, not the exact `pREC_ID` requested |
| Bridgewater-Raynham | miss | near-hit | SchoolBlocks canonicalizes the vanity slug to a UUID path; the UUID (`9945763c`) matches the expected URL's slug suffix exactly |
| Brewster (Nauset) | HIT | HIT | Unchanged (matches once scheme is normalized) |
| Bridge Boston Charter | miss | **HIT** | "Board of Trustees" vocabulary finds `/board` plus the exact dated minutes page |

`*` = crawler found real, correct-topic documents in the same section as expected, just not byte-identical to the single curated URL.

## Remaining gaps (not yet addressed)

1. **Cross-domain follow** (Wareham: `warehamps.schoolblocks.com` seed vs. real content on `warehamps.org`) — needs allowing same-organization domain hops or using the final redirect target as `base_domain`.
2. **Deeper/targeted frontier exploration for archive-heavy nav** (Ware: the target archived page was never reached within `max_pages=10`) — may need a higher page budget or better prioritization of `archived_news`-style paths.
3. **Exact-page disambiguation within a large CMS document set** (Brimfield/Tantasqua, Bristol County Agricultural) — the crawler finds many correct-topic documents on the right CMS but not the single curated `pREC_ID`/document. This is arguably a ground-truth-specificity issue rather than a crawler bug.

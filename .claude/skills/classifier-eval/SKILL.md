---
name: classifier-eval
description: Measures accuracy of the heatmap_ingest chunk classifier (app/services/heatmap_ingest/prompt.py) against an independent, neutral ground truth, and scores any batch run deterministically against it. Use when the user asks to test or validate a classifier prompt change, measure chunk-classification accuracy, check for a false-positive/false-negative regression after editing prompt.py, pilot a new prompt version before a full corpus run, or compare two versions of the chunk classifier.
allowed-tools: Bash, Read, Write
---

Two separable pieces: **grading** (judgment — read a chunk, independently
decide what it should be classified as) and **scoring** (deterministic code —
compare a run's actual output to that graded ground truth). Never hand-recompute
precision/recall/F1 — always run `score_run.py`. Never grade a prompt version
against ground truth built by consulting that same prompt's decision rules —
`rubric.md` is intentionally a separate, more literal reading of the taxonomy,
independent of the classifier's own forced co-occurrence rules and examples.

## Files in this skill

- `rubric.md` — the neutral taxonomy definitions used ONLY for grading. No
  forced co-occurrence, no worked examples, no classifier-prompt-specific
  edge-case corrections.
- `ground_truth.json` — a growing fixture of chunks graded against
  `rubric.md`, each with `{custom_id, off_topic, topics, topic_tags,
  confidence, note}`. Extend it when grading new chunks; don't replace it.
- `score_run.py` — deterministic scorer. Given `ground_truth.json` + one or
  more batch output JSONLs, produces a per-chunk comparison CSV and a
  scoring JSON (precision/recall/F1 with Wilson 95% CIs, per-label and
  micro-averaged, for `off_topic`/`topics`/`topic_tags`).
- `run_batch.py` — rebuilds a batch input JSONL from an existing chunk set
  using the **current** `prompt.py` content, then submits/polls/downloads via
  the real OpenAI Batch API.
- `eval_prompt.py` — an independent-judge version of `rubric.md`, written as
  a callable prompt (schema matches `ground_truth.json`'s fields) so grading
  could run at scale via API instead of manual reading. **Not currently
  wired up or usable**: requires `ANTHROPIC_API_KEY`, which is not in
  `.env` (only OpenAI keys are configured). Deliberately meant to run on
  Claude, not GPT, to keep judge and classifier in different model families.

## How to pilot a prompt change

1. Pick (or reuse) a chunk set as a batch input JSONL — e.g. an existing one
   under `runs/<name>/`. `ground_truth.json`'s `custom_id`s currently cover a
   132-chunk subset of a real 20-district production batch.
2. `python run_batch.py build --source <existing_input.jsonl> --out
   <new_run_dir>/input.jsonl` — pulls the CURRENT `SYSTEM_PROMPT` and
   `build_response_format_schema()` from `prompt.py` live, keeping the same
   `custom_id`/chunk_text/DOC-context per line as the source file.
3. `python run_batch.py submit --input <new_run_dir>/input.jsonl --meta
   <new_run_dir>/batch_meta.json`
4. `python run_batch.py wait --meta <new_run_dir>/batch_meta.json --out
   <new_run_dir>/output.jsonl --poll-seconds 20` — run this via a
   backgrounded shell command; it blocks until the batch reaches a terminal
   status, typically a few minutes for a 100-200 chunk pilot.
5. If the chunk set is new (not already in `ground_truth.json`), grade it
   first — see "Grading protocol" below — before scoring.
6. `python score_run.py --gt ground_truth.json --text-source
   <any_input.jsonl_covering_these_ids> --run run1=<path> --run run2=<path>
   ... --out-csv <out>/per_chunk_comparison.csv --out-json
   <out>/scoring_results.json` — pass every run you want compared side by
   side in one call; it's cheap and keeps the comparison apples-to-apples.
7. Interpret against the priorities below, not raw accuracy alone.

`.env` has environment variables the scripts need but don't auto-load —
export `OPENAI_API_KEY` from it before `submit`/`wait` (see pitfall below on
which of the two keys to use).

## Priorities to weigh results against

Established for this classifier, in order — a change that trades a lower
bar for a higher one is a net win even if a single "overall accuracy" number
drops:

1. **The off_topic gate must never drop a chunk that has a real `topics`
   value.** `batch_classifier.py`'s `if classification.off_topic: continue`
   silently excludes that chunk from `heatmap_aggregate` regardless of what
   topic it would otherwise have counted toward. Measure: of chunks with a
   non-empty `topics` in ground truth, how many got `off_topic=True`? Target
   zero.
2. **Main topic (`topics` field) identified correctly ≥90% of the time** —
   per topic-bearing chunk, does the predicted `topics` set overlap the real
   one at all (not necessarily an exact-set match)? Caution: this is
   typically measured over a small number of positive chunks (10 in the
   current `ground_truth.json`) — each chunk swings the percentage by ~10
   points, so treat a pass/fail near the 90% line as low-confidence until
   measured on a larger positive sample.
3. **`topic_tags`/subtopic-level mistakes are acceptable for v1** as long as
   the broad topic is right. Don't over-invest prompt-engineering effort
   chasing exact subtopic match at the expense of 1 or 2 — check whether a
   "miss" is really a `low`-confidence, disputed ground-truth entry (see
   below) before treating it as a confirmed classifier bug.

General `off_topic` accuracy across ALL chunks (not just topic-bearing ones)
still matters but is explicitly lower priority than #1 — a version that
over-fires `off_topic=True` on generic substantive content (budgets,
personnel, facilities) is worse UX/data-quality but not the same failure
class as silently losing a real topic.

## Grading protocol — for adding NEW chunks to `ground_truth.json`

For each `custom_id`, before looking at any model output:

1. Read the full `chunk_text` plus `DOC entity_type`/`DOC meeting_date`/state
   vocabulary pack context given to the model. Grade the text, not the
   model's answer — don't look at the prediction first.
2. Decide `off_topic` per `rubric.md`, independent of everything else: `true`
   ONLY for pure procedural boilerplate (roll call, adjournment, attendance,
   page numbers, empty/near-empty fragments). Any substantive content — even
   content matching zero taxonomy labels — is `false`. This is the single
   most common grading error; when in doubt, `false`.
3. Decide `topics`: 0..N from the TOPICS enum, applying the substantiveness
   rule — a topic fires only for substantive discussion, not an incidental
   one-word/short-phrase mention with no elaboration, stance, or consequence.
4. Decide `topic_tags`: 0..N `{category, subtopic}` pairs from the closed
   vocabulary. Narrower than `topics` — `topic_tags: []` while `topics` is
   non-empty is normal and correct. Never force a mapping; if it's a stretch,
   leave it out and drop confidence to `low` with a note.
5. Don't grade `subtopics` (the legacy sex_education-only field) at all —
   it's excluded from `ground_truth.json` on purpose (see prompt.py comment
   history / taxonomy.py: it's kept only for backward compatibility with an
   older rollup path, not part of the current taxonomy design).
6. `confidence: "low"` whenever: mapping to a specific subtopic required
   judgment; the substantiveness call was borderline (agenda lists, garbled
   OCR); a taxonomy concept appears only inside generic legal boilerplate
   with no elaboration; or the closed vocabulary doesn't cleanly cover a
   real-seeming case. Roughly a third of chunks land on `low` in practice —
   that's a sanity-check ratio, not a target.
7. Write a `note` whenever confidence is `low`, or a `high` call might look
   surprising without explanation. State *why* in rubric terms. Leave `""`
   for clean, unsurprising `high` calls.
8. Append to `ground_truth.json` (dedupe by `custom_id`) — don't replace the
   file wholesale, and don't grade the same `custom_id` twice with different
   standards.

A `low`-confidence entry with a note questioning its own label is not the
same as a confirmed ground-truth fact — if a run "misses" one of these,
check whether the disagreement is real before treating it as a classifier
regression. Two ground-truth entries found this way during actual use
(`gt_topic_tags` on two `lgbtq.protections_adopted` chunks) turned out to be
graded `high` confidence but contradicted by the chunk's own text on
inspection — confidence tags reduce this risk but don't eliminate it; spot
check surprising misses against the raw chunk text before trusting the
metric over the model.

## Known pitfalls (found the hard way)

- **JSON schema property order is real generation order.** Under OpenAI
  structured-outputs strict mode, the model emits keys in the order declared
  in `response_format`'s schema — this is not cosmetic. `prompt.py`'s prose
  DECISION PROCEDURE must match `build_response_format_schema()`'s property
  order exactly, or the model can be anchored toward a default value by
  whatever it already emitted before reaching a field it hasn't "seen" yet
  in the token stream. Concretely: moving `off_topic` too far down the
  schema (after 6 other fields instead of the 4 it actually depends on)
  measurably increased false positives from an already-fixed prior state —
  confirmed by two separate before/after batch runs. Keep the field count
  before any derived boolean to the minimum its own rule needs.
- **Prompt length/dilution is real and measurable**, independent of what the
  new content says. Adding ~550 words of unrelated `topic_tags`
  documentation elsewhere in the prompt measurably degraded `off_topic`
  accuracy even though the OFF_TOPIC section's own text was byte-for-byte
  unchanged (confirmed via `diff` between two pilot runs' system prompts).
  Trimming redundant restatements elsewhere recovered it. Don't pad any
  section defensively — every added sentence has a cost even in unrelated
  sections.
- **`.env` has two `OPENAI_API_KEY` entries** (a default one, and a personal
  one further down under a name comment). `source .env` uses the LAST
  occurrence; `grep OPENAI_API_KEY .env | head -1` picks the WRONG one and
  causes a silent 404 on `batches.retrieve` (batch exists, just under a
  different account/project than the one you're querying with). Always use
  `grep -E '^OPENAI_API_KEY=' .env | tail -1 | cut -d= -f2-`.
- **OpenAI Batch API does not reliably apply prompt caching in practice** —
  measured `cached_tokens: 0` across an entire 132-request batch with a
  fully static >4,000-token system prompt sent first in every request
  (textbook cacheable shape). Likely cause: Batch API fans requests across a
  parallel worker pool, defeating whatever request-locality server-side
  caching needs. Don't assume caching discounts in Batch API cost estimates;
  check `response.body.usage.prompt_tokens_details.cached_tokens` on real
  output if it matters for a decision.

#!/usr/bin/env python3
"""
Phase 1 eval harness runner for the heatmap chunk classifier.

Calls gpt-4o-mini (sync, structured output) on each labeled chunk in
labeled_chunks.yaml and prints per-label precision / recall / F1 plus a
per-chunk error dump.

This is the gate before building the rest of the ingest pipeline. The
plan calls for: macro F1 >= 0.75 AND per-label recall >= 0.60 for the 3
hardest labels. The runner prints both numbers so the user can decide
whether to greenlight Phase 2.

Usage
-----
    poetry run python -m scripts.classifier_eval.runner
    poetry run python -m scripts.classifier_eval.runner --limit 20      # quick smoke
    poetry run python -m scripts.classifier_eval.runner --concurrency 5
    poetry run python -m scripts.classifier_eval.runner --report /path/to/report.md

Environment
-----------
Requires OPENROUTER_API_KEY (or OPENAI_API_KEY) via app.core.config settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.llm.client import get_async_openai_client, get_llm_api_key, normalize_model_name
from scripts.classifier_eval.prompt import (
    SYSTEM_PROMPT,
    build_response_format_schema,
    build_user_message,
)
from scripts.classifier_eval.taxonomy import (
    ALL_CHUNK_LABELS,
    ChunkClassification,
)

logger = logging.getLogger(__name__)

MODEL_DEFAULT = "openai/gpt-4o-mini"
HERE = Path(__file__).resolve().parent
LABELED_CHUNKS_PATH = HERE / "labeled_chunks.yaml"
DEFAULT_REPORT_PATH = HERE / "eval_report.md"

# ── Data model for a labeled chunk ─────────────────────────────────────────────


class LabeledChunk:
    """One row of the labeled eval set."""

    def __init__(self, raw: dict[str, Any]):
        self.id: str = raw["id"]
        self.entity_type: str | None = raw.get("entity_type")
        self.meeting_date: str | None = raw.get("meeting_date")
        self.chunk_text: str = raw["chunk_text"]
        self.expected = ChunkClassification(
            topics=list(raw.get("topics", [])),
            action_types=list(raw.get("action_types", [])),
            subtopics=list(raw.get("subtopics", [])),
            evidence_quote="",  # not eval'd
            off_topic=bool(raw.get("off_topic", False)),
        )


def load_labeled_chunks(path: Path = LABELED_CHUNKS_PATH) -> list[LabeledChunk]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list at top level of {path}; got {type(data)}")
    return [LabeledChunk(item) for item in data]


# ── Classifier call ────────────────────────────────────────────────────────────


class Classifier:
    """Thin wrapper around the OpenAI Chat Completions API with structured output."""

    def __init__(self, model: str = MODEL_DEFAULT, timeout_s: float = 60.0):
        get_llm_api_key()
        self._model = normalize_model_name(model)
        self._client = get_async_openai_client(timeout=timeout_s)

    async def classify(self, chunk: LabeledChunk) -> ChunkClassification:
        user_msg = build_user_message(
            chunk.chunk_text,
            entity_type=chunk.entity_type,
            meeting_date=chunk.meeting_date,
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
            max_completion_tokens=200,
            response_format=build_response_format_schema(),
        )
        raw = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Non-JSON response for chunk {chunk.id}: {exc}\nRaw: {raw!r}"
            ) from exc
        try:
            return ChunkClassification.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(
                f"Schema violation for chunk {chunk.id}: {exc}\nPayload: {payload!r}"
            ) from exc


# ── Per-label set metrics ──────────────────────────────────────────────────────


def _set_metrics(predicted: set[str], expected: set[str]) -> tuple[float, float, float]:
    """Return (precision, recall, f1) for a single multi-label field."""
    if not predicted and not expected:
        return (1.0, 1.0, 1.0)  # both empty = trivially correct
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _per_label_prf(
    predicted_field: list[set[str]], expected_field: list[set[str]], universe: tuple[str, ...]
) -> dict[str, dict[str, float]]:
    """
    For each label in `universe`, compute precision / recall / F1 across
    all chunks where the label is either expected or predicted.

    This is the per-label binary view (one-vs-rest) — the standard way to
    summarize multi-label classifier quality.
    """
    out: dict[str, dict[str, float]] = {}
    for label in universe:
        tp = fp = fn = tn = 0
        for preds, exps in zip(predicted_field, expected_field):
            p = label in preds
            e = label in exps
            if p and e:
                tp += 1
            elif p and not e:
                fp += 1
            elif not p and e:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        support = tp + fn  # how many chunks actually had this label
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(support),
            "tp": float(tp),
            "fp": float(fp),
            "fn": float(fn),
        }
    return out


# ── Runner ──────────────────────────────────────────────────────────────────────


async def run_all(
    chunks: list[LabeledChunk],
    classifier: Classifier,
    *,
    concurrency: int,
    progress_every: int = 10,
) -> tuple[list[tuple[LabeledChunk, ChunkClassification | None, str | None]], float]:
    """Classify every chunk with a bounded concurrency semaphore. Returns (results, elapsed_s)."""
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[LabeledChunk, ChunkClassification | None, str | None]] = [None] * len(chunks)  # type: ignore[list-item]
    done = 0
    started = time.perf_counter()

    async def _one(idx: int, chunk: LabeledChunk) -> None:
        nonlocal done
        async with sem:
            try:
                pred = await classifier.classify(chunk)
                results[idx] = (chunk, pred, None)
            except Exception as exc:  # noqa: BLE001 — capture any failure for the report
                results[idx] = (chunk, None, repr(exc))
        done += 1
        if done % progress_every == 0 or done == len(chunks):
            elapsed = time.perf_counter() - started
            logger.info("Classified %d/%d chunks in %.1fs", done, len(chunks), elapsed)

    await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
    elapsed = time.perf_counter() - started
    return results, elapsed


def build_report(
    results: list[tuple[LabeledChunk, ChunkClassification | None, str | None]],
    elapsed_s: float,
    *,
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Build the markdown eval report and a structured-machine-readable summary."""
    total = len(results)
    failures = [r for r in results if r[1] is None]
    successes: list[tuple[LabeledChunk, ChunkClassification]] = [
        (r[0], r[1]) for r in results if r[1] is not None
    ]

    # Aggregate per-field predictions vs expected.
    by_field: dict[str, tuple[list[set[str]], list[set[str]]]] = {}
    for field, _ in ALL_CHUNK_LABELS:
        preds = [set(getattr(p, field)) for (_, p) in successes]
        exps = [set(getattr(e.expected, field)) for (e, _) in successes]
        by_field[field] = (preds, exps)

    # Overall per-field micro-averaged F1 (treating each chunk as one multiset).
    field_metrics: dict[str, dict[str, float]] = {}
    for field, (preds, exps) in by_field.items():
        precisions = []
        recalls = []
        f1s = []
        for p, e in zip(preds, exps):
            prec, rec, f1 = _set_metrics(p, e)
            precisions.append(prec)
            recalls.append(rec)
            f1s.append(f1)
        n = len(successes) or 1
        field_metrics[field] = {
            "micro_precision": sum(precisions) / n,
            "micro_recall": sum(recalls) / n,
            "micro_f1": sum(f1s) / n,
        }

    # Per-label one-vs-rest precision/recall/F1.
    per_label: dict[str, dict[str, dict[str, float]]] = {}
    for field, universe in ALL_CHUNK_LABELS:
        preds, exps = by_field[field]
        per_label[field] = _per_label_prf(preds, exps, universe)

    # off_topic accuracy.
    off_topic_correct = sum(
        1 for (e, p) in successes if bool(p.off_topic) == bool(e.expected.off_topic)
    )
    off_topic_accuracy = off_topic_correct / len(successes) if successes else 0.0

    # Macro F1 across all chunk-level labels (topics + actions + subtopics).
    macro_f1_values: list[float] = []
    for field in ALL_CHUNK_LABELS:
        for label_metrics in per_label[field[0]].values():
            if label_metrics["support"] > 0:  # only count labels that appear in eval set
                macro_f1_values.append(label_metrics["f1"])
    macro_f1 = sum(macro_f1_values) / len(macro_f1_values) if macro_f1_values else 0.0

    # Identify the 3 hardest labels (lowest recall among labels with support > 0).
    hardest: list[tuple[str, str, float]] = []
    for field, universe in ALL_CHUNK_LABELS:
        for label in universe:
            m = per_label[field][label]
            if m["support"] > 0:
                hardest.append((field, label, m["recall"]))
    hardest.sort(key=lambda t: t[2])
    hardest_3 = hardest[:3]

    # Gate decision.
    gate_passes = macro_f1 >= 0.75 and all(r >= 0.60 for _, _, r in hardest_3)

    # ── Build markdown report ────────────────────────────────────────────────
    lines: list[str] = []
    lines.append("# Heatmap Chunk Classifier — Phase 1 Eval Report\n")
    lines.append(f"- Model: `{model}`")
    lines.append(f"- Labeled chunks: {total}")
    lines.append(f"- Successful calls: {len(successes)}")
    lines.append(f"- Failed calls: {len(failures)}")
    lines.append(f"- Wall-clock: {elapsed_s:.1f}s")
    lines.append(f"- **Macro F1 (chunk-level labels with support > 0): {macro_f1:.3f}**")
    lines.append(f"- **off_topic accuracy: {off_topic_accuracy:.3f}**")
    lines.append("")
    lines.append(f"## Gate decision: **{'PASS' if gate_passes else 'FAIL'}**\n")
    lines.append(f"- Required macro F1 >= 0.75 → actual {macro_f1:.3f}")
    for field, label, recall in hardest_3:
        ok = "PASS" if recall >= 0.60 else "FAIL"
        lines.append(f"- Required recall >= 0.60 for {field}.{label} → actual {recall:.3f} [{ok}]")
    lines.append("")

    lines.append("## Per-field micro-averaged metrics\n")
    lines.append("| Field | Precision | Recall | F1 |")
    lines.append("|---|---|---|---|")
    for field, _ in ALL_CHUNK_LABELS:
        m = field_metrics[field]
        lines.append(
            f"| {field} | {m['micro_precision']:.3f} | {m['micro_recall']:.3f} | {m['micro_f1']:.3f} |"
        )
    lines.append("")

    for field, universe in ALL_CHUNK_LABELS:
        lines.append(f"## Per-label one-vs-rest — {field}\n")
        lines.append("| Label | Precision | Recall | F1 | Support | TP | FP | FN |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for label in universe:
            m = per_label[field][label]
            lines.append(
                f"| {label} | {m['precision']:.3f} | {m['recall']:.3f} | "
                f"{m['f1']:.3f} | {int(m['support'])} | "
                f"{int(m['tp'])} | {int(m['fp'])} | {int(m['fn'])} |"
            )
        lines.append("")

    if failures:
        lines.append("## Failed calls\n")
        for chunk, _, err in failures:
            lines.append(f"- `{chunk.id}` — {err}")
        lines.append("")

    # Per-chunk mismatches for manual review.
    lines.append("## Per-chunk mismatches (for manual review)\n")
    mismatch_count = 0
    for chunk, pred in successes:
        diffs: list[str] = []
        for field, _ in ALL_CHUNK_LABELS:
            p = set(getattr(pred, field))
            e = set(getattr(chunk.expected, field))
            if p != e:
                diffs.append(f"{field}: expected {sorted(e)} got {sorted(p)}")
        if bool(pred.off_topic) != bool(chunk.expected.off_topic):
            diffs.append(
                f"off_topic: expected {chunk.expected.off_topic} got {pred.off_topic}"
            )
        if diffs:
            mismatch_count += 1
            lines.append(f"- **`{chunk.id}`** — " + "; ".join(diffs))
    if mismatch_count == 0:
        lines.append("_No mismatches._")
    lines.append("")

    report_md = "\n".join(lines)

    structured = {
        "model": model,
        "total": total,
        "successes": len(successes),
        "failures": len(failures),
        "elapsed_s": elapsed_s,
        "macro_f1": macro_f1,
        "off_topic_accuracy": off_topic_accuracy,
        "field_metrics": field_metrics,
        "per_label": per_label,
        "hardest_3": [
            {"field": f, "label": l, "recall": r} for f, l, r in hardest_3
        ],
        "gate_passes": gate_passes,
    }
    return report_md, structured


# ── CLI ─────────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0, help="Only classify first N chunks (0 = all)")
    p.add_argument("--concurrency", type=int, default=5, help="Parallel in-flight API calls")
    p.add_argument("--model", default=MODEL_DEFAULT, help="OpenAI model name")
    p.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Where to write the markdown report",
    )
    p.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional path to write the structured JSON summary",
    )
    p.add_argument(
        "--quiet", action="store_true", help="Suppress per-chunk stdout logging"
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    chunks = load_labeled_chunks()
    if args.limit > 0:
        chunks = chunks[: args.limit]
    logger.info("Loaded %d labeled chunks from %s", len(chunks), LABELED_CHUNKS_PATH)

    classifier = Classifier(model=args.model)
    results, elapsed = await run_all(
        chunks, classifier, concurrency=args.concurrency
    )

    report_md, structured = build_report(results, elapsed, model=args.model)

    args.report.write_text(report_md, encoding="utf-8")
    logger.info("Wrote markdown report to %s", args.report)
    if args.json:
        args.json.write_text(
            json.dumps(structured, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Wrote structured JSON to %s", args.json)

    # Always print the headline numbers + gate decision to stdout.
    print()
    print(f"Model: {structured['model']}")
    print(f"Chunks: {structured['total']} ({structured['successes']} ok, {structured['failures']} failed)")
    print(f"Elapsed: {structured['elapsed_s']:.1f}s")
    print(f"Macro F1: {structured['macro_f1']:.3f}  (gate: >= 0.75)")
    print(f"off_topic accuracy: {structured['off_topic_accuracy']:.3f}")
    for h in structured["hardest_3"]:
        gate = "PASS" if h["recall"] >= 0.60 else "FAIL"
        print(
            f"  hardest: {h['field']}.{h['label']} recall={h['recall']:.3f}  (gate: >= 0.60) [{gate}]"
        )
    print()
    print(f"GATE: {'PASS' if structured['gate_passes'] else 'FAIL'}")
    print(f"Report: {args.report}")
    return 0 if structured["gate_passes"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

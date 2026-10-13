#!/usr/bin/env python3
"""Crawler eval harness runner for the schema-driven page classifier.

Evaluates PageClassifier.classify against labeled_pages.yaml. Mirrors
scripts/classifier_eval/runner.py (loader, asyncio.Semaphore concurrency,
per-field metrics, markdown report, PASS/FAIL gate) but evaluates the
page-classifier fields (has_data, has_data_links, data_type, is_archive,
possible_relevant_pages link set) instead of the chunk-classifier labels.

Two run modes:
  - live (default): fetch each page's markdown (cached to fixtures/), call
    PageClassifier.classify, and show per-page delta vs the stored POC
    `predicted:` snapshot.
  - --offline: replay the stored `predicted:` snapshot as the prediction;
    no LLM calls, no cost. Rows without a `predicted:` block (live-only
    negatives) are skipped with a count logged.

Gate (exit code 0 = PASS):
  - has_data F1 >= 0.80
  - data_type macro F1 >= 0.70 (over rows where has_data=true)
  - is_archive recall >= 0.75
  - link-set recall >= 0.60
  - zero hard crashes (every live row returns a valid RelevantPage)

Usage
-----
    poetry run python -m scripts.crawler_eval.runner
    poetry run python -m scripts.crawler_eval.runner --offline      # free, no API
    poetry run python -m scripts.crawler_eval.runner --limit 20      # quick smoke
    poetry run python -m scripts.crawler_eval.runner --refresh      # re-fetch markdown

Environment
-----------
Requires OPENROUTER_API_KEY (or OPENAI_API_KEY) via app.core.config settings
for live mode. --offline needs no API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml

from app.services.web_scraper.page_classifier import PageClassifier
from app.services.web_scraper.page_schemas import DATA_TYPES, RelevantPage
from app.services.web_scraper.schema_driven_crawler import SchemaDrivenCrawler

logger = logging.getLogger(__name__)

MODEL_DEFAULT = "openai/gpt-4o-mini"
HERE = Path(__file__).resolve().parent
LABELED_PAGES_PATH = HERE / "labeled_pages.yaml"
DEFAULT_REPORT_PATH = HERE / "eval_report.md"
FIXTURES_DIR = HERE / "fixtures"

# Per-1M-token USD prices (mirrors scripts/setup_model_pricing.py for the
# models we expect to eval). Used only for the cost estimate in the report.
PRICING_PER_1M: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


def _price_key(model: str) -> str:
    """Strip provider prefix and date suffix to look up PRICING_PER_1M."""
    m = model.split("/", 1)[-1]
    # strip a trailing date like -2024-07-18
    parts = m.split("-")
    if len(parts) >= 3 and parts[-1].isdigit() and len(parts[-1]) == 2 and len(parts[-2]) == 4:
        m = "-".join(parts[:-2])
    return m


# ── Data model for a labeled page ────────────────────────────────────────────


class LabeledPage:
    """One row of the labeled eval set."""

    def __init__(self, raw: dict[str, Any]):
        self.id: str = raw["id"]
        self.school_name: str = raw.get("school_name", "")
        self.url: str = raw["url"]
        # Ground truth
        self.has_data: bool = bool(raw.get("has_data", False))
        self.has_data_links: bool = bool(raw.get("has_data_links", False))
        self.data_type: str | None = raw.get("data_type")
        self.is_archive: bool = bool(raw.get("is_archive", False))
        self.data_years_available: list[int] = list(raw.get("data_years_available", []))
        self.expected_relevant_links: list[dict[str, Any]] = list(
            raw.get("expected_relevant_links", [])
        )
        self.notes: str = raw.get("notes", "")
        # POC prediction snapshot (None for live-only rows).
        self.predicted_snapshot: dict[str, Any] | None = raw.get("predicted")

    @property
    def has_snapshot(self) -> bool:
        return self.predicted_snapshot is not None

    def expected_relevant_link_set(self) -> set[str]:
        return {_normalize_url(l["url"]) for l in self.expected_relevant_links if l.get("url")}


def load_labeled_pages(path: Path = LABELED_PAGES_PATH) -> list[LabeledPage]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list at top level of {path}; got {type(data)}")
    return [LabeledPage(item) for item in data]


# ── Metrics helpers (adapted from scripts/classifier_eval/runner.py) ────────


def _set_metrics(predicted: set[str], expected: set[str]) -> tuple[float, float, float]:
    """Return (precision, recall, f1) for a single set field."""
    if not predicted and not expected:
        return (1.0, 1.0, 1.0)  # both empty = trivially correct
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _bool_prf(
    predicted: list[bool], expected: list[bool]
) -> dict[str, float]:
    """One-vs-rest P/R/F1 for a boolean field (True = positive class)."""
    tp = fp = fn = tn = 0
    for p, e in zip(predicted, expected):
        p = bool(p)
        e = bool(e)
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
    n = len(predicted) or 1
    accuracy = (tp + tn) / n
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "support": float(tp + fn),
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "tn": float(tn),
    }


def _per_label_prf(
    predicted_field: list[str | None],
    expected_field: list[str | None],
    universe: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    """Per-class one-vs-rest P/R/F1 for a single-label field.

    `None` predictions/expectations are treated as "no label" (not matching any
    class in the universe).
    """
    out: dict[str, dict[str, float]] = {}
    for label in universe:
        tp = fp = fn = tn = 0
        for p, e in zip(predicted_field, expected_field):
            p_has = p == label
            e_has = e == label
            if p_has and e_has:
                tp += 1
            elif p_has and not e_has:
                fp += 1
            elif not p_has and e_has:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        support = tp + fn
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


def _normalize_url(url: str) -> str:
    """Normalize a URL for set comparison (strip trailing slash + fragment)."""
    url = (url or "").strip().rstrip("/")
    if "#" in url:
        url = url.split("#", 1)[0]
    return url


def _link_set_metrics(
    predicted_links: list[dict[str, Any]],
    expected_links: set[str],
    *,
    min_confidence: float = 0.0,
    page_url: str | None = None,
) -> tuple[float, float, float]:
    """Set P/R/F1 over predicted link URLs vs expected link URLs.

    `min_confidence` filters predicted links before comparison (confidence
    sweep). Predicted URLs are resolved absolute against `page_url` (when
    provided) before normalization, because the LLM is instructed to return
    link URLs "exactly as they appear in the page markup" — which on most
    school sites are site-relative (e.g. `/SC/school_committee_...`).
    Comparing them raw against the absolute URLs in the labeled set would
    report every relative match as both a miss and a false positive.
    """
    pred_set: set[str] = set()
    for link in predicted_links:
        conf = link.get("confidence", 0.0)
        if conf is None or conf < min_confidence:
            continue
        u = link.get("url")
        if not u:
            continue
        if page_url:
            u = urljoin(page_url, u)
        pred_set.add(_normalize_url(u))
    exp_set = set(expected_links)
    return _set_metrics(pred_set, exp_set)


# ── Classifier wrapper (live + offline) ─────────────────────────────────────


class Classifier:
    """Wraps PageClassifier (live) or a stored snapshot (offline)."""

    def __init__(self, model: str = MODEL_DEFAULT, timeout_s: float = 60.0):
        # PageClassifier resolves model via settings; pass through normalize.
        self._classifier = PageClassifier(model=model, timeout_s=timeout_s)
        self._crawler = SchemaDrivenCrawler(classifier=self._classifier)

    async def fetch_markdown(self, page: LabeledPage, *, refresh: bool) -> str | None:
        """Return cached markdown from fixtures/, fetching if missing or refresh."""
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = FIXTURES_DIR / f"{page.id}.md"
        if cache_path.exists() and not refresh:
            return cache_path.read_text(encoding="utf-8")
        markdown = await self._crawler.fetch_markdown(page.url)
        if markdown is not None:
            cache_path.write_text(markdown, encoding="utf-8")
        return markdown

    async def classify_live(self, page: LabeledPage, markdown: str) -> RelevantPage:
        return await self._classifier.classify(page.url, markdown)


# ── Run loop ─────────────────────────────────────────────────────────────────


# A result row: (page, prediction RelevantPage | None, error str | None, meta dict)
ResultRow = tuple[LabeledPage, RelevantPage | None, str | None, dict[str, Any]]


async def run_live(
    pages: list[LabeledPage],
    classifier: Classifier,
    *,
    concurrency: int,
    refresh: bool,
    progress_every: int = 5,
) -> tuple[list[ResultRow], float]:
    """Live mode: fetch markdown (cached) + classify each page."""
    sem = asyncio.Semaphore(concurrency)
    results: list[ResultRow] = [None] * len(pages)  # type: ignore[list-item]
    done = 0
    started = time.perf_counter()

    async def _one(idx: int, page: LabeledPage) -> None:
        nonlocal done
        async with sem:
            meta: dict[str, Any] = {}
            try:
                markdown = await classifier.fetch_markdown(page, refresh=refresh)
                if markdown is None:
                    results[idx] = (page, None, "fetch_failed: no markdown", meta)
                    return
                pred = await classifier.classify_live(page, markdown)
                meta = dict(classifier._classifier.last_response_meta or {})
                results[idx] = (page, pred, None, meta)
            except Exception as exc:  # noqa: BLE001
                results[idx] = (page, None, repr(exc), {})
        done += 1
        if done % progress_every == 0 or done == len(pages):
            elapsed = time.perf_counter() - started
            logger.info("Classified %d/%d pages in %.1fs", done, len(pages), elapsed)

    await asyncio.gather(*[_one(i, p) for i, p in enumerate(pages)])
    elapsed = time.perf_counter() - started
    return results, elapsed


def run_offline(pages: list[LabeledPage]) -> list[ResultRow]:
    """Offline mode: replay stored `predicted:` snapshots. No LLM calls."""
    results: list[ResultRow] = []
    skipped = 0
    for page in pages:
        if not page.has_snapshot:
            skipped += 1
            continue
        try:
            pred = RelevantPage.model_validate(
                {
                    "url": page.url,
                    "title": "",
                    "has_data": page.predicted_snapshot.get("has_data", False),
                    "has_data_links": page.predicted_snapshot.get("has_data_links", False),
                    "description": None,
                    "data_page_info": (
                        {
                            "data_type": page.predicted_snapshot.get("data_type") or "unknown",
                            "is_archive": page.predicted_snapshot.get("is_archive", False),
                            "data_years_available": page.predicted_snapshot.get(
                                "data_years_available", []
                            )
                            or [],
                            "confidence": 1.0,
                        }
                        if page.predicted_snapshot.get("has_data")
                        else None
                    ),
                    "possible_relevant_pages": [],
                }
            )
            results.append((page, pred, None, {}))
        except Exception as exc:  # noqa: BLE001
            results.append((page, None, repr(exc), {}))
    if skipped:
        logger.info("Offline mode skipped %d live-only rows (no predicted snapshot).", skipped)
    return results


# ── Report builder ───────────────────────────────────────────────────────────


def _extract_pred(page: LabeledPage, pred: RelevantPage) -> dict[str, Any]:
    """Pull the prediction fields into a flat dict for metrics + delta."""
    return {
        "has_data": pred.has_data,
        "has_data_links": pred.has_data_links,
        "data_type": pred.data_page_info.data_type if pred.data_page_info else None,
        "is_archive": pred.data_page_info.is_archive if pred.data_page_info else False,
        "data_years_available": (
            pred.data_page_info.data_years_available if pred.data_page_info else []
        ),
        "links": [
            {"url": l.url, "confidence": l.confidence, "reason": l.reason}
            for l in pred.possible_relevant_pages
        ],
    }


def _extract_expected(page: LabeledPage) -> dict[str, Any]:
    return {
        "has_data": page.has_data,
        "has_data_links": page.has_data_links,
        "data_type": page.data_type,
        "is_archive": page.is_archive,
        "data_years_available": page.data_years_available,
        "links": page.expected_relevant_links,
    }


def build_report(
    results: list[ResultRow],
    elapsed_s: float,
    *,
    model: str,
    offline: bool,
) -> tuple[str, dict[str, Any]]:
    """Build the markdown eval report and structured JSON summary."""
    total = len(results)
    failures = [r for r in results if r[1] is None]
    successes: list[tuple[LabeledPage, RelevantPage]] = [
        (r[0], r[1]) for r in results if r[1] is not None
    ]

    # ── Boolean fields (has_data, has_data_links) over all successes ──────────
    has_data_metrics = _bool_prf(
        [p.has_data for (_, p) in successes],
        [e.has_data for (e, _) in successes],
    )
    has_data_links_metrics = _bool_prf(
        [p.has_data_links for (_, p) in successes],
        [e.has_data_links for (e, _) in successes],
    )

    # ── data_type + is_archive: only over rows where expected has_data=true ──
    data_rows = [(e, p) for (e, p) in successes if e.has_data]
    data_type_preds = [p.data_page_info.data_type if p.data_page_info else None for (_, p) in data_rows]
    data_type_exps = [e.data_type for (e, _) in data_rows]
    data_type_per_label = _per_label_prf(data_type_preds, data_type_exps, DATA_TYPES)
    # macro F1 over labels with support > 0
    macro_f1_values = [
        m["f1"] for m in data_type_per_label.values() if m["support"] > 0
    ]
    data_type_macro_f1 = sum(macro_f1_values) / len(macro_f1_values) if macro_f1_values else 0.0

    is_archive_metrics = _bool_prf(
        [p.data_page_info.is_archive if p.data_page_info else False for (_, p) in data_rows],
        [e.is_archive for (e, _) in data_rows],
    )

    # ── Link-set metrics (with a min_confidence sweep at 0.5) ─────────────────
    link_metrics_per_row = []
    link_set_recall = 0.0
    link_set_precision = 0.0
    link_set_f1 = 0.0
    rows_with_links = 0
    for (e, p) in successes:
        expected_set = e.expected_relevant_link_set()
        if not expected_set:
            continue
        rows_with_links += 1
        pred_links = [
            {"url": l.url, "confidence": l.confidence} for l in p.possible_relevant_pages
        ]
        prec, rec, f1 = _link_set_metrics(
            pred_links, expected_set, min_confidence=0.5, page_url=e.url
        )
        link_metrics_per_row.append(
            {"id": e.id, "precision": prec, "recall": rec, "f1": f1,
             "n_expected": len(expected_set),
             "n_predicted": len([l for l in pred_links if (l["confidence"] or 0) >= 0.5])}
        )
        link_set_recall += rec
        link_set_precision += prec
        link_set_f1 += f1
    if rows_with_links:
        link_set_recall /= rows_with_links
        link_set_precision /= rows_with_links
        link_set_f1 /= rows_with_links

    # Confidence sweep: recall at thresholds 0.3, 0.5, 0.7, 0.9
    confidence_sweep: dict[float, float] = {}
    for thresh in (0.3, 0.5, 0.7, 0.9):
        recalls = []
        for (e, p) in successes:
            expected_set = e.expected_relevant_link_set()
            if not expected_set:
                continue
            pred_links = [
                {"url": l.url, "confidence": l.confidence} for l in p.possible_relevant_pages
            ]
            _, rec, _ = _link_set_metrics(
                pred_links, expected_set, min_confidence=thresh, page_url=e.url
            )
            recalls.append(rec)
        confidence_sweep[thresh] = sum(recalls) / len(recalls) if recalls else 0.0

    # ── Truncation + cost (live mode only) ────────────────────────────────────
    truncation_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    for (_, _, _, meta) in results:
        if not meta:
            continue
        if meta.get("finish_reason") == "length":
            truncation_count += 1
        prompt_tokens += int(meta.get("prompt_tokens", 0) or 0)
        completion_tokens += int(meta.get("completion_tokens", 0) or 0)
    live_rows_with_meta = sum(1 for r in results if r[3])
    truncation_rate = (
        truncation_count / live_rows_with_meta if live_rows_with_meta else 0.0
    )
    price = PRICING_PER_1M.get(_price_key(model), {"input": 0.0, "output": 0.0})
    input_cost = (prompt_tokens / 1_000_000) * price["input"]
    output_cost = (completion_tokens / 1_000_000) * price["output"]
    total_cost = input_cost + output_cost

    # ── Gate decision ──────────────────────────────────────────────────────────
    has_data_f1 = has_data_metrics["f1"]
    is_archive_recall = is_archive_metrics["recall"]
    gate_criteria = [
        ("has_data F1", has_data_f1, 0.80),
        ("data_type macro F1", data_type_macro_f1, 0.70),
        ("is_archive recall", is_archive_recall, 0.75),
        ("link-set recall", link_set_recall, 0.60),
        ("zero hard crashes", float(len(failures) == 0), 1.0),
    ]
    gate_passes = all(actual >= required for _, actual, required in gate_criteria)

    # ── Markdown report ────────────────────────────────────────────────────────
    lines: list[str] = []
    mode_label = "offline (replayed POC snapshots)" if offline else "live (fetched + classified)"
    lines.append("# Schema-Driven Page Classifier — Eval Report\n")
    lines.append(f"- Model: `{model}`")
    lines.append(f"- Mode: {mode_label}")
    lines.append(f"- Labeled pages: {total}")
    lines.append(f"- Successful rows: {len(successes)}")
    lines.append(f"- Failed rows: {len(failures)}")
    lines.append(f"- Wall-clock: {elapsed_s:.1f}s")
    lines.append(f"- **has_data F1: {has_data_f1:.3f}**")
    lines.append(f"- **data_type macro F1 (has_data=true rows): {data_type_macro_f1:.3f}**")
    lines.append(f"- **is_archive recall (has_data=true rows): {is_archive_recall:.3f}**")
    lines.append(f"- **link-set recall (conf>=0.5): {link_set_recall:.3f}**")
    if not offline:
        lines.append(f"- Truncation rate (finish_reason=length): {truncation_rate:.1%}")
        lines.append(
            f"- Tokens: {prompt_tokens} in / {completion_tokens} out — est cost ${total_cost:.4f}"
        )
    lines.append("")
    lines.append(f"## Gate decision: **{'PASS' if gate_passes else 'FAIL'}**\n")
    for name, actual, required in gate_criteria:
        ok = "PASS" if actual >= required else "FAIL"
        if name == "zero hard crashes":
            lines.append(f"- Required {name} → {len(failures)} failures [{ok}]")
        else:
            lines.append(f"- Required {name} >= {required:.2f} → actual {actual:.3f} [{ok}]")
    lines.append("")

    # Per-field micro-averaged table
    lines.append("## Per-field metrics\n")
    lines.append("| Field | Precision | Recall | F1 | Accuracy | Support |")
    lines.append("|---|---|---|---|---|---|")
    for name, m in (
        ("has_data", has_data_metrics),
        ("has_data_links", has_data_links_metrics),
        ("is_archive (has_data=true rows)", is_archive_metrics),
    ):
        lines.append(
            f"| {name} | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['f1']:.3f} | {m['accuracy']:.3f} | {int(m['support'])} |"
        )
    lines.append(
        f"| link-set (conf>=0.5) | {link_set_precision:.3f} | {link_set_recall:.3f} | "
        f"{link_set_f1:.3f} | — | {rows_with_links} |"
    )
    lines.append("")

    # data_type per-label table
    lines.append("## data_type — per-label one-vs-rest (over has_data=true rows)\n")
    lines.append("| Label | Precision | Recall | F1 | Support | TP | FP | FN |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for label in DATA_TYPES:
        m = data_type_per_label[label]
        lines.append(
            f"| {label} | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['f1']:.3f} | {int(m['support'])} | "
            f"{int(m['tp'])} | {int(m['fp'])} | {int(m['fn'])} |"
        )
    lines.append(f"\n**Macro F1 (labels with support > 0): {data_type_macro_f1:.3f}**\n")

    # Confidence sweep
    lines.append("## Link-set confidence sweep (recall by min_confidence)\n")
    lines.append("| min_confidence | recall |")
    lines.append("|---|---|")
    for thresh, rec in confidence_sweep.items():
        lines.append(f"| {thresh:.1f} | {rec:.3f} |")
    lines.append("")

    # Truncation / cost summary (live only)
    if not offline:
        lines.append("## Truncation & cost\n")
        lines.append(f"- Rows with response metadata: {live_rows_with_meta}")
        lines.append(f"- Truncated (finish_reason=length): {truncation_count} ({truncation_rate:.1%})")
        lines.append(f"- Prompt tokens (total): {prompt_tokens}")
        lines.append(f"- Completion tokens (total): {completion_tokens}")
        lines.append(f"- Estimated cost: ${total_cost:.4f}")
        lines.append("")

    if failures:
        lines.append("## Failed rows\n")
        for page, _, err, _ in failures:
            lines.append(f"- `{page.id}` ({page.url}) — {err}")
        lines.append("")

    # Per-page mismatches (with live-vs-snapshot delta in live mode)
    lines.append("## Per-page mismatches (for manual review)\n")
    mismatch_count = 0
    for (e, p) in successes:
        exp = _extract_expected(e)
        pred = _extract_pred(e, p)
        diffs: list[str] = []
        for field in ("has_data", "has_data_links", "data_type", "is_archive"):
            if exp[field] != pred[field]:
                diffs.append(f"{field}: expected {exp[field]!r} got {pred[field]!r}")
        # link-set diff
        expected_set = e.expected_relevant_link_set()
        if expected_set:
            pred_link_set = {
                _normalize_url(urljoin(e.url, l["url"]) if l.get("url") else "")
                for l in pred["links"]
                if (l.get("confidence") or 0) >= 0.5
            }
            pred_link_set.discard(_normalize_url(""))
            if pred_link_set != expected_set:
                missing = sorted(expected_set - pred_link_set)
                extra = sorted(pred_link_set - expected_set)
                if missing:
                    diffs.append(f"links missing: {missing}")
                if extra:
                    diffs.append(f"links extra: {extra}")
        # live-vs-snapshot delta
        delta_str = ""
        if not offline and e.has_snapshot:
            snap = e.predicted_snapshot or {}
            snap_fields = []
            for field in ("has_data", "has_data_links", "data_type", "is_archive"):
                if snap.get(field) != pred[field]:
                    snap_fields.append(
                        f"{field}: snapshot {snap.get(field)!r} -> live {pred[field]!r}"
                    )
            if snap_fields:
                delta_str = " | delta: " + "; ".join(snap_fields)
        if diffs:
            mismatch_count += 1
            lines.append(f"- **`{e.id}`** ({e.url}) — " + "; ".join(diffs) + delta_str)
    if mismatch_count == 0:
        lines.append("_No mismatches._")
    lines.append("")

    report_md = "\n".join(lines)

    structured = {
        "model": model,
        "mode": "offline" if offline else "live",
        "total": total,
        "successes": len(successes),
        "failures": len(failures),
        "elapsed_s": elapsed_s,
        "has_data_f1": has_data_f1,
        "has_data_links_f1": has_data_links_metrics["f1"],
        "data_type_macro_f1": data_type_macro_f1,
        "is_archive_recall": is_archive_recall,
        "link_set_recall": link_set_recall,
        "link_set_precision": link_set_precision,
        "link_set_f1": link_set_f1,
        "confidence_sweep": confidence_sweep,
        "truncation_rate": truncation_rate,
        "truncation_count": truncation_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": total_cost,
        "data_type_per_label": data_type_per_label,
        "gate_passes": gate_passes,
        "gate_criteria": [
            {"name": n, "actual": a, "required": r} for n, a, r in gate_criteria
        ],
    }
    return report_md, structured


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0, help="Only classify first N pages (0 = all)")
    p.add_argument("--concurrency", type=int, default=5, help="Parallel in-flight API calls (live mode)")
    p.add_argument("--model", default=MODEL_DEFAULT, help="OpenAI/OpenRouter model name")
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
    p.add_argument("--quiet", action="store_true", help="Suppress per-page stdout logging")
    p.add_argument(
        "--offline",
        action="store_true",
        help="Replay stored predicted: snapshots; no LLM calls. Skips live-only rows.",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch markdown for every page (ignore fixtures/ cache). Live mode only.",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    pages = load_labeled_pages()
    if args.limit > 0:
        pages = pages[: args.limit]
    logger.info("Loaded %d labeled pages from %s", len(pages), LABELED_PAGES_PATH)

    if args.offline:
        results = run_offline(pages)
        elapsed = 0.0
    else:
        classifier = Classifier(model=args.model)
        results, elapsed = await run_live(
            pages, classifier, concurrency=args.concurrency, refresh=args.refresh
        )

    report_md, structured = build_report(
        results, elapsed, model=args.model, offline=args.offline
    )

    args.report.write_text(report_md, encoding="utf-8")
    logger.info("Wrote markdown report to %s", args.report)
    if args.json:
        args.json.write_text(
            json.dumps(structured, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Wrote structured JSON to %s", args.json)

    print()
    print(f"Model: {structured['model']}  (mode: {structured['mode']})")
    print(f"Pages: {structured['total']} ({structured['successes']} ok, {structured['failures']} failed)")
    if not args.offline:
        print(f"Elapsed: {structured['elapsed_s']:.1f}s")
    print(f"has_data F1: {structured['has_data_f1']:.3f}  (gate: >= 0.80)")
    print(f"data_type macro F1: {structured['data_type_macro_f1']:.3f}  (gate: >= 0.70)")
    print(f"is_archive recall: {structured['is_archive_recall']:.3f}  (gate: >= 0.75)")
    print(f"link-set recall: {structured['link_set_recall']:.3f}  (gate: >= 0.60)")
    if not args.offline:
        print(f"Truncation: {structured['truncation_count']} ({structured['truncation_rate']:.1%})")
        print(f"Cost: ${structured['estimated_cost_usd']:.4f}")
    print()
    print(f"GATE: {'PASS' if structured['gate_passes'] else 'FAIL'}")
    print(f"Report: {args.report}")
    return 0 if structured["gate_passes"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

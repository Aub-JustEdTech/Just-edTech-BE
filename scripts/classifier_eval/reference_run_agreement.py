"""
Measure agreement between the current chunk classifier run (live Qdrant
collection `justedtech_2_documents`) and an earlier run of the same
pipeline restored from a teammate's snapshot into
`justedtech_2_documents_yug_reference` (name fixed to match the existing
Qdrant collection restored from that snapshot — not renamed here to avoid
breaking the live data dependency).

IMPORTANT CAVEAT: the "reference" collection is NOT human-verified ground
truth. Its payload schema (topic_tags/topics/action_types/subtopics/
off_topic/classified/classified_at) and document_id format exactly match
this codebase's own output, and it was taken 2026-08-04 -- it is an
earlier automated run of the same (or a very similar) LLM classifier over
the same 20-district corpus. This script therefore measures RUN-TO-RUN
AGREEMENT / DRIFT, not accuracy against reality. Treat "false positive" /
"false negative" here as "live tagged something the earlier run didn't"
and vice versa -- useful for catching regressions, prompt/model drift, or
non-determinism, not a substitute for a human-labeled eval set (see
labeled_chunks.yaml / eval_report.md for that).

Matching strategy:
  1. Join on (document_id, chunk_index).
  2. Only compare documents where the reference and live runs produced the
     *same set* of chunk_index values (chunking may have drifted for some
     docs between the two ingestion runs -- e.g. a chunker parameter
     change -- and misaligned indices would silently compare unrelated
     text). Docs with a different chunk count are excluded and counted.
  3. Within a matched (document_id, chunk_index) pair, verify the chunk
     `text` is actually identical (whitespace-normalized). A pair whose
     text drifted despite matching indices is excluded and counted --
     comparing tags for different underlying text would be meaningless.

Run (inside the api container, where qdrant-client + app settings live):
    docker compose exec -T api python scripts/classifier_eval/reference_run_agreement.py

Writes scripts/classifier_eval/reference_agreement_report.md.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from qdrant_client import QdrantClient

from app.services.heatmap_ingest.taxonomy import ACTION_TYPES, SEX_ED_SUBTOPICS, TOPICS

# Matches an existing Qdrant collection name; not renamed to avoid breaking
# the live restored-snapshot dependency.
REFERENCE_COLLECTION = "justedtech_2_documents_yug_reference"
LIVE_COLLECTION = "justedtech_2_documents"
QDRANT_URL = "http://qdrant:6333"

FIELDS = [
    "document_id",
    "chunk_index",
    "text",
    "topics",
    "action_types",
    "subtopics",
    "off_topic",
]

ALL_CHUNK_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("topics", TOPICS),
    ("action_types", ACTION_TYPES),
    ("subtopics", SEX_ED_SUBTOPICS),
)

_WS_RE = re.compile(r"\s+")


def _norm_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "")).strip()


@dataclass
class ChunkRecord:
    document_id: str
    chunk_index: int
    text: str
    topics: set[str]
    action_types: set[str]
    subtopics: set[str]
    off_topic: bool


@dataclass
class MatchedPair:
    document_id: str
    chunk_index: int
    text: str
    ref: ChunkRecord
    live: ChunkRecord


def _scroll_all(client: QdrantClient, collection: str) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=1000,
            offset=offset,
            with_payload=FIELDS,
            with_vectors=False,
        )
        for p in batch:
            pl = p.payload
            records.append(
                ChunkRecord(
                    document_id=pl["document_id"],
                    chunk_index=pl["chunk_index"],
                    text=pl.get("text", "") or "",
                    topics=set(pl.get("topics") or []),
                    action_types=set(pl.get("action_types") or []),
                    subtopics=set(pl.get("subtopics") or []),
                    off_topic=bool(pl.get("off_topic")),
                )
            )
        if offset is None:
            break
    return records


def build_matched_pairs(
    ref_records: list[ChunkRecord], live_records: list[ChunkRecord]
) -> tuple[list[MatchedPair], dict[str, int]]:
    """Join ref/live on (document_id, chunk_index), filtering to documents
    with identical chunking and chunks with identical text."""
    ref_by_doc: dict[str, dict[int, ChunkRecord]] = defaultdict(dict)
    for r in ref_records:
        ref_by_doc[r.document_id][r.chunk_index] = r
    live_by_doc: dict[str, dict[int, ChunkRecord]] = defaultdict(dict)
    for r in live_records:
        live_by_doc[r.document_id][r.chunk_index] = r

    stats = {
        "ref_documents": len(ref_by_doc),
        "live_documents": len(live_by_doc),
        "overlapping_documents": 0,
        "docs_excluded_chunking_mismatch": 0,
        "docs_included_identical_chunking": 0,
        "chunks_excluded_text_drift": 0,
        "chunks_matched": 0,
    }

    pairs: list[MatchedPair] = []
    overlap_docs = set(ref_by_doc) & set(live_by_doc)
    stats["overlapping_documents"] = len(overlap_docs)

    for doc_id in overlap_docs:
        ref_chunks = ref_by_doc[doc_id]
        live_chunks = live_by_doc[doc_id]
        if set(ref_chunks.keys()) != set(live_chunks.keys()):
            stats["docs_excluded_chunking_mismatch"] += 1
            continue
        stats["docs_included_identical_chunking"] += 1
        for idx, ref_rec in ref_chunks.items():
            live_rec = live_chunks[idx]
            if _norm_text(ref_rec.text) != _norm_text(live_rec.text):
                stats["chunks_excluded_text_drift"] += 1
                continue
            pairs.append(
                MatchedPair(
                    document_id=doc_id,
                    chunk_index=idx,
                    text=ref_rec.text,
                    ref=ref_rec,
                    live=live_rec,
                )
            )
            stats["chunks_matched"] += 1

    return pairs, stats


def _set_metrics(predicted: set[str], expected: set[str]) -> tuple[float, float, float]:
    if not predicted and not expected:
        return (1.0, 1.0, 1.0)
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
    out: dict[str, dict[str, float]] = {}
    for label in universe:
        tp = fp = fn = tn = 0
        for preds, exps in zip(predicted_field, expected_field, strict=True):
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
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(tp + fn),
            "tp": float(tp),
            "fp": float(fp),
            "fn": float(fn),
            "tn": float(tn),
        }
    return out


def build_report(pairs: list[MatchedPair], stats: dict[str, int]) -> str:
    by_field: dict[str, tuple[list[set[str]], list[set[str]]]] = {}
    for field_name, _ in ALL_CHUNK_LABELS:
        preds = [getattr(pr.live, field_name) for pr in pairs]
        exps = [getattr(pr.ref, field_name) for pr in pairs]
        by_field[field_name] = (preds, exps)

    field_metrics: dict[str, dict[str, float]] = {}
    for field_name, (preds, exps) in by_field.items():
        precisions, recalls, f1s = [], [], []
        for p, e in zip(preds, exps, strict=True):
            prec, rec, f1 = _set_metrics(p, e)
            precisions.append(prec)
            recalls.append(rec)
            f1s.append(f1)
        n = len(pairs) or 1
        field_metrics[field_name] = {
            "micro_precision": sum(precisions) / n,
            "micro_recall": sum(recalls) / n,
            "micro_f1": sum(f1s) / n,
        }

    per_label: dict[str, dict[str, dict[str, float]]] = {}
    for field_name, universe in ALL_CHUNK_LABELS:
        preds, exps = by_field[field_name]
        per_label[field_name] = _per_label_prf(preds, exps, universe)

    off_topic_agree = sum(1 for pr in pairs if pr.live.off_topic == pr.ref.off_topic)
    off_topic_agreement_rate = off_topic_agree / len(pairs) if pairs else 0.0
    off_topic_ref_true_live_false = sum(
        1 for pr in pairs if pr.ref.off_topic and not pr.live.off_topic
    )
    off_topic_ref_false_live_true = sum(
        1 for pr in pairs if not pr.ref.off_topic and pr.live.off_topic
    )

    macro_f1_values = [
        m["f1"]
        for field_name, _ in ALL_CHUNK_LABELS
        for m in per_label[field_name].values()
        if m["support"] > 0
    ]
    macro_f1 = sum(macro_f1_values) / len(macro_f1_values) if macro_f1_values else 0.0

    lines: list[str] = []
    lines.append("# Chunk Classifier — Run-to-Run Agreement Report\n")
    lines.append(
        "**Caveat:** the reference collection is an earlier automated run of "
        "this same LLM classifier (2026-08-04 snapshot), not human-verified "
        "ground truth. This measures agreement/drift between two runs, not "
        "real-world accuracy. For a true accuracy gate, see "
        "`scripts/classifier_eval/eval_report.md` (hand-labeled set).\n"
    )
    lines.append("## Matching coverage\n")
    lines.append(f"- Reference documents: {stats['ref_documents']}")
    lines.append(f"- Live documents: {stats['live_documents']}")
    lines.append(f"- Overlapping documents: {stats['overlapping_documents']}")
    lines.append(
        f"- Excluded (chunking drifted between runs): "
        f"{stats['docs_excluded_chunking_mismatch']}"
    )
    lines.append(
        f"- Included (identical chunk_index sets): "
        f"{stats['docs_included_identical_chunking']}"
    )
    lines.append(
        f"- Chunks excluded (text drifted despite matching index): "
        f"{stats['chunks_excluded_text_drift']}"
    )
    lines.append(f"- **Chunks compared: {stats['chunks_matched']}**\n")

    lines.append("## Headline numbers\n")
    lines.append(f"- **Macro F1 (labels with support > 0): {macro_f1:.3f}**")
    lines.append(f"- **off_topic agreement rate: {off_topic_agreement_rate:.3f}**")
    lines.append(
        f"  - reference=off_topic, live=on_topic (live surfaced something "
        f"the earlier run missed): {off_topic_ref_true_live_false}"
    )
    lines.append(
        f"  - reference=on_topic, live=off_topic (live now misses something "
        f"the earlier run caught): {off_topic_ref_false_live_true}"
    )
    lines.append("")

    lines.append("## Per-field micro-averaged agreement\n")
    lines.append("| Field | Precision | Recall | F1 |")
    lines.append("|---|---|---|---|")
    for field_name, _ in ALL_CHUNK_LABELS:
        m = field_metrics[field_name]
        lines.append(
            f"| {field_name} | {m['micro_precision']:.3f} | "
            f"{m['micro_recall']:.3f} | {m['micro_f1']:.3f} |"
        )
    lines.append("")

    for field_name, universe in ALL_CHUNK_LABELS:
        lines.append(f"## Per-label one-vs-rest — {field_name}\n")
        lines.append("| Label | Precision | Recall | F1 | Support | TP | FP | FN | TN |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for label in universe:
            m = per_label[field_name][label]
            lines.append(
                f"| {label} | {m['precision']:.3f} | {m['recall']:.3f} | "
                f"{m['f1']:.3f} | {int(m['support'])} | {int(m['tp'])} | "
                f"{int(m['fp'])} | {int(m['fn'])} | {int(m['tn'])} |"
            )
        lines.append("")

    lines.append("## off_topic disagreements (all cases)\n")
    lines.append(
        "`ref->live` shows the flip direction. All chunks below had "
        "identical, empty topics/action_types/subtopics on both sides -- "
        "the *only* disagreement is whether the chunk counts as on-topic "
        "at all.\n"
    )
    off_topic_mismatches = [pr for pr in pairs if pr.live.off_topic != pr.ref.off_topic]
    for pr in off_topic_mismatches:
        snippet = _norm_text(pr.text)[:220]
        direction = f"{pr.ref.off_topic}->{pr.live.off_topic}"
        lines.append(
            f"- **{direction}** `{pr.document_id[:24]}...` chunk "
            f"{pr.chunk_index}: \"{snippet}...\""
        )
    lines.append("")

    lines.append("## False-positive / false-negative examples\n")
    lines.append(
        "Up to 5 examples per label. False positive = live tagged it, "
        "reference didn't. False negative = reference tagged it, live "
        "didn't. (Empty below means zero disagreements found for that "
        "label across all 3,323 compared chunks.)\n"
    )
    for field_name, universe in ALL_CHUNK_LABELS:
        preds, exps = by_field[field_name]
        for label in universe:
            fps = [
                pr
                for pr, p, e in zip(pairs, preds, exps, strict=True)
                if label in p and label not in e
            ][:5]
            fns = [
                pr
                for pr, p, e in zip(pairs, preds, exps, strict=True)
                if label in e and label not in p
            ][:5]
            if not fps and not fns:
                continue
            lines.append(f"### {field_name}.{label}\n")
            for pr in fps:
                snippet = _norm_text(pr.text)[:220]
                lines.append(
                    f"- **FP** `{pr.document_id[:24]}...` chunk {pr.chunk_index}: "
                    f"live={sorted(getattr(pr.live, field_name))} "
                    f"ref={sorted(getattr(pr.ref, field_name))} — \"{snippet}...\""
                )
            for pr in fns:
                snippet = _norm_text(pr.text)[:220]
                lines.append(
                    f"- **FN** `{pr.document_id[:24]}...` chunk {pr.chunk_index}: "
                    f"live={sorted(getattr(pr.live, field_name))} "
                    f"ref={sorted(getattr(pr.ref, field_name))} — \"{snippet}...\""
                )
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    client = QdrantClient(url=QDRANT_URL, check_compatibility=False)
    print(f"Scrolling {REFERENCE_COLLECTION}...")
    ref_records = _scroll_all(client, REFERENCE_COLLECTION)
    print(f"  {len(ref_records)} reference chunks")
    print(f"Scrolling {LIVE_COLLECTION}...")
    live_records = _scroll_all(client, LIVE_COLLECTION)
    print(f"  {len(live_records)} live chunks")

    pairs, stats = build_matched_pairs(ref_records, live_records)
    print(f"Matched {len(pairs)} comparable chunk pairs. Stats: {stats}")

    report = build_report(pairs, stats)
    # scripts/ is mounted read-only in the container; write to the writable
    # temp_uploads volume instead and copy out from the host if needed.
    out_path = "/app/temp_uploads/reference_agreement_report.md"
    with open(out_path, "w") as f:
        f.write(report)
    print(f"Wrote {out_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()

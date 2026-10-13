"""
Deterministic scorer for classifier runs against a neutral ground truth.

Reproduces exactly what was used to score run1 (old prompt) vs run2 (new v3
prompt) in this directory, so future runs (run3, ...) are scored with
identical formulas and are genuinely comparable — no hand-recomputation.

Ground truth is produced separately by a human/Claude following
`ground_truth_protocol.md`; this script only consumes it.

Usage:
    python score_run.py \\
        --gt ground_truth.json \\
        --text-source ../run1/input.jsonl \\
        --run run1=../run1/batch_6a7bf8d3d76481908db230c83ea36fa6_output\\ \\(1\\).jsonl \\
        --run run2=../run2/batch_6a7dd0cb6b0c8190849fe3e98cdbf41a_output.jsonl \\
        --out-csv per_chunk_comparison.csv \\
        --out-json scoring_results.json

Each --run is NAME=PATH to that run's OpenAI Batch API output JSONL
(one line per chunk: {"custom_id": ..., "response": {"body": {"choices": [
{"message": {"content": "<json string>"}}]}}}).

--text-source is any batch INPUT jsonl covering the same custom_ids, used
only to pull a short chunk-text preview into the CSV; it does not affect
scoring.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

Z = 1.96  # 95% Wilson score interval


def wilson_ci(successes: int, n: int) -> tuple[float | None, float | None]:
    if n == 0:
        return (None, None)
    p = successes / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = (Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def prf1(tp: int, fp: int, fn: int) -> dict:
    n_p = tp + fp
    n_r = tp + fn
    precision = tp / n_p if n_p else None
    recall = tp / n_r if n_r else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "precision_n": n_p,
        "precision_ci": list(wilson_ci(tp, n_p)),
        "recall": recall,
        "recall_n": n_r,
        "recall_ci": list(wilson_ci(tp, n_r)),
        "f1": f1,
    }


def tag_str(tags: list[dict]) -> str:
    return "; ".join(sorted(f"{t['category']}.{t['subtopic']}" for t in tags))


def load_run_output(path: str) -> dict[str, dict]:
    """custom_id -> parsed classification dict"""
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("error"):
                continue
            content = row["response"]["body"]["choices"][0]["message"]["content"]
            out[row["custom_id"]] = json.loads(content)
    return out


def load_text_previews(path: str, custom_ids: set[str]) -> dict[str, str]:
    previews = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("custom_id")
            if cid not in custom_ids or cid in previews:
                continue
            for msg in row["body"]["messages"]:
                if msg["role"] == "user":
                    content = msg["content"]
                    idx = content.rfind("CHUNK:")
                    text = content[idx + len("CHUNK:") :].strip() if idx != -1 else content
                    previews[cid] = text.replace("\n", " ")[:200]
                    break
    return previews


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True, help="ground_truth.json")
    ap.add_argument("--text-source", required=True, help="a batch input jsonl for chunk-text previews")
    ap.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="repeatable: run name and its batch output jsonl path",
    )
    ap.add_argument("--out-csv", default="per_chunk_comparison.csv")
    ap.add_argument("--out-json", default="scoring_results.json")
    args = ap.parse_args()

    gt_list = json.load(open(args.gt))
    gt = {g["custom_id"]: g for g in gt_list}
    custom_ids = list(gt.keys())

    runs = {}
    for spec in args.run:
        name, path = spec.split("=", 1)
        runs[name] = load_run_output(path)

    previews = load_text_previews(args.text_source, set(custom_ids))

    # ---- per-chunk CSV ----
    fieldnames = ["custom_id", "chunk_text_preview", "gt_off_topic", "gt_topics", "gt_topic_tags", "gt_confidence", "gt_note"]
    for name in runs:
        fieldnames += [f"{name}_off_topic", f"{name}_topics", f"{name}_topic_tags"]
    for name in runs:
        fieldnames += [f"{name}_off_topic_correct", f"{name}_topics_exact_match", f"{name}_tags_exact_match"]

    rows_out = []
    for cid in custom_ids:
        g = gt[cid]
        row = {
            "custom_id": cid,
            "chunk_text_preview": previews.get(cid, ""),
            "gt_off_topic": g["off_topic"],
            "gt_topics": "; ".join(sorted(g["topics"])),
            "gt_topic_tags": tag_str(g["topic_tags"]),
            "gt_confidence": g["confidence"],
            "gt_note": g["note"],
        }
        for name, out in runs.items():
            pred = out.get(cid)
            if pred is None:
                row[f"{name}_off_topic"] = ""
                row[f"{name}_topics"] = ""
                row[f"{name}_topic_tags"] = ""
                continue
            row[f"{name}_off_topic"] = pred["off_topic"]
            row[f"{name}_topics"] = "; ".join(sorted(pred["topics"]))
            row[f"{name}_topic_tags"] = tag_str(pred["topic_tags"])
        for name, out in runs.items():
            pred = out.get(cid)
            if pred is None:
                row[f"{name}_off_topic_correct"] = ""
                row[f"{name}_topics_exact_match"] = ""
                row[f"{name}_tags_exact_match"] = ""
                continue
            row[f"{name}_off_topic_correct"] = pred["off_topic"] == g["off_topic"]
            row[f"{name}_topics_exact_match"] = set(pred["topics"]) == set(g["topics"])
            row[f"{name}_tags_exact_match"] = {
                (t["category"], t["subtopic"]) for t in pred["topic_tags"]
            } == {(t["category"], t["subtopic"]) for t in g["topic_tags"]}
        rows_out.append(row)

    with open(args.out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    # ---- aggregate scoring ----
    n = len(custom_ids)
    results = {"n": n}

    # off_topic: binary, positive class = off_topic==True
    off_topic_scores = {}
    for name, out in runs.items():
        tp = fp = fn = tn = 0
        for cid in custom_ids:
            pred = out.get(cid)
            if pred is None:
                continue
            p, gtruth = pred["off_topic"], gt[cid]["off_topic"]
            if p and gtruth:
                tp += 1
            elif p and not gtruth:
                fp += 1
            elif not p and gtruth:
                fn += 1
            else:
                tn += 1
        scored = tp + fp + fn + tn
        score = prf1(tp, fp, fn)
        score["tn"] = tn
        score["accuracy"] = (tp + tn) / scored if scored else None
        score["accuracy_ci"] = list(wilson_ci(tp + tn, scored))
        off_topic_scores[name] = score
    results["off_topic"] = off_topic_scores

    def multilabel_scores(gt_field: str, pred_key_fn, all_labels: set[str]):
        per_label = {}
        for name, out in runs.items():
            per_label[name] = {}
            for label in all_labels:
                tp = fp = fn = 0
                for cid in custom_ids:
                    pred = out.get(cid)
                    if pred is None:
                        continue
                    gt_set = set(gt[cid][gt_field]) if gt_field == "topics" else pred_key_fn(gt[cid][gt_field])
                    pred_set = set(pred["topics"]) if gt_field == "topics" else pred_key_fn(pred["topic_tags"])
                    in_gt = label in gt_set
                    in_pred = label in pred_set
                    if in_pred and in_gt:
                        tp += 1
                    elif in_pred and not in_gt:
                        fp += 1
                    elif not in_pred and in_gt:
                        fn += 1
                per_label[name][label] = prf1(tp, fp, fn)
        return per_label

    def micro(gt_field: str, pred_key_fn):
        micro_scores = {}
        for name, out in runs.items():
            tp = fp = fn = 0
            for cid in custom_ids:
                pred = out.get(cid)
                if pred is None:
                    continue
                gt_set = set(gt[cid][gt_field]) if gt_field == "topics" else pred_key_fn(gt[cid][gt_field])
                pred_set = set(pred["topics"]) if gt_field == "topics" else pred_key_fn(pred["topic_tags"])
                tp += len(gt_set & pred_set)
                fp += len(pred_set - gt_set)
                fn += len(gt_set - pred_set)
            micro_scores[name] = prf1(tp, fp, fn)
        return micro_scores

    def tagset(tags):
        return {f"{t['category']}.{t['subtopic']}" for t in tags}

    all_topics = {t for g in gt_list for t in g["topics"]}
    all_tags = {f"{t['category']}.{t['subtopic']}" for g in gt_list for t in g["topic_tags"]}

    results["topics_per_label"] = multilabel_scores("topics", None, all_topics)
    results["topics_micro"] = micro("topics", None)
    results["tags_per_label"] = multilabel_scores("topic_tags", tagset, all_tags)
    results["tags_micro"] = micro("topic_tags", tagset)

    gt_topic_counts = {t: 0 for t in all_topics}
    for g in gt_list:
        for t in g["topics"]:
            gt_topic_counts[t] += 1
    gt_tag_counts = {t: 0 for t in all_tags}
    for g in gt_list:
        for t in g["topic_tags"]:
            gt_tag_counts[f"{t['category']}.{t['subtopic']}"] += 1

    results["gt_topic_counts"] = gt_topic_counts
    results["gt_tag_counts"] = gt_tag_counts

    with open(args.out_json, "w") as f:
        json.dump(results, f, indent=4)

    print(f"Scored {n} chunks across runs: {', '.join(runs)}")
    print(f"Wrote {args.out_csv} and {args.out_json}")


if __name__ == "__main__":
    main()

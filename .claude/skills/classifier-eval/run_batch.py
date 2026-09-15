"""
Reusable OpenAI Batch API pilot runner for the heatmap chunk classifier.

Rebuilds a batch input JSONL from an EXISTING pilot input (same custom_id /
chunk_text / DOC context per line) but with the CURRENT system prompt and
response_format from app.services.heatmap_ingest.prompt — so re-piloting after
a prompt change is a same-chunks, prompt-only diff. Then submits it to the
real OpenAI Batch API and polls until done.

Usage:
    # 1. Build a new input.jsonl from an old one, using the current prompt.py
    python run_batch.py build --source ../run2/batch_v3_pilot_132.jsonl --out ../run3/input.jsonl

    # 2. Submit it
    python run_batch.py submit --input ../run3/input.jsonl --meta ../run3/batch_meta.json

    # 3. Poll until complete and download the output (blocking; run this via
    #    Bash run_in_background so you get one notification when it's done)
    python run_batch.py wait --meta ../run3/batch_meta.json --out ../run3/output.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.services.heatmap_ingest.prompt import SYSTEM_PROMPT, build_response_format_schema  # noqa: E402


def cmd_build(args):
    response_format = build_response_format_schema()
    n = 0
    with open(args.source) as src, open(args.out, "w") as out:
        for line in src:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            for msg in row["body"]["messages"]:
                if msg["role"] == "system":
                    msg["content"] = SYSTEM_PROMPT
            row["body"]["response_format"] = response_format
            out.write(json.dumps(row) + "\n")
            n += 1
    print(f"Wrote {n} requests to {args.out} (current prompt.py, same chunks as {args.source})")


def cmd_submit(args):
    from openai import OpenAI

    client = OpenAI()
    with open(args.input, "rb") as f:
        uploaded = client.files.create(file=f, purpose="batch")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    meta = {"batch_id": batch.id, "input_file_id": uploaded.id, "input_path": args.input}
    with open(args.meta, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Submitted batch {batch.id} (input file {uploaded.id}). Status: {batch.status}")
    print(f"Wrote {args.meta}")


def cmd_wait(args):
    from openai import OpenAI

    client = OpenAI()
    meta = json.load(open(args.meta))
    batch_id = meta["batch_id"]
    terminal = {"completed", "failed", "expired", "cancelled"}
    poll_s = args.poll_seconds
    while True:
        batch = client.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"[{time.strftime('%H:%M:%S')}] batch {batch_id} status={batch.status} "
            f"completed={counts.completed}/{counts.total} failed={counts.failed}"
        )
        if batch.status in terminal:
            break
        time.sleep(poll_s)

    if batch.status != "completed":
        print(f"FINAL STATUS: {batch.status} (not completed) — errors file: {batch.error_file_id}")
        sys.exit(1)

    content = client.files.content(batch.output_file_id)
    with open(args.out, "wb") as f:
        f.write(content.read())
    print(f"FINAL STATUS: completed. Wrote output to {args.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="rebuild a batch input jsonl using the current prompt.py")
    b.add_argument("--source", required=True, help="an existing batch input jsonl (same chunk set)")
    b.add_argument("--out", required=True)
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("submit", help="upload + submit a batch input jsonl")
    s.add_argument("--input", required=True)
    s.add_argument("--meta", required=True, help="where to write {batch_id, input_file_id}")
    s.set_defaults(func=cmd_submit)

    w = sub.add_parser("wait", help="poll a submitted batch until done, then download output")
    w.add_argument("--meta", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--poll-seconds", type=int, default=30)
    w.set_defaults(func=cmd_wait)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

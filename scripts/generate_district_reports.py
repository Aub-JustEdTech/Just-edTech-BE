"""Generate district analytics report PDFs from the CLI for the fixed Q1-Q7 queries.

Calls the same `district_report_service` the Celery task uses, so the PDF
output is identical to the API. Writes one PDF per query to disk (no HTTP,
no S3, no Celery). The `justedtech_{tenant_id}_documents` collection is
read via the same vector-store path as production.

Run (host-side — Postgres needs the localhost override):
    POSTGRES_SERVER=localhost poetry run python scripts/generate_district_reports.py \
        --tenant-id 4 --query Q1

Generate all seven:
    POSTGRES_SERVER=localhost poetry run python scripts/generate_district_reports.py \
        --tenant-id 4 --all

Output:
    scripts/output/district_reports/tenant_{id}/Q1_<title>_tenant{id}_{date}.pdf
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Force the host-side Postgres override so AsyncSessionLocal can reach the
# locally-running Postgres (the docker compose services use
# host.docker.internal, which doesn't resolve from a host shell).
os.environ.setdefault("POSTGRES_SERVER", "localhost")

from app.services.district_report import district_report_service  # noqa: E402
from app.services.district_report.queries import list_query_ids  # noqa: E402

logger = logging.getLogger("generate_district_reports")

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "district_reports"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate district analytics report PDFs for fixed queries.",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        required=True,
        help="Tenant ID whose corpus to report against.",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Fixed query ID to generate (e.g. Q1). Mutually exclusive with --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate a separate PDF for every fixed query (Q1-Q7).",
    )
    parser.add_argument(
        "--chatbot-config-id",
        type=int,
        default=None,
        help="Optional chatbot config ID for the writer LLM (default: tenant's default).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR}).",
    )
    return parser.parse_args()


async def _generate_one(
    tenant_id: int,
    query_id: str,
    chatbot_config_id: int | None,
    out_dir: Path,
) -> Path:
    print(f"[{query_id}] generating...", flush=True)
    result = await district_report_service.generate_report(
        tenant_id=tenant_id,
        query_id=query_id,
        chatbot_config_id=chatbot_config_id,
    )

    tenant_dir = out_dir / f"tenant_{tenant_id}"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    out_path = tenant_dir / result["filename"]
    out_path.write_bytes(result["pdf_bytes"])
    print(f"[{query_id}] wrote {out_path} ({len(result['pdf_bytes'])} bytes)", flush=True)
    return out_path


async def _run(args: argparse.Namespace) -> int:
    if not args.all and not args.query:
        print("error: pass either --query <ID> or --all", file=sys.stderr)
        return 2

    if args.query and args.all:
        print("error: --query and --all are mutually exclusive", file=sys.stderr)
        return 2

    if args.query:
        query_ids = [args.query]
    else:
        query_ids = list_query_ids()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for qid in query_ids:
        try:
            await _generate_one(args.tenant_id, qid, args.chatbot_config_id, args.out_dir)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[{qid}] FAILED: {exc}", file=sys.stderr)
            logger.exception("Failed to generate %s for tenant %s", qid, args.tenant_id)

    if failures:
        print(f"\n{failures}/{len(query_ids)} report(s) failed.", file=sys.stderr)
        return 1
    print(f"\nGenerated {len(query_ids)} report(s) in {args.out_dir}.")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

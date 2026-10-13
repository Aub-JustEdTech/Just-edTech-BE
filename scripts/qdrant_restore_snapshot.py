"""Restore Qdrant snapshots into the local Qdrant container.

Snapshots live in ``/home/aubergine/Desktop/JustEdTech/`` and total ~3.5 GB.
This script uploads them to the local Qdrant REST API at
``http://localhost:6343`` (host-mapped from the container's 6333) and
rebuilds collections from them. The restore endpoint is::

    POST /collections/{name}/snapshots/upload?priority=snapshot&wait=true
    multipart/form-data; field name = "snapshot"

By default snapshots are restored to a ``_test`` suffix so the existing
small collections (tenant 2's 20k-point corpus, the 7-point tenant-4
test data) are not overwritten. Use ``--in-place`` to restore to the
real collection name instead.

Examples::

    # Restore tenant 4 documents + summaries to *_test collections
    poetry run python scripts/qdrant_restore_snapshot.py \\
        --snapshot-dir /home/aubergine/Desktop/JustEdTech \\
        --tenant 4

    # Restore in place (overwrites the existing collection)
    poetry run python scripts/qdrant_restore_snapshot.py \\
        --snapshot-dir /home/aubergine/Desktop/JustEdTech \\
        --tenant 4 --in-place

    # Restore a specific snapshot file by path
    poetry run python scripts/qdrant_restore_snapshot.py \\
        --file /home/aubergine/Desktop/JustEdTech/justedtech_4_documents-*.snapshot \\
        --collection-name justedtech_4_documents_test

    # List snapshots available in the dir without restoring
    poetry run python scripts/qdrant_restore_snapshot.py --list \\
        --snapshot-dir /home/aubergine/Desktop/JustEdTech
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterator

import httpx

# Default Qdrant URL — the local Docker container's host-mapped port.
DEFAULT_QDRANT_URL = "http://localhost:6343"
DEFAULT_SNAPSHOT_DIR = "/home/aubergine/Desktop/JustEdTech"
PREFIX = "justedtech"

# Snapshot filename pattern:
#   justedtech_{tenant}_{kind}-{shard_id}-{timestamp}.snapshot
SNAPSHOT_RE = re.compile(
    rf"^{PREFIX}_(\d+)_(documents|summaries)-[\w-]+\.snapshot$"
)


def list_snapshots(snapshot_dir: Path) -> list[Path]:
    """Return all *.snapshot files in ``snapshot_dir`` sorted by size desc."""
    files = sorted(
        snapshot_dir.glob("*.snapshot"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    return files


def tenant_snapshots(
    snapshot_dir: Path, tenant_id: int, kind: str | None = None
) -> Iterator[tuple[Path, str, str]]:
    """Yield ``(path, kind, collection_name)`` for a tenant's snapshots.

    ``kind`` is ``documents`` or ``summaries``; ``collection_name`` is the
    base collection name (without the ``_test`` suffix).
    """
    for p in list_snapshots(snapshot_dir):
        m = SNAPSHOT_RE.match(p.name)
        if not m:
            continue
        snap_tenant, snap_kind = int(m.group(1)), m.group(2)
        if snap_tenant != tenant_id:
            continue
        if kind and snap_kind != kind:
            continue
        base = f"{PREFIX}_{tenant_id}_{snap_kind}"
        yield p, snap_kind, base


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def restore_snapshot(
    qdrant_url: str,
    snapshot_path: Path,
    collection_name: str,
    *,
    wait: bool = True,
    priority: str = "snapshot",
    timeout: float = 600.0,
) -> dict:
    """Upload ``snapshot_path`` to ``qdrant_url`` as ``collection_name``.

    Returns the Qdrant JSON response. Raises ``RuntimeError`` on non-200.
    """
    url = (
        f"{qdrant_url.rstrip('/')}/collections/{collection_name}"
        f"/snapshots/upload"
    )
    params = {"priority": priority}
    if wait:
        params["wait"] = "true"

    size_mb = snapshot_path.stat().st_size / (1024 * 1024)
    print(
        f"  Uploading {snapshot_path.name} "
        f"({human_size(snapshot_path.stat().st_size)}) -> {collection_name}"
    )

    # Stream the file so a 3 GB snapshot doesn't get fully buffered in RAM.
    # httpx supports streaming via `files=<reader>` but for multipart we
    # open the file and let httpx read it.
    with snapshot_path.open("rb") as fh:
        resp = httpx.post(
            url,
            params=params,
            files={"snapshot": (snapshot_path.name, fh)},
            timeout=timeout,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Restore failed for {snapshot_path.name} -> {collection_name}: "
            f"HTTP {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def count_points(qdrant_url: str, collection_name: str) -> int:
    """Exact count of points in a collection (0 if missing)."""
    url = f"{qdrant_url.rstrip('/')}/collections/{collection_name}/points/count"
    resp = httpx.post(url, json={"exact": True, "filter": {}}, timeout=60.0)
    if resp.status_code != 200:
        return 0
    return int(resp.json().get("result", {}).get("count", 0))


def list_collections(qdrant_url: str) -> list[str]:
    resp = httpx.get(f"{qdrant_url.rstrip('/')}/collections", timeout=30.0)
    return [c["name"] for c in resp.json().get("result", {}).get("collections", [])]


def cmd_list(args: argparse.Namespace) -> int:
    snap_dir = Path(args.snapshot_dir)
    if not snap_dir.is_dir():
        print(f"Snapshot dir not found: {snap_dir}", file=sys.stderr)
        return 1
    snaps = list_snapshots(snap_dir)
    if not snaps:
        print(f"No *.snapshot files in {snap_dir}")
        return 0
    print(f"Snapshots in {snap_dir} ({len(snaps)} files):")
    for p in snaps:
        m = SNAPSHOT_RE.match(p.name)
        tenant = int(m.group(1)) if m else "?"
        kind = m.group(2) if m else "?"
        print(
            f"  tenant={tenant}  kind={kind:<10}  "
            f"size={human_size(p.stat().st_size):>8}  {p.name}"
        )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    qdrant_url = args.qdrant_url

    # Sanity-check the Qdrant is reachable before we start streaming GBs.
    try:
        cols = list_collections(qdrant_url)
    except Exception as e:
        print(f"Cannot reach Qdrant at {qdrant_url}: {e}", file=sys.stderr)
        return 1
    print(f"Connected to Qdrant at {qdrant_url} ({len(cols)} existing collections)")

    if args.file:
        # Single-file mode.
        snapshot_path = Path(args.file)
        if not snapshot_path.is_file():
            print(f"Snapshot file not found: {snapshot_path}", file=sys.stderr)
            return 1
        if not args.collection_name:
            print(
                "--file requires --collection-name "
                "(the collection to restore into)",
                file=sys.stderr,
            )
            return 1
        targets = [(snapshot_path, args.collection_name)]
    else:
        # Tenant mode: restore all snapshots for the given tenant.
        snap_dir = Path(args.snapshot_dir)
        if not snap_dir.is_dir():
            print(f"Snapshot dir not found: {snap_dir}", file=sys.stderr)
            return 1
        suffix = "" if args.in_place else "_test"
        targets = []
        for p, _kind, base in tenant_snapshots(snap_dir, args.tenant):
            targets.append((p, f"{base}{suffix}"))

    if not targets:
        print(
            f"No snapshots found for tenant {args.tenant} in {args.snapshot_dir}",
            file=sys.stderr,
        )
        return 1

    print(f"\nRestoring {len(targets)} snapshot(s):")
    for p, name in targets:
        try:
            restore_snapshot(qdrant_url, p, name, wait=True)
        except RuntimeError as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            return 1
    print()

    print("Verification:")
    for _p, name in targets:
        count = count_points(qdrant_url, name)
        print(f"  {name}: {count:,} points")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Restore Qdrant snapshots into the local Qdrant container.",
    )
    p.add_argument(
        "--qdrant-url",
        default=DEFAULT_QDRANT_URL,
        help=f"Qdrant REST URL (default: {DEFAULT_QDRANT_URL})",
    )

    sub = p.add_subparsers(dest="command")

    # `list`
    p_list = sub.add_parser("list", help="List available snapshots in a dir")
    p_list.add_argument(
        "--snapshot-dir",
        default=DEFAULT_SNAPSHOT_DIR,
        help=f"Directory containing .snapshot files (default: {DEFAULT_SNAPSHOT_DIR})",
    )

    # `restore`
    p_restore = sub.add_parser("restore", help="Restore snapshots into Qdrant")
    p_restore.add_argument(
        "--snapshot-dir",
        default=DEFAULT_SNAPSHOT_DIR,
        help=f"Directory containing .snapshot files (default: {DEFAULT_SNAPSHOT_DIR})",
    )
    p_restore.add_argument(
        "--tenant",
        type=int,
        default=None,
        help="Tenant ID to restore (restores all snapshots for that tenant)",
    )
    p_restore.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Restore to the real collection name (overwrites existing data). "
            "Default is to restore to a *_test collection so existing data "
            "is preserved."
        ),
    )
    p_restore.add_argument(
        "--file",
        default=None,
        help="Restore a single .snapshot file by path (requires --collection-name)",
    )
    p_restore.add_argument(
        "--collection-name",
        default=None,
        help=(
            "Collection name to restore into (only with --file)"
        ),
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "restore":
        return cmd_restore(args)
    else:
        # No subcommand: if --list was the intent, handle legacy.
        if getattr(args, "list", False):
            return cmd_list(args)
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())

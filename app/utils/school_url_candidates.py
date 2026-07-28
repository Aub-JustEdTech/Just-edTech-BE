"""
Helpers for deduplicating and ranking school URL discovery candidates.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.crud.schools import url_hash


def candidate_dedupe_key(url: str) -> str:
    """Dedupe key for review/display candidates.

    Keeps query strings (CMS pages often differ only by ``?pageId=`` /
    ``pREC_ID``) but drops fragments (``#mobile-nav``) and trailing slashes.
    Distinct from ``url_hash`` / ``normalize_url``, which strip query params
    for confirmed-scrape-url uniqueness.
    """
    parts = urlsplit(url.strip())
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def dedupe_and_rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int,
    preserve_query: bool = False,
) -> list[dict[str, Any]]:
    """
    Deduplicate candidate URLs by hash, keep highest score first,
    and return up to `max_candidates` entries.

    When ``preserve_query`` is True (confirmation/review API), query strings
    are kept so distinct CMS pages are not collapsed. When False (default),
    uses the stricter ``url_hash`` that strips query+fragment.

    Passes through optional schema-crawler fields (data_type, is_archive,
    data_years_available) when present on an entry; keyword-only candidates
    leave these unset so the caller persists the column defaults.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    key_fn = candidate_dedupe_key if preserve_query else url_hash

    for entry in sorted(
        candidates,
        key=lambda item: int(item.get("score") or 0),
        reverse=True,
    ):
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        key = key_fn(url)
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, Any] = {
            "url": url,
            "matched_keywords": list(entry.get("matched_keywords") or []),
            "score": int(entry.get("score") or 0),
            "url_hash": key,
        }
        # Schema-crawler pass-through fields (additive; keyword path omits them).
        if "data_type" in entry:
            row["data_type"] = entry.get("data_type")
        if "is_archive" in entry:
            row["is_archive"] = bool(entry.get("is_archive") or False)
        if "data_years_available" in entry:
            row["data_years_available"] = list(entry.get("data_years_available") or [])
        unique.append(row)
        if len(unique) >= max_candidates:
            break

    return unique

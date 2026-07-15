"""
Helpers for deduplicating and ranking school URL discovery candidates.
"""

from __future__ import annotations

from typing import Any

from app.crud.schools import url_hash


def dedupe_and_rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """
    Deduplicate candidate URLs by normalized hash, keep highest score first,
    and return up to `max_candidates` entries.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for entry in sorted(
        candidates,
        key=lambda item: int(item.get("score") or 0),
        reverse=True,
    ):
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        key = url_hash(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "url": url,
                "matched_keywords": list(entry.get("matched_keywords") or []),
                "score": int(entry.get("score") or 0),
                "url_hash": key,
            }
        )
        if len(unique) >= max_candidates:
            break

    return unique

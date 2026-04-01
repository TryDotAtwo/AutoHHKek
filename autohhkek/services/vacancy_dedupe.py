from __future__ import annotations

import re
from typing import Any

_REMOTE_MARKERS = ("удален", "удалён", "remote", "дистанц", "home office", "wfh")


def _norm_snippet(value: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").lower().strip())
    return text[:limit]


def merge_serp_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve first-seen order; keep richer card text when duplicate URL appears."""
    by_url: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        if url not in by_url:
            by_url[url] = dict(item)
            order.append(url)
            continue
        existing = by_url[url]
        for key in ("description", "summary", "all_text"):
            new_val = str(item.get(key) or "")
            old_val = str(existing.get(key) or "")
            if len(new_val) > len(old_val):
                existing[key] = new_val
    return [by_url[u] for u in order]


def _remoteish_card(item: dict[str, Any]) -> bool:
    if str(item.get("is_remote") or "").lower() == "true":
        return True
    blob = " ".join(
        [
            str(item.get("location") or ""),
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("description") or "")[:200],
        ]
    ).lower()
    return any(marker in blob for marker in _REMOTE_MARKERS)


def _posting_fingerprint(item: dict[str, Any]) -> str:
    title = _norm_snippet(item.get("title") or "", 160)
    company = _norm_snippet(item.get("company") or "", 160)
    body = str(item.get("description") or item.get("summary") or item.get("all_text") or "")
    body = _norm_snippet(body, 700)
    return f"{title}|{company}|{body}"


def dedupe_remote_same_posting_different_region(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    Drop near-duplicate SERP rows typical for remote: same title/company/body, different city in location.
    Only applies when the card looks remote-ish and fingerprint is long enough.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    removed = 0
    for item in items:
        if not _remoteish_card(item):
            out.append(item)
            continue
        fp = _posting_fingerprint(item)
        if len(fp) < 48:
            out.append(item)
            continue
        if fp in seen:
            removed += 1
            continue
        seen.add(fp)
        out.append(item)
    return out, removed

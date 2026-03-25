from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from autohhkek.domain.models import utc_now_iso


def sanitize_account_key(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or "default"


def derive_account_profile(*, storage_state: dict[str, Any] | None = None, resumes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    resume_items = list(resumes or [])
    resume_ids = sorted({str(item.get("resume_id") or "").strip() for item in resume_items if str(item.get("resume_id") or "").strip()})
    if resume_ids:
        seed = "resume:" + "|".join(resume_ids)
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        primary_title = str((resume_items[0] or {}).get("title") or resume_ids[0]).strip()
        display_name = primary_title if len(resume_items) == 1 else f"{primary_title} +{len(resume_items) - 1}"
    else:
        cookies = (storage_state or {}).get("cookies") or []
        stable_cookies = [
            {
                "name": str(item.get("name") or ""),
                "domain": str(item.get("domain") or ""),
            }
            for item in cookies
        ]
        digest = hashlib.sha1(json.dumps(stable_cookies, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        display_name = f"hh-{digest}"
    account_key = sanitize_account_key(f"hh-{digest}")
    return {
        "account_key": account_key,
        "display_name": display_name,
        "resume_ids": resume_ids,
        "resume_count": len(resume_ids),
        "updated_at": utc_now_iso(),
    }

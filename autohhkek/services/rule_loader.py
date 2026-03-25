from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.analysis import unique_preserve_order


LIST_KEYS = {
    "target_titles",
    "excluded_companies",
    "excluded_keywords",
    "required_skills",
    "preferred_skills",
    "preferred_locations",
    "forbidden_keywords",
    "primary_skills",
    "secondary_skills",
    "achievements",
    "languages",
    "links",
}

BOOL_KEYS = {"remote_only", "allow_relocation"}
INT_KEYS = {"salary_min"}
ANAMNESIS_KEYS = {"headline", "summary", "primary_skills", "secondary_skills", "achievements", "languages", "links"}


@dataclass(slots=True)
class RuleBundle:
    source_path: str
    raw_markdown: str
    preferences_patch: dict[str, Any]
    anamnesis_patch: dict[str, Any]


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _parse_scalar(key: str, raw_value: str) -> Any:
    value = raw_value.strip()
    if key in LIST_KEYS:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]
    if key in BOOL_KEYS:
        return value.lower() in {"1", "true", "yes", "y", "да"}
    if key in INT_KEYS:
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else None
    return value


def _assign_patch(target: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, "", []):
        return
    target[key] = value


def _parse_rule_bundle(source_path: str, raw: str) -> RuleBundle:
    preferences_patch: dict[str, Any] = {}
    anamnesis_patch: dict[str, Any] = {}

    current_key = ""
    current_list: list[str] = []

    def flush_current_list() -> None:
        nonlocal current_key, current_list
        if not current_key or not current_list:
            current_key = ""
            current_list = []
            return
        target = anamnesis_patch if current_key in ANAMNESIS_KEYS else preferences_patch
        _assign_patch(target, current_key, unique_preserve_order(current_list))
        current_key = ""
        current_list = []

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            normalized = _normalize_key(heading)
            if normalized in LIST_KEYS | ANAMNESIS_KEYS | BOOL_KEYS | INT_KEYS | {"cover_letter_mode", "notes"}:
                flush_current_list()
                current_key = normalized
                current_list = []
            continue
        if line.startswith("- ") and current_key:
            current_list.append(line[2:].strip())
            continue
        if ":" in line:
            flush_current_list()
            key, value = line.split(":", 1)
            normalized = _normalize_key(key)
            parsed = _parse_scalar(normalized, value)
            target = anamnesis_patch if normalized in ANAMNESIS_KEYS else preferences_patch
            if normalized in LIST_KEYS and parsed == []:
                current_key = normalized
                current_list = []
                continue
            _assign_patch(target, normalized, parsed)
            continue
        if current_key:
            current_list.append(line)

    flush_current_list()
    return RuleBundle(source_path=source_path, raw_markdown=raw, preferences_patch=preferences_patch, anamnesis_patch=anamnesis_patch)


def load_rule_bundle(path: str | Path) -> RuleBundle:
    source_path = Path(path)
    raw = source_path.read_text(encoding="utf-8")
    return _parse_rule_bundle(str(source_path), raw)


def load_rule_bundle_from_text(source_name: str, raw_markdown: str) -> RuleBundle:
    return _parse_rule_bundle(source_name, raw_markdown)


def apply_rule_bundle(
    preferences: UserPreferences,
    anamnesis: Anamnesis,
    bundle: RuleBundle,
    *,
    current_rules_markdown: str = "",
) -> tuple[UserPreferences, Anamnesis, str]:
    pref_payload = preferences.to_dict()
    anam_payload = anamnesis.to_dict()

    for key, value in bundle.preferences_patch.items():
        if key in LIST_KEYS:
            pref_payload[key] = unique_preserve_order(pref_payload.get(key, []) + value)
        else:
            pref_payload[key] = value

    for key, value in bundle.anamnesis_patch.items():
        if key in LIST_KEYS:
            anam_payload[key] = unique_preserve_order(anam_payload.get(key, []) + value)
        else:
            anam_payload[key] = value

    merged_markdown = current_rules_markdown.rstrip()
    if merged_markdown:
        merged_markdown += "\n\n"
    merged_markdown += (
        "# Imported user rules\n\n"
        f"Source: {bundle.source_path}\n\n"
        f"{bundle.raw_markdown.strip()}\n"
    )

    return (
        UserPreferences.from_dict(pref_payload),
        Anamnesis.from_dict(anam_payload),
        merged_markdown,
    )


def apply_rule_bundles(
    preferences: UserPreferences,
    anamnesis: Anamnesis,
    bundles: list[RuleBundle],
    *,
    current_rules_markdown: str = "",
) -> tuple[UserPreferences, Anamnesis, str]:
    merged_preferences = preferences
    merged_anamnesis = anamnesis
    merged_markdown = current_rules_markdown
    for bundle in bundles:
        merged_preferences, merged_anamnesis, merged_markdown = apply_rule_bundle(
            merged_preferences,
            merged_anamnesis,
            bundle,
            current_rules_markdown=merged_markdown,
        )
    return merged_preferences, merged_anamnesis, merged_markdown

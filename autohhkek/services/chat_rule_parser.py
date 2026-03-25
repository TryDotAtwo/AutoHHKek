from __future__ import annotations

import re
from typing import Any


RU_ZARP = "\u0437\u0430\u0440\u043f\u043b\u0430\u0442\u0430"
RU_OT = "\u043e\u0442"
RU_NE_HOCHU = "\u043d\u0435 \u0445\u043e\u0447\u0443"
RU_BEZ = "\u0431\u0435\u0437"
RU_ISHU = "\u0438\u0449\u0443"
RU_I = "\u0438"
RU_ONLY = "\u0442\u043e\u043b\u044c\u043a\u043e"
RU_UDAL = "\u0443\u0434\u0430\u043b"
RU_MOSCOW = "\u043c\u043e\u0441\u043a\u0432\u0430"
RU_ROLE = "\u0440\u043e\u043b\u044c"
RU_EXCLUDE_COMPANY = "\u0438\u0441\u043a\u043b\u044e\u0447\u0438 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044e"
RU_REQUIRED = ("\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e", "\u043d\u0443\u0436\u0435\u043d", "\u043d\u0443\u0436\u043d\u044b")


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def parse_rule_request(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    lower = normalized.lower()
    patch: dict[str, Any] = {}

    salary_match = re.search(rf"(?:{RU_ZARP}\s*{RU_OT}|{RU_OT})\s*([\d\s]{{5,}})", lower)
    if salary_match:
        digits = "".join(ch for ch in salary_match.group(1) if ch.isdigit())
        if digits:
            patch["salary_min"] = int(digits)

    if any(token in lower for token in (f"{RU_ONLY} remote", f"{RU_ONLY} {RU_UDAL}", "\u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0443\u0434\u0430\u043b", "\u0443\u0434\u0430\u043b\u0435\u043d\u043a\u0430 \u0442\u043e\u043b\u044c\u043a\u043e", "\u0443\u0434\u0430\u043b\u0451\u043d\u043a\u0430 \u0442\u043e\u043b\u044c\u043a\u043e")):
        patch["remote_only"] = True
    elif any(token in lower for token in ("\u043c\u043e\u0436\u043d\u043e \u043e\u0444\u0438\u0441", "\u043c\u043e\u0436\u043d\u043e \u0433\u0438\u0431\u0440\u0438\u0434", f"\u043d\u0435 {RU_ONLY} remote", f"\u043d\u0435 {RU_ONLY} {RU_UDAL}")):
        patch["remote_only"] = False

    excluded_companies: list[str] = []
    company_patterns = (
        rf"(?:{RU_EXCLUDE_COMPANY}|\u0438\u0441\u043a\u043b\u044e\u0447\u0438 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438|{RU_BEZ} \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438|{RU_NE_HOCHU} \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044e)\s+([^,.]+)",
    )
    for pattern in company_patterns:
        for match in re.finditer(pattern, lower):
            excluded_companies.append(normalized[match.start(1):match.end(1)].strip())
    if excluded_companies:
        patch["excluded_companies"] = _unique(excluded_companies)

    forbidden_keywords: list[str] = []
    if RU_NE_HOCHU in lower:
        fragment = re.split(RU_NE_HOCHU, normalized, flags=re.IGNORECASE, maxsplit=1)[1]
        fragment = re.split(rf",|;| {RU_ZARP} | {RU_ISHU} | {RU_ONLY} remote| {RU_ONLY} {RU_UDAL}", fragment, maxsplit=1, flags=re.IGNORECASE)[0]
        for part in re.split(rf"\s+{RU_I}\s+", fragment):
            value = part.strip()
            if value:
                forbidden_keywords.append(value)
    for match in re.finditer(rf"(?:{RU_BEZ})\s+([^,.]+)", lower):
        value = normalized[match.start(1):match.end(1)].strip()
        if value and len(value.split()) <= 6:
            forbidden_keywords.append(value)
    if forbidden_keywords:
        patch["forbidden_keywords"] = _unique(forbidden_keywords)

    target_titles: list[str] = []
    for match in re.finditer(rf"(?:{RU_ISHU}|\u0445\u043e\u0447\u0443|\u043d\u0443\u0436\u043d\u0430 {RU_ROLE}|\u043d\u0443\u0436\u043d\u0430 \u043f\u043e\u0437\u0438\u0446\u0438\u044f|{RU_ROLE})\s+([^,.]+)", lower):
        value = normalized[match.start(1):match.end(1)].strip()
        if value and len(value.split()) <= 8:
            target_titles.extend([item.strip() for item in re.split(rf"/|,| {RU_I} ", value) if item.strip()])
    if target_titles:
        patch["target_titles"] = _unique(target_titles)

    preferred_locations: list[str] = []
    city_aliases = {
        RU_MOSCOW: "\u041c\u043e\u0441\u043a\u0432\u0430",
        "\u0441\u0430\u043d\u043a\u0442-\u043f\u0435\u0442\u0435\u0440\u0431\u0443\u0440\u0433": "\u0421\u0430\u043d\u043a\u0442-\u041f\u0435\u0442\u0435\u0440\u0431\u0443\u0440\u0433",
        "\u043f\u0435\u0442\u0435\u0440\u0431\u0443\u0440\u0433": "\u0421\u0430\u043d\u043a\u0442-\u041f\u0435\u0442\u0435\u0440\u0431\u0443\u0440\u0433",
        "\u043d\u043e\u0432\u043e\u0441\u0438\u0431\u0438\u0440\u0441\u043a": "\u041d\u043e\u0432\u043e\u0441\u0438\u0431\u0438\u0440\u0441\u043a",
        "\u043a\u0430\u0437\u0430\u043d\u044c": "\u041a\u0430\u0437\u0430\u043d\u044c",
        "remote": "Remote",
    }
    for key, value in city_aliases.items():
        if key in lower:
            preferred_locations.append(value)
    if preferred_locations:
        patch["preferred_locations"] = _unique(preferred_locations)

    required_skills: list[str] = []
    known_skills = {
        "python": "Python",
        "llm": "LLM",
        "nlp": "NLP",
        "rag": "RAG",
        "pytorch": "PyTorch",
        "transformers": "Transformers",
        "langchain": "LangChain",
        "sql": "SQL",
    }
    if any(token in lower for token in (*RU_REQUIRED, "must-have")):
        for token, label in known_skills.items():
            if token in lower:
                required_skills.append(label)
    if required_skills:
        patch["required_skills"] = _unique(required_skills)

    patch["notes"] = normalized
    return patch


def patch_to_markdown(patch: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in patch.items():
        if isinstance(value, list):
            lines.append(f"{key}: {', '.join(value)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines).strip()

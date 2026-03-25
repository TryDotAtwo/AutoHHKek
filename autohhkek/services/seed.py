from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy

from .storage import WorkspaceStore


def _slug_from_url_or_title(title: str, url: str) -> str:
    raw = url or title
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _deduce_skills(text: str) -> list[str]:
    known = [
        "Python",
        "SQL",
        "NLP",
        "LLM",
        "Machine Learning",
        "PyTorch",
        "Transformers",
        "RAG",
        "CV",
        "MLOps",
    ]
    lowered = text.lower()
    return [skill for skill in known if skill.lower() in lowered]


def import_legacy_vacancies(store: WorkspaceStore, path: Path, limit: int = 200) -> list[Vacancy]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    vacancies: list[Vacancy] = []
    seen: set[str] = set()
    items = payload if limit <= 0 else payload[:limit]
    for item in items:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title:
            continue
        key = url or title.lower()
        if key in seen:
            continue
        seen.add(key)
        vacancies.append(
            Vacancy(
                vacancy_id=_slug_from_url_or_title(title, url),
                title=title,
                url=url,
                is_remote="удал" in title.lower() or "remote" in title.lower(),
                skills=_deduce_skills(title),
                summary="Импортировано из legacy cache.",
                meta={"source": "legacy_cache"},
            )
        )
    if vacancies:
        store.save_vacancies(vacancies)
        store.record_event("seed", f"Импортировано {len(vacancies)} вакансий из legacy cache.")
    return vacancies


def bootstrap_from_legacy_resume(store: WorkspaceStore, path: Path) -> tuple[UserPreferences, Anamnesis] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload:
        return None
    first_resume = next(iter(payload.values()))
    text = str(first_resume)
    skills = _deduce_skills(text)
    headline = "ML / LLM Engineer"
    headline_match = re.search(r"([A-Za-zА-Яа-я0-9/+ -]{6,60})", text)
    if headline_match:
        headline = headline_match.group(1).strip()

    preferences = UserPreferences(
        full_name="",
        target_titles=["LLM Engineer", "ML Engineer", "Data Scientist", "NLP Engineer"],
        excluded_companies=["правительство", "университет", "институт", "мфти", "мгу", "оияи"],
        excluded_keywords=["государствен", "research institute"],
        required_skills=skills[:3],
        preferred_skills=skills,
        preferred_locations=["Москва"],
        remote_only=False,
        allow_relocation=False,
        cover_letter_mode="adaptive",
        notes="Автобутстрап из legacy resume cache. Желательно пройти intake и уточнить правила.",
    )
    anamnesis = Anamnesis(
        headline=headline,
        summary=text[:1000],
        experience_years=3.0 if "3" in text else 2.0,
        primary_skills=skills[:5],
        secondary_skills=skills[5:],
        achievements=["Перенесено из legacy cache, требуется уточнение пользователем."],
    )
    store.save_preferences(preferences)
    store.save_anamnesis(anamnesis)
    store.record_event("bootstrap", "Профиль и правила созданы из legacy resume cache.")
    return preferences, anamnesis

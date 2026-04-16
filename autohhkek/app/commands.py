from __future__ import annotations

import random
import re
import time
import zipfile
from pathlib import Path
from typing import Any
import asyncio

from autohhkek.agents.application_agent import ApplicationAgent
from autohhkek.agents.intake_agent import IntakeAgent
from autohhkek.agents.openrouter_resume_intake_agent import OpenRouterResumeIntakeAgent
from autohhkek.agents.resume_agent import ResumeAgent
from autohhkek.agents.vacancy_analysis_agent import VacancyAnalysisAgent
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, utc_now_iso
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.hh_apply import apply_to_vacancy
from autohhkek.services.hh_login import run_hh_login
from autohhkek.services.hh_preflight import ensure_hh_context
from autohhkek.services.hh_resume_sync import HHResumeProfileSync
from autohhkek.services.chat_rule_parser import parse_rule_request
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.hh_refresh import HHVacancyRefresher
from autohhkek.services.profile_rules import compose_rules_markdown
from autohhkek.services.rule_loader import apply_rule_bundles, load_rule_bundle_from_text
from autohhkek.services.rules import build_selection_rules_markdown, build_user_rules_contract, evaluate_intake_readiness
from autohhkek.services.storage import WorkspaceStore
from autohhkek.agents.openrouter_cover_letter_agent import OpenRouterCoverLetterAgent

def _mark_analysis_stale(store: WorkspaceStore, reason: str) -> None:
    analysis_state = store.load_analysis_state() or {}
    analysis_state["stale"] = True
    analysis_state["stale_reason"] = reason
    store.save_analysis_state(analysis_state)


def update_runtime_settings(store: WorkspaceStore, patch: dict[str, Any]) -> dict[str, Any]:
    current = store.load_runtime_settings().to_dict()
    normalized_patch = dict(patch)
    if ("dashboard_mode" in normalized_patch or "agent_mode" in normalized_patch) and "mode_selected" not in normalized_patch:
        normalized_patch["mode_selected"] = True
    current.update(normalized_patch)
    saved = store.save_runtime_settings(current)
    store.record_event("runtime-settings", "Обновлены настройки runtime.", details=saved.to_dict())
    return saved.to_dict()


def _split_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").replace("\r", "\n").replace(";", ",").replace("\n", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _parse_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(str(value).strip().replace(" ", "")))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any, fallback: float = 0.0) -> float:
    if value in ("", None):
        return fallback
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def _extract_xml_text(content: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()


def _read_text_attachment(path_value: str) -> tuple[str, str]:
    path = Path(str(path_value or "").strip().strip('"'))
    if not path.exists() or not path.is_file():
        raise RuntimeError("Файл с ответами не найден.")
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.name, path.read_text(encoding="utf-8")
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        return path.name, _extract_xml_text(xml)
    raise RuntimeError("Поддерживаются только .md, .txt и .docx. Для .doc лучше сохранить в .docx или вставить текст в чат.")


def _yes_no(value: bool) -> str:
    return "" if value else ""


def _unique_casefold(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        marker = value.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _extract_locations_from_text(text: str) -> list[str]:
    lowered = _normalize_text(text)
    aliases = {
        "": "",
        "-": "-",
        "": "-",
        "": "",
        "": "",
        "": "",
        "": "",
        "": "",
        "eu": "EU",
        "europe": "Europe",
    }
    result = [value for token, value in aliases.items() if token in lowered]
    if any(token in lowered for token in ("remote", "", "", "", "")):
        result.append("Remote")
    return _unique_casefold(result)


def _extract_industries_from_text(text: str) -> list[str]:
    lowered = _normalize_text(text)
    known = {
        "llm": "LLM products",
        "nlp": "NLP",
        "ai infra": "AI infra",
        "ml infra": "ML infra",
        "research": "Applied research",
        "": "",
        "healthcare": "Healthcare",
        "": "Healthcare",
        "": "Biotech",
        "edtech": "EdTech",
        "adtech": "AdTech",
        "cv": "Computer Vision",
        "computer vision": "Computer Vision",
        "robotics": "Robotics",
        "": "Robotics",
    }
    return _unique_casefold([value for token, value in known.items() if token in lowered])


def _detect_remote_only(text: str) -> bool | None:
    lowered = _normalize_text(text)
    if any(token in lowered for token in (" remote", " ", " ", "remote only", " ", " ")):
        return True
    if any(token in lowered for token in ("", "", "on-site", "onsite", "  remote", "  ")):
        return False
    return None


def _detect_allow_relocation(text: str) -> bool | None:
    lowered = _normalize_text(text)
    if any(token in lowered for token in ("", " ", "  ")):
        return True
    if any(token in lowered for token in (" ", "  ", "   ")):
        return False
    return None


def _skills_from_text(text: str) -> list[str]:
    lowered = _normalize_text(text)
    skills = {
        "python": "Python",
        "nlp": "NLP",
        "llm": "LLM",
        "transformers": "Transformers",
        "pytorch": "PyTorch",
        "rag": "RAG",
        "mlops": "MLOps",
        "sql": "SQL",
        "langchain": "LangChain",
        "agents": "Agents",
        "": "Agents",
    }
    return _unique_casefold([value for token, value in skills.items() if token in lowered])


def _parse_free_text_titles(text: str, fallback_roles: list[str]) -> list[str]:
    parsed = parse_rule_request(text).get("target_titles") or []
    explicit = _split_items(parsed)
    inferred = _infer_roles_from_resume_text(text)
    return _unique_casefold(explicit + inferred + list(fallback_roles or []))


def _compose_intake_notes(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if str(part or "").strip()).strip()


def _resume_title_from_store(store: WorkspaceStore) -> str:
    for item in store.load_hh_resumes():
        title = str(item.get("title") or "").strip()
        if title:
            return title
    return ""


def _safe_resume_sync_for_intake(store: WorkspaceStore) -> dict[str, Any]:
    selected_resume_id = store.load_selected_resume_id()
    if not selected_resume_id:
        return {}
    dashboard_state = store.load_dashboard_state()
    anamnesis = store.load_anamnesis()
    cached_extracted = dashboard_state.get("last_resume_sync_extracted")
    if isinstance(cached_extracted, dict) and cached_extracted:
        return cached_extracted

    local_profile = {
        "headline": str(dashboard_state.get("last_resume_sync_title") or _resume_title_from_store(store) or getattr(anamnesis, "headline", "") or "").strip(),
        "summary": str(getattr(anamnesis, "summary", "") or "").strip(),
        "skills": list(getattr(anamnesis, "primary_skills", []) or []),
        "languages": list(getattr(anamnesis, "languages", []) or []),
        "links": list(getattr(anamnesis, "links", []) or []),
        "experience_years": getattr(anamnesis, "experience_years", 0.0),
    }
    if any(
        [
            local_profile["headline"],
            local_profile["summary"],
            local_profile["skills"],
            local_profile["languages"],
            local_profile["links"],
            local_profile["experience_years"],
        ]
    ):
        return local_profile

    if not store.hh_state_path.exists():
        return {}
    try:
        result = HHResumeProfileSync(store).sync_selected_resume()
    except Exception:
        return {}
    if str(result.get("status") or "") not in {"updated", "no_changes"}:
        return {}
    extracted = result.get("extracted")
    return extracted if isinstance(extracted, dict) else {}


def _build_resume_intake_source_payload(store: WorkspaceStore, *, extracted: dict[str, Any]) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    return {
        "selected_resume_id": store.load_selected_resume_id(),
        "resume_title": str(extracted.get("headline") or _resume_title_from_store(store) or getattr(anamnesis, "headline", "") or "").strip(),
        "resume_summary": str(extracted.get("summary") or getattr(anamnesis, "summary", "") or getattr(preferences, "notes", "") or "").strip(),
        "preferences": preferences.to_dict() if preferences else {},
        "anamnesis": anamnesis.to_dict() if anamnesis else {},
        "extracted": extracted,
    }


def _llm_resume_intake_analysis(store: WorkspaceStore, *, extracted: dict[str, Any]) -> dict[str, Any]:
    source_payload = _build_resume_intake_source_payload(store, extracted=extracted)
    source_marker = repr(source_payload)
    dashboard_state = store.load_dashboard_state()
    cached_marker = str(dashboard_state.get("resume_intake_analysis_marker") or "")
    cached_payload = dashboard_state.get("resume_intake_analysis")
    if cached_marker == source_marker and isinstance(cached_payload, dict) and cached_payload:
        return dict(cached_payload)

    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    if not preferences or not anamnesis:
        return {}

    agent = OpenRouterResumeIntakeAgent()
    output = agent.analyze(
        preferences=preferences,
        anamnesis=anamnesis,
        resume_title=source_payload["resume_title"],
        resume_summary=source_payload["resume_summary"],
        extracted=extracted,
    )
    if not output:
        fallback_state = dict(dashboard_state.get("heuristic_fallback") or {})
        if not fallback_state.get("resume_intake"):
            reason = agent.last_error or "OpenRouter недоступен на этапе resume intake."
            store.update_dashboard_state(
                {
                    "llm_gate": {
                        "active": True,
                        "stage": "resume_intake",
                        "backend": "openrouter",
                        "message": reason[:1200],
                        "title": "LLM временно недоступен",
                    }
                }
            )
            return {"blocked": True, "stage": "resume_intake", "message": reason}
        if agent.last_status == "error":
            store.update_dashboard_state({"resume_intake_analysis_error": agent.last_error[:1000]})
        return {}
    payload = output.model_dump()
    store.update_dashboard_state(
        {
            "llm_gate": {},
            "resume_intake_analysis": payload,
            "resume_intake_analysis_marker": source_marker,
            "resume_intake_analysis_error": "",
        }
    )
    return payload


def choose_heuristic_fallback(store: WorkspaceStore, *, stage: str) -> dict[str, Any]:
    state = store.load_dashboard_state()
    fallback_state = dict(state.get("heuristic_fallback") or {})
    fallback_state[stage] = True
    store.update_dashboard_state({"heuristic_fallback": fallback_state, "llm_gate": {}})
    return {
        "action": "heuristic-fallback",
        "stage": stage,
        "message": "Переходим на эвристики. Можно продолжать без LLM-разбора.",
    }


def postpone_until_llm_available(store: WorkspaceStore, *, stage: str) -> dict[str, Any]:
    state = store.load_dashboard_state()
    fallback_state = dict(state.get("heuristic_fallback") or {})
    fallback_state[stage] = False
    llm_gate = dict(state.get("llm_gate") or {})
    llm_gate.update({"active": True, "stage": stage})
    store.update_dashboard_state({"heuristic_fallback": fallback_state, "llm_gate": llm_gate})
    return {
        "action": "llm-wait",
        "stage": stage,
        "message": "Оставляю шаг в ожидании. Продолжим, когда LLM снова будет доступен.",
    }


def _infer_roles_from_resume_text(*values: str) -> list[str]:
    known_roles = [
        "LLM Engineer",
        "NLP Engineer",
        "ML Engineer",
        "Machine Learning Engineer",
        "Research Scientist",
        "Research Engineer",
        "Applied Scientist",
        "Data Scientist",
        "AI Engineer",
    ]
    joined = "\n".join(str(value or "") for value in values)
    lowered = joined.casefold()
    result: list[str] = []
    for role in known_roles:
        if role.casefold() in lowered:
            result.append(role)
    return list(dict.fromkeys(result))


def _infer_skill_candidates(*values: str) -> list[str]:
    known_skills = ["Python", "NLP", "LLM", "Transformers", "PyTorch", "RAG", "MLOps", "SQL"]
    joined = "\n".join(str(value or "") for value in values)
    lowered = joined.casefold()
    result: list[str] = []
    for skill in known_skills:
        if skill.casefold() in lowered:
            result.append(skill)
    return list(dict.fromkeys(result))


def _missing_intake_topics(preferences: Any, anamnesis: Any) -> list[tuple[str, str, str]]:
    topics: list[tuple[str, str, str]] = []
    if not preferences.target_titles:
        topics.append(("Какие роли считаем целевыми?", "Лучше сразу назвать 3-7 приоритетных ролей для поиска.", 'Пример: "LLM Engineer, NLP Engineer, Research Scientist in NLP, Applied Scientist".'))
    if not anamnesis.primary_skills:
        topics.append(("Какие навыки уже есть в профиле?", "Нужно зафиксировать базовый стек, который вы реально подтверждаете опытом.", 'Пример: "Python, NLP, LLM, Transformers, PyTorch, RAG".'))
    if not preferences.required_skills:
        topics.append(("Что является must-have во вакансии?", "Если этого нет, вакансию обычно не стоит продвигать дальше.", 'Пример: "Python + NLP/LLM, сильный инженерный или исследовательский контекст".'))
    if not preferences.preferred_skills:
        topics.append(("Что желательно, но не обязательно?", "Эти сигналы повышают приоритет, но не являются стоп-фактором.", 'Пример: "RAG, MLOps, publication track, production ML, agentic systems".'))
    if not preferences.preferred_locations:
        topics.append(("Какая география и формат подходят?", "Важно сразу понять remote, города, страны и часовые пояса.", 'Пример: "remote, Москва, международные команды, UTC+2..UTC+5".'))
    if not preferences.remote_only and not preferences.allow_relocation:
        topics.append(("Нужен ли только remote?", "Это влияет на жёсткий отсев вакансий ещё до анализа.", 'Пример: "Только remote. Переезд не нужен.".'))
    if not preferences.salary_min:
        topics.append(("Какая минимальная компенсация имеет смысл?", "Нижняя граница помогает не тратить время на заведомо слабые варианты.", 'Пример: "От 350 000 net" или "от 450 000 gross".'))
    if not preferences.excluded_companies:
        topics.append(("Какие компании или типы работодателей исключаем?", "Это нужно, чтобы сразу убирать нежелательные вакансии.", 'Пример: "госструктуры, университеты, research institutes, бюрократичные non-ML роли".'))
    if not preferences.forbidden_keywords and not preferences.excluded_keywords:
        topics.append(("Какие стоп-слова режут вакансию сразу?", "Стоп-слова помогают быстро вычищать шум из выдачи.", 'Пример: "CV only, computer vision only, sales, analyst without ML, госслужба".'))
    if not anamnesis.industries:
        topics.append(("Какие домены интересны или нежелательны?", "Так агент лучше понимает смысл вакансии, а не только название.", 'Пример: "интересно: AI infra, LLM products, applied research; не хочу: госуха, adtech".'))
    if not anamnesis.education:
        topics.append(("Какой образовательный или исследовательский контекст важно учитывать?", "Это помогает лучше подать профиль в сопроводительных и фильтрах.", 'Пример: "сильный research background, физика, математика".'))
    if not anamnesis.languages:
        topics.append(("Какие языки рабочие?", "Это влияет на поиск, анкеты и тон сопроводительных.", 'Пример: "Русский свободно, английский C1".'))
    if not anamnesis.links:
        topics.append(("Какие ссылки стоит использовать в откликах?", "GitHub, Scholar, LinkedIn, статьи и pet-проекты усиливают профиль.", 'Пример: "GitHub ..., Scholar ..., LinkedIn ...".'))
    return topics


def build_detailed_intake_prompt(store: WorkspaceStore) -> str:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    extracted = _safe_resume_sync_for_intake(store)
    llm_analysis = _llm_resume_intake_analysis(store, extracted=extracted)
    if llm_analysis.get("blocked"):
        return str(llm_analysis.get("message") or "LLM сейчас недоступен, поэтому интейк лучше продолжить вручную.")
    summary = str(llm_analysis.get("summary") or extracted.get("summary") or anamnesis.summary or preferences.notes or "").strip()
    resume_title = str(llm_analysis.get("headline") or extracted.get("headline") or _resume_title_from_store(store) or anamnesis.headline or "не удалось определить").strip()
    inferred_roles = list(llm_analysis.get("inferred_roles") or _infer_roles_from_resume_text(resume_title, summary, preferences.notes))
    detected_skills = list(llm_analysis.get("core_skills") or extracted.get("skills") or anamnesis.primary_skills or _infer_skill_candidates(resume_title, summary, preferences.notes))
    detected_languages = list(extracted.get("languages") or anamnesis.languages)
    detected_links = list(extracted.get("links") or anamnesis.links)
    detected_experience = extracted.get("experience_years") if extracted.get("experience_years") is not None else anamnesis.experience_years
    topics = _missing_intake_topics(preferences, anamnesis)

    current_rules = [
        f"- Целевые роли: {', '.join(preferences.target_titles) if preferences.target_titles else 'не заданы'}",
        f"- Must-have навыки: {', '.join(preferences.required_skills) if preferences.required_skills else 'не заданы'}",
        f"- Желательные навыки: {', '.join(preferences.preferred_skills) if preferences.preferred_skills else 'не заданы'}",
        f"- Локации и формат: {', '.join(preferences.preferred_locations) if preferences.preferred_locations else 'не заданы'}",
        f"- Только remote: {_yes_no(preferences.remote_only)}",
        f"- Минимальная зарплата: {preferences.salary_min if preferences.salary_min else 'не задана'}",
        f"- Исключённые компании: {', '.join(preferences.excluded_companies) if preferences.excluded_companies else 'не заданы'}",
        f"- Стоп-слова: {', '.join(preferences.forbidden_keywords or preferences.excluded_keywords) if (preferences.forbidden_keywords or preferences.excluded_keywords) else 'не заданы'}",
    ]
    resume_facts = [
        f"- Заголовок резюме: {resume_title}",
        f"- Роли из резюме: {', '.join(inferred_roles) if inferred_roles else 'не выделены'}",
        f"- Опыт: {detected_experience if detected_experience else 'не удалось определить'}",
        f"- Навыки: {', '.join(detected_skills[:12]) if detected_skills else 'почти ничего не выделено'}",
        f"- Языки: {', '.join(detected_languages) if detected_languages else 'не заданы'}",
        f"- Ссылки: {', '.join(detected_links[:5]) if detected_links else 'не заданы'}",
    ]
    if summary:
        resume_facts.append(f"- Краткое резюме: {summary[:350]}")

    gaps: list[str] = []
    if inferred_roles and not preferences.target_titles:
        gaps.append(f"В резюме уже видны роли: {', '.join(inferred_roles)}. Их стоит либо подтвердить, либо сузить.")
    if detected_skills and not preferences.required_skills and not preferences.preferred_skills:
        gaps.append(f"Из резюме уже видны навыки: {', '.join(detected_skills[:6])}. Надо разделить их на must-have и nice-to-have.")
    for item in list(llm_analysis.get("missing_topics") or [])[:6]:
        gaps.append(str(item))

    parts = [
        "Ниже сводка для ручного интейка по текущему hh-профилю.",
        "",
        "Что уже вытащили из резюме:",
        *resume_facts,
        "",
        "Что уже зафиксировано в правилах:",
        *current_rules,
    ]
    if gaps:
        parts.extend(["", "Что ещё нужно уточнить:", *[f"- {item}" for item in gaps]])

    if topics:
        parts.extend(["", "Вопросы для пользователя:"])
        for index, (question, hint, example) in enumerate(topics, start=1):
            parts.extend(["", f"{index}. {question}", f"Почему спрашиваю: {hint}", example])
    else:
        parts.extend(["", "Критичных пробелов почти не осталось. Можно только добрать тонкие предпочтения.", 'Пример ответа: "Не хочу CV-only, беру только LLM/NLP-роли, зарплата от 350k".'])

    parts.extend(["", "Ответы можно собрать в одном месте и потом импортировать.", "Файл для импорта: C:\\answers.md"])
    return "\n".join(parts)


def begin_intake_dialog(store: WorkspaceStore) -> dict[str, Any]:
    result = start_intake_interview(store)
    return {
        "action": "intake-dialog",
        "status": str(result.get("status") or "running"),
        "dialog_state": dict(result.get("session") or {}),
        "message": str(result.get("message") or ""),
    }


def restart_intake_dialog(store: WorkspaceStore) -> dict[str, Any]:
    store.update_dashboard_state(
        {
            "intake_dialog": {},
            "intake_dialog_completed": False,
            "intake_dialog_completed_at": "",
            "intake_confirmed": False,
            "intake_confirmed_at": "",
        }
    )
    return begin_intake_dialog(store)


def continue_intake_dialog(store: WorkspaceStore, *, message: str) -> dict[str, Any]:
    progress = continue_intake_interview(store, answer_text=message)
    return {
        **progress,
        "action": "intake-dialog",
        "dialog_state": dict(progress.get("session") or progress.get("dialog_state") or {}),
    }


def _interview_context(store: WorkspaceStore) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    extracted = _safe_resume_sync_for_intake(store)
    summary = str(extracted.get("summary") or getattr(anamnesis, "summary", "") or getattr(preferences, "notes", "") or "").strip()
    resume_title = str(extracted.get("headline") or _resume_title_from_store(store) or getattr(anamnesis, "headline", "") or "").strip()
    llm_analysis = _llm_resume_intake_analysis(store, extracted=extracted)
    if llm_analysis.get("blocked"):
        return {
            "resume_title": resume_title,
            "summary": summary,
            "inferred_roles": [],
            "detected_skills": [],
            "detected_languages": list(extracted.get("languages") or getattr(anamnesis, "languages", [])),
            "detected_links": list(extracted.get("links") or getattr(anamnesis, "links", [])),
            "detected_experience": extracted.get("experience_years") if extracted.get("experience_years") is not None else getattr(anamnesis, "experience_years", 0.0),
            "detected_domains": list(extracted.get("domains") or getattr(anamnesis, "industries", [])),
            "likely_constraints": [],
            "missing_topics": [],
            "llm_blocked": True,
            "llm_message": str(llm_analysis.get("message") or ""),
        }
    inferred_roles = list(llm_analysis.get("inferred_roles") or _infer_roles_from_resume_text(resume_title, summary, getattr(preferences, "notes", "")))
    detected_skills = list(llm_analysis.get("core_skills") or extracted.get("skills") or getattr(anamnesis, "primary_skills", []) or _infer_skill_candidates(resume_title, summary, getattr(preferences, "notes", "")))
    return {
        "resume_title": resume_title,
        "summary": str(llm_analysis.get("summary") or summary),
        "inferred_roles": inferred_roles,
        "detected_skills": detected_skills,
        "detected_languages": list(extracted.get("languages") or getattr(anamnesis, "languages", [])),
        "detected_links": list(extracted.get("links") or getattr(anamnesis, "links", [])),
        "detected_experience": extracted.get("experience_years") if extracted.get("experience_years") is not None else getattr(anamnesis, "experience_years", 0.0),
        "detected_domains": list(llm_analysis.get("domains") or getattr(anamnesis, "industries", [])),
        "likely_constraints": list(llm_analysis.get("likely_constraints") or []),
        "missing_topics": list(llm_analysis.get("missing_topics") or []),
        "llm_analysis": llm_analysis,
        "llm_blocked": False,
    }


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(str(item or "").strip() for item in value)
    return bool(value)


def _normalize_topic_key(value: str) -> str:
    normalized = _normalize_text(value)
    for source, target in (
        ("рол", "target_titles"),
        ("позици", "target_titles"),
        ("must", "required_skills"),
        ("skill", "required_skills"),
        ("навык", "required_skills"),
        ("стек", "required_skills"),
        ("формат", "work_mode"),
        ("remote", "work_mode"),
        ("локац", "work_mode"),
        ("географ", "work_mode"),
        ("компан", "excluded_companies"),
        ("работодат", "excluded_companies"),
        ("стоп", "forbidden_keywords"),
        ("исключ", "forbidden_keywords"),
        ("salary", "salary_min"),
        ("зарплат", "salary_min"),
        ("домен", "industries"),
        ("industry", "industries"),
        ("nice", "preferred_skills"),
        ("желател", "preferred_skills"),
        ("language", "languages"),
        ("язык", "languages"),
        ("link", "links"),
        ("github", "links"),
        ("портфол", "links"),
    ):
        if source in normalized:
            return target
    return ""


def _build_intake_interview_questions(store: WorkspaceStore) -> list[dict[str, Any]]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    context = _interview_context(store)
    questions: list[dict[str, Any]] = []
    covered_topics: set[str] = set()

    def add_question(question_id: str, title: str, hint: str, example: str, *, prefill: str = "", importance: str = "critical") -> None:
        if question_id in covered_topics:
            return
        if _has_meaningful_value(prefill):
            covered_topics.add(question_id)
        questions.append(
            {
                "id": question_id,
                "title": title,
                "hint": hint,
                "example": example,
                "prefill": str(prefill or "").strip(),
                "importance": importance,
            }
        )

    role_prefill = ", ".join(getattr(preferences, "target_titles", []) or context["inferred_roles"])
    skill_prefill = ", ".join(context["detected_skills"][:6])
    domains_prefill = ", ".join(context.get("detected_domains") or [])
    role_example = (
        f'Пример: "Оставить как есть: {role_prefill}" или "Сузить до {", ".join((context["inferred_roles"] or [])[:2])}".'
        if role_prefill
        else 'Пример: "Оставить как есть" или "Сузить до LLM Engineer и NLP Engineer".'
    )
    must_have_example = (
        f'Пример: "must-have: {skill_prefill}" и отдельно отметить, что из этого обязательно.'
        if skill_prefill
        else 'Пример: "must-have: Python, NLP/LLM, сильная инженерная часть".'
    )
    domains_example = (
        f'Пример: "интересно: {domains_prefill}; это можно оставить как базу и уточнить приоритеты".'
        if domains_prefill
        else 'Пример: "интересно: LLM products, AI infra; не хочу: госуха, чистая академия".'
    )

    add_question(
        "target_titles",
        "Подтверждаем целевые роли или хотите их сузить?",
        "Резюме уже даёт базовое позиционирование. Здесь нужно только подтвердить его или поправить.",
        role_example,
        prefill=role_prefill,
    )
    if not _has_meaningful_value(getattr(preferences, "required_skills", [])):
        add_question(
            "required_skills",
            "Что из уже видимого в профиле считаем must-have?",
            "Нужно отделить обязательное от просто полезного, а не заново перечислять весь стек.",
            must_have_example,
            prefill=skill_prefill,
        )
    if not (_has_meaningful_value(getattr(preferences, "preferred_locations", [])) or getattr(preferences, "remote_only", False) or getattr(preferences, "allow_relocation", False)):
        format_prefill = "; ".join([item for item in context["likely_constraints"] if any(token in _normalize_text(item) for token in ("remote", "удален", "офис", "гибрид", "релокац", "локац", "географ"))])
        add_question(
            "work_mode",
            "Какой формат работы допустим?",
            "Это один из немногих критичных пунктов, который обычно не достаётся уверенно из резюме.",
            'Пример: "Только remote. Россия или международные команды. Переезд не нужен".',
            prefill=format_prefill,
        )
    if not _has_meaningful_value(getattr(preferences, "excluded_companies", [])):
        add_question(
            "excluded_companies",
            "Какие компании или типы работодателей точно исключаем?",
            "Это нужно, чтобы не тратить время на заведомо нежелательные вакансии.",
            'Пример: "госструктуры, университеты, research institutes, окологос проекты".',
            prefill="",
        )
    if not (_has_meaningful_value(getattr(preferences, "forbidden_keywords", [])) or _has_meaningful_value(getattr(preferences, "excluded_keywords", []))):
        add_question(
            "forbidden_keywords",
            "Какие стоп-слова и признаки режут вакансию сразу?",
            "Это помогает быстро вычищать шум из большой выдачи.",
            'Пример: "CV only, computer vision only, sales, преподавание, госслужба".',
            prefill="",
        )
    if not _has_meaningful_value(getattr(preferences, "salary_min", None)):
        add_question(
            "salary_min",
            "Какая минимальная компенсация имеет смысл?",
            "Если жёсткого порога нет, это тоже нормальный ответ.",
            'Пример: "От 350000 net" или "Жесткого порога нет".',
            prefill="",
        )
    if not _has_meaningful_value(getattr(anamnesis, "industries", [])):
        add_question(
            "industries",
            "Какие домены и типы задач вам реально интересны?",
            "Это помогает агенту понимать смысл вакансии, а не только слова в названии.",
            domains_example,
            prefill=domains_prefill,
            importance="important",
        )
    if not _has_meaningful_value(getattr(preferences, "preferred_skills", [])):
        add_question(
            "preferred_skills",
            "Что желательно, но не обязательно?",
            "Это повышает приоритет, но не является жестким стоп-фактором.",
            'Пример: "RAG, MLOps, production ML, agents".',
            prefill="",
            importance="important",
        )
    if not _has_meaningful_value(getattr(anamnesis, "languages", [])):
        add_question(
            "languages",
            "Какие языки реально рабочие?",
            "Это влияет на поиск, анкеты и тон сопроводительных.",
            'Пример: "Русский свободно, английский C1".',
            prefill=", ".join(context["detected_languages"]),
            importance="optional",
        )
    if not _has_meaningful_value(getattr(anamnesis, "links", [])):
        add_question(
            "links",
            "Какие ссылки стоит использовать в откликах?",
            "GitHub, Scholar, LinkedIn, портфолио и статьи усиливают профиль.",
            'Пример: "GitHub ..., Scholar ..., LinkedIn ...".',
            prefill=", ".join(context["detected_links"]),
            importance="optional",
        )

    for topic in list(context.get("missing_topics") or [])[:6]:
        text = str(topic or "").strip()
        if not text:
            continue
        topic_key = _normalize_topic_key(text)
        if topic_key in covered_topics:
            continue
        add_question(
            topic_key or f"followup_{len(questions) + 1}",
            text[0].upper() + text[1:] if text else "Что ещё нужно уточнить?",
            "Этот вопрос агент добавил после разбора резюме и уже известных правил.",
            'Пример: "Оставить как есть" или коротко уточнить нужное ограничение.',
            importance="important" if topic_key else "optional",
        )

    add_question(
        "extras",
        "Есть ли еще пожелания или тонкие ограничения?",
        "Это финальный свободный вопрос на случай, если в резюме и правилах что-то важное ещё не отражено.",
        'Пример: "не хочу слишком junior-роли; письмо всегда только на русском".',
        importance="optional",
    )
    return questions


def _format_intake_interview_question(session: dict[str, Any]) -> str:
    questions = list(session.get("questions") or [])
    index = int(session.get("step_index") or 0)
    if not questions:
        return "Вопросов не осталось. Можно завершать интейк."
    question = questions[min(index, len(questions) - 1)]
    lines: list[str] = []
    if index == 0:
        context = dict(session.get("context") or {})
        lines.extend(
            [
                "Сначала зафиксируем обязательные требования кандидата. Пока этот этап не завершен, поиск вакансий и анализ не запускаются.",
                "",
                "Что уже удалось вытащить из hh-резюме:",
                f"- Заголовок: {context.get('resume_title') or 'не удалось определить'}",
                f"- Роли из резюме: {', '.join(context.get('inferred_roles') or []) or 'не удалось уверенно выделить'}",
                f"- Навыки из резюме: {', '.join((context.get('detected_skills') or [])[:8]) or 'почти ничего не выделено'}",
                "",
            ]
        )
    lines.extend([f"Вопрос {index + 1} из {len(questions)}.", question["title"], f"Почему спрашиваю: {question['hint']}"])
    if question.get("prefill"):
        lines.append(f"Что уже вижу: {question['prefill']}")
    lines.extend([question["example"], "Можно ответить свободно. Если текущее понимание подходит, напишите: оставить как есть. Если пункт неважен, напишите: пропустить."])
    return "\n".join(lines)


def start_intake_interview(store: WorkspaceStore) -> dict[str, Any]:
    context = _interview_context(store)
    if context.get("llm_blocked"):
        return {
            "action": "intake_interview",
            "status": "blocked",
            "session": {},
            "message": str(context.get("llm_message") or "LLM сейчас недоступен."),
        }
    session = {
        "active": True,
        "completed": False,
        "step_index": 0,
        "questions": _build_intake_interview_questions(store),
        "answers": {},
        "context": context,
        "started_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    store.update_dashboard_state({"intake_dialog": session, "intake_dialog_completed": False, "intake_dialog_completed_at": "", "intake_confirmed": False, "intake_confirmed_at": ""})
    return {"action": "intake_interview", "status": "started", "session": session, "message": _format_intake_interview_question(session)}


def _answer_is_skip(value: str) -> bool:
    normalized = _normalize_text(value)
    return normalized in {"пропустить", "skip", "дальше", "не знаю", "без ответа"}


def _answer_is_keep(value: str) -> bool:
    normalized = _normalize_text(value)
    return normalized in {"оставить как есть", "как есть", "подтверждаю", "ок", "да"}


def _payload_from_intake_interview(store: WorkspaceStore, session: dict[str, Any]) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    context = dict(session.get("context") or {})
    answers = dict(session.get("answers") or {})
    work_mode = str(answers.get("work_mode") or "")
    normalized_work_mode = _normalize_text(work_mode)
    remote_only = getattr(preferences, "remote_only", False)
    allow_relocation = getattr(preferences, "allow_relocation", False)
    if work_mode:
        if any(token in normalized_work_mode for token in ("только удал", "remote only", "только remote")):
            remote_only = True
        elif any(token in normalized_work_mode for token in ("гибрид", "офис")):
            remote_only = False
        if any(token in normalized_work_mode for token in ("релокац", "переезд возможен", "готов к переезду")):
            allow_relocation = True
        elif any(token in normalized_work_mode for token in ("без переезда", "переезд не нужен", "не готов к переезду")):
            allow_relocation = False
    notes_blocks = []
    for key, label in (("work_mode", "Формат работы"), ("industries", "Домены"), ("extras", "Дополнительные пожелания")):
        value = str(answers.get(key) or "").strip()
        if value:
            notes_blocks.append(f"{label}: {value}")
    return {
        "target_titles": _split_items(answers.get("target_titles") or getattr(preferences, "target_titles", []) or context.get("inferred_roles") or []),
        "required_skills": _split_items(answers.get("required_skills") or getattr(preferences, "required_skills", []) or []),
        "preferred_skills": _split_items(answers.get("preferred_skills") or getattr(preferences, "preferred_skills", []) or context.get("detected_skills") or []),
        "preferred_locations": _split_items(work_mode or getattr(preferences, "preferred_locations", [])),
        "excluded_companies": _split_items(answers.get("excluded_companies") or getattr(preferences, "excluded_companies", []) or []),
        "forbidden_keywords": _split_items(answers.get("forbidden_keywords") or getattr(preferences, "forbidden_keywords", []) or getattr(preferences, "excluded_keywords", []) or []),
        "excluded_keywords": _split_items(answers.get("forbidden_keywords") or getattr(preferences, "excluded_keywords", []) or []),
        "salary_min": _parse_int(answers.get("salary_min")) if answers.get("salary_min") not in ("", None) else getattr(preferences, "salary_min", None),
        "remote_only": remote_only,
        "allow_relocation": allow_relocation,
        "headline": getattr(anamnesis, "headline", "") or context.get("resume_title") or ", ".join(context.get("inferred_roles") or []),
        "summary": getattr(anamnesis, "summary", "") or context.get("summary") or "",
        "experience_years": context.get("detected_experience") or getattr(anamnesis, "experience_years", 0.0),
        "primary_skills": getattr(anamnesis, "primary_skills", []) or context.get("detected_skills") or [],
        "industries": _split_items(answers.get("industries") or getattr(anamnesis, "industries", []) or []),
        "languages": _split_items(answers.get("languages") or getattr(anamnesis, "languages", []) or context.get("detected_languages") or []),
        "links": _split_items(answers.get("links") or getattr(anamnesis, "links", []) or context.get("detected_links") or []),
        "notes": "\n".join([str(getattr(preferences, "notes", "") or "").strip(), *notes_blocks]).strip(),
    }


def continue_intake_interview(store: WorkspaceStore, *, answer_text: str) -> dict[str, Any]:
    dashboard_state = store.load_dashboard_state()
    session = dict(dashboard_state.get("intake_dialog") or {})
    if not session or not session.get("active"):
        return start_intake_interview(store)
    questions = list(session.get("questions") or [])
    index = int(session.get("step_index") or 0)
    answers = dict(session.get("answers") or {})
    if questions:
        question = questions[min(index, len(questions) - 1)]
        answer_value = str(answer_text or "").strip()
        if not _answer_is_skip(answer_value) and not _answer_is_keep(answer_value):
            answers[str(question.get("id") or f"step_{index}")] = answer_value
        session["answers"] = answers
    next_index = index + 1
    if next_index < len(questions):
        session["step_index"] = next_index
        session["updated_at"] = utc_now_iso()
        store.update_dashboard_state({"intake_dialog": session})
        return {"action": "intake_interview", "status": "running", "session": session, "message": _format_intake_interview_question(session)}
    payload = _payload_from_intake_interview(store, session)
    result = run_intake(store, interactive=False, payload=payload)
    completed_session = {**session, "active": False, "completed": True, "completed_at": utc_now_iso(), "updated_at": utc_now_iso()}
    rules_contract = build_user_rules_contract(store.load_preferences(), store.load_anamnesis(), store.load_dashboard_state())
    store.update_dashboard_state({"intake_dialog": completed_session, "intake_dialog_completed": True, "intake_dialog_completed_at": utc_now_iso(), "intake_confirmed": False, "intake_confirmed_at": "", "intake_user_rules_contract": rules_contract})
    result["message"] = "Диалог завершён. Проверьте итоговые правила и подтвердите их перед запуском поиска."
    return result


def intake_interview_state(store: WorkspaceStore) -> dict[str, Any]:
    dashboard_state = store.load_dashboard_state()
    session = dict(dashboard_state.get("intake_dialog") or {})
    completed = bool(dashboard_state.get("intake_dialog_completed"))
    questions = list(session.get("questions") or [])
    step_index = int(session.get("step_index") or 0)
    current = questions[min(step_index, len(questions) - 1)] if questions else {}
    return {
        "completed": completed,
        "active": bool(session.get("active")),
        "step_index": step_index,
        "total_steps": len(questions),
        "current_question": current,
        "started_at": str(session.get("started_at") or ""),
        "updated_at": str(session.get("updated_at") or ""),
        "completed_at": str(dashboard_state.get("intake_dialog_completed_at") or session.get("completed_at") or ""),
    }


def _extract_section_map(raw_text: str) -> dict[str, str]:
    aliases = {
        "name": "full_name",
        "имя": "full_name",
        "целевая роль": "target_titles",
        "целевые роли": "target_titles",
        "что ищу сейчас": "notes",
        "что точно не хочу": "forbidden_keywords",
        "must-have навыки": "required_skills",
        "must have навыки": "required_skills",
        "желательные навыки": "preferred_skills",
        "сильные стороны": "primary_skills",
        "слабые стороны / пробелы": "secondary_skills",
        "слабые стороны": "secondary_skills",
        "опыт по годам": "experience_years",
        "отрасли и домены": "industries",
        "города / страны / часовые пояса": "preferred_locations",
        "удаленка / офис / гибрид": "work_mode",
        "минимальная зарплата": "salary_min",
        "компании и типы компаний, которые исключаю": "excluded_companies",
        "ключевые стоп-слова": "forbidden_keywords",
        "образование": "education",
        "языки": "languages",
        "ссылки": "links",
        "насколько важны критерии": "weights",
        "развернутый рассказ о себе и о желаемой работе": "long_form",
        "headline": "headline",
        "summary": "summary",
    }
    sections: dict[str, list[str]] = {}
    current = "long_form"
    for raw_line in str(raw_text or "").replace("\r", "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if ":" in line:
            head, tail = line.split(":", 1)
            key = aliases.get(head.strip().lower())
            if key:
                current = key
                sections.setdefault(current, [])
                if tail.strip():
                    sections[current].append(tail.strip())
                continue
        sections.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items() if value}


def _payload_from_open_intake(raw_text: str) -> dict[str, Any]:
    sections = _extract_section_map(raw_text)
    parsed_patch = parse_rule_request(raw_text)
    work_mode = str(sections.get("work_mode") or parsed_patch.get("notes") or "")
    notes = sections.get("notes", "")
    long_form = sections.get("long_form", "")
    all_text = "\n".join(part for part in [notes, long_form, str(parsed_patch.get("notes") or "")] if part).strip()
    detected_remote_only = _detect_remote_only(work_mode or all_text)
    detected_allow_relocation = _detect_allow_relocation(work_mode or all_text)
    extracted_locations = _split_items(sections.get("preferred_locations", "")) or _extract_locations_from_text(work_mode or all_text)
    required_skills = _split_items(sections.get("required_skills", "")) or _split_items(parsed_patch.get("required_skills") or []) or _skills_from_text(all_text)
    preferred_skills = _split_items(sections.get("preferred_skills", "")) or _skills_from_text(all_text)
    forbidden_keywords = _split_items(sections.get("forbidden_keywords", "")) or _split_items(parsed_patch.get("forbidden_keywords") or [])
    excluded_companies = _split_items(sections.get("excluded_companies", "")) or _split_items(parsed_patch.get("excluded_companies") or [])
    target_titles = _split_items(sections.get("target_titles", "")) or _parse_free_text_titles(all_text, [])
    summary = sections.get("summary") or long_form[:2000] or all_text[:2000]
    headline = sections.get("headline") or ", ".join(target_titles[:4])[:180]
    salary_min = _parse_int(sections.get("salary_min")) or _parse_int(parsed_patch.get("salary_min"))
    payload: dict[str, Any] = {
        "full_name": sections.get("full_name", ""),
        "target_titles": _unique_casefold(target_titles),
        "required_skills": _unique_casefold(required_skills),
        "preferred_skills": _unique_casefold(preferred_skills),
        "preferred_locations": _unique_casefold(extracted_locations),
        "excluded_companies": _unique_casefold(excluded_companies),
        "excluded_keywords": _unique_casefold(forbidden_keywords),
        "forbidden_keywords": _unique_casefold(forbidden_keywords),
        "salary_min": salary_min,
        "remote_only": bool(parsed_patch.get("remote_only")) if parsed_patch.get("remote_only") is not None else bool(detected_remote_only),
        "allow_relocation": bool(detected_allow_relocation),
        "notes": _compose_intake_notes(notes, sections.get("weights", ""), long_form, str(parsed_patch.get("notes") or "")),
        "headline": headline,
        "summary": summary,
        "experience_years": _parse_float(sections.get("experience_years"), 0.0),
        "primary_skills": _split_items(sections.get("primary_skills", "")) or _unique_casefold(required_skills + preferred_skills),
        "secondary_skills": _split_items(sections.get("secondary_skills", "")),
        "industries": _split_items(sections.get("industries", "")) or _extract_industries_from_text(all_text),
        "education": _split_items(sections.get("education", "")),
        "languages": _split_items(sections.get("languages", "")),
        "links": _split_items(sections.get("links", "")),
    }
    return payload


def _compose_rules_markdown(store: WorkspaceStore, preferences, anamnesis) -> str:
    return compose_rules_markdown(store, preferences, anamnesis)


def _require_intake(store: WorkspaceStore) -> tuple[Any, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    intake_state = evaluate_intake_readiness(preferences, anamnesis, store.load_dashboard_state())
    if not preferences or not anamnesis or not intake_state["ready"]:
        raise RuntimeError("intake is required before this action.")
    return preferences, anamnesis


def _require_rules(store: WorkspaceStore) -> None:
    if not store.load_selection_rules().strip() and not store.load_imported_rules():
        raise RuntimeError("rules are required before this action.")


def _require_mode_selection(store: WorkspaceStore) -> None:
    settings = store.load_runtime_settings()
    if not settings.mode_selected:
        raise RuntimeError("mode selection is required before running the selected workflow.")

def _ensure_cover_letter_draft(store: WorkspaceStore, *, vacancy_id: str, force: bool = False) -> str:
    vacancy_key = str(vacancy_id or "").strip()
    if not vacancy_key:
        return ""

    existing = store.load_cover_letter_draft(vacancy_key)
    legacy_markers = (
        "Кандидат имеет",
        "кандидат имеет",
        "ML/AI",
        "Здравствуйте! Меня заинтересовала вакансия",
        "в работе мне помогают навыки",
        "Мне близки задачи этой роли",
    )
    legacy_style = any(marker in existing for marker in legacy_markers)

    if existing.strip() and not force and not legacy_style:
        return existing

    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    assessments = {item.vacancy_id: item for item in store.load_assessments()}

    vacancy = vacancies.get(vacancy_key)
    assessment = assessments.get(vacancy_key)

    if not vacancy or not assessment or assessment.category != FitCategory.FIT:
        return existing

    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    if not preferences or not anamnesis:
        return existing

    generated = OpenRouterCoverLetterAgent().generate(
        vacancy=vacancy,
        assessment=assessment,
        preferences=preferences,
        anamnesis=anamnesis,
        resume_markdown=store.load_resume_markdown(),
        selection_rules=store.load_selection_rules(),
        imported_rules=store.load_imported_rules(),
        dashboard_state=store.load_dashboard_state(),
    ).strip()

    if generated:
        store.save_cover_letter_draft(vacancy_key, generated)

    return generated or existing

def _ensure_cover_letters_for_fit_vacancies(store: WorkspaceStore) -> dict[str, int]:
    generated = 0
    skipped = 0
    for assessment in store.load_assessments():
        if assessment.category != FitCategory.FIT:
            continue
        if _ensure_cover_letter_draft(store, vacancy_id=assessment.vacancy_id):
            generated += 1
        else:
            skipped += 1
    return {"generated": generated, "skipped": skipped}


def _today_apply_counter(store: WorkspaceStore) -> tuple[str, int]:
    state = store.load_dashboard_state()
    today = utc_now_iso()[:10]
    bucket = str(state.get("apply_daily_bucket") or "")
    count = int(state.get("apply_daily_count") or 0)
    if bucket != today:
        count = 0
    return today, count


def _bump_apply_counter(store: WorkspaceStore, increment: int) -> int:
    today, current = _today_apply_counter(store)
    next_count = current + max(0, int(increment))
    store.update_dashboard_state({"apply_daily_bucket": today, "apply_daily_count": next_count})
    return next_count


def build_rules_from_profile(store: WorkspaceStore) -> dict[str, Any]:
    preferences, anamnesis = _require_intake(store)
    rules_markdown = _compose_rules_markdown(store, preferences, anamnesis)
    store.save_selection_rules(rules_markdown)
    store.touch_dashboard_timestamp("last_rules_rebuilt_at")
    _mark_analysis_stale(store, "Profile rules changed after the last analysis. Run Analyze again to refresh classifications.")
    store.record_event("rules", "Rebuilt search rules from onboarding profile.")
    return {
        "action": "build_rules",
        "rules_ready": True,
        "rules_path": str(store.paths.rules_markdown_path),
    }


def run_intake(store: WorkspaceStore, *, interactive: bool = False, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload:
        preferences, anamnesis = IntakeAgent(store).ensure(interactive=False)
        preferences.full_name = str(payload.get("full_name") or preferences.full_name).strip()
        preferences.target_titles = _split_items(payload.get("target_titles") or preferences.target_titles)
        preferences.required_skills = _split_items(payload.get("required_skills") or preferences.required_skills)
        preferences.preferred_skills = _split_items(payload.get("preferred_skills") or preferences.preferred_skills)
        preferences.preferred_locations = _split_items(payload.get("preferred_locations") or preferences.preferred_locations)
        preferences.excluded_companies = _split_items(payload.get("excluded_companies") or preferences.excluded_companies)
        preferences.excluded_keywords = _split_items(payload.get("excluded_keywords") or preferences.excluded_keywords)
        preferences.forbidden_keywords = _split_items(payload.get("forbidden_keywords") or preferences.forbidden_keywords)
        preferences.salary_min = _parse_int(payload.get("salary_min")) if payload.get("salary_min") not in ("", None) else preferences.salary_min
        preferences.remote_only = bool(payload.get("remote_only", preferences.remote_only))
        preferences.allow_relocation = bool(payload.get("allow_relocation", preferences.allow_relocation))
        preferences.cover_letter_mode = str(payload.get("cover_letter_mode") or preferences.cover_letter_mode or "adaptive").strip()
        preferences.notes = str(payload.get("notes") or preferences.notes).strip()

        anamnesis.headline = str(payload.get("headline") or anamnesis.headline).strip()
        anamnesis.summary = str(payload.get("summary") or anamnesis.summary).strip()
        anamnesis.experience_years = _parse_float(payload.get("experience_years"), anamnesis.experience_years or 0.0)
        anamnesis.primary_skills = _split_items(payload.get("primary_skills") or anamnesis.primary_skills)
        anamnesis.secondary_skills = _split_items(payload.get("secondary_skills") or anamnesis.secondary_skills)
        anamnesis.industries = _split_items(payload.get("industries") or anamnesis.industries)
        anamnesis.achievements = _split_items(payload.get("achievements") or anamnesis.achievements)
        anamnesis.education = _split_items(payload.get("education") or anamnesis.education)
        anamnesis.languages = _split_items(payload.get("languages") or anamnesis.languages)
        anamnesis.links = _split_items(payload.get("links") or anamnesis.links)

        rules_markdown = _compose_rules_markdown(store, preferences, anamnesis)
        store.save_preferences(preferences)
        store.save_anamnesis(anamnesis)
        store.save_selection_rules(rules_markdown)
        store.touch_dashboard_timestamp("last_rules_rebuilt_at")
        store.update_dashboard_state(
            {
                "intake_dialog_completed": True,
                "intake_confirmed": True,
                "intake_confirmed_at": utc_now_iso(),
                "intake_user_rules_contract": build_user_rules_contract(preferences, anamnesis, store.load_dashboard_state()),
            }
        )
        _mark_analysis_stale(store, "Profile data changed after the last analysis. Run Analyze again to refresh classifications.")
        store.record_event("intake", "Saved onboarding questionnaire from dashboard.")
    else:
        preferences, anamnesis = IntakeAgent(store).ensure(interactive=interactive)
        if not interactive:
            store.update_dashboard_state(
                {
                    "intake_dialog_completed": True,
                    "intake_confirmed": True,
                    "intake_confirmed_at": utc_now_iso(),
                    "intake_user_rules_contract": build_user_rules_contract(preferences, anamnesis, store.load_dashboard_state()),
                }
            )
    store.touch_dashboard_timestamp("last_intake_at")
    return {
        "action": "intake",
        "preferences": preferences.to_dict(),
        "anamnesis": anamnesis.to_dict(),
        "rules_ready": bool(store.load_selection_rules().strip()),
    }


def confirm_intake_rules(store: WorkspaceStore) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    readiness = evaluate_intake_readiness(preferences, anamnesis, store.load_dashboard_state())
    if not readiness.get("dialog_completed"):
        raise RuntimeError("Сначала завершите intake-диалог, потом подтверждайте правила.")
    store.update_dashboard_state(
        {
            "intake_confirmed": True,
            "intake_confirmed_at": utc_now_iso(),
            "intake_user_rules_contract": build_user_rules_contract(preferences, anamnesis, store.load_dashboard_state()),
        }
    )
    store.record_event("intake", "Правила intake подтверждены пользователем.")
    return {
        "action": "confirm_intake",
        "status": "confirmed",
        "message": "Правила подтверждены. Можно переходить к синхронизации профиля, анализу и откликам.",
    }


def run_intake_from_text(store: WorkspaceStore, *, raw_text: str, source_name: str = "chat") -> dict[str, Any]:
    payload = _payload_from_open_intake(raw_text)
    result = run_intake(store, interactive=False, payload=payload)
    store.update_dashboard_state({"last_intake_source": source_name, "last_intake_raw_text": str(raw_text or "")[:12000]})
    result["message"] = "Подробный интейк сохранен. Правила поиска пересобраны из ваших ответов."
    result["source_name"] = source_name
    return result


def run_intake_from_file(store: WorkspaceStore, *, path_value: str) -> dict[str, Any]:
    filename, content = _read_text_attachment(path_value)
    result = run_intake_from_text(store, raw_text=content, source_name=filename)
    result["filename"] = filename
    return result


def _status_requires_repair(status: str) -> bool:
    return status in {"needs_repair", "needs_follow_up", "questionnaire_required", "completed_without_confirmation"}


def _status_counts_as_apply(status: str) -> bool:
    return status in {"completed", "completed_without_cover_letter", "completed_without_confirmation"}


def run_analyze(store: WorkspaceStore, *, limit: int = 0, interactive: bool = False, progress_callback=None) -> dict[str, Any]:
    _require_intake(store)
    _require_rules(store)
    hh_context = ensure_hh_context(store, auto_login=True)
    if hh_context["status"] != "ready":
        analysis_state = store.load_analysis_state()
        if analysis_state:
            analysis_state["stale"] = True
            analysis_state["stale_reason"] = str(hh_context.get("message") or "hh.ru preflight blocked a fresh analysis run.")
            store.save_analysis_state(analysis_state)
        store.record_event("hh-preflight", str(hh_context["message"]), details=hh_context)
        return {"action": "analyze", **hh_context, "status": "blocked"}


    run, assessments = asyncio.run(
        VacancyAnalysisAgent(store).analyze_async(
            limit=limit,
            progress_callback=progress_callback,
            max_concurrency=60,
        )
    )
    cover_letter_stats = _ensure_cover_letters_for_fit_vacancies(store)
    store.touch_dashboard_timestamp("last_analysis_at")
    analysis_state = store.load_analysis_state()
    refresh_result = dict(analysis_state.get("refresh_result") or {})
    refresh_message = str(refresh_result.get("message") or refresh_result.get("reason") or "vacancy source unknown")
    return {
        "action": "analyze",
        "status": "completed",
        "run": run.to_dict(),
        "assessments": len(assessments),
        "refresh_result": refresh_result,
        "rules_synced": True,
        "message": f"Processed {len(assessments)} vacancies. Source: {refresh_message}. Автосопроводительных для подходящих: {cover_letter_stats['generated']}.",
        "hh_context": hh_context,
        "cover_letter_stats": cover_letter_stats,
    }


def run_resume(store: WorkspaceStore) -> dict[str, Any]:
    preferences = store.load_preferences() or UserPreferences()
    anamnesis = store.load_anamnesis() or Anamnesis()
    sync_result = HHResumeProfileSync(store).sync_selected_resume()
    llm_analysis: dict[str, Any] = {}
    extracted = sync_result.get("extracted")
    if isinstance(extracted, dict) and extracted:
        llm_analysis = _llm_resume_intake_analysis(store, extracted=extracted)
    preferences = store.load_preferences() or preferences
    anamnesis = store.load_anamnesis() or anamnesis
    draft, markdown = ResumeAgent(store).build_resume_draft()
    rules_markdown = _compose_rules_markdown(store, preferences, anamnesis)
    store.save_selection_rules(rules_markdown)
    resume_extra: dict[str, Any] = {"last_rules_rebuilt_at": utc_now_iso()}
    store.touch_dashboard_timestamp("last_resume_draft_at", extra=resume_extra)
    _mark_analysis_stale(store, "Профиль и правила обновились. Для свежей очереди вакансий запустите анализ заново.")
    sync_message = str(sync_result.get("message") or "")
    change_count = len(list(sync_result.get("changes") or []))
    if sync_result.get("status") == "updated":
        message = f"Профиль синхронизирован с hh.ru, найдено изменений: {change_count}. Черновик резюме и правила поиска обновлены."
    elif sync_result.get("status") == "no_changes":
        message = "Профиль hh.ru уже был актуален. Черновик резюме и правила поиска всё равно пересобраны."
    elif sync_result.get("status") == "failed":
        message = f"Не удалось синхронизировать профиль с hh.ru: {sync_message}."
    else:
        message = "Черновик резюме и правила поиска обновлены."
    return {
        "action": "resume",
        "draft": draft.to_dict(),
        "markdown": markdown,
        "rules_ready": True,
        "sync_result": sync_result,
        "llm_analysis": llm_analysis,
        "message": message,
    }


def run_plan_filters(store: WorkspaceStore) -> dict[str, Any]:
    preferences, anamnesis = _require_intake(store)
    runtime_settings = store.load_runtime_settings()
    plan = HHFilterPlanner(
        preferences,
        anamnesis,
        selected_resume_id=store.load_selected_resume_id(),
        llm_backend=runtime_settings.llm_backend,
    ).build()
    store.save_filter_plan(plan)
    if plan.get("llm_planner_status") == "ok" and plan.get("planner_backend") != "rules":
        store.update_dashboard_state({"llm_gate": {}})
    store.record_event("filters", "Updated hh.ru filter plan from the current profile.", details=plan)
    return {
        "action": "plan_filters",
        "payload": plan,
        "message": f"План фильтров обновлен. Поисковый текст: {plan.get('search_text') or 'не задан'}.",
    }


def run_refresh_vacancies(
    store: WorkspaceStore,
    *,
    limit: int = 0,
    log_line: Any = None,
) -> dict[str, Any]:
    _require_intake(store)
    _require_rules(store)
    hh_context = ensure_hh_context(store, auto_login=True)
    if hh_context["status"] != "ready":
        return {"action": "refresh_vacancies", **hh_context, "status": "blocked"}
    result = HHVacancyRefresher(store).refresh(limit=limit, log_line=log_line)
    _mark_analysis_stale(store, "Источник вакансий обновлён. Запустите анализ заново, чтобы пересчитать оценки.")
    return {
        "action": "refresh_vacancies",
        "status": str(result.get("status") or "unknown"),
        "payload": result,
        "message": str(result.get("message") or "Обновление вакансий завершено."),
        "hh_context": hh_context,
    }


def select_resume_for_search(store: WorkspaceStore, *, resume_id: str) -> dict[str, Any]:
    selected_resume_id = str(resume_id or "").strip()
    store.save_selected_resume_id(selected_resume_id)
    store.touch_dashboard_timestamp(
        "last_resume_selection_at",
        extra={
            "resume_intake_analysis": {},
            "resume_intake_analysis_marker": "",
            "resume_intake_analysis_error": "",
            "llm_gate": {},
        },
    )
    _mark_analysis_stale(store, "Выбранное резюме hh.ru изменилось. Запустите анализ заново, чтобы получить свежую очередь вакансий.")
    store.record_event(
        "hh-resume-selection",
        f"Выбрано резюме hh.ru для live search: {selected_resume_id or 'сброшено'}.",
        details={"selected_resume_id": selected_resume_id},
    )
    return {
        "action": "select-resume",
        "selected_resume_id": selected_resume_id,
        "analysis_stale": True,
        "message": "Выбор резюме сохранен. Запустите анализ заново, чтобы собрать вакансии по этому резюме.",
    }


def select_hh_account(store: WorkspaceStore, *, account_key: str) -> dict[str, Any]:
    normalized = str(account_key or "").strip()
    if normalized == store.account_key:
        return {
            "action": "select-account",
            "account_key": normalized,
            "message": "Этот hh-аккаунт уже активен.",
        }
    accounts = {str(item.get("account_key") or ""): item for item in store.load_accounts()}
    if normalized not in accounts:
        raise RuntimeError("account_key was not found in saved hh accounts.")
    store.set_active_account(normalized)
    switched_store = WorkspaceStore(store.project_root, account_key=normalized)
    switched_store.save_account_profile({**accounts[normalized], "updated_at": utc_now_iso()})
    selected_resume_id = switched_store.load_selected_resume_id()
    hh_resumes = switched_store.load_hh_resumes()
    if not selected_resume_id and len(hh_resumes) == 1:
        selected_resume_id = str(hh_resumes[0].get("resume_id") or "").strip()
        if selected_resume_id:
            switched_store.save_selected_resume_id(selected_resume_id)
    profile_sync: dict[str, Any] = {}
    auto_resume: dict[str, Any] = {}
    if selected_resume_id:
        profile_sync = HHResumeProfileSync(switched_store).sync_selected_resume()
        if str(profile_sync.get("status") or "") in ("updated", "no_changes") and switched_store.load_preferences() and switched_store.load_anamnesis():
            auto_resume = run_resume(switched_store)
    switched_store.record_event(
        "hh-account",
        f"Switched active hh account to {accounts[normalized].get('display_name') or normalized}.",
        details={"account_key": normalized},
    )
    return {
        "action": "select-account",
        "account_key": normalized,
        "selected_resume_id": selected_resume_id,
        "profile_sync": profile_sync,
        "auto_resume": auto_resume,
        "message": f"Аккаунт hh.ru переключен: {accounts[normalized].get('display_name') or normalized}.",
    }


def delete_hh_account(store: WorkspaceStore, *, account_key: str) -> dict[str, Any]:
    result = store.delete_account_profile(account_key)
    deleted_key = str(result.get("deleted_account_key") or "")
    deleted_name = str(result.get("deleted_display_name") or deleted_key)
    next_account_key = str(result.get("next_account_key") or "default")
    active_changed = bool(result.get("active_changed"))
    message = (
        f"Профиль hh.ru удалён: {deleted_name}. Активный профиль переключён на {next_account_key}."
        if active_changed
        else f"Профиль hh.ru удалён: {deleted_name}."
    )
    next_store = WorkspaceStore(store.project_root)
    next_store.record_event(
        "hh-account",
        message,
        details={
            "deleted_account_key": deleted_key,
            "next_account_key": next_account_key,
            "active_changed": active_changed,
        },
    )
    return {
        "action": "delete-account",
        "deleted_account_key": deleted_key,
        "next_account_key": next_account_key,
        "active_changed": active_changed,
        "message": message,
    }


def run_plan_apply(store: WorkspaceStore, *, vacancy_id: str | None = None) -> dict[str, Any]:
    _require_intake(store)
    _require_rules(store)
    if not store.load_assessments():
        run_analyze(store, limit=0, interactive=False)
    if vacancy_id:
        _ensure_cover_letter_draft(store, vacancy_id=str(vacancy_id), force=True)
    payload = ApplicationAgent(store).build_plan(vacancy_id=vacancy_id)
    store.touch_dashboard_timestamp("last_apply_plan_at")
    vacancy_title = str(((payload.get("vacancy") or {}).get("title") or "выбранной вакансии"))
    return {
        "action": "plan_apply",
        "payload": payload,
        "message": f"План отклика собран для вакансии «{vacancy_title}».",
    }


def save_cover_letter_override(store: WorkspaceStore, *, vacancy_id: str, cover_letter: str) -> dict[str, Any]:
    normalized_vacancy_id = str(vacancy_id or "").strip()
    if not normalized_vacancy_id:
        raise RuntimeError("vacancy_id is required.")
    store.save_cover_letter_draft(normalized_vacancy_id, str(cover_letter or ""))
    apply_plan = store.load_apply_plan() or {}
    if ((apply_plan.get("vacancy") or {}).get("vacancy_id") or "") == normalized_vacancy_id:
        apply_plan["cover_letter_preview"] = str(cover_letter or "")
        apply_plan["cover_letter_enabled"] = bool(str(cover_letter or "").strip())
        store.save_apply_plan(apply_plan)
    store.record_event("cover-letter", "Сопроводительное письмо обновлено пользователем.", details={"vacancy_id": normalized_vacancy_id})
    _mark_analysis_stale(store, "Пользователь обновил сопроводительное письмо для отклика.")
    return {
        "action": "save_cover_letter",
        "vacancy_id": normalized_vacancy_id,
        "cover_letter_enabled": bool(str(cover_letter or "").strip()),
        "message": "Сопроводительное письмо сохранено.",
    }


def run_apply_submit(store: WorkspaceStore, *, vacancy_id: str, cover_letter: str = "") -> dict[str, Any]:
    if cover_letter.strip():
        store.save_cover_letter_draft(vacancy_id, cover_letter)
    elif not store.load_cover_letter_draft(vacancy_id).strip():
        _ensure_cover_letter_draft(store, vacancy_id=vacancy_id, force=False)
    result = apply_to_vacancy(store, vacancy_id=vacancy_id, cover_letter_override=cover_letter)
    status = str(((result.get("result") or {}).get("status") or "")).lower()
    relogin_result: dict[str, Any] | None = None
    if status == "needs_login":
        relogin_result = run_hh_login(store.project_root)
        if relogin_result.get("status") == "completed":
            result = apply_to_vacancy(store, vacancy_id=vacancy_id, cover_letter_override=cover_letter)
            status = str(((result.get("result") or {}).get("status") or "")).lower()
    result_payload = dict(result.get("result") or {})
    runtime_reason = str(result_payload.get("reason") or "").strip().lower()
    runtime_message = str(result_payload.get("message") or "").strip()
    if runtime_reason == "playwright_launch_denied" or "Failed to start Playwright browser" in runtime_message:
        store.update_dashboard_state(
            {
                "local_playwright_runtime_ready": False,
                "local_playwright_runtime_message": runtime_message or "Локальный Playwright-браузер недоступен в текущем окружении.",
                "local_playwright_runtime_failed_at": utc_now_iso(),
            }
        )
    elif status in {"completed", "completed_without_confirmation", "completed_without_cover_letter", "already_applied"}:
        store.update_dashboard_state(
            {
                "local_playwright_runtime_ready": True,
                "local_playwright_runtime_message": "",
                "local_playwright_runtime_failed_at": "",
            }
        )
    _bump_apply_counter(store, 1)
    store.touch_dashboard_timestamp("last_apply_submit_at")
    repair_result = None
    if _status_requires_repair(status):
        repair_result = run_plan_repair(
            store,
            action="apply_submit",
            payload={"vacancy_id": vacancy_id, "result": dict(result.get("result") or {})},
            error=status or "apply_flow_requires_follow_up",
            run_agent=True,
        )
    store.record_event(
        "apply",
        "Запущен отклик из дашборда.",
        details={
            "vacancy_id": vacancy_id,
            "status": ((result.get("result") or {}).get("status") or ""),
            "relogin_status": (relogin_result or {}).get("status", ""),
            "repair_triggered": bool(repair_result),
        },
    )
    payload = {
        "action": "apply_submit",
        "payload": result,
        "message": str(((result.get("result") or {}).get("message") or "Отклик обработан.")),
    }
    if relogin_result:
        payload["relogin_result"] = relogin_result
    if repair_result:
        payload["repair"] = repair_result
    return payload


def update_vacancy_feedback(store: WorkspaceStore, *, vacancy_id: str, decision: str) -> dict[str, Any]:
    vacancy_key = str(vacancy_id or "").strip()
    decision_key = str(decision or "").strip().lower()
    if not vacancy_key:
        raise RuntimeError("vacancy_id is required.")
    if decision_key not in {"fit", "doubt", "no_fit"}:
        raise RuntimeError("decision must be fit, doubt, or no_fit.")

    assessments = store.load_assessments()
    target = None
    for assessment in assessments:
        if assessment.vacancy_id == vacancy_key:
            target = assessment
            break
    if target is None:
        raise RuntimeError("vacancy assessment was not found.")

    target.category = FitCategory(decision_key)
    target.subcategory = f"user_{decision_key}"
    target.recommended_action = "user_override"
    target.review_notes = {
        "fit": "Пользователь вручную перевёл вакансию в «Подходит». Нужен быстрый отклик или хотя бы проверка автосопроводительного.",
        "doubt": "Пользователь вручную перевёл вакансию в «Сомневаюсь». Нужен короткий повторный разбор.",
        "no_fit": "Пользователь вручную исключил вакансию из приоритетных. Отклик по ней сейчас не нужен.",
    }[decision_key]
    target.explanation = target.review_notes
    store.save_assessments(assessments)
    store.save_vacancy_feedback_item(
        vacancy_key,
        {
            "decision": decision_key,
            "decided_at": utc_now_iso(),
        },
    )
    store.record_event("vacancy-feedback", "Пользователь изменил статус вакансии.", details={"vacancy_id": vacancy_key, "decision": decision_key})
    cover_letter_generated = False
    apply_plan_error = ""
    if decision_key == "fit":
        draft_text = _ensure_cover_letter_draft(store, vacancy_id=vacancy_key, force=True)
        cover_letter_generated = bool(str(draft_text or "").strip())
        try:
            run_plan_apply(store, vacancy_id=vacancy_key)
        except Exception as exc:  # noqa: BLE001
            apply_plan_error = str(exc)
    message = f"Статус вакансии обновлён: {decision_key}."
    if decision_key == "fit":
        if cover_letter_generated:
            message += " Сопроводительное письмо сгенерировано."
        else:
            message += " Черновик письма пуст — проверьте LLM/правила или нажмите «Собрать план отклика»."
        if apply_plan_error:
            message += f" План отклика не собран: {apply_plan_error}"
        else:
            message += " План отклика обновлён."
    return {
        "action": "vacancy_feedback",
        "vacancy_id": vacancy_key,
        "decision": decision_key,
        "cover_letter_generated": cover_letter_generated,
        "message": message,
    }


def run_apply_batch(
    store: WorkspaceStore,
    *,
    category: str,
    daily_limit: int = 200,
    min_delay_seconds: float = 12.0,
    max_delay_seconds: float = 27.0,
) -> dict[str, Any]:
    category_key = str(category or "").strip().lower()
    if category_key not in {"fit", "doubt", "no_fit"}:
        raise RuntimeError("category must be fit, doubt, or no_fit.")

    _, current_count = _today_apply_counter(store)
    remaining_budget = max(0, int(daily_limit) - current_count)
    if remaining_budget <= 0:
        raise RuntimeError("Дневной лимит откликов уже исчерпан.")

    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    assessments = [item for item in store.load_assessments() if item.category.value == category_key and item.vacancy_id in vacancies]
    queue = [item.vacancy_id for item in sorted(assessments, key=lambda item: item.score, reverse=True)[:remaining_budget]]
    if not queue:
        return {
            "action": "apply_batch",
            "category": category_key,
            "attempted": 0,
            "applied": 0,
            "failed": 0,
            "message": "В выбранной колонке сейчас нет вакансий для отклика.",
        }

    applied = 0
    failed = 0
    repairs = 0
    for index, vacancy_id in enumerate(queue):
        _ensure_cover_letter_draft(store, vacancy_id=vacancy_id, force=False)
        result = apply_to_vacancy(store, vacancy_id=vacancy_id, cover_letter_override="")
        status = str(((result.get("result") or {}).get("status") or "")).lower()
        if _status_counts_as_apply(status):
            applied += 1
            _bump_apply_counter(store, 1)
        elif status == "already_applied":
            pass
        else:
            failed += 1
            if _status_requires_repair(status):
                repairs += 1
                run_plan_repair(
                    store,
                    action="apply_batch",
                    payload={"vacancy_id": vacancy_id, "category": category_key, "result": dict(result.get("result") or {})},
                    error=status or "apply_flow_requires_follow_up",
                    run_agent=True,
                )
        if index < len(queue) - 1:
            time.sleep(random.uniform(min_delay_seconds, max_delay_seconds))

    store.touch_dashboard_timestamp("last_apply_submit_at")
    store.record_event(
        "apply-batch",
        f"Пакетный отклик по колонке {category_key}.",
        details={"category": category_key, "attempted": len(queue), "applied": applied, "failed": failed},
    )
    return {
        "action": "apply_batch",
        "category": category_key,
        "attempted": len(queue),
        "applied": applied,
        "failed": failed,
        "remaining_daily_budget": max(0, daily_limit - _today_apply_counter(store)[1]),
        "message": f"Пакетный отклик по колонке {category_key}: обработано {len(queue)}, успешных или доведённых до финального шага {applied}, с ошибкой {failed}.",
    }


def import_rules_text(store: WorkspaceStore, *, filename: str, markdown: str) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    if not preferences or not anamnesis:
        raise RuntimeError("Import rules requires intake first.")

    bundle = load_rule_bundle_from_text(filename or "dashboard_rules.md", markdown)
    temp_preferences, temp_anamnesis, imported_section = apply_rule_bundles(
        preferences,
        anamnesis,
        [bundle],
        current_rules_markdown="",
    )
    final_rules = build_selection_rules_markdown(temp_preferences, temp_anamnesis)
    if imported_section.strip():
        final_rules = f"{final_rules.rstrip()}\n\n{imported_section.strip()}\n"

    store.save_preferences(temp_preferences)
    store.save_anamnesis(temp_anamnesis)
    store.save_imported_rule(filename or "dashboard_rules.md", markdown)
    final_rules = _compose_rules_markdown(store, temp_preferences, temp_anamnesis)
    store.save_selection_rules(final_rules)
    store.touch_dashboard_timestamp("last_rules_rebuilt_at")
    _mark_analysis_stale(store, "Imported rules changed after the last analysis. Run Analyze again to refresh classifications.")
    store.record_event("rules", "Imported markdown rules from dashboard.", details={"path": filename or "dashboard_rules.md"})
    return {"action": "import_rules", "filename": filename or "dashboard_rules.md"}


def run_plan_repair(
    store: WorkspaceStore,
    *,
    action: str,
    payload: dict[str, Any],
    error: str,
    run_agent: bool = False,
) -> dict[str, Any]:
    runtime = HHAutomationRuntime(project_root=store.project_root)
    result = runtime.run_repair(action, payload, error) if run_agent else runtime.build_repair_plan(action, payload, error)
    store.save_repair_task(result)
    store.record_event("repair", f"Prepared repair task for {action}.", details={"status": result.get("status", "prepared")})
    return {"action": "repair", "payload": result}


def run_selected_mode(store: WorkspaceStore) -> dict[str, Any]:
    settings = store.load_runtime_settings()
    _require_mode_selection(store)
    mode = settings.dashboard_mode
    if mode == "apply_plan":
        return run_plan_apply(store)
    if mode == "repair":
        return {
            "action": "repair",
            "payload": {
                "status": "idle",
                "message": "Repair mode selected. Use a specific repair action from the dashboard.",
            },
        }
    if mode == "full_pipeline":
        analyze_result = run_analyze(store, limit=0, interactive=False)
        apply_result = run_plan_apply(store)
        return {"action": "full_pipeline", "analyze": analyze_result, "apply_plan": apply_result}
    return run_analyze(store, limit=0, interactive=False)


from __future__ import annotations

import random
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from autohhkek.agents.application_agent import ApplicationAgent
from autohhkek.agents.intake_agent import IntakeAgent
from autohhkek.agents.openrouter_resume_intake_agent import OpenRouterResumeIntakeAgent
from autohhkek.agents.resume_agent import ResumeAgent
from autohhkek.agents.vacancy_analysis_agent import VacancyAnalysisAgent
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import utc_now_iso
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.hh_apply import apply_to_vacancy
from autohhkek.services.hh_preflight import ensure_hh_context
from autohhkek.services.hh_resume_sync import HHResumeProfileSync
from autohhkek.services.chat_rule_parser import parse_rule_request
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.profile_rules import compose_rules_markdown
from autohhkek.services.rule_loader import apply_rule_bundles, load_rule_bundle_from_text
from autohhkek.services.rules import build_selection_rules_markdown, build_user_rules_contract, evaluate_intake_readiness
from autohhkek.services.storage import WorkspaceStore


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
    store.record_event("runtime-settings", "РћР±РЅРѕРІР»РµРЅС‹ РЅР°СЃС‚СЂРѕР№РєРё runtime.", details=saved.to_dict())
    return saved.to_dict()


def _split_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").replace("\r", "\n").replace(";", ",").replace("\n", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


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
        raise RuntimeError("Р¤Р°Р№Р» СЃ РѕС‚РІРµС‚Р°РјРё РЅРµ РЅР°Р№РґРµРЅ.")
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.name, path.read_text(encoding="utf-8")
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        return path.name, _extract_xml_text(xml)
    raise RuntimeError("РџРѕРґРґРµСЂР¶РёРІР°СЋС‚СЃСЏ С‚РѕР»СЊРєРѕ .md, .txt Рё .docx. Р”Р»СЏ .doc Р»СѓС‡С€Рµ СЃРѕС…СЂР°РЅРёС‚СЊ РІ .docx РёР»Рё РІСЃС‚Р°РІРёС‚СЊ С‚РµРєСЃС‚ РІ С‡Р°С‚.")


def _yes_no(value: bool) -> str:
    return "да" if value else "нет"


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
        "москва": "Москва",
        "санкт-петербург": "Санкт-Петербург",
        "петербург": "Санкт-Петербург",
        "новосибирск": "Новосибирск",
        "екатеринбург": "Екатеринбург",
        "казань": "Казань",
        "россия": "Россия",
        "рф": "Россия",
        "eu": "EU",
        "europe": "Europe",
    }
    result = [value for token, value in aliases.items() if token in lowered]
    if any(token in lowered for token in ("remote", "удален", "удалён", "гибрид", "офис")):
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
        "финтех": "Финтех",
        "healthcare": "Healthcare",
        "медицина": "Healthcare",
        "биотех": "Biotech",
        "edtech": "EdTech",
        "adtech": "AdTech",
        "cv": "Computer Vision",
        "computer vision": "Computer Vision",
        "robotics": "Robotics",
        "робот": "Robotics",
    }
    return _unique_casefold([value for token, value in known.items() if token in lowered])


def _detect_remote_only(text: str) -> bool | None:
    lowered = _normalize_text(text)
    if any(token in lowered for token in ("только remote", "только удал", "полностью удал", "remote only", "удаленка только", "удалёнка только")):
        return True
    if any(token in lowered for token in ("гибрид", "офис", "on-site", "onsite", "не только remote", "не только удал")):
        return False
    return None


def _detect_allow_relocation(text: str) -> bool | None:
    lowered = _normalize_text(text)
    if any(token in lowered for token in ("релокац", "переезд возможен", "готов к переезду")):
        return True
    if any(token in lowered for token in ("без переезда", "переезд не нужен", "не готов к переезду")):
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
        "агент": "Agents",
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
    if not selected_resume_id or not store.hh_state_path.exists():
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
            reason = agent.last_error or "OpenRouter недоступен для анализа резюме."
            store.update_dashboard_state(
                {
                    "llm_gate": {
                        "active": True,
                        "stage": "resume_intake",
                        "backend": "openrouter",
                        "message": reason[:1200],
                        "title": "LLM недоступна для анализа резюме",
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
        "message": "LLM fallback подтвержден. Продолжаю работу на эвристиках, пока модель недоступна.",
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
        "message": "Ок. Эвристики не включаю. Жду, пока LLM снова станет доступна.",
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
        topics.append(("????? ???? ??? ??? ??????? ????? ???????", "???????? 3-7 ?????, ?? ??????? ??????? ?????? ???????????.", '??????: "LLM Engineer, NLP Engineer, Research Scientist in NLP, Applied Scientist".'))
    if not anamnesis.primary_skills:
        topics.append(("????? ?????? ??????? ????? ??????? ??????", "??? ??, ??? ?? ???????? ?????? ????????? ???? ? ???????????????? ? ?? ????????.", '??????: "Python, NLP, LLM, Transformers, PyTorch, RAG".'))
    if not preferences.required_skills:
        topics.append(("??? ??? ??? must-have ?? ?????????", "??? ??????? ??????????: ???? ?? ???, ???????? ?????? ?? ?????.", '??????: "Python + NLP/LLM, research/product relevance, ??????????? ????????? ??????".'))
    if not preferences.preferred_skills:
        topics.append(("??? ??????????, ?? ?? ????????????", "??? ?????, ??????? ???????? ????????? ????????, ?? ?? ???????? ????-????????.", '??????: "RAG, MLOps, publication track, production ML, agentic systems".'))
    if not preferences.preferred_locations:
        topics.append(("????? ???????, ?????? ? ??????? ????? ??? ?????????", "???? ????? ????????, ??? ????? ??????? ??????? ?????????? ?????? ??? ???????? ??????? ??????.", '??????: "remote ?? ??, ??????, UTC+2..UTC+5, ?????? ?? ???????????".'))
    if not preferences.remote_only and not preferences.allow_relocation:
        topics.append(("????????? ??? ??? ???????? ?????? ???????", "????????, ??? ?????????: ?????? remote, ??????, ????, ???????? ?? ???????.", '??????: "?????? remote. ?????? ? ???? ?? ????????????. ??????? ?? ?????".'))
    if not preferences.salary_min:
        topics.append(("????? ??????????? ??????????? ????? ??????", "????? ??????? ?????? ??????? ? ???????? ????????? ????????.", '??????: "?? 350 000 net, ????????? 450 000+".'))
    if not preferences.excluded_companies:
        topics.append(("????? ???????? ? ???? ???????? ??????????", "????? ????? ??????????? ?????? ?????? ?????????????, ? ?? ?????? ????????? ??????.", '??????: "????????????, ????????????, research institutes, ??????????? ??? ??????? ML-??????".'))
    if not preferences.forbidden_keywords and not preferences.excluded_keywords:
        topics.append(("????? ????-????? ? ??????? ????? ????? ?????????", "??? ???????? ?????? ??????? ????? ?? ??????.", '??????: "CV, computer vision only, sales, analyst without ML, ????????????, ?????????".'))
    if not anamnesis.industries:
        topics.append(("????? ?????? ? ??????? ??? ????????? ??? ???????? ?????????????", "??? ?????, ????? ????? ???????? ???????? ??????? ???? ? ????????????? ???.", '??????: "?????????: AI infra, LLM products, applied research; ?? ????: ??????, ?????? ????????, adtech".'))
    if not anamnesis.education:
        topics.append(("??? ????? ????????????? ??? ??????????? ? ??????????", "???? ????? ???????? ??, ??? ????????? ???????????????? ? ?????? ?? ?????.", '??????: "????????????? ??????, ??????? ??????????, research background".'))
    if not anamnesis.languages:
        topics.append(("????? ????? ??????? ????????", "??? ?????? ? ?? ?????, ? ?? ??? ?????????????????, ? ?? ?????????? ????????.", '??????: "??????? ????????, ?????????? C1 ??? ??????/????????/?????????".'))
    if not anamnesis.links:
        topics.append(("????? ?????? ????? ???????????? ? ?????????", "???????? GitHub, Google Scholar, LinkedIn, ?????????, ??????, pet-projects.", '??????: "GitHub ..., Scholar ..., LinkedIn ...".'))
    return topics


def build_detailed_intake_prompt(store: WorkspaceStore) -> str:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    extracted = _safe_resume_sync_for_intake(store)
    summary = str(extracted.get("summary") or anamnesis.summary or preferences.notes or "").strip()
    resume_title = str(extracted.get("headline") or _resume_title_from_store(store) or anamnesis.headline or "?? ??????? ??????????").strip()
    inferred_roles = _infer_roles_from_resume_text(resume_title, summary, preferences.notes)
    detected_skills = list(extracted.get("skills") or anamnesis.primary_skills or _infer_skill_candidates(resume_title, summary, preferences.notes))
    detected_languages = list(extracted.get("languages") or anamnesis.languages)
    detected_links = list(extracted.get("links") or anamnesis.links)
    detected_experience = extracted.get("experience_years") if extracted.get("experience_years") is not None else anamnesis.experience_years
    topics = _missing_intake_topics(preferences, anamnesis)

    current_rules = [
        f"- ??????? ????: {', '.join(preferences.target_titles) if preferences.target_titles else '???? ?????'}",
        f"- Must-have ??????: {', '.join(preferences.required_skills) if preferences.required_skills else '???? ?????'}",
        f"- ??????????? ??????: {', '.join(preferences.preferred_skills) if preferences.preferred_skills else '???? ?????'}",
        f"- ???????: {', '.join(preferences.preferred_locations) if preferences.preferred_locations else '???? ?????'}",
        f"- ?????? ????????: {_yes_no(preferences.remote_only)}",
        f"- ??????????? ????????: {preferences.salary_min if preferences.salary_min else '?? ???????'}",
        f"- ??????????? ????????: {', '.join(preferences.excluded_companies) if preferences.excluded_companies else '???? ?????'}",
        f"- ????-?????: {', '.join(preferences.forbidden_keywords or preferences.excluded_keywords) if (preferences.forbidden_keywords or preferences.excluded_keywords) else '???? ?????'}",
    ]
    resume_facts = [
        f"- ????????? ??????: {resume_title}",
        f"- ????, ??????? ???????? ?? ??????: {', '.join(inferred_roles) if inferred_roles else '?? ??????? ???????? ????????'}",
        f"- ????: {detected_experience if detected_experience else '?? ??????? ???????? ??????????'}",
        f"- ?????????? ??????: {', '.join(detected_skills[:12]) if detected_skills else '????? ?????? ?? ????????'}",
        f"- ?????: {', '.join(detected_languages) if detected_languages else '?? ????????'}",
        f"- ??????: {', '.join(detected_links[:5]) if detected_links else '?? ???????'}",
    ]
    if summary:
        resume_facts.append(f"- ??????? summary ?? ??????: {summary[:350]}")

    gaps: list[str] = []
    if inferred_roles and not preferences.target_titles:
        gaps.append(f"? ?????? ??? ????? ???? {', '.join(inferred_roles)}, ?? ? ??????????? ???????? ??????? ???? ???? ??????")
    if detected_skills and not preferences.required_skills and not preferences.preferred_skills:
        gaps.append(f"? ?????? ? ???????? ????? ?????? {', '.join(detected_skills[:6])}, ?? must-have ? preferred skills ???? ?? ?????????")

    parts = [
        "? ??????? ????????? ????????? hh-?????? ? ??????? ??? ? ???????? ?????????.",
        "",
        "??? ??? ??????? ???????? ?? ??????:",
        *resume_facts,
        "",
        "??? ?????? ???????? ? ???????? ? ??????????:",
        *current_rules,
    ]
    if gaps:
        parts.extend(["", "??? ??? ???? ??? ????? ??????????? ????? ?????? ? ?????????:", *[f"- {item}" for item in gaps]])

    if topics:
        parts.extend(["", "?????? ?? ????? ???????????? ??? ?????? ??????. ???????? ?????? ?? ??????????? ??? ?????? ??? ?????? ?????? ????."])
        for index, (question, hint, example) in enumerate(topics, start=1):
            parts.extend(["", f"{index}. {question}", f"????? ??? ?????: {hint}", example])
    else:
        parts.extend(["", "??????????? ???? ??? ????????? ???????. ???????? ?????? ???? ?????????????? ?????????, ??????????? ? ????-???????, ??????? ??? ??? ? ????????.", '??????: "?? ???? ??????, ???????????? ? ???? ??? ?????? LLM/NLP-??????????; salary ???? 350k ??? ?????? ????????".'])

    parts.extend(["", "????? ???????? ????????? ??????? ??? ???????? ??????? ?? ???????.", "???? ???????, ????? ????????? ????: ?????? ?? ????? C:\????\answers.md"])
    return "\n".join(parts)


def begin_intake_dialog(store: WorkspaceStore) -> dict[str, Any]:
    result = start_intake_interview(store)
    return {
        "action": "intake-dialog",
        "status": str(result.get("status") or "running"),
        "dialog_state": dict(result.get("session") or {}),
        "message": str(result.get("message") or ""),
    }


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
        "llm_analysis": llm_analysis,
        "llm_blocked": False,
    }


def _build_intake_interview_questions(store: WorkspaceStore) -> list[dict[str, Any]]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    context = _interview_context(store)
    questions: list[dict[str, Any]] = []

    def add_question(question_id: str, title: str, hint: str, example: str, *, prefill: str = "", importance: str = "critical") -> None:
        questions.append(
            {
                "id": question_id,
                "title": title,
                "hint": hint,
                "example": example,
                "prefill": prefill,
                "importance": importance,
            }
        )

    add_question(
        "target_titles",
        "РљР°РєРёРµ СЂРѕР»Рё СЃС‡РёС‚Р°РµРј С†РµР»РµРІС‹РјРё РїСЂСЏРјРѕ СЃРµР№С‡Р°СЃ?",
        "Р­С‚Рѕ РіР»Р°РІРЅС‹Р№ С„РёР»СЊС‚СЂ РґР»СЏ РїРѕРёСЃРєР° Рё РѕС†РµРЅРєРё РІР°РєР°РЅСЃРёР№.",
        'РџСЂРёРјРµСЂ: "LLM Engineer, NLP Engineer, Research Scientist in NLP, Applied Scientist".',
        prefill=", ".join(getattr(preferences, "target_titles", []) or context["inferred_roles"]),
    )
    add_question(
        "required_skills",
        "Р§С‚Рѕ РґР»СЏ РІР°СЃ must-have РІРѕ РІР°РєР°РЅСЃРёРё?",
        "Р•СЃР»Рё СЌС‚РѕРіРѕ РЅРµС‚, РІР°РєР°РЅСЃРёСЋ РѕР±С‹С‡РЅРѕ РЅРµ СЃС‚РѕРёС‚ РїСЂРѕРґРІРёРіР°С‚СЊ РґР°Р»СЊС€Рµ.",
        'РџСЂРёРјРµСЂ: "Python, NLP/LLM, СЃРёР»СЊРЅС‹Р№ РёРЅР¶РµРЅРµСЂРЅС‹Р№ РёР»Рё РёСЃСЃР»РµРґРѕРІР°С‚РµР»СЊСЃРєРёР№ РєРѕРЅС‚РµРєСЃС‚".',
        prefill=", ".join(getattr(preferences, "required_skills", []) or context["detected_skills"][:6]),
    )
    add_question(
        "work_mode",
        "РљР°РєРѕР№ С„РѕСЂРјР°С‚ СЂР°Р±РѕС‚С‹ РґРѕРїСѓСЃС‚РёРј?",
        "Р­С‚Рѕ РєСЂРёС‚РёС‡РЅРѕ РґР»СЏ РѕС‚Р±РѕСЂР°: remote, РіРёР±СЂРёРґ, РѕС„РёСЃ, СЂРµР»РѕРєР°С†РёСЏ, СЃС‚СЂР°РЅС‹, С‡Р°СЃРѕРІС‹Рµ РїРѕСЏСЃР°.",
        'РџСЂРёРјРµСЂ: "РўРѕР»СЊРєРѕ remote. Р РѕСЃСЃРёСЏ РёР»Рё РјРµР¶РґСѓРЅР°СЂРѕРґРЅС‹Рµ РєРѕРјР°РЅРґС‹. РџРµСЂРµРµР·Рґ РЅРµ РЅСѓР¶РµРЅ".',
        prefill=", ".join(getattr(preferences, "preferred_locations", [])),
    )
    add_question(
        "excluded_companies",
        "РљР°РєРёРµ РєРѕРјРїР°РЅРёРё РёР»Рё С‚РёРїС‹ СЂР°Р±РѕС‚РѕРґР°С‚РµР»РµР№ С‚РѕС‡РЅРѕ РёСЃРєР»СЋС‡Р°РµРј?",
        "Р­С‚Рѕ РЅСѓР¶РЅРѕ, С‡С‚РѕР±С‹ РЅРµ С‚СЂР°С‚РёС‚СЊ РІСЂРµРјСЏ РЅР° Р·Р°РІРµРґРѕРјРѕ РЅРµР¶РµР»Р°С‚РµР»СЊРЅС‹Рµ РІР°РєР°РЅСЃРёРё.",
        'РџСЂРёРјРµСЂ: "РіРѕСЃСЃС‚СЂСѓРєС‚СѓСЂС‹, СѓРЅРёРІРµСЂСЃРёС‚РµС‚С‹, research institutes, РѕРєРѕР»РѕРіРѕСЃ РїСЂРѕРµРєС‚С‹".',
        prefill=", ".join(getattr(preferences, "excluded_companies", [])),
    )
    add_question(
        "forbidden_keywords",
        "РљР°РєРёРµ СЃС‚РѕРї-СЃР»РѕРІР° Рё РїСЂРёР·РЅР°РєРё СЂРµР¶СѓС‚ РІР°РєР°РЅСЃРёСЋ СЃСЂР°Р·Сѓ?",
        "Р­С‚Рѕ РїРѕРјРѕРіР°РµС‚ Р±С‹СЃС‚СЂРѕ РІС‹С‡РёС‰Р°С‚СЊ С€СѓРј РёР· Р±РѕР»СЊС€РѕР№ РІС‹РґР°С‡Рё.",
        'РџСЂРёРјРµСЂ: "CV only, computer vision only, sales, РїСЂРµРїРѕРґР°РІР°РЅРёРµ, РіРѕСЃСЃР»СѓР¶Р±Р°".',
        prefill=", ".join((getattr(preferences, "forbidden_keywords", []) or []) + (getattr(preferences, "excluded_keywords", []) or [])),
    )
    add_question(
        "salary_min",
        "РљР°РєР°СЏ РјРёРЅРёРјР°Р»СЊРЅР°СЏ РєРѕРјРїРµРЅСЃР°С†РёСЏ РёРјРµРµС‚ СЃРјС‹СЃР»?",
        "РњРѕР¶РЅРѕ РЅР°Р·РІР°С‚СЊ РЅРёР¶РЅСЋСЋ РіСЂР°РЅРёС†Сѓ РёР»Рё РїСЂСЏРјРѕ СЃРєР°Р·Р°С‚СЊ, С‡С‚Рѕ Р¶РµСЃС‚РєРѕРіРѕ РїРѕСЂРѕРіР° РЅРµС‚.",
        'РџСЂРёРјРµСЂ: "РћС‚ 350000 net" РёР»Рё "Р–РµСЃС‚РєРѕРіРѕ РїРѕСЂРѕРіР° РЅРµС‚".',
        prefill=str(getattr(preferences, "salary_min", "") or ""),
    )
    add_question(
        "industries",
        "РљР°РєРёРµ РґРѕРјРµРЅС‹ РІР°Рј РёРЅС‚РµСЂРµСЃРЅС‹, Р° РєР°РєРёРµ РЅРµР¶РµР»Р°С‚РµР»СЊРЅС‹?",
        "Р­С‚Рѕ РїРѕРјРѕРіР°РµС‚ Р°РіРµРЅС‚Сѓ РїРѕРЅРёРјР°С‚СЊ СЃРјС‹СЃР» РІР°РєР°РЅСЃРёРё, Р° РЅРµ С‚РѕР»СЊРєРѕ СЃР»РѕРІР° РІ РЅР°Р·РІР°РЅРёРё.",
        'РџСЂРёРјРµСЂ: "РёРЅС‚РµСЂРµСЃРЅРѕ: LLM products, AI infra; РЅРµ С…РѕС‡Сѓ: РіРѕСЃСѓС…Р°, С‡РёСЃС‚Р°СЏ Р°РєР°РґРµРјРёСЏ".',
        prefill=", ".join(getattr(anamnesis, "industries", [])),
        importance="important",
    )
    add_question(
        "preferred_skills",
        "Р§С‚Рѕ Р¶РµР»Р°С‚РµР»СЊРЅРѕ, РЅРѕ РЅРµ РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ?",
        "Р­С‚Рѕ РїРѕРІС‹С€Р°РµС‚ РїСЂРёРѕСЂРёС‚РµС‚, РЅРѕ РЅРµ СЏРІР»СЏРµС‚СЃСЏ Р¶РµСЃС‚РєРёРј СЃС‚РѕРї-С„Р°РєС‚РѕСЂРѕРј.",
        'РџСЂРёРјРµСЂ: "RAG, MLOps, production ML, agents".',
        prefill=", ".join(getattr(preferences, "preferred_skills", [])),
        importance="important",
    )
    add_question(
        "languages",
        "РљР°РєРёРµ СЏР·С‹РєРё СЂРµР°Р»СЊРЅРѕ СЂР°Р±РѕС‡РёРµ?",
        "Р­С‚Рѕ РІР»РёСЏРµС‚ РЅР° РїРѕРёСЃРє, Р°РЅРєРµС‚С‹ Рё С‚РѕРЅ СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С….",
        'РџСЂРёРјРµСЂ: "Р СѓСЃСЃРєРёР№ СЃРІРѕР±РѕРґРЅРѕ, Р°РЅРіР»РёР№СЃРєРёР№ C1".',
        prefill=", ".join(getattr(anamnesis, "languages", []) or context["detected_languages"]),
        importance="optional",
    )
    add_question(
        "links",
        "РљР°РєРёРµ СЃСЃС‹Р»РєРё СЃС‚РѕРёС‚ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ РІ РѕС‚РєР»РёРєР°С…?",
        "GitHub, Scholar, LinkedIn, РїРѕСЂС‚С„РѕР»РёРѕ Рё СЃС‚Р°С‚СЊРё СѓСЃРёР»РёРІР°СЋС‚ РїСЂРѕС„РёР»СЊ.",
        'РџСЂРёРјРµСЂ: "GitHub ..., Scholar ..., LinkedIn ...".',
        prefill=", ".join(getattr(anamnesis, "links", []) or context["detected_links"]),
        importance="optional",
    )
    add_question(
        "extras",
        "Р•СЃС‚СЊ Р»Рё РµС‰Рµ РїРѕР¶РµР»Р°РЅРёСЏ РёР»Рё С‚РѕРЅРєРёРµ РѕРіСЂР°РЅРёС‡РµРЅРёСЏ?",
        "Р­С‚Рѕ СѓР¶Рµ РЅРµРѕР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ РЅСЋР°РЅСЃС‹ РґР»СЏ СЂР°РЅР¶РёСЂРѕРІР°РЅРёСЏ Рё СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С….",
        'РџСЂРёРјРµСЂ: "РЅРµ С…РѕС‡Сѓ СЃР»РёС€РєРѕРј junior-СЂРѕР»Рё; РїРёСЃСЊРјРѕ РІСЃРµРіРґР° С‚РѕР»СЊРєРѕ РЅР° СЂСѓСЃСЃРєРѕРј".',
        importance="optional",
    )
    return questions


def _format_intake_interview_question(session: dict[str, Any]) -> str:
    questions = list(session.get("questions") or [])
    index = int(session.get("step_index") or 0)
    if not questions:
        return "Р’РѕРїСЂРѕСЃРѕРІ РЅРµ РѕСЃС‚Р°Р»РѕСЃСЊ. РњРѕР¶РЅРѕ Р·Р°РІРµСЂС€Р°С‚СЊ РёРЅС‚РµР№Рє."
    question = questions[min(index, len(questions) - 1)]
    lines: list[str] = []
    if index == 0:
        context = dict(session.get("context") or {})
        lines.extend(
            [
                "РЎРЅР°С‡Р°Р»Р° Р·Р°С„РёРєСЃРёСЂСѓРµРј РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ С‚СЂРµР±РѕРІР°РЅРёСЏ РєР°РЅРґРёРґР°С‚Р°. РџРѕРєР° СЌС‚РѕС‚ СЌС‚Р°Рї РЅРµ Р·Р°РІРµСЂС€РµРЅ, РїРѕРёСЃРє РІР°РєР°РЅСЃРёР№ Рё Р°РЅР°Р»РёР· РЅРµ Р·Р°РїСѓСЃРєР°СЋС‚СЃСЏ.",
                "",
                "Р§С‚Рѕ СѓР¶Рµ СѓРґР°Р»РѕСЃСЊ РІС‹С‚Р°С‰РёС‚СЊ РёР· hh-СЂРµР·СЋРјРµ:",
                f"- Р—Р°РіРѕР»РѕРІРѕРє: {context.get('resume_title') or 'РЅРµ СѓРґР°Р»РѕСЃСЊ РѕРїСЂРµРґРµР»РёС‚СЊ'}",
                f"- Р РѕР»Рё РёР· СЂРµР·СЋРјРµ: {', '.join(context.get('inferred_roles') or []) or 'РЅРµ СѓРґР°Р»РѕСЃСЊ СѓРІРµСЂРµРЅРЅРѕ РІС‹РґРµР»РёС‚СЊ'}",
                f"- РќР°РІС‹РєРё РёР· СЂРµР·СЋРјРµ: {', '.join((context.get('detected_skills') or [])[:8]) or 'РїРѕС‡С‚Рё РЅРёС‡РµРіРѕ РЅРµ РІС‹РґРµР»РµРЅРѕ'}",
                "",
            ]
        )
    lines.extend([f"Р’РѕРїСЂРѕСЃ {index + 1} РёР· {len(questions)}.", question["title"], f"РџРѕС‡РµРјСѓ СЃРїСЂР°С€РёРІР°СЋ: {question['hint']}"])
    if question.get("prefill"):
        lines.append(f"Р§С‚Рѕ СѓР¶Рµ РІРёР¶Сѓ: {question['prefill']}")
    lines.extend([question["example"], "РњРѕР¶РЅРѕ РѕС‚РІРµС‚РёС‚СЊ СЃРІРѕР±РѕРґРЅРѕ. Р•СЃР»Рё С‚РµРєСѓС‰РµРµ РїРѕРЅРёРјР°РЅРёРµ РїРѕРґС…РѕРґРёС‚, РЅР°РїРёС€РёС‚Рµ: РѕСЃС‚Р°РІРёС‚СЊ РєР°Рє РµСЃС‚СЊ. Р•СЃР»Рё РїСѓРЅРєС‚ РЅРµРІР°Р¶РµРЅ, РЅР°РїРёС€РёС‚Рµ: РїСЂРѕРїСѓСЃС‚РёС‚СЊ."])
    return "\n".join(lines)


def start_intake_interview(store: WorkspaceStore) -> dict[str, Any]:
    context = _interview_context(store)
    if context.get("llm_blocked"):
        return {
            "action": "intake_interview",
            "status": "blocked",
            "session": {},
            "message": str(context.get("llm_message") or "LLM недоступна для анализа резюме."),
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
    return normalized in {"РїСЂРѕРїСѓСЃС‚РёС‚СЊ", "skip", "РґР°Р»СЊС€Рµ", "РЅРµ Р·РЅР°СЋ", "Р±РµР· РѕС‚РІРµС‚Р°"}


def _answer_is_keep(value: str) -> bool:
    normalized = _normalize_text(value)
    return normalized in {"РѕСЃС‚Р°РІРёС‚СЊ РєР°Рє РµСЃС‚СЊ", "РєР°Рє РµСЃС‚СЊ", "РїРѕРґС‚РІРµСЂР¶РґР°СЋ", "РѕРє", "РґР°"}


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
        if any(token in normalized_work_mode for token in ("С‚РѕР»СЊРєРѕ СѓРґР°Р»", "remote only", "С‚РѕР»СЊРєРѕ remote")):
            remote_only = True
        elif any(token in normalized_work_mode for token in ("РіРёР±СЂРёРґ", "РѕС„РёСЃ")):
            remote_only = False
        if any(token in normalized_work_mode for token in ("СЂРµР»РѕРєР°С†", "РїРµСЂРµРµР·Рґ РІРѕР·РјРѕР¶РµРЅ", "РіРѕС‚РѕРІ Рє РїРµСЂРµРµР·РґСѓ")):
            allow_relocation = True
        elif any(token in normalized_work_mode for token in ("Р±РµР· РїРµСЂРµРµР·РґР°", "РїРµСЂРµРµР·Рґ РЅРµ РЅСѓР¶РµРЅ", "РЅРµ РіРѕС‚РѕРІ Рє РїРµСЂРµРµР·РґСѓ")):
            allow_relocation = False
    notes_blocks = []
    for key, label in (("work_mode", "Р¤РѕСЂРјР°С‚ СЂР°Р±РѕС‚С‹"), ("industries", "Р”РѕРјРµРЅС‹"), ("extras", "Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РїРѕР¶РµР»Р°РЅРёСЏ")):
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
    result["message"] = "Опрос завершен. Правила кандидата собраны. Проверьте краткую сводку и подтвердите правила, только после этого запущу поиск вакансий."
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
        "РёРјСЏ": "full_name",
        "С†РµР»РµРІР°СЏ СЂРѕР»СЊ": "target_titles",
        "С†РµР»РµРІС‹Рµ СЂРѕР»Рё": "target_titles",
        "С‡С‚Рѕ РёС‰Сѓ СЃРµР№С‡Р°СЃ": "notes",
        "С‡С‚Рѕ С‚РѕС‡РЅРѕ РЅРµ С…РѕС‡Сѓ": "forbidden_keywords",
        "must-have РЅР°РІС‹РєРё": "required_skills",
        "must have РЅР°РІС‹РєРё": "required_skills",
        "Р¶РµР»Р°С‚РµР»СЊРЅС‹Рµ РЅР°РІС‹РєРё": "preferred_skills",
        "СЃРёР»СЊРЅС‹Рµ СЃС‚РѕСЂРѕРЅС‹": "primary_skills",
        "СЃР»Р°Р±С‹Рµ СЃС‚РѕСЂРѕРЅС‹ / РїСЂРѕР±РµР»С‹": "secondary_skills",
        "СЃР»Р°Р±С‹Рµ СЃС‚РѕСЂРѕРЅС‹": "secondary_skills",
        "РѕРїС‹С‚ РїРѕ РіРѕРґР°Рј": "experience_years",
        "РѕС‚СЂР°СЃР»Рё Рё РґРѕРјРµРЅС‹": "industries",
        "РіРѕСЂРѕРґР° / СЃС‚СЂР°РЅС‹ / С‡Р°СЃРѕРІС‹Рµ РїРѕСЏСЃР°": "preferred_locations",
        "СѓРґР°Р»РµРЅРєР° / РѕС„РёСЃ / РіРёР±СЂРёРґ": "work_mode",
        "РјРёРЅРёРјР°Р»СЊРЅР°СЏ Р·Р°СЂРїР»Р°С‚Р°": "salary_min",
        "РєРѕРјРїР°РЅРёРё Рё С‚РёРїС‹ РєРѕРјРїР°РЅРёР№, РєРѕС‚РѕСЂС‹Рµ РёСЃРєР»СЋС‡Р°СЋ": "excluded_companies",
        "РєР»СЋС‡РµРІС‹Рµ СЃС‚РѕРї-СЃР»РѕРІР°": "forbidden_keywords",
        "РѕР±СЂР°Р·РѕРІР°РЅРёРµ": "education",
        "СЏР·С‹РєРё": "languages",
        "СЃСЃС‹Р»РєРё": "links",
        "РЅР°СЃРєРѕР»СЊРєРѕ РІР°Р¶РЅС‹ РєСЂРёС‚РµСЂРёРё": "weights",
        "СЂР°Р·РІРµСЂРЅСѓС‚С‹Р№ СЂР°СЃСЃРєР°Р· Рѕ СЃРµР±Рµ Рё Рѕ Р¶РµР»Р°РµРјРѕР№ СЂР°Р±РѕС‚Рµ": "long_form",
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
    work_mode = sections.get("work_mode", "").lower()
    notes = sections.get("notes", "")
    long_form = sections.get("long_form", "")
    summary = sections.get("summary") or long_form[:2000]
    headline = sections.get("headline") or ", ".join(_split_items(sections.get("target_titles", "")))[:180]
    payload: dict[str, Any] = {
        "full_name": sections.get("full_name", ""),
        "target_titles": _split_items(sections.get("target_titles", "")),
        "required_skills": _split_items(sections.get("required_skills", "")),
        "preferred_skills": _split_items(sections.get("preferred_skills", "")),
        "preferred_locations": _split_items(sections.get("preferred_locations", "")),
        "excluded_companies": _split_items(sections.get("excluded_companies", "")),
        "excluded_keywords": _split_items(sections.get("forbidden_keywords", "")),
        "forbidden_keywords": _split_items(sections.get("forbidden_keywords", "")),
        "salary_min": _parse_int(sections.get("salary_min")),
        "remote_only": any(token in work_mode for token in ("remote", "home office", "СѓРґР°Р»РµРЅ", "СѓРґР°Р»С‘РЅ")),
        "allow_relocation": any(token in work_mode for token in ("relocation", "РїРµСЂРµРµР·Рґ", "СЂРµР»РѕРєР°С†")),
        "notes": "\n\n".join(part for part in [notes, sections.get("weights", ""), long_form] if part).strip(),
        "headline": headline,
        "summary": summary,
        "experience_years": _parse_float(sections.get("experience_years"), 0.0),
        "primary_skills": _split_items(sections.get("primary_skills", "")),
        "secondary_skills": _split_items(sections.get("secondary_skills", "")),
        "industries": _split_items(sections.get("industries", "")),
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
    if existing.strip() and not force:
        return existing
    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    assessments = {item.vacancy_id: item for item in store.load_assessments()}
    vacancy = vacancies.get(vacancy_key)
    assessment = assessments.get(vacancy_key)
    if not vacancy or not assessment or assessment.category != FitCategory.FIT:
        return existing
    generated = ResumeAgent(store).build_cover_letter(vacancy, assessment).strip()
    if generated:
        store.save_cover_letter_draft(vacancy_key, generated)
    return generated


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
                "intake_confirmed": False,
                "intake_confirmed_at": "",
                "intake_user_rules_contract": build_user_rules_contract(preferences, anamnesis, store.load_dashboard_state()),
            }
        )
        _mark_analysis_stale(store, "Profile data changed after the last analysis. Run Analyze again to refresh classifications.")
        store.record_event("intake", "Saved onboarding questionnaire from dashboard.")
    else:
        preferences, anamnesis = IntakeAgent(store).ensure(interactive=interactive)
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
        raise RuntimeError("Сначала завершите обязательный intake-диалог.")
    store.update_dashboard_state(
        {
            "intake_confirmed": True,
            "intake_confirmed_at": utc_now_iso(),
            "intake_user_rules_contract": build_user_rules_contract(preferences, anamnesis, store.load_dashboard_state()),
        }
    )
    store.record_event("intake", "Пользователь подтвердил итоговые правила поиска.")
    return {
        "action": "confirm_intake",
        "status": "confirmed",
        "message": "Правила подтверждены. Теперь можно запускать поиск вакансий и анализ.",
    }


def run_intake_from_text(store: WorkspaceStore, *, raw_text: str, source_name: str = "chat") -> dict[str, Any]:
    payload = _payload_from_open_intake(raw_text)
    result = run_intake(store, interactive=False, payload=payload)
    store.update_dashboard_state({"last_intake_source": source_name, "last_intake_raw_text": str(raw_text or "")[:12000]})
    result["message"] = "РџРѕРґСЂРѕР±РЅС‹Р№ РёРЅС‚РµР№Рє СЃРѕС…СЂР°РЅРµРЅ. РџСЂР°РІРёР»Р° РїРѕРёСЃРєР° РїРµСЂРµСЃРѕР±СЂР°РЅС‹ РёР· РІР°С€РёС… РѕС‚РІРµС‚РѕРІ."
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


def run_analyze(store: WorkspaceStore, *, limit: int = 120, interactive: bool = False, progress_callback=None) -> dict[str, Any]:
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
    run, assessments = VacancyAnalysisAgent(store).analyze(limit=limit, progress_callback=progress_callback)
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
        "message": f"Processed {len(assessments)} vacancies. Source: {refresh_message}. РђРІС‚РѕСЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С… РґР»СЏ РїРѕРґС…РѕРґСЏС‰РёС…: {cover_letter_stats['generated']}.",
        "hh_context": hh_context,
        "cover_letter_stats": cover_letter_stats,
    }


def run_resume(store: WorkspaceStore) -> dict[str, Any]:
    _require_intake(store)
    sync_result = HHResumeProfileSync(store).sync_selected_resume()
    draft, markdown = ResumeAgent(store).build_resume_draft()
    preferences, anamnesis = _require_intake(store)
    rules_markdown = _compose_rules_markdown(store, preferences, anamnesis)
    store.save_selection_rules(rules_markdown)
    store.touch_dashboard_timestamp("last_resume_draft_at", extra={"last_rules_rebuilt_at": utc_now_iso()})
    _mark_analysis_stale(store, "Р§РµСЂРЅРѕРІРёРє СЂРµР·СЋРјРµ Рё РїСЂР°РІРёР»Р° РїСЂРѕС„РёР»СЏ РѕР±РЅРѕРІР»РµРЅС‹. Р—Р°РїСѓСЃС‚РёС‚Рµ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ, С‡С‚РѕР±С‹ РїРµСЂРµСЃС‡РёС‚Р°С‚СЊ РІР°РєР°РЅСЃРёРё.")
    sync_message = str(sync_result.get("message") or "")
    change_count = len(list(sync_result.get("changes") or []))
    if sync_result.get("status") == "updated":
        message = f"РџСЂРѕС„РёР»СЊ СЃРёРЅС…СЂРѕРЅРёР·РёСЂРѕРІР°РЅ СЃ hh.ru, РЅР°Р№РґРµРЅРѕ РёР·РјРµРЅРµРЅРёР№: {change_count}. Р§РµСЂРЅРѕРІРёРє Рё РїСЂР°РІРёР»Р° РѕР±РЅРѕРІР»РµРЅС‹. Р—Р°РїСѓСЃС‚РёС‚Рµ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ."
    elif sync_result.get("status") == "no_changes":
        message = "Р’С‹Р±СЂР°РЅРЅРѕРµ СЂРµР·СЋРјРµ hh.ru РїСЂРѕРІРµСЂРµРЅРѕ, РёР·РјРµРЅРµРЅРёР№ РІ РїСЂРѕС„РёР»Рµ РЅРµ РЅР°Р№РґРµРЅРѕ. Р§РµСЂРЅРѕРІРёРє РѕР±РЅРѕРІР»РµРЅ, РјРѕР¶РЅРѕ Р·Р°РїСѓСЃРєР°С‚СЊ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ."
    elif sync_result.get("status") == "failed":
        message = f"Р§РµСЂРЅРѕРІРёРє РѕР±РЅРѕРІР»РµРЅ, РЅРѕ СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РїСЂРѕС„РёР»СЏ hh.ru Р·Р°РІРµСЂС€РёР»Р°СЃСЊ РѕС€РёР±РєРѕР№: {sync_message}."
    else:
        message = "Р§РµСЂРЅРѕРІРёРє РѕР±РЅРѕРІР»РµРЅ, РїСЂР°РІРёР»Р° РїРµСЂРµСЃРѕР±СЂР°РЅС‹ РёР· С‚РµРєСѓС‰РµРіРѕ РїСЂРѕС„РёР»СЏ. РњРѕР¶РЅРѕ Р·Р°РїСѓСЃРєР°С‚СЊ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ."
    return {
        "action": "resume",
        "draft": draft.to_dict(),
        "markdown": markdown,
        "rules_ready": True,
        "sync_result": sync_result,
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
    store.record_event("filters", "Updated hh.ru filter plan from the current profile.", details=plan)
    return {
        "action": "plan_filters",
        "payload": plan,
        "message": f"РџР»Р°РЅ С„РёР»СЊС‚СЂРѕРІ РѕР±РЅРѕРІР»РµРЅ. РџРѕРёСЃРєРѕРІС‹Р№ С‚РµРєСЃС‚: {plan.get('search_text') or 'РЅРµ Р·Р°РґР°РЅ'}.",
    }


def select_resume_for_search(store: WorkspaceStore, *, resume_id: str) -> dict[str, Any]:
    selected_resume_id = str(resume_id or "").strip()
    store.save_selected_resume_id(selected_resume_id)
    store.touch_dashboard_timestamp("last_resume_selection_at")
    _mark_analysis_stale(store, "Р’С‹Р±СЂР°РЅРЅРѕРµ СЂРµР·СЋРјРµ hh.ru РёР·РјРµРЅРёР»РѕСЃСЊ. Р—Р°РїСѓСЃС‚РёС‚Рµ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ, С‡С‚РѕР±С‹ РїРѕР»СѓС‡РёС‚СЊ СЃРІРµР¶СѓСЋ РѕС‡РµСЂРµРґСЊ РІР°РєР°РЅСЃРёР№.")
    store.record_event(
        "hh-resume-selection",
        f"Р’С‹Р±СЂР°РЅРѕ СЂРµР·СЋРјРµ hh.ru РґР»СЏ live search: {selected_resume_id or 'СЃР±СЂРѕС€РµРЅРѕ'}.",
        details={"selected_resume_id": selected_resume_id},
    )
    return {
        "action": "select-resume",
        "selected_resume_id": selected_resume_id,
        "analysis_stale": True,
        "message": "Р’С‹Р±РѕСЂ СЂРµР·СЋРјРµ СЃРѕС…СЂР°РЅРµРЅ. Р—Р°РїСѓСЃС‚РёС‚Рµ Р°РЅР°Р»РёР· Р·Р°РЅРѕРІРѕ, С‡С‚РѕР±С‹ СЃРѕР±СЂР°С‚СЊ РІР°РєР°РЅСЃРёРё РїРѕ СЌС‚РѕРјСѓ СЂРµР·СЋРјРµ.",
    }


def select_hh_account(store: WorkspaceStore, *, account_key: str) -> dict[str, Any]:
    normalized = str(account_key or "").strip()
    if normalized == store.account_key:
        return {
            "action": "select-account",
            "account_key": normalized,
            "message": "Р­С‚РѕС‚ hh-Р°РєРєР°СѓРЅС‚ СѓР¶Рµ Р°РєС‚РёРІРµРЅ.",
        }
    accounts = {str(item.get("account_key") or ""): item for item in store.load_accounts()}
    if normalized not in accounts:
        raise RuntimeError("account_key was not found in saved hh accounts.")
    store.set_active_account(normalized)
    switched_store = WorkspaceStore(store.project_root, account_key=normalized)
    switched_store.save_account_profile({**accounts[normalized], "updated_at": utc_now_iso()})
    switched_store.record_event(
        "hh-account",
        f"Switched active hh account to {accounts[normalized].get('display_name') or normalized}.",
        details={"account_key": normalized},
    )
    return {
        "action": "select-account",
        "account_key": normalized,
        "message": f"РђРєРєР°СѓРЅС‚ hh.ru РїРµСЂРµРєР»СЋС‡РµРЅ: {accounts[normalized].get('display_name') or normalized}.",
    }


def run_plan_apply(store: WorkspaceStore, *, vacancy_id: str | None = None) -> dict[str, Any]:
    _require_intake(store)
    _require_rules(store)
    if not store.load_assessments():
        VacancyAnalysisAgent(store).analyze(limit=120)
    if vacancy_id:
        _ensure_cover_letter_draft(store, vacancy_id=str(vacancy_id), force=False)
    payload = ApplicationAgent(store).build_plan(vacancy_id=vacancy_id)
    store.touch_dashboard_timestamp("last_apply_plan_at")
    return {"action": "plan_apply", "payload": payload}


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
    store.record_event("cover-letter", "РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ РїРёСЃСЊРјРѕ РѕР±РЅРѕРІР»РµРЅРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј.", details={"vacancy_id": normalized_vacancy_id})
    _mark_analysis_stale(store, "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РѕР±РЅРѕРІРёР» СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ РїРёСЃСЊРјРѕ РґР»СЏ РѕС‚РєР»РёРєР°.")
    return {
        "action": "save_cover_letter",
        "vacancy_id": normalized_vacancy_id,
        "cover_letter_enabled": bool(str(cover_letter or "").strip()),
        "message": "РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ РїРёСЃСЊРјРѕ СЃРѕС…СЂР°РЅРµРЅРѕ.",
    }


def run_apply_submit(store: WorkspaceStore, *, vacancy_id: str, cover_letter: str = "") -> dict[str, Any]:
    if cover_letter.strip():
        store.save_cover_letter_draft(vacancy_id, cover_letter)
    elif not store.load_cover_letter_draft(vacancy_id).strip():
        _ensure_cover_letter_draft(store, vacancy_id=vacancy_id, force=False)
    result = apply_to_vacancy(store, vacancy_id=vacancy_id, cover_letter_override=cover_letter)
    _bump_apply_counter(store, 1)
    store.touch_dashboard_timestamp("last_apply_submit_at")
    store.record_event("apply", "Р—Р°РїСѓС‰РµРЅ РѕС‚РєР»РёРє РёР· РґР°С€Р±РѕСЂРґР°.", details={"vacancy_id": vacancy_id, "status": ((result.get("result") or {}).get("status") or "")})
    return {
        "action": "apply_submit",
        "payload": result,
        "message": str(((result.get("result") or {}).get("message") or "РћС‚РєР»РёРє РѕР±СЂР°Р±РѕС‚Р°РЅ.")),
    }


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
        "fit": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІСЂСѓС‡РЅСѓСЋ РїРµСЂРµРІС‘Р» РІР°РєР°РЅСЃРёСЋ РІ В«РџРѕРґС…РѕРґРёС‚В». РќСѓР¶РµРЅ Р±С‹СЃС‚СЂС‹Р№ РѕС‚РєР»РёРє РёР»Рё С…РѕС‚СЏ Р±С‹ РїСЂРѕРІРµСЂРєР° Р°РІС‚РѕСЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРіРѕ.",
        "doubt": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІСЂСѓС‡РЅСѓСЋ РїРµСЂРµРІС‘Р» РІР°РєР°РЅСЃРёСЋ РІ В«РЎРѕРјРЅРµРІР°СЋСЃСЊВ». РќСѓР¶РµРЅ РєРѕСЂРѕС‚РєРёР№ РїРѕРІС‚РѕСЂРЅС‹Р№ СЂР°Р·Р±РѕСЂ.",
        "no_fit": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІСЂСѓС‡РЅСѓСЋ РёСЃРєР»СЋС‡РёР» РІР°РєР°РЅСЃРёСЋ РёР· РїСЂРёРѕСЂРёС‚РµС‚РЅС‹С…. РћС‚РєР»РёРє РїРѕ РЅРµР№ СЃРµР№С‡Р°СЃ РЅРµ РЅСѓР¶РµРЅ.",
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
    store.record_event("vacancy-feedback", "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РёР·РјРµРЅРёР» СЃС‚Р°С‚СѓСЃ РІР°РєР°РЅСЃРёРё.", details={"vacancy_id": vacancy_key, "decision": decision_key})
    cover_letter_generated = False
    if decision_key == "fit":
        cover_letter_generated = bool(_ensure_cover_letter_draft(store, vacancy_id=vacancy_key, force=True))
    return {
        "action": "vacancy_feedback",
        "vacancy_id": vacancy_key,
        "decision": decision_key,
        "cover_letter_generated": cover_letter_generated,
        "message": f"РЎС‚Р°С‚СѓСЃ РІР°РєР°РЅСЃРёРё РѕР±РЅРѕРІР»РµРЅ: {decision_key}." + (" РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ РїРѕРґРіРѕС‚РѕРІР»РµРЅРѕ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё." if cover_letter_generated else ""),
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
        raise RuntimeError("Р”РЅРµРІРЅРѕР№ Р»РёРјРёС‚ РѕС‚РєР»РёРєРѕРІ СѓР¶Рµ РёСЃС‡РµСЂРїР°РЅ.")

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
            "message": "Р’ РІС‹Р±СЂР°РЅРЅРѕР№ РєРѕР»РѕРЅРєРµ СЃРµР№С‡Р°СЃ РЅРµС‚ РІР°РєР°РЅСЃРёР№ РґР»СЏ РѕС‚РєР»РёРєР°.",
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
        f"РџР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ {category_key}.",
        details={"category": category_key, "attempted": len(queue), "applied": applied, "failed": failed},
    )
    return {
        "action": "apply_batch",
        "category": category_key,
        "attempted": len(queue),
        "applied": applied,
        "failed": failed,
        "remaining_daily_budget": max(0, daily_limit - _today_apply_counter(store)[1]),
        "message": f"РџР°РєРµС‚РЅС‹Р№ РѕС‚РєР»РёРє РїРѕ РєРѕР»РѕРЅРєРµ {category_key}: РѕР±СЂР°Р±РѕС‚Р°РЅРѕ {len(queue)}, СѓСЃРїРµС€РЅС‹С… РёР»Рё РґРѕРІРµРґС‘РЅРЅС‹С… РґРѕ С„РёРЅР°Р»СЊРЅРѕРіРѕ С€Р°РіР° {applied}, СЃ РѕС€РёР±РєРѕР№ {failed}.",
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
        analyze_result = run_analyze(store, limit=120, interactive=False)
        apply_result = run_plan_apply(store)
        return {"action": "full_pipeline", "analyze": analyze_result, "apply_plan": apply_result}
    return run_analyze(store, limit=120, interactive=False)


from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any

from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import utc_now_iso
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.account_profiles import derive_account_profile
from autohhkek.services.hh_refresh import HHVacancyRefresher
from autohhkek.services.rules import evaluate_intake_readiness, split_rules_markdown
from autohhkek.services.runtime_settings import AVAILABLE_DASHBOARD_MODES, AVAILABLE_LLM_BACKENDS
from autohhkek.services.storage import WorkspaceStore, build_vacancy_snapshot_hash


FIT_LABELS = {
    FitCategory.FIT.value: "Подходит",
    FitCategory.DOUBT.value: "Сомневаюсь",
    FitCategory.NO_FIT.value: "Не подходит",
}

MODE_LABELS = {
    "analyze": "Анализ вакансий",
    "apply_plan": "План отклика",
    "repair": "Починка сценариев",
    "full_pipeline": "Полный пайплайн",
}

BACKEND_LABELS = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "g4f": "g4f",
}

RUN_STATUS_LABELS = {
    "completed": "Завершён",
    "failed": "С ошибкой",
    "running": "Выполняется",
    "idle": "Ожидает запуска",
}

REPAIR_STATUS_LABELS = {
    "prepared": "Подготовлено",
    "running": "В работе",
    "ready": "Готово",
    "completed": "Завершено",
    "failed": "Ошибка",
    "error": "Ошибка",
    "unavailable": "Недоступно",
}

READY_STATE_META = {
    "needs_mode": {
        "label": "Нужно выбрать режим",
        "detail": "Сначала зафиксируйте режим работы, чтобы дашборд понимал, какой запуск считать основным.",
    },
    "needs_intake": {
        "label": "Нужно собрать анамнез",
        "detail": "Перед первым запуском нужно заполнить анкету кандидата и сохранить базовые вводные.",
    },
    "needs_rules": {
        "label": "Нужно собрать правила",
        "detail": "После анкеты нужен базовый набор правил поиска и отбора вакансий.",
    },
    "repair_attention": {
        "label": "Нужно разобрать repair queue",
        "detail": "Есть незавершённые DOM-поломки. Лучше разобрать их до следующего массового запуска.",
    },
    "ready_to_run": {
        "label": "Можно запускать режим",
        "detail": "Onboarding завершён. Можно стартовать выбранный режим и собрать свежую очередь вакансий.",
    },
    "needs_apply_plan": {
        "label": "Нужен план отклика",
        "detail": "Подходящие вакансии уже найдены. Следующий шаг — подготовить apply plan.",
    },
    "ready": {
        "label": "Рабочая зона готова",
        "detail": "Данные собраны, дашборд показывает очередь и ждёт следующего действия.",
    },
}

FRESHNESS_WINDOW_DAYS = 21
AUTO_BOOTSTRAP_COOLDOWN_HOURS = 6


def _file_timestamp(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _is_stale_timestamp(value: str, *, days: int = FRESHNESS_WINDOW_DAYS) -> bool:
    if not value:
        return True
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return timestamp < (datetime.now(timezone.utc) - timedelta(days=days))


def _is_recent_timestamp(value: str, *, hours: int) -> bool:
    if not value:
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp >= (datetime.now(timezone.utc) - timedelta(hours=hours))


def _build_freshness(store: WorkspaceStore, project_root: Path, analysis_state: dict[str, Any]) -> dict[str, Any]:
    dashboard_state = store.load_dashboard_state()
    timestamps = {
        "seen_at": str(dashboard_state.get("last_seen_at") or ""),
        "hh_login_at": str(dashboard_state.get("last_hh_login_at") or _file_timestamp(store.hh_state_path)),
        "hh_login_attempt_at": str(dashboard_state.get("last_hh_login_attempt_at") or ""),
        "resume_catalog_at": str(dashboard_state.get("last_resume_catalog_at") or _file_timestamp(store.paths.hh_resumes_path)),
        "resume_selection_at": str(dashboard_state.get("last_resume_selection_at") or ""),
        "resume_sync_at": str(dashboard_state.get("last_resume_sync_at") or ""),
        "intake_at": str(dashboard_state.get("last_intake_at") or max(_file_timestamp(store.paths.preferences_path), _file_timestamp(store.paths.anamnesis_path))),
        "rules_at": str(dashboard_state.get("last_rules_rebuilt_at") or _file_timestamp(store.paths.rules_markdown_path)),
        "resume_draft_at": str(dashboard_state.get("last_resume_draft_at") or _file_timestamp(store.paths.resume_draft_path)),
        "vacancies_at": _file_timestamp(store.paths.vacancies_path),
        "analysis_at": str(dashboard_state.get("last_analysis_at") or analysis_state.get("assessed_at") or analysis_state.get("analyzed_at") or _file_timestamp(store.paths.analysis_state_path)),
        "apply_plan_at": str(dashboard_state.get("last_apply_plan_at") or _file_timestamp(store.paths.apply_plan_path)),
        "apply_submit_at": str(dashboard_state.get("last_apply_submit_at") or ""),
        "auto_bootstrap_at": str(dashboard_state.get("last_auto_bootstrap_at") or ""),
    }
    stale = {key: _is_stale_timestamp(value) for key, value in timestamps.items()}
    resume_selection_missing = not bool(store.load_selected_resume_id())
    resume_catalog_count = int(dashboard_state.get("last_resume_catalog_count") or len(store.load_hh_resumes()) or 0)
    auto_bootstrap_recent = _is_recent_timestamp(timestamps["auto_bootstrap_at"], hours=AUTO_BOOTSTRAP_COOLDOWN_HOURS)
    return {
        "timestamps": timestamps,
        "stale": stale,
        "is_new_user": stale["intake_at"] or stale["rules_at"] or stale["hh_login_at"],
        "needs_login_refresh": stale["hh_login_at"],
        "needs_rules_refresh": stale["rules_at"],
        "needs_resume_refresh": stale["resume_draft_at"] or stale["resume_catalog_at"] or resume_selection_missing,
        "resume_selection_missing": resume_selection_missing,
        "resume_catalog_count": resume_catalog_count,
        "auto_bootstrap_recent": auto_bootstrap_recent,
        "dashboard_state": dashboard_state,
    }


def _mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode.replace("_", " "))


def _backend_label(name: str) -> str:
    return BACKEND_LABELS.get(name, name or "Не выбран")


def _run_status_label(status: str) -> str:
    return RUN_STATUS_LABELS.get(status, status or "Неизвестно")


def _repair_status_label(status: str) -> str:
    return REPAIR_STATUS_LABELS.get(status, status or "Неизвестно")


def _latin_dominant(text: str) -> bool:
    value = str(text or "")
    latin = sum(1 for char in value if "a" <= char.lower() <= "z")
    cyrillic = sum(1 for char in value if "а" <= char.lower() <= "я")
    return latin > cyrillic and latin >= 8


def _localized_reason_text(reason) -> tuple[str, str]:
    code = str(getattr(reason, "subcategory", "") or getattr(reason, "code", "") or "").lower()
    fallback = {
        "role_fit": ("Совпадение по роли", "Название вакансии пересекается с целевыми ролями пользователя."),
        "title_gap": ("Слабое совпадение по роли", "По названию вакансии совпадение с целевыми ролями неочевидно."),
        "must_have_hit": ("Совпадают обязательные навыки", "В описании нашлись ключевые обязательные навыки."),
        "skill_gap": ("Есть пробелы по must-have", "Не все обязательные навыки подтверждаются текстом вакансии."),
        "skill_overlap": ("Есть пересечение по стеку", "Вакансия частично совпадает по стеку и тематике."),
        "remote_fit": ("Подходит по формату", "Формат работы выглядит совместимым с ожиданием remote."),
        "format_mismatch": ("Нет удалённого формата", "Для пользователя удалёнка обязательна, а вакансия это не подтверждает."),
        "location_fit": ("Локация подходит", "География вакансии выглядит совместимой с предпочтениями."),
        "location_mismatch": ("Есть вопрос по локации", "Локация не совпадает с ожиданиями и требует ручной проверки."),
        "missing_salary": ("Зарплата не указана", "Без зарплатного диапазона вакансию нужно проверять вручную."),
        "salary_fit": ("Зарплата в диапазоне", "Видимая зарплата не ниже заданного порога."),
        "salary_low": ("Зарплата ниже порога", "Указанная зарплата ниже минимального ожидания пользователя."),
        "screening_or_test_required": ("Есть анкета или тест", "Отклик может потребовать отдельную анкету или тестовый этап."),
        "cover_letter_requested": ("Может понадобиться сопроводительное", "В тексте вакансии есть признаки запроса на сопроводительное письмо."),
        "blacklisted_employer": ("Жёсткое исключение", "В вакансии найден запрещённый для пользователя маркер."),
        "user_fit": ("Ручное решение пользователя", "Пользователь сам перевёл вакансию в подходящие."),
        "user_doubt": ("Ручное решение пользователя", "Пользователь отправил вакансию в сомнительные."),
        "user_no_fit": ("Ручное решение пользователя", "Пользователь исключил вакансию из приоритетных."),
    }.get(code)
    label = str(getattr(reason, "label", "") or "")
    detail = str(getattr(reason, "detail", "") or "")
    if fallback:
        base_label, base_detail = fallback
        return base_label, detail if detail and not _latin_dominant(detail) else base_detail
    if _latin_dominant(label):
        label = "Фактор оценки"
    if _latin_dominant(detail):
        detail = "Эта причина пришла из старой англоязычной оценки; её лучше перепроверить свежим анализом."
    return label or "Фактор оценки", detail or "Подробности причины не сохранены."


def _vacancy_decision_explanation(assessment) -> str:
    review_notes = str(assessment.review_notes or "").strip()
    explanation = str(assessment.explanation or "").strip()
    if review_notes and "legacy resume cache" not in review_notes.lower() and not _latin_dominant(review_notes):
        return review_notes
    if explanation and "score " not in explanation.lower() and not _latin_dominant(explanation):
        return explanation
    top_reasons = sorted(list(assessment.reasons or []), key=lambda item: abs(item.weight), reverse=True)[:3]
    if top_reasons:
        summary = "; ".join(f"{_localized_reason_text(reason)[0]}: {_localized_reason_text(reason)[1]}" for reason in top_reasons)
        if summary:
            return summary
    return explanation or review_notes or "Причины решения пока не сохранены."


def _vacancy_card(vacancy, assessment) -> dict[str, Any]:
    reasons = []
    for reason in assessment.reasons:
        item = reason.to_dict()
        item["label"], item["detail"] = _localized_reason_text(reason)
        reasons.append(item)
    category = assessment.category.value
    decision_reason = _vacancy_decision_explanation(assessment)
    return {
        "id": vacancy.vacancy_id,
        "title": vacancy.title,
        "company": vacancy.company,
        "location": vacancy.location,
        "url": vacancy.url,
        "score": assessment.score,
        "category": category,
        "category_label": FIT_LABELS.get(category, category),
        "subcategory": assessment.subcategory,
        "reason_summary": decision_reason,
        "explanation": decision_reason,
        "raw_explanation": assessment.explanation,
        "recommended_action": assessment.recommended_action,
        "ready_for_apply": assessment.ready_for_apply,
        "review_strategy": assessment.review_strategy,
        "review_notes": assessment.review_notes,
        "reasons": reasons,
        "summary": vacancy.summary,
        "description": vacancy.description[:1500],
        "skills": vacancy.skills,
    }


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_age_label(timestamp: datetime | None) -> str:
    if not timestamp:
        return "нет данных"
    delta = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
    if delta < timedelta(minutes=1):
        return "только что"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} мин назад"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} ч назад"
    return f"{delta.days} дн назад"


def _freshness_item(*, label: str, timestamp: datetime | None, max_age_days: int) -> dict[str, Any]:
    if not timestamp:
        return {"label": label, "state": "missing", "updated_at": "", "age_label": "нет данных"}
    is_stale = timestamp < datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return {
        "label": label,
        "state": "stale" if is_stale else "fresh",
        "updated_at": timestamp.isoformat(),
        "age_label": _format_age_label(timestamp),
    }


def _normalize_repair_task(task: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "prepared")
    backend = str(task.get("selected_llm_backend") or task.get("llm_backend") or "")
    repair_mode = str(task.get("repair_mode") or "")
    return {
        **task,
        "status": status,
        "status_label": _repair_status_label(status),
        "action_label": str(task.get("action") or "unknown_action").replace("_", " "),
        "backend_label": _backend_label(backend),
        "backend_key": backend,
        "error_label": str(task.get("error") or "Причина не сохранена"),
        "repair_mode_label": "Plan only" if repair_mode == "plan_only" else ("Live MCP" if repair_mode else "Не указан"),
        "has_artifacts": bool(task.get("repair_patch_path") or task.get("repair_test_path")),
    }


def _build_last_run_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {
            "present": False,
            "label": "Запусков пока не было",
            "detail": "После первого запуска здесь появится краткая история пайплайна.",
            "status": "idle",
            "status_label": _run_status_label("idle"),
            "summary": "No runs recorded yet.",
        }

    latest = runs[0]
    mode = str(latest.get("mode") or "unknown")
    status = str(latest.get("status") or "unknown")
    processed = int(latest.get("processed") or 0)
    counts = latest.get("counts") or {}
    detail = f"{_mode_label(mode)} • {_run_status_label(status)} • обработано {processed}"
    if counts:
        detail = (
            f"{detail} • подходит {counts.get('fit', 0)}, "
            f"сомневаюсь {counts.get('doubt', 0)}, не подходит {counts.get('no_fit', 0)}"
        )
    return {
        "present": True,
        "run_id": latest.get("run_id", ""),
        "mode": mode,
        "mode_label": _mode_label(mode),
        "status": status,
        "status_label": _run_status_label(status),
        "started_at": latest.get("started_at", ""),
        "finished_at": latest.get("finished_at", ""),
        "processed": processed,
        "counts": counts,
        "notes": latest.get("notes") or [],
        "label": latest.get("run_id") or "Последний запуск",
        "detail": detail,
        "summary": detail,
    }


def _build_setup_summary(
    *,
    runtime_settings,
    preferences,
    anamnesis,
    dashboard_state: dict[str, Any],
    imported_rules: list[dict[str, str]],
    rules_markdown: str,
    filter_plan: dict[str, Any],
    apply_plan: dict[str, Any],
    resume_markdown: str,
    pending_repair_count: int,
    live_refresh_ready: bool,
    live_refresh_message: str,
    hh_resumes: list[dict[str, str]],
    selected_resume_id: str,
    live_refresh_stats: dict[str, Any],
) -> dict[str, Any]:
    rules_loaded = bool(rules_markdown.strip() or imported_rules)
    intake_state = evaluate_intake_readiness(preferences, anamnesis, dashboard_state)
    return {
        "mode_selected": bool(runtime_settings.mode_selected),
        "intake_ready": bool(intake_state["ready"]),
        "intake_structured_ready": bool(intake_state["structured_ready"]),
        "intake_dialog_completed": bool(intake_state["dialog_completed"]),
        "intake_confirmed": bool(intake_state.get("confirmed")),
        "intake_missing": list(intake_state["missing"]),
        "rules_loaded": rules_loaded,
        "imported_rules_count": len(imported_rules),
        "filter_plan_ready": bool(filter_plan),
        "apply_plan_ready": bool(apply_plan),
        "resume_draft_ready": bool(resume_markdown.strip()),
        "ready_to_run": bool(runtime_settings.mode_selected and intake_state["ready"] and rules_loaded),
        "cover_letter_mode": getattr(preferences, "cover_letter_mode", "adaptive") if preferences else "adaptive",
        "target_titles": list(getattr(preferences, "target_titles", []) or []),
        "preferred_locations": list(getattr(preferences, "preferred_locations", []) or []),
        "salary_min": getattr(preferences, "salary_min", None) if preferences else None,
        "remote_only": bool(getattr(preferences, "remote_only", False)) if preferences else False,
        "search_text": str(filter_plan.get("search_text") or ""),
        "planner_backend": str(filter_plan.get("planner_backend") or "rules"),
        "repair_queue_count": pending_repair_count,
        "live_refresh_ready": live_refresh_ready,
        "live_refresh_message": live_refresh_message,
        "selected_resume_id": selected_resume_id,
        "resume_selection_required": len(hh_resumes) > 1 and not selected_resume_id,
        "live_refresh_stats": live_refresh_stats,
    }


def _build_operator_summary(
    *,
    runtime_settings,
    runtime_capabilities: dict[str, Any],
    counts: dict[str, int],
    setup_summary: dict[str, Any],
    filter_plan: dict[str, Any],
    apply_plan: dict[str, Any],
    repair_tasks: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    analysis_state: dict[str, Any],
) -> dict[str, Any]:
    pending_repairs = [task for task in repair_tasks if task["status"] not in {"completed"}]
    blocking_issues: list[str] = []
    attention_items: list[str] = []

    selected_backend = runtime_settings.llm_backend
    backend_capabilities = runtime_capabilities.get("llm_backends", {}).get(selected_backend, {})
    selected_backend_ready = bool(runtime_capabilities.get("selected_llm_backend_ready"))
    effective_backend = str(runtime_capabilities.get("effective_backend") or runtime_capabilities.get("selected_llm_backend") or "")
    last_analysis_backend = str(analysis_state.get("effective_backend") or "")
    llm_reviewed_count = int(analysis_state.get("llm_reviewed_count") or 0)
    stale = bool(analysis_state.get("stale"))
    backend_proved = bool(last_analysis_backend and last_analysis_backend == effective_backend and llm_reviewed_count > 0)

    if not runtime_settings.mode_selected:
        blocking_issues.append("Mode is not selected yet.")
    elif not setup_summary["intake_ready"]:
        blocking_issues.append("Intake is not collected yet.")
    elif not setup_summary["rules_loaded"]:
        blocking_issues.append("Search rules are missing.")

    if pending_repairs:
        blocking_issues.append(f"Repair queue contains {len(pending_repairs)} pending DOM action(s).")
    if not filter_plan:
        attention_items.append("План фильтров hh.ru ещё не сохранён. Анализ пойдёт по базовым правилам.")
    if counts.get("fit", 0) > 0 and not apply_plan:
        attention_items.append("Есть подходящие вакансии, но apply plan ещё не построен.")
    if selected_backend == "openai" and not runtime_capabilities.get("openai_ready"):
        attention_items.append("OpenAI API не настроен. Где возможно, будет использован fallback.")
    if selected_backend == "openrouter" and not runtime_capabilities.get("openrouter_ready"):
        attention_items.append("OpenRouter API не настроен. Где возможно, будет использован fallback.")
    if selected_backend == "g4f" and not backend_capabilities.get("ready"):
        attention_items.append("g4f сейчас недоступен или выбран неподходящий provider/model.")
    if pending_repairs and selected_backend in {"openai", "openrouter"} and not runtime_capabilities.get("playwright_mcp_ready"):
        attention_items.append("Playwright MCP не настроен, поэтому live repair worker пока недоступен.")
    if counts.get("total_vacancies", 0) <= 0:
        attention_items.append("Кэш вакансий пуст. После анализа очередь появится автоматически.")
    if not selected_backend_ready:
        attention_items.append("Выбранный LLM backend не готов. Агент может откатиться к эвристикам.")

    if stale:
        attention_items.append(str(analysis_state.get("stale_reason") or "Current vacancy assessments are stale and need a fresh analysis run."))
    if not backend_proved:
        attention_items.append("No confirmed LLM analysis exists yet for the current backend. The board may still show old deterministic results.")

    if not runtime_settings.mode_selected:
        ready_state = "needs_mode"
    elif not setup_summary["intake_ready"]:
        ready_state = "needs_intake"
    elif not setup_summary["rules_loaded"]:
        ready_state = "needs_rules"
    elif pending_repairs:
        ready_state = "repair_attention"
    elif counts.get("assessed", 0) <= 0:
        ready_state = "ready_to_run"
    elif counts.get("fit", 0) > 0 and not apply_plan:
        ready_state = "needs_apply_plan"
    else:
        ready_state = "ready"

    if ready_state == "needs_mode":
        next_action = {
            "id": "choose_mode",
            "label": "Выбрать режим",
            "reason": "Сначала зафиксируйте режим работы: анализ, отклик, repair или полный pipeline.",
        }
    elif ready_state == "needs_intake":
        next_action = {
            "id": "intake",
            "label": "Заполнить анкету",
            "reason": "Перед первым запуском нужно собрать анамнез кандидата и сохранить базовые вводные.",
        }
    elif ready_state == "needs_rules":
        next_action = {
            "id": "build_rules",
            "label": "Собрать правила",
            "reason": "На базе анкеты нужно сгенерировать правила отбора вакансий и фильтры поиска.",
        }
    elif ready_state == "repair_attention":
        next_action = {
            "id": "repair_queue",
            "label": "Открыть repair queue",
            "reason": "Есть незавершённые DOM-поломки. Их лучше разобрать до следующего массового запуска.",
        }
    elif ready_state == "ready_to_run":
        next_action = {
            "id": "run-selected",
            "label": "Запустить выбранный режим",
            "reason": f"Режим «{_mode_label(runtime_settings.dashboard_mode)}» готов к первому прогону.",
        }
    elif ready_state == "needs_apply_plan":
        next_action = {
            "id": "apply_plan",
            "label": "Собрать план отклика",
            "reason": "Подходящие вакансии уже найдены. Следующий шаг — подготовить apply plan.",
        }
    else:
        next_action = {
            "id": "vacancies",
            "label": "Открыть вакансии",
            "reason": "Очередь вакансий уже готова. Можно перейти к детальному разбору.",
        }

    ready_meta = READY_STATE_META[ready_state]
    last_run_summary = _build_last_run_summary(runs)
    return {
        "mode_key": runtime_settings.dashboard_mode,
        "mode_label": _mode_label(runtime_settings.dashboard_mode),
        "backend_key": selected_backend,
        "backend_label": _backend_label(selected_backend),
        "ready_state": ready_state,
        "ready_label": ready_meta["label"],
        "ready_detail": ready_meta["detail"],
        "blocking_issues": blocking_issues,
        "attention_items": attention_items,
        "next_recommended_action": next_action,
        "last_run_summary": last_run_summary,
        "repair_queue_count": len(pending_repairs),
        "backend_proved": backend_proved,
    }


def build_dashboard_snapshot(project_root: Path, limit: int = 120) -> dict[str, Any]:
    store = WorkspaceStore(project_root.resolve())
    runtime = HHAutomationRuntime(project_root=project_root.resolve())

    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    vacancies = {item.vacancy_id: item for item in store.load_vacancies()}
    all_assessments = sorted(store.load_assessments(), key=lambda item: item.score, reverse=True)
    display_assessments = all_assessments[:limit]
    runs = [item.to_dict() for item in store.list_runs(limit=8)]
    events = store.load_events(limit=20)
    rules_markdown = store.load_selection_rules()
    imported_rules = store.load_imported_rules()
    filter_plan = store.load_filter_plan() or {}
    resume_markdown = store.load_resume_draft_markdown()
    apply_plan = store.load_apply_plan() or {}
    analysis_state = store.load_analysis_state() or {}
    runtime_settings = store.load_runtime_settings()
    runtime_capabilities = runtime.describe_capabilities()
    hh_resumes = store.load_hh_resumes()
    hh_accounts = store.load_accounts()
    active_account = store.load_active_account()
    selected_resume_id = store.load_selected_resume_id()
    cover_letter_drafts = store.load_cover_letter_drafts()
    vacancy_feedback = store.load_vacancy_feedback()
    refresher = HHVacancyRefresher(store)
    state_file_exists = refresher.state_path.exists()
    live_refresh_ready = bool(refresher.resume_id and state_file_exists)
    if not state_file_exists:
        live_refresh_message = "Нужен вход в hh.ru, чтобы искать новые вакансии."
    elif not hh_resumes:
        live_refresh_message = "После входа нужно подтянуть резюме с hh.ru."
    elif not selected_resume_id:
        live_refresh_message = "Выберите резюме, по которому нужно искать вакансии."
    else:
        live_refresh_message = "Поиск новых вакансий по hh.ru готов."
    if hh_resumes and len(hh_resumes) > 1 and not selected_resume_id:
        live_refresh_message = "На hh.ru найдено несколько резюме. Выберите одно резюме для поиска."
    elif hh_resumes and len(hh_resumes) == 1 and selected_resume_id:
        selected_title = next((item.get("title", "") for item in hh_resumes if item.get("resume_id") == selected_resume_id), "")
        live_refresh_message = f"Для live refresh выбрано резюме: {selected_title or selected_resume_id}."
    dashboard_state = store.load_dashboard_state()
    refresh_result = dict(analysis_state.get("refresh_result") or {})
    live_refresh_stats = {
        "total_available": int(dashboard_state.get("last_live_refresh_total_available") or refresh_result.get("total_available") or len(vacancies)),
        "count": int(dashboard_state.get("last_live_refresh_count") or refresh_result.get("count") or len(vacancies)),
        "new_count": int(dashboard_state.get("last_live_refresh_new_count") or refresh_result.get("new_count") or 0),
        "pages_parsed": int(dashboard_state.get("last_live_refresh_pages_parsed") or refresh_result.get("pages_parsed") or 0),
        "search_url": str(dashboard_state.get("last_live_refresh_search_url") or refresh_result.get("search_url") or filter_plan.get("search_url") or ""),
    }
    repair_tasks = [_normalize_repair_task(item) for item in store.load_repair_tasks(limit=12)]
    pending_repairs = [task for task in repair_tasks if task["status"] not in {"completed"}]
    current_vacancies = list(vacancies.values())
    current_vacancy_hash = build_vacancy_snapshot_hash(current_vacancies)
    saved_vacancy_hash = str(analysis_state.get("vacancy_snapshot_hash") or "")
    current_rules_hash = hashlib.sha1(rules_markdown.encode("utf-8")).hexdigest() if rules_markdown else ""
    saved_rules_hash = str(analysis_state.get("rules_hash") or "")
    analysis_state["current_vacancy_snapshot_hash"] = current_vacancy_hash
    analysis_state["current_rules_hash"] = current_rules_hash
    analysis_state["stale"] = bool(analysis_state.get("stale")) or bool(
        (saved_vacancy_hash and saved_vacancy_hash != current_vacancy_hash)
        or (saved_rules_hash and current_rules_hash and saved_rules_hash != current_rules_hash)
    )

    columns = {
        FitCategory.NO_FIT.value: [],
        FitCategory.DOUBT.value: [],
        FitCategory.FIT.value: [],
    }
    reason_counter: Counter[str] = Counter()
    for assessment in display_assessments:
        vacancy = vacancies.get(assessment.vacancy_id)
        if not vacancy:
            continue
        card = _vacancy_card(vacancy, assessment)
        card["user_feedback"] = dict(vacancy_feedback.get(card["id"], {}) or {})
        card["cover_letter_draft"] = str(cover_letter_drafts.get(card["id"], "") or "")
        columns[assessment.category.value].append(card)
        reason_counter.update(reason["subcategory"] or reason["code"] for reason in card["reasons"])

    counts = {
        "total_vacancies": len(vacancies),
        "assessed": len(all_assessments),
        "fit": len([item for item in all_assessments if item.category == FitCategory.FIT]),
        "doubt": len([item for item in all_assessments if item.category == FitCategory.DOUBT]),
        "no_fit": len([item for item in all_assessments if item.category == FitCategory.NO_FIT]),
        "imported_rules": len(imported_rules),
    }
    if saved_rules_hash and current_rules_hash and saved_rules_hash != current_rules_hash:
        analysis_state["stale_reason"] = "Profile or selection rules changed after the last analysis. Run Analyze again to refresh classifications."
    elif analysis_state["stale"]:
        analysis_state["stale_reason"] = "Vacancy cache changed after the last analysis. Run Analyze again to refresh classifications."
    elif counts["assessed"] > 0 and int(analysis_state.get("llm_reviewed_count") or 0) <= 0:
        analysis_state["stale_reason"] = "Current vacancy cards were not confirmed by an LLM yet. Run Analyze with a ready backend."
    else:
        analysis_state["stale_reason"] = str(analysis_state.get("stale_reason") or "")

    rules_parts = split_rules_markdown(rules_markdown)
    apply_bucket = str(dashboard_state.get("apply_daily_bucket") or "")
    apply_count = int(dashboard_state.get("apply_daily_count") or 0)
    if apply_bucket != utc_now_iso()[:10]:
        apply_count = 0
    intake_summary = {
        "ready": bool(preferences and anamnesis),
        "preferences": preferences.to_dict() if preferences else {},
        "anamnesis": anamnesis.to_dict() if anamnesis else {},
        "rules_preview": rules_markdown[:5000],
        "system_rules_preview": rules_parts["system"][:2000],
        "user_rules_preview": rules_parts["user"][:3000],
        "user_rules_contract": dict(dashboard_state.get("intake_user_rules_contract") or {}),
        "imported_rules": imported_rules,
    }
    freshness = _build_freshness(store, project_root, analysis_state)

    repair_status_counter: Counter[str] = Counter(task["status"] for task in repair_tasks)
    setup_summary = _build_setup_summary(
        runtime_settings=runtime_settings,
        preferences=preferences,
        anamnesis=anamnesis,
        dashboard_state=dashboard_state,
        imported_rules=imported_rules,
        rules_markdown=rules_markdown,
        filter_plan=filter_plan,
        apply_plan=apply_plan,
        resume_markdown=resume_markdown,
        pending_repair_count=len(pending_repairs),
        live_refresh_ready=live_refresh_ready,
        live_refresh_message=live_refresh_message,
        hh_resumes=hh_resumes,
        selected_resume_id=selected_resume_id,
        live_refresh_stats=live_refresh_stats,
    )
    operator_summary = _build_operator_summary(
        runtime_settings=runtime_settings,
        runtime_capabilities=runtime_capabilities,
        counts=counts,
        setup_summary=setup_summary,
        filter_plan=filter_plan,
        apply_plan=apply_plan,
        repair_tasks=repair_tasks,
        runs=runs,
        analysis_state=analysis_state,
    )

    ready_meta = READY_STATE_META[operator_summary["ready_state"]]
    setup_summary.update(
        {
            "headline": ready_meta["label"],
            "state": "ready" if setup_summary["ready_to_run"] and operator_summary["repair_queue_count"] == 0 else "needs_attention",
            "selected_backend_label": operator_summary["backend_label"],
            "selected_mode_label": operator_summary["mode_label"],
            "details": [
                f"Mode selected: {'yes' if setup_summary['mode_selected'] else 'no'}",
                f"Mode: {operator_summary['mode_label']}",
                f"Backend: {operator_summary['backend_label']}",
                f"Vacancies cached: {counts['total_vacancies']}",
                f"Assessed vacancies: {counts['assessed']}",
                f"Repair queue: {operator_summary['repair_queue_count']}",
                f"Backend proved: {'yes' if operator_summary['backend_proved'] else 'no'}",
                f"Search rules: {'ready' if setup_summary['rules_loaded'] else 'missing'}",
                f"Rules synced at: {analysis_state.get('rules_rebuilt_at') or 'not analyzed yet'}",
                f"Vacancy source: {(analysis_state.get('refresh_result') or {}).get('message') or live_refresh_message}",
                f"Live hh refresh: {'ready' if setup_summary['live_refresh_ready'] else 'not ready'}",
                f"Selected resume: {selected_resume_id or 'not selected'}",
                f"Resume draft: {'ready' if setup_summary['resume_draft_ready'] else 'missing'}",
                f"Last run: {operator_summary['last_run_summary'].get('detail', 'No runs recorded yet.')}",
            ],
            "timestamps": freshness["timestamps"],
            "stale_timestamps": freshness["stale"],
        }
    )

    capability_summary = {
        "selected_backend": operator_summary["backend_key"],
        "selected_backend_label": operator_summary["backend_label"],
        "selected_backend_ready": bool(runtime_capabilities.get("selected_llm_backend_ready")),
        "openai_ready": bool(runtime_capabilities.get("openai_ready")),
        "openrouter_ready": bool(runtime_capabilities.get("openrouter_ready")),
        "g4f_ready": bool(runtime_capabilities.get("llm_backends", {}).get("g4f", {}).get("ready")),
        "playwright_mcp_ready": bool(runtime_capabilities.get("playwright_mcp_ready")),
    }
    last_run_summary = operator_summary["last_run_summary"]
    selected_resume_title = next((item.get("title", "") for item in hh_resumes if item.get("resume_id") == selected_resume_id), "")
    if hh_resumes and not hh_accounts:
        synthesized = derive_account_profile(resumes=hh_resumes)
        synthesized["account_key"] = store.account_key
        synthesized["display_name"] = synthesized.get("display_name") or selected_resume_id or store.account_key
        hh_accounts = [synthesized]
    elif hh_resumes:
        hh_accounts = [
            (
                {
                    **account,
                    **derive_account_profile(resumes=hh_resumes),
                    "account_key": account.get("account_key") or store.account_key,
                    "display_name": account.get("display_name") or selected_resume_title or selected_resume_id or store.account_key,
                }
                if str(account.get("account_key") or "") == store.account_key and not int(account.get("resume_count") or 0)
                else account
            )
            for account in hh_accounts
        ]
    if not active_account:
        current_account = next((item for item in hh_accounts if str(item.get("account_key") or "") == store.account_key), None)
        active_account = current_account or {
            "account_key": store.account_key,
            "display_name": selected_resume_id or store.account_key,
        }
    elif hh_resumes and str(active_account.get("account_key") or "") == store.account_key and not int(active_account.get("resume_count") or 0):
        active_account = {
            **active_account,
            **derive_account_profile(resumes=hh_resumes),
            "account_key": active_account.get("account_key") or store.account_key,
            "display_name": active_account.get("display_name") or selected_resume_title or selected_resume_id or store.account_key,
        }
    auto_setup = {
        "should_run_now": bool(
            freshness["is_new_user"]
            and not freshness["auto_bootstrap_recent"]
            and (freshness["needs_login_refresh"] or freshness["needs_resume_refresh"] or freshness["needs_rules_refresh"])
        ),
        "is_new_user": bool(freshness["is_new_user"]),
        "last_auto_bootstrap_at": str(freshness["timestamps"].get("auto_bootstrap_at") or ""),
    }
    dashboard_state = freshness.get("dashboard_state") or {}
    profile_sync = {
        "status": str(dashboard_state.get("last_resume_sync_status") or "idle"),
        "message": str(dashboard_state.get("last_resume_sync_message") or ""),
        "updated_at": str(dashboard_state.get("last_resume_sync_at") or ""),
        "resume_id": str(dashboard_state.get("last_resume_sync_resume_id") or ""),
        "resume_title": str(dashboard_state.get("last_resume_sync_title") or ""),
        "change_count": int(dashboard_state.get("last_resume_sync_change_count") or 0),
    }
    intake_dialog = dict(dashboard_state.get("intake_dialog") or {})
    llm_gate = dict(dashboard_state.get("llm_gate") or {})
    apply_batch_job = {
        "running": bool(dashboard_state.get("apply_batch_running")),
        "category": str(dashboard_state.get("apply_batch_category") or ""),
        "message": str(dashboard_state.get("apply_batch_message") or ""),
        "started_at": str(dashboard_state.get("apply_batch_started_at") or ""),
        "finished_at": str(dashboard_state.get("apply_batch_finished_at") or ""),
    }

    return {
        "generated_at": utc_now_iso(),
        "status": "ok",
        "workspace": {
            "project_root": str(project_root.resolve()),
            "runtime_root": str(store.paths.runtime_root),
            "account_key": store.account_key,
        },
        "intake": intake_summary,
        "counts": counts,
        "columns": columns,
        "reason_breakdown": [{"label": key or "manual_review", "count": value} for key, value in reason_counter.most_common(12)],
        "recent_runs": runs,
        "recent_events": events,
        "runtime_capabilities": runtime_capabilities,
        "runtime_status": runtime.backend_status(),
        "runtime_settings": runtime_settings.to_dict(),
        "analysis_state": analysis_state,
        "freshness": freshness,
        "auto_setup": auto_setup,
        "profile_sync": profile_sync,
        "intake_dialog": intake_dialog,
        "llm_gate": llm_gate,
        "apply_batch_job": apply_batch_job,
        "action_catalog": {
            "backends": AVAILABLE_LLM_BACKENDS,
            "modes": AVAILABLE_DASHBOARD_MODES,
            "actions": [
                "intake",
                "confirm-intake",
                "llm-fallback-heuristics",
                "llm-wait",
                "build-rules",
                "import-rules",
                "resume",
                "plan-filters",
                "run-selected",
                "analyze",
                "apply-plan",
                "plan-repair",
                "run-repair",
                "hh-login",
                "hh-resumes",
                "select-account",
                "select-resume",
                "save-cover-letter",
                "apply-batch",
                "apply-submit",
                "vacancy-feedback",
            ],
        },
        "hh_resumes": hh_resumes,
        "hh_accounts": hh_accounts,
        "active_account": active_account,
        "selected_resume_id": selected_resume_id,
        "selected_resume_title": selected_resume_title,
        "cover_letter_drafts": cover_letter_drafts,
        "vacancy_feedback": vacancy_feedback,
        "apply_limits": {
            "daily_limit": 200,
            "used_today": apply_count,
            "remaining_today": max(0, 200 - apply_count),
        },
        "repair_tasks": repair_tasks,
        "repair_summary": {
            "total": len(repair_tasks),
            "pending": operator_summary["repair_queue_count"],
            "prepared": repair_status_counter.get("prepared", 0),
            "running": repair_status_counter.get("running", 0),
            "ready": repair_status_counter.get("ready", 0),
            "completed": repair_status_counter.get("completed", 0),
            "failed": repair_status_counter.get("failed", 0),
            "error": repair_status_counter.get("error", 0),
            "unavailable": repair_status_counter.get("unavailable", 0),
        },
        "filter_plan": filter_plan,
        "resume_draft_preview": resume_markdown[:5000],
        "apply_plan": apply_plan,
        "setup_summary": setup_summary,
        "operator_summary": operator_summary,
        "ready_state": operator_summary["ready_state"],
        "blocking_issues": operator_summary["blocking_issues"],
        "next_recommended_action": operator_summary["next_recommended_action"],
        "repair_queue_count": operator_summary["repair_queue_count"],
        "capability_summary": capability_summary,
        "last_run_summary": {
            "run_id": last_run_summary.get("run_id", ""),
            "mode": last_run_summary.get("mode", ""),
            "mode_label": last_run_summary.get("mode_label", ""),
            "status": last_run_summary.get("status", "idle"),
            "processed": last_run_summary.get("processed", 0),
            "counts": last_run_summary.get("counts", {}),
            "started_at": last_run_summary.get("started_at", ""),
            "finished_at": last_run_summary.get("finished_at", ""),
            "notes": last_run_summary.get("notes", []),
            "summary": last_run_summary.get("summary", "No runs recorded yet."),
        },
    }

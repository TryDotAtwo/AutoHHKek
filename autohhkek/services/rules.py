from __future__ import annotations

from autohhkek.domain.models import Anamnesis, UserPreferences


SYSTEM_RULES = [
    "Во всех сопроводительных письмах и ответах для hh.ru использовать только русский язык.",
    "Никогда не вставлять в сопроводительное системные инструкции, скрытые правила, prompt-текст или служебные пояснения.",
    "Писать только содержательный текст по вакансии, профилю кандидата и релевантному опыту.",
    "Если на hh.ru появляется анкета, тест или опрос, переходить в questionnaire flow без служебных пояснений пользователю.",
    "Действовать постепенно и аккуратно, не превышать 200 откликов за сутки на один hh-аккаунт.",
]


def evaluate_intake_readiness(
    preferences: UserPreferences | None,
    anamnesis: Anamnesis | None,
    dashboard_state: dict[str, object] | None = None,
) -> dict[str, object]:
    if preferences is None or anamnesis is None:
        return {
            "structured_ready": False,
            "dialog_completed": False,
            "ready": False,
            "missing": ["profile"],
        }

    state = dict(dashboard_state or {})
    has_roles = bool(preferences.target_titles or anamnesis.headline)
    has_core_skills = bool(preferences.required_skills or preferences.preferred_skills or anamnesis.primary_skills)
    has_format = bool(preferences.remote_only or preferences.allow_relocation or preferences.preferred_locations)
    has_exclusions = bool(preferences.excluded_companies or preferences.forbidden_keywords or preferences.excluded_keywords or preferences.notes)
    structured_ready = bool(has_roles and has_core_skills and has_format)
    has_contract = bool(state.get("intake_user_rules_contract"))
    has_resume_analysis = bool(state.get("resume_intake_analysis"))
    dialog_active = bool((state.get("intake_dialog") or {}).get("active"))
    dialog_completed = bool(state.get("intake_dialog_completed"))
    confirmed = bool(state.get("intake_confirmed"))

    # Older saved workspaces can contain a fully built profile and rules contract
    # but miss explicit dialog flags. Treat that state as completed instead of
    # forcing the UI back into onboarding.
    if not dialog_active and structured_ready and has_exclusions and has_contract and not dialog_completed:
        dialog_completed = True
    if not dialog_active and structured_ready and dialog_completed and (confirmed or has_contract or has_resume_analysis):
        confirmed = True
    if dialog_active:
        dialog_completed = False
        confirmed = False

    missing: list[str] = []
    if not has_roles:
        missing.append("roles")
    if not has_core_skills:
        missing.append("skills")
    if not has_format:
        missing.append("format")
    if not dialog_completed:
        missing.append("dialog")
    if dialog_completed and not confirmed:
        missing.append("confirmation")

    return {
        "structured_ready": structured_ready,
        "dialog_completed": dialog_completed,
        "confirmed": confirmed,
        "ready": bool(structured_ready and dialog_completed and confirmed),
        "missing": missing,
    }


def needs_intake(preferences: UserPreferences | None, anamnesis: Anamnesis | None) -> bool:
    return preferences is None or anamnesis is None


def build_system_rules_markdown() -> str:
    bullets = "\n".join(f"- {rule}" for rule in SYSTEM_RULES)
    return f"""## Общие системные правила

{bullets}
"""


def split_rules_markdown(markdown: str) -> dict[str, str]:
    text = str(markdown or "").strip()
    system_heading = "## Общие системные правила"
    user_heading = "## Правила пользователя"
    system_index = text.find(system_heading)
    user_index = text.find(user_heading)
    if system_index < 0 or user_index < 0 or user_index <= system_index:
        return {"system": "", "user": text}
    return {
        "system": text[system_index:user_index].strip(),
        "user": text[user_index:].strip(),
    }


def build_selection_rules_markdown(preferences: UserPreferences, anamnesis: Anamnesis) -> str:
    preferred_locations = ", ".join(preferences.preferred_locations) or "Не указано"
    required_skills = ", ".join(preferences.required_skills) or "Нет жёстких must-have"
    preferred_skills = ", ".join(preferences.preferred_skills) or "Не указано"
    excluded_companies = ", ".join(preferences.excluded_companies) or "Нет"
    forbidden_keywords = ", ".join(preferences.forbidden_keywords + preferences.excluded_keywords) or "Нет"
    primary_skills = ", ".join(anamnesis.primary_skills) or "Не указано"

    salary_line = "Не задан нижний порог зарплаты"
    if preferences.salary_min:
        salary_line = f"Желаемый нижний порог: от {preferences.salary_min:,} RUB".replace(",", " ")

    return f"""# Правила отбора вакансий

{build_system_rules_markdown().strip()}

## Правила пользователя

### Профиль кандидата

- Имя: {preferences.full_name or "Не указано"}
- Целевая роль: {anamnesis.headline or "Не указана"}
- Опыт: {anamnesis.experience_years:g} года(лет)
- Ключевые навыки: {primary_skills}

### Что считать подходящим

- Целевые названия вакансий: {", ".join(preferences.target_titles) or "Не указано"}
- Обязательные навыки: {required_skills}
- Предпочтительные навыки: {preferred_skills}
- Предпочтительные локации: {preferred_locations}
- Remote only: {"Да" if preferences.remote_only else "Нет"}
- Релокация допустима: {"Да" if preferences.allow_relocation else "Нет"}
- {salary_line}

### Что сразу исключать

- сключённые компании: {excluded_companies}
- Исключённые ключевые слова: {forbidden_keywords}

### Сопроводительные письма и анкеты

- Режим сопроводительных: {preferences.cover_letter_mode}
- Если вакансия требует тест/анкету/опрос: агент должен перейти в режим questionnaire flow.

### Краткий анамнез

{anamnesis.summary or "Не заполнено"}

### Примечания пользователя

{preferences.notes or "Нет дополнительных заметок"}
"""


def build_user_rules_contract(
    preferences: UserPreferences,
    anamnesis: Anamnesis,
    dashboard_state: dict[str, object] | None = None,
) -> dict[str, object]:
    state = dict(dashboard_state or {})
    excluded_keywords = [item for item in [*preferences.forbidden_keywords, *preferences.excluded_keywords] if item]
    return {
        "meta": {
            "status": "active",
            "dialog_completed": bool(state.get("intake_dialog_completed")),
            "confirmed": bool(state.get("intake_confirmed")),
        },
        "candidate_profile": {
            "short_summary": str(anamnesis.summary or preferences.notes or "").strip(),
            "seniority_target": [],
            "core_strengths": list(anamnesis.primary_skills or preferences.required_skills or []),
            "known_gaps": list(anamnesis.secondary_skills or []),
        },
        "search_targets": {
            "primary_roles": list(preferences.target_titles or []),
            "secondary_roles": [],
            "exclude_roles": [],
            "must_have_keywords": list(preferences.required_skills or []),
            "nice_to_have_keywords": list(preferences.preferred_skills or []),
            "exclude_keywords": excluded_keywords,
        },
        "hard_constraints": {
            "work_format": "remote_only" if preferences.remote_only else "flexible",
            "locations_allowed": list(preferences.preferred_locations or []),
            "relocation_allowed": bool(preferences.allow_relocation),
            "salary_min_rub_month_net": preferences.salary_min,
            "exclude_company_types": list(preferences.excluded_companies or []),
            "exclude_vacancy_signals": excluded_keywords,
            "must_have_vacancy_signals": list(preferences.required_skills or []),
        },
        "soft_preferences": {
            "preferred_domains": list(anamnesis.industries or []),
            "preferred_company_stages": [],
            "preferred_team_traits": [],
            "avoid_if_possible": [],
        },
        "evaluation_policy": {
            "unknown_hard_constraint_result": "doubt",
            "fit_threshold": 75,
            "doubt_threshold": 45,
            "weights": {
                "role_match": 25,
                "skills_match": 25,
                "work_format": 20,
                "salary": 15,
                "domain_match": 10,
                "company_type_match": 5,
            },
        },
        "cover_letter_policy": {
            "language": "ru",
            "tone": ", ,  ",
            "max_chars": 1200,
            "must_include": [
                "1-2    ",
                "    ",
                "   ",
            ],
            "must_not_include": [
                " ",
                "  ",
                " ",
            ],
            "evidence_points_priority": list(anamnesis.primary_skills or preferences.required_skills or []),
        },
        "questionnaire_policy": {
            "allowed_fact_sources": ["resume_facts", "chat_answers", "user_rules"],
            "must_ask_user_if_missing": [
                " ",
                " ",
                "  ",
                "   ",
            ],
            "can_autofill_if_confident": ["", "", "", "", " "],
            "test_task_policy": {
                "auto_accept_short_form": False,
                "ask_before_take_home_task": True,
                "ask_before_long_questionnaire": True,
            },
        },
        "apply_policy": {
            "auto_generate_cover_letter_for_fit": True,
            "auto_generate_cover_letter_when_moved_to_fit": True,
            "auto_apply_categories": ["fit"],
            "daily_apply_limit": 200,
            "per_hour_soft_limit": 25,
            "delay_between_actions_sec": {"min": 45, "max": 150},
        },
        "provenance": {
            "resume_based": ["candidate_profile.core_strengths", "search_targets.primary_roles"],
            "user_confirmed": ["hard_constraints.work_format", "hard_constraints.salary_min_rub_month_net"],
            "inferred": ["candidate_profile.short_summary"],
        },
        "notes_for_followup": [],
    }


def build_cover_letter_rules_markdown() -> str:
    return """## Правила написания сопроводительного письма

- Язык письма: только русский.
- Назначение письма: прямой отклик на конкретную вакансию hh.ru.
- Источники фактов: только резюме кандидата, anamnesis, assessment вакансии, полное описание вакансии, подтверждённые пользовательские правила.
- Запрещено выдумывать опыт, достижения, метрики, стек, образование, домен компании, управленческие функции, уровень английского, сертификаты, публикации, командование командой, если соответствующие факты явно не переданы во входном контексте.
- Письмо должно быть адаптировано под конкретную вакансию, а не быть универсальным шаблоном.
- Письмо должно объяснять релевантность кандидата через конкретные совпадения между профилем кандидата и вакансией.
- Основой аргументации должны быть 1-3 наиболее сильных совпадения из assessment и резюме.
- Если совпадение неполное, нельзя утверждать полное соответствие. Нужно формулировать аккуратно: через релевантный опыт, близкие задачи, переносимые навыки.
- Нельзя пересказывать всё резюме подряд.
- Нельзя писать абстрактный канцелярит без привязки к вакансии.
- Нельзя использовать списки, markdown, заголовки, JSON, служебные пометки, префиксы ролей, пояснения о правилах.
- Нельзя вставлять системные инструкции, prompt-текст, hidden rules, chain-of-thought, developer/user/assistant/tool fragments.
- Тон: деловой, плотный, спокойный, без фамильярности, без чрезмерной эмоциональности.
- Структура письма:
  1. Краткая привязка к вакансии и компании.
  2. 1 абзац о релевантном опыте и навыках.
  3. 1 абзац о том, почему кандидат может быть полезен именно в этой роли.
  4. Короткое нейтральное завершение.
- Длина: ориентир 500-1200 символов; письмо должно быть достаточно коротким для поля отклика hh.ru.
- Письмо должно быть пригодно для немедленной вставки в форму отклика без ручной очистки.
"""

def build_cover_letter_response_contract_markdown() -> str:
    return """## Правила формирования ответа агента и записи результата

- Агент должен вернуть только готовый текст сопроводительного письма.
- Агент не должен возвращать пояснение prompt, анализ правил, рассуждение, markdown-заголовки, JSON, XML, YAML, комментарии к себе, дисклеймеры или служебные секции.
- Если модельный backend поддерживает structured output, итоговое поле письма должно находиться в поле cover_letter.
- Если фактов недостаточно для сильных утверждений, агент должен строить осторожный нейтральный текст без домысливания.
- Если assessment содержит ограничения, слабые места или частичное совпадение, агент должен делать акцент на релевантных сторонах профиля, не искажая исходные данные.
- Итоговый текст должен быть чистым пользовательским артефактом без служебных токенов.
- В постоянное хранилище должен записываться только итоговый очищенный текст сопроводительного письма.
- В историю программы должен записываться только факт генерации письма и технические метаданные верхнего уровня.
- В историю программы запрещено записывать полный prompt, системные правила, скрытые инструкции, полное резюме, полный raw output модели и chain-of-thought.
"""
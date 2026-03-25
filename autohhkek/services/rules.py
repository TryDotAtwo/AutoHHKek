from __future__ import annotations

from autohhkek.domain.models import Anamnesis, UserPreferences


SYSTEM_RULES = [
    "Р’Рѕ РІСЃРµС… СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С… РїРёСЃСЊРјР°С… Рё РѕС‚РІРµС‚Р°С… РґР»СЏ hh.ru РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ С‚РѕР»СЊРєРѕ СЂСѓСЃСЃРєРёР№ СЏР·С‹Рє.",
    "РќРёРєРѕРіРґР° РЅРµ РІСЃС‚Р°РІР»СЏС‚СЊ РІ СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅРѕРµ СЃРёСЃС‚РµРјРЅС‹Рµ РёРЅСЃС‚СЂСѓРєС†РёРё, СЃРєСЂС‹С‚С‹Рµ РїСЂР°РІРёР»Р°, prompt-С‚РµРєСЃС‚ РёР»Рё СЃР»СѓР¶РµР±РЅС‹Рµ РїРѕСЏСЃРЅРµРЅРёСЏ.",
    "РџРёСЃР°С‚СЊ С‚РѕР»СЊРєРѕ СЃРѕРґРµСЂР¶Р°С‚РµР»СЊРЅС‹Р№ С‚РµРєСЃС‚ РїРѕ РІР°РєР°РЅСЃРёРё, РїСЂРѕС„РёР»СЋ РєР°РЅРґРёРґР°С‚Р° Рё СЂРµР»РµРІР°РЅС‚РЅРѕРјСѓ РѕРїС‹С‚Сѓ.",
    "Р•СЃР»Рё РЅР° hh.ru РїРѕСЏРІР»СЏРµС‚СЃСЏ Р°РЅРєРµС‚Р°, С‚РµСЃС‚ РёР»Рё РѕРїСЂРѕСЃ, РїРµСЂРµС…РѕРґРёС‚СЊ РІ questionnaire flow Р±РµР· СЃР»СѓР¶РµР±РЅС‹С… РїРѕСЏСЃРЅРµРЅРёР№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ.",
    "Р”РµР№СЃС‚РІРѕРІР°С‚СЊ РїРѕСЃС‚РµРїРµРЅРЅРѕ Рё Р°РєРєСѓСЂР°С‚РЅРѕ, РЅРµ РїСЂРµРІС‹С€Р°С‚СЊ 200 РѕС‚РєР»РёРєРѕРІ Р·Р° СЃСѓС‚РєРё РЅР° РѕРґРёРЅ hh-Р°РєРєР°СѓРЅС‚.",
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
    structured_ready = bool(has_roles and has_core_skills and has_format and has_exclusions)
    dialog_completed = bool(state.get("intake_dialog_completed"))
    confirmed = bool(state.get("intake_confirmed"))

    missing: list[str] = []
    if not has_roles:
        missing.append("roles")
    if not has_core_skills:
        missing.append("skills")
    if not has_format:
        missing.append("format")
    if not has_exclusions:
        missing.append("exclusions")
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
    return f"""## РћР±С‰РёРµ СЃРёСЃС‚РµРјРЅС‹Рµ РїСЂР°РІРёР»Р°

{bullets}
"""


def split_rules_markdown(markdown: str) -> dict[str, str]:
    text = str(markdown or "").strip()
    system_heading = "## РћР±С‰РёРµ СЃРёСЃС‚РµРјРЅС‹Рµ РїСЂР°РІРёР»Р°"
    user_heading = "## РџСЂР°РІРёР»Р° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ"
    system_index = text.find(system_heading)
    user_index = text.find(user_heading)
    if system_index < 0 or user_index < 0 or user_index <= system_index:
        return {"system": "", "user": text}
    return {
        "system": text[system_index:user_index].strip(),
        "user": text[user_index:].strip(),
    }


def build_selection_rules_markdown(preferences: UserPreferences, anamnesis: Anamnesis) -> str:
    preferred_locations = ", ".join(preferences.preferred_locations) or "РќРµ СѓРєР°Р·Р°РЅРѕ"
    required_skills = ", ".join(preferences.required_skills) or "РќРµС‚ Р¶С‘СЃС‚РєРёС… must-have"
    preferred_skills = ", ".join(preferences.preferred_skills) or "РќРµ СѓРєР°Р·Р°РЅРѕ"
    excluded_companies = ", ".join(preferences.excluded_companies) or "РќРµС‚"
    forbidden_keywords = ", ".join(preferences.forbidden_keywords + preferences.excluded_keywords) or "РќРµС‚"
    primary_skills = ", ".join(anamnesis.primary_skills) or "РќРµ СѓРєР°Р·Р°РЅРѕ"

    salary_line = "РќРµ Р·Р°РґР°РЅ РЅРёР¶РЅРёР№ РїРѕСЂРѕРі Р·Р°СЂРїР»Р°С‚С‹"
    if preferences.salary_min:
        salary_line = f"Р–РµР»Р°РµРјС‹Р№ РЅРёР¶РЅРёР№ РїРѕСЂРѕРі: РѕС‚ {preferences.salary_min:,} RUB".replace(",", " ")

    return f"""# РџСЂР°РІРёР»Р° РѕС‚Р±РѕСЂР° РІР°РєР°РЅСЃРёР№

{build_system_rules_markdown().strip()}

## РџСЂР°РІРёР»Р° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ

### РџСЂРѕС„РёР»СЊ РєР°РЅРґРёРґР°С‚Р°

- РРјСЏ: {preferences.full_name or "РќРµ СѓРєР°Р·Р°РЅРѕ"}
- Р¦РµР»РµРІР°СЏ СЂРѕР»СЊ: {anamnesis.headline or "РќРµ СѓРєР°Р·Р°РЅР°"}
- РћРїС‹С‚: {anamnesis.experience_years:g} РіРѕРґР°(Р»РµС‚)
- РљР»СЋС‡РµРІС‹Рµ РЅР°РІС‹РєРё: {primary_skills}

### Р§С‚Рѕ СЃС‡РёС‚Р°С‚СЊ РїРѕРґС…РѕРґСЏС‰РёРј

- Р¦РµР»РµРІС‹Рµ РЅР°Р·РІР°РЅРёСЏ РІР°РєР°РЅСЃРёР№: {", ".join(preferences.target_titles) or "РќРµ СѓРєР°Р·Р°РЅРѕ"}
- РћР±СЏР·Р°С‚РµР»СЊРЅС‹Рµ РЅР°РІС‹РєРё: {required_skills}
- РџСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅС‹Рµ РЅР°РІС‹РєРё: {preferred_skills}
- РџСЂРµРґРїРѕС‡С‚РёС‚РµР»СЊРЅС‹Рµ Р»РѕРєР°С†РёРё: {preferred_locations}
- Remote only: {"Р”Р°" if preferences.remote_only else "РќРµС‚"}
- Р РµР»РѕРєР°С†РёСЏ РґРѕРїСѓСЃС‚РёРјР°: {"Р”Р°" if preferences.allow_relocation else "РќРµС‚"}
- {salary_line}

### Р§С‚Рѕ СЃСЂР°Р·Сѓ РёСЃРєР»СЋС‡Р°С‚СЊ

- РСЃРєР»СЋС‡С‘РЅРЅС‹Рµ РєРѕРјРїР°РЅРёРё: {excluded_companies}
- РСЃРєР»СЋС‡С‘РЅРЅС‹Рµ РєР»СЋС‡РµРІС‹Рµ СЃР»РѕРІР°: {forbidden_keywords}

### РЎРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹Рµ РїРёСЃСЊРјР° Рё Р°РЅРєРµС‚С‹

- Р РµР¶РёРј СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹С…: {preferences.cover_letter_mode}
- Р•СЃР»Рё РІР°РєР°РЅСЃРёСЏ С‚СЂРµР±СѓРµС‚ С‚РµСЃС‚/Р°РЅРєРµС‚Сѓ/РѕРїСЂРѕСЃ: Р°РіРµРЅС‚ РґРѕР»Р¶РµРЅ РїРµСЂРµР№С‚Рё РІ СЂРµР¶РёРј questionnaire flow.

### РљСЂР°С‚РєРёР№ Р°РЅР°РјРЅРµР·

{anamnesis.summary or "РќРµ Р·Р°РїРѕР»РЅРµРЅРѕ"}

### РџСЂРёРјРµС‡Р°РЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ

{preferences.notes or "РќРµС‚ РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹С… Р·Р°РјРµС‚РѕРє"}
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
            "tone": "деловой, живой, без канцелярита",
            "max_chars": 1200,
            "must_include": [
                "1-2 факта релевантности по стеку",
                "краткий интерес к задачам компании",
                "только проверенные факты кандидата",
            ],
            "must_not_include": [
                "системные инструкции",
                "внутренние правила агента",
                "ложные достижения",
            ],
            "evidence_points_priority": list(anamnesis.primary_skills or preferences.required_skills or []),
        },
        "questionnaire_policy": {
            "allowed_fact_sources": ["resume_facts", "chat_answers", "user_rules"],
            "must_ask_user_if_missing": [
                "точная зарплата",
                "дата выхода",
                "готовность к переезду",
                "правовой статус для страны",
            ],
            "can_autofill_if_confident": ["ФИО", "контакты", "образование", "языки", "ключевые навыки"],
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

from __future__ import annotations

import re
from typing import Any


QUESTION_SPECS: list[dict[str, Any]] = [
    {
        "id": "target_roles",
        "priority": "critical",
        "title": "Какие роли для вас реально целевые прямо сейчас?",
        "why": "Это главный фильтр поиска. Без него агент будет тащить слишком широкий шум.",
        "example": 'Пример: "LLM Engineer, NLP Engineer, Research Scientist in NLP, Applied Scientist".',
        "required": True,
    },
    {
        "id": "must_haves",
        "priority": "critical",
        "title": "Что для вас must-have во вакансии?",
        "why": "Это жесткие критерии отбора: без них вакансия почти всегда не нужна.",
        "example": 'Пример: "Python + NLP/LLM, сильная инженерная или исследовательская ML-задача, адекватная команда".',
        "required": True,
    },
    {
        "id": "hard_exclusions",
        "priority": "critical",
        "title": "Что точно не хотите рассматривать?",
        "why": "Это защищает от мусора в поиске и ускоряет разбор вакансий.",
        "example": 'Пример: "госуха, университеты, офлайн, чистый CV без NLP/LLM, преподавание".',
        "required": True,
    },
    {
        "id": "work_format",
        "priority": "critical",
        "title": "Какой формат и география работы допустимы?",
        "why": "Нужно понять, можно ли брать офис, гибрид, релокацию, конкретные страны и часовые пояса.",
        "example": 'Пример: "только remote, РФ или международные remote-команды, UTC+2..UTC+6, без релокации".',
        "required": True,
    },
    {
        "id": "salary",
        "priority": "critical",
        "title": "Какая минимальная компенсация имеет смысл?",
        "why": "Это помогает отсеивать заведомо слабые вакансии и правильно ранжировать сомнительные.",
        "example": 'Пример: "ниже 350 000 net не интересно, комфортно от 450 000+".',
        "required": False,
    },
    {
        "id": "company_types",
        "priority": "important",
        "title": "Какие типы компаний вам интересны, а какие исключаем?",
        "why": "Это влияет и на поиск, и на итоговый fit: продукт, стартап, корпорация, аутсорс, research lab и так далее.",
        "example": 'Пример: "интересны AI product companies и R&D-команды; не хочу интеграторов, окологос и академию".',
        "required": False,
    },
    {
        "id": "domains",
        "priority": "important",
        "title": "Какие домены и типы задач для вас желательны?",
        "why": "Так агент сможет отличать полезные смежные вакансии от нерелевантных.",
        "example": 'Пример: "интересно: LLM products, AI infra, applied research; не хочу adtech, pure analytics, support".',
        "required": False,
    },
    {
        "id": "nice_to_have",
        "priority": "important",
        "title": "Что будет сильным плюсом, но не является обязательным?",
        "why": "Это помогает тоньше ранжировать fit и doubt вакансии.",
        "example": 'Пример: "RAG, MLOps, агентные системы, публикации, production ML".',
        "required": False,
    },
    {
        "id": "weights",
        "priority": "important",
        "title": "Какие критерии жесткие, а где допустим компромисс?",
        "why": "Без этого агент не понимает, чем можно пожертвовать, а чем нельзя.",
        "example": 'Пример: "удаленка и отсутствие гос/вуз-тематики жестко; зарплата и домен обсуждаемы".',
        "required": False,
    },
    {
        "id": "extra_context",
        "priority": "nice",
        "title": "Есть ли еще важный контекст для откликов и сопроводительных?",
        "why": "Это финальная доводка: что подчеркнуть, чего избегать и как лучше себя позиционировать.",
        "example": 'Пример: "в сопроводительном делать акцент на research + engineering mix, писать только по-русски".',
        "required": False,
    },
]


def _split_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").replace("\r", "\n").replace(";", ",").replace("\n", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_salary_floor(value: str) -> int | None:
    digits = re.findall(r"\d[\d\s]{2,}", str(value or ""))
    if not digits:
        return None
    normalized = digits[0].replace(" ", "")
    try:
        return int(normalized)
    except ValueError:
        return None


def _looks_remote_only(value: str) -> bool:
    lowered = str(value or "").casefold()
    positive = ("только remote", "только удал", "remote only", "полностью удал")
    negative = ("гибрид", "офис", "on-site", "onsite")
    return any(token in lowered for token in positive) and not any(token in lowered for token in negative)


def _looks_relocation_allowed(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(token in lowered for token in ("релокац", "переезд", "relocation"))


def _infer_roles_from_text(*values: str) -> list[str]:
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
    joined = "\n".join(str(value or "") for value in values).casefold()
    return [role for role in known_roles if role.casefold() in joined]


def _infer_skills_from_text(*values: str) -> list[str]:
    known_skills = ["Python", "NLP", "LLM", "Transformers", "PyTorch", "RAG", "MLOps", "SQL"]
    joined = "\n".join(str(value or "") for value in values).casefold()
    return [skill for skill in known_skills if skill.casefold() in joined]


def build_intake_context(store) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    hh_resumes = store.load_hh_resumes()
    resume_title = ""
    for item in hh_resumes:
        title = str(item.get("title") or "").strip()
        if title:
            resume_title = title
            break
    summary = str(getattr(anamnesis, "summary", "") or getattr(preferences, "notes", "") or "").strip()
    inferred_roles = _infer_roles_from_text(resume_title, summary)
    inferred_skills = _infer_skills_from_text(resume_title, summary)
    return {
        "resume_title": resume_title,
        "summary": summary,
        "inferred_roles": inferred_roles,
        "inferred_skills": inferred_skills,
        "languages": list(getattr(anamnesis, "languages", []) or []),
        "links": list(getattr(anamnesis, "links", []) or []),
        "existing_target_titles": list(getattr(preferences, "target_titles", []) or []),
        "existing_required_skills": list(getattr(preferences, "required_skills", []) or []),
        "existing_preferred_skills": list(getattr(preferences, "preferred_skills", []) or []),
        "existing_locations": list(getattr(preferences, "preferred_locations", []) or []),
        "remote_only": bool(getattr(preferences, "remote_only", False)),
    }


def build_intake_questions(store) -> list[dict[str, Any]]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    context = build_intake_context(store)
    questions: list[dict[str, Any]] = []
    for spec in QUESTION_SPECS:
        question = dict(spec)
        question["prefill"] = ""
        if spec["id"] == "target_roles":
            question["prefill"] = ", ".join(context["existing_target_titles"] or context["inferred_roles"])
        elif spec["id"] == "must_haves":
            question["prefill"] = ", ".join(getattr(preferences, "required_skills", []) or [])
        elif spec["id"] == "work_format":
            if getattr(preferences, "preferred_locations", None) or getattr(preferences, "remote_only", False):
                mode_bits = []
                if getattr(preferences, "remote_only", False):
                    mode_bits.append("только remote")
                if getattr(preferences, "preferred_locations", None):
                    mode_bits.append(", ".join(preferences.preferred_locations))
                question["prefill"] = "; ".join(mode_bits)
        elif spec["id"] == "salary":
            if getattr(preferences, "salary_min", None):
                question["prefill"] = str(preferences.salary_min)
        elif spec["id"] == "company_types":
            question["prefill"] = ", ".join(getattr(preferences, "excluded_companies", []) or [])
        elif spec["id"] == "nice_to_have":
            question["prefill"] = ", ".join(getattr(preferences, "preferred_skills", []) or context["inferred_skills"])
        elif spec["id"] == "extra_context":
            question["prefill"] = str(getattr(preferences, "notes", "") or getattr(anamnesis, "summary", "") or "")[:300]
        questions.append(question)
    return questions


def start_intake_dialog(store) -> dict[str, Any]:
    context = build_intake_context(store)
    questions = build_intake_questions(store)
    state = {
        "active": True,
        "step_index": 0,
        "questions": questions,
        "answers": {},
        "context": context,
        "started_at": str(store.load_dashboard_state().get("last_intake_started_at") or ""),
    }
    store.update_dashboard_state(
        {
            "intake_dialog_completed": False,
            "intake_dialog": state,
        }
    )
    return state


def _dialog_intro(context: dict[str, Any], *, remaining: int) -> list[str]:
    facts = [
        "Сначала проведем обязательный опрос. Пока он не завершен, поиск вакансий и анализ запускать не нужно.",
        "",
        "Что уже удалось понять из резюме и текущего профиля:",
        f"- Заголовок резюме: {context.get('resume_title') or 'не удалось определить'}",
        f"- Роли из резюме: {', '.join(context.get('inferred_roles') or []) or 'не выделены уверенно'}",
        f"- Навыки из резюме/заметок: {', '.join(context.get('inferred_skills') or []) or 'не выделены уверенно'}",
        f"- Языки: {', '.join(context.get('languages') or []) or 'не указаны'}",
        f"- Ссылки: {', '.join(context.get('links') or []) or 'не указаны'}",
        "",
        f"Вопросов осталось: {remaining}. Отвечайте свободно, можно коротко. Если пункт неважен, напишите «пропустить».",
    ]
    return facts


def render_dialog_message(dialog_state: dict[str, Any]) -> str:
    questions = list(dialog_state.get("questions") or [])
    step_index = int(dialog_state.get("step_index") or 0)
    if step_index >= len(questions):
        return "Опрос завершен. Собираю пользовательские правила."
    current = questions[step_index]
    lines = _dialog_intro(dict(dialog_state.get("context") or {}), remaining=max(0, len(questions) - step_index))
    lines.extend(
        [
            "",
            f"Вопрос {step_index + 1} из {len(questions)}.",
            current["title"],
            f"Почему это важно: {current['why']}",
            current["example"],
        ]
    )
    prefill = str(current.get("prefill") or "").strip()
    if prefill:
        lines.append(f"Что уже вижу по этому пункту: {prefill}")
    return "\n".join(lines)


def _compose_notes(answers: dict[str, str]) -> str:
    ordered = [
        ("Роли", answers.get("target_roles", "")),
        ("Обязательное", answers.get("must_haves", "")),
        ("Не хочу", answers.get("hard_exclusions", "")),
        ("Локация и формат", answers.get("work_format", "")),
        ("Зарплата", answers.get("salary", "")),
        ("Компании и типы компаний", answers.get("company_types", "")),
        ("Домены", answers.get("domains", "")),
        ("Желательное", answers.get("nice_to_have", "")),
        ("Вес критериев", answers.get("weights", "")),
        ("Дополнительный контекст", answers.get("extra_context", "")),
    ]
    return "\n".join(f"{label}: {value}" for label, value in ordered if str(value or "").strip())


def synthesize_intake_payload(store, dialog_state: dict[str, Any]) -> dict[str, Any]:
    preferences = store.load_preferences()
    anamnesis = store.load_anamnesis()
    context = dict(dialog_state.get("context") or {})
    answers = {str(key): str(value).strip() for key, value in dict(dialog_state.get("answers") or {}).items()}

    role_values = _split_items(answers.get("target_roles") or context.get("inferred_roles") or preferences.target_titles)
    required_skills = _split_items(answers.get("must_haves") or preferences.required_skills)
    preferred_skills = _split_items(answers.get("nice_to_have") or preferences.preferred_skills or context.get("inferred_skills"))
    location_text = answers.get("work_format") or ", ".join(preferences.preferred_locations or [])
    company_text = answers.get("company_types") or ", ".join(preferences.excluded_companies or [])
    exclusion_text = answers.get("hard_exclusions") or ", ".join(preferences.forbidden_keywords or preferences.excluded_keywords or [])
    summary_text = answers.get("extra_context") or context.get("summary") or preferences.notes or anamnesis.summary

    payload = {
        "target_titles": role_values,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "preferred_locations": _split_items(location_text),
        "excluded_companies": _split_items(company_text),
        "forbidden_keywords": _split_items(exclusion_text),
        "salary_min": _parse_salary_floor(answers.get("salary", "")),
        "remote_only": _looks_remote_only(location_text),
        "allow_relocation": _looks_relocation_allowed(location_text),
        "notes": _compose_notes(answers),
        "headline": ", ".join(role_values[:3])[:180] or anamnesis.headline,
        "summary": summary_text,
        "primary_skills": list(anamnesis.primary_skills or context.get("inferred_skills") or []),
        "industries": _split_items(answers.get("domains", "") or anamnesis.industries),
        "education": list(anamnesis.education or []),
        "languages": list(anamnesis.languages or []),
        "links": list(anamnesis.links or []),
    }
    return payload


def advance_intake_dialog(store, text: str) -> dict[str, Any]:
    dashboard_state = store.load_dashboard_state()
    dialog_state = dict(dashboard_state.get("intake_dialog") or {})
    if not dialog_state.get("active"):
        dialog_state = start_intake_dialog(store)

    questions = list(dialog_state.get("questions") or [])
    step_index = int(dialog_state.get("step_index") or 0)
    if step_index >= len(questions):
        return {"status": "completed", "dialog_state": dialog_state}

    current = questions[step_index]
    answers = dict(dialog_state.get("answers") or {})
    normalized = str(text or "").strip()
    answers[current["id"]] = "" if normalized.casefold() in {"пропустить", "skip"} else normalized
    dialog_state["answers"] = answers
    dialog_state["step_index"] = step_index + 1

    if dialog_state["step_index"] >= len(questions):
        dialog_state["active"] = False
        store.update_dashboard_state(
            {
                "intake_dialog_completed": True,
                "intake_dialog": dialog_state,
            }
        )
        return {"status": "completed", "dialog_state": dialog_state}

    store.update_dashboard_state({"intake_dialog": dialog_state})
    return {"status": "running", "dialog_state": dialog_state}

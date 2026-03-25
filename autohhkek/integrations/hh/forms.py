from __future__ import annotations

from urllib.parse import urlparse

from autohhkek.domain.enums import QuestionKind, ScreeningPlatform
from autohhkek.domain.models import Anamnesis, QuestionField, ScreeningPlan, UserPreferences, Vacancy


def detect_screening_platform(url: str) -> ScreeningPlatform:
    host = urlparse(url).netloc.lower()
    if "docs.google.com" in host or "forms.gle" in host:
        return ScreeningPlatform.GOOGLE_FORMS
    if "forms.yandex" in host or "forms.yandex.ru" in host:
        return ScreeningPlatform.YANDEX_FORMS
    if "hh.ru" in host:
        return ScreeningPlatform.HH
    return ScreeningPlatform.UNKNOWN


def build_screening_plan(vacancy: Vacancy) -> ScreeningPlan:
    platform = detect_screening_platform(vacancy.url)
    text = vacancy.searchable_text().lower()
    notes: list[str] = []
    questions: list[QuestionField] = []
    if any(marker in text for marker in ("тест", "опрос", "анкета", "скрининг")):
        notes.append("Вакансия содержит признаки предварительного теста или анкеты.")
        questions.append(
            QuestionField(
                label="Мотивация и релевантный опыт",
                kind=QuestionKind.LONG_TEXT,
                required=True,
                description="Типичный открытый вопрос для hh-анкеты, Google Forms или Yandex Forms.",
            )
        )
    if "зарплат" in text:
        questions.append(
            QuestionField(
                label="Ожидаемая зарплата",
                kind=QuestionKind.NUMBER,
                required=False,
            )
        )
    if "релокац" in text or "переезд" in text:
        questions.append(
            QuestionField(
                label="Готовность к релокации",
                kind=QuestionKind.SINGLE_CHOICE,
                required=False,
                options=["Да", "Нет", "Обсуждаемо"],
            )
        )
    if "github" in text or "портфолио" in text:
        questions.append(
            QuestionField(
                label="Ссылка на портфолио / GitHub",
                kind=QuestionKind.SHORT_TEXT,
                required=False,
            )
        )
    return ScreeningPlan(
        platform=platform,
        target_url=vacancy.url,
        questions=questions,
        notes=notes or ["Явных сигналов теста не найдено, но анкету всё равно стоит ожидать."],
        requires_manual_review=False,
    )


def answer_question(question: QuestionField, anamnesis: Anamnesis, preferences: UserPreferences, vacancy: Vacancy) -> str | list[str]:
    label = question.label.lower()
    if "зарплат" in label and preferences.salary_min:
        return str(preferences.salary_min)
    if "релокац" in label:
        return "Да" if preferences.allow_relocation else "Нет"
    if "опыт" in label or "experience" in label:
        return f"{anamnesis.experience_years:g}"
    if "портфолио" in label or "github" in label:
        return anamnesis.links[0] if anamnesis.links else "Ссылка будет предоставлена по запросу."
    if question.kind in {QuestionKind.LONG_TEXT, QuestionKind.SHORT_TEXT}:
        return (
            f"Интересна вакансия {vacancy.title}. "
            f"Релевантный опыт: {anamnesis.summary or ', '.join(anamnesis.primary_skills[:5])}. "
            f"Готов пройти дополнительные этапы отбора."
        )
    if question.kind in {QuestionKind.SINGLE_CHOICE, QuestionKind.DROPDOWN} and question.options:
        normalized = [item.lower() for item in question.options]
        if "да" in normalized and preferences.allow_relocation:
            return question.options[normalized.index("да")]
        if "нет" in normalized and not preferences.allow_relocation:
            return question.options[normalized.index("нет")]
        return question.options[0]
    if question.kind == QuestionKind.MULTI_CHOICE and question.options:
        return question.options[: min(2, len(question.options))]
    return "Требуется ручная проверка."

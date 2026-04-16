from __future__ import annotations

from autohhkek.domain.models import ResumeDraft, Vacancy, VacancyAssessment
from autohhkek.services.storage import WorkspaceStore


class ResumeAgent:
    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def build_resume_draft(self) -> tuple[ResumeDraft, str]:
        preferences = self.store.load_preferences()
        anamnesis = self.store.load_anamnesis()
        if not preferences or not anamnesis:
            raise RuntimeError("Нельзя строить резюме без intake.")

        summary = anamnesis.summary or (
            f"{anamnesis.headline}. Опыт {anamnesis.experience_years:g} лет. "
            f"Ключевой стек: {', '.join(anamnesis.primary_skills[:6])}."
        )
        draft = ResumeDraft(
            title=anamnesis.headline or "ML / LLM Engineer",
            summary=summary,
            key_skills=anamnesis.primary_skills + anamnesis.secondary_skills[:4],
            experience_highlights=anamnesis.achievements or ["Добавьте 3-5 достижений с цифрами и эффектом."],
            project_highlights=[
                f"Рекомендуется адаптировать резюме под роли: {', '.join(preferences.target_titles[:4]) or 'уточните целевые роли'}."
            ],
            education=anamnesis.education or ["Добавьте образование, курсы и сертификаты."],
            notes=[
                "Сделайте отдельные версии резюме под LLM/NLP и Data Science при необходимости.",
                "Добавьте блок с публикациями, pet-проектами и реальными impact-кейсами.",
            ],
        )
        markdown = self._to_markdown(draft)
        self.store.save_resume_draft(draft, markdown)
        self.store.record_event("resume", "Собран черновик резюме.")
        return draft, markdown


    def _to_markdown(self, draft: ResumeDraft) -> str:
        bullets = "\n".join(f"- {item}" for item in draft.key_skills)
        highlights = "\n".join(f"- {item}" for item in draft.experience_highlights)
        projects = "\n".join(f"- {item}" for item in draft.project_highlights)
        education = "\n".join(f"- {item}" for item in draft.education)
        notes = "\n".join(f"- {item}" for item in draft.notes)
        return f"""# Черновик резюме

## Целевая роль

{draft.title}

## Summary

{draft.summary}

## Key Skills

{bullets}

## Experience Highlights

{highlights}

## Project Highlights

{projects}

## Education

{education}

## Notes

{notes}
"""

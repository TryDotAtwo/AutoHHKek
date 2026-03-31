from __future__ import annotations

from autohhkek.domain.models import Anamnesis, RuntimeSettings, Vacancy, VacancyAssessment
from autohhkek.services.analysis import VacancyRuleEngine

from .g4f_review_agent import G4FVacancyReviewer
from .openai_review_agent import OpenAIVacancyReviewer
from .openrouter_review_agent import OpenRouterVacancyReviewer


class VacancyReviewAgent:
    def __init__(
        self,
        preferences,
        anamnesis: Anamnesis,
        llm_backend: str = "",
        runtime_settings: RuntimeSettings | None = None,
        openai_reviewer: OpenAIVacancyReviewer | None = None,
        openrouter_reviewer: OpenRouterVacancyReviewer | None = None,
        g4f_reviewer: G4FVacancyReviewer | None = None,
    ) -> None:
        self.preferences = preferences
        self.anamnesis = anamnesis
        self.runtime_settings = runtime_settings or RuntimeSettings(llm_backend=llm_backend or "openrouter")
        self.rule_engine = VacancyRuleEngine(preferences, anamnesis)
        self.openai_reviewer = openai_reviewer or OpenAIVacancyReviewer()
        self.openrouter_reviewer = openrouter_reviewer or OpenRouterVacancyReviewer()
        self.g4f_reviewer = g4f_reviewer or G4FVacancyReviewer()

    def review(self, vacancy: Vacancy) -> VacancyAssessment:
        backend = self.runtime_settings.llm_backend
        if backend == "g4f":
            reviewer = self.g4f_reviewer
        elif backend == "openrouter":
            reviewer = self.openrouter_reviewer
        else:
            reviewer = self.openai_reviewer
        assessment = reviewer.review(vacancy, self.preferences, self.anamnesis)
        if assessment is not None:
            return assessment

        assessment = self.rule_engine.assess(vacancy)
        assessment.review_strategy = "rule_based_fallback"
        last_status = getattr(reviewer, "last_status", "unknown")
        last_error = getattr(reviewer, "last_error", "")
        if last_status == "unavailable":
            assessment.review_notes = f"LLM-проверка через {self.runtime_settings.llm_backend} недоступна. Использованы детерминированные правила."
        elif last_status == "error":
            assessment.review_notes = (
                f"LLM-проверка через {self.runtime_settings.llm_backend} завершилась ошибкой, поэтому использованы детерминированные правила. "
                f"Последняя ошибка: {last_error}"
            )
        else:
            assessment.review_notes = "Использованы детерминированные правила как стабильный fallback."
        return assessment

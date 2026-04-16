# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

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

    def _select_reviewer(self):
        backend = self.runtime_settings.llm_backend
        if backend == "g4f":
            return self.g4f_reviewer
        if backend == "openrouter":
            return self.openrouter_reviewer
        return self.openai_reviewer

    def _build_rule_fallback(self, reviewer, vacancy: Vacancy) -> VacancyAssessment:
        assessment = self.rule_engine.assess(vacancy)
        assessment.review_strategy = "rule_based_fallback"

        backend_name = self.runtime_settings.llm_backend
        last_status = getattr(reviewer, "last_status", "unknown")
        last_error = getattr(reviewer, "last_error", "")

        if last_status == "unavailable":
            assessment.review_notes = (
                f"LLM-проверка через {backend_name} недоступна. "
                "Использованы детерминированные правила."
            )
        elif last_status == "error":
            assessment.review_notes = (
                f"LLM-проверка через {backend_name} завершилась ошибкой, поэтому использованы "
                f"детерминированные правила. Последняя ошибка: {last_error}"
            )
        elif last_status == "parse_error":
            assessment.review_notes = (
                f"LLM-проверка через {backend_name} вернула невалидный ответ, поэтому использованы "
                "детерминированные правила."
            )
        elif last_status == "empty":
            assessment.review_notes = (
                f"LLM-проверка через {backend_name} не вернула содержательный результат, поэтому "
                "использованы детерминированные правила."
            )
        else:
            assessment.review_notes = "Использованы детерминированные правила как стабильный fallback."

        return assessment

    def review(self, vacancy: Vacancy) -> VacancyAssessment | None:
        reviewer = self._select_reviewer()
        return reviewer.review(vacancy, self.preferences, self.anamnesis)

    async def review_async(self, vacancy: Vacancy) -> VacancyAssessment | None:
        reviewer = self._select_reviewer()

        review_async_fn = getattr(reviewer, "review_async", None)
        if callable(review_async_fn):
            return await review_async_fn(vacancy, self.preferences, self.anamnesis)

        return await asyncio.to_thread(reviewer.review, vacancy, self.preferences, self.anamnesis)
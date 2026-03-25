from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import BaseModel, Field

from autohhkek.domain.enums import FitCategory, ReasonGroup
from autohhkek.domain.models import Anamnesis, AssessmentReason, UserPreferences, Vacancy, VacancyAssessment
from autohhkek.services.openai_runtime import OpenAIAppConfig


class VacancyReasonOutput(BaseModel):
    code: str
    label: str
    group: str = "neutral"
    detail: str = ""
    weight: float = 0.0
    subcategory: str = ""


class VacancyReviewOutput(BaseModel):
    category: str
    subcategory: str = ""
    score: float = 50.0
    explanation: str = ""
    recommended_action: str = ""
    review_notes: str = ""
    reasons: list[VacancyReasonOutput] = Field(default_factory=list)


RunnerFn = Callable[[Any, str, Any], Any]


class OpenAIVacancyReviewer:
    def __init__(self, config: OpenAIAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or OpenAIAppConfig.from_env()
        self.runner = runner or self._run_sync
        self.last_status = "idle"
        self.last_error = ""

    def review(
        self,
        vacancy: Vacancy,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
    ) -> VacancyAssessment | None:
        if not self.config.is_available():
            self.last_status = "unavailable"
            self.last_error = ""
            return None

        try:
            result = self.runner(
                self._build_agent(),
                self._build_prompt(vacancy, preferences, anamnesis),
                run_config=self.config.build_run_config(workflow_name="AutoHHKek vacancy review"),
            )
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = str(exc)
            return None

        output = getattr(result, "final_output", None)
        if output is None:
            self.last_status = "empty"
            self.last_error = "missing final_output"
            return None
        if not isinstance(output, VacancyReviewOutput):
            try:
                output = VacancyReviewOutput.model_validate(output)
            except Exception as exc:  # noqa: BLE001
                self.last_status = "error"
                self.last_error = str(exc)
                return None
        self.last_status = "ok"
        self.last_error = ""
        return self._to_assessment(vacancy, output)

    def _build_agent(self):
        from agents import Agent

        return Agent(
            name="AutoHHKek Vacancy Reviewer",
            model=self.config.model,
            instructions=(
                "Ты оцениваешь вакансии hh.ru для одного кандидата. "
                "Возвращай только структурированный ответ. "
                "Пиши только на русском языке. "
                "Классифицируй вакансии в fit, doubt или no_fit. "
                "Объясняй решение кратко и по делу, оценки держи в диапазоне от 0 до 100, причины делай короткими и машиночитаемыми."
            ),
            output_type=VacancyReviewOutput,
        )

    def _build_prompt(
        self,
        vacancy: Vacancy,
        preferences: UserPreferences,
        anamnesis: Anamnesis,
    ) -> str:
        payload = {
            "vacancy": vacancy.to_dict(),
            "preferences": preferences.to_dict(),
            "anamnesis": anamnesis.to_dict(),
        }
        return (
            "Оцени эту вакансию относительно профиля кандидата.\n"
            "Отвечай только на русском языке.\n"
            "Верни структурированные причины с group = positive, neutral или negative.\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _run_sync(self, agent, prompt: str, run_config=None):
        from agents import Runner

        return Runner.run_sync(agent, prompt, run_config=run_config)

    def _to_assessment(self, vacancy: Vacancy, output: VacancyReviewOutput) -> VacancyAssessment:
        category = _coerce_category(output.category)
        reasons = [
            AssessmentReason(
                code=reason.code,
                label=reason.label,
                group=_coerce_reason_group(reason.group),
                detail=reason.detail,
                weight=reason.weight,
                subcategory=reason.subcategory,
            )
            for reason in output.reasons
        ]
        recommended_action = output.recommended_action or _default_action(category)
        return VacancyAssessment(
            vacancy_id=vacancy.vacancy_id,
            category=category,
            subcategory=output.subcategory or "llm_review",
            score=max(0.0, min(100.0, output.score)),
            explanation=output.explanation or "Вакансия оценена агентом OpenAI.",
            reasons=reasons,
            recommended_action=recommended_action,
            ready_for_apply=category == FitCategory.FIT,
            review_strategy="openai_agent",
            review_notes=output.review_notes or f"Проверено моделью {self.config.model}.",
        )


def _coerce_category(value: str) -> FitCategory:
    normalized = (value or "").strip().lower()
    if normalized == FitCategory.FIT.value:
        return FitCategory.FIT
    if normalized == FitCategory.NO_FIT.value:
        return FitCategory.NO_FIT
    return FitCategory.DOUBT


def _coerce_reason_group(value: str) -> ReasonGroup:
    normalized = (value or "").strip().lower()
    if normalized == ReasonGroup.POSITIVE.value:
        return ReasonGroup.POSITIVE
    if normalized == ReasonGroup.NEGATIVE.value:
        return ReasonGroup.NEGATIVE
    return ReasonGroup.NEUTRAL


def _default_action(category: FitCategory) -> str:
    return {
        FitCategory.FIT: "Откликнуться",
        FitCategory.DOUBT: "Проверить вручную",
        FitCategory.NO_FIT: "Пропустить",
    }[category]

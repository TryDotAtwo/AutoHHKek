from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
from typing import Any, Callable

from autohhkek.agents.openai_review_agent import (
    VacancyReviewOutput,
    _coerce_category,
    _coerce_reason_group,
    _default_action,
)
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, AssessmentReason, UserPreferences, Vacancy, VacancyAssessment
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


RunnerFn = Callable[[Any, str, Any], Any]


class OpenRouterVacancyReviewer:
    def __init__(self, config: OpenRouterAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or OpenRouterAppConfig.from_env()
        self.runner = runner or self._run_sync
        self.last_status = "idle"
        self.last_error = ""
        self.last_model = self.config.model
        self.review_timeout_sec = max(5.0, float(getattr(self.config, "timeout_sec", 25.0) or 25.0) + 5.0)

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

        result = None
        prompt = self._build_prompt(vacancy, preferences, anamnesis)
        errors: list[str] = []
        for model in self._candidate_models():
            self.last_model = model
            try:
                result = self.runner(
                    self._build_agent(model),
                    prompt,
                    run_config=self.config.build_run_config(
                        workflow_name="AutoHHKek OpenRouter vacancy review",
                        model=model,
                    ),
                )
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {exc}")
                continue

        if result is None:
            self.last_status = "error"
            self.last_error = " | ".join(errors)
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

    def _candidate_models(self) -> list[str]:
        candidates = [self.config.model, "openai/gpt-4o-mini"]
        unique: list[str] = []
        for model in candidates:
            value = str(model or "").strip()
            if value and value not in unique:
                unique.append(value)
        return unique

    def _build_agent(self, model: str):
        from agents import Agent

        return Agent(
            name="AutoHHKek OpenRouter Vacancy Reviewer",
            model=model,
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
            "vacancy_searchable_text": vacancy.searchable_text(),
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
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(Runner.run_sync, agent, prompt, run_config=run_config)
            try:
                return future.result(timeout=self.review_timeout_sec)
            except FutureTimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"OpenRouter review timed out after {self.review_timeout_sec:.0f}s") from exc

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
            explanation=output.explanation or "Вакансия оценена агентом OpenRouter.",
            reasons=reasons,
            recommended_action=recommended_action,
            ready_for_apply=category == FitCategory.FIT,
            review_strategy="openrouter_agent",
            review_notes=output.review_notes or f"Проверено моделью {self.last_model} через OpenRouter.",
        )

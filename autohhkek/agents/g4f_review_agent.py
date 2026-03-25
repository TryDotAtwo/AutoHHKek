from __future__ import annotations

import json
from typing import Any, Callable

from autohhkek.agents.openai_review_agent import VacancyReviewOutput, _coerce_category, _coerce_reason_group, _default_action
from autohhkek.domain.models import Anamnesis, AssessmentReason, UserPreferences, Vacancy, VacancyAssessment
from autohhkek.services.g4f_runtime import G4FAppConfig


RunnerFn = Callable[[list[dict[str, str]], G4FAppConfig], Any]


class G4FVacancyReviewer:
    def __init__(self, config: G4FAppConfig | None = None, runner: RunnerFn | None = None) -> None:
        self.config = config or G4FAppConfig.from_env()
        self.runner = runner or self._run_completion
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
        messages = self._build_messages(vacancy, preferences, anamnesis)
        try:
            output = self.runner(messages, self.config)
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = str(exc)
            return None

        if not isinstance(output, VacancyReviewOutput):
            output = VacancyReviewOutput.model_validate(output)
        self.last_status = "ok"
        self.last_error = ""
        return self._to_assessment(vacancy, output)

    def _build_messages(self, vacancy: Vacancy, preferences: UserPreferences, anamnesis: Anamnesis) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Ты оцениваешь вакансии hh.ru для одного кандидата. "
                    "Отвечай только JSON. "
                    "Пиши только на русском языке. "
                    "Верни category, subcategory, score, explanation, recommended_action, review_notes и reasons."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "vacancy": vacancy.to_dict(),
                        "preferences": preferences.to_dict(),
                        "anamnesis": anamnesis.to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]

    def _run_completion(self, messages: list[dict[str, str]], config: G4FAppConfig) -> VacancyReviewOutput:
        from g4f.client import Client

        client = Client()
        completion = client.chat.completions.create(
            model=config.model,
            provider=config.provider or None,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        return VacancyReviewOutput.model_validate_json(content)

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
        return VacancyAssessment(
            vacancy_id=vacancy.vacancy_id,
            category=category,
            subcategory=output.subcategory or "g4f_review",
            score=max(0.0, min(100.0, output.score)),
            explanation=output.explanation or "Вакансия оценена через g4f.",
            reasons=reasons,
            recommended_action=output.recommended_action or _default_action(category),
            ready_for_apply=category == _coerce_category("fit"),
            review_strategy="g4f_agent",
            review_notes=output.review_notes or f"Проверено через g4f ({self.config.model}).",
        )

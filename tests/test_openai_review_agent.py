from autohhkek.agents.openai_review_agent import OpenAIVacancyReviewer, VacancyReviewOutput
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.openai_runtime import OpenAIAppConfig


class _FakeResult:
    def __init__(self, output):
        self.final_output = output


def test_openai_reviewer_converts_structured_output_to_assessment():
    config = OpenAIAppConfig(api_key="sk-test", model="gpt-5.4")
    reviewer = OpenAIVacancyReviewer(
        config=config,
        runner=lambda agent, prompt, run_config=None: _FakeResult(
            VacancyReviewOutput(
                category="fit",
                subcategory="llm_match",
                score=87.5,
                explanation="Strong LLM match",
                recommended_action="Apply",
                review_notes="Reviewed by OpenAI agent",
                reasons=[
                    {
                        "code": "stack_match",
                        "label": "Stack match",
                        "group": "positive",
                        "detail": "Python and LLM are present",
                        "weight": 14,
                        "subcategory": "skill_overlap",
                    }
                ],
            )
        ),
    )
    prefs = UserPreferences(target_titles=["LLM Engineer"])
    anamnesis = Anamnesis(headline="LLM Engineer", summary="3 years in LLM products")
    vacancy = Vacancy(vacancy_id="v1", title="LLM Engineer", description="Python and LLM")

    assessment = reviewer.review(vacancy, prefs, anamnesis)

    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "openai_agent"
    assert assessment.review_notes == "Reviewed by OpenAI agent"
    assert assessment.reasons[0].label == "Stack match"


def test_openai_reviewer_returns_none_when_api_unavailable():
    config = OpenAIAppConfig(api_key="", model="gpt-5.4")
    reviewer = OpenAIVacancyReviewer(config=config)

    result = reviewer.review(
        Vacancy(vacancy_id="v1", title="LLM Engineer"),
        UserPreferences(),
        Anamnesis(),
    )

    assert result is None

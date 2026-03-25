from autohhkek.agents.openrouter_filter_agent import OpenRouterHHFilterAgent
from autohhkek.agents.openrouter_review_agent import OpenRouterVacancyReviewer
from autohhkek.agents.openai_filter_agent import FilterPlanningOutput
from autohhkek.agents.openai_review_agent import VacancyReviewOutput
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


class _FakeResult:
    def __init__(self, output):
        self.final_output = output


def test_openrouter_reviewer_converts_structured_output_to_assessment():
    reviewer = OpenRouterVacancyReviewer(
        config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        runner=lambda agent, prompt, run_config=None: _FakeResult(
            VacancyReviewOutput(
                category="fit",
                subcategory="openrouter_match",
                score=91,
                explanation="Strong match from OpenRouter",
                recommended_action="Apply",
                review_notes="Reviewed by OpenRouter agent",
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

    assessment = reviewer.review(
        Vacancy(vacancy_id="v1", title="LLM Engineer", description="Python and LLM"),
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
    )

    assert assessment is not None
    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "openrouter_agent"
    assert assessment.review_notes == "Reviewed by OpenRouter agent"
    assert assessment.reasons[0].label == "Stack match"


def test_openrouter_filter_agent_returns_structured_filter_intent():
    agent = OpenRouterHHFilterAgent(
        config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        runner=lambda agent, prompt, run_config=None: _FakeResult(
            FilterPlanningOutput(
                search_text="LLM Engineer OR Applied Scientist",
                area_code="1",
                remote_only=True,
                salary_min=350000,
                residual_rules=["Avoid academic employers"],
                rationale="Optimize for senior Moscow-based LLM roles",
            )
        ),
    )

    result = agent.plan(UserPreferences(target_titles=["LLM Engineer"]), Anamnesis(primary_skills=["Python", "LLM"]))

    assert result is not None
    assert result.search_text.startswith("LLM Engineer")
    assert result.area_code == "1"
    assert result.remote_only is True


def test_openrouter_reviewer_falls_back_to_working_model():
    calls = []

    def runner(agent, prompt, run_config=None):
        calls.append(run_config.model)
        if run_config.model == "openai/gpt-5-nano":
            raise RuntimeError("provider failed")
        return _FakeResult(
            VacancyReviewOutput(
                category="fit",
                subcategory="fallback_model",
                score=88,
                explanation="Fallback model worked",
                recommended_action="Apply",
                reasons=[
                    {
                        "code": "fallback_ok",
                        "label": "Fallback model succeeded",
                        "group": "positive",
                        "detail": "OpenRouter fallback returned a valid result",
                        "weight": 10,
                        "subcategory": "backend_fallback",
                    }
                ],
            )
        )

    reviewer = OpenRouterVacancyReviewer(
        config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-5-nano"),
        runner=runner,
    )

    assessment = reviewer.review(
        Vacancy(vacancy_id="v2", title="LLM Engineer", description="Python and LLM"),
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
    )

    assert assessment is not None
    assert calls == ["openai/gpt-5-nano", "openai/gpt-4o-mini"]
    assert assessment.review_notes == "Reviewed by openai/gpt-4o-mini via OpenRouter"

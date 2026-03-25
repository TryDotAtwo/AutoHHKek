from autohhkek.agents.g4f_review_agent import G4FVacancyReviewer
from autohhkek.agents.openai_review_agent import VacancyReviewOutput
from autohhkek.agents.openrouter_filter_agent import OpenRouterHHFilterAgent
from autohhkek.agents.openrouter_review_agent import OpenRouterVacancyReviewer
from autohhkek.agents.vacancy_review_agent import VacancyReviewAgent
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.filter_planner import HHFilterPlanner
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


def test_vacancy_review_agent_can_use_g4f_backend():
    reviewer = G4FVacancyReviewer(
        runner=lambda messages, config: VacancyReviewOutput(
            category="fit",
            subcategory="g4f_match",
            score=88,
            explanation="Strong match from g4f backend",
        )
    )

    assessment = VacancyReviewAgent(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
        llm_backend="g4f",
        g4f_reviewer=reviewer,
    ).review(Vacancy(vacancy_id="vac-1", title="LLM Engineer"))

    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "g4f_agent"


def test_vacancy_review_agent_can_use_openrouter_backend():
    reviewer = OpenRouterVacancyReviewer(
        config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        runner=lambda agent, prompt, run_config=None: type(
            "Result",
            (),
            {
                "final_output": VacancyReviewOutput(
                    category="fit",
                    subcategory="openrouter_match",
                    score=94,
                    explanation="Strong match from OpenRouter backend",
                    review_notes="OpenRouter review succeeded",
                )
            },
        )(),
    )

    assessment = VacancyReviewAgent(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
        llm_backend="openrouter",
        openrouter_reviewer=reviewer,
    ).review(Vacancy(vacancy_id="vac-1", title="LLM Engineer"))

    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "openrouter_agent"


def test_filter_planner_uses_openrouter_planner_when_selected():
    planner = HHFilterPlanner(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(primary_skills=["Python", "LLM"]),
        llm_backend="openrouter",
    )

    assert isinstance(planner.llm_planner, OpenRouterHHFilterAgent)

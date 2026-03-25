from autohhkek.agents.g4f_filter_agent import G4FHHFilterAgent
from autohhkek.agents.g4f_review_agent import G4FVacancyReviewer
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.g4f_runtime import G4FAppConfig


def test_g4f_reviewer_converts_json_result_to_assessment():
    reviewer = G4FVacancyReviewer(
        config=G4FAppConfig(model="gpt-4o-mini"),
        runner=lambda messages, model, response_schema=None: {
            "category": "fit",
            "subcategory": "g4f_match",
            "score": 84,
            "explanation": "Strong match",
            "review_notes": "Reviewed by g4f",
            "reasons": [
                {
                    "code": "stack_match",
                    "label": "Stack match",
                    "group": "positive",
                    "detail": "Python and LLM are present",
                    "weight": 10,
                    "subcategory": "skill_overlap",
                }
            ],
        },
    )

    assessment = reviewer.review(
        Vacancy(vacancy_id="vac-1", title="LLM Engineer", description="Python and LLM"),
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
    )

    assert assessment is not None
    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "g4f_agent"
    assert assessment.reasons[0].label == "Stack match"


def test_g4f_filter_agent_returns_structured_plan():
    planner = G4FHHFilterAgent(
        config=G4FAppConfig(model="gpt-4o-mini"),
        runner=lambda messages, model, response_schema=None: {
            "search_text": "LLM Engineer OR Applied Scientist",
            "area_code": "1",
            "remote_only": True,
            "salary_min": 350000,
            "residual_rules": ["Avoid outsourcing"],
            "rationale": "Prefer Moscow remote-friendly roles",
        },
    )

    result = planner.plan(
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(primary_skills=["Python", "LLM"]),
    )

    assert result is not None
    assert result.area_code == "1"
    assert result.remote_only is True

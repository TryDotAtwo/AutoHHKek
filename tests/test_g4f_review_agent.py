from autohhkek.agents.g4f_review_agent import G4FVacancyReviewer
from autohhkek.agents.openai_review_agent import VacancyReviewOutput
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.g4f_runtime import G4FAppConfig


def test_g4f_reviewer_converts_structured_output_to_assessment():
    reviewer = G4FVacancyReviewer(
        config=G4FAppConfig(model="gpt-4o-mini"),
        runner=lambda messages, config: VacancyReviewOutput(
            category="fit",
            subcategory="g4f_match",
            score=91,
            explanation="Strong match from g4f",
            review_notes="Reviewed by g4f",
        ),
    )

    assessment = reviewer.review(
        Vacancy(vacancy_id="v1", title="LLM Engineer"),
        UserPreferences(target_titles=["LLM Engineer"]),
        Anamnesis(headline="LLM Engineer"),
    )

    assert assessment is not None
    assert assessment.category == FitCategory.FIT
    assert assessment.review_strategy == "g4f_agent"
    assert assessment.review_notes == "Reviewed by g4f"

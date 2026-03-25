from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy
from autohhkek.services.analysis import VacancyRuleEngine


def test_analysis_marks_blacklisted_company_as_no_fit():
    prefs = UserPreferences(
        target_titles=["LLM Engineer"],
        excluded_companies=["университет"],
        required_skills=["Python", "LLM"],
    )
    anamnesis = Anamnesis(headline="LLM Engineer", primary_skills=["Python", "LLM", "NLP"], experience_years=3)
    vacancy = Vacancy(vacancy_id="1", title="LLM Engineer", company="Университет Иннополис", description="Работа с LLM")

    assessment = VacancyRuleEngine(prefs, anamnesis).assess(vacancy)

    assert assessment.category == FitCategory.NO_FIT
    assert assessment.subcategory == "blacklisted_employer"


def test_analysis_marks_strong_match_as_fit():
    prefs = UserPreferences(
        target_titles=["LLM Engineer"],
        required_skills=["Python", "LLM"],
        preferred_skills=["NLP", "RAG"],
        preferred_locations=["Москва"],
        salary_min=250000,
    )
    anamnesis = Anamnesis(headline="LLM Engineer", primary_skills=["Python", "LLM", "NLP", "RAG"], experience_years=4)
    vacancy = Vacancy(
        vacancy_id="2",
        title="Senior LLM Engineer",
        company="AI Startup",
        location="Москва",
        salary_text="300 000 RUB",
        description="Python, LLM, NLP, RAG, production agents.",
    )

    assessment = VacancyRuleEngine(prefs, anamnesis).assess(vacancy)

    assert assessment.category == FitCategory.FIT
    assert assessment.score >= 72


def test_analysis_marks_partial_match_as_doubt():
    prefs = UserPreferences(
        target_titles=["ML Engineer"],
        required_skills=["Python", "SQL", "LLM"],
        preferred_locations=["Москва"],
        salary_min=200000,
    )
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python", "SQL"], experience_years=2)
    vacancy = Vacancy(
        vacancy_id="3",
        title="ML Engineer",
        company="Product Team",
        location="Санкт-Петербург",
        description="Python, SQL. Предусмотрен тест и анкета.",
    )

    assessment = VacancyRuleEngine(prefs, anamnesis).assess(vacancy)

    assert assessment.category == FitCategory.DOUBT

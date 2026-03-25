from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, UserPreferences, Vacancy, VacancyAssessment
from autohhkek.services.storage import WorkspaceStore


def test_storage_roundtrip(tmp_path):
    store = WorkspaceStore(tmp_path)
    prefs = UserPreferences(full_name="Ivan", target_titles=["ML Engineer"])
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python"])
    vacancy = Vacancy(vacancy_id="vac-1", title="ML Engineer", url="https://hh.ru/vacancy/1")
    assessment = VacancyAssessment(
        vacancy_id="vac-1",
        category=FitCategory.FIT,
        subcategory="role_fit",
        score=88,
        explanation="looks good",
    )

    store.save_preferences(prefs)
    store.save_anamnesis(anamnesis)
    store.save_vacancies([vacancy])
    store.save_assessments([assessment])

    assert store.load_preferences().full_name == "Ivan"
    assert store.load_anamnesis().headline == "ML Engineer"
    assert store.load_vacancies()[0].url.endswith("/1")
    assert store.load_assessments()[0].category == FitCategory.FIT

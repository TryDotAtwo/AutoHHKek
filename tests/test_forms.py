from autohhkek.domain.enums import QuestionKind, ScreeningPlatform
from autohhkek.domain.models import Anamnesis, QuestionField, UserPreferences, Vacancy
from autohhkek.integrations.hh.forms import answer_question, detect_screening_platform


def test_detect_screening_platform():
    assert detect_screening_platform("https://docs.google.com/forms/d/e/example/viewform") == ScreeningPlatform.GOOGLE_FORMS
    assert detect_screening_platform("https://forms.yandex.ru/u/example/") == ScreeningPlatform.YANDEX_FORMS
    assert detect_screening_platform("https://hh.ru/vacancy/123") == ScreeningPlatform.HH


def test_answer_question_uses_preferences_and_profile():
    prefs = UserPreferences(salary_min=250000, allow_relocation=False)
    anamnesis = Anamnesis(summary="3 years with Python and LLMs", primary_skills=["Python", "LLM"], experience_years=3)
    vacancy = Vacancy(vacancy_id="1", title="LLM Engineer")
    question = QuestionField(label="Ожидаемая зарплата", kind=QuestionKind.NUMBER)

    assert answer_question(question, anamnesis, prefs, vacancy) == "250000"

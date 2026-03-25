from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.rules import build_selection_rules_markdown, split_rules_markdown


def test_selection_rules_separate_system_and_user_sections():
    markdown = build_selection_rules_markdown(
        UserPreferences(target_titles=["LLM Engineer"], notes="Не хочу госуху и университеты."),
        Anamnesis(headline="Research Engineer", summary="LLM, NLP, Python"),
    )

    parts = split_rules_markdown(markdown)

    assert "Общие системные правила" in parts["system"]
    assert "только русский язык" in parts["system"]
    assert "Правила пользователя" in parts["user"]
    assert "Не хочу госуху" in parts["user"]

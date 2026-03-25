from pathlib import Path

from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.rule_loader import apply_rule_bundle, load_rule_bundle


def test_load_rule_bundle_supports_heading_sections(tmp_path):
    rules_path = tmp_path / "rules.md"
    rules_path.write_text(
        """
# User rules

## target_titles
- Applied Scientist
- LLM Engineer

## required_skills
- Python
- Transformers

salary_min: 320000
remote_only: true
cover_letter_mode: never
""".strip(),
        encoding="utf-8",
    )

    bundle = load_rule_bundle(rules_path)

    assert bundle.preferences_patch["target_titles"] == ["Applied Scientist", "LLM Engineer"]
    assert bundle.preferences_patch["required_skills"] == ["Python", "Transformers"]
    assert bundle.preferences_patch["salary_min"] == 320000
    assert bundle.preferences_patch["remote_only"] is True
    assert bundle.preferences_patch["cover_letter_mode"] == "never"


def test_apply_rule_bundle_merges_preferences_and_anamnesis(tmp_path):
    rules_path = tmp_path / "rules.md"
    rules_path.write_text(
        """
target_titles: LLM Engineer, AI Engineer
preferred_locations: Москва, Санкт-Петербург
forbidden_keywords: университет, институт
summary: Focus on production LLM roles only.
""".strip(),
        encoding="utf-8",
    )

    preferences = UserPreferences(target_titles=["Data Scientist"], preferred_locations=["Казань"])
    anamnesis = Anamnesis(headline="ML Engineer", summary="Base summary")

    updated_preferences, updated_anamnesis, merged_markdown = apply_rule_bundle(
        preferences,
        anamnesis,
        load_rule_bundle(rules_path),
        current_rules_markdown="# Existing\n",
    )

    assert updated_preferences.target_titles == ["Data Scientist", "LLM Engineer", "AI Engineer"]
    assert updated_preferences.preferred_locations == ["Казань", "Москва", "Санкт-Петербург"]
    assert updated_preferences.forbidden_keywords == ["университет", "институт"]
    assert updated_anamnesis.summary == "Focus on production LLM roles only."
    assert "Imported user rules" in merged_markdown

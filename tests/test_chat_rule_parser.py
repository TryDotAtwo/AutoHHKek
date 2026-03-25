from autohhkek.services.chat_rule_parser import parse_rule_request, patch_to_markdown


def test_parse_rule_request_handles_natural_language_preferences():
    patch = parse_rule_request("Не хочу финтех и офис в Москве, только remote, зарплата от 350000, ищу LLM Engineer")

    assert patch["remote_only"] is True
    assert patch["salary_min"] == 350000
    assert "LLM Engineer" in patch["target_titles"]
    assert "финтех" in patch["forbidden_keywords"][0].lower()


def test_patch_to_markdown_serializes_patch():
    markdown = patch_to_markdown({"remote_only": True, "salary_min": 300000, "target_titles": ["LLM Engineer", "ML Engineer"]})

    assert "remote_only: true" in markdown
    assert "salary_min: 300000" in markdown
    assert "target_titles: LLM Engineer, ML Engineer" in markdown

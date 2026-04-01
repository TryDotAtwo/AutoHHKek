from autohhkek.domain.models import Anamnesis, UserPreferences
from autohhkek.services.filter_planner import HHFilterPlanner


def test_filter_planner_builds_query_params_and_ui_actions():
    preferences = UserPreferences(
        target_titles=["LLM Engineer", "Applied Scientist"],
        preferred_locations=["Москва"],
        salary_min=300000,
        remote_only=True,
        excluded_companies=["университет"],
        forbidden_keywords=["государственный"],
    )
    anamnesis = Anamnesis(primary_skills=["Python", "LLM", "NLP"])

    plan = HHFilterPlanner(preferences, anamnesis, selected_resume_id="resume-123").build()

    assert plan["search_text"] == ""
    assert "text" not in plan["query_params"]
    assert "salary_from" not in plan["query_params"]
    assert plan["query_params"]["remote_work"] == "1"
    assert "area" not in plan["query_params"]
    assert plan["resume_id"] == "resume-123"
    assert "resume=resume-123" in plan["search_url"]
    assert "work_format=REMOTE" in plan["search_url"]
    assert not any(step["action"] == "set_search_text" for step in plan["ui_actions"])
    assert any(step["action"] == "set_remote_filter" for step in plan["ui_actions"])
    assert "университет" in " ".join(plan["residual_rules"])
    assert any("Salary preference" in rule for rule in plan["residual_rules"])
    rounds = plan["search_rounds"]
    assert isinstance(rounds, list)
    assert rounds[0]["id"] == "primary_broad"
    assert rounds[0]["persist_serp_cache"] is True
    assert any(r.get("id", "").startswith("followup_kw_") for r in rounds[1:])


def test_filter_planner_falls_back_to_skill_query_when_titles_absent():
    preferences = UserPreferences(target_titles=[], preferred_locations=[], remote_only=False)
    anamnesis = Anamnesis(primary_skills=["Python", "NLP"])

    plan = HHFilterPlanner(preferences, anamnesis).build()

    assert plan["search_text"] == "Python NLP"
    assert plan["query_params"]["text"] == "Python NLP"
    assert len(plan["search_rounds"]) == 1


def test_filter_planner_prefers_resume_first_search_when_resume_selected_and_no_explicit_fields():
    preferences = UserPreferences(target_titles=[], preferred_locations=[], remote_only=True, notes="remote only")
    anamnesis = Anamnesis(primary_skills=[])

    plan = HHFilterPlanner(preferences, anamnesis, selected_resume_id="resume-123").build()

    assert plan["search_text"] == ""
    assert "text" not in plan["query_params"]
    assert "resume=resume-123" in plan["search_url"]
    assert "work_format=REMOTE" in plan["search_url"]
    assert not any(step["action"] == "set_search_text" for step in plan["ui_actions"])
    assert any("Resume-first search enabled" in note for note in plan["planning_notes"])

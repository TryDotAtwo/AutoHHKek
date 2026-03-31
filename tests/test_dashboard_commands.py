import pytest

from autohhkek.app.commands import (
    _payload_from_open_intake,
    confirm_intake_rules,
    import_rules_text,
    run_analyze,
    run_apply_submit,
    run_intake,
    run_intake_from_text,
    run_plan_filters,
    run_plan_apply,
    run_plan_repair,
    run_resume,
    run_selected_mode,
    select_resume_for_search,
    update_runtime_settings,
)
from autohhkek.services.storage import WorkspaceStore


def test_update_runtime_settings_persists_backend_choice(tmp_path):
    store = WorkspaceStore(tmp_path)

    result = update_runtime_settings(store, {"llm_backend": "g4f", "dashboard_mode": "analyze"})

    assert result["llm_backend"] == "g4f"
    assert result["mode_selected"] is True
    assert store.load_runtime_settings().llm_backend == "g4f"
    assert store.load_runtime_settings().mode_selected is True


def test_run_plan_repair_saves_repair_task(tmp_path):
    store = WorkspaceStore(tmp_path)

    result = run_plan_repair(
        store,
        action="click_apply_button",
        payload={"vacancy_id": "vac-1"},
        error="selector_mismatch",
        run_agent=False,
    )

    assert result["action"] == "repair"
    assert store.load_repair_tasks()[0]["action"] == "click_apply_button"


def test_run_apply_submit_retries_after_hh_relogin_and_enqueues_repair(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    store.save_selected_resume_id("resume-1")

    responses = iter(
        [
            {
                "vacancy_id": "vac-1",
                "result": {"status": "needs_login", "message": "login expired"},
            },
            {
                "vacancy_id": "vac-1",
                "result": {"status": "needs_repair", "message": "selector mismatch"},
            },
        ]
    )
    monkeypatch.setattr("autohhkek.app.commands.apply_to_vacancy", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        "autohhkek.app.commands.run_hh_login",
        lambda project_root: {"status": "completed", "message": "relogged"},
    )

    repair_calls = []

    def fake_run_plan_repair(store, *, action, payload, error, run_agent=False):
        repair_calls.append({"action": action, "payload": payload, "error": error, "run_agent": run_agent})
        return {"action": "repair", "payload": {"status": "queued"}}

    monkeypatch.setattr("autohhkek.app.commands.run_plan_repair", fake_run_plan_repair)
    monkeypatch.setattr("autohhkek.app.commands._bump_apply_counter", lambda store, count: None)

    result = run_apply_submit(store, vacancy_id="vac-1", cover_letter="hello")

    assert result["payload"]["result"]["status"] == "needs_repair"
    assert result["relogin_result"]["status"] == "completed"
    assert result["repair"]["action"] == "repair"
    assert repair_calls == [
        {
            "action": "apply_submit",
            "payload": {"vacancy_id": "vac-1", "result": {"status": "needs_repair", "message": "selector mismatch"}},
            "error": "needs_repair",
            "run_agent": True,
        }
    ]


def test_run_intake_creates_profile_data(tmp_path):
    store = WorkspaceStore(tmp_path)
    (tmp_path / "resume_cache.json").write_text(
        '{"title":"ML Engineer","summary":"Python and LLM","skills":["Python","LLM"]}',
        encoding="utf-8",
    )

    result = run_intake(store, interactive=False)

    assert result["action"] == "intake"
    assert store.load_preferences() is not None
    assert store.load_anamnesis() is not None


def test_run_intake_accepts_dashboard_payload_and_builds_rules(tmp_path):
    store = WorkspaceStore(tmp_path)

    result = run_intake(
        store,
        payload={
            "full_name": "Ivan Litvak",
            "headline": "LLM Engineer",
            "summary": "Python, NLP, agents",
            "experience_years": 5,
            "target_titles": ["LLM Engineer", "ML Engineer"],
            "primary_skills": ["Python", "LLM", "NLP"],
            "required_skills": ["Python"],
            "preferred_locations": ["Moscow"],
            "salary_min": 350000,
            "remote_only": True,
            "cover_letter_mode": "adaptive",
        },
    )

    assert result["action"] == "intake"
    assert store.load_preferences().target_titles == ["LLM Engineer", "ML Engineer"]
    assert store.load_anamnesis().headline == "LLM Engineer"
    assert "LLM Engineer" in store.load_selection_rules()


def test_run_resume_builds_resume_draft(tmp_path):
    store = WorkspaceStore(tmp_path)
    (tmp_path / "resume_cache.json").write_text(
        '{"title":"ML Engineer","summary":"Python and LLM","skills":["Python","LLM"]}',
        encoding="utf-8",
    )
    run_intake(store, interactive=False)

    result = run_resume(store)

    assert result["action"] == "resume"
    assert "markdown" in result
    assert result["rules_ready"] is True
    assert "анализ" in result["message"].lower()
    assert store.load_resume_draft_markdown()
    assert store.load_selection_rules().strip()


def test_run_plan_filters_builds_and_persists_filter_plan(tmp_path):
    store = WorkspaceStore(tmp_path)
    run_intake(
        store,
        payload={
            "full_name": "Ivan Litvak",
            "headline": "LLM Engineer",
            "summary": "Python, NLP, agents",
            "experience_years": 5,
            "target_titles": ["LLM Engineer", "ML Engineer"],
            "primary_skills": ["Python", "LLM"],
            "required_skills": ["Python"],
            "preferred_locations": ["Moscow"],
            "salary_min": 350000,
            "remote_only": True,
        },
    )

    result = run_plan_filters(store)

    assert result["action"] == "plan_filters"
    assert result["payload"]["search_text"] == "LLM Engineer OR ML Engineer"
    assert store.load_filter_plan()["search_text"] == "LLM Engineer OR ML Engineer"


def test_select_resume_for_search_marks_analysis_stale(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.save_analysis_state({"stale": False, "stale_reason": ""})

    result = select_resume_for_search(store, resume_id="resume-42")

    assert result["selected_resume_id"] == "resume-42"
    assert result["analysis_stale"] is True
    assert store.load_selected_resume_id() == "resume-42"
    assert store.load_analysis_state()["stale"] is True


def test_run_analyze_reports_vacancy_source_in_message(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    (tmp_path / "vacancies_cache.json").write_text(
        '[{"title":"LLM Engineer","url":"https://example.com/v1"}]',
        encoding="utf-8",
    )
    run_intake(
        store,
        payload={
            "full_name": "Ivan Litvak",
            "headline": "LLM Engineer",
            "summary": "Python, NLP, agents",
            "experience_years": 5,
            "target_titles": ["LLM Engineer"],
            "primary_skills": ["Python", "LLM"],
            "required_skills": ["Python"],
            "preferred_locations": ["Moscow"],
        },
    )
    monkeypatch.setattr(
        "autohhkek.app.commands.ensure_hh_context",
        lambda store, auto_login=True: {"status": "ready", "message": "hh context ready", "selected_resume_id": "resume-1"},
    )

    result = run_analyze(store, limit=10, interactive=False)

    assert result["status"] == "completed"
    assert result["rules_synced"] is True
    assert "Source:" in result["message"]
    assert result["refresh_result"]["status"] in {"seeded_cache", "updated", "empty", "skipped", "failed"}


def test_import_rules_text_saves_rule_file_and_updates_preferences(tmp_path):
    store = WorkspaceStore(tmp_path)
    (tmp_path / "resume_cache.json").write_text(
        '{"title":"ML Engineer","summary":"Python and LLM","skills":["Python","LLM"]}',
        encoding="utf-8",
    )
    run_intake(store, interactive=False)

    result = import_rules_text(
        store,
        filename="dashboard_rules.md",
        markdown="target_titles: LLM Engineer\nrequired_skills: Python, Transformers\n",
    )

    assert result["action"] == "import_rules"
    assert store.load_imported_rules()[0]["name"] == "dashboard_rules.md"
    assert "LLM Engineer" in store.load_selection_rules()


def test_run_selected_mode_requires_explicit_mode_selection(tmp_path):
    store = WorkspaceStore(tmp_path)

    with pytest.raises(RuntimeError, match="mode"):
        run_selected_mode(store)


def test_run_analyze_requires_completed_onboarding(tmp_path):
    store = WorkspaceStore(tmp_path)

    with pytest.raises(RuntimeError, match="intake"):
        run_analyze(store, limit=10, interactive=False)


def test_run_analyze_blocks_when_hh_context_is_not_ready(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    run_intake(
        store,
        payload={
            "full_name": "Ivan Litvak",
            "headline": "LLM Engineer",
            "summary": "Python, NLP, agents",
            "experience_years": 5,
            "target_titles": ["LLM Engineer"],
            "primary_skills": ["Python", "LLM"],
            "required_skills": ["Python"],
            "preferred_locations": ["Moscow"],
        },
    )
    monkeypatch.setattr(
        "autohhkek.app.commands.ensure_hh_context",
        lambda store, auto_login=True: {"status": "needs_resume_selection", "message": "Select resume first."},
    )

    result = run_analyze(store, limit=10, interactive=False)

    assert result["status"] == "blocked"
    assert result["message"] == "Select resume first."


def test_run_plan_apply_requires_completed_onboarding(tmp_path):
    store = WorkspaceStore(tmp_path)

    with pytest.raises(RuntimeError, match="intake"):
        run_plan_apply(store)


def test_run_resume_requires_intake(tmp_path):
    store = WorkspaceStore(tmp_path)

    with pytest.raises(RuntimeError, match="intake"):
        run_resume(store)


def test_run_intake_from_text_extracts_core_user_constraints(tmp_path):
    store = WorkspaceStore(tmp_path)

    result = run_intake_from_text(
        store,
        raw_text=(
            "Ищу роли LLM Engineer, NLP Engineer, Research Scientist. "
            "Только remote, без переезда. "
            "Зарплата от 350000. "
            "Не хочу госуху, университеты и CV only. "
            "Must-have: Python, NLP, LLM, Transformers. "
            "Интересны AI infra и applied research."
        ),
    )

    prefs = store.load_preferences()
    anamnesis = store.load_anamnesis()

    assert result["action"] == "intake"
    assert prefs.remote_only is True
    assert prefs.salary_min == 350000
    assert "LLM Engineer" in prefs.target_titles
    assert "Python" in prefs.required_skills
    assert any("гос" in item.lower() for item in prefs.forbidden_keywords)
    assert any("Applied research" == item or "AI infra" == item for item in anamnesis.industries)


def test_confirm_intake_rules_is_required_for_readiness(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)

    run_intake(
        store,
        payload={
            "full_name": "Ivan Litvak",
            "headline": "LLM Engineer",
            "summary": "Python, NLP, agents",
            "experience_years": 5,
            "target_titles": ["LLM Engineer"],
            "primary_skills": ["Python", "LLM"],
            "required_skills": ["Python"],
            "preferred_locations": ["Remote"],
            "remote_only": True,
            "excluded_companies": ["госуха"],
            "forbidden_keywords": ["университет"],
        },
    )
    store.update_dashboard_state({"intake_dialog_completed": True, "intake_confirmed": False})

    with pytest.raises(RuntimeError, match="intake"):
        run_analyze(store, limit=10, interactive=False)

    confirm_intake_rules(store)
    store.save_selection_rules("target_titles: LLM Engineer")

    monkeypatch.setattr(
        "autohhkek.app.commands.ensure_hh_context",
        lambda store, auto_login=True: {"status": "ready", "message": "hh context ready", "selected_resume_id": "resume-1"},
    )
    result = run_analyze(store, limit=1, interactive=False)
    assert result["status"] in {"completed", "blocked"}

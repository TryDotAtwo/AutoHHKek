from autohhkek.dashboard import snapshot as snapshot_module
from autohhkek.dashboard.snapshot import build_dashboard_snapshot
from autohhkek.domain.enums import FitCategory
from autohhkek.domain.models import Anamnesis, RunSummary, UserPreferences, Vacancy, VacancyAssessment
from autohhkek.services.rules import build_selection_rules_markdown
from autohhkek.services.storage import WorkspaceStore


class _ReadyRuntime:
    def __init__(self, project_root):
        self.project_root = project_root

    def describe_capabilities(self):
        return {
            "selected_llm_backend": "openai",
            "selected_llm_backend_ready": True,
            "openai_ready": True,
            "playwright_mcp_ready": True,
            "llm_backends": {
                "openai": {"ready": True, "model": "gpt-5.4", "supports_mcp_repair": True},
                "g4f": {"ready": True, "model": "gpt-4o-mini", "provider": "", "supports_mcp_repair": False},
            },
        }

    def backend_status(self):
        return "ready"


def test_dashboard_snapshot_groups_vacancies_and_builds_session_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "HHAutomationRuntime", _ReadyRuntime)
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings({"dashboard_mode": "analyze", "mode_selected": True})
    prefs = UserPreferences(full_name="Ivan", target_titles=["ML Engineer"])
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python"])
    vacancy = Vacancy(vacancy_id="vac-1", title="ML Engineer", company="Demo")
    assessment = VacancyAssessment(
        vacancy_id="vac-1",
        category=FitCategory.FIT,
        subcategory="role_fit",
        score=91,
        explanation="Подходит",
    )
    store.save_preferences(prefs)
    store.save_anamnesis(anamnesis)
    store.save_selection_rules(build_selection_rules_markdown(prefs, anamnesis))
    store.save_vacancies([vacancy])
    store.save_assessments([assessment])
    store.save_run(
        RunSummary(
            run_id="analyze-1",
            mode="analyze",
            status="completed",
            processed=1,
            counts={"fit": 1},
        )
    )
    store.save_repair_task(
        {
            "action": "click_apply_button",
            "status": "prepared",
            "repair_patch_path": "x.diff",
            "repair_test_path": "y.py",
        }
    )

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["counts"]["fit"] == 1
    assert snapshot["columns"]["fit"][0]["title"] == "ML Engineer"
    assert snapshot["intake"]["ready"] is True
    assert "intake" in snapshot["action_catalog"]["actions"]
    assert snapshot["ready_state"] == "repair_attention"
    assert snapshot["blocking_issues"]
    assert snapshot["next_recommended_action"]["id"] == "repair_queue"
    assert snapshot["repair_queue_count"] == 1
    assert snapshot["last_run_summary"]["run_id"] == "analyze-1"
    assert snapshot["last_run_summary"]["run_id"] == "analyze-1"
    assert snapshot["setup_summary"]["state"] == "needs_attention"
    assert snapshot["setup_summary"]["repair_queue_count"] == 1


def test_dashboard_snapshot_requires_mode_selection_before_intake(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "HHAutomationRuntime", _ReadyRuntime)

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["ready_state"] == "needs_mode"
    assert snapshot["next_recommended_action"]["id"] == "choose_mode"
    assert any("mode" in issue.lower() for issue in snapshot["blocking_issues"])
    assert snapshot["setup_summary"]["intake_ready"] is False
    assert snapshot["setup_summary"]["mode_selected"] is False
    assert snapshot["repair_queue_count"] == 0


def test_dashboard_snapshot_reports_missing_rules_after_intake(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "HHAutomationRuntime", _ReadyRuntime)
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings({"dashboard_mode": "analyze", "mode_selected": True})
    prefs = UserPreferences(full_name="Ivan", target_titles=["ML Engineer"])
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python"])
    store.save_preferences(prefs)
    store.save_anamnesis(anamnesis)

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["ready_state"] == "needs_rules"
    assert snapshot["next_recommended_action"]["id"] == "build_rules"
    assert any("rules" in issue.lower() for issue in snapshot["blocking_issues"])


def test_dashboard_snapshot_counts_full_assessment_pool_even_when_display_is_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "HHAutomationRuntime", _ReadyRuntime)
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings({"dashboard_mode": "analyze", "mode_selected": True})
    prefs = UserPreferences(full_name="Ivan", target_titles=["ML Engineer"])
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python"])
    store.save_preferences(prefs)
    store.save_anamnesis(anamnesis)
    store.save_selection_rules(build_selection_rules_markdown(prefs, anamnesis))
    store.save_vacancies(
        [
            Vacancy(vacancy_id="vac-1", title="ML Engineer", company="Demo"),
            Vacancy(vacancy_id="vac-2", title="Data Scientist", company="Demo"),
        ]
    )
    store.save_assessments(
        [
            VacancyAssessment(vacancy_id="vac-1", category=FitCategory.FIT, subcategory="fit", score=95, explanation="fit"),
            VacancyAssessment(vacancy_id="vac-2", category=FitCategory.NO_FIT, subcategory="no_fit", score=20, explanation="no fit"),
        ]
    )

    snapshot = build_dashboard_snapshot(tmp_path, limit=1)

    assert snapshot["counts"]["assessed"] == 2
    assert snapshot["counts"]["fit"] == 1
    assert snapshot["counts"]["no_fit"] == 1
    assert len(snapshot["columns"]["fit"]) == 1


def test_dashboard_snapshot_ignores_completed_repairs_when_calculating_queue_attention(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_module, "HHAutomationRuntime", _ReadyRuntime)
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings({"dashboard_mode": "analyze", "mode_selected": True})
    prefs = UserPreferences(full_name="Ivan", target_titles=["ML Engineer"])
    anamnesis = Anamnesis(headline="ML Engineer", primary_skills=["Python"])
    store.save_preferences(prefs)
    store.save_anamnesis(anamnesis)
    store.save_selection_rules(build_selection_rules_markdown(prefs, anamnesis))
    store.save_repair_task({"action": "click_apply_button", "status": "completed"})

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["repair_queue_count"] == 0
    assert snapshot["ready_state"] != "repair_attention"

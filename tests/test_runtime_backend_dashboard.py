from autohhkek.dashboard.snapshot import build_dashboard_snapshot
from autohhkek.domain.models import RuntimeSettings
from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.storage import WorkspaceStore


def test_runtime_uses_stored_g4f_backend(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings(RuntimeSettings(llm_backend="g4f", dashboard_mode="analyze"))

    runtime = HHAutomationRuntime(project_root=tmp_path)
    capabilities = runtime.describe_capabilities()

    assert capabilities["selected_llm_backend"] == "g4f"


def test_runtime_uses_stored_openrouter_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings(
        RuntimeSettings(
            llm_backend="openrouter",
            dashboard_mode="analyze",
            openrouter_model="openai/gpt-5-nano",
        )
    )

    runtime = HHAutomationRuntime(project_root=tmp_path)
    capabilities = runtime.describe_capabilities()

    assert capabilities["selected_llm_backend"] == "openrouter"
    assert capabilities["openrouter_ready"] is True
    assert capabilities["openrouter_model"] == "openai/gpt-5-nano"


def test_dashboard_snapshot_exposes_runtime_settings_and_repair_tasks(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings(RuntimeSettings(llm_backend="g4f", dashboard_mode="analyze"))
    store.save_repair_task(
        {
            "action": "click_apply_button",
            "status": "prepared",
            "repair_patch_path": "x.diff",
            "repair_test_path": "y.py",
        }
    )

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["runtime_settings"]["llm_backend"] == "g4f"
    assert snapshot["repair_tasks"][0]["action"] == "click_apply_button"


def test_dashboard_snapshot_exposes_openrouter_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    store = WorkspaceStore(tmp_path)
    store.save_runtime_settings(
        RuntimeSettings(
            llm_backend="openrouter",
            dashboard_mode="analyze",
            openrouter_model="openai/gpt-5-nano",
        )
    )

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["runtime_settings"]["llm_backend"] == "openrouter"
    assert snapshot["runtime_settings"]["openrouter_model"] == "openai/gpt-5-nano"
    assert snapshot["capability_summary"]["selected_backend"] == "openrouter"
    assert snapshot["capability_summary"]["openrouter_ready"] is True

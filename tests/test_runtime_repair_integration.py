from pathlib import Path

from autohhkek.integrations.hh.runtime import HHAutomationRuntime


def test_runtime_builds_repair_plan_for_failed_script(tmp_path: Path):
    runtime = HHAutomationRuntime(project_root=tmp_path)

    plan = runtime.build_repair_plan("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert plan["action"] == "click_apply_button"
    assert plan["error"] == "selector mismatch"
    assert plan["repair_patch_path"].endswith("click_apply_button_patch.diff")


def test_runtime_capabilities_report_repair_worker(tmp_path: Path):
    runtime = HHAutomationRuntime(project_root=tmp_path)

    capabilities = runtime.describe_capabilities()

    assert "repair-worker" in capabilities["supports"]

from autohhkek.integrations.hh.runtime import HHAutomationRuntime
from autohhkek.services.storage import WorkspaceStore


def test_runtime_stores_repair_task_when_script_falls_back(tmp_path):
    runtime = HHAutomationRuntime(project_root=tmp_path)

    result = runtime.plan_script_action("unknown_action", {"goal": "repair me"})

    store = WorkspaceStore(tmp_path)
    repair_tasks = store.load_repair_tasks()

    assert result["strategy"] == "agent_fallback"
    assert repair_tasks
    assert repair_tasks[0]["action"] == "unknown_action"


def test_repair_tasks_are_upserted_by_action_payload_and_error(tmp_path):
    store = WorkspaceStore(tmp_path)

    first = {
        "action": "click_apply_button",
        "payload": {"vacancy_id": "vac-1"},
        "error": "selector_mismatch",
        "status": "prepared",
    }
    second = {
        "action": "click_apply_button",
        "payload": {"vacancy_id": "vac-1"},
        "error": "selector_mismatch",
        "status": "completed",
        "repair_patch_path": "patch.diff",
    }

    store.save_repair_task(first)
    store.save_repair_task(second)

    tasks = store.load_repair_tasks()

    assert len(tasks) == 1
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["repair_patch_path"] == "patch.diff"

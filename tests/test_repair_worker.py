from pathlib import Path

import pytest

from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig
from autohhkek.integrations.hh.repair_worker import PlaywrightRepairOutput, PlaywrightRepairWorker
from autohhkek.services.g4f_runtime import G4FAppConfig
from autohhkek.services.openai_runtime import OpenAIAppConfig
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


class _FakeResult:
    def __init__(self, output):
        self.final_output = output


def test_repair_worker_prepares_task_with_patch_and_test_targets(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        openai_config=OpenAIAppConfig(api_key="sk-test", model="gpt-5.4"),
        bridge=PlaywrightMCPBridge(
            PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"]),
        ),
    )

    task = worker.prepare_task("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert task["action"] == "click_apply_button"
    assert task["error"] == "selector mismatch"
    assert task["repair_patch_path"].endswith("click_apply_button_patch.diff")
    assert task["repair_test_path"].endswith("test_click_apply_button_repair.py")
    assert "click_apply_button" in task["prompt"]


def test_repair_worker_returns_structured_repair_plan_from_runner(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        openai_config=OpenAIAppConfig(api_key="sk-test", model="gpt-5.4"),
        bridge=PlaywrightMCPBridge(
            PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"]),
        ),
        runner=lambda agent, prompt, run_config=None: _FakeResult(
            PlaywrightRepairOutput(
                diagnosis="Selector changed from button to anchor",
                patch_summary="Update click_apply_button selector candidates",
                patch_text="*** Begin Patch\n*** End Patch\n",
                test_text="def test_click_apply_button_repair():\n    assert True\n",
                selector_notes=["Prefer vacancy-response-link-top"],
            ),
        ),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "ready"
    assert result["action"] == "click_apply_button"
    assert result["output"]["patch_summary"] == "Update click_apply_button selector candidates"
    assert "Selector changed" in result["output"]["diagnosis"]
    assert result["mcp_ready"] is True


def test_repair_worker_reports_unavailable_without_openai_or_mcp(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        openai_config=OpenAIAppConfig(api_key="", model="gpt-5.4"),
        openrouter_config=OpenRouterAppConfig(api_key="", model="openai/gpt-4o-mini"),
        g4f_config=G4FAppConfig(model="gpt-4o-mini", provider=""),
        bridge=PlaywrightMCPBridge(PlaywrightMCPConfig(command="", args=[])),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "unavailable"
    assert result["mcp_ready"] is False
    assert result["openai_ready"] is False


def test_repair_worker_run_sync_connects_and_cleans_mcp_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        openai_config=OpenAIAppConfig(api_key="sk-test", model="gpt-5.4"),
        bridge=PlaywrightMCPBridge(
            PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"]),
        ),
    )

    events: list[str] = []

    class _FakeServer:
        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("exit")

    class _FakeAgent:
        def __init__(self):
            self.mcp_servers = [_FakeServer()]

    async def _fake_run(agent, prompt, run_config=None):
        events.append("run")
        return _FakeResult(
            PlaywrightRepairOutput(
                diagnosis="Connected to MCP",
                patch_summary="ok",
                patch_text="*** Begin Patch\n*** End Patch\n",
                test_text="def test_repair():\n    assert True\n",
            ),
        )

    monkeypatch.setattr("agents.Runner.run", _fake_run)

    result = worker._run_sync(_FakeAgent(), "repair prompt")

    assert isinstance(result, _FakeResult)
    assert events == ["enter", "run", "exit"]

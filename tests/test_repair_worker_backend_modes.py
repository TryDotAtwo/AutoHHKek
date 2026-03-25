from pathlib import Path

from autohhkek.domain.models import RuntimeSettings
from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig
from autohhkek.integrations.hh.repair_worker import PlaywrightRepairOutput, PlaywrightRepairWorker
from autohhkek.services.g4f_runtime import G4FAppConfig
from autohhkek.services.openrouter_runtime import OpenRouterAppConfig


def test_repair_worker_allows_g4f_plan_only_without_mcp(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        runtime_settings=RuntimeSettings(llm_backend="g4f"),
        g4f_config=G4FAppConfig(model="gpt-4o-mini"),
        bridge=PlaywrightMCPBridge(PlaywrightMCPConfig(command="", args=[])),
        g4f_runner=lambda messages, model, response_schema=None: PlaywrightRepairOutput(
            diagnosis="Plan-only repair",
            patch_summary="Update selector candidates",
            patch_text="*** Begin Patch\n*** End Patch\n",
            test_text="def test_repair():\n    assert True\n",
        ),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "ready"
    assert result["repair_mode"] == "plan_only"


def test_repair_worker_reports_unavailable_for_openrouter_without_mcp(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        runtime_settings=RuntimeSettings(llm_backend="openrouter"),
        openrouter_config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        bridge=PlaywrightMCPBridge(PlaywrightMCPConfig(command="", args=[])),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "unavailable"
    assert result["openrouter_ready"] is True
    assert result["mcp_ready"] is False


def test_repair_worker_allows_openrouter_plan_with_mcp(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        runtime_settings=RuntimeSettings(llm_backend="openrouter"),
        openrouter_config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        bridge=PlaywrightMCPBridge(PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"])),
        runner=lambda agent, prompt, run_config=None: type(
            "Result",
            (),
            {
                "final_output": PlaywrightRepairOutput(
                    diagnosis="OpenRouter repair",
                    patch_summary="Update selector candidates",
                    patch_text="*** Begin Patch\n*** End Patch\n",
                    test_text="def test_repair():\n    assert True\n",
                )
            },
        )(),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "ready"
    assert result["repair_mode"] != "plan_only"


def test_repair_worker_falls_back_to_openrouter_when_openai_selected(tmp_path: Path):
    worker = PlaywrightRepairWorker(
        project_root=tmp_path,
        runtime_settings=RuntimeSettings(llm_backend="openai"),
        openrouter_config=OpenRouterAppConfig(api_key="or-test", model="openai/gpt-4o-mini"),
        bridge=PlaywrightMCPBridge(PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"])),
        runner=lambda agent, prompt, run_config=None: type(
            "Result",
            (),
            {
                "final_output": PlaywrightRepairOutput(
                    diagnosis="Fallback to OpenRouter",
                    patch_summary="Use fallback backend",
                    patch_text="*** Begin Patch\n*** End Patch\n",
                    test_text="def test_repair():\n    assert True\n",
                )
            },
        )(),
    )

    result = worker.run("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert result["status"] == "ready"
    assert result["requested_llm_backend"] == "openai"
    assert result["selected_llm_backend"] == "openrouter"

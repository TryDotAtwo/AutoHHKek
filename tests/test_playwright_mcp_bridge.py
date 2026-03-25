from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPBridge, PlaywrightMCPConfig


def test_playwright_mcp_config_parses_env_args(monkeypatch):
    monkeypatch.setenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", "npx")
    monkeypatch.setenv("AUTOHHKEK_PLAYWRIGHT_MCP_ARGS", "-y @playwright/mcp@latest --headless")

    config = PlaywrightMCPConfig.from_env()

    assert config.command == "npx"
    assert config.args == ["-y", "@playwright/mcp@latest", "--headless"]


def test_playwright_mcp_bridge_builds_stdio_params():
    bridge = PlaywrightMCPBridge(
        PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"])
    )

    params = bridge.to_stdio_params()

    assert params["command"] == "npx"
    assert params["args"] == ["-y", "@playwright/mcp@latest"]


def test_playwright_mcp_bridge_builds_repair_prompt():
    bridge = PlaywrightMCPBridge(
        PlaywrightMCPConfig(command="npx", args=["-y", "@playwright/mcp@latest"])
    )

    prompt = bridge.build_repair_prompt("click_apply_button", {"vacancy_id": "vac-1"}, "selector mismatch")

    assert "click_apply_button" in prompt
    assert "selector mismatch" in prompt

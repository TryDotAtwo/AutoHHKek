from autohhkek.integrations.hh.playwright_mcp import PlaywrightMCPConfig


def test_playwright_mcp_config_resolves_command_from_path(monkeypatch):
    monkeypatch.setenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", "npx")
    monkeypatch.setattr("autohhkek.integrations.hh.playwright_mcp.shutil.which", lambda command: "C:/Program Files/nodejs/npx.cmd")

    config = PlaywrightMCPConfig.from_env()

    assert config.command.endswith("npx.cmd")


def test_playwright_mcp_config_autodetects_local_npx_without_env(monkeypatch):
    monkeypatch.delenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", raising=False)
    monkeypatch.setattr("autohhkek.integrations.hh.playwright_mcp.shutil.which", lambda command: "npx")

    config = PlaywrightMCPConfig.from_env()

    assert config.command == "npx"
    assert config.is_configured() is True

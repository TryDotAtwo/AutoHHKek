from autohhkek.integrations.hh.script_engine import HHScriptRegistry, build_default_script_registry


def test_script_engine_uses_script_when_available():
    registry = build_default_script_registry()

    result = registry.execute("set_search_text", {"query": "LLM Engineer"})

    assert result.success is True
    assert result.strategy == "script"
    assert result.action == "set_search_text"
    assert result.fallback is None


def test_script_engine_falls_back_when_script_missing():
    registry = HHScriptRegistry()

    result = registry.execute("solve_unknown_dom", {"goal": "submit weird form"})

    assert result.success is False
    assert result.strategy == "agent_fallback"
    assert result.fallback is not None
    assert result.fallback["backend"] == "playwright_mcp"
    assert "solve_unknown_dom" in result.fallback["prompt"]


def test_script_engine_falls_back_when_script_raises():
    registry = HHScriptRegistry()

    def broken_handler(payload):
        raise RuntimeError("selector mismatch")

    registry.register("click_apply_button", broken_handler)
    result = registry.execute("click_apply_button", {"vacancy_id": "vac-1"})

    assert result.success is False
    assert result.strategy == "agent_fallback"
    assert result.error == "selector mismatch"

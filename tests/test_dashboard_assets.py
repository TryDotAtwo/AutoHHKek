from pathlib import Path


def test_dashboard_app_js_has_persistent_chat_and_apply_flow() -> None:
    text = Path("autohhkek/dashboard/assets/app.js").read_text(encoding="utf-8")

    assert "\ufffd" not in text
    assert "renderChatShell" in text
    assert "renderChatLog" in text
    assert 'id: "agent"' in text
    assert 'id: "vacancy"' in text
    assert 'return "vacancies";' in text
    assert "sendChatCommand" in text
    assert "sendClientLog" in text
    assert "/api/chat" in text
    assert "/api/actions/plan-filters" in text
    assert "/api/actions/save-cover-letter" in text
    assert "/api/actions/apply-batch" in text
    assert "/api/actions/apply-submit" in text
    assert "/api/actions/vacancy-feedback" in text
    assert "/api/actions/select-account" in text
    assert "cover-letter-input" in text
    assert "nextVacancyId" in text
    assert "system_rules_preview" in text
    assert "pauseAutoRefresh" in text
    assert "workspaceScrollTopByTab" in text
    assert "initInteractionGuards" in text
    assert "renderAccountSwitcher" in text


def test_dashboard_layout_keeps_workspace_left_and_chat_right() -> None:
    html = Path("autohhkek/dashboard/assets/index.html").read_text(encoding="utf-8")
    css = Path("autohhkek/dashboard/assets/app.css").read_text(encoding="utf-8")

    assert 'class="app-shell"' in html
    assert 'id="agent-view"' in html
    assert html.index('class="workspace"') < html.index('id="layout-resizer"') < html.index('id="chat-sidebar"')
    assert "grid-template-columns: minmax(0, 1fr) 10px minmax(18rem, var(--sidebar-width));" in css
    assert ".layout-resizer {" in css
    assert ".sidebar {\n  overflow: hidden;" in css
    assert ".workspace {\n  display: flex;" in css
    assert ".app-shell {" in css

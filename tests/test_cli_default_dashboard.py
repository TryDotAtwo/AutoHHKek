from types import SimpleNamespace

from autohhkek.app import cli


class _FakeThread:
    def join(self):
        raise KeyboardInterrupt


class _FakeHandle:
    def __init__(self):
        self.url = "http://127.0.0.1:8766"
        self.thread = _FakeThread()

    def close(self):
        return None


def test_cli_defaults_to_dashboard_and_opens_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)
    calls = {"ensure": 0, "analyze": 0}
    monkeypatch.setattr(cli.IntakeAgent, "ensure", lambda self, interactive=False: calls.__setitem__("ensure", calls["ensure"] + 1))
    monkeypatch.setattr(cli.VacancyAnalysisAgent, "analyze", lambda self, limit=120: calls.__setitem__("analyze", calls["analyze"] + 1))
    monkeypatch.setattr(cli, "start_dashboard_server", lambda project_root, host="127.0.0.1", port=8766: _FakeHandle())

    opened = {}
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.setdefault("url", url))

    result = cli.main([])

    assert result == 0
    assert opened["url"] == "http://127.0.0.1:8766"
    assert calls["ensure"] == 0
    assert calls["analyze"] == 0

import pytest

from autohhkek.services.playwright_browser import launch_chromium_resilient


class _FakeChromium:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def launch(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


@pytest.mark.asyncio
async def test_launch_chromium_resilient_falls_back_to_system_chrome():
    chromium = _FakeChromium(
        [
            RuntimeError("Executable doesn't exist at ... headless_shell.exe"),
            "chrome-browser",
        ]
    )

    browser = await launch_chromium_resilient(_FakePlaywright(chromium), headless=True)

    assert browser == "chrome-browser"
    assert chromium.calls == [
        {"headless": True},
        {"channel": "chrome", "headless": True},
    ]


@pytest.mark.asyncio
async def test_launch_chromium_resilient_falls_back_to_edge_and_then_headed():
    chromium = _FakeChromium(
        [
            RuntimeError("Executable doesn't exist at ... headless_shell.exe"),
            RuntimeError("chrome channel unavailable"),
            RuntimeError("edge channel unavailable"),
            RuntimeError("chrome headed unavailable"),
            "edge-headed-browser",
        ]
    )

    browser = await launch_chromium_resilient(_FakePlaywright(chromium), headless=True)

    assert browser == "edge-headed-browser"
    assert chromium.calls == [
        {"headless": True},
        {"channel": "chrome", "headless": True},
        {"channel": "msedge", "headless": True},
        {"channel": "chrome", "headless": False},
        {"channel": "msedge", "headless": False},
    ]


@pytest.mark.asyncio
async def test_launch_chromium_resilient_headed_prefers_system_channels():
    chromium = _FakeChromium(
        [
            RuntimeError("Executable doesn't exist at ... chrome.exe"),
            "chrome-browser",
        ]
    )

    browser = await launch_chromium_resilient(_FakePlaywright(chromium), headless=False)

    assert browser == "chrome-browser"
    assert chromium.calls == [
        {"headless": False},
        {"channel": "chrome", "headless": False},
    ]

from __future__ import annotations

import asyncio


_ASYNC_SUBPROCESS_SENTINEL = object()
_ASYNC_SUBPROCESS_PROBE: str | None | object = _ASYNC_SUBPROCESS_SENTINEL
_BROWSER_LAUNCH_SENTINEL = object()
_BROWSER_LAUNCH_PROBE: str | None | object = _BROWSER_LAUNCH_SENTINEL


def _is_missing_browser(error: Exception) -> bool:
    message = str(error)
    return "headless_shell.exe" in message or "Executable doesn't exist" in message


def get_async_subprocess_probe_error() -> str | None:
    global _ASYNC_SUBPROCESS_PROBE
    if _ASYNC_SUBPROCESS_PROBE is not _ASYNC_SUBPROCESS_SENTINEL:
        return _ASYNC_SUBPROCESS_PROBE

    async def _probe() -> None:
        proc = await asyncio.create_subprocess_exec(
            "cmd.exe",
            "/c",
            "echo",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    try:
        asyncio.run(_probe())
        _ASYNC_SUBPROCESS_PROBE = None
    except Exception as exc:  # noqa: BLE001
        _ASYNC_SUBPROCESS_PROBE = str(exc)
    return _ASYNC_SUBPROCESS_PROBE


def ensure_async_subprocess_available() -> None:
    error = get_async_subprocess_probe_error()
    if error:
        raise RuntimeError(
            "Local async subprocess pipes are unavailable in this Windows environment. "
            f"Playwright Python cannot start here: {error}"
        )


def get_browser_launch_probe_error() -> str | None:
    global _BROWSER_LAUNCH_PROBE
    if _BROWSER_LAUNCH_PROBE is not _BROWSER_LAUNCH_SENTINEL:
        return _BROWSER_LAUNCH_PROBE

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        _BROWSER_LAUNCH_PROBE = str(exc)
        return _BROWSER_LAUNCH_PROBE

    async def _probe() -> None:
        async with async_playwright() as playwright:
            browser = await launch_chromium_resilient(playwright, headless=True)
            await browser.close()

    try:
        asyncio.run(_probe())
        _BROWSER_LAUNCH_PROBE = None
    except Exception as exc:  # noqa: BLE001
        _BROWSER_LAUNCH_PROBE = str(exc)
    return _BROWSER_LAUNCH_PROBE


def ensure_local_playwright_browser_available() -> None:
    ensure_async_subprocess_available()
    error = get_browser_launch_probe_error()
    if error:
        raise RuntimeError(f"Local Playwright browser launch is unavailable: {error}")


async def launch_chromium_resilient(playwright, *, headless: bool):
    launch_attempts = [
        {"headless": headless},
        {"channel": "chrome", "headless": headless},
        {"channel": "msedge", "headless": headless},
    ]
    if headless:
        launch_attempts.extend(
            [
                {"channel": "chrome", "headless": False},
                {"channel": "msedge", "headless": False},
                {"headless": False},
            ]
        )

    last_error = None
    for kwargs in launch_attempts:
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("No Playwright browser launch attempts were made.")

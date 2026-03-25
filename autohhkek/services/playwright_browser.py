from __future__ import annotations


def _is_missing_browser(error: Exception) -> bool:
    message = str(error)
    return "headless_shell.exe" in message or "Executable doesn't exist" in message


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
            if not _is_missing_browser(exc) and kwargs.get("channel") is None:
                raise
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("No Playwright browser launch attempts were made.")

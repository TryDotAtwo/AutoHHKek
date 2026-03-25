from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_PLAYWRIGHT_MCP_ARGS = ["-y", "@playwright/mcp@latest"]


def _candidate_paths(command: str) -> list[str]:
    normalized = command.strip().lower()
    if normalized not in {"npx", "npx.cmd", "node", "node.exe"}:
        return []
    filenames = ["npx.cmd", "npx.exe"] if normalized.startswith("npx") else ["node.exe"]
    roots = [
        os.getenv("ProgramFiles", ""),
        os.getenv("ProgramFiles(x86)", ""),
        os.path.join(os.getenv("APPDATA", ""), "npm"),
    ]
    results: list[str] = []
    for root in roots:
        if not root:
            continue
        for filename in filenames:
            candidate = Path(root) / ("nodejs" if "Program Files" in root else "") / filename
            candidate_text = str(candidate)
            if candidate_text not in results:
                results.append(candidate_text)
    return results


def _default_local_command() -> str:
    return _resolve_command("npx")


def _split_args(raw: str, default: list[str] | None = None) -> list[str]:
    if not raw.strip():
        return list(default or [])
    return shlex.split(raw)


def _resolve_command(command: str) -> str:
    value = command.strip()
    if not value:
        return ""
    resolved = shutil.which(value)
    if resolved:
        return resolved
    for candidate in _candidate_paths(value):
        if os.path.exists(candidate):
            return candidate
    return value


def _command_exists(command: str) -> bool:
    value = command.strip()
    if not value:
        return False
    if os.path.isabs(value):
        return os.path.exists(value)
    if shutil.which(value) is not None:
        return True
    return any(os.path.exists(candidate) for candidate in _candidate_paths(value))


@dataclass(slots=True)
class PlaywrightMCPConfig:
    command: str = ""
    args: list[str] = field(default_factory=lambda: list(DEFAULT_PLAYWRIGHT_MCP_ARGS))
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    encoding: str = "utf-8"

    @classmethod
    def from_env(cls) -> "PlaywrightMCPConfig":
        resolved_command = _resolve_command(os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", "").strip())
        if not resolved_command:
            resolved_command = _default_local_command()
        return cls(
            command=resolved_command,
            args=_split_args(
                os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_ARGS", ""),
                default=DEFAULT_PLAYWRIGHT_MCP_ARGS,
            ),
            cwd=os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_CWD", "").strip(),
            encoding=os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_ENCODING", "utf-8").strip() or "utf-8",
        )

    def is_configured(self) -> bool:
        return _command_exists(self.command)


class PlaywrightMCPBridge:
    def __init__(self, config: PlaywrightMCPConfig | None = None) -> None:
        self.config = config or PlaywrightMCPConfig.from_env()

    def is_available(self) -> bool:
        return self.config.is_configured()

    def to_stdio_params(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command": self.config.command,
            "args": list(self.config.args),
        }
        if self.config.cwd:
            payload["cwd"] = self.config.cwd
        if self.config.env:
            payload["env"] = dict(self.config.env)
        if self.config.encoding:
            payload["encoding"] = self.config.encoding
        return payload

    def to_mcp_server(self):
        from agents.mcp import MCPServerStdio

        return MCPServerStdio(params=self.to_stdio_params(), name="playwright-mcp")

    def build_repair_prompt(self, action: str, payload: dict[str, Any], error: str = "") -> str:
        issue = error or "missing_script"
        return (
            "Use Playwright MCP to repair an hh.ru automation step.\n"
            f"Action: {action}\n"
            f"Payload: {payload}\n"
            f"Failure: {issue}\n"
            "Find a working selector path or interaction sequence, then return a reusable script patch "
            "that preserves the script-first runtime."
        )

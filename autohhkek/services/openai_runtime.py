from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from typing import Any


DEFAULT_PLAYWRIGHT_MCP_ARGS = ["-y", "@playwright/mcp@latest"]
DEFAULT_OPENAI_TIMEOUT_SEC = 25.0


def _split_args(raw: str, default: list[str] | None = None) -> list[str]:
    if not raw.strip():
        return list(default or [])
    return shlex.split(raw)


@dataclass(slots=True)
class OpenAIAppConfig:
    api_key: str = ""
    model: str = "gpt-5.4"
    base_url: str = ""
    organization: str = ""
    project: str = ""
    timeout_sec: float = DEFAULT_OPENAI_TIMEOUT_SEC
    playwright_mcp_command: str = ""
    playwright_mcp_args: list[str] = field(default_factory=lambda: list(DEFAULT_PLAYWRIGHT_MCP_ARGS))

    @classmethod
    def from_env(cls) -> "OpenAIAppConfig":
        playwright_mcp_command = os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_COMMAND", "").strip()
        if not playwright_mcp_command:
            playwright_mcp_command = shutil.which("npx") or ""
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("AUTOHHKEK_OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4",
            base_url=os.getenv("AUTOHHKEK_OPENAI_BASE_URL", "").strip(),
            organization=os.getenv("OPENAI_ORG_ID", "").strip(),
            project=os.getenv("OPENAI_PROJECT_ID", "").strip(),
            timeout_sec=float(os.getenv("AUTOHHKEK_OPENAI_TIMEOUT_SEC", str(DEFAULT_OPENAI_TIMEOUT_SEC)) or DEFAULT_OPENAI_TIMEOUT_SEC),
            playwright_mcp_command=playwright_mcp_command,
            playwright_mcp_args=_split_args(
                os.getenv("AUTOHHKEK_PLAYWRIGHT_MCP_ARGS", ""),
                default=DEFAULT_PLAYWRIGHT_MCP_ARGS,
            ),
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def build_provider(self):
        from agents import OpenAIProvider

        return OpenAIProvider(
            api_key=self.api_key or None,
            base_url=self.base_url or None,
            organization=self.organization or None,
            project=self.project or None,
            timeout=self.timeout_sec,
            use_responses=True,
        )

    def build_model_settings(self):
        from agents import ModelSettings

        return ModelSettings(verbosity="low")

    def build_run_config(self, *, workflow_name: str = "AutoHHKek workflow"):
        from agents import RunConfig

        return RunConfig(
            model=self.model,
            model_provider=self.build_provider(),
            model_settings=self.build_model_settings(),
            tracing_disabled=True,
            workflow_name=workflow_name,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "available": self.is_available(),
            "model": self.model,
            "base_url": self.base_url,
            "organization": bool(self.organization),
            "project": bool(self.project),
            "timeout_sec": self.timeout_sec,
            "playwright_mcp_command": self.playwright_mcp_command,
            "playwright_mcp_args": list(self.playwright_mcp_args),
        }

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .account_profiles import sanitize_account_key


@dataclass(slots=True)
class WorkspacePaths:
    project_root: Path
    account_key: str = "default"

    @property
    def global_runtime_root(self) -> Path:
        return self.project_root / ".autohhkek"

    @property
    def global_memory_dir(self) -> Path:
        return self.global_runtime_root / "memory"

    @property
    def accounts_dir(self) -> Path:
        return self.global_runtime_root / "accounts"

    @property
    def runtime_root(self) -> Path:
        return self.accounts_dir / sanitize_account_key(self.account_key)

    @property
    def session_dir(self) -> Path:
        return self.runtime_root / "session"

    @property
    def memory_dir(self) -> Path:
        return self.runtime_root / "memory"

    @property
    def rules_dir(self) -> Path:
        return self.runtime_root / "rules"

    @property
    def imported_rules_dir(self) -> Path:
        return self.rules_dir / "user_rules"

    @property
    def runs_dir(self) -> Path:
        return self.runtime_root / "runs"

    @property
    def artifacts_dir(self) -> Path:
        return self.runtime_root / "artifacts"

    @property
    def events_dir(self) -> Path:
        return self.runtime_root / "events"

    @property
    def snapshots_dir(self) -> Path:
        return self.runtime_root / "snapshots"

    @property
    def readme_path(self) -> Path:
        return self.runtime_root / "README.md"

    @property
    def active_account_path(self) -> Path:
        return self.global_memory_dir / "active_account.json"

    @property
    def accounts_registry_path(self) -> Path:
        return self.global_memory_dir / "hh_accounts.json"

    @property
    def incoming_hh_state_path(self) -> Path:
        return self.global_memory_dir / "incoming_hh_state.json"

    @property
    def preferences_path(self) -> Path:
        return self.memory_dir / "user_preferences.json"

    @property
    def anamnesis_path(self) -> Path:
        return self.memory_dir / "anamnesis.json"

    @property
    def runtime_settings_path(self) -> Path:
        return self.memory_dir / "runtime_settings.json"

    @property
    def dashboard_state_path(self) -> Path:
        return self.memory_dir / "dashboard_state.json"

    @property
    def hh_resumes_path(self) -> Path:
        return self.memory_dir / "hh_resumes.json"

    @property
    def hh_state_path(self) -> Path:
        return self.session_dir / "hh_state.json"

    @property
    def rules_markdown_path(self) -> Path:
        return self.rules_dir / "selection_rules.md"

    @property
    def vacancies_path(self) -> Path:
        return self.snapshots_dir / "vacancies.json"

    @property
    def assessments_path(self) -> Path:
        return self.snapshots_dir / "assessments.json"

    @property
    def analysis_state_path(self) -> Path:
        return self.snapshots_dir / "analysis_state.json"

    @property
    def resume_draft_path(self) -> Path:
        return self.artifacts_dir / "resume_draft.md"

    @property
    def resume_draft_json_path(self) -> Path:
        return self.artifacts_dir / "resume_draft.json"

    @property
    def apply_plan_path(self) -> Path:
        return self.artifacts_dir / "application_plan.json"

    @property
    def cover_letter_drafts_path(self) -> Path:
        return self.artifacts_dir / "cover_letter_drafts.json"

    @property
    def vacancy_feedback_path(self) -> Path:
        return self.artifacts_dir / "vacancy_feedback.json"

    @property
    def filter_plan_path(self) -> Path:
        return self.artifacts_dir / "filter_plan.json"

    @property
    def repair_tasks_path(self) -> Path:
        return self.artifacts_dir / "repair_tasks.json"

    @property
    def events_log_path(self) -> Path:
        return self.events_dir / "events.jsonl"

    def run_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def _migrate_legacy_layout_if_needed(self) -> None:
        default_root = self.accounts_dir / "default"
        if sanitize_account_key(self.account_key) != "default" or default_root.exists():
            return

        legacy_candidates = [
            "memory",
            "rules",
            "runs",
            "artifacts",
            "events",
            "snapshots",
        ]
        has_legacy_data = any((self.global_runtime_root / name).exists() for name in legacy_candidates) or (self.project_root / "hh_state.json").exists()
        if not has_legacy_data:
            return

        default_root.mkdir(parents=True, exist_ok=True)
        for name in legacy_candidates:
            source = self.global_runtime_root / name
            target = default_root / name
            if source.exists():
                shutil.copytree(source, target, dirs_exist_ok=True)

        session_dir = default_root / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        legacy_state = self.project_root / "hh_state.json"
        if legacy_state.exists() and not (session_dir / "hh_state.json").exists():
            shutil.copy2(legacy_state, session_dir / "hh_state.json")

    def ensure(self) -> None:
        self.global_runtime_root.mkdir(parents=True, exist_ok=True)
        self.global_memory_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_layout_if_needed()

        for path in (
            self.runtime_root,
            self.session_dir,
            self.memory_dir,
            self.rules_dir,
            self.imported_rules_dir,
            self.runs_dir,
            self.artifacts_dir,
            self.events_dir,
            self.snapshots_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        if not self.readme_path.exists():
            self.readme_path.write_text(
                "# AutoHHKek Runtime\n\n"
                "Здесь хранится долговременная память агента, правила отбора, артефакты, "
                "снимки вакансий, логи событий и история запусков.\n",
                encoding="utf-8",
            )

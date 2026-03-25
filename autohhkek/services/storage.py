from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from autohhkek.domain.models import Anamnesis, ResumeDraft, RunSummary, RuntimeSettings, UserPreferences, Vacancy, VacancyAssessment, utc_now_iso

from .account_profiles import sanitize_account_key
from .paths import WorkspacePaths
from .runtime_settings import normalize_runtime_settings


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    for _ in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            time.sleep(0.05)
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    for _ in range(3):
        temp_path = path.with_suffix(path.suffix + f".{time.time_ns()}.tmp")
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
            return
        except OSError:
            time.sleep(0.05)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    path.write_text(content, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def _vacancy_signature(item: Vacancy) -> dict[str, Any]:
    return {
        "vacancy_id": item.vacancy_id,
        "title": item.title,
        "company": item.company,
        "location": item.location,
        "url": item.url,
    }


def build_vacancy_snapshot_hash(vacancies: list[Vacancy]) -> str:
    stable = [_vacancy_signature(item) for item in vacancies]
    return hashlib.sha1(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _repair_task_key(payload: dict[str, Any]) -> str:
    stable = {
        "action": str(payload.get("action") or ""),
        "payload": payload.get("payload") or {},
        "error": str(payload.get("error") or ""),
    }
    return hashlib.sha1(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class WorkspaceStore:
    def __init__(self, project_root: Path, account_key: str | None = None) -> None:
        self.project_root = project_root.resolve()
        self.account_key = sanitize_account_key(account_key or self._read_active_account_key(project_root.resolve()) or "default")
        self.paths = WorkspacePaths(self.project_root, account_key=self.account_key)
        self.paths.ensure()
        self._ensure_active_account()

    @staticmethod
    def _read_active_account_key(project_root: Path) -> str:
        paths = WorkspacePaths(project_root.resolve(), account_key="default")
        paths.ensure()
        payload = _read_json(paths.active_account_path, {})
        return sanitize_account_key(str(dict(payload).get("account_key") or "default"))

    def _ensure_active_account(self) -> None:
        payload = _read_json(self.paths.active_account_path, {})
        if str(dict(payload).get("account_key") or "") != self.account_key:
            _write_json(
                self.paths.active_account_path,
                {
                    "account_key": self.account_key,
                    "updated_at": utc_now_iso(),
                },
            )

    @property
    def hh_state_path(self) -> Path:
        return self.paths.hh_state_path

    def load_active_account(self) -> dict[str, Any]:
        payload = _read_json(self.paths.active_account_path, {})
        return dict(payload) if isinstance(payload, dict) else {}

    def set_active_account(self, account_key: str) -> dict[str, Any]:
        normalized = sanitize_account_key(account_key)
        payload = {"account_key": normalized, "updated_at": utc_now_iso()}
        _write_json(self.paths.active_account_path, payload)
        return payload

    def load_accounts(self) -> list[dict[str, Any]]:
        payload = _read_json(self.paths.accounts_registry_path, [])
        items = list(payload) if isinstance(payload, list) else []
        normalized: list[dict[str, Any]] = []
        for item in items:
            current = dict(item or {})
            current["account_key"] = sanitize_account_key(str(current.get("account_key") or "default"))
            normalized.append(current)
        return normalized

    def save_account_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = dict(payload)
        item["account_key"] = sanitize_account_key(str(item.get("account_key") or "default"))
        item["updated_at"] = str(item.get("updated_at") or utc_now_iso())
        accounts = self.load_accounts()
        for index, current in enumerate(accounts):
            if current.get("account_key") == item["account_key"]:
                merged = dict(current)
                merged.update(item)
                accounts[index] = merged
                _write_json(self.paths.accounts_registry_path, accounts)
                return merged
        accounts.append(item)
        accounts.sort(key=lambda current: str(current.get("updated_at") or ""), reverse=True)
        _write_json(self.paths.accounts_registry_path, accounts)
        return item

    def load_preferences(self) -> UserPreferences | None:
        payload = _read_json(self.paths.preferences_path, None)
        return UserPreferences.from_dict(payload) if payload else None

    def save_preferences(self, preferences: UserPreferences) -> None:
        _write_json(self.paths.preferences_path, preferences.to_dict())

    def load_anamnesis(self) -> Anamnesis | None:
        payload = _read_json(self.paths.anamnesis_path, None)
        return Anamnesis.from_dict(payload) if payload else None

    def save_anamnesis(self, anamnesis: Anamnesis) -> None:
        _write_json(self.paths.anamnesis_path, anamnesis.to_dict())

    def load_selection_rules(self) -> str:
        if not self.paths.rules_markdown_path.exists():
            return ""
        return self.paths.rules_markdown_path.read_text(encoding="utf-8")

    def save_selection_rules(self, markdown: str) -> None:
        self.paths.rules_markdown_path.write_text(markdown.strip() + "\n", encoding="utf-8")

    def load_vacancies(self) -> list[Vacancy]:
        payload = _read_json(self.paths.vacancies_path, [])
        return [Vacancy.from_dict(item) for item in payload]

    def save_vacancies(self, vacancies: list[Vacancy]) -> None:
        _write_json(self.paths.vacancies_path, [item.to_dict() for item in vacancies])

    def load_assessments(self) -> list[VacancyAssessment]:
        payload = _read_json(self.paths.assessments_path, [])
        return [VacancyAssessment.from_dict(item) for item in payload]

    def save_assessments(self, assessments: list[VacancyAssessment]) -> None:
        _write_json(self.paths.assessments_path, [item.to_dict() for item in assessments])

    def load_analysis_state(self) -> dict[str, Any]:
        return dict(_read_json(self.paths.analysis_state_path, {}))

    def save_analysis_state(self, payload: dict[str, Any]) -> None:
        _write_json(self.paths.analysis_state_path, dict(payload))

    def load_resume_draft(self) -> ResumeDraft | None:
        payload = _read_json(self.paths.resume_draft_json_path, None)
        return ResumeDraft.from_dict(payload) if payload else None

    def load_resume_draft_markdown(self) -> str:
        if not self.paths.resume_draft_path.exists():
            return ""
        return self.paths.resume_draft_path.read_text(encoding="utf-8")

    def save_resume_draft(self, draft: ResumeDraft, markdown: str) -> None:
        _write_json(self.paths.resume_draft_json_path, draft.to_dict())
        self.paths.resume_draft_path.write_text(markdown.strip() + "\n", encoding="utf-8")

    def save_apply_plan(self, payload: dict[str, Any]) -> None:
        _write_json(self.paths.apply_plan_path, payload)

    def load_apply_plan(self) -> dict[str, Any] | None:
        return _read_json(self.paths.apply_plan_path, None)

    def save_filter_plan(self, payload: dict[str, Any]) -> None:
        _write_json(self.paths.filter_plan_path, payload)

    def load_filter_plan(self) -> dict[str, Any] | None:
        return _read_json(self.paths.filter_plan_path, None)

    def load_runtime_settings(self) -> RuntimeSettings:
        return RuntimeSettings.from_dict(normalize_runtime_settings(_read_json(self.paths.runtime_settings_path, {})))

    def save_runtime_settings(self, payload: RuntimeSettings | dict[str, Any]) -> RuntimeSettings:
        source = payload.to_dict() if hasattr(payload, "to_dict") else dict(payload)
        normalized = normalize_runtime_settings(source)
        _write_json(self.paths.runtime_settings_path, normalized)
        return RuntimeSettings.from_dict(normalized)

    def load_hh_resumes(self) -> list[dict[str, str]]:
        return list(_read_json(self.paths.hh_resumes_path, []))

    def save_hh_resumes(self, items: list[dict[str, str]]) -> None:
        _write_json(self.paths.hh_resumes_path, items)

    def load_selected_resume_id(self) -> str:
        settings = self.load_runtime_settings()
        return str(getattr(settings, "selected_resume_id", "") or "")

    def save_selected_resume_id(self, resume_id: str) -> RuntimeSettings:
        settings = self.load_runtime_settings().to_dict()
        settings["selected_resume_id"] = str(resume_id or "").strip()
        return self.save_runtime_settings(settings)

    def load_dashboard_state(self) -> dict[str, Any]:
        return dict(_read_json(self.paths.dashboard_state_path, {}))

    def save_dashboard_state(self, payload: dict[str, Any]) -> None:
        _write_json(self.paths.dashboard_state_path, dict(payload))

    def update_dashboard_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.load_dashboard_state()
        state.update(dict(patch))
        self.save_dashboard_state(state)
        return state

    def touch_dashboard_timestamp(self, key: str, *, value: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self.load_dashboard_state()
        state[str(key)] = value or utc_now_iso()
        if extra:
            state.update(dict(extra))
        self.save_dashboard_state(state)
        return state

    def load_cover_letter_drafts(self) -> dict[str, str]:
        payload = _read_json(self.paths.cover_letter_drafts_path, {})
        return {str(key): str(value) for key, value in dict(payload).items()}

    def save_cover_letter_drafts(self, payload: dict[str, str]) -> None:
        _write_json(self.paths.cover_letter_drafts_path, {str(key): str(value) for key, value in dict(payload).items()})

    def load_cover_letter_draft(self, vacancy_id: str) -> str:
        return self.load_cover_letter_drafts().get(str(vacancy_id), "")

    def save_cover_letter_draft(self, vacancy_id: str, text: str) -> None:
        drafts = self.load_cover_letter_drafts()
        vacancy_key = str(vacancy_id or "").strip()
        if not vacancy_key:
            return
        drafts[vacancy_key] = str(text or "")
        self.save_cover_letter_drafts(drafts)

    def load_vacancy_feedback(self) -> dict[str, dict[str, Any]]:
        payload = _read_json(self.paths.vacancy_feedback_path, {})
        return dict(payload) if isinstance(payload, dict) else {}

    def load_vacancy_feedback_item(self, vacancy_id: str) -> dict[str, Any]:
        return dict(self.load_vacancy_feedback().get(vacancy_id, {}) or {})

    def save_vacancy_feedback_item(self, vacancy_id: str, payload: dict[str, Any]) -> None:
        vacancy_key = str(vacancy_id or "").strip()
        if not vacancy_key:
            return
        items = self.load_vacancy_feedback()
        merged = dict(items.get(vacancy_key, {}) or {})
        merged.update(dict(payload))
        items[vacancy_key] = merged
        _write_json(self.paths.vacancy_feedback_path, items)

    def load_repair_tasks(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = _read_json(self.paths.repair_tasks_path, [])
        if limit is not None:
            return list(items)[-limit:][::-1]
        return list(items)[::-1]

    def append_repair_task(self, payload: dict[str, Any]) -> None:
        items = _read_json(self.paths.repair_tasks_path, [])
        items.append(payload)
        _write_json(self.paths.repair_tasks_path, items)

    def save_repair_task(self, payload: dict[str, Any]) -> None:
        items = _read_json(self.paths.repair_tasks_path, [])
        item = dict(payload)
        task_key = _repair_task_key(item)
        item["task_key"] = task_key
        for index, current in enumerate(items):
            if _repair_task_key(current) == task_key:
                merged = dict(current)
                merged.update(item)
                items[index] = merged
                _write_json(self.paths.repair_tasks_path, items)
                return
        items.append(item)
        _write_json(self.paths.repair_tasks_path, items)

    def save_imported_rule(self, source_name: str, markdown: str) -> None:
        target = self.paths.imported_rules_dir / Path(source_name).name
        target.write_text(markdown, encoding="utf-8")

    def load_imported_rules(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for path in sorted(self.paths.imported_rules_dir.glob("*.md")):
            items.append({"name": path.name, "content": path.read_text(encoding="utf-8")})
        return items

    def build_run_id(self, mode: str) -> str:
        digest = hashlib.sha1(f"{mode}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:10]
        return f"{mode}-{digest}"

    def save_run(self, run: RunSummary) -> None:
        run_path = self.paths.run_path(run.run_id)
        run_path.mkdir(parents=True, exist_ok=True)
        _write_json(run_path / "summary.json", run.to_dict())

    def list_runs(self, limit: int = 12) -> list[RunSummary]:
        runs: list[RunSummary] = []
        for summary_path in sorted(self.paths.runs_dir.glob("*/summary.json"), reverse=True):
            runs.append(RunSummary.from_dict(_read_json(summary_path, {})))
            if len(runs) >= limit:
                break
        return runs

    def record_event(self, kind: str, message: str, *, details: dict[str, Any] | None = None, run_id: str = "") -> None:
        payload = {
            "timestamp": utc_now_iso(),
            "kind": kind,
            "message": message,
            "details": details or {},
            "run_id": run_id,
        }
        _append_jsonl(self.paths.events_log_path, payload)

    def load_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.paths.events_log_path.exists():
            return []
        lines = self.paths.events_log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]][::-1]

    def save_debug_artifact(self, name: str, payload: Any, *, extension: str = "json", subdir: str = "debug") -> str:
        safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in str(name or "artifact")).strip("-") or "artifact"
        target_dir = self.paths.artifacts_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{utc_now_iso().replace(':', '-').replace('.', '-')}_{safe_name}.{extension.lstrip('.')}"
        if extension.lower().lstrip(".") == "json":
            _write_json(target, payload)
        else:
            target.write_text(str(payload), encoding="utf-8")
        return str(target)

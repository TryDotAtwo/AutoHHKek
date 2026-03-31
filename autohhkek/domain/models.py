from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .enums import BrowserBackend, FitCategory, QuestionKind, ReasonGroup, ScreeningPlatform


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    return value


def _filter_known_fields(model_type: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    known = {field_.name for field_ in fields(model_type)}
    return {key: value for key, value in payload.items() if key in known}


@dataclass(slots=True)
class AssessmentReason:
    code: str
    label: str
    group: ReasonGroup
    detail: str
    weight: float = 0.0
    subcategory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AssessmentReason":
        data = dict(payload)
        raw_group = data.get("group")
        try:
            data["group"] = ReasonGroup(raw_group)
        except Exception:  # noqa: BLE001
            fallback_map = {
                "fit": ReasonGroup.POSITIVE,
                "doubt": ReasonGroup.NEUTRAL,
                "no_fit": ReasonGroup.NEGATIVE,
            }
            data["group"] = fallback_map.get(str(raw_group or "").lower(), ReasonGroup.NEUTRAL)
        return cls(**_filter_known_fields(cls, data))


@dataclass(slots=True)
class Vacancy:
    vacancy_id: str
    title: str
    company: str = ""
    location: str = ""
    employment: str = ""
    salary_text: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    is_remote: bool = False
    url: str = ""
    summary: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [
            self.title,
            self.company,
            self.location,
            self.employment,
            self.salary_text,
            self.summary,
            self.description,
            " ".join(self.skills),
        ]
        return "\n".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Vacancy":
        return cls(**_filter_known_fields(cls, payload))


@dataclass(slots=True)
class VacancyAssessment:
    vacancy_id: str
    category: FitCategory
    subcategory: str
    score: float
    explanation: str
    reasons: list[AssessmentReason] = field(default_factory=list)
    recommended_action: str = ""
    ready_for_apply: bool = False
    review_strategy: str = "rule_based"
    review_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VacancyAssessment":
        data = dict(payload)
        data["category"] = FitCategory(data["category"])
        data["reasons"] = [AssessmentReason.from_dict(item) for item in data.get("reasons", [])]
        return cls(**_filter_known_fields(cls, data))


@dataclass(slots=True)
class UserPreferences:
    full_name: str = ""
    target_titles: list[str] = field(default_factory=list)
    excluded_companies: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    preferred_locations: list[str] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    salary_min: int | None = None
    remote_only: bool = False
    allow_relocation: bool = False
    cover_letter_mode: str = "adaptive"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserPreferences":
        return cls(**_filter_known_fields(cls, payload))


@dataclass(slots=True)
class Anamnesis:
    headline: str = ""
    summary: str = ""
    experience_years: float = 0.0
    primary_skills: list[str] = field(default_factory=list)
    secondary_skills: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Anamnesis":
        return cls(**_filter_known_fields(cls, payload))


@dataclass(slots=True)
class ResumeDraft:
    title: str
    summary: str
    key_skills: list[str]
    experience_highlights: list[str]
    project_highlights: list[str]
    education: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResumeDraft":
        return cls(**_filter_known_fields(cls, payload))


@dataclass(slots=True)
class QuestionField:
    label: str
    kind: QuestionKind
    required: bool = False
    options: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QuestionField":
        data = dict(payload)
        data["kind"] = QuestionKind(data["kind"])
        return cls(**_filter_known_fields(cls, data))


@dataclass(slots=True)
class ScreeningPlan:
    platform: ScreeningPlatform
    target_url: str
    questions: list[QuestionField] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requires_manual_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScreeningPlan":
        data = dict(payload)
        data["platform"] = ScreeningPlatform(data["platform"])
        data["questions"] = [QuestionField.from_dict(item) for item in data.get("questions", [])]
        return cls(**_filter_known_fields(cls, data))


@dataclass(slots=True)
class RunSummary:
    run_id: str
    mode: str
    status: str
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunSummary":
        return cls(**_filter_known_fields(cls, payload))


@dataclass(slots=True)
class HHRuntimeConfig:
    backend: BrowserBackend = BrowserBackend.PLAYWRIGHT
    headless: bool = False
    storage_state_path: str = "hh_state.json"
    locale: str = "ru-RU"

    def to_dict(self) -> dict[str, Any]:
        return serialize(self)


@dataclass(slots=True)
class RuntimeSettings:
    llm_backend: str = "openrouter"
    dashboard_mode: str = "analyze"
    mode_selected: bool = False
    auto_run_repair_worker: bool = False
    openai_model: str = "gpt-5.4"
    openrouter_model: str = "openai/gpt-5-nano"
    g4f_model: str = "gpt-4o-mini"
    g4f_provider: str = ""
    selected_resume_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = serialize(self)
        payload["agent_mode"] = self.dashboard_mode
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeSettings":
        data = dict(payload)
        if "agent_mode" in data and "dashboard_mode" not in data:
            data["dashboard_mode"] = data["agent_mode"]
        return cls(**_filter_known_fields(cls, data))

    @property
    def agent_mode(self) -> str:
        if self.dashboard_mode == "apply_plan":
            return "plan_apply"
        return self.dashboard_mode

    def __getitem__(self, key: str) -> Any:
        if key == "agent_mode":
            return self.agent_mode
        return getattr(self, key)

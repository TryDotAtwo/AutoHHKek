from __future__ import annotations

from enum import Enum


class FitCategory(str, Enum):
    FIT = "fit"
    DOUBT = "doubt"
    NO_FIT = "no_fit"


class ReasonGroup(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class ScreeningPlatform(str, Enum):
    HH = "hh"
    GOOGLE_FORMS = "google_forms"
    YANDEX_FORMS = "yandex_forms"
    UNKNOWN = "unknown"


class QuestionKind(str, Enum):
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    DROPDOWN = "dropdown"
    NUMBER = "number"
    DATE = "date"
    UNKNOWN = "unknown"


class BrowserBackend(str, Enum):
    PLAYWRIGHT = "playwright"
    PLAYWRIGHT_MCP = "playwright_mcp"

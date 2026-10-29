"""Pydantic schemas: one validation path for HTML forms, the JSON API and CSV import."""

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.i18n import t
from app.models import ApplicationStatus, EventType, WorkMode

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MAX_SALARY = 100_000_000

ShortText = Annotated[str | None, Field(max_length=200)]


class ApplicationIn(BaseModel):
    """Full, validated set of editable application fields."""

    model_config = ConfigDict(str_strip_whitespace=True)

    company: str = Field(min_length=1, max_length=200)
    position: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    portal_url: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=100)
    location: ShortText = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    salary_min: int | None = Field(default=None, ge=0, le=MAX_SALARY)
    salary_max: int | None = Field(default=None, ge=0, le=MAX_SALARY)
    salary_currency: str | None = None
    description: str | None = Field(default=None, max_length=50_000)
    resume_version: str | None = Field(default=None, max_length=50)
    contact_name: ShortText = None
    contact_email: str | None = Field(default=None, max_length=254)
    status: ApplicationStatus = ApplicationStatus.APPLIED
    applied_at: date | None = None
    notes: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="before")
    @classmethod
    def blank_strings_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: (None if isinstance(v, str) and not v.strip() else v) for k, v in data.items()
            }
        return data

    @field_validator("salary_min", "salary_max", mode="before")
    @classmethod
    def strip_number_separators(cls, value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(r"[\s  ]", "", value)
        return value

    @field_validator("url", "portal_url")
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"^https?://[^\s/]+", value, re.IGNORECASE):
            raise ValueError(t("err_url"))
        return value

    @field_validator("salary_currency")
    @classmethod
    def check_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper()
        if not CURRENCY_RE.match(value):
            raise ValueError(t("err_currency"))
        return value

    @field_validator("contact_email")
    @classmethod
    def check_email(cls, value: str | None) -> str | None:
        if value is not None and not EMAIL_RE.match(value):
            raise ValueError(t("err_invalid_email"))
        return value

    @field_validator("salary_max")
    @classmethod
    def check_salary_range(cls, value: int | None, info: ValidationInfo) -> int | None:
        # Field (not model) validator: the error is reported together with other field errors.
        salary_min = info.data.get("salary_min")
        if value is not None and salary_min is not None and value < salary_min:
            raise ValueError(t("err_salary_range"))
        return value


class ApplicationPatch(BaseModel):
    """Partial update for the JSON API. Only types are checked here; the merged
    result is validated as a whole by ApplicationIn (so cross-field rules still apply)."""

    company: str | None = None
    position: str | None = None
    url: str | None = None
    portal_url: str | None = None
    source: str | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    description: str | None = None
    resume_version: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    status: ApplicationStatus | None = None
    applied_at: date | None = None
    notes: str | None = None
    archived: bool | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    position: str
    url: str | None
    portal_url: str | None
    source: str | None
    location: str | None
    work_mode: WorkMode
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    resume_version: str | None
    contact_name: str | None
    contact_email: str | None
    status: ApplicationStatus
    applied_at: date | None
    last_activity_at: datetime
    notes: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class ApplicationList(BaseModel):
    items: list[ApplicationOut]
    total: int


class EventIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    type: EventType = EventType.NOTE
    text: str = Field(min_length=1, max_length=5000)

    @field_validator("type")
    @classmethod
    def no_manual_status_events(cls, value: EventType) -> EventType:
        # status_change events are written by the system only, so the history stays truthful
        if value == EventType.STATUS_CHANGE:
            raise ValueError(t("err_invalid"))
        return value


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: EventType
    text: str
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus | None
    created_at: datetime


_ERROR_KEYS = {
    "missing": "err_required",
    "string_too_short": "err_required",
    "string_type": "err_required",
    "int_parsing": "err_int",
    "int_type": "err_int",
    "int_from_float": "err_int",
    "greater_than_equal": "err_negative",
    "date_from_datetime_parsing": "err_date",
    "date_parsing": "err_date",
    "date_type": "err_date",
}


def form_errors(exc: ValidationError) -> dict[str, str]:
    """Turn a ValidationError into {field: human message}; model-level errors go to "__all__"."""
    errors: dict[str, str] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "__all__"
        kind = err["type"]
        if kind == "value_error":
            message = str(err["ctx"].get("error", err["msg"])) if "ctx" in err else err["msg"]
        elif kind == "string_too_long":
            message = t("err_too_long", max=err["ctx"]["max_length"])
        elif kind == "less_than_equal":
            message = t("err_invalid")
        else:
            message = t(_ERROR_KEYS.get(kind, "err_invalid"))
        errors.setdefault(field, message)
    return errors

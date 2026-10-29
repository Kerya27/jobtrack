"""Template rendering, flash messages and small HTMX helpers shared by the HTML routers."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.i18n import (
    LANGUAGE_NAMES,
    current_language,
    event_type_label,
    status_label,
    t,
    work_mode_label,
)
from app.models import ApplicationStatus, Event, EventType, WorkMode
from app.security import get_csrf_token
from app.services.applications import SENT_PERIODS, SORT_ORDERS, normalize_company
from app.timeutil import format_date, format_datetime

TEMPLATES_DIR = Path(__file__).parent / "templates"
FLASH_SESSION_KEY = "flash"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals.update(
    t=t,
    current_language=current_language,
    LANGUAGE_NAMES=LANGUAGE_NAMES,
    status_label=status_label,
    work_mode_label=work_mode_label,
    event_type_label=event_type_label,
    STATUSES=list(ApplicationStatus),
    WORK_MODES=list(WorkMode),
    USER_EVENT_TYPES=[e for e in EventType if e != EventType.STATUS_CHANGE],
)
templates.env.filters.update(dt=format_datetime, d=format_date)


def event_text(event: Event) -> str:
    """Status changes are rendered from from/to_status, so they follow the UI language."""
    if event.type == EventType.STATUS_CHANGE and event.to_status is not None:
        if event.from_status is None:
            return t("status_initial", to=status_label(event.to_status))
        return t(
            "status_changed",
            from_=status_label(event.from_status),
            to=status_label(event.to_status),
        )
    return event.text


templates.env.globals.update(
    event_text=event_text,
    normalize_company=normalize_company,
    SENT_PERIODS=SENT_PERIODS,
    SORT_ORDERS=SORT_ORDERS,
)


def format_salary(salary_min: int | None, salary_max: int | None, currency: str | None) -> str:
    def fmt(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    if salary_min is None and salary_max is None:
        return ""
    if salary_min is not None and salary_max is not None and salary_min != salary_max:
        text = f"{fmt(salary_min)}–{fmt(salary_max)}"
    else:
        text = fmt(salary_min if salary_min is not None else salary_max)  # type: ignore[arg-type]
    return f"{text} {currency}" if currency else text


templates.env.globals["format_salary"] = format_salary


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault(FLASH_SESSION_KEY, []).append({"message": message, "kind": kind})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop(FLASH_SESSION_KEY, [])


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200
) -> HTMLResponse:
    ctx: dict[str, Any] = {
        "csrf_token": get_csrf_token(request),
        "flashes": pop_flashes(request) if not name.startswith("partials/") else [],
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def safe_next(value: str | None) -> str:
    """Only same-site paths: never redirect to another host ("//evil.com", "https://…")."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def redirect(request: Request, url: str) -> Response:
    """POST/redirect/GET that also works for HTMX requests (full-page navigation)."""
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)

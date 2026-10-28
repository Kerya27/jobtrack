"""HTML pages and HTMX fragments for applications: list, quick add, card, timeline."""

import re
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app.config import get_settings
from app.deps import CSRFProtected, CurrentUser, DbSession
from app.i18n import t
from app.models import Application, ApplicationStatus, EventType, User
from app.schemas import ApplicationIn, EventIn, form_errors
from app.services import applications as svc
from app.services import job_parser, rate_limit
from app.services.ssrf import FetchFailure
from app.web import flash, is_htmx, redirect, render

router = APIRouter(tags=["pages"])

FORM_FIELDS = tuple(ApplicationIn.model_fields)
PARSE_ERROR_MESSAGES = {
    FetchFailure.BAD_URL: "parse_bad_url",
    FetchFailure.BLOCKED_BY_SITE: "parse_blocked_site",
    FetchFailure.NETWORK: "parse_network",
    FetchFailure.TOO_BIG: "parse_too_big",
    FetchFailure.NOT_HTML: "parse_not_html",
}


# ---------- helpers ----------


def _parse_filters(status: str | None, q: str | None, archived: str | None) -> svc.ListFilters:
    try:
        status_value = ApplicationStatus(status) if status else None
    except ValueError:
        status_value = None
    return svc.ListFilters(
        status=status_value,
        q=(q or "").strip() or None,
        archived=archived in {"1", "on", "true"},
    )


def _list_context(db: DbSession, user_id: int, filters: svc.ListFilters) -> dict[str, Any]:
    apps, total = svc.list_applications(db, user_id, filters)
    counts = svc.status_counts(db, user_id, filters)
    return {
        "apps": apps,
        "total": total,
        "counts": counts,
        "count_all": sum(counts.values()),
        "filters": filters,
    }


def form_values(source: Mapping[str, Any] | Application | None) -> dict[str, str]:
    """Values for re-displaying the form: everything as a string, None as ''."""
    values: dict[str, str] = {}
    for field in FORM_FIELDS:
        if isinstance(source, Application):
            raw = getattr(source, field)
        elif source is not None:
            raw = source.get(field)
        else:
            raw = None
        if raw is None:
            values[field] = ""
        elif hasattr(raw, "value"):  # enums
            values[field] = str(raw.value)
        else:
            values[field] = str(raw)
    return values


async def _submitted(request: Request) -> dict[str, Any]:
    form = await request.form()
    return {field: form.get(field) for field in FORM_FIELDS if field in form}


def render_new_form(
    request: Request,
    db: DbSession,
    user: User,
    values: dict[str, str],
    *,
    errors: dict[str, str] | None = None,
    message: str | None = None,
    message_kind: str = "info",
    offer_text_fallback: bool = False,
    status_code: int = 200,
) -> Response:
    context = {
        "user": user,
        "resume_versions": svc.resume_versions(db, user.id),
        "mode": "new",
        "values": values,
        "errors": errors or {},
        "message": message,
        "message_kind": message_kind,
        "offer_text_fallback": offer_text_fallback,
        "action": "/applications",
    }
    template = "partials/app_form.html" if is_htmx(request) else "form_page.html"
    return render(request, template, context, status_code=status_code)


def _card_context(db: DbSession, app: Application) -> dict[str, Any]:
    return {
        "app": app,
        "events": svc.list_events(db, app.id),
        "resume_versions": svc.resume_versions(db, app.user_id),
    }


# ---------- list ----------


@router.get("/")
def index(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    archived: Annotated[str | None, Query()] = None,
) -> Response:
    filters = _parse_filters(status, q, archived)
    context = {"user": user, **_list_context(db, user.id, filters)}
    if is_htmx(request) and request.headers.get("hx-target") == "results":
        return render(request, "partials/results.html", context)
    return render(request, "index.html", context)


# ---------- create ----------


@router.get("/applications/new")
def new_form(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    mode: Annotated[str, Query()] = "manual",
    url: Annotated[str, Query(max_length=2000)] = "",
) -> Response:
    if mode == "text":
        return render(request, "partials/paste_text.html", {"user": user, "url": url})
    return render_new_form(request, db, user, form_values(None))


@router.post("/applications/parse", dependencies=[CSRFProtected])
def parse_url(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    url: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """Quick add: download the vacancy page and return a pre-filled form (HTMX fragment)."""
    url = url.strip()
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url  # people often paste "jobs.cz/rpd/…"

    limit = get_settings().parse_rate_limit_per_hour
    if not rate_limit.try_acquire(db, user.id, limit):
        return render_new_form(
            request,
            db,
            user,
            form_values({"url": url}),
            message=t("parse_rate_limited", limit=limit),
            message_kind="warning",
            offer_text_fallback=True,
        )

    parsed = job_parser.parse_job(url)
    values = form_values(parsed.form_values())
    if parsed.error:
        message, kind = t(PARSE_ERROR_MESSAGES[parsed.error]), "warning"
    elif parsed.needs_manual:
        message, kind = t("parse_failed"), "warning"
    elif parsed.is_complete:
        message, kind = t("parse_ok"), "success"
    else:
        message, kind = t("parse_partial"), "info"
    return render_new_form(
        request,
        db,
        user,
        values,
        message=message,
        message_kind=kind,
        offer_text_fallback=kind == "warning",
    )


@router.post("/applications/parse-text", dependencies=[CSRFProtected])
def parse_text(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    text: Annotated[str, Form(max_length=100_000)] = "",
    url: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """Fallback: find the salary in pasted vacancy text; the rest becomes the description."""
    parsed = job_parser.parse_pasted_text(text, url.strip() or None)
    found = parsed.salary_min is not None or parsed.salary_max is not None
    return render_new_form(
        request,
        db,
        user,
        form_values(parsed.form_values()),
        message=t("text_parsed_salary") if found else t("text_parsed_no_salary"),
        message_kind="success" if found else "info",
    )


@router.post("/applications", dependencies=[CSRFProtected])
async def create(request: Request, user: CurrentUser, db: DbSession) -> Response:
    data = await _submitted(request)
    form = await request.form()
    if form.get("only_save"):
        data["status"] = ApplicationStatus.SAVED.value
    try:
        validated = ApplicationIn.model_validate(data)
    except ValidationError as exc:
        values = form_values(data)
        return render_new_form(request, db, user, values, errors=form_errors(exc))
    svc.create_application(db, user.id, validated)
    flash(request, t("created_ok"), "success")
    return redirect(request, "/")


# ---------- card ----------


@router.get("/applications/{application_id:int}")
def card(request: Request, application_id: int, user: CurrentUser, db: DbSession) -> Response:
    app = svc.get_application(db, user.id, application_id)
    return render(
        request,
        "application.html",
        {"user": user, "values": form_values(app), "errors": {}, **_card_context(db, app)},
    )


@router.post("/applications/{application_id:int}", dependencies=[CSRFProtected])
async def update(
    request: Request, application_id: int, user: CurrentUser, db: DbSession
) -> Response:
    app = svc.get_application(db, user.id, application_id)
    data = await _submitted(request)
    try:
        validated = ApplicationIn.model_validate(data)
    except ValidationError as exc:
        return render(
            request,
            "application.html",
            {
                "user": user,
                "values": form_values(data),
                "errors": form_errors(exc),
                **_card_context(db, app),
            },
            status_code=422,
        )
    svc.update_application(db, app, validated)
    flash(request, t("saved_ok"), "success")
    return redirect(request, f"/applications/{app.id}")


@router.post("/applications/{application_id:int}/status", dependencies=[CSRFProtected])
def change_status(
    request: Request,
    application_id: int,
    user: CurrentUser,
    db: DbSession,
    new_status: Annotated[ApplicationStatus, Form()],
    # current list filters, sent along so the chip counters can be refreshed out-of-band
    status: Annotated[str | None, Form()] = None,
    q: Annotated[str | None, Form()] = None,
    archived: Annotated[str | None, Form()] = None,
) -> Response:
    app = svc.get_application(db, user.id, application_id)
    svc.change_status(db, app, new_status)
    if not is_htmx(request):
        return redirect(request, f"/applications/{app.id}")
    filters = _parse_filters(status, q, archived)
    counts = svc.status_counts(db, user.id, filters)
    return render(
        request,
        "partials/status_changed.html",
        {"app": app, "counts": counts, "count_all": sum(counts.values()), "filters": filters},
    )


@router.post("/applications/{application_id:int}/events", dependencies=[CSRFProtected])
def add_event(
    request: Request,
    application_id: int,
    user: CurrentUser,
    db: DbSession,
    type: Annotated[str, Form()] = EventType.NOTE.value,
    text: Annotated[str, Form()] = "",
) -> Response:
    app = svc.get_application(db, user.id, application_id)
    error = None
    try:
        event_in = EventIn(type=type, text=text)  # type: ignore[arg-type]
    except ValidationError:
        error = t("err_event_text")
    else:
        svc.add_event(db, app, event_in.type, event_in.text)
    if is_htmx(request):
        return render(
            request,
            "partials/timeline.html",
            {"app": app, "events": svc.list_events(db, app.id), "event_error": error},
        )
    flash(request, error or t("event_added"), "error" if error else "success")
    return redirect(request, f"/applications/{app.id}")


@router.post("/applications/{application_id:int}/archive", dependencies=[CSRFProtected])
def toggle_archive(
    request: Request, application_id: int, user: CurrentUser, db: DbSession
) -> Response:
    app = svc.get_application(db, user.id, application_id)
    svc.set_archived(db, app, not app.archived)
    flash(request, t("archived_ok") if app.archived else t("unarchived_ok"), "success")
    return redirect(request, f"/applications/{app.id}")


@router.post("/applications/{application_id:int}/delete", dependencies=[CSRFProtected])
def delete(request: Request, application_id: int, user: CurrentUser, db: DbSession) -> Response:
    app = svc.get_application(db, user.id, application_id)
    svc.delete_application(db, app)
    flash(request, t("deleted_ok"), "success")
    return redirect(request, "/")

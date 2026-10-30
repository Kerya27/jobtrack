"""HTML pages and HTMX fragments for applications: list/board, quick add, card, bulk actions."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app.config import get_settings
from app.deps import CSRFProtected, CurrentUser, DbSession
from app.i18n import t
from app.models import Application, ApplicationStatus, DuoStatus, EventType, User, WorkMode
from app.schemas import ApplicationIn, CaptureIn, EventIn, form_errors
from app.services import applications as svc
from app.services import duo as duo_svc
from app.services import friends as friends_svc
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
# Board columns, in funnel order.
BOARD_STATUSES = list(ApplicationStatus)


# ---------- list filters ----------


def parse_filters(params: Mapping[str, Any]) -> svc.ListFilters:
    """Filters from query params (GET) or form fields (HTMX POST); bad values are ignored."""

    def text(name: str) -> str | None:
        value = params.get(name)
        return value.strip() or None if isinstance(value, str) else None

    def enum(name: str, enum_cls: Any) -> Any:
        try:
            return enum_cls(text(name)) if text(name) else None
        except ValueError:
            return None

    sent = text("sent")
    sort = text("sort")
    return svc.ListFilters(
        status=enum("status", ApplicationStatus),
        q=text("q"),
        archived=text("archived") in {"1", "on", "true"},
        work_mode=enum("work_mode", WorkMode),
        source=text("source"),
        resume_version=text("resume"),
        sent=sent if sent in svc.SENT_PERIODS else None,  # type: ignore[arg-type]
        sort=sort if sort in svc.SORT_ORDERS else "activity",  # type: ignore[arg-type]
    )


def filters_query(filters: svc.ListFilters, view: str = "list", **overrides: Any) -> str:
    """The filters as a query string (links: the "Today" chip, "reset", no-JS fallbacks)."""
    values = {
        "q": filters.q,
        "status": filters.status.value if filters.status else None,
        "archived": "1" if filters.archived else None,
        "work_mode": filters.work_mode.value if filters.work_mode else None,
        "source": filters.source,
        "resume": filters.resume_version,
        "sent": filters.sent,
        "sort": None if filters.sort == "activity" else filters.sort,
        "view": None if view == "list" else view,
    }
    values.update(overrides)
    return urlencode({k: v for k, v in values.items() if v})


def _view(params: Mapping[str, Any]) -> str:
    return "board" if params.get("view") == "board" else "list"


def render_results(request: Request, context: dict[str, Any]) -> Response:
    """The list/board fragment; the address bar gets a clean URL (no empty ?source=&sort=…)."""
    response = render(request, "partials/results.html", context)
    query = filters_query(context["filters"], context["view"])
    response.headers["HX-Push-Url"] = context["base_url"] + (f"?{query}" if query else "")
    return response


def list_context(
    db: DbSession,
    owner: User,
    filters: svc.ListFilters,
    view: str,
    *,
    viewer: User,
) -> dict[str, Any]:
    """Everything the list/board fragment needs; ``owner`` differs from ``viewer`` for friends."""
    readonly = owner.id != viewer.id
    board = view == "board"
    # The board shows every status as a column, so the status filter doesn't apply there.
    query_filters = replace(filters, status=None) if board else filters
    apps, total = svc.list_applications(db, owner.id, query_filters)
    counts = svc.status_counts(db, owner.id, filters)
    columns: dict[ApplicationStatus, list[Application]] = {s: [] for s in BOARD_STATUSES}
    if board:
        for app in apps:
            columns[app.status].append(app)
    base_url = f"/friends/{owner.id}" if readonly else "/"
    return {
        "apps": apps,
        "total": total,
        "counts": counts,
        "count_all": sum(counts.values()),
        "filters": filters,
        "view": view,
        "board_columns": columns,
        "today_count": svc.sent_today_count(db, owner.id, filters),
        "daily_goal": owner.daily_goal,
        "today_toggle_query": filters_query(
            filters, view, sent=None if filters.sent == "today" else "today"
        ),
        "sources": svc.distinct_sources(db, owner.id),
        "resume_versions": svc.resume_versions(db, owner.id),
        "friend_companies": {} if readonly else friends_svc.friends_by_company(db, viewer.id),
        "readonly": readonly,
        "base_url": base_url,
        "app_url_prefix": f"/friends/{owner.id}/applications/" if readonly else "/applications/",
    }


# ---------- the "new application" form ----------


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


def new_form_context(
    db: DbSession,
    user: User,
    values: dict[str, str],
    *,
    errors: dict[str, str] | None = None,
    message: str | None = None,
    message_kind: str = "info",
    offer_text_fallback: bool = False,
    check_duplicates: bool = False,
) -> dict[str, Any]:
    if not values.get("portal_url") and values.get("company"):
        values["portal_url"] = svc.portal_for_company(db, user.id, values["company"]) or ""
    duplicates: Sequence[Application] = []
    if check_duplicates:
        duplicates = svc.find_duplicates(
            db,
            user.id,
            url=values.get("url") or None,
            company=values.get("company") or None,
            position=values.get("position") or None,
            location=values.get("location") or None,
        )
    return {
        "user": user,
        "resume_versions": svc.resume_versions(db, user.id),
        "mode": "new",
        "values": values,
        "errors": errors or {},
        "message": message,
        "message_kind": message_kind,
        "offer_text_fallback": offer_text_fallback,
        "duplicates": duplicates,
        "action": "/applications",
    }


def render_new_form(
    request: Request, db: DbSession, user: User, values: dict[str, str], **kwargs: Any
) -> Response:
    status_code = kwargs.pop("status_code", 200)
    context = new_form_context(db, user, values, **kwargs)
    template = "partials/app_form.html" if is_htmx(request) else "form_page.html"
    return render(request, template, context, status_code=status_code)


def clone_values(app: Application) -> dict[str, str]:
    """A copy for "same company, another city": link, date and notes start empty."""
    values = form_values(app)
    values.update(url="", applied_at="", notes="", status=ApplicationStatus.APPLIED.value)
    return values


def _card_context(db: DbSession, app: Application) -> dict[str, Any]:
    return {
        "app": app,
        "events": svc.list_events(db, app.id),
        "resume_versions": svc.resume_versions(db, app.user_id),
    }


# ---------- list / board ----------


@router.get("/")
def index(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    clone: Annotated[int | None, Query()] = None,
) -> Response:
    params = request.query_params
    filters = parse_filters(params)
    context = {"user": user, **list_context(db, user, filters, _view(params), viewer=user)}
    battle = duo_svc.current_battle(db, user.id)
    today = svc.today_local()
    calendar_days = [today - timedelta(days=days_ago) for days_ago in range(13, -1, -1)]
    context.update(
        activity_streak=svc.application_streak(db, user.id, user.daily_goal),
        activity_calendar_days=calendar_days,
        activity_calendar_counts=svc.sent_counts_by_day(
            db, user.id, calendar_days[0], today
        ),
        activity_today=today,
        activity_battle=battle,
        activity_friends=friends_svc.friends(db, user.id),
        activity_today_count=svc.sent_today_count(db, user.id, svc.ListFilters()),
        activity_friend_today_count=(
            svc.sent_today_count(
                db,
                battle.friend.id,
                svc.ListFilters(),
            )
            if battle and battle.battle.status == DuoStatus.ACTIVE
            else 0
        ),
    )
    if is_htmx(request) and request.headers.get("hx-target") == "results":
        return render_results(request, context)
    if clone is not None:
        source = svc.get_application(db, user.id, clone)
        context["new_form"] = new_form_context(
            db, user, clone_values(source), message=t("clone_title")
        )
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
        if parsed.error_detail and parsed.error != FetchFailure.BAD_URL:
            message = f"{message} {t('parse_reason', detail=parsed.error_detail)}"
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
        check_duplicates=True,
    )


@router.get("/capture")
def capture_page(
    request: Request, user: CurrentUser, url: Annotated[str, Query(max_length=2000)] = ""
) -> Response:
    """The window the bookmarklet opens; app.js receives the page and posts it below."""
    return render(request, "capture.html", {"user": user, "url": url})


@router.post("/applications/capture", dependencies=[CSRFProtected])
def capture(request: Request, data: CaptureIn, user: CurrentUser, db: DbSession) -> Response:
    """Parse a page the user's browser already has open. Nothing is fetched by the server,
    so sites that block hosting IPs (and pages behind the user's login) work too."""
    url = data.url.strip()
    if not re.match(r"^https?://[^\s/]+\S*$", url, re.IGNORECASE):
        url = ""
    parsed = job_parser.parse_captured(data.html, url, data.text)
    if parsed.needs_manual:
        message, kind = t("parse_failed"), "warning"
    elif parsed.is_complete:
        message, kind = t("parse_ok"), "success"
    else:
        message, kind = t("parse_partial"), "info"
    return render_new_form(
        request,
        db,
        user,
        form_values(parsed.form_values()),
        message=message,
        message_kind=kind,
        check_duplicates=True,
    )


@router.post("/applications/parse-text", dependencies=[CSRFProtected])
def parse_text(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    text: Annotated[str, Form(max_length=100_000)] = "",
    url: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """Fallback: split pasted vacancy text into fields; the full text becomes the description."""
    parsed = job_parser.parse_pasted_text(text, url.strip() or None)
    found = parsed.salary_min is not None or parsed.salary_max is not None
    return render_new_form(
        request,
        db,
        user,
        form_values(parsed.form_values()),
        message=t("text_parsed_salary") if found else t("text_parsed_no_salary"),
        message_kind="success" if found else "info",
        check_duplicates=True,
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
    if not form.get("confirm_duplicate"):
        duplicates = svc.find_duplicates(
            db,
            user.id,
            url=validated.url,
            company=validated.company,
            position=validated.position,
            location=validated.location,
        )
        if duplicates:  # ask once; the re-rendered form carries confirm_duplicate=1
            return render_new_form(request, db, user, form_values(data), check_duplicates=True)
    if validated.portal_url is None:
        portal = svc.portal_for_company(db, user.id, validated.company)
        validated = validated.model_copy(update={"portal_url": portal})
    svc.create_application(db, user.id, validated)
    flash(request, t("created_ok"), "success")
    return redirect(request, "/")


# ---------- bulk actions ----------


@router.post("/applications/bulk", dependencies=[CSRFProtected])
async def bulk(request: Request, user: CurrentUser, db: DbSession) -> Response:
    form = await request.form()
    ids = [int(v) for v in form.getlist("ids") if isinstance(v, str) and v.isdigit()]
    action = form.get("action")
    text = str(form.get("bulk_text") or "").strip()
    apps = svc.owned_applications(db, user.id, ids)

    message, kind = None, "success"
    if not apps:
        message, kind = t("bulk_nothing"), "warning"
    elif action == "status":
        try:
            new_status = ApplicationStatus(str(form.get("bulk_status")))
        except ValueError:
            new_status = None
        if new_status is None:
            message, kind = t("err_invalid"), "warning"
        else:
            message = t("bulk_done", count=svc.bulk_change_status(db, apps, new_status))
    elif action in {"archive", "unarchive"}:
        changed = svc.bulk_set_archived(db, apps, action == "archive")
        message = t("bulk_done", count=changed)
    elif action in {"note", "portal"}:
        if not text:
            message, kind = t("bulk_need_text"), "warning"
        elif action == "note":
            message = t("bulk_done", count=svc.bulk_add_note(db, apps, text[:5000]))
        elif not re.match(r"^https?://[^\s/]+\S*$", text, re.IGNORECASE) or len(text) > 2000:
            message, kind = t("bulk_bad_portal"), "warning"
        else:
            message = t("bulk_done", count=svc.bulk_set_portal(db, apps, text))
    else:
        message, kind = t("err_invalid"), "warning"

    if not is_htmx(request):
        flash(request, message, kind)
        return redirect(request, "/")
    filters = parse_filters(form)
    context = {"user": user, **list_context(db, user, filters, _view(form), viewer=user)}
    context["bulk_message"] = {"message": message, "kind": kind}
    return render(request, "partials/results.html", context)


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
async def change_status(
    request: Request,
    application_id: int,
    user: CurrentUser,
    db: DbSession,
    new_status: Annotated[ApplicationStatus, Form()],
) -> Response:
    app = svc.get_application(db, user.id, application_id)
    svc.change_status(db, app, new_status)
    if not is_htmx(request):
        return redirect(request, f"/applications/{app.id}")
    # The current list filters come along, so counters can be refreshed in place.
    form = await request.form()
    filters = parse_filters(form)
    context = {"user": user, **list_context(db, user, filters, _view(form), viewer=user)}
    if context["view"] == "board":
        return render(request, "partials/results.html", context)
    return render(request, "partials/status_changed.html", {**context, "app": app})


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

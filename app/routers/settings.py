"""Account settings: preferences, language, password, CSV export/import, account deletion."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete

from app.deps import CSRFProtected, CurrentUser, DbSession, OptionalUser
from app.i18n import LANGUAGES, normalize_language, set_language, t
from app.models import User
from app.routers.auth import validate_new_password
from app.security import LANG_SESSION_KEY, hash_password, rotate_session, verify_password
from app.services import csv_io
from app.timeutil import today_local
from app.web import flash, redirect, render

router = APIRouter(tags=["pages"])


def _settings_page(
    request: Request, user: User, status_code: int = 200, **context: Any
) -> Response:
    return render(request, "settings.html", {"user": user, **context}, status_code=status_code)


@router.get("/settings")
def settings_page(request: Request, user: CurrentUser) -> Response:
    return _settings_page(request, user)


def _safe_next(value: str) -> str:
    """Only same-site paths: never redirect to another host ("//evil.com", "https://…")."""
    if value.startswith("/") and not value.startswith(("//", "/\\")) and "\\" not in value:
        return value
    return "/"


@router.post("/language", dependencies=[CSRFProtected])
def switch_language(
    request: Request,
    user: OptionalUser,
    db: DbSession,
    lang: Annotated[str, Form()] = "",
    next: Annotated[str, Form(max_length=2000)] = "/",
) -> Response:
    """The EN/RU switch in the header; works before login too."""
    lang = normalize_language(lang)
    request.session[LANG_SESSION_KEY] = lang
    if user is not None:
        user.language = lang
        db.commit()
    return redirect(request, _safe_next(next))


@router.post("/settings/preferences", dependencies=[CSRFProtected])
def save_preferences(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    language: Annotated[str, Form()] = "",
    daily_goal: Annotated[str, Form()] = "",
) -> Response:
    try:
        goal = int(daily_goal.strip())
    except ValueError:
        goal = -1
    if not 0 <= goal <= 100:
        return _settings_page(request, user, status_code=422, goal_error=t("err_goal"))
    if language in LANGUAGES:
        user.language = language
        request.session[LANG_SESSION_KEY] = language
        set_language(language)  # the flash below is already in the new language
    user.daily_goal = goal
    db.commit()
    flash(request, t("preferences_saved"), "success")
    return redirect(request, "/settings")


@router.post("/settings/password", dependencies=[CSRFProtected])
def change_password(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    password_repeat: Annotated[str, Form()] = "",
) -> Response:
    errors: dict[str, str] = {}
    if not verify_password(current_password, user.password_hash):
        errors["current_password"] = t("err_wrong_password")
    elif error := validate_new_password(new_password, password_repeat):
        errors["new_password"] = error
    if errors:
        return _settings_page(request, user, status_code=422, password_errors=errors)
    user.password_hash = hash_password(new_password)
    db.commit()
    flash(request, t("password_changed"), "success")
    return redirect(request, "/settings")


@router.post("/settings/delete", dependencies=[CSRFProtected])
def delete_account(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    password: Annotated[str, Form()] = "",
) -> Response:
    if not verify_password(password, user.password_hash):
        return _settings_page(request, user, status_code=422, delete_error=t("err_wrong_password"))
    # Applications, events and rate-limit rows go with the user via ON DELETE CASCADE.
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    rotate_session(request)
    flash(request, t("account_deleted"), "success")
    return redirect(request, "/login")


@router.get("/export.csv")
def export_csv(user: CurrentUser, db: DbSession) -> Response:
    filename = f"jobtrack-{today_local().isoformat()}.csv"
    return Response(
        content=csv_io.export_for_user(db, user.id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", dependencies=[CSRFProtected])
async def import_csv(
    request: Request, user: CurrentUser, db: DbSession, file: UploadFile | None = None
) -> Response:
    if file is None or not file.filename:
        return _settings_page(request, user, status_code=422, import_error=t("import_no_file"))
    content = await file.read(csv_io.MAX_FILE_BYTES + 1)
    try:
        report = csv_io.import_csv(db, user.id, content)
    except csv_io.CSVImportError as exc:
        return _settings_page(
            request, user, status_code=422, import_error=t("import_bad_file", error=exc)
        )
    return _settings_page(request, user, import_report=report)

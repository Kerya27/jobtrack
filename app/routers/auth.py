"""Registration, login and logout (HTML forms, session cookie)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.config import get_settings
from app.deps import USER_SESSION_KEY, CSRFProtected, DbSession, OptionalUser
from app.i18n import current_language, t
from app.models import User
from app.schemas import EMAIL_RE
from app.security import (
    BCRYPT_MAX_BYTES,
    LANG_SESSION_KEY,
    hash_password,
    rotate_session,
    verify_password,
)
from app.web import flash, redirect, render, safe_next

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LENGTH = 8
# Checked when the email is unknown, so a failed login takes the same time either way
# (response timing doesn't reveal which emails are registered).
_DUMMY_HASH = hash_password("timing-equalizer-password")


def validate_new_password(password: str, repeat: str | None = None) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return t("err_password_short")
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        return t("err_password_long")
    if repeat is not None and password != repeat:
        return t("err_password_mismatch")
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


@router.get("/register")
def register_page(request: Request, user: OptionalUser) -> Response:
    if user:
        return redirect(request, "/")
    return render(
        request, "register.html", {"allowed": get_settings().allow_registration, "form": {}}
    )


@router.post("/register", dependencies=[CSRFProtected])
def register(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_repeat: Annotated[str, Form()] = "",
) -> Response:
    if not get_settings().allow_registration:
        return render(request, "register.html", {"allowed": False, "form": {}}, status_code=403)

    email = normalize_email(email)
    errors: dict[str, str] = {}
    if not EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = t("err_invalid_email")
    if password_error := validate_new_password(password, password_repeat):
        errors["password"] = password_error
    if not errors and db.scalar(select(User.id).where(User.email == email)):
        errors["email"] = t("err_email_taken")
    if errors:
        return render(
            request,
            "register.html",
            {"allowed": True, "form": {"email": email}, "errors": errors},
            status_code=422,
        )

    user = User(email=email, password_hash=hash_password(password), language=current_language())
    db.add(user)
    db.commit()
    rotate_session(request)
    request.session[USER_SESSION_KEY] = user.id
    flash(request, t("welcome"), "success")
    return redirect(request, "/")


@router.get("/login")
def login_page(request: Request, user: OptionalUser, next: str = "/") -> Response:
    if user:
        return redirect(request, safe_next(next))
    return render(request, "login.html", {"form": {}, "next": safe_next(next)})


@router.post("/login", dependencies=[CSRFProtected])
def login(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form(max_length=2000)] = "/",
) -> Response:
    email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        return render(
            request,
            "login.html",
            {"form": {"email": email}, "error": t("err_bad_credentials"), "next": safe_next(next)},
            status_code=401,
        )
    rotate_session(request)
    request.session[USER_SESSION_KEY] = user.id
    request.session[LANG_SESSION_KEY] = user.language
    return redirect(request, safe_next(next))


@router.post("/logout", dependencies=[CSRFProtected])
def logout(request: Request) -> Response:
    rotate_session(request)
    flash(request, t("logged_out"))
    return redirect(request, "/login")

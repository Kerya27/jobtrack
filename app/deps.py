"""FastAPI dependencies: current user, login guards and CSRF protection."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import CSRF_FORM_FIELD, CSRF_HEADER, csrf_token_is_valid

USER_SESSION_KEY = "user_id"


class LoginRequiredError(Exception):
    """Anonymous access to a page: redirect to /login (or 401 for the API)."""


class CSRFError(Exception):
    """Raised when a state-changing request has no valid CSRF token."""


DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbSession) -> User | None:
    user_id = request.session.get(USER_SESSION_KEY)
    if user_id is None:
        return None
    # Reload on every request: a deleted account is logged out immediately.
    user = db.get(User, user_id)
    if user is None:
        request.session.pop(USER_SESSION_KEY, None)
    return user


def require_user(user: Annotated[User | None, Depends(get_current_user)]) -> User:
    if user is None:
        raise LoginRequiredError
    return user


async def csrf_protect(request: Request) -> None:
    """Accept the token from the X-CSRF-Token header (HTMX, JSON API) or a hidden form field."""
    token = request.headers.get(CSRF_HEADER)
    if token is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()  # Starlette caches the parsed form for the endpoint
            value = form.get(CSRF_FORM_FIELD)
            token = value if isinstance(value, str) else None
    if not csrf_token_is_valid(request, token):
        raise CSRFError


CurrentUser = Annotated[User, Depends(require_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user)]
CSRFProtected = Depends(csrf_protect)

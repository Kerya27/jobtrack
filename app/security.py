"""Password hashing and CSRF tokens."""

import secrets

import bcrypt
from fastapi import Request

BCRYPT_MAX_BYTES = 72
CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode()
    if len(encoded) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(encoded, password_hash.encode())


def get_csrf_token(request: Request) -> str:
    """Return the per-session CSRF token, creating it on first use."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def rotate_session(request: Request) -> None:
    """Drop everything in the session (prevents session fixation on login/logout)."""
    request.session.clear()
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)


def csrf_token_is_valid(request: Request, submitted: str | None) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(expected, submitted)

"""FastAPI application factory: middleware, routers and error handlers."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings
from app.deps import CSRFError, LoginRequiredError
from app.i18n import set_language, t
from app.routers import api, applications, auth, friends, settings, stats
from app.routers.api import UnprocessableError
from app.security import LANG_SESSION_KEY
from app.services.applications import NotFoundError
from app.web import is_htmx, render

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


class LanguageMiddleware:
    """Sets the UI language for the request from the session (must run inside SessionMiddleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            set_language(scope.get("session", {}).get(LANG_SESSION_KEY))
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    config = get_settings()
    app = FastAPI(
        title="JobTrack",
        version="1.0.0",
        # Interactive docs are handy locally but are not exposed in production.
        docs_url=None if config.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if config.is_prod else "/openapi.json",
    )
    # Starlette wraps later middleware around earlier ones: the session is decoded first.
    app.add_middleware(LanguageMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        session_cookie="jobtrack_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=config.is_prod,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(applications.router)
    app.include_router(stats.router)
    app.include_router(settings.router)
    app.include_router(friends.router)
    app.include_router(api.router)
    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequiredError)
    async def login_required(request: Request, _exc: LoginRequiredError) -> Response:
        if _is_api(request):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if is_htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CSRFError)
    async def csrf_failed(request: Request, _exc: CSRFError) -> Response:
        if _is_api(request):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return render(
            request, "error.html", {"title": "403", "text": t("err_csrf")}, status_code=403
        )

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, _exc: NotFoundError) -> Response:
        return _not_found_response(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return _not_found_response(request)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(UnprocessableError)
    async def unprocessable(_request: Request, exc: UnprocessableError) -> Response:
        return JSONResponse({"detail": exc.errors}, status_code=422)


def _not_found_response(request: Request) -> Response:
    if _is_api(request):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return render(
        request,
        "error.html",
        {"title": t("not_found"), "text": t("not_found_text")},
        status_code=404,
    )


app = create_app()

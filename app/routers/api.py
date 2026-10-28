"""Small JSON API (/api/v1) over the same services, authenticated by the session cookie.

State-changing calls need the X-CSRF-Token header (the token is in <meta name="csrf-token">
on every page, or from GET /api/v1/me).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import Response
from pydantic import ValidationError

from app.deps import CSRFProtected, CurrentUser, DbSession
from app.models import ApplicationStatus
from app.schemas import (
    ApplicationIn,
    ApplicationList,
    ApplicationOut,
    ApplicationPatch,
    EventIn,
    EventOut,
)
from app.security import get_csrf_token
from app.services import applications as svc

router = APIRouter(prefix="/api/v1", tags=["api"])


class UnprocessableError(Exception):
    def __init__(self, errors: list[dict[str, object]]) -> None:
        self.errors = errors


@router.get("/me")
def me(request: Request, user: CurrentUser) -> dict[str, object]:
    return {"id": user.id, "email": user.email, "csrf_token": get_csrf_token(request)}


@router.get("/applications", response_model=ApplicationList)
def list_applications(
    user: CurrentUser,
    db: DbSession,
    status_: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    q: str | None = None,
    archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApplicationList:
    filters = svc.ListFilters(status=status_, q=q, archived=archived)
    items, total = svc.list_applications(db, user.id, filters, limit=limit, offset=offset)
    return ApplicationList(items=[ApplicationOut.model_validate(a) for a in items], total=total)


@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CSRFProtected],
)
def create_application(data: ApplicationIn, user: CurrentUser, db: DbSession) -> ApplicationOut:
    return ApplicationOut.model_validate(svc.create_application(db, user.id, data))


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(application_id: int, user: CurrentUser, db: DbSession) -> ApplicationOut:
    return ApplicationOut.model_validate(svc.get_application(db, user.id, application_id))


@router.patch(
    "/applications/{application_id}",
    response_model=ApplicationOut,
    dependencies=[CSRFProtected],
)
def patch_application(
    application_id: int, patch: ApplicationPatch, user: CurrentUser, db: DbSession
) -> ApplicationOut:
    app = svc.get_application(db, user.id, application_id)
    changes = patch.model_dump(exclude_unset=True)
    archived = changes.pop("archived", None)
    merged = {**svc.application_to_input(app), **changes}
    try:
        validated = ApplicationIn.model_validate(merged)
    except ValidationError as exc:
        raise UnprocessableError(exc.errors(include_url=False, include_context=False)) from exc
    svc.update_application(db, app, validated)
    if archived is not None:
        svc.set_archived(db, app, archived)
    return ApplicationOut.model_validate(app)


@router.delete(
    "/applications/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[CSRFProtected],
)
def delete_application(application_id: int, user: CurrentUser, db: DbSession) -> Response:
    svc.delete_application(db, svc.get_application(db, user.id, application_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/applications/{application_id}/events", response_model=list[EventOut])
def list_events(application_id: int, user: CurrentUser, db: DbSession) -> list[EventOut]:
    app = svc.get_application(db, user.id, application_id)
    return [EventOut.model_validate(e) for e in svc.list_events(db, app.id)]


@router.post(
    "/applications/{application_id}/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[CSRFProtected],
)
def add_event(application_id: int, data: EventIn, user: CurrentUser, db: DbSession) -> EventOut:
    app = svc.get_application(db, user.id, application_id)
    return EventOut.model_validate(svc.add_event(db, app, data.type, data.text))

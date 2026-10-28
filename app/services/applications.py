"""Business logic for applications and their timeline.

Every function takes the owner's user_id (or an already ownership-checked Application),
so data isolation is enforced in one place.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from app.i18n import status_label, t
from app.models import Application, ApplicationStatus, Event, EventType, utcnow
from app.schemas import ApplicationIn
from app.timeutil import today_local


class NotFoundError(Exception):
    """Raised for missing objects and for objects owned by another user (both → 404)."""


@dataclass(frozen=True)
class ListFilters:
    status: ApplicationStatus | None = None
    q: str | None = None
    archived: bool = False


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(user_id: int, filters: ListFilters) -> Select[tuple[Application]]:
    stmt = select(Application).where(Application.user_id == user_id)
    if not filters.archived:
        stmt = stmt.where(Application.archived.is_(False))
    if filters.q:
        pattern = f"%{_escape_like(filters.q.strip())}%"
        stmt = stmt.where(
            or_(
                Application.company.ilike(pattern, escape="\\"),
                Application.position.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def list_applications(
    db: Session, user_id: int, filters: ListFilters, limit: int = 500, offset: int = 0
) -> tuple[Sequence[Application], int]:
    stmt = _filtered(user_id, filters)
    if filters.status:
        stmt = stmt.where(Application.status == filters.status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Application.last_activity_at.desc(), Application.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total


def status_counts(db: Session, user_id: int, filters: ListFilters) -> dict[ApplicationStatus, int]:
    """Counts per status for the filter chips (respecting search and archive toggle)."""
    sub = _filtered(user_id, filters).subquery()
    rows = db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all()
    return {ApplicationStatus(status): count for status, count in rows}


def resume_versions(db: Session, user_id: int) -> list[str]:
    """Distinct resume versions the user has used (suggestions in the form)."""
    rows = db.scalars(
        select(Application.resume_version)
        .where(Application.user_id == user_id, Application.resume_version.is_not(None))
        .distinct()
        .order_by(Application.resume_version)
    ).all()
    return [r for r in rows if r]


def get_application(db: Session, user_id: int, application_id: int) -> Application:
    app = db.scalar(
        select(Application).where(Application.id == application_id, Application.user_id == user_id)
    )
    if app is None:
        raise NotFoundError
    return app


def list_events(db: Session, application_id: int) -> Sequence[Event]:
    return db.scalars(
        select(Event)
        .where(Event.application_id == application_id)
        .order_by(Event.created_at.desc(), Event.id.desc())
    ).all()


def _status_event(
    application_id: int, old: ApplicationStatus | None, new: ApplicationStatus, comment: str | None
) -> Event:
    if old is None:
        text = t("status_initial", to=status_label(new))
    else:
        text = t("status_changed", from_=status_label(old), to=status_label(new))
    if comment:
        text = f"{text}. {comment}"
    return Event(
        application_id=application_id,
        type=EventType.STATUS_CHANGE,
        text=text,
        from_status=old,
        to_status=new,
    )


def status_initial_event(application_id: int, status: ApplicationStatus) -> Event:
    return _status_event(application_id, None, status, None)


def create_application(db: Session, user_id: int, data: ApplicationIn) -> Application:
    values = data.model_dump()
    if data.status != ApplicationStatus.SAVED and data.applied_at is None:
        values["applied_at"] = today_local()
    now = utcnow()
    app = Application(user_id=user_id, last_activity_at=now, **values)
    db.add(app)
    db.flush()
    db.add(_status_event(app.id, None, app.status, None))
    db.commit()
    return app


def change_status(
    db: Session, app: Application, new_status: ApplicationStatus, comment: str | None = None
) -> bool:
    """Change status, log a timeline event, bump last_activity_at. False if nothing changed."""
    if app.status == new_status:
        return False
    db.add(_status_event(app.id, app.status, new_status, comment))
    if app.status == ApplicationStatus.SAVED and app.applied_at is None:
        app.applied_at = today_local()
    app.status = new_status
    app.last_activity_at = utcnow()
    db.commit()
    return True


def update_application(db: Session, app: Application, data: ApplicationIn) -> Application:
    values = data.model_dump()
    new_status = values.pop("status")
    for field, value in values.items():
        setattr(app, field, value)
    if not change_status(db, app, new_status):
        db.commit()
    return app


def application_to_input(app: Application) -> dict[str, object]:
    """Current editable values of an application (used to merge partial API updates)."""
    return {field: getattr(app, field) for field in ApplicationIn.model_fields}


def add_event(db: Session, app: Application, event_type: EventType, text: str) -> Event:
    event = Event(application_id=app.id, type=event_type, text=text)
    db.add(event)
    app.last_activity_at = utcnow()
    db.commit()
    return event


def set_archived(db: Session, app: Application, archived: bool) -> None:
    app.archived = archived
    db.commit()


def delete_application(db: Session, app: Application) -> None:
    db.execute(delete(Application).where(Application.id == app.id))
    db.commit()

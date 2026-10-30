"""Business logic for applications and their timeline.

Every function takes the owner's user_id (or an already ownership-checked Application),
so data isolation is enforced in one place.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from app.i18n import status_label, t
from app.models import Application, ApplicationStatus, Event, EventType, WorkMode, utcnow
from app.schemas import ApplicationIn
from app.timeutil import today_local

SentPeriod = Literal["today", "7d", "30d"]
SortOrder = Literal["activity", "applied", "company", "salary"]
SENT_PERIODS: tuple[SentPeriod, ...] = ("today", "7d", "30d")
SORT_ORDERS: tuple[SortOrder, ...] = ("activity", "applied", "company", "salary")
_SENT_DAYS = {"today": 0, "7d": 6, "30d": 29}
MAX_BULK = 500


class NotFoundError(Exception):
    """Raised for missing objects and for objects owned by another user (both → 404)."""


@dataclass(frozen=True)
class ListFilters:
    status: ApplicationStatus | None = None
    q: str | None = None
    archived: bool = False
    work_mode: WorkMode | None = None
    source: str | None = None
    resume_version: str | None = None
    sent: SentPeriod | None = None
    applied_on: date | None = None
    sort: SortOrder = "activity"

    @property
    def extra_count(self) -> int:
        """How many of the "Filters" panel fields are set (shown next to its title)."""
        values = (self.work_mode, self.source, self.resume_version, self.sent)
        return sum(v is not None for v in values) + (self.sort != "activity")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(user_id: int, filters: ListFilters) -> Select[tuple[Application]]:
    """Every filter except the status (the status chips count across statuses)."""
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
    if filters.work_mode:
        stmt = stmt.where(Application.work_mode == filters.work_mode)
    if filters.source:
        stmt = stmt.where(Application.source == filters.source)
    if filters.resume_version:
        stmt = stmt.where(Application.resume_version == filters.resume_version)
    if filters.sent:
        today = today_local()
        since = today - timedelta(days=_SENT_DAYS[filters.sent])
        stmt = stmt.where(
            Application.applied_at >= since,
            Application.applied_at <= today,
            Application.status != ApplicationStatus.SAVED,
        )
    if filters.applied_on:
        stmt = stmt.where(
            Application.applied_at == filters.applied_on,
            Application.status != ApplicationStatus.SAVED,
        )
    return stmt


def _ordered(stmt: Select[tuple[Application]], sort: SortOrder) -> Select[tuple[Application]]:
    if sort == "applied":
        return stmt.order_by(Application.applied_at.desc().nulls_last(), Application.id.desc())
    if sort == "company":
        return stmt.order_by(func.lower(Application.company), Application.id.desc())
    if sort == "salary":
        salary = func.coalesce(Application.salary_max, Application.salary_min)
        return stmt.order_by(salary.desc().nulls_last(), Application.id.desc())
    return stmt.order_by(Application.last_activity_at.desc(), Application.id.desc())


def list_applications(
    db: Session, user_id: int, filters: ListFilters, limit: int = 500, offset: int = 0
) -> tuple[Sequence[Application], int]:
    stmt = _filtered(user_id, filters)
    if filters.status:
        stmt = stmt.where(Application.status == filters.status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(_ordered(stmt, filters.sort).limit(limit).offset(offset)).all()
    return rows, total


def status_counts(db: Session, user_id: int, filters: ListFilters) -> dict[ApplicationStatus, int]:
    """Counts per status for the filter chips (respecting all the other filters)."""
    sub = _filtered(user_id, filters).subquery()
    rows = db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all()
    return {ApplicationStatus(status): count for status, count in rows}


def sent_today_count(db: Session, user_id: int, filters: ListFilters) -> int:
    """For the "Today 3/5" chip: exactly the rows you'd see after clicking it."""
    stmt = _filtered(user_id, replace(filters, sent="today"))
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def sent_counts_by_day(
    db: Session, user_id: int, start: date, end: date
) -> dict[date, int]:
    """Count sent applications by applied date in the inclusive date range."""
    rows = db.execute(
        select(Application.applied_at, func.count())
        .where(
            Application.user_id == user_id,
            Application.applied_at >= start,
            Application.applied_at <= end,
            Application.status != ApplicationStatus.SAVED,
        )
        .group_by(Application.applied_at)
    ).all()
    return {applied_on: count for applied_on, count in rows if applied_on}


def application_streak(db: Session, user_id: int, goal: int) -> int:
    """Consecutive days meeting the daily goal; an incomplete today preserves yesterday's streak."""
    today = today_local()
    rows = db.execute(
        select(Application.applied_at, func.count())
        .where(
            Application.user_id == user_id,
            Application.applied_at <= today,
            Application.status != ApplicationStatus.SAVED,
        )
        .group_by(Application.applied_at)
    ).all()
    counts: dict[date, int] = {applied_on: count for applied_on, count in rows if applied_on}

    def completed(day: date) -> bool:
        count = counts.get(day, 0)
        return count >= goal if goal else count > 0

    day = today if completed(today) else today - timedelta(days=1)
    streak = 0
    while completed(day):
        streak += 1
        day -= timedelta(days=1)
    return streak


def distinct_sources(db: Session, user_id: int) -> list[str]:
    rows = db.scalars(
        select(Application.source)
        .where(Application.user_id == user_id, Application.source.is_not(None))
        .distinct()
        .order_by(Application.source)
    ).all()
    return [r for r in rows if r]


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
    db: Session,
    app: Application,
    new_status: ApplicationStatus,
    comment: str | None = None,
    *,
    commit: bool = True,
) -> bool:
    """Change status, log a timeline event, bump last_activity_at. False if nothing changed."""
    if app.status == new_status:
        return False
    db.add(_status_event(app.id, app.status, new_status, comment))
    if app.status == ApplicationStatus.SAVED and app.applied_at is None:
        app.applied_at = today_local()
    app.status = new_status
    app.last_activity_at = utcnow()
    if commit:
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


def add_event(
    db: Session, app: Application, event_type: EventType, text: str, *, commit: bool = True
) -> Event:
    event = Event(application_id=app.id, type=event_type, text=text)
    db.add(event)
    app.last_activity_at = utcnow()
    if commit:
        db.commit()
    return event


def set_archived(db: Session, app: Application, archived: bool) -> None:
    app.archived = archived
    db.commit()


def delete_application(db: Session, app: Application) -> None:
    db.execute(delete(Application).where(Application.id == app.id))
    db.commit()


# ---------- bulk actions (one transaction each) ----------


def owned_applications(db: Session, user_id: int, ids: Iterable[int]) -> Sequence[Application]:
    """The user's applications among ``ids``; ids of other users' rows are silently ignored."""
    wanted = list(dict.fromkeys(ids))[:MAX_BULK]
    if not wanted:
        return []
    return db.scalars(
        select(Application).where(Application.user_id == user_id, Application.id.in_(wanted))
    ).all()


def bulk_change_status(
    db: Session, apps: Sequence[Application], new_status: ApplicationStatus
) -> int:
    changed = sum(change_status(db, app, new_status, commit=False) for app in apps)
    db.commit()
    return changed


def bulk_set_archived(db: Session, apps: Sequence[Application], archived: bool) -> int:
    changed = 0
    for app in apps:
        if app.archived != archived:
            app.archived = archived
            changed += 1
    db.commit()
    return changed


def bulk_add_note(db: Session, apps: Sequence[Application], text: str) -> int:
    for app in apps:
        add_event(db, app, EventType.NOTE, text, commit=False)
    db.commit()
    return len(apps)


def bulk_set_portal(db: Session, apps: Sequence[Application], url: str) -> int:
    for app in apps:
        app.portal_url = url
    db.commit()
    return len(apps)


# ---------- duplicates, company portal ----------

_LEGAL_SUFFIXES = frozenset(
    {"sro", "as", "ltd", "inc", "llc", "gmbh", "uab", "plc", "bv", "ag", "oy", "ab", "spzoo", "co"}
)


def normalize_company(name: str) -> str:
    """ "CyberCare, s.r.o." and "cybercare" → "cybercare" (for matching, never for display)."""
    words = re.sub(r"[^\w\s]+", "", name.casefold()).split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    joined = "".join(words)
    for suffix in ("sro", "spzoo"):  # "s.r.o." / "sp. z o.o." collapse into one token
        if joined.endswith(suffix) and len(joined) > len(suffix):
            joined = joined.removesuffix(suffix)
    return joined


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _norm_url(value: str | None) -> str:
    return (value or "").strip().rstrip("/").casefold()


def find_duplicates(
    db: Session,
    user_id: int,
    *,
    url: str | None,
    company: str | None,
    position: str | None,
    location: str | None,
    limit: int = 3,
) -> list[Application]:
    """Same link, or same company + position + location (another city is not a duplicate).

    An empty location on either side matches any city: better one extra warning than a miss.
    """
    conditions = []
    if url and _norm_url(url):
        conditions.append(func.lower(Application.url).in_({_norm_url(url), _norm_url(url) + "/"}))
    if company and position:
        conditions.append(func.lower(Application.position) == position.strip().lower())
    if not conditions:
        return []
    candidates = db.scalars(
        select(Application)
        .where(Application.user_id == user_id, or_(*conditions))
        .order_by(Application.id.desc())
        .limit(50)
    ).all()
    found = []
    for app in candidates:
        same_url = bool(url) and _norm_url(app.url) == _norm_url(url)
        same_job = (
            company is not None
            and normalize_company(app.company) == normalize_company(company)
            and _norm(app.position) == _norm(position)
            and (not app.location or not location or _norm(app.location) == _norm(location))
        )
        if same_url or same_job:
            found.append(app)
    return found[:limit]


def portal_for_company(db: Session, user_id: int, company: str | None) -> str | None:
    """The portal link the user saved last time for this company (to prefill new applications)."""
    if not company or not company.strip():
        return None
    target = normalize_company(company)
    rows = db.execute(
        select(Application.company, Application.portal_url)
        .where(Application.user_id == user_id, Application.portal_url.is_not(None))
        .order_by(Application.updated_at.desc())
        .limit(200)
    ).all()
    return next((portal for name, portal in rows if normalize_company(name) == target), None)

"""SQLAlchemy models.

There are deliberately no ORM relationships: every query is explicit, so there is
no lazy loading and no accidental N+1. Child rows are removed by ON DELETE CASCADE.
"""

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApplicationStatus(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    REPLIED = "replied"
    INTERVIEW = "interview"
    TEST_TASK = "test_task"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"


class WorkMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EventType(StrEnum):
    STATUS_CHANGE = "status_change"
    NOTE = "note"
    INTERVIEW = "interview"
    MESSAGE = "message"


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store enums as VARCHAR + CHECK constraint (portable across SQLite and PostgreSQL)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_user_status", "user_id", "status"),
        Index("ix_applications_user_last_activity", "user_id", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    company: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2000))
    source: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(200))
    work_mode: Mapped[WorkMode] = mapped_column(
        _str_enum(WorkMode, "work_mode"), default=WorkMode.UNKNOWN
    )
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    description: Mapped[str | None] = mapped_column(Text)
    resume_version: Mapped[str | None] = mapped_column(String(50))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[ApplicationStatus] = mapped_column(
        _str_enum(ApplicationStatus, "application_status"), default=ApplicationStatus.APPLIED
    )
    applied_at: Mapped[date | None] = mapped_column(Date)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[EventType] = mapped_column(_str_enum(EventType, "event_type"))
    text: Mapped[str] = mapped_column(Text, default="")
    # Filled for status_change events: lets /stats rebuild the real funnel
    # (an application that went applied -> interview -> rejected still counts as an interview).
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        _str_enum(ApplicationStatus, "event_from_status")
    )
    to_status: Mapped[ApplicationStatus | None] = mapped_column(
        _str_enum(ApplicationStatus, "event_to_status")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ParseRequest(Base):
    """One row per call to the URL parser; used for the per-user hourly rate limit."""

    __tablename__ = "parse_requests"
    __table_args__ = (Index("ix_parse_requests_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

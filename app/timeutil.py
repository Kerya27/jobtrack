"""Time helpers: everything is stored in UTC and shown in the display time zone."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().display_timezone)


def as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; they are UTC by convention in this app.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_local(value: datetime) -> datetime:
    return as_utc(value).astimezone(local_tz())


def today_local() -> date:
    return datetime.now(local_tz()).date()


def format_datetime(value: datetime | None) -> str:
    return to_local(value).strftime("%d.%m.%Y %H:%M") if value else ""


def format_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = to_local(value).date()
    return value.strftime("%d.%m.%Y")

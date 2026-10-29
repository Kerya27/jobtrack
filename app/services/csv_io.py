"""CSV export / import of applications (e.g. to move data from or back to Excel).

The export and import use the same columns, so export → import is lossless for every
editable field plus ``archived`` and ``last_activity_at``. The timeline is not exported.
"""

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import STATUS_LABELS, WORK_MODE_LABELS, t
from app.models import Application, ApplicationStatus, WorkMode, utcnow
from app.schemas import ApplicationIn, form_errors
from app.services.applications import status_initial_event
from app.timeutil import as_utc

COLUMNS: tuple[str, ...] = (
    "company",
    "position",
    "status",
    "applied_at",
    "url",
    "portal_url",
    "source",
    "location",
    "work_mode",
    "salary_min",
    "salary_max",
    "salary_currency",
    "resume_version",
    "contact_name",
    "contact_email",
    "notes",
    "description",
    "archived",
    "last_activity_at",
)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
MAX_REPORTED_ERRORS = 20
# Cells starting with these are treated as formulas by Excel/LibreOffice ("CSV injection").
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_TRUE = {"1", "true", "yes", "y", "да", "так", "ano", "x"}


class CSVImportError(Exception):
    """The file as a whole can't be read (wrong encoding, no header, too big)."""


@dataclass
class ImportReport:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def skip(self, row_number: int, reason: str) -> None:
        self.skipped += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(t("csv_row", row=row_number, reason=reason))


# ---------- export ----------


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value"):  # enums
        return str(value.value)
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def export_csv(apps: Iterable[Application]) -> str:
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM: Excel then opens UTF-8 (Czech, Cyrillic) correctly
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for app in apps:
        writer.writerow([_cell(getattr(app, column)) for column in COLUMNS])
    return buffer.getvalue()


def export_for_user(db: Session, user_id: int) -> str:
    apps = db.scalars(
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.last_activity_at.desc(), Application.id.desc())
    ).all()
    return export_csv(apps)


# ---------- import ----------


def _decode(content: bytes) -> str:
    if len(content) > MAX_FILE_BYTES:
        raise CSVImportError(t("csv_too_big"))
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVImportError(t("csv_not_utf8")) from exc


def _dialect_delimiter(text: str) -> str:
    header = text.split("\n", 1)[0]
    return max((",", ";", "\t"), key=header.count)


def _unescape_formula(value: str) -> str:
    if value.startswith("'") and value[1:].startswith(_FORMULA_START):
        return value[1:]
    return value


def _lookup(value: str, enum_cls: Any, labels: dict[str, dict[Any, str]]) -> str:
    """Accept the code ("interview") and the UI label in any language ("Interview", "Интервью")."""
    normalized = value.strip().lower()
    for member in enum_cls:
        names = {member.value} | {by_lang[member].lower() for by_lang in labels.values()}
        if normalized in names:
            return str(member.value)
    return normalized


def _parse_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    match = re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", value)  # 28.10.2026 (Excel CZ/RU)
    if match:
        day, month, year = (int(g) for g in match.groups())
        return date(year, month, day).isoformat()
    return value[:10] if re.match(r"\d{4}-\d{2}-\d{2}", value) else value


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _dedupe_key(
    company: str, position: str, url: str | None, applied: date | None
) -> tuple[Any, ...]:
    if url:
        return ("url", url.strip().lower())
    return ("fields", company.strip().lower(), position.strip().lower(), applied)


def _row_to_data(row: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in ApplicationIn.model_fields:
        raw = row.get(column)
        if raw is None:
            continue
        data[column] = _unescape_formula(raw.strip())
    if data.get("status"):
        data["status"] = _lookup(data["status"], ApplicationStatus, STATUS_LABELS)
    if data.get("work_mode"):
        data["work_mode"] = _lookup(data["work_mode"], WorkMode, WORK_MODE_LABELS)
    if "applied_at" in data:
        try:
            data["applied_at"] = _parse_date(data["applied_at"])
        except ValueError:
            data["applied_at"] = "invalid"
    return data


def import_csv(db: Session, user_id: int, content: bytes) -> ImportReport:
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text), delimiter=_dialect_delimiter(text))
    if not reader.fieldnames:
        raise CSVImportError(t("csv_empty"))
    reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
    if not {"company", "position"} <= set(reader.fieldnames):
        raise CSVImportError(t("csv_no_columns"))

    existing = {
        _dedupe_key(company, position, url, applied)
        for company, position, url, applied in db.execute(
            select(
                Application.company, Application.position, Application.url, Application.applied_at
            ).where(Application.user_id == user_id)
        )
    }
    report = ImportReport()
    new_apps: list[tuple[Application, ApplicationStatus]] = []
    for index, row in enumerate(reader):
        row_number = index + 2  # header is line 1
        if index >= MAX_ROWS:
            report.skip(row_number, t("csv_too_many_rows", max=MAX_ROWS))
            continue
        cells: dict[str, str] = {k: v or "" for k, v in row.items() if isinstance(k, str)}
        if not any(v.strip() for v in cells.values()):
            continue  # blank line
        data = _row_to_data(cells)
        if not data.get("company") or not data.get("position"):
            report.skip(row_number, t("csv_missing"))
            continue
        try:
            validated = ApplicationIn.model_validate(data)
        except ValidationError as exc:
            column, message = next(iter(form_errors(exc).items()))
            report.skip(row_number, f"{t('csv_bad_value', column=column)} ({message})")
            continue
        key = _dedupe_key(
            validated.company, validated.position, validated.url, validated.applied_at
        )
        if key in existing:
            report.skip(row_number, t("csv_duplicate"))
            continue
        existing.add(key)

        values = validated.model_dump()  # applied_at stays empty if the file has none
        last_activity = _parse_datetime(cells.get("last_activity_at", "")) or utcnow()
        app = Application(
            user_id=user_id,
            archived=cells.get("archived", "").strip().lower() in _TRUE,
            last_activity_at=last_activity,
            **values,
        )
        new_apps.append((app, validated.status))

    db.add_all(app for app, _ in new_apps)
    db.flush()
    db.add_all(status_initial_event(app.id, status) for app, status in new_apps)
    db.commit()
    report.added = len(new_apps)
    return report

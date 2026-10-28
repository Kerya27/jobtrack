"""Statistics for /stats.

Data is loaded with two queries (applications + status-change events) and aggregated in
Python by the pure function ``compute_stats`` — simple to test on a known data set, and
fast enough for thousands of rows.

Definitions:
- *applied*: the application left the "saved" stage (any status other than saved was reached).
- *responded*: the company reacted — replied, invited to an interview/test task, made an offer,
  or rejected. Ghosted/withdrawn are not responses.
- Funnel stages use the *furthest* stage ever reached, rebuilt from the status history, so an
  application that went to an interview and was then rejected still counts as an interview.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, Event, EventType
from app.timeutil import to_local

S = ApplicationStatus
STAGE_RANK: dict[ApplicationStatus, int] = {
    S.SAVED: 0,
    S.APPLIED: 1,
    S.REJECTED: 1,
    S.GHOSTED: 1,
    S.WITHDRAWN: 1,
    S.REPLIED: 2,
    S.INTERVIEW: 3,
    S.TEST_TASK: 3,
    S.OFFER: 4,
}
RESPONSE_STATUSES = frozenset({S.REPLIED, S.INTERVIEW, S.TEST_TASK, S.OFFER, S.REJECTED})
CLOSED_STATUSES = frozenset({S.REJECTED, S.GHOSTED, S.WITHDRAWN})
WEEKS = 12


@dataclass(frozen=True)
class AppFacts:
    id: int
    status: ApplicationStatus
    source: str | None
    resume_version: str | None
    applied_at: date | None
    archived: bool


@dataclass(frozen=True)
class StatusEvent:
    application_id: int
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    created_at: datetime


@dataclass(frozen=True)
class FunnelStep:
    key: str  # i18n key
    count: int
    pct: float | None  # share of applied


@dataclass(frozen=True)
class GroupRow:
    name: str | None
    applied: int
    responded: int
    interviews: int

    @property
    def response_rate(self) -> float | None:
        return self.responded / self.applied if self.applied else None

    @property
    def interview_rate(self) -> float | None:
        return self.interviews / self.applied if self.applied else None


@dataclass(frozen=True)
class WeekBucket:
    start: date
    count: int


@dataclass(frozen=True)
class Stats:
    total: int
    active: int
    by_status: list[tuple[ApplicationStatus, int]]
    applied: int
    responded: int
    funnel: list[FunnelStep]
    by_source: list[GroupRow]
    by_resume: list[GroupRow]
    weekly: list[WeekBucket]
    avg_days_to_response: float | None

    @property
    def response_rate(self) -> float | None:
        return self.responded / self.applied if self.applied else None

    @property
    def weekly_max(self) -> int:
        return max((w.count for w in self.weekly), default=0)


@dataclass
class _Progress:
    rank: int
    responded: bool


def _progress(app: AppFacts, events: Iterable[StatusEvent]) -> _Progress:
    reached = {app.status, *(e.to_status for e in events)}
    rank = max(STAGE_RANK[s] for s in reached)
    responded = rank >= 1 and (rank >= STAGE_RANK[S.REPLIED] or S.REJECTED in reached)
    return _Progress(rank=rank, responded=responded)


def _groups(apps: Sequence[AppFacts], progress: dict[int, _Progress], key: str) -> list[GroupRow]:
    buckets: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    for app in apps:
        p = progress[app.id]
        if p.rank < 1:
            continue
        bucket = buckets[getattr(app, key) or None]
        bucket[0] += 1
        bucket[1] += p.responded
        bucket[2] += p.rank >= STAGE_RANK[S.INTERVIEW]
    rows = [GroupRow(name, *counts) for name, counts in buckets.items()]
    return sorted(rows, key=lambda r: (-r.applied, r.name is None, r.name or ""))


def _pct(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def compute_stats(apps: Sequence[AppFacts], events: Sequence[StatusEvent], today: date) -> Stats:
    events_by_app: dict[int, list[StatusEvent]] = defaultdict(list)
    for event in events:
        events_by_app[event.application_id].append(event)

    progress = {a.id: _progress(a, events_by_app[a.id]) for a in apps}
    applied_apps = [a for a in apps if progress[a.id].rank >= 1]
    applied = len(applied_apps)
    responded = sum(progress[a.id].responded for a in apps)
    interviews = sum(progress[a.id].rank >= STAGE_RANK[S.INTERVIEW] for a in apps)
    offers = sum(progress[a.id].rank >= STAGE_RANK[S.OFFER] for a in apps)

    status_counts: dict[ApplicationStatus, int] = defaultdict(int)
    for app in apps:
        status_counts[app.status] += 1

    # weekly histogram (weeks start on Monday)
    this_week = today - timedelta(days=today.weekday())
    starts = [this_week - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]
    per_week: dict[date, int] = defaultdict(int)
    for app in applied_apps:
        if app.applied_at and app.applied_at >= starts[0]:
            per_week[app.applied_at - timedelta(days=app.applied_at.weekday())] += 1

    # days from applying to the first response (status changes after creation only)
    delays: list[int] = []
    for app in applied_apps:
        if not progress[app.id].responded or app.applied_at is None:
            continue
        responses = [
            e.created_at
            for e in events_by_app[app.id]
            if e.from_status is not None and e.to_status in RESPONSE_STATUSES
        ]
        if responses:
            first = to_local(min(responses)).date()
            delays.append(max((first - app.applied_at).days, 0))

    return Stats(
        total=len(apps),
        active=sum(1 for a in apps if not a.archived and a.status not in CLOSED_STATUSES),
        by_status=[(s, status_counts[s]) for s in ApplicationStatus if status_counts[s]],
        applied=applied,
        responded=responded,
        funnel=[
            FunnelStep("funnel_applied", applied, _pct(applied, applied)),
            FunnelStep("funnel_replied", responded, _pct(responded, applied)),
            FunnelStep("funnel_interview", interviews, _pct(interviews, applied)),
            FunnelStep("funnel_offer", offers, _pct(offers, applied)),
        ],
        by_source=_groups(apps, progress, "source"),
        by_resume=_groups(apps, progress, "resume_version"),
        weekly=[WeekBucket(start, per_week[start]) for start in starts],
        avg_days_to_response=sum(delays) / len(delays) if delays else None,
    )


def load_stats(db: Session, user_id: int, today: date) -> Stats:
    app_rows = db.execute(
        select(
            Application.id,
            Application.status,
            Application.source,
            Application.resume_version,
            Application.applied_at,
            Application.archived,
        ).where(Application.user_id == user_id)
    ).all()
    event_rows = db.execute(
        select(Event.application_id, Event.from_status, Event.to_status, Event.created_at)
        .join(Application, Application.id == Event.application_id)
        .where(
            Application.user_id == user_id,
            Event.type == EventType.STATUS_CHANGE,
            Event.to_status.is_not(None),
        )
    ).all()
    return compute_stats(
        [AppFacts(*row) for row in app_rows],
        [StatusEvent(*row) for row in event_rows],
        today,
    )

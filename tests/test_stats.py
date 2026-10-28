from datetime import UTC, date, datetime

from app.models import ApplicationStatus as S
from app.services.stats import AppFacts, StatusEvent, compute_stats
from tests.conftest import Client

TODAY = date(2026, 10, 28)  # a Wednesday


def ts(day: int, month: int = 10) -> datetime:
    return datetime(2026, month, day, 10, 0, tzinfo=UTC)


def app(
    id: int,
    status: S,
    *,
    source: str | None = "Jobs.cz",
    resume: str | None = "v1",
    applied: date | None = date(2026, 10, 1),
    archived: bool = False,
) -> AppFacts:
    return AppFacts(id, status, source, resume, applied, archived)


def created(app_id: int, status: S, day: int = 1) -> StatusEvent:
    return StatusEvent(app_id, None, status, ts(day))


def moved(app_id: int, old: S, new: S, day: int) -> StatusEvent:
    return StatusEvent(app_id, old, new, ts(day))


def build_dataset() -> tuple[list[AppFacts], list[StatusEvent]]:
    apps = [
        app(1, S.APPLIED),  # no answer yet
        app(2, S.REJECTED, applied=date(2026, 10, 5)),  # rejected right away → a response
        app(3, S.REJECTED, resume="v2", source="LinkedIn"),  # interview, then rejected
        app(4, S.OFFER, resume="v2", source="LinkedIn", applied=date(2026, 10, 20)),
        app(5, S.GHOSTED, resume=None, source=None, archived=True),  # no response
        app(6, S.SAVED, applied=None),  # never applied: excluded from the funnel
    ]
    events = [
        created(1, S.APPLIED),
        created(2, S.APPLIED, day=5),
        moved(2, S.APPLIED, S.REJECTED, day=9),  # 4 days
        created(3, S.APPLIED),
        moved(3, S.APPLIED, S.INTERVIEW, day=3),  # 2 days
        moved(3, S.INTERVIEW, S.REJECTED, day=10),
        created(4, S.APPLIED, day=20),
        moved(4, S.APPLIED, S.REPLIED, day=26),  # 6 days
        moved(4, S.REPLIED, S.INTERVIEW, day=27),
        moved(4, S.INTERVIEW, S.OFFER, day=28),
        created(5, S.APPLIED),
        moved(5, S.APPLIED, S.GHOSTED, day=20),
        created(6, S.SAVED),
    ]
    return apps, events


def test_totals_and_funnel() -> None:
    stats = compute_stats(*build_dataset(), today=TODAY)
    assert stats.total == 6
    assert stats.active == 3  # 1 applied, 4 offer, 6 saved (5 is archived, 2/3 rejected)
    assert dict(stats.by_status) == {
        S.SAVED: 1,
        S.APPLIED: 1,
        S.OFFER: 1,
        S.REJECTED: 2,
        S.GHOSTED: 1,
    }

    funnel = {step.key: (step.count, step.pct) for step in stats.funnel}
    assert funnel == {
        "funnel_applied": (5, 1.0),
        "funnel_replied": (3, 0.6),  # 2 (rejection), 3, 4
        "funnel_interview": (2, 0.4),  # 3 counts although it ended as a rejection
        "funnel_offer": (1, 0.2),
    }
    assert stats.response_rate == 0.6
    assert stats.avg_days_to_response == 4.0  # (4 + 2 + 6) / 3


def test_response_rate_by_resume_version_and_source() -> None:
    stats = compute_stats(*build_dataset(), today=TODAY)
    by_resume = {r.name: (r.applied, r.responded, r.interviews) for r in stats.by_resume}
    assert by_resume == {"v1": (2, 1, 0), "v2": (2, 2, 2), None: (1, 0, 0)}
    v2 = next(r for r in stats.by_resume if r.name == "v2")
    assert v2.response_rate == 1.0 and v2.interview_rate == 1.0

    by_source = {r.name: (r.applied, r.responded) for r in stats.by_source}
    assert by_source == {"Jobs.cz": (2, 1), "LinkedIn": (2, 2), None: (1, 0)}
    assert stats.by_source[-1].name is None  # "not set" goes last


def test_weekly_histogram() -> None:
    stats = compute_stats(*build_dataset(), today=TODAY)
    assert len(stats.weekly) == 12
    assert stats.weekly[-1].start == date(2026, 10, 26)  # Monday of the current week
    counts = {w.start: w.count for w in stats.weekly}
    assert counts[date(2026, 9, 28)] == 3  # apps 1, 3, 5 applied on Thu 1 Oct
    assert counts[date(2026, 10, 5)] == 1
    assert counts[date(2026, 10, 19)] == 1
    assert sum(counts.values()) == 5


def test_empty_stats() -> None:
    stats = compute_stats([], [], today=TODAY)
    assert stats.total == 0
    assert stats.response_rate is None
    assert stats.avg_days_to_response is None


def test_stats_page_end_to_end(alice: Client) -> None:
    assert "появится" in alice.get("/stats").text
    first = alice.create_app(resume_version="backend-v2", source="Jobs.cz")
    alice.create_app(resume_version="backend-v1", source="LinkedIn")
    alice.htmx(f"/applications/{first['id']}/status", {"new_status": "interview"})

    page = alice.get("/stats").text
    assert "Воронка" in page
    assert "backend-v2" in page and "backend-v1" in page
    assert "50%" in page  # overall response rate: 1 of 2

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Application, Event
from app.timeutil import as_utc, today_local
from tests.conftest import Client


def test_create_via_htmx_form_and_list(alice: Client) -> None:
    response = alice.htmx(
        "/applications",
        {
            "company": "Kiwi.com",
            "position": "Python Engineer",
            "salary_min": "80 000",
            "salary_max": "100000",
            "salary_currency": "czk",
            "work_mode": "hybrid",
            "resume_version": "backend-v2",
        },
    )
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/"

    page = alice.get("/").text
    assert "Kiwi.com" in page and "Python Engineer" in page
    assert "80 000–100 000 CZK" in page

    item = alice.get("/api/v1/applications").json()["items"][0]
    assert item["status"] == "applied"
    assert item["applied_at"] == today_local().isoformat()
    assert item["salary_currency"] == "CZK"


def test_only_save_creates_saved_without_applied_date(alice: Client) -> None:
    alice.htmx("/applications", {"company": "A", "position": "B", "only_save": "1"})
    item = alice.get("/api/v1/applications").json()["items"][0]
    assert item["status"] == "saved"
    assert item["applied_at"] is None


def test_validation_errors_are_shown_next_to_fields(alice: Client) -> None:
    response = alice.htmx(
        "/applications",
        {
            "company": "",
            "position": "Dev",
            "salary_min": "90000",
            "salary_max": "50000",
            "contact_email": "nope",
            "url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="company-error"' in html
    assert 'id="salary_max-error"' in html
    assert 'id="contact_email-error"' in html
    assert 'id="url-error"' in html
    assert 'value="Dev"' in html  # entered values are kept
    assert alice.get("/api/v1/applications").json()["total"] == 0


def test_card_shows_fields_and_timeline(alice: Client) -> None:
    app = alice.create_app(company="Seznam.cz", notes="Ask about Go")
    page = alice.get(f"/applications/{app['id']}").text
    assert "Seznam.cz" in page
    assert "Ask about Go" in page
    assert "Создан со статусом" in page


def test_update_all_fields(alice: Client) -> None:
    app = alice.create_app()
    response = alice.form(
        f"/applications/{app['id']}",
        {
            "company": "New Co",
            "position": "Senior Dev",
            "location": "Brno",
            "work_mode": "remote",
            "status": "interview",
            "notes": "x",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    data = alice.get(f"/api/v1/applications/{app['id']}").json()
    assert (data["company"], data["position"], data["location"]) == ("New Co", "Senior Dev", "Brno")
    assert data["work_mode"] == "remote"
    assert data["status"] == "interview"


def test_update_with_invalid_data_shows_errors(alice: Client) -> None:
    app = alice.create_app()
    response = alice.form(f"/applications/{app['id']}", {"company": "", "position": "x"})
    assert response.status_code == 422
    assert 'id="company-error"' in response.text


def test_status_change_creates_event_and_bumps_last_activity(alice: Client, db: Session) -> None:
    app = alice.create_app()
    old = datetime.now(UTC) - timedelta(days=5)
    db.execute(update(Application).where(Application.id == app["id"]).values(last_activity_at=old))
    db.commit()

    response = alice.htmx(f"/applications/{app['id']}/status", {"new_status": "interview"})
    assert response.status_code == 200
    assert 'id="app-' in response.text  # updated row
    assert 'hx-swap-oob="true"' in response.text  # refreshed counters

    db.expire_all()
    stored = db.get(Application, app["id"])
    assert stored is not None and stored.status == "interview"
    assert as_utc(stored.last_activity_at) > old + timedelta(days=4)

    events = db.scalars(
        select(Event).where(Event.application_id == app["id"]).order_by(Event.id)
    ).all()
    assert [(e.type, e.from_status, e.to_status) for e in events] == [
        ("status_change", None, "applied"),
        ("status_change", "applied", "interview"),
    ]


def test_same_status_does_not_create_event(alice: Client) -> None:
    app = alice.create_app()
    alice.htmx(f"/applications/{app['id']}/status", {"new_status": "applied"})
    assert len(alice.get(f"/api/v1/applications/{app['id']}/events").json()) == 1


def test_saved_to_applied_sets_applied_date(alice: Client) -> None:
    app = alice.create_app(status="saved")
    assert app["applied_at"] is None
    alice.htmx(f"/applications/{app['id']}/status", {"new_status": "applied"})
    assert (
        alice.get(f"/api/v1/applications/{app['id']}").json()["applied_at"]
        == today_local().isoformat()
    )


def test_add_note_event(alice: Client) -> None:
    app = alice.create_app()
    response = alice.htmx(
        f"/applications/{app['id']}/events",
        {"type": "interview", "text": "Call with CTO on Thursday"},
    )
    assert "Call with CTO on Thursday" in response.text
    empty = alice.htmx(f"/applications/{app['id']}/events", {"type": "note", "text": "   "})
    assert "Напиши текст события" in empty.text
    forged = alice.htmx(
        f"/applications/{app['id']}/events", {"type": "status_change", "text": "fake"}
    )
    assert "Напиши текст события" in forged.text
    events = alice.get(f"/api/v1/applications/{app['id']}/events").json()
    assert [e["type"] for e in events] == ["interview", "status_change"]  # newest first


def test_archive_hides_from_default_list(alice: Client) -> None:
    app = alice.create_app(company="Archived Inc")
    alice.form(f"/applications/{app['id']}/archive")
    assert "Archived Inc" not in alice.get("/").text
    assert "Archived Inc" in alice.get("/?archived=1").text
    alice.form(f"/applications/{app['id']}/archive")  # toggles back
    assert "Archived Inc" in alice.get("/").text


def test_delete_removes_application_and_events(alice: Client, db: Session) -> None:
    app = alice.create_app()
    alice.form(f"/applications/{app['id']}/events", {"text": "note"})
    response = alice.form(f"/applications/{app['id']}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert alice.get(f"/applications/{app['id']}").status_code == 404
    assert db.scalars(select(Event).where(Event.application_id == app["id"])).all() == []


def test_filters_status_and_search(alice: Client) -> None:
    alice.create_app(company="Škoda Auto", position="Разработчик Python")
    alice.create_app(company="Avast", position="QA", status="rejected")
    htmx = {"HX-Request": "true", "HX-Target": "results"}

    rejected = alice.get("/?status=rejected", headers=htmx).text
    assert "Avast" in rejected and "Škoda" not in rejected

    # case-insensitive, non-ASCII search (works on SQLite too)
    found = alice.get("/?q=РАЗРАБОТЧИК", headers=htmx).text
    assert "Škoda" in found and "Avast" not in found
    assert "Škoda" in alice.get("/?q=ŠKODA", headers=htmx).text
    assert "Avast" not in alice.get("/?q=%25", headers=htmx).text  # LIKE wildcards are escaped


def test_list_is_sorted_by_last_activity(alice: Client) -> None:
    first = alice.create_app(company="First")
    alice.create_app(company="Second")
    alice.form(f"/applications/{first['id']}/events", {"text": "bump"})
    items = alice.get("/api/v1/applications").json()["items"]
    assert [i["company"] for i in items] == ["First", "Second"]


def test_api_crud(alice: Client) -> None:
    created = alice.create_app(salary_min=1000, salary_max=2000, salary_currency="EUR")
    app_id = created["id"]

    patched = alice.api(
        "PATCH", f"/api/v1/applications/{app_id}", json={"status": "offer", "archived": True}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "offer" and patched.json()["archived"] is True

    # the merged object is validated as a whole: max < existing min is rejected
    bad = alice.api("PATCH", f"/api/v1/applications/{app_id}", json={"salary_max": 10})
    assert bad.status_code == 422

    assert alice.api("POST", "/api/v1/applications", json={"company": "x"}).status_code == 422
    assert alice.api("DELETE", f"/api/v1/applications/{app_id}").status_code == 204
    assert alice.get(f"/api/v1/applications/{app_id}").status_code == 404


def test_unknown_application_is_404(alice: Client) -> None:
    assert alice.get("/applications/999999").status_code == 404
    assert alice.get("/no-such-page").status_code == 404

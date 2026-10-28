import csv
import io

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application, Event, User
from tests.conftest import Client


def rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))))


def upload(client: Client, content: bytes, name: str = "data.csv"):  # type: ignore[no-untyped-def]
    return client.post(
        "/import",
        data={"csrf_token": client.token},
        files={"file": (name, content, "text/csv")},
    )


def test_export_import_round_trip(alice: Client, bob: Client) -> None:
    alice.create_app(
        company='Firma "Uvozovky", a.s.',
        position="Vývojář / Розробник",
        url="https://www.jobs.cz/rpd/1/",
        source="Jobs.cz",
        location="Praha",
        work_mode="hybrid",
        salary_min=60000,
        salary_max=80000,
        salary_currency="CZK",
        resume_version="backend-v2",
        contact_name="Jana Nováková",
        contact_email="jana@firma.cz",
        notes="Line 1\nLine 2, with comma",
        description='=HYPERLINK("http://evil")',
        applied_at="2026-10-01",
        status="interview",
    )
    saved = alice.create_app(company="Saved Co", position="Later", status="saved")
    alice.api("PATCH", f"/api/v1/applications/{saved['id']}", json={"archived": True})

    exported = alice.get("/export.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.text.startswith("﻿")
    assert "'=HYPERLINK" in exported.text  # formula escaped for Excel

    report = upload(bob, exported.content)
    assert "Добавлено: 2, пропущено: 0" in report.text

    assert rows(bob.get("/export.csv").text) == rows(exported.text)
    bob_items = bob.get("/api/v1/applications?archived=true").json()["items"]
    interview = next(i for i in bob_items if i["status"] == "interview")
    assert interview["description"] == '=HYPERLINK("http://evil")'  # restored on import

    # importing the same file again: everything is a duplicate
    again = upload(bob, exported.content)
    assert "Добавлено: 0, пропущено: 2" in again.text


def test_import_excel_style_file(alice: Client) -> None:
    content = (
        "Company;Position;Status;Applied_at;Salary_min;Work_mode\n"
        "Avast;QA Engineer;Интервью;05.10.2026;55 000;Гибрид\n"
        ";Missing company;applied;;;\n"
        "Bad Co;Dev;not-a-status;;;\n"
        "Bad Date;Dev;applied;32.13.2026;;\n"
        "\n"
    ).encode()
    response = upload(alice, content)
    assert "Добавлено: 1, пропущено: 3" in response.text
    assert (
        "строка 3" in response.text and "строка 4" in response.text and "строка 5" in response.text
    )
    item = alice.get("/api/v1/applications").json()["items"][0]
    assert (item["company"], item["status"], item["applied_at"]) == (
        "Avast",
        "interview",
        "2026-10-05",
    )
    assert (item["salary_min"], item["work_mode"]) == (55000, "hybrid")


def test_import_rejects_bad_files(alice: Client) -> None:
    assert "UTF-8" in upload(alice, "company,position\nŠkoda,Dev\n".encode("cp1250")).text
    assert "company и position" in upload(alice, b"name,title\nA,B\n").text
    no_file = alice.post("/import", data={"csrf_token": alice.token})
    assert no_file.status_code == 422


def test_change_password(alice: Client) -> None:
    wrong = alice.form(
        "/settings/password",
        {
            "current_password": "nope",
            "new_password": "new-password-1",
            "password_repeat": "new-password-1",
        },
    )
    assert wrong.status_code == 422 and "Неверный пароль" in wrong.text
    short = alice.form(
        "/settings/password",
        {
            "current_password": "correct-horse-42",
            "new_password": "short",
            "password_repeat": "short",
        },
    )
    assert short.status_code == 422

    ok = alice.form(
        "/settings/password",
        {
            "current_password": "correct-horse-42",
            "new_password": "new-password-1",
            "password_repeat": "new-password-1",
        },
        follow_redirects=False,
    )
    assert ok.status_code == 303
    alice.form("/logout")
    token = alice.refresh_token()
    login = alice.post(
        "/login",
        data={"email": "alice@example.com", "password": "new-password-1", "csrf_token": token},
        follow_redirects=False,
    )
    assert login.status_code == 303


def test_delete_account_removes_everything(alice: Client, bob: Client, db: Session) -> None:
    app = alice.create_app()
    alice.form(f"/applications/{app['id']}/events", {"text": "note"})
    bob.create_app()

    wrong = alice.form("/settings/delete", {"password": "nope"})
    assert wrong.status_code == 422

    response = alice.form(
        "/settings/delete", {"password": "correct-horse-42"}, follow_redirects=False
    )
    assert response.headers["location"] == "/login"
    assert alice.get("/", follow_redirects=False).status_code == 303  # logged out

    assert db.scalar(select(User).where(User.email == "alice@example.com")) is None
    assert (
        db.scalar(select(func.count()).select_from(Event).where(Event.application_id == app["id"]))
        == 0
    )
    assert db.scalar(select(func.count()).select_from(Application)) == 1  # Bob's stays


def test_settings_page(alice: Client) -> None:
    page = alice.get("/settings").text
    assert "alice@example.com" in page and "/export.csv" in page

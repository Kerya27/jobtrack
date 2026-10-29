"""Friends (read-only shared trackers), UI language, migrations."""

import re
import tempfile
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.db import Base
from app.i18n import EVENT_TYPE_LABELS, STATUS_LABELS, STRINGS, WORK_MODE_LABELS
from app.models import Friendship
from tests.conftest import ROOT, Client, _logged_in_client


def befriend(a: Client, b: Client, b_email: str) -> None:
    a.form("/friends/request", {"email": b_email})
    friendship_id = int(re.findall(r"/friends/(\d+)/accept", b.get("/friends").text)[0])
    assert b.form(f"/friends/{friendship_id}/accept", follow_redirects=False).status_code == 303


def user_id(client: Client) -> int:
    return int(client.get("/api/v1/me").json()["id"])


# ---------- friends ----------


def test_friend_request_flow_and_read_only_access(alice: Client, bob: Client) -> None:
    bob_app = bob.create_app(company="Kiwi.com", position="Backend", notes="HR: Jana")
    bob_id = user_id(bob)

    # before accepting: nothing is visible
    alice.form("/friends/request", {"email": "BOB@example.com "})
    assert alice.get(f"/friends/{bob_id}").status_code == 404
    assert "Friend requests waiting for you: 1" in bob.get("/").text

    incoming = bob.get("/friends").text
    friendship_id = int(re.findall(r"/friends/(\d+)/accept", incoming)[0])
    # Alice can't accept her own request on Bob's behalf
    assert alice.form(f"/friends/{friendship_id}/accept").status_code == 404
    bob.form(f"/friends/{friendship_id}/accept")

    tracker = alice.get(f"/friends/{bob_id}").text
    assert "Backend" in tracker and "Kiwi.com" in tracker
    assert 'name="new_status"' not in tracker and 'form="bulk"' not in tracker  # read-only
    card = alice.get(f"/friends/{bob_id}/applications/{bob_app['id']}").text
    assert "HR: Jana" in card  # no privacy between friends, by design

    # still can't change Bob's data through the normal endpoints
    assert alice.form(f"/applications/{bob_app['id']}/delete").status_code == 404
    assert alice.get(f"/api/v1/applications/{bob_app['id']}").status_code == 404

    # board and filters work in the friend's tracker too
    board = alice.get(
        f"/friends/{bob_id}?view=board", headers={"HX-Request": "true", "HX-Target": "results"}
    ).text
    assert 'data-status="applied"' in board and "data-board" not in board


def test_strangers_and_removed_friends_see_nothing(alice: Client, bob: Client) -> None:
    bob_app = bob.create_app()
    bob_id = user_id(bob)
    with _logged_in_client("carol@example.com") as carol:
        assert carol.get(f"/friends/{bob_id}").status_code == 404
        assert carol.get(f"/friends/{bob_id}/applications/{bob_app['id']}").status_code == 404

    befriend(alice, bob, "bob@example.com")
    assert alice.get(f"/friends/{bob_id}").status_code == 200
    friendship_id = int(re.findall(r"/friends/(\d+)/remove", alice.get("/friends").text)[0])
    alice.form(f"/friends/{friendship_id}/remove")
    assert alice.get(f"/friends/{bob_id}").status_code == 404
    assert bob.get(f"/friends/{user_id(alice)}").status_code == 404


def test_request_outcomes_do_not_reveal_accounts(alice: Client, bob: Client, db: Session) -> None:
    sent = alice.form("/friends/request", {"email": "nobody@example.com"}).text
    assert "Request sent" in sent
    assert db.scalar(select(func.count()).select_from(Friendship)) == 0
    assert "your own email" in alice.form("/friends/request", {"email": "alice@example.com"}).text

    alice.form("/friends/request", {"email": "bob@example.com"})
    assert "already" in alice.form("/friends/request", {"email": "bob@example.com"}).text
    # Bob asking back accepts the pending request instead of creating a second one
    assert "now friends" in bob.form("/friends/request", {"email": "alice@example.com"}).text
    assert db.scalar(select(func.count()).select_from(Friendship)) == 1


def test_friend_applied_badge(alice: Client, bob: Client) -> None:
    befriend(alice, bob, "bob@example.com")
    bob.create_app(company="CyberCare, s.r.o.", position="Support")
    bob.create_app(company="Saved Only", position="X", status="saved")
    alice.create_app(company="cybercare", position="Analyst")
    alice.create_app(company="Saved Only", position="Y")
    page = alice.get("/").text
    assert "Also applied: bob" in page
    assert page.count("Also applied") == 1  # Bob only saved the second one, didn't apply


def test_deleting_an_account_removes_friendships(alice: Client, bob: Client, db: Session) -> None:
    befriend(alice, bob, "bob@example.com")
    bob.form("/settings/delete", {"password": "correct-horse-42"})
    assert db.scalar(select(func.count()).select_from(Friendship)) == 0
    assert "No friends yet" in alice.get("/friends").text


# ---------- language ----------


def test_all_languages_have_the_same_keys() -> None:
    assert set(STRINGS["en"]) == set(STRINGS["ru"])
    for labels in (STATUS_LABELS, WORK_MODE_LABELS, EVENT_TYPE_LABELS):
        assert set(labels["en"]) == set(labels["ru"])


def test_language_switch_is_remembered(alice: Client) -> None:
    app = alice.create_app()
    assert 'lang="en"' in alice.get("/").text
    response = alice.form("/language", {"lang": "ru", "next": "/stats"}, follow_redirects=False)
    assert response.headers["location"] == "/stats"
    page = alice.get(f"/applications/{app['id']}").text
    assert 'lang="ru"' in page and "История" in page
    # status events are rendered in the current language, not stored text
    assert "Создан со статусом «Отклик отправлен»" in page

    alice.form("/logout")
    alice.refresh_token()
    assert "Вход" in alice.get("/login").text  # the language survives logout
    alice.form("/language", {"lang": "en"})
    assert "Log in" in alice.get("/login").text
    alice.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "correct-horse-42",
            "csrf_token": alice.refresh_token(),
        },
    )
    assert 'lang="ru"' in alice.get("/").text  # the account's language is restored on login


def test_language_switch_never_redirects_off_site(anon: Client) -> None:
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        response = anon.form("/language", {"lang": "ru", "next": target}, follow_redirects=False)
        assert response.headers["location"] == "/"
    assert anon.form("/language", {"lang": "xx"}, follow_redirects=False).status_code == 303
    assert 'lang="en"' in anon.get("/login").text  # unknown language → default


def test_csv_import_accepts_english_and_russian_labels(alice: Client) -> None:
    content = "company,position,status,work_mode\nA,Dev,Interview,Hybrid\nB,Dev,Отказ,Удалёнка\n"
    alice.post(
        "/import",
        data={"csrf_token": alice.token},
        files={"file": ("x.csv", content.encode(), "text/csv")},
    )
    items = {i["company"]: i for i in alice.get("/api/v1/applications").json()["items"]}
    assert (items["A"]["status"], items["A"]["work_mode"]) == ("interview", "hybrid")
    assert (items["B"]["status"], items["B"]["work_mode"]) == ("rejected", "remote")


# ---------- migrations ----------


def test_migrations_match_models_and_downgrade_cleanly() -> None:
    # Separate throwaway SQLite file, so the shared test database is not touched.
    path = Path(tempfile.mkdtemp(prefix="jobtrack-mig-")) / "m.db"
    url = f"sqlite:///{path}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    engine.dispose()
    command.downgrade(config, "0001")
    command.upgrade(config, "head")

"""Friends (read-only shared trackers), UI language, migrations."""

import re
import tempfile
from datetime import timedelta
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
from app.timeutil import today_local
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
    bob.create_app(company="Saved", position="Saved role", status="saved")
    bob.create_app(
        company="Old",
        position="Old role",
        applied_at=(today_local() - timedelta(days=1)).isoformat(),
    )
    bob_id = user_id(bob)

    # before accepting: nothing is visible
    alice.form("/friends/request", {"email": "BOB@example.com "})
    assert alice.get(f"/friends/{bob_id}").status_code == 404
    home = bob.get("/").text
    assert 'class="icon-btn notification-toggle"' in home and 'class="nav-count"' in home
    assert 'class="daily-goal' in home
    assert 'class="daily-goal-divider"> / </span>5' in home
    assert 'aria-label="Today&#39;s applications: 1 of 5"' in home
    assert "incoming friend requests" in home
    assert 'class="flash flash-info"' not in home
    stats_page = bob.get("/stats").text
    assert 'class="daily-goal-divider"> / </span>5' in stats_page
    assert 'class="nav-count"' in stats_page and ">1</span>" in stats_page
    friends_link_start = stats_page.index('<a href="/friends" class="nav-item')
    friends_link = stats_page[friends_link_start : stats_page.index("</a>", friends_link_start)]
    assert 'class="nav-count" aria-hidden="true">1</span>' in friends_link
    assert "Pending friend requests: 1" in stats_page

    incoming = bob.get("/friends").text
    assert 'class="success small"' in incoming
    friendship_id = int(re.findall(r"/friends/(\d+)/accept", incoming)[0])
    # Alice can't accept her own request on Bob's behalf
    assert alice.form(f"/friends/{friendship_id}/accept").status_code == 404
    bob.form(f"/friends/{friendship_id}/accept")
    stats_after_accept = bob.get("/stats").text
    assert 'class="nav-count"' not in stats_after_accept
    assert 'href="/friends"' in stats_after_accept
    friends_page = alice.get("/friends").text
    assert friends_page.index('class="friend-invite"') < friends_page.index('class="friend-cards"')
    assert 'class="friend-avatar"' in friends_page
    assert 'id="friend-remove-dialog"' in friends_page
    assert 'data-confirm-dialog="friend-remove-dialog"' in friends_page
    assert "Remove this friend?" in friends_page
    assert "onsubmit=" not in friends_page
    assert "3 applications" in friends_page
    assert "Today: 1 sent" in friends_page

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


def test_duo_invitation_acceptance_and_leave(alice: Client, bob: Client) -> None:
    befriend(alice, bob, "bob@example.com")
    alice.create_app(company="Alice job")
    bob.create_app(company="Bob job")

    sent = alice.form("/duo/invite", {"friend_id": user_id(bob)}, follow_redirects=False)
    assert sent.status_code == 303
    alice_home = alice.get("/").text
    assert "Waiting for bob to accept" in alice_home
    assert "Alice job" in alice_home

    bob_home = bob.get("/").text
    assert "alice challenges you" in bob_home
    assert "Accept" in bob_home and "Decline" in bob_home
    battle_id = int(re.findall(r"/duo/(\d+)/accept", bob_home)[0])
    assert 'class="nav-count">1</span>' in bob_home
    activity_card = re.search(
        r'<section class="panel activity-card".*?</section>', bob_home, re.DOTALL
    )
    assert activity_card
    assert "<strong>alice</strong>" in activity_card.group(0)
    assert f'action="/duo/{battle_id}/accept"' in activity_card.group(0)
    assert 'class="activity-accept"' in activity_card.group(0)

    bob.form(f"/duo/{battle_id}/accept")
    alice_home = alice.get("/").text
    bob_home = bob.get("/").text
    assert "Leave battle" in alice_home and "⚡" in alice_home
    assert "Leave battle" in bob_home and "⚡" in bob_home
    for page in (alice_home, bob_home):
        activity_card = re.search(
            r'<section class="panel activity-card".*?</section>', page, re.DOTALL
        )
        assert activity_card
        player_blocks = re.findall(
            r'<div class="(activity-player.*?)</div>', activity_card.group(0), re.DOTALL
        )
        assert len(player_blocks) == 2
        assert all("is-leading" not in block for block in player_blocks)
        assert all(re.search(r"<strong>1</strong>", block) for block in player_blocks)
        assert all(not re.search(r"<strong>\d+/\d+</strong>", block) for block in player_blocks)

    alice.create_app(company="Alice second job")
    alice_home = alice.get("/").text
    activity_card = re.search(
        r'<section class="panel activity-card".*?</section>', alice_home, re.DOTALL
    )
    assert activity_card
    player_blocks = re.findall(
        r'<div class="(activity-player.*?)</div>', activity_card.group(0), re.DOTALL
    )
    assert len(player_blocks) == 2
    assert player_blocks[0].startswith("activity-player is-leading")
    assert "<strong>2</strong>" in player_blocks[0]
    assert player_blocks[1].startswith("activity-player")
    assert "is-leading" not in player_blocks[1]
    assert "<strong>1</strong>" in player_blocks[1]

    alice.form(f"/duo/{battle_id}/leave")
    assert "Challenge" in alice.get("/").text
    assert "Challenge" in bob.get("/").text


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

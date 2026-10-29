"""Filters, sorting, the "Today" chip, the board view, bulk actions, duplicates, portal, clone."""

import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application, Event, EventType
from app.timeutil import today_local
from tests.conftest import Client

HTMX_RESULTS = {"HX-Request": "true", "HX-Target": "results"}


def titles(html: str) -> list[str]:
    return re.findall(r'class="app-title">([^<]+)<', html)


# ---------- filters, sorting, today ----------


def test_filters_by_work_mode_source_resume_and_sent(alice: Client) -> None:
    yesterday = (today_local() - timedelta(days=1)).isoformat()
    alice.create_app(position="Remote today", work_mode="remote", source="DOU")
    alice.create_app(position="Office old", work_mode="onsite", applied_at=yesterday)
    alice.create_app(position="Saved one", status="saved", resume_version="v2")
    alice.create_app(position="Hybrid v2", work_mode="hybrid", resume_version="v2")

    def shown(query: str) -> set[str]:
        return set(titles(alice.get(f"/?{query}", headers=HTMX_RESULTS).text))

    assert shown("work_mode=remote") == {"Remote today"}
    assert shown("source=DOU") == {"Remote today"}
    assert shown("resume=v2") == {"Saved one", "Hybrid v2"}
    # "applied today" leaves out saved-only and older applications
    assert shown("sent=today") == {"Remote today", "Hybrid v2"}
    assert shown("sent=7d") == {"Remote today", "Hybrid v2", "Office old"}
    # junk values are ignored instead of failing
    assert len(shown("work_mode=spaceship&sent=forever&sort=random")) == 4


def test_sorting(alice: Client) -> None:
    alice.create_app(company="Beta", position="B", salary_min=3000)
    alice.create_app(company="alpha", position="A", salary_min=1000, salary_max=5000)
    alice.create_app(company="Gamma", position="G")
    assert titles(alice.get("/?sort=company", headers=HTMX_RESULTS).text) == ["A", "B", "G"]
    # salary: max (or min) descending, no salary last
    assert titles(alice.get("/?sort=salary", headers=HTMX_RESULTS).text) == ["A", "B", "G"]
    assert titles(alice.get("/", headers=HTMX_RESULTS).text) == ["G", "A", "B"]  # activity


def test_today_chip_counts_and_respects_goal(alice: Client) -> None:
    alice.create_app(position="One")
    alice.create_app(position="Two")
    alice.create_app(position="Later", status="saved")
    page = alice.get("/").text
    assert "Today 2/5" in page
    assert 'href="/?sent=today"' in page  # works as a plain link without JS

    alice.form("/settings/preferences", {"language": "en", "daily_goal": "2"})
    assert "chip-today done" in alice.get("/").text
    alice.form("/settings/preferences", {"language": "en", "daily_goal": "0"})
    assert "Today 2<" in alice.get("/").text.replace("\n", "").replace("  ", "")
    bad = alice.form("/settings/preferences", {"language": "en", "daily_goal": "500"})
    assert bad.status_code == 422


def test_board_view_and_status_change_from_board(alice: Client) -> None:
    app = alice.create_app(position="Dragged")
    alice.create_app(position="Stays", status="interview")
    board = alice.get("/?view=board", headers=HTMX_RESULTS).text
    assert 'data-status="applied"' in board and 'data-status="offer"' in board
    assert 'draggable="true"' in board

    response = alice.htmx(
        f"/applications/{app['id']}/status", {"new_status": "offer", "view": "board"}
    )
    offer_column = response.text.split('data-status="offer"')[1].split(
        '<section class="board-col"'
    )[0]
    assert "Dragged" in offer_column
    assert alice.get(f"/api/v1/applications/{app['id']}").json()["status"] == "offer"


# ---------- bulk actions ----------


def test_bulk_status_archive_note_and_portal(alice: Client, bob: Client, db: Session) -> None:
    a1 = alice.create_app(position="A1")
    a2 = alice.create_app(position="A2")
    untouched = alice.create_app(position="A3")
    foreign = bob.create_app(position="Bob's")
    ids = [a1["id"], a2["id"], foreign["id"]]

    done = alice.htmx(
        "/applications/bulk", {"ids": ids, "action": "status", "bulk_status": "rejected"}
    )
    assert "Updated: 2" in done.text
    statuses = {
        a["position"]: a["status"] for a in alice.get("/api/v1/applications").json()["items"]
    }
    assert statuses == {"A1": "rejected", "A2": "rejected", "A3": "applied"}
    assert bob.get(f"/api/v1/applications/{foreign['id']}").json()["status"] == "applied"

    note = "https://guidehouse.wd1.myworkdayjobs.com/en-US/External/userHome"
    alice.htmx("/applications/bulk", {"ids": ids, "action": "note", "bulk_text": note})
    notes = db.scalar(select(func.count()).where(Event.type == EventType.NOTE, Event.text == note))
    assert notes == 2  # Bob's application got nothing

    alice.htmx("/applications/bulk", {"ids": ids, "action": "portal", "bulk_text": note})
    assert alice.get(f"/api/v1/applications/{a1['id']}").json()["portal_url"] == note
    assert bob.get(f"/api/v1/applications/{foreign['id']}").json()["portal_url"] is None
    bad = alice.htmx("/applications/bulk", {"ids": ids, "action": "portal", "bulk_text": "x"})
    assert "must start with http" in bad.text

    alice.htmx("/applications/bulk", {"ids": [a1["id"]], "action": "archive"})
    visible = titles(alice.get("/", headers=HTMX_RESULTS).text)
    assert "A1" not in visible and untouched["position"] in visible

    assert "Select at least one" in alice.htmx("/applications/bulk", {"action": "archive"}).text
    assert (
        "Type the text first"
        in alice.htmx("/applications/bulk", {"ids": [a2["id"]], "action": "note"}).text
    )


def test_bulk_without_htmx_redirects_with_flash(alice: Client) -> None:
    app = alice.create_app()
    response = alice.form(
        "/applications/bulk", {"ids": [app["id"]], "action": "archive"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "Updated: 1" in alice.get("/").text


def test_bulk_needs_csrf(alice: Client) -> None:
    app = alice.create_app()
    response = alice.post("/applications/bulk", data={"ids": [app["id"]], "action": "archive"})
    assert response.status_code == 403


# ---------- duplicates, portal, clone ----------


def _new(client: Client, **fields: str):  # type: ignore[no-untyped-def]
    data = {"company": "CyberCare", "position": "Support", "location": "Vilnius", **fields}
    return client.htmx("/applications", data)


def test_duplicate_is_confirmed_once(alice: Client, db: Session) -> None:
    assert _new(alice, url="https://cybercare.cc/jobs/1").headers.get("HX-Redirect") == "/"
    again = _new(alice, url="https://cybercare.cc/jobs/1/")
    assert "Looks like a duplicate" in again.text and 'name="confirm_duplicate"' in again.text
    assert db.scalar(select(func.count()).select_from(Application)) == 1

    # same company/position, another city: not a duplicate
    assert _new(alice, location="Kaunas").headers.get("HX-Redirect") == "/"
    # same company with a legal suffix, same position and city: duplicate
    assert "Looks like a duplicate" in _new(alice, company="CyberCare, s.r.o.").text
    # no location given: could be either city, so it's worth a warning
    assert "Looks like a duplicate" in _new(alice, location="").text

    forced = _new(alice, url="https://cybercare.cc/jobs/1", confirm_duplicate="1")
    assert forced.headers.get("HX-Redirect") == "/"
    assert db.scalar(select(func.count()).select_from(Application)) == 3


def test_duplicates_are_per_user(alice: Client, bob: Client) -> None:
    _new(alice, url="https://cybercare.cc/jobs/1")
    assert _new(bob, url="https://cybercare.cc/jobs/1").headers.get("HX-Redirect") == "/"


def test_portal_link_is_reused_for_the_same_company(alice: Client) -> None:
    portal = "https://guidehouse.wd1.myworkdayjobs.com/en-US/External/userHome"
    alice.create_app(company="Guidehouse", position="Analyst", portal_url=portal)
    _new(alice, company="Guidehouse UAB", position="Investigations Analyst")
    items = alice.get("/api/v1/applications").json()["items"]
    assert all(i["portal_url"] == portal for i in items)
    assert f'href="{portal}"' in alice.get("/").text  # shown right in the list
    # the pasted-text form is prefilled too
    form = alice.htmx(
        "/applications/parse-text",
        {"text": "Analyst\nVilnius\nGuidehouse is a consulting company with offices in Vilnius."},
    )
    assert portal in form.text


def test_clone_prefills_form_without_link_and_date(alice: Client, bob: Client) -> None:
    source = alice.create_app(
        company="CyberCare",
        position="Support (live chat)",
        location="Vilnius",
        url="https://cybercare.cc/jobs/1",
        salary_min=1350,
    )
    page = alice.get(f"/?clone={source['id']}").text
    assert 'value="Support (live chat)"' in page and 'value="1350"' in page
    assert 'value="https://cybercare.cc/jobs/1"' not in page
    assert bob.get(f"/?clone={source['id']}").status_code == 404

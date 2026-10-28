"""User B must never see or change user A's data: every foreign access is a 404."""

from tests.conftest import Client


def test_other_user_cannot_see_or_modify_application(alice: Client, bob: Client) -> None:
    app = alice.create_app(company="Secret Corp", position="Backend Dev")
    app_id = app["id"]

    # list pages and API
    assert "Secret Corp" not in bob.get("/").text
    assert bob.get("/api/v1/applications").json()["total"] == 0

    # read
    assert bob.get(f"/applications/{app_id}").status_code == 404
    assert bob.get(f"/api/v1/applications/{app_id}").status_code == 404
    assert bob.get(f"/api/v1/applications/{app_id}/events").status_code == 404

    # write
    assert (
        bob.form(f"/applications/{app_id}", {"company": "Hacked", "position": "x"}).status_code
        == 404
    )
    assert bob.htmx(f"/applications/{app_id}/status", {"new_status": "offer"}).status_code == 404
    assert bob.form(f"/applications/{app_id}/events", {"text": "hi"}).status_code == 404
    assert bob.form(f"/applications/{app_id}/archive").status_code == 404
    assert bob.form(f"/applications/{app_id}/delete").status_code == 404
    assert (
        bob.api("PATCH", f"/api/v1/applications/{app_id}", json={"company": "Hacked"}).status_code
        == 404
    )
    assert bob.api("DELETE", f"/api/v1/applications/{app_id}").status_code == 404

    # Alice's data is untouched
    still = alice.get(f"/api/v1/applications/{app_id}").json()
    assert still["company"] == "Secret Corp"
    assert still["status"] == "applied"
    assert still["archived"] is False
    assert len(alice.get(f"/api/v1/applications/{app_id}/events").json()) == 1


def test_stats_and_export_only_include_own_data(alice: Client, bob: Client) -> None:
    alice.create_app(company="Alice Only")
    bob.create_app(company="Bob Only")
    assert "Alice Only" not in bob.get("/export.csv").text
    assert "Bob Only" in bob.get("/export.csv").text

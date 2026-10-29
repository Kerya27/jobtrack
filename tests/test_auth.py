from app.config import get_settings
from tests.conftest import PASSWORD, Client, csrf_token, register


def test_healthz_is_public(anon: Client) -> None:
    response = anon.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_pages_require_login(anon: Client) -> None:
    for url in ("/", "/applications/1", "/stats", "/settings", "/export.csv"):
        response = anon.get(url, follow_redirects=False)
        assert response.status_code == 303, url
        assert response.headers["location"] == "/login"


def test_htmx_request_without_login_gets_hx_redirect(anon: Client) -> None:
    response = anon.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.headers["HX-Redirect"] == "/login"


def test_api_requires_login(anon: Client) -> None:
    assert anon.get("/api/v1/applications").status_code == 401


def test_register_logs_in_and_logout(anon: Client) -> None:
    register(anon, "new@example.com")
    assert "new@example.com" not in anon.get("/").text  # page renders for the logged-in user
    assert anon.get("/", follow_redirects=False).status_code == 200

    anon.refresh_token()
    response = anon.form("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert anon.get("/", follow_redirects=False).status_code == 303


def test_login_with_correct_and_wrong_password(anon: Client) -> None:
    register(anon, "User@Example.com")
    anon.refresh_token()
    anon.form("/logout")

    token = csrf_token(anon)
    wrong = anon.post(
        "/login", data={"email": "user@example.com", "password": "nope-nope", "csrf_token": token}
    )
    assert wrong.status_code == 401
    assert "Wrong email or password" in wrong.text

    ok = anon.post(
        "/login",
        data={"email": " USER@example.com ", "password": PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert anon.get("/", follow_redirects=False).status_code == 200


def test_register_validation(anon: Client) -> None:
    register(anon, "taken@example.com")
    with Client(anon.app) as fresh:
        token = csrf_token(fresh)
        cases = [
            (
                {"email": "not-an-email", "password": PASSWORD, "password_repeat": PASSWORD},
                "look like an email",
            ),
            (
                {"email": "a@example.com", "password": "short", "password_repeat": "short"},
                "8 characters",
            ),
            (
                {"email": "a@example.com", "password": PASSWORD, "password_repeat": "other-pass"},
                "match",
            ),
            (
                {"email": "taken@example.com", "password": PASSWORD, "password_repeat": PASSWORD},
                "already registered",
            ),
        ]
        for data, message in cases:
            response = fresh.post("/register", data={**data, "csrf_token": token})
            assert response.status_code == 422
            assert message in response.text


def test_registration_can_be_closed(anon: Client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "allow_registration", False)
    assert "closed" in anon.get("/register").text
    response = anon.post(
        "/register",
        data={
            "email": "x@example.com",
            "password": PASSWORD,
            "password_repeat": PASSWORD,
            "csrf_token": anon.token,
        },
    )
    assert response.status_code == 403


def test_post_without_csrf_token_is_rejected(alice: Client) -> None:
    response = alice.post("/logout", data={}, follow_redirects=False)
    assert response.status_code == 403
    api = alice.post("/api/v1/applications", json={"company": "A", "position": "B"})
    assert api.status_code == 403
    bad = alice.post("/logout", data={"csrf_token": "forged"}, follow_redirects=False)
    assert bad.status_code == 403


def test_password_is_stored_hashed(alice: Client, db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.models import User

    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    assert user.password_hash.startswith("$2")
    assert PASSWORD not in user.password_hash

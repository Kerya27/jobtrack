"""Test setup.

Runs against SQLite by default; set TEST_DATABASE_URL to a PostgreSQL URL to run the same
suite on Postgres (CI does both). The schema is created with Alembic, so migrations are tested too.
"""

import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

# Must happen before any `app.*` import: settings and the engine are created at import time.
_tmp_dir = tempfile.mkdtemp(prefix="jobtrack-tests-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp_dir}/test.db")
os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ALLOW_REGISTRATION"] = "true"

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-42"


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(delete(table))


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def csrf_token(client: TestClient) -> str:
    """Read the CSRF token the way a browser submits it: from a form's hidden field.

    (/login, or / when already logged in.) It must match the <meta> token used by HTMX.
    """
    page = client.get("/login")
    hidden = re.search(r'name="csrf_token" value="([^"]*)"', page.text)
    meta = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
    assert hidden and meta, "csrf field not found"
    assert hidden.group(1) == meta.group(1), "hidden form field must carry the session token"
    return hidden.group(1)


def register(client: TestClient, email: str, password: str = PASSWORD) -> None:
    response = client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "password_repeat": password,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


class Client(TestClient):
    """TestClient that knows its CSRF token and offers small helpers."""

    token: str = ""

    def refresh_token(self) -> str:
        self.token = csrf_token(self)
        return self.token

    def form(self, url: str, data: dict[str, object] | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        payload = {"csrf_token": self.token, **(data or {})}
        return self.post(url, data=payload, **kwargs)  # type: ignore[arg-type]

    def htmx(self, url: str, data: dict[str, object] | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        headers = {"HX-Request": "true", "X-CSRF-Token": self.token}
        return self.post(url, data=data or {}, headers=headers, **kwargs)  # type: ignore[arg-type]

    def api(self, method: str, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        headers = {"X-CSRF-Token": self.token}
        return self.request(method, url, headers=headers, **kwargs)  # type: ignore[arg-type]

    def create_app(self, **fields: object) -> dict[str, object]:
        body = {"company": "Acme s.r.o.", "position": "Python Developer", **fields}
        response = self.api("POST", "/api/v1/applications", json=body)
        assert response.status_code == 201, response.text
        return response.json()


def _logged_in_client(email: str) -> Client:
    client = Client(app)
    register(client, email)
    client.refresh_token()
    return client


@pytest.fixture
def anon() -> Iterator[Client]:
    with Client(app) as client:
        client.refresh_token()
        yield client


@pytest.fixture
def alice() -> Iterator[Client]:
    with _logged_in_client("alice@example.com") as client:
        yield client


@pytest.fixture
def bob() -> Iterator[Client]:
    with _logged_in_client("bob@example.com") as client:
        yield client

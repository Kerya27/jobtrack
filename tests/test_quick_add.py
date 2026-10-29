"""The quick-add flow over HTTP: paste a link → pre-filled form."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.services import job_parser
from app.services.ssrf import FetchedPage, FetchError, FetchFailure
from tests.conftest import Client

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the network: jobs.cz pages come from a fixture, linkedin.com refuses."""
    requested: list[str] = []

    def fetch(url: str) -> FetchedPage:
        requested.append(url)
        if "linkedin.com" in url:
            raise FetchError(FetchFailure.BLOCKED_BY_SITE, "999")
        html = (FIXTURES / "jsonld_jobposting.html").read_text(encoding="utf-8")
        return FetchedPage(url=url, html=html)

    monkeypatch.setattr(job_parser, "fetch_html", fetch)
    return requested


def test_parse_prefills_form(alice: Client, fake_site: list[str]) -> None:
    response = alice.htmx("/applications/parse", {"url": "www.jobs.cz/rpd/123/"})
    assert response.status_code == 200
    html = response.text
    assert fake_site == ["https://www.jobs.cz/rpd/123/"]  # scheme added
    assert 'value="Moravská Software s.r.o."' in html
    assert 'value="Python vývojář / Backend Developer"' in html
    assert 'value="60000"' in html and 'value="80000"' in html
    assert 'value="Jobs.cz"' in html
    assert "Details found" in html


def test_blocked_site_shows_message_and_fallback(alice: Client, fake_site: list[str]) -> None:
    response = alice.htmx("/applications/parse", {"url": "https://www.linkedin.com/jobs/view/42"})
    assert response.status_code == 200
    assert "block bots" in response.text
    assert "Reason: HTTP 999." in response.text  # tells "blocked" apart from a timeout
    assert "mode=text" in response.text  # link to the paste-text fallback
    assert 'value="https://www.linkedin.com/jobs/view/42"' in response.text


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/"]
)
def test_internal_urls_are_rejected_by_the_endpoint(alice: Client, url: str) -> None:
    response = alice.htmx("/applications/parse", {"url": url})
    assert response.status_code == 200
    assert "public http(s) address" in response.text


def test_rate_limit(alice: Client, fake_site: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "parse_rate_limit_per_hour", 2)
    for _ in range(2):
        assert (
            "Details found" in alice.htmx("/applications/parse", {"url": "https://jobs.cz/1"}).text
        )
    limited = alice.htmx("/applications/parse", {"url": "https://jobs.cz/1"})
    assert "Too many links" in limited.text
    assert len(fake_site) == 2  # the third request never hit the network


def test_rate_limit_is_per_user(
    alice: Client, bob: Client, fake_site: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "parse_rate_limit_per_hour", 1)
    alice.htmx("/applications/parse", {"url": "https://jobs.cz/1"})
    assert "Too many" in alice.htmx("/applications/parse", {"url": "https://jobs.cz/1"}).text
    assert "Details found" in bob.htmx("/applications/parse", {"url": "https://jobs.cz/1"}).text


def test_paste_text_fallback(alice: Client) -> None:
    form = alice.get(
        "/applications/new?mode=text&url=https://www.linkedin.com/jobs/view/42",
        headers={"HX-Request": "true"},
    )
    assert 'name="text"' in form.text
    response = alice.htmx(
        "/applications/parse-text",
        {
            "text": "Python dev\nMzda 60 000–80 000 Kč",
            "url": "https://www.linkedin.com/jobs/view/42",
        },
    )
    assert 'value="60000"' in response.text and 'value="80000"' in response.text
    assert 'value="CZK"' in response.text
    assert 'value="LinkedIn"' in response.text
    assert "Mzda 60 000–80 000 Kč</textarea>" in response.text


def test_manual_form(alice: Client) -> None:
    response = alice.get("/applications/new", headers={"HX-Request": "true"})
    assert 'name="company"' in response.text and 'name="only_save"' in response.text


def test_parse_warns_about_an_existing_application(alice: Client, fake_site: list[str]) -> None:
    alice.create_app(url="https://www.jobs.cz/rpd/123/", position="Old one")
    response = alice.htmx("/applications/parse", {"url": "https://www.jobs.cz/rpd/123/"})
    assert "Looks like a duplicate" in response.text and "Old one" in response.text

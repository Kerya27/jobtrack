"""The browser button: the user's browser sends the open vacancy page, the server only parses it."""

from pathlib import Path
from urllib.parse import unquote

import pytest

from app.services import job_parser
from app.services.bookmarklet import bookmarklet_href
from tests.conftest import Client

FIXTURES = Path(__file__).parent / "fixtures"
DOU_URL = "https://jobs.dou.ua/companies/sbc-performance/vacancies/360629/"


def capture(client: Client, **body: str):  # type: ignore[no-untyped-def]
    headers = {"X-CSRF-Token": client.token, "HX-Request": "true"}
    return client.post("/applications/capture", json=body, headers=headers)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the feature: the server never downloads the page."""

    def fail(_url: str) -> None:
        raise AssertionError("capture must not fetch anything")

    monkeypatch.setattr(job_parser, "fetch_html", fail)


def test_page_with_json_ld(alice: Client) -> None:
    html = (FIXTURES / "jsonld_jobposting.html").read_text(encoding="utf-8")
    response = capture(alice, url="https://www.jobs.cz/rpd/123/", html=html, text="")
    assert response.status_code == 200
    assert 'value="Moravská Software s.r.o."' in response.text
    assert 'value="60000"' in response.text and 'value="Jobs.cz"' in response.text
    assert 'value="https://www.jobs.cz/rpd/123/"' in response.text
    assert "Details found" in response.text


def test_dou_page_as_the_browser_sends_it(alice: Client) -> None:
    html = (FIXTURES / "dou_page.html").read_text(encoding="utf-8")
    text = (FIXTURES / "dou_pasted.txt").read_text(encoding="utf-8")
    response = capture(alice, url=DOU_URL, html=html, text=text)
    assert 'value="Full Stack Engineer (Python &amp; Angular)"' in response.text
    assert 'value="SBC Performance"' in response.text
    assert 'value="DOU"' in response.text


def test_text_fills_gaps_when_html_has_nothing(alice: Client) -> None:
    text = "Python Developer\nBrno\nAcme Corp is a Czech software company.\nMzda 60 000 Kč"
    response = capture(alice, url="https://spa.example.com/job/1", html="<html></html>", text=text)
    assert 'value="Python Developer"' in response.text and 'value="Acme Corp"' in response.text
    assert 'value="60000"' in response.text and 'value="CZK"' in response.text


def test_bad_url_is_dropped_but_page_is_still_parsed(alice: Client) -> None:
    html = (FIXTURES / "jsonld_jobposting.html").read_text(encoding="utf-8")
    response = capture(alice, url="javascript:alert(1)", html=html)
    assert "javascript:" not in response.text
    assert 'value="Moravská Software s.r.o."' in response.text


def test_capture_warns_about_duplicates(alice: Client) -> None:
    alice.create_app(url=DOU_URL, position="Already here")
    response = capture(alice, url=DOU_URL, html="<title>X</title>")
    assert "Looks like a duplicate" in response.text and "Already here" in response.text


def test_capture_needs_login_csrf_and_a_sane_size(alice: Client, anon: Client) -> None:
    anonymous = capture(anon, url="https://a.cz/", html="<title>X</title>")
    assert anonymous.headers.get("HX-Redirect") == "/login"
    no_token = alice.post("/applications/capture", json={"url": "https://a.cz/", "html": "x"})
    assert no_token.status_code == 403
    huge = capture(alice, url="https://a.cz/", html="x" * 2_200_000)
    assert huge.status_code == 422


def test_capture_window_and_login_comes_back_to_it(alice: Client, anon: Client) -> None:
    page = alice.get("/capture?url=https%3A%2F%2Fjobs.dou.ua%2Fvacancies%2F1").text
    assert 'id="capture"' in page and 'data-url="https://jobs.dou.ua/vacancies/1"' in page

    # session expired: log in and land back in the capture window (the bookmarklet still waits)
    redirect = anon.get("/capture?url=x", follow_redirects=False)
    assert redirect.headers["location"] == "/login?next=%2Fcapture%3Furl%3Dx"
    login_page = anon.get("/login?next=/capture%3Furl%3Dx").text
    assert 'name="next" value="/capture?url=x"' in login_page
    response = anon.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "correct-horse-42",
            "next": "/capture?url=x",
            "csrf_token": anon.refresh_token(),
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/capture?url=x"
    anon.form("/logout")
    evil = anon.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "correct-horse-42",
            "next": "//evil.com",
            "csrf_token": anon.refresh_token(),
        },
        follow_redirects=False,
    )
    assert evil.headers["location"] == "/"


def test_bookmarklet_link(alice: Client) -> None:
    href = bookmarklet_href("https://jobtrack.example.com/")
    assert href.startswith("javascript:")
    code = unquote(href.removeprefix("javascript:"))
    assert 'var J = "https://jobtrack.example.com";' in code
    assert "//" not in code.replace("https://", "")  # comments are stripped (one line of JS)
    assert " " not in href  # spaces are encoded

    settings = alice.get("/settings").text
    assert 'id="bookmarklet"' in settings
    assert "http%3A" not in settings  # the origin is plain text inside the link
    assert "http://testserver" in unquote(
        settings.split('class="bookmarklet"')[0].split('href="')[-1]
    )

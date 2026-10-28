from pathlib import Path

import pytest

from app.models import WorkMode
from app.services import job_parser
from app.services.job_parser import parse_html, parse_job, parse_pasted_text, source_from_url
from app.services.salary import Salary, extract_salary
from app.services.ssrf import FetchedPage, FetchError, FetchFailure

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_jsonld_job_posting() -> None:
    job = parse_html(fixture("jsonld_jobposting.html"), "https://www.jobs.cz/rpd/123/")
    assert job.found_structured_data
    assert job.position == "Python vývojář / Backend Developer"
    assert job.company == "Moravská Software s.r.o."
    assert job.location == "Brno, CZ"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (60000, 80000, "CZK")
    assert job.source == "Jobs.cz"
    assert job.employment_type == "FULL_TIME, CONTRACTOR"
    assert job.description is not None
    assert "<" not in job.description
    assert "Hledáme Python vývojáře do týmu v Brně." in job.description
    assert "• FastAPI" in job.description
    assert job.is_complete and not job.needs_manual


def test_jsonld_remote_list_types_and_double_encoded_html() -> None:
    job = parse_html(fixture("jsonld_remote.html"), "https://startupjobs.cz/nabidka/1")
    assert job.position == "Senior Backend Engineer (Remote)"
    assert job.company == "Remote First Inc"
    assert job.work_mode == WorkMode.REMOTE
    assert job.location == "Remote"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (5500, 5500, "EUR")
    assert job.description == "Fully remote role.\nPython, AWS."
    assert job.source == "StartupJobs"


def test_opengraph_fallback() -> None:
    job = parse_html(fixture("og_only.html"), "https://www.work.ua/jobs/555/")
    assert not job.found_structured_data
    assert job.position == "Junior Python Developer — Kyiv / remote"
    assert job.company is None
    assert job.description is not None and job.description.startswith("Шукаємо джуна")
    # salary found in the description text, work mode guessed from the title
    assert (job.salary_min, job.salary_max, job.salary_currency) == (40000, None, "UAH")
    assert job.work_mode == WorkMode.REMOTE
    assert job.source == "Work.ua"
    assert not job.needs_manual and not job.is_complete


def test_empty_page_needs_manual() -> None:
    job = parse_html(fixture("empty.html"), "https://spa.example.com/job/1")
    assert job.needs_manual
    assert job.position is None and job.company is None
    assert job.source == "spa.example.com"


def test_broken_html_does_not_crash() -> None:
    job = parse_html(
        '<html><script type="application/ld+json">{not json</script><title>X</title>',
        "https://a.cz/",
    )
    assert job.position == "X"


@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("https://www.linkedin.com/jobs/view/1", "LinkedIn"),
        ("https://cz.indeed.com/viewjob?jk=1", "Indeed"),
        ("https://m.jobs.cz/rpd/1", "Jobs.cz"),
        ("https://djinni.co/jobs/1", "Djinni"),
        ("https://careers.example.org/1", "careers.example.org"),
        ("not a url", None),
    ],
)
def test_source_from_domain(url: str, source: str | None) -> None:
    assert source_from_url(url) == source


def test_parse_job_turns_fetch_errors_into_result() -> None:
    def blocked(_url: str) -> FetchedPage:
        raise FetchError(FetchFailure.BLOCKED_BY_SITE, "999")

    job = parse_job("https://www.linkedin.com/jobs/view/1", fetch=blocked)
    assert job.error == FetchFailure.BLOCKED_BY_SITE
    assert job.needs_manual
    assert job.source == "LinkedIn"


def test_parse_job_keeps_pasted_url_after_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(_url: str) -> FetchedPage:
        return FetchedPage(
            url="https://www.jobs.cz/rpd/123/", html=fixture("jsonld_jobposting.html")
        )

    monkeypatch.setattr(job_parser, "fetch_html", fake_fetch)
    job = parse_job("https://short.link/abc")
    assert job.url == "https://short.link/abc"
    assert job.source == "Jobs.cz"
    assert job.company == "Moravská Software s.r.o."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Plat: 60 000–80 000 Kč měsíčně", Salary(60000, 80000, "CZK")),
        ("Mzda 45.000 - 55.000 CZK", Salary(45000, 55000, "CZK")),
        ("CZK 70 000 - 90 000", Salary(70000, 90000, "CZK")),
        ("odměna 50 000,- Kč", Salary(50000, 50000, "CZK")),
        ("od 50 000 Kč", Salary(50000, None, "CZK")),
        ("80 tis. Kč", Salary(80000, 80000, "CZK")),
        ("€3000", Salary(3000, 3000, "EUR")),
        ("3.5k €", Salary(3500, 3500, "EUR")),
        ("$4,500 - $6,000", Salary(4500, 6000, "USD")),
        ("up to 120k USD", Salary(None, 120000, "USD")),
        ("60-80k EUR", Salary(60000, 80000, "EUR")),
        ("salary 60k", Salary(60000, 60000, None)),
        ("від 40 000 грн", Salary(40000, None, "UAH")),
        ("до 3000$", Salary(None, 3000, "USD")),
        ("we have 10k users and pay 90 000 Kč", Salary(90000, 90000, "CZK")),
        ("Founded 2024, 3-5 let praxe, Python 3.12", None),
    ],
)
def test_salary_extraction(text: str, expected: Salary | None) -> None:
    assert extract_salary(text) == expected


def test_pasted_text_fallback() -> None:
    job = parse_pasted_text(
        "Backend Developer\r\n\r\n\r\n\r\nNabízíme: 70 000 – 95 000 Kč, home office.",
        "https://www.prace.cz/nabidka/1",
    )
    assert (job.salary_min, job.salary_max, job.salary_currency) == (70000, 95000, "CZK")
    assert job.description == "Backend Developer\n\nNabízíme: 70 000 – 95 000 Kč, home office."
    assert job.source == "Prace.cz"


# ---------- pages without JSON-LD (e.g. DOU) and pasted text ----------


def test_page_without_structured_data_uses_full_text_and_url_slug() -> None:
    job = parse_html(
        fixture("dou_page.html"),
        "https://jobs.dou.ua/companies/sbc-performance/vacancies/360629/",
    )
    assert not job.found_structured_data
    assert job.position == "Full Stack Engineer (Python & Angular)"
    # company: slug from the URL, spelled as it appears in the page text
    assert job.company == "SBC Performance"
    assert job.source == "DOU"
    assert job.description is not None
    assert "Requirements:" in job.description and "• 3+ years of experience" in job.description
    assert "Similar vacancies" not in job.description and "© DOU" not in job.description
    assert job.is_complete


def test_slug_is_prettified_when_not_found_in_text() -> None:
    job = parse_html(
        "<html><head><title>QA</title></head><body></body></html>",
        "https://jobs.dou.ua/companies/acme-labs/vacancies/1/",
    )
    assert job.company == "Acme Labs"


@pytest.mark.parametrize(
    ("title", "position", "company"),
    [
        ("Backend Developer | Kiwi.com", "Backend Developer", "Kiwi.com"),
        ("Python Engineer at Red Hat", "Python Engineer", "Red Hat"),
        ("Python Engineer | Jobs.cz", "Python Engineer", None),
        ("Software Engineer – Python", "Software Engineer – Python", None),
    ],
)
def test_title_split(title: str, position: str, company: str | None) -> None:
    html = f"<html><head><title>{title}</title></head></html>"
    job = parse_html(html, "https://www.jobs.cz/rpd/1/")
    assert (job.position, job.company) == (position, company)


def test_pasted_vacancy_text_is_split_into_fields() -> None:
    text = (FIXTURES / "dou_pasted.txt").read_text(encoding="utf-8")
    job = parse_pasted_text(text, "https://jobs.dou.ua/companies/sbc-performance/vacancies/360629/")
    assert job.position == "Full Stack Engineer (Python & Angular)"
    assert job.company == "SBC Performance"
    assert job.location == "Прага (Чехія), Варшава (Польща), віддалено"
    assert job.work_mode == WorkMode.REMOTE
    assert job.source == "DOU"
    assert job.salary_min is None  # "Competitive compensation" is not a number
    assert job.description is not None and "Key Responsibilities:" in job.description


def test_pasted_text_company_without_url() -> None:
    job = parse_pasted_text("Python Developer\nBrno\nAcme Corp is a Czech software company.\n")
    assert (job.position, job.location, job.company) == ("Python Developer", "Brno", "Acme Corp")


def test_pasted_text_does_not_guess_a_wrong_company() -> None:
    job = parse_pasted_text("Backend Developer\nWe are looking for a developer to join our team.\n")
    assert job.company is None and job.position == "Backend Developer"
    assert (
        parse_pasted_text("Just one long sentence without any structure at all. " * 3).position
        is None
    )

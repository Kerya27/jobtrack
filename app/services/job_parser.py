"""Extract vacancy data from a job page.

Order of attempts:
1. JSON-LD ``JobPosting`` (schema.org) — most Czech and international boards embed it.
2. OpenGraph / <meta> / <title> — fallback for the title and the description.
3. ``source`` from the domain.
If nothing useful is found, the result has ``needs_manual=True``.

``parse_html`` is a pure function (easy to test on saved fixtures); ``parse_job`` adds the
safe download and turns fetch errors into a result instead of an exception.
"""

import html as html_lib
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import extruct
import httpx
from bs4 import BeautifulSoup

from app.i18n import t
from app.models import WorkMode
from app.services.salary import extract_salary
from app.services.ssrf import FetchedPage, FetchError, FetchFailure, fetch_html
from app.services.text_heuristics import (
    company_from_slug,
    company_slug_from_url,
    guess_work_mode,
    main_content_html,
    parse_vacancy_text,
    split_title,
)

logger = logging.getLogger(__name__)

MAX_TEXT = 200
MAX_DESCRIPTION = 50_000

# Domain → display name. Subdomains match too (cz.indeed.com → Indeed).
KNOWN_SOURCES: dict[str, str] = {
    "linkedin.com": "LinkedIn",
    "jobs.cz": "Jobs.cz",
    "prace.cz": "Prace.cz",
    "startupjobs.cz": "StartupJobs",
    "profesia.cz": "Profesia.cz",
    "cocuma.cz": "Cocuma",
    "jobstack.it": "JobStack",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "welcometothejungle.com": "Welcome to the Jungle",
    "justjoin.it": "JustJoin.it",
    "nofluffjobs.com": "No Fluff Jobs",
    "pracuj.pl": "Pracuj.pl",
    "work.ua": "Work.ua",
    "robota.ua": "robota.ua",
    "djinni.co": "Djinni",
    "dou.ua": "DOU",
    "hh.ru": "hh.ru",
    "weworkremotely.com": "We Work Remotely",
    "remoteok.com": "Remote OK",
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "workable.com": "Workable",
    "teamio.com": "Teamio",
    "jooble.org": "Jooble",
}


@dataclass
class ParsedJob:
    url: str
    source: str | None = None
    company: str | None = None
    position: str | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    description: str | None = None
    employment_type: str | None = None
    found_structured_data: bool = False
    error: FetchFailure | None = None
    error_detail: str = ""  # e.g. "HTTP 403", shown to the user to tell blocking from timeouts

    @property
    def needs_manual(self) -> bool:
        return not (self.position or self.company)

    @property
    def is_complete(self) -> bool:
        return bool(self.position and self.company)

    def form_values(self) -> dict[str, Any]:
        notes = t("employment_type", value=self.employment_type) if self.employment_type else None
        return {
            "url": self.url,
            "source": self.source,
            "company": self.company,
            "position": self.position,
            "location": self.location,
            "work_mode": self.work_mode.value,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_currency": self.salary_currency,
            "description": self.description,
            "notes": notes,
        }


# ---------- small helpers ----------


def source_from_url(url: str) -> str | None:
    try:
        host = (httpx.URL(url).host or "").lower()
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    host = re.sub(r"^(www|m)\.", "", host)
    if not host:
        return None
    for domain, name in KNOWN_SOURCES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def _clean(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if v)
    text = re.sub(r"\s+", " ", html_lib.unescape(str(value))).strip()
    return text[:limit] or None


def html_to_text(markup: str) -> str:
    """HTML description → readable plain text with paragraph breaks kept."""
    if "&lt;" in markup and "<" not in markup:
        markup = html_lib.unescape(markup)  # some boards double-encode the HTML
    soup = BeautifulSoup(markup, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
    text = soup.get_text()
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = re.sub(r"[\s  ]", "", str(value))
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
            text = re.sub(r"\D", "", text)
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return None
    return round(number) if 0 < number <= 100_000_000 else None


# ---------- JSON-LD ----------


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _walk(node["@graph"])


def _has_type(node: dict[str, Any], wanted: str) -> bool:
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(
        isinstance(t, str) and t.rsplit("/", 1)[-1].rsplit(":", 1)[-1] == wanted for t in types
    )


def find_job_posting(html: str, url: str) -> dict[str, Any] | None:
    try:
        data = extruct.extract(
            html, base_url=url, syntaxes=["json-ld"], uniform=True, errors="ignore"
        )
    except Exception:  # extruct raises a variety of parser errors on broken pages
        logger.info("structured data extraction failed for %s", source_from_url(url))
        return None
    for node in _walk(data.get("json-ld", [])):
        if _has_type(node, "JobPosting"):
            return node
    return None


def _name(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("name")
    return _clean(value)


def _location(value: Any) -> str | None:
    places = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for place in places:
        if isinstance(place, str):
            parts.append(place)
            continue
        if not isinstance(place, dict):
            continue
        address = place.get("address", place)
        if isinstance(address, list):
            address = address[0] if address else {}
        if isinstance(address, str):
            parts.append(address)
            continue
        if not isinstance(address, dict):
            continue
        city = _clean(address.get("addressLocality")) or _clean(address.get("addressRegion"))
        country = _name(address.get("addressCountry"))
        text = ", ".join(p for p in (city, country) if p)
        if text:
            parts.append(text)
    unique = list(dict.fromkeys(p for p in parts if p))
    return _clean("; ".join(unique)) if unique else None


def _apply_salary(job: ParsedJob, base_salary: Any) -> None:
    if isinstance(base_salary, list):
        base_salary = base_salary[0] if base_salary else None
    if base_salary is None:
        return
    if not isinstance(base_salary, dict):
        job.salary_min = job.salary_max = _to_int(base_salary)
        return
    currency = base_salary.get("currency")
    value = base_salary.get("value", base_salary)
    if isinstance(value, dict):
        low = _to_int(value.get("minValue"))
        high = _to_int(value.get("maxValue"))
        single = _to_int(value.get("value"))
        currency = currency or value.get("currency")
    else:
        low = high = None
        single = _to_int(value)
    if low is None and high is None and single is not None:
        low = high = single
    if low is not None and high is not None and high < low:
        low, high = high, low
    job.salary_min, job.salary_max = low, high
    if (
        (low or high)
        and isinstance(currency, str)
        and re.fullmatch(r"[A-Za-z]{3}", currency.strip())
    ):
        job.salary_currency = currency.strip().upper()


def _apply_job_posting(job: ParsedJob, posting: dict[str, Any]) -> None:
    job.found_structured_data = True
    job.position = _clean(posting.get("title") or posting.get("name"))
    job.company = _name(posting.get("hiringOrganization"))
    job.location = _location(posting.get("jobLocation"))
    job.employment_type = _clean(posting.get("employmentType"))
    description = posting.get("description")
    if isinstance(description, str) and description.strip():
        job.description = html_to_text(description)[:MAX_DESCRIPTION]
    _apply_salary(job, posting.get("baseSalary"))

    location_type = posting.get("jobLocationType")
    types = location_type if isinstance(location_type, list) else [location_type]
    if any(isinstance(t, str) and "TELECOMMUTE" in t.upper() for t in types):
        job.work_mode = WorkMode.REMOTE
        if not job.location:
            job.location = "Remote"


# ---------- OpenGraph / meta ----------


def _meta(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        content = tag.get("content") if tag else None
        if isinstance(content, str) and content.strip():
            return content
    return None


def _apply_meta(job: ParsedJob, soup: BeautifulSoup) -> None:
    if not job.position:
        title = _meta(soup, "og:title", "twitter:title")
        if not title and soup.title and soup.title.string:
            title = soup.title.string
        title = _clean(title)
        if title:
            job.position, company = split_title(title, job.source)
            job.company = job.company or company
    if not job.description:
        description = _meta(soup, "og:description", "twitter:description", "description")
        if description:
            job.description = html_to_text(description)[:MAX_DESCRIPTION]


# Per-site CSS selectors (all optional; the generic path below is the real safety net).
# NOTE: these follow the sites' public markup as far as known and are not guaranteed.
SITE_SELECTORS: dict[str, dict[str, str]] = {
    "DOU": {
        "company": ".b-compinfo .l-n a, .l-vacancy .company a, .l-n a",
        "location": ".sh-info .place, .l-vacancy .place",
        "salary": ".sh-info .salary, .l-vacancy .salary",
        "description": ".b-typo.vacancy-section, .vacancy-section",
    },
}


def _select_text(soup: BeautifulSoup, selector: str | None) -> str | None:
    if not selector:
        return None
    node = soup.select_one(selector)
    return _clean(node.get_text(" ", strip=True)) if node else None


def _apply_site_selectors(job: ParsedJob, soup: BeautifulSoup) -> None:
    selectors = SITE_SELECTORS.get(job.source or "")
    if not selectors:
        return
    job.company = job.company or _select_text(soup, selectors.get("company"))
    job.location = job.location or _select_text(soup, selectors.get("location"))
    if job.salary_min is None and job.salary_max is None:
        salary = _select_text(soup, selectors.get("salary"))
        found = extract_salary(salary) if salary else None
        if found:
            job.salary_min, job.salary_max, job.salary_currency = (
                found.min,
                found.max,
                found.currency,
            )
    node = soup.select_one(selectors["description"]) if selectors.get("description") else None
    if node is not None:
        text = html_to_text(str(node))
        if len(text) > len(job.description or ""):
            job.description = text[:MAX_DESCRIPTION]


def _apply_page_text(job: ParsedJob, html: str) -> None:
    """Pages without JSON-LD: og:description is a one-line teaser, so take the real text block."""
    main = main_content_html(html)
    if main:
        text = html_to_text(main)
        if len(text) > max(len(job.description or "") * 1.5, 200):
            job.description = text[:MAX_DESCRIPTION]


def _apply_company_from_url(job: ParsedJob) -> None:
    slug = company_slug_from_url(job.url)
    if slug and not job.company:
        job.company = company_from_slug(slug, job.description, job.position)


# ---------- public API ----------


def parse_html(html: str, url: str) -> ParsedJob:
    """Pure function: page HTML (+ its URL) → ParsedJob."""
    job = ParsedJob(url=url, source=source_from_url(url))
    posting = find_job_posting(html, url)
    if posting:
        _apply_job_posting(job, posting)
    soup = BeautifulSoup(html, "lxml")
    if not job.position or not job.description:
        _apply_meta(job, soup)
    if not posting:
        _apply_page_text(job, html)
    _apply_site_selectors(job, soup)
    _apply_company_from_url(job)

    if job.work_mode == WorkMode.UNKNOWN:
        job.work_mode = guess_work_mode(job.position, job.location)
    if job.salary_min is None and job.salary_max is None and job.description:
        salary = extract_salary(job.description[:10_000], require_currency=True)
        if salary:
            job.salary_min, job.salary_max, job.salary_currency = (
                salary.min,
                salary.max,
                salary.currency,
            )
    return job


def parse_job(url: str, fetch: Callable[[str], FetchedPage] | None = None) -> ParsedJob:
    """Download the page safely and parse it. Never raises for network/site problems."""
    try:
        page = (fetch or fetch_html)(url)
    except FetchError as exc:
        detail = f"HTTP {exc.detail}" if exc.detail.isdigit() else exc.detail
        logger.warning("parse failed for %s: %s %s", source_from_url(url), exc.reason, detail)
        return ParsedJob(
            url=url, source=source_from_url(url), error=exc.reason, error_detail=detail
        )
    job = parse_html(page.html, page.url)
    job.url = url  # keep the link the user pasted (not a tracking redirect target)
    return job


def parse_pasted_text(text: str, url: str | None = None) -> ParsedJob:
    """Fallback when the page can't be fetched: the user pastes the vacancy text.

    Finds position, location, work mode, company (a guess) and salary; the full text stays
    in the description so nothing is lost.
    """
    cleaned = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()[:MAX_DESCRIPTION]
    job = ParsedJob(url=url or "", source=source_from_url(url) if url else None)
    job.description = cleaned or None
    fields = parse_vacancy_text(cleaned)
    job.position, job.company = fields.position, fields.company
    job.location, job.work_mode = fields.location, fields.work_mode
    if url and not job.company:
        slug = company_slug_from_url(url)
        job.company = company_from_slug(slug, cleaned) if slug else None
    salary = extract_salary(cleaned[:20_000])
    if salary:
        job.salary_min, job.salary_max, job.salary_currency = (
            salary.min,
            salary.max,
            salary.currency,
        )
    return job


def parse_captured(html: str, url: str, text: str = "") -> ParsedJob:
    """A page the user's own browser sent us (bookmarklet): nothing is downloaded.

    The HTML (with JSON-LD) is parsed as usual. The page's visible text only fills gaps: the
    salary and the description, and position/company when the HTML gave nothing at all
    (the first lines of a whole page are usually navigation, so they are a last resort).
    """
    job = parse_html(html, url) if html.strip() else ParsedJob(url=url)
    job.url = url
    job.source = job.source or (source_from_url(url) if url else None)
    if not text.strip():
        return job
    from_text = parse_pasted_text(text, url or None)
    if job.salary_min is None and job.salary_max is None:
        job.salary_min, job.salary_max = from_text.salary_min, from_text.salary_max
        job.salary_currency = from_text.salary_currency
    if not job.description:
        job.description = from_text.description
    if job.needs_manual:
        job.position, job.company = from_text.position, from_text.company
        job.location = job.location or from_text.location
        if job.work_mode == WorkMode.UNKNOWN:
            job.work_mode = from_text.work_mode
    return job

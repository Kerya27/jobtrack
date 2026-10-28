"""Cheap, rule-based guessing of vacancy fields from plain text and page markup (no LLM).

Everything here is a *guess* that the user confirms in the form, so rules favour precision:
returning ``None`` is better than a wrong company.
"""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from app.models import WorkMode

REMOTE_WORDS = ("remote", "na dálku", "home office", "удален", "удалён", "віддален", "дистанц")
HYBRID_WORDS = ("hybrid", "hybridní", "гибрид", "гібрид")

_CITIES = (
    "praha", "prague", "прага", "брно", "brno", "ostrava", "plzeň", "olomouc",
    "warsaw", "варшава", "warszawa", "kraków", "krakow", "краків", "wrocław",
    "kyiv", "київ", "киев", "lviv", "львів", "львов", "dnipro", "дніпро", "kharkiv", "харків",
    "berlin", "berlín", "vienna", "wien", "відень", "bratislava", "братислава",
    "london", "amsterdam", "munich", "münchen", "dublin", "lisbon", "madrid", "barcelona",
)  # fmt: skip
_COUNTRY_PAREN = re.compile(r"\w[\w .'-]*\s*\([^)]{2,30}\)")
_SENTENCE_END = (".", ":", "!", "?", ";")

_COMPANY_STOP = {
    "we", "our", "about", "join", "the", "you", "your", "as", "at", "are", "if", "for", "in",
    "this", "that", "it", "a", "an", "and", "my", "i", "мы", "наша", "наш", "ми", "про",
}  # fmt: skip
_COMPANY_VERBS = (
    "is|are|was|has|have|drives|builds|helps|provides|offers|develops|delivers|creates|"
    "specializes|specialises|operates|connects|empowers|makes|brings|powers|combines|focuses|"
    "serves|enables|transforms|designs|runs|works|looks|seeks|hires|grows|"
    "є|це|розробляє|створює|надає|пропонує|займається|допомагає|"
    "это|разрабатывает|создаёт|создает|предоставляет|предлагает|занимается|помогает"
)
_UPPER = "A-ZА-ЯІЇЄҐĚŠČŘŽÝÁÍÉÚŮŇŤĎ"
_COMPANY_LEAD = re.compile(
    rf"^((?:[{_UPPER}][\w&.'’-]*)(?:\s+[{_UPPER}][\w&.'’-]*){{0,3}})\s+(?:{_COMPANY_VERBS})\b",
    re.IGNORECASE | re.UNICODE,
)
_COMPANY_LABEL = re.compile(
    r"^(?:company(?: name)?|компания|компанія|firma|společnost|employer|about(?: us)?|"
    r"о компании|про компанію|о нас|про нас)\s*[:\-–—]?\s*(?P<name>[^\n.:]{2,60})$",
    re.IGNORECASE,
)


def guess_work_mode(*texts: str | None) -> WorkMode:
    blob = " ".join(t for t in texts if t).lower()
    if any(word in blob for word in HYBRID_WORDS):
        return WorkMode.HYBRID
    if any(word in blob for word in REMOTE_WORDS):
        return WorkMode.REMOTE
    return WorkMode.UNKNOWN


# ---------- company ----------


def company_slug_from_url(url: str) -> str | None:
    """``.../companies/sbc-performance/vacancies/1`` → ``sbc-performance`` (DOU, Djinni…)."""
    match = re.search(r"/(?:companies|company|firma|employers?)/([a-z0-9][\w.-]*)", url, re.I)
    return match.group(1) if match else None


def company_from_slug(slug: str, *texts: str | None) -> str:
    """Prefer the spelling found in the page text ("SBC Performance"); else prettify the slug."""
    parts = [p for p in re.split(r"[-_.]+", slug) if p]
    if not parts:
        return slug
    pattern = re.compile(r"[\s\-_.]*".join(re.escape(p) for p in parts), re.IGNORECASE)
    for text in texts:
        if text and (found := pattern.search(text)):
            return found.group(0)
    return " ".join(p.capitalize() if p.islower() else p for p in parts)


def split_title(title: str, source: str | None) -> tuple[str, str | None]:
    """ "Backend Dev | Acme" → ("Backend Dev", "Acme"); a trailing "| Jobs.cz" is just dropped.

    Only unambiguous separators are used: a dash can be part of the position itself.
    """
    for separator in (" | ", " @ ", " at "):
        if separator in title:
            left, _, right = title.rpartition(separator)
            right = right.strip()
            left = left.strip()
            if not left or not right or len(right) > 60:
                continue
            if source and right.casefold().replace(" ", "") in source.casefold().replace(" ", ""):
                return left, None  # the site name, not the employer
            if separator == " at " and len(right.split()) > 4:
                continue  # "Engineer at scale in distributed systems"
            return left, right
    return title, None


def guess_company_from_text(lines: list[str]) -> str | None:
    """Company from "Company: X", "About X" or a first sentence like "SBC Performance drives…"."""
    for line in lines[:12]:
        if match := _COMPANY_LABEL.match(line):
            name = match["name"].strip(" .,")
            if name.casefold() not in _COMPANY_STOP:
                return name
    for line in lines[:8]:
        if len(line) < 25:
            continue
        match = _COMPANY_LEAD.match(line)
        if not match:
            continue
        words = match.group(1).split()
        while words and words[0].casefold() in _COMPANY_STOP:
            words.pop(0)
        if words:
            return " ".join(words)
        break  # first long sentence starts with "We…"/"Our…": stop, don't guess from later ones
    return None


# ---------- pasted text ----------


@dataclass
class TextFields:
    position: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN


def looks_like_location(line: str) -> bool:
    if len(line) > 120 or line.endswith(_SENTENCE_END) or len(line.split()) > 14:
        return False
    lowered = line.lower()
    return (
        any(city in lowered for city in _CITIES)
        or any(word in lowered for word in REMOTE_WORDS + HYBRID_WORDS)
        or bool(_COUNTRY_PAREN.search(line))
    )


def parse_vacancy_text(text: str) -> TextFields:
    """Position / location / work mode / company from a pasted vacancy.

    Typical layout: line 1 = title, line 2 = "City (Country), remote", then the description.
    """
    lines = [line.strip(" \t•*-–—") for line in text.splitlines() if line.strip()]
    fields = TextFields()
    if not lines:
        return fields

    first = lines[0]
    if len(first) <= 150 and not first.endswith(_SENTENCE_END) and len(first.split()) <= 16:
        fields.position = first
        lines = lines[1:]

    header = lines[:3]
    for line in header:
        if looks_like_location(line):
            fields.location = line
            break

    fields.work_mode = guess_work_mode(fields.location, fields.position)
    if fields.work_mode == WorkMode.UNKNOWN:
        # explicit statements anywhere in the text ("Hybrid job in Prague", "fully remote")
        body = " ".join(lines)[:6000].lower()
        if re.search(r"hybrid|hybridní|гибрид|гібрид", body):
            fields.work_mode = WorkMode.HYBRID
        elif re.search(
            r"fully remote|100% remote|full remote|повністю віддален|полностью удал", body
        ):
            fields.work_mode = WorkMode.REMOTE

    body_lines = [line for line in lines if line != fields.location]
    fields.company = guess_company_from_text(body_lines)
    return fields


# ---------- main content of an arbitrary page ----------

_NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]
_MIN_CONTENT_CHARS = 300
_DESCEND_SHARE = 0.7


def main_content_html(html: str) -> str | None:
    """The block of the page that holds most of the paragraph/list text (a "readability lite").

    Counts the text of every <p>/<li> towards all of its ancestors, then descends from <body>
    while a single child still holds ≥70 % of it. Returns None for pages with little text.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    root = soup.body or soup

    scores: dict[int, int] = {}
    total = 0
    for element in root.find_all(["p", "li"]):
        length = len(element.get_text(" ", strip=True))
        if not length or element.find(["p", "li"]):
            continue  # count only the innermost items
        total += length
        for parent in element.parents:
            scores[id(parent)] = scores.get(id(parent), 0) + length
    if total < _MIN_CONTENT_CHARS:
        return None

    node: Tag = root
    while True:
        children = [c for c in node.find_all(recursive=False) if isinstance(c, Tag)]
        best = max(children, key=lambda c: scores.get(id(c), 0), default=None)
        if best is None or scores.get(id(best), 0) < _DESCEND_SHARE * total:
            break
        node = best
    return str(node)

"""Find a salary in free text with regular expressions (no paid LLM).

Handles the formats that appear on Czech, Ukrainian and international job boards:
``60 000–80 000 Kč``, ``45.000 - 55.000 CZK``, ``€3000``, ``$4,500 - $6,000``, ``60-80k EUR``,
``60k``, ``від 40 000 грн``, ``до 3000$``, ``50 000,- Kč``.
"""

import re
from dataclasses import dataclass

_LETTER_AFTER = r"(?![^\W\d_])"  # not followed by a letter (Unicode-aware)

_CURRENCY = (
    r"(?:"
    r"(?:kč|czk|euro?|usd|uah|pln|zł|gbp|грн)" + _LETTER_AFTER + r"\.?"
    r"|€|\$|₴|£"
    r")"
)
_NUMBER = (
    r"\d{1,3}(?:[   .,']\d{3})+(?!\d)"  # 60 000 / 45.000 / 4,500
    r"|\d+(?:[.,]\d+)?"  # 3000 / 3.5
)
_THOUSANDS = r"(?:\s?(?:k|tis\.?|тис\.?|тыс\.?)" + _LETTER_AFTER + r")"
_DASH_SUFFIX = r"(?:,-|\.-|,–)?"  # Czech "50 000,- Kč"
_RANGE_SEP = r"(?:\s*[-–—]\s*|\s+(?:to|do|až|до)\s+)"

_RANGE_RE = re.compile(
    rf"(?P<cur1>{_CURRENCY})?\s*(?P<lo>{_NUMBER})(?P<lo_k>{_THOUSANDS})?{_DASH_SUFFIX}\s*(?P<cur2>{_CURRENCY})?"
    rf"{_RANGE_SEP}"
    rf"(?P<cur3>{_CURRENCY})?\s*(?P<hi>{_NUMBER})(?P<hi_k>{_THOUSANDS})?{_DASH_SUFFIX}\s*(?P<cur4>{_CURRENCY})?",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?:(?P<prefix>\b(?:od|from|від|от|min\.?|до|do|up\s+to|max\.?))\s*)?"
    rf"(?P<cur1>{_CURRENCY})?\s*(?P<num>{_NUMBER})(?P<k>{_THOUSANDS})?{_DASH_SUFFIX}\s*(?P<cur2>{_CURRENCY})?",
    re.IGNORECASE,
)

_CURRENCY_CODES = {
    "kč": "CZK", "czk": "CZK",
    "eur": "EUR", "euro": "EUR", "€": "EUR",
    "usd": "USD", "$": "USD",
    "uah": "UAH", "грн": "UAH", "₴": "UAH",
    "pln": "PLN", "zł": "PLN",
    "gbp": "GBP", "£": "GBP",
}  # fmt: skip
_MIN_WORDS = {"od", "from", "від", "от", "min", "min."}
_MAX_WORDS = {"до", "do", "up to", "max", "max."}
MAX_REASONABLE = 10_000_000


@dataclass(frozen=True)
class Salary:
    min: int | None
    max: int | None
    currency: str | None


def _currency(*candidates: str | None) -> str | None:
    for value in candidates:
        if value:
            return _CURRENCY_CODES.get(value.lower().rstrip("."))
    return None


def _number(text: str, thousands: bool) -> int:
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(?:[   .,']\d{3})+", text):
        value = float(re.sub(r"\D", "", text))
    else:
        value = float(text.replace(",", "."))
    return round(value * 1000 if thousands else value)


def _plausible(*values: int | None) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and all(0 < v <= MAX_REASONABLE for v in present)


def extract_salary(text: str, *, require_currency: bool = False) -> Salary | None:
    """Return the first salary-looking amount or range, or None.

    A bare number only counts if it has a currency or a "k"/"tis." suffix, so years
    ("2024") and experience ("3-5 let") are not mistaken for salaries. Amounts with a currency
    win over "k"-only ones ("10k users … 90 000 Kč" → 90 000 Kč). With ``require_currency``
    the "k" alone is never enough (used for long descriptions).
    """
    found = _extract(text, require_currency=True)
    if found is None and not require_currency:
        found = _extract(text, require_currency=False)
    return found


def _extract(text: str, *, require_currency: bool) -> Salary | None:
    for match in _RANGE_RE.finditer(text):
        currency = _currency(match["cur1"], match["cur2"], match["cur3"], match["cur4"])
        lo_k, hi_k = bool(match["lo_k"]), bool(match["hi_k"])
        if currency is None and (require_currency or not (lo_k or hi_k)):
            continue
        lo = _number(match["lo"], lo_k)
        hi = _number(match["hi"], hi_k)
        # "60-80k": the suffix applies to both ends
        if hi_k and not lo_k and lo < 1000:
            lo *= 1000
        if lo_k and not hi_k and hi < 1000:
            hi *= 1000
        if lo <= hi and _plausible(lo, hi):
            return Salary(lo, hi, currency)

    for match in _SINGLE_RE.finditer(text):
        currency = _currency(match["cur1"], match["cur2"])
        has_k = bool(match["k"])
        if currency is None and (require_currency or not has_k):
            continue
        value = _number(match["num"], has_k)
        if not _plausible(value):
            continue
        prefix = (match["prefix"] or "").lower()
        prefix = re.sub(r"\s+", " ", prefix)
        if prefix in _MIN_WORDS:
            return Salary(value, None, currency)
        if prefix in _MAX_WORDS:
            return Salary(None, value, currency)
        return Salary(value, value, currency)
    return None

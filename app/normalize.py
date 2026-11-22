"""Turning messy bank values into clean, comparable ones."""
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d",
    "%m-%d-%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%b %d %Y",
    "%Y%m%d", "%d.%m.%Y",
]


def parse_date(value: str, fmt: str) -> date:
    return datetime.strptime(value.strip(), fmt).date()


def detect_date_format(samples: list[str]) -> str | None:
    """Pick the first format that parses every non-empty sample.
    US month-first wins ties (e.g. 03/04/2026) because it is listed earlier;
    the user can override it on the mapping screen."""
    values = [s.strip() for s in samples if s and s.strip()]
    if not values:
        return None
    for fmt in DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v, fmt)
            return fmt
        except ValueError:
            continue
    return None


def looks_like_date(value: str) -> bool:
    return detect_date_format([value]) is not None


_AMOUNT_JUNK = re.compile(r"[\s$€£₹,]")


def parse_amount_cents(value: str | None) -> int | None:
    """'$1,234.56' -> 123456, '(12.30)' -> -1230, '12.30 CR' -> 1230, '12.30 DR' -> -1230, '' -> None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    sign = 1
    upper = s.upper()
    if upper.endswith("CR"):
        s = s[:-2]
    elif upper.endswith("DR"):
        s, sign = s[:-2], -1
    if s.startswith("(") and s.endswith(")"):
        s, sign = s[1:-1], -sign
    s = _AMOUNT_JUNK.sub("", s)
    if s.endswith("-"):  # some banks write trailing minus: 12.30-
        s, sign = s[:-1], -sign
    if s.startswith("+"):
        s = s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Not an amount: {value!r}")
    return sign * int((d * 100).quantize(Decimal("1")))


_PAYEE_NOISE = [
    re.compile(r"\b(POS|DEBIT CARD|CHECKCARD|PURCHASE|RECURRING|ACH|ONLINE|WEB PMT|PPD ID:?\s*\S+)\b", re.I),
    re.compile(r"\b(x{2,}|\*{2,})\d{2,}\b", re.I),        # masked card numbers
    re.compile(r"#\s?\d{3,}"),                            # store numbers
    re.compile(r"\b\d{2}/\d{2}\b"),                        # embedded dates
    re.compile(r"\s{2,}"),
]


def clean_payee(raw: str) -> str:
    s = raw or ""
    for pat in _PAYEE_NOISE[:-1]:
        s = pat.sub(" ", s)
    s = _PAYEE_NOISE[-1].sub(" ", s).strip(" -*")
    return s.title() if s.isupper() else s or raw.strip()


def fingerprint(*parts) -> str:
    joined = "\x1f".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def header_fingerprint(headers: list[str]) -> str:
    return fingerprint(*[h.strip().lower() for h in headers])

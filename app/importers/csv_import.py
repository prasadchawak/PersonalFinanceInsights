"""CSV import driven by a per-bank column mapping.

A mapping (stored as an import profile) looks like:
{
  "header_row": 0,                 # index of the header line (some banks put account info above it)
  "date_col": "Posting Date",
  "date_format": "%m/%d/%Y",
  "description_col": "Description",
  "memo_col": null,
  "amount_mode": "single" | "split",
  "amount_col": "Amount",          # single mode
  "negate": false,                 # single mode: true if the bank shows purchases as positive
  "debit_col": "Withdrawals",      # split mode: money out
  "credit_col": "Deposits"         # split mode: money in
}
"""
import csv
import io
from collections import Counter

from . import ParsedTxn
from ..normalize import detect_date_format, fingerprint, looks_like_date, parse_amount_cents, parse_date

_DATE_HINTS = ["transaction date", "trans date", "posting date", "post date", "posted date", "date"]
_DESC_HINTS = ["description", "payee", "merchant", "name", "details", "transaction", "narrative"]
_MEMO_HINTS = ["memo", "extended details", "notes", "reference"]
_AMOUNT_HINTS = ["amount", "transaction amount", "amt"]
_DEBIT_HINTS = ["debit", "withdrawal", "withdrawals", "money out", "debits", "charge"]
_CREDIT_HINTS = ["credit", "deposit", "deposits", "money in", "credits", "payment"]


def read_rows(text: str) -> list[list[str]]:
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]


def find_header_row(rows: list[list[str]]) -> int:
    """The header is the last row before data starts: no dates in it, and the next row has one."""
    for i in range(min(len(rows) - 1, 30)):
        here, nxt = rows[i], rows[i + 1]
        if len(here) >= 3 and not any(looks_like_date(c) for c in here) and any(looks_like_date(c) for c in nxt):
            return i
    return 0


def _pick(headers: list[str], hints: list[str], exclude: set[str] = frozenset()) -> str | None:
    lowered = {h: h.strip().lower() for h in headers if h not in exclude}
    for hint in hints:                       # exact match first
        for h, low in lowered.items():
            if low == hint:
                return h
    for hint in hints:                       # then substring match
        for h, low in lowered.items():
            if hint in low:
                return h
    return None


def suggest_mapping(rows: list[list[str]]) -> dict:
    hr = find_header_row(rows)
    headers = rows[hr]
    data = rows[hr + 1: hr + 51]
    date_col = _pick(headers, _DATE_HINTS)
    desc_col = _pick(headers, _DESC_HINTS, {date_col})
    memo_col = _pick(headers, _MEMO_HINTS, {date_col, desc_col})
    amount_col = _pick(headers, _AMOUNT_HINTS)
    debit_col = _pick(headers, _DEBIT_HINTS)
    credit_col = _pick(headers, _CREDIT_HINTS, {debit_col})
    mapping = {
        "header_row": hr,
        "date_col": date_col,
        "date_format": None,
        "description_col": desc_col,
        "memo_col": memo_col,
        "amount_mode": "single" if amount_col or not (debit_col and credit_col) else "split",
        "amount_col": amount_col,
        "negate": False,
        "debit_col": debit_col,
        "credit_col": credit_col,
    }
    if date_col:
        idx = headers.index(date_col)
        mapping["date_format"] = detect_date_format([r[idx] for r in data if idx < len(r)])
    return mapping


def validate_mapping(m: dict, headers: list[str]) -> list[str]:
    errors = []
    for key in ("date_col", "description_col"):
        if m.get(key) not in headers:
            errors.append(f"Choose a column for {key.replace('_col', '')}.")
    if not m.get("date_format"):
        errors.append("Choose a date format.")
    if m.get("amount_mode") == "split":
        if m.get("debit_col") not in headers or m.get("credit_col") not in headers:
            errors.append("Choose both a money-out and a money-in column.")
    elif m.get("amount_col") not in headers:
        errors.append("Choose the amount column.")
    return errors


def parse(rows: list[list[str]], m: dict) -> tuple[list[ParsedTxn], list[str]]:
    """Returns (transactions, row errors). Bad rows are reported, not silently dropped."""
    hr = m.get("header_row", 0)
    headers = [h.strip() for h in rows[hr]]
    col = {h: i for i, h in enumerate(headers)}

    def get(row, name):
        i = col.get(name)
        return row[i].strip() if name and i is not None and i < len(row) else ""

    parsed: list[ParsedTxn] = []
    errors: list[str] = []
    seen: Counter = Counter()
    for n, row in enumerate(rows[hr + 1:], start=hr + 2):
        raw_date = get(row, m["date_col"])
        if not raw_date:
            continue  # footer / blank lines
        try:
            iso = parse_date(raw_date, m["date_format"]).isoformat()
            if m.get("amount_mode") == "split":
                out_c = parse_amount_cents(get(row, m["debit_col"])) or 0
                in_c = parse_amount_cents(get(row, m["credit_col"])) or 0
                amount = abs(in_c) - abs(out_c)
            else:
                amount = parse_amount_cents(get(row, m["amount_col"]))
                if amount is None:
                    raise ValueError("empty amount")
                if m.get("negate"):
                    amount = -amount
        except ValueError as e:
            errors.append(f"Line {n}: {e}")
            continue
        payee = get(row, m["description_col"]) or "(no description)"
        memo = get(row, m.get("memo_col")) or None
        # Two genuinely identical charges on the same day (two coffees) are kept apart by an
        # occurrence counter, so re-importing an overlapping file still matches them one-to-one.
        key = (iso, amount, payee.lower())
        seen[key] += 1
        fp = fingerprint("csv", iso, amount, payee, seen[key])
        parsed.append(ParsedTxn(iso, amount, payee, memo, fp, raw=dict(zip(headers, row))))
    return parsed, errors

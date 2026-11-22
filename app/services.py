"""Import pipeline and queries shared by the web app and the tests."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from . import rules as rules_mod
from .importers import ParsedTxn
from .normalize import clean_payee


@dataclass
class ImportResult:
    import_id: int
    total: int
    new: int
    duplicate: int
    errors: list[str]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(conn: sqlite3.Connection, account_id: int, filename: str, file_hash: str, fmt: str,
          txns: list[ParsedTxn], errors: list[str] | None = None) -> ImportResult:
    rules = rules_mod.load(conn)
    cur = conn.execute(
        "INSERT INTO imports(account_id, filename, file_sha256, format, rows_total, rows_new, rows_duplicate)"
        " VALUES (?,?,?,?,?,0,0)", (account_id, filename, file_hash, fmt, len(txns)))
    import_id = cur.lastrowid
    new = 0
    for t in txns:
        payee = clean_payee(t.payee_raw)
        category_id = None
        r = rules_mod.first_match(rules, account_id, t.payee_raw, t.memo)
        if r:
            category_id = r["category_id"]
            payee = r["rename_to"] or payee
        cur = conn.execute(
            "INSERT OR IGNORE INTO transactions(account_id, posted_date, amount_cents, payee_raw, payee, memo,"
            " category_id, fingerprint, import_id, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (account_id, t.posted_date, t.amount_cents, t.payee_raw, payee, t.memo, category_id,
             t.fingerprint, import_id, json.dumps(t.raw)))
        new += cur.rowcount
    dup = len(txns) - new
    conn.execute("UPDATE imports SET rows_new = ?, rows_duplicate = ? WHERE id = ?", (new, dup, import_id))
    conn.commit()
    return ImportResult(import_id, len(txns), new, dup, errors or [])


def undo_import(conn: sqlite3.Connection, import_id: int) -> int:
    n = conn.execute("DELETE FROM transactions WHERE import_id = ?", (import_id,)).rowcount
    conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    conn.commit()
    return n


def account_balances(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT a.*, a.opening_balance_cents + COALESCE(SUM(t.amount_cents), 0) AS balance_cents,
               COUNT(t.id) AS tx_count, MAX(t.posted_date) AS last_date
        FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
        GROUP BY a.id ORDER BY a.type, a.name""").fetchall()


def month_summary(conn: sqlite3.Connection, month: str) -> dict:
    """Spending/income for a yyyy-mm month. Transfers are excluded so card payments aren't double counted."""
    rows = conn.execute("""
        SELECT c.id AS category_id, COALESCE(c.name, 'Uncategorized') AS category,
               COALESCE(c.kind, CASE WHEN t.amount_cents < 0 THEN 'expense' ELSE 'income' END) AS kind,
               SUM(t.amount_cents) AS total, COUNT(*) AS n
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id
        WHERE substr(t.posted_date, 1, 7) = ? AND COALESCE(c.kind, '') != 'transfer'
        GROUP BY 1, 2, 3 ORDER BY total""", (month,)).fetchall()
    spending = [r for r in rows if r["kind"] == "expense"]
    income = [r for r in rows if r["kind"] == "income"]
    spent = -sum(r["total"] for r in spending)
    earned = sum(r["total"] for r in income)
    return {"spending": spending, "income": income, "spent": spent, "earned": earned, "net": earned - spent}

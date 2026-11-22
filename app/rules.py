"""Categorisation rules: plain, explicit, yours. First matching rule (lowest priority number) wins."""
import re
import sqlite3


def _matches(rule: sqlite3.Row, text: str) -> bool:
    p, t = rule["pattern"], text or ""
    mt = rule["match_type"]
    if mt == "contains":
        return p.lower() in t.lower()
    if mt == "starts_with":
        return t.lower().startswith(p.lower())
    if mt == "equals":
        return t.strip().lower() == p.strip().lower()
    if mt == "regex":
        try:
            return re.search(p, t, re.I) is not None
        except re.error:
            return False
    return False


def load(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM rules ORDER BY priority, id").fetchall()


def first_match(rules: list[sqlite3.Row], account_id: int, payee_raw: str, memo: str | None):
    for r in rules:
        if r["account_id"] and r["account_id"] != account_id:
            continue
        if _matches(r, payee_raw) or (memo and _matches(r, memo)):
            return r
    return None


def apply_all(conn: sqlite3.Connection, only_uncategorized: bool = True) -> int:
    """Re-run rules over existing transactions. Never touches hand-set categories."""
    rules = load(conn)
    sql = ("SELECT id, account_id, payee_raw, memo FROM transactions t WHERE category_locked = 0"
           " AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)")
    if only_uncategorized:
        sql += " AND category_id IS NULL"
    changed = 0
    for tx in conn.execute(sql).fetchall():
        r = first_match(rules, tx["account_id"], tx["payee_raw"], tx["memo"])
        if not r:
            continue
        sets, args = [], []
        if r["category_id"]:
            sets.append("category_id = ?"); args.append(r["category_id"])
        if r["rename_to"]:
            sets.append("payee = ?"); args.append(r["rename_to"])
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", (*args, tx["id"]))
            changed += 1
    conn.commit()
    return changed


def match_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """How many transactions each rule would win (first match) — helps spot rules that never fire."""
    rules = load(conn)
    counts = {r["id"]: 0 for r in rules}
    for tx in conn.execute("SELECT account_id, payee_raw, memo FROM transactions"):
        r = first_match(rules, tx["account_id"], tx["payee_raw"], tx["memo"])
        if r:
            counts[r["id"]] += 1
    return counts

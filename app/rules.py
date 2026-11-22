"""Categorisation rules: plain, explicit, yours. First matching rule (lowest priority number) wins.

A rule has one or more conditions and ALL of them must match (AND). For "either A or B", make two rules.

Condition fields
  text       description OR memo (how single-condition rules have always worked)
  payee      the bank's description only
  memo       the memo only
  amount     the size of the transaction, ignoring sign (e.g. "more than 100")
  direction  money out / money in
The rule's optional account also has to match.
"""
import re
import sqlite3

TEXT_FIELDS = ("text", "payee", "memo")
TEXT_OPS = {"contains": "contains", "not_contains": "doesn't contain", "starts_with": "starts with",
            "equals": "equals", "regex": "matches regex"}
AMOUNT_OPS = {"eq": "=", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "between"}
FIELD_LABELS = {"text": "Description or memo", "payee": "Description", "memo": "Memo",
                "amount": "Amount", "direction": "Direction"}


class RuleError(ValueError):
    pass


def _text_match(op: str, pattern: str, text: str | None) -> bool:
    t, p = (text or ""), pattern or ""
    if op == "contains":
        return p.lower() in t.lower()
    if op == "not_contains":
        return p.lower() not in t.lower()
    if op == "starts_with":
        return t.lower().startswith(p.lower())
    if op == "equals":
        return t.strip().lower() == p.strip().lower()
    if op == "regex":
        try:
            return re.search(p, t, re.I) is not None
        except re.error:
            return False
    return False


def cond_matches(c: dict, payee_raw: str, memo: str | None, amount_cents: int | None) -> bool:
    f, op = c["field"], c["op"]
    if f == "text":
        # Positive tests pass if either description or memo matches; "doesn't contain" needs both to lack it.
        if op == "not_contains":
            return _text_match(op, c["value"], payee_raw) and _text_match(op, c["value"], memo)
        return _text_match(op, c["value"], payee_raw) or bool(memo and _text_match(op, c["value"], memo))
    if f == "payee":
        return _text_match(op, c["value"], payee_raw)
    if f == "memo":
        return _text_match(op, c["value"], memo)
    if f == "direction":
        if amount_cents is None:
            return False
        return (amount_cents < 0) if c["value"] == "out" else (amount_cents > 0)
    if f == "amount":
        if amount_cents is None:
            return False
        a, v, v2 = abs(amount_cents), int(c["value"]), int(c["value2"] or 0)
        return {"eq": a == v, "gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v,
                "between": min(v, v2) <= a <= max(v, v2)}.get(op, False)
    return False


def describe(c: dict) -> str:
    f, op = c["field"], c["op"]
    if f == "direction":
        return "money out" if c["value"] == "out" else "money in"
    if f == "amount":
        v = f"${int(c['value']) / 100:,.2f}"
        if op == "between":
            return f"amount between {v} and ${int(c['value2'] or 0) / 100:,.2f}"
        return f"amount {AMOUNT_OPS[op]} {v}"
    return f"{FIELD_LABELS[f].lower()} {TEXT_OPS.get(op, op)} “{c['value']}”"


def load(conn: sqlite3.Connection) -> list[dict]:
    rules = [dict(r) for r in conn.execute("SELECT * FROM rules ORDER BY priority, id")]
    conds: dict[int, list[dict]] = {}
    for c in conn.execute("SELECT * FROM rule_conditions ORDER BY id"):
        conds.setdefault(c["rule_id"], []).append(dict(c))
    for r in rules:
        r["conds"] = conds.get(r["id"], [])
    return rules


def first_match(rules: list[dict], account_id: int, payee_raw: str, memo: str | None,
                amount_cents: int | None = None):
    for r in rules:
        if r["account_id"] and r["account_id"] != account_id:
            continue
        if r["conds"] and all(cond_matches(c, payee_raw, memo, amount_cents) for c in r["conds"]):
            return r
    return None


def validate_conditions(conds: list[dict]) -> list[dict]:
    """Clean and check conditions from a form. Raises RuleError with a readable message."""
    out = []
    for c in conds:
        f, op = c.get("field"), c.get("op")
        if f in TEXT_FIELDS:
            val = (c.get("value") or "").strip()
            if not val:
                continue
            if op not in TEXT_OPS:
                raise RuleError("Unknown text test.")
            if op == "regex":
                try:
                    re.compile(val)
                except re.error as e:
                    raise RuleError(f"That regex isn't valid: {e}.")
            out.append({"field": f, "op": op, "value": val, "value2": None})
        elif f == "amount":
            if c.get("value") in (None, ""):
                continue
            if op not in AMOUNT_OPS:
                raise RuleError("Unknown amount test.")
            if op == "between" and c.get("value2") in (None, ""):
                raise RuleError("“Between” needs two amounts.")
            out.append({"field": f, "op": op, "value": str(abs(int(c["value"]))),
                        "value2": str(abs(int(c["value2"]))) if op == "between" else None})
        elif f == "direction":
            if c.get("value") in ("in", "out"):
                out.append({"field": f, "op": "is", "value": c["value"], "value2": None})
    if not any(c["field"] in TEXT_FIELDS for c in out) and not any(c["field"] == "amount" for c in out):
        raise RuleError("A rule needs at least one text or amount condition.")
    return out


def save(conn, rule_id: int | None, conds: list[dict], category_id, rename_to, account_id, priority) -> int:
    if not category_id and not rename_to:
        raise RuleError("A rule needs a category or a rename.")
    conds = validate_conditions(conds)
    first_text = next((c for c in conds if c["field"] in TEXT_FIELDS and c["op"] != "not_contains"), None)
    # match_type/pattern are kept as a readable summary of the main text condition.
    mt, pat = (first_text["op"], first_text["value"]) if first_text else ("contains", "")
    if rule_id is None:
        rule_id = conn.execute("INSERT INTO rules(priority, match_type, pattern, account_id, category_id, rename_to)"
                               " VALUES (?,?,?,?,?,?)", (priority, mt, pat, account_id, category_id, rename_to)).lastrowid
    else:
        conn.execute("UPDATE rules SET priority=?, match_type=?, pattern=?, account_id=?, category_id=?, rename_to=?"
                     " WHERE id=?", (priority, mt, pat, account_id, category_id, rename_to, rule_id))
        conn.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
    conn.executemany("INSERT INTO rule_conditions(rule_id, field, op, value, value2) VALUES (?,?,?,?,?)",
                     [(rule_id, c["field"], c["op"], c["value"], c["value2"]) for c in conds])
    return rule_id


def apply_all(conn: sqlite3.Connection, only_uncategorized: bool = True) -> int:
    """Re-run rules over existing transactions. Never touches hand-set categories or split transactions."""
    rules = load(conn)
    sql = ("SELECT id, account_id, payee_raw, memo, amount_cents FROM transactions t WHERE category_locked = 0"
           " AND NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)")
    if only_uncategorized:
        sql += " AND category_id IS NULL"
    changed = 0
    for tx in conn.execute(sql).fetchall():
        r = first_match(rules, tx["account_id"], tx["payee_raw"], tx["memo"], tx["amount_cents"])
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
    for tx in conn.execute("SELECT account_id, payee_raw, memo, amount_cents FROM transactions"):
        r = first_match(rules, tx["account_id"], tx["payee_raw"], tx["memo"], tx["amount_cents"])
        if r:
            counts[r["id"]] += 1
    return counts

"""Categorisation rules: plain, explicit, yours. First matching rule (lowest priority number) wins.

A rule has one or more conditions and ALL of them must match (AND). For "either A or B", make two rules.

Condition fields
  text       description OR memo; "description" means the bank's original text or the name shown in the app
  payee      description only (original or shown name)
  memo       the memo only
  amount     the size of the transaction as a positive number, e.g. "= 24.85" or "more than 100".
             Whether it's money out or in is a separate choice (direction). If a negative number is
             typed anyway, it is read as "money out" and its size is used.
  direction  money out / money in
The rule's optional account also has to match.
"""
import re
import sqlite3

TEXT_FIELDS = ("text", "payee", "memo")
TEXT_OPS = {"contains": "contains", "not_contains": "doesn't contain", "starts_with": "starts with",
            "equals": "equals", "regex": "matches regex"}
AMOUNT_OPS = {"eq": "equals", "gt": "more than", "gte": "at least", "lt": "less than", "lte": "at most",
              "between": "between"}
FIELD_LABELS = {"text": "Description or memo", "payee": "Description", "memo": "Memo",
                "amount": "Amount", "direction": "Direction"}


class RuleError(ValueError):
    pass


def _any_text(op: str, pattern: str, texts: list[str | None]) -> bool:
    """Positive tests pass if any of the texts matches; "doesn't contain" needs all of them to lack it."""
    texts = [t for t in texts if t]
    if op == "not_contains":
        return all(_text_match(op, pattern, t) for t in texts)
    return any(_text_match(op, pattern, t) for t in texts)


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


def cond_matches(c: dict, payee_raw: str, memo: str | None, amount_cents: int | None,
                 payee: str | None = None) -> bool:
    f, op = c["field"], c["op"]
    if f == "text":
        return _any_text(op, c["value"], [payee_raw, payee, memo])
    if f == "payee":
        return _any_text(op, c["value"], [payee_raw, payee])
    if f == "memo":
        return _text_match(op, c["value"], memo)
    if f == "direction":
        if amount_cents is None:
            return False
        return (amount_cents < 0) if c["value"] == "out" else (amount_cents > 0)
    if f == "amount":
        if amount_cents is None:
            return False
        v, v2 = int(c["value"]), int(c["value2"] or 0)
        if v < 0 or v2 < 0:          # a minus sign means "money out" (also covers rules saved by v0.4)
            if amount_cents >= 0:
                return False
            v, v2 = abs(v), abs(v2)
        a = abs(amount_cents)
        return {"eq": a == v, "gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v,
                "between": min(v, v2) <= a <= max(v, v2)}.get(op, False)
    return False


def _m(cents) -> str:
    cents = int(cents or 0)
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def describe(c: dict) -> str:
    f, op = c["field"], c["op"]
    if f == "direction":
        return "money out" if c["value"] == "out" else "money in"
    if f == "amount":
        out = " (money out)" if int(c["value"]) < 0 or int(c["value2"] or 0) < 0 else ""
        v, v2 = abs(int(c["value"])), abs(int(c["value2"] or 0))
        if op == "between":
            return f"amount between {_m(min(v, v2))} and {_m(max(v, v2))}{out}"
        return f"amount {AMOUNT_OPS[op]} {_m(v)}{out}"
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
                amount_cents: int | None = None, payee: str | None = None):
    for r in rules:
        if rule_matches(r, account_id, payee_raw, memo, amount_cents, payee):
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
            op = op or "eq"   # typed an amount but left the test on "any amount": treat as "equals"
            if op not in AMOUNT_OPS:
                raise RuleError("Unknown amount test.")
            if op == "between" and c.get("value2") in (None, ""):
                raise RuleError("“Between” needs two amounts.")
            v, v2 = int(c["value"]), (int(c["value2"]) if op == "between" else None)
            if v < 0 or (v2 or 0) < 0:   # minus sign typed: store as size + "money out"
                out.append({"field": "direction", "op": "is", "value": "out", "value2": None})
            out.append({"field": f, "op": op, "value": str(abs(v)),
                        "value2": str(abs(v2)) if v2 is not None else None})
        elif f == "direction":
            if c.get("value") in ("in", "out"):
                out.append({"field": f, "op": "is", "value": c["value"], "value2": None})
    dirs = [c for c in out if c["field"] == "direction"]
    if len({d["value"] for d in dirs}) > 1:
        raise RuleError("A negative amount means money out, but the rule says money in. Use a positive amount for money in.")
    out = [c for c in out if c["field"] != "direction"] + dirs[:1]
    if not any(c["field"] in TEXT_FIELDS for c in out) and not any(c["field"] == "amount" for c in out):
        raise RuleError("A rule needs at least one text or amount condition.")
    return out


def save(conn, rule_id: int | None, conds: list[dict], category_id, rename_to, account_id, priority,
         set_excluded: bool = False) -> int:
    if not category_id and not rename_to and not set_excluded:
        raise RuleError("A rule needs to do something: set a category, rename the payee, or exclude from reports.")
    conds = validate_conditions(conds)
    first_text = next((c for c in conds if c["field"] in TEXT_FIELDS and c["op"] != "not_contains"), None)
    # match_type/pattern are kept as a readable summary of the main text condition.
    mt, pat = (first_text["op"], first_text["value"]) if first_text else ("contains", "")
    if rule_id is None:
        rule_id = conn.execute("INSERT INTO rules(priority, match_type, pattern, account_id, category_id, rename_to,"
                               " set_excluded) VALUES (?,?,?,?,?,?,?)",
                               (priority, mt, pat, account_id, category_id, rename_to, int(bool(set_excluded)))).lastrowid
    else:
        conn.execute("UPDATE rules SET priority=?, match_type=?, pattern=?, account_id=?, category_id=?, rename_to=?,"
                     " set_excluded=? WHERE id=?",
                     (priority, mt, pat, account_id, category_id, rename_to, int(bool(set_excluded)), rule_id))
        conn.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
    conn.executemany("INSERT INTO rule_conditions(rule_id, field, op, value, value2) VALUES (?,?,?,?,?)",
                     [(rule_id, c["field"], c["op"], c["value"], c["value2"]) for c in conds])
    return rule_id


def apply_all(conn: sqlite3.Connection, only_uncategorized: bool = True, include_hand_set: bool = False) -> int:
    """Re-run rules over existing transactions. Split transactions are never touched. Hand-set (locked)
    categories are skipped unless include_hand_set — then a matching rule replaces them and unlocks them."""
    rules = load(conn)
    sql = ("SELECT id, account_id, payee_raw, payee, memo, amount_cents FROM transactions t WHERE"
           " NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)")
    if not include_hand_set:
        sql += " AND category_locked = 0"
    if only_uncategorized:
        sql += " AND category_id IS NULL"
    changed = 0
    for tx in conn.execute(sql).fetchall():
        r = first_match(rules, tx["account_id"], tx["payee_raw"], tx["memo"], tx["amount_cents"], tx["payee"])
        if not r:
            continue
        sets, args = [], []
        if r["category_id"]:
            sets.append("category_id = ?"); args.append(r["category_id"])
            sets.append("category_locked = 0")
        if r["rename_to"]:
            sets.append("payee = ?"); args.append(r["rename_to"])
        if r.get("set_excluded"):
            sets.append("excluded = 1")
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", (*args, tx["id"]))
            changed += 1
    conn.commit()
    return changed


def rule_matches(r: dict, account_id: int, payee_raw: str, memo: str | None, amount_cents: int | None,
                 payee: str | None = None) -> bool:
    if r["account_id"] and r["account_id"] != account_id:
        return False
    return bool(r["conds"]) and all(cond_matches(c, payee_raw, memo, amount_cents, payee) for c in r["conds"])


def match_counts(conn: sqlite3.Connection) -> dict[int, dict]:
    """Per rule: 'wins' = transactions it categorises (it's the first match); 'shadowed' = transactions it
    also matches but an earlier rule (lower priority number, or same priority and created earlier) wins.
    A rule with wins 0 and shadowed > 0 needs a lower priority number to take effect."""
    rules = load(conn)
    out = {r["id"]: {"wins": 0, "shadowed": 0, "blockers": {}} for r in rules}
    names = {r["id"]: (describe(r["conds"][0]) if r["conds"] else f"rule {r['id']}") for r in rules}
    for tx in conn.execute("SELECT account_id, payee_raw, payee, memo, amount_cents FROM transactions"):
        hits = [r for r in rules if rule_matches(r, tx["account_id"], tx["payee_raw"], tx["memo"], tx["amount_cents"],
                                                 tx["payee"])]
        if not hits:
            continue
        out[hits[0]["id"]]["wins"] += 1
        for r in hits[1:]:
            out[r["id"]]["shadowed"] += 1
            b = out[r["id"]]["blockers"]
            b[names[hits[0]["id"]]] = b.get(names[hits[0]["id"]], 0) + 1
    return out

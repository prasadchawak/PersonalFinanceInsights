"""Credit card rewards — estimated locally from card terms + your transactions. No network.

Vocabulary
- program: a credit account's card setup (reward unit, value per unit, base rate, annual fee)
- earn rule: a higher rate for some purchases (categories / merchants), optionally capped per
  month/quarter/year (rules in the same cap group share one cap), limited to dates, or needing activation
- rate: units per $1 (cash back: 3 = 3%); value in cents = dollars x rate x cpp

How a purchase earns: the purchase goes to its matching rules from highest rate down; each takes as
much as its remaining cap allows; whatever is left earns the base rate. Caps fill in date order and use
calendar periods (issuers may use billing cycles, so treat cap edges as approximate).
Refunds take back what a purchase of that size would have earned (caps are not refilled).
Card payments and transfers (transfer-kind categories) earn nothing. Splits earn per part. Transactions
excluded from reports still earn — exclusion is about budgeting, not about what the card paid you.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from . import categories as cats

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "card_catalog.json"
NETWORKS = ["Visa", "Mastercard", "Amex", "Discover", "Other"]
UNITS = ["cash", "points", "miles"]

# Merchants that only take some networks (US). Keeps suggestions honest.
ACCEPTANCE = [
    ({"COSTCO"}, {"Visa"}),
]


class RewardsError(ValueError):
    pass


# ---------------------------------------------------------------- catalog

@lru_cache(maxsize=1)
def catalog() -> dict:
    data = json.loads(CATALOG_PATH.read_text())
    data["by_key"] = {c["key"]: c for c in data["cards"]}
    return data


def catalog_groups() -> list[tuple[str, list[dict]]]:
    """Cards grouped by issuer for the picker (generic templates last)."""
    groups: dict[str, list] = {}
    for c in catalog()["cards"]:
        groups.setdefault(c["issuer"], []).append(c)
    order = sorted((k for k in groups if k != "—"), key=str.lower) + (["—"] if "—" in groups else [])
    return [(k, sorted(groups[k], key=lambda c: c["name"])) for k in order]


def _resolve_categories(tree: cats.Tree, names: list[str]) -> tuple[list[int], list[str]]:
    by_name: dict[str, cats.Cat] = {}
    for c in tree.ordered():                       # shallowest wins if a name repeats
        by_name.setdefault(c.name.lower(), c)
    ids, missing = [], []
    for n in names or []:
        c = by_name.get(n.lower())
        (ids.append(c.id) if c else missing.append(n))
    return ids, missing


def apply_profile(conn: sqlite3.Connection, account_id: int, key: str) -> dict:
    """Set a credit account's card from the catalog (replaces its earn rules). Returns unmapped names."""
    card = catalog()["by_key"].get(key)
    if not card:
        raise RewardsError("Unknown card.")
    acct = conn.execute("SELECT type FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not acct or acct["type"] != "credit":
        raise RewardsError("Rewards can only be set up on credit accounts.")
    tree = cats.load(conn)
    conn.execute("""INSERT INTO card_programs(account_id, catalog_key, card_name, network, unit, cpp, base_rate,
                    annual_fee_cents, terms_checked, notes) VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account_id) DO UPDATE SET catalog_key=excluded.catalog_key,
                    card_name=excluded.card_name, network=excluded.network, unit=excluded.unit, cpp=excluded.cpp,
                    base_rate=excluded.base_rate, annual_fee_cents=excluded.annual_fee_cents,
                    terms_checked=excluded.terms_checked, notes=excluded.notes""",
                 (account_id, key, card["name"], card["network"], card["unit"], card["cpp"], card["base"],
                  card["annual_fee"], card["checked"],
                  " ".join(x for x in (card.get("notes"), card.get("membership")) if x) or None))
    conn.execute("DELETE FROM earn_rules WHERE account_id = ?", (account_id,))
    unmapped_all = []
    for r in card["rules"]:
        ids, missing = _resolve_categories(tree, r.get("categories", []))
        unmapped_all += missing
        if r.get("categories") and not ids and not r.get("merchants"):
            pass  # keep the rule; it shows as "needs a category" until mapped
        conn.execute("""INSERT INTO earn_rules(account_id, label, rate, category_ids, merchants, exclude, cap_cents,
                        cap_period, cap_group, start_date, end_date, needs_activation, activated, unmapped)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (account_id, r["label"], r["rate"], ",".join(map(str, ids)) or None,
                      "|".join(r.get("merchants") or []) or None, "|".join(r.get("exclude") or []) or None,
                      r.get("cap"), r.get("period"), r.get("group"), r.get("start"), r.get("end"),
                      1 if r.get("activation") else 0, 0 if r.get("activation") else 1,
                      ", ".join(missing) or None))
    return {"card": card, "unmapped": sorted(set(unmapped_all))}


# ---------------------------------------------------------------- loading

@dataclass
class Rule:
    id: int
    label: str
    rate: float
    cat_ids: set[int]
    has_categories: bool
    merchants: list[str]
    exclude: list[str]
    cap: int | None
    period: str | None
    group: str
    start: str | None
    end: str | None
    needs_activation: bool
    activated: bool
    unmapped: str | None

    def active_on(self, d: str) -> bool:
        if self.start and d < self.start:
            return False
        if self.end and d > self.end:
            return False
        return not (self.needs_activation and not self.activated)

    def in_window(self, d: str) -> bool:
        return not ((self.start and d < self.start) or (self.end and d > self.end))


@dataclass
class Program:
    account_id: int
    account_name: str
    card_name: str
    network: str
    unit: str
    cpp: float
    base: float
    fee: int
    checked: str | None
    notes: str | None
    rules: list[Rule] = field(default_factory=list)


def _split(v):
    return [x.strip().upper() for x in (v or "").split("|") if x.strip()]


def load_programs(conn: sqlite3.Connection) -> dict[int, Program]:
    tree = cats.load(conn)
    progs = {}
    for p in conn.execute("""SELECT cp.*, a.name AS account_name FROM card_programs cp
                             JOIN accounts a ON a.id = cp.account_id WHERE a.type = 'credit' ORDER BY a.name"""):
        progs[p["account_id"]] = Program(p["account_id"], p["account_name"], p["card_name"], p["network"], p["unit"],
                                         p["cpp"], p["base_rate"], p["annual_fee_cents"], p["terms_checked"], p["notes"])
    for r in conn.execute("SELECT * FROM earn_rules ORDER BY rate DESC, id"):
        if r["account_id"] not in progs:
            continue
        ids = [int(x) for x in (r["category_ids"] or "").split(",") if x.strip()]
        expanded = set()
        for i in ids:
            expanded.update(tree.descendants(i))
        progs[r["account_id"]].rules.append(Rule(
            r["id"], r["label"], r["rate"], expanded, bool(ids) or bool(r["unmapped"]), _split(r["merchants"]),
            _split(r["exclude"]), r["cap_cents"], r["cap_period"], r["cap_group"] or f"rule{r['id']}",
            r["start_date"], r["end_date"], bool(r["needs_activation"]), bool(r["activated"]), r["unmapped"]))
    return progs


# ---------------------------------------------------------------- matching & earning

def period_key(d: str, period: str | None) -> str:
    if period == "month":
        return d[:7]
    if period == "quarter":
        return f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"
    return d[:4]


def _text(line) -> str:
    return " ".join(x for x in (line.get("payee_raw"), line.get("payee"), line.get("memo")) if x).upper()


def rule_matches(r: Rule, line: dict, d: str) -> bool:
    if not r.active_on(d):
        return False
    if r.has_categories and line.get("category_id") not in r.cat_ids:
        return False
    text = _text(line)
    if r.merchants and not any(m in text for m in r.merchants):
        return False
    if r.exclude and any(m in text for m in r.exclude):
        return False
    return bool(r.has_categories or r.merchants)


def accepted(p: Program, line: dict) -> bool:
    text = _text(line)
    for merchants, networks in ACCEPTANCE:
        # "Other" means we don't know the network, so don't rule the card out.
        if any(m in text for m in merchants) and p.network != "Other" and p.network not in networks:
            return False
    return True


class Ledger:
    """Cap usage per (card, cap group, period)."""

    def __init__(self):
        self.used: dict[tuple, int] = {}

    def left(self, p: Program, r: Rule, d: str) -> int | None:
        if not r.cap:
            return None
        return max(0, r.cap - self.used.get((p.account_id, r.group, period_key(d, r.period)), 0))

    def add(self, p: Program, r: Rule, d: str, cents: int):
        if r.cap:
            k = (p.account_id, r.group, period_key(d, r.period))
            self.used[k] = self.used.get(k, 0) + cents


def earn(p: Program, line: dict, ledger: Ledger, commit: bool = True) -> dict:
    """Value (cents, float) a purchase earns on card p. Negative for refunds."""
    d, amt = line["posted_date"], line["amount_cents"]
    spend = abs(amt)
    matching = [r for r in p.rules if r.rate > p.base and rule_matches(r, line, d)]
    if amt > 0:                                   # refund: claw back at the best matching rate
        rate = max([p.base] + [r.rate for r in matching])
        return {"value": -spend / 100 * rate * p.cpp, "rule": None, "rate": rate}
    remaining, units, top = spend, 0.0, None
    for r in matching:                            # already sorted by rate desc
        left = ledger.left(p, r, d)
        take = remaining if left is None else min(remaining, left)
        if take <= 0:
            continue
        units += take / 100 * r.rate
        top = top or r
        if commit:
            ledger.add(p, r, d, take)
        remaining -= take
        if not remaining:
            break
    units += remaining / 100 * p.base
    return {"value": units * p.cpp, "rule": top, "rate": units / (spend / 100) if spend else p.base}


# ---------------------------------------------------------------- whole-history computation

def _purchase_lines(conn, end: str, start_from: str):
    """All lines from the start of the year containing start_from (so yearly caps fill correctly)."""
    return conn.execute("""
        SELECT l.tx_id, l.account_id, l.posted_date, l.amount_cents, l.category_id, t.payee_raw, t.payee, t.memo,
               COALESCE(c.kind, '') AS kind
        FROM tx_lines l JOIN transactions t ON t.id = l.tx_id
        LEFT JOIN categories c ON c.id = l.category_id
        WHERE l.posted_date BETWEEN ? AND ? ORDER BY l.posted_date, l.tx_id""",
                        (f"{start_from[:4]}-01-01", end)).fetchall()


def compute(conn: sqlite3.Connection, start: str, end: str, progs: dict[int, Program] | None = None) -> dict:
    """Actual estimated rewards on your cards, and what the best of your cards would have earned
    (each purchase on the card that pays most at that moment, caps respected)."""
    progs = progs if progs is not None else load_programs(conn)
    actual_ledger, best_ledger = Ledger(), Ledger()
    lines = []
    for row in _purchase_lines(conn, end, start):
        line = dict(row)
        if line["kind"] == "transfer" or line["amount_cents"] == 0:
            continue
        # Money in only counts as a refund when it's in a spending category (not a payment or cashback credit).
        if line["amount_cents"] > 0 and line["kind"] != "expense":
            continue
        in_range = line["posted_date"] >= start
        on_card = progs.get(line["account_id"])
        actual = earn(on_card, line, actual_ledger) if on_card else None
        # Best card among those you hold — only for purchases you made on a card (rent from checking
        # usually can't go on a card, so it isn't counted as a "miss").
        best, best_p = None, None
        if on_card and line["amount_cents"] < 0:
            for p in progs.values():
                if not accepted(p, line):
                    continue
                e = earn(p, line, best_ledger, commit=False)
                if best is None or e["value"] > best["value"] + 1e-9:
                    best, best_p = e, p
            if best_p:
                earn(best_p, line, best_ledger, commit=True)
        if in_range:
            lines.append({**line, "actual": actual, "card": on_card, "best": best, "best_card": best_p})
    return {"lines": lines, "programs": progs, "ledger": actual_ledger}


def summarize(conn: sqlite3.Connection, start: str, end: str) -> dict:
    res = compute(conn, start, end)
    progs = res["programs"]
    tree = cats.load(conn)
    by_card = {aid: {"program": p, "spend": 0, "earned": 0.0, "last_used": None, "n": 0} for aid, p in progs.items()}
    missed_cat: dict = {}
    missed_merchant: dict = {}
    monthly: dict = {}
    total_earned = total_best = 0.0
    card_spend = 0
    for ln in res["lines"]:
        if ln["actual"] is not None:
            c = by_card[ln["account_id"]]
            c["earned"] += ln["actual"]["value"]
            if ln["amount_cents"] < 0:
                c["spend"] += -ln["amount_cents"]
                c["n"] += 1
                c["last_used"] = max(c["last_used"] or "", ln["posted_date"])
                card_spend += -ln["amount_cents"]
            total_earned += ln["actual"]["value"]
            m = ln["posted_date"][:7]
            monthly.setdefault(m, {})
            monthly[m][ln["account_id"]] = monthly[m].get(ln["account_id"], 0) + ln["actual"]["value"]
        if ln["best"] is not None and ln["amount_cents"] < 0:
            got = ln["actual"]["value"] if ln["actual"] else 0.0
            total_best += ln["best"]["value"]
            miss = ln["best"]["value"] - got
            if miss > 0.5:                         # ignore fractions of a cent
                cat = tree.by_id.get(ln["category_id"])
                key = cat.path if cat else "Uncategorised"
                e = missed_cat.setdefault(key, {"missed": 0.0, "spend": 0, "best_cards": {}})
                e["missed"] += miss; e["spend"] += -ln["amount_cents"]
                e["best_cards"][ln["best_card"].account_name] = e["best_cards"].get(ln["best_card"].account_name, 0) + miss
                e2 = missed_merchant.setdefault(ln["payee"], {"missed": 0.0, "spend": 0, "best_cards": {}})
                e2["missed"] += miss; e2["spend"] += -ln["amount_cents"]
                e2["best_cards"][ln["best_card"].account_name] = e2["best_cards"].get(ln["best_card"].account_name, 0) + miss
    for c in by_card.values():
        c["rate"] = (c["earned"] / c["spend"] * 100) if c["spend"] else None
        c["net"] = c["earned"] - c["program"].fee * _years(start, end)
    top = lambda d: sorted(({"label": k, **v, "best_card": max(v["best_cards"], key=v["best_cards"].get)}
                            for k, v in d.items()), key=lambda x: -x["missed"])[:12]
    return {"by_card": sorted(by_card.values(), key=lambda c: -c["earned"]), "earned": total_earned,
            "best": total_best, "missed": max(0.0, total_best - sum(
                (ln["actual"]["value"] if ln["actual"] else 0.0) for ln in res["lines"]
                if ln["best"] is not None and ln["amount_cents"] < 0)),
            "card_spend": card_spend, "missed_by_category": top(missed_cat), "missed_by_merchant": top(missed_merchant),
            "monthly": monthly, "programs": progs, "result": res}


def _years(start: str, end: str) -> float:
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return max(days, 1) / 365.0


# ---------------------------------------------------------------- recommendations & alerts

def recommendations(conn: sqlite3.Connection, today: date | None = None, lookback_days: int = 120,
                    limit: int = 12) -> list[dict]:
    """For what you actually buy (top category + merchant groups recently), which of your cards pays most today,
    given what's left of each cap this period."""
    today = today or date.today()
    t = today.isoformat()
    res = compute(conn, t, t)                     # fills each card's real cap usage up to today
    progs, ledger = res["programs"], res["ledger"]
    if not progs:
        return []
    since = (today - timedelta(days=lookback_days)).isoformat()
    groups = conn.execute("""
        SELECT l.category_id, t.payee, MIN(t.payee_raw) AS payee_raw, COUNT(*) AS n, -SUM(l.amount_cents) AS spend
        FROM tx_lines l JOIN transactions t ON t.id = l.tx_id LEFT JOIN categories c ON c.id = l.category_id
        JOIN accounts a ON a.id = l.account_id
        WHERE l.posted_date >= ? AND l.amount_cents < 0 AND COALESCE(c.kind, 'expense') = 'expense'
          AND a.type = 'credit'
        GROUP BY l.category_id, t.payee ORDER BY spend DESC LIMIT ?""", (since, limit * 3)).fetchall()
    tree = cats.load(conn)
    out, seen = [], set()
    for g in groups:
        typical = max(100, int(g["spend"] / max(1, g["n"])))
        line = {"posted_date": t, "amount_cents": -typical, "category_id": g["category_id"],
                "payee_raw": g["payee_raw"], "payee": g["payee"], "memo": None}
        options = []
        for p in progs.values():
            if not accepted(p, line):
                continue
            e = earn(p, line, ledger, commit=False)
            cap_note = None
            if e["rule"] and e["rule"].cap:
                left = ledger.left(p, e["rule"], t)
                cap_note = {"left": left, "period": e["rule"].period, "end": e["rule"].end}
            options.append({"program": p, "pct": e["value"] / typical * 100, "rule": e["rule"], "cap": cap_note})
        if not options:
            continue
        options.sort(key=lambda o: -o["pct"])
        cat = tree.by_id.get(g["category_id"])
        key = (options[0]["program"].account_id, cat.path if cat else None, options[0]["rule"].id if options[0]["rule"] else None)
        label_cat = cat.path if cat else "Uncategorised"
        out.append({"category": label_cat, "merchant": g["payee"], "spend": g["spend"], "n": g["n"],
                    "best": options[0], "runner_up": options[1] if len(options) > 1 else None,
                    "dupe_key": key})
    # collapse merchants that get the same answer inside a category, keep the biggest
    final = []
    for r in out:
        if r["dupe_key"] in seen:
            continue
        seen.add(r["dupe_key"])
        final.append(r)
    return final[:limit]


def alerts(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    today = today or date.today()
    t = today.isoformat()
    soon = (today + timedelta(days=21)).isoformat()
    progs = load_programs(conn)
    res = compute(conn, t, t, progs)
    ledger = res["ledger"]
    last = dict(conn.execute("""SELECT account_id, MAX(posted_date) FROM transactions WHERE amount_cents < 0
                                GROUP BY account_id""").fetchall())
    out = []
    for p in progs.values():
        rotating = [r for r in p.rules if r.group == "rotating"]
        for r in p.rules:
            if r.needs_activation and not r.activated and r.in_window(t):
                out.append({"level": "warn", "account_id": p.account_id,
                            "text": f"{p.account_name}: activate “{r.label}” — it earns the base rate until you do."})
            if r.end and t <= r.end <= soon:
                out.append({"level": "info", "account_id": p.account_id,
                            "text": f"{p.account_name}: “{r.label}” ends {r.end}."})
            if r.cap and r.active_on(t):
                left = ledger.left(p, r, t)
                if left is not None and left <= r.cap * 0.2:
                    out.append({"level": "info", "account_id": p.account_id,
                                "text": f"{p.account_name}: “{r.label}” cap nearly used — ${left / 100:,.0f} left this {r.period}."})
            if r.unmapped:
                out.append({"level": "warn", "account_id": p.account_id,
                            "text": f"{p.account_name}: “{r.label}” needs a category ({r.unmapped} isn't one of yours)."})
        if rotating and not any(r.in_window(t) for r in rotating):
            out.append({"level": "warn", "account_id": p.account_id,
                        "text": f"{p.account_name}: no rotating 5% categories for this quarter — add them from the issuer's calendar."})
        lu = last.get(p.account_id)
        if lu and lu < (today - timedelta(days=150)).isoformat():
            out.append({"level": "info", "account_id": p.account_id,
                        "text": f"{p.account_name}: not used since {lu}. Issuers can close inactive cards — a small recurring charge keeps it alive."})
        if p.checked and p.checked < (today - timedelta(days=183)).strftime("%Y-%m"):
            out.append({"level": "info", "account_id": p.account_id,
                        "text": f"{p.account_name}: card terms last checked {p.checked} — worth re-checking with the issuer."})
    return out

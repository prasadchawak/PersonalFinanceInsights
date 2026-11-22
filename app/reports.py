"""Report data. All money in integer cents; transfers are always excluded from income/spending."""
import sqlite3
from datetime import date, timedelta

from . import categories as cats

PERIODS = [
    ("this_month", "This month"), ("last_month", "Last month"), ("3m", "Last 3 months"),
    ("6m", "Last 6 months"), ("12m", "Last 12 months"), ("ytd", "Year to date"),
    ("last_year", "Last year"), ("all", "All time"), ("custom", "Custom…"),
]


def _month_start(d: date, back: int = 0) -> date:
    idx = d.year * 12 + (d.month - 1) - back   # months since year 0; works both ways
    return date(idx // 12, idx % 12 + 1, 1)


def period_range(preset: str, start: str | None, end: str | None, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    if preset == "custom":
        try:
            s, e = date.fromisoformat(start or ""), date.fromisoformat(end or "")
            return (min(s, e).isoformat(), max(s, e).isoformat())
        except ValueError:
            preset = "this_month"
    if preset == "last_month":
        s = _month_start(today, 1)
        return s.isoformat(), (_month_start(today) - timedelta(days=1)).isoformat()
    if preset in ("3m", "6m", "12m"):
        return _month_start(today, int(preset[:-1]) - 1).isoformat(), today.isoformat()
    if preset == "ytd":
        return date(today.year, 1, 1).isoformat(), today.isoformat()
    if preset == "last_year":
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()
    if preset == "all":
        return "0000-01-01", "9999-12-31"
    return _month_start(today).isoformat(), today.isoformat()


def months_between(start: str, end: str) -> list[str]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    out, cur = [], date(s.year, s.month, 1)
    while cur <= e:
        out.append(cur.strftime("%Y-%m"))
        cur = _month_start(cur, -1)
    return out


def data_bounds(conn, account: int | None = None) -> tuple[str, str] | None:
    sql = "SELECT MIN(posted_date), MAX(posted_date) FROM transactions" + (" WHERE account_id = ?" if account else "")
    lo, hi = conn.execute(sql, (account,) if account else ()).fetchone()
    return (lo, hi) if lo else None


def _where(start, end, account):
    w, a = ["t.posted_date BETWEEN ? AND ?"], [start, end]
    if account:
        w.append("t.account_id = ?"); a.append(account)
    return " AND ".join(w), a


def _kind_sql() -> str:
    # Uncategorised money out counts as an expense, uncategorised money in as income.
    return "COALESCE(c.kind, CASE WHEN t.amount_cents < 0 THEN 'expense' ELSE 'income' END)"


def category_totals(conn: sqlite3.Connection, start: str, end: str, kind: str = "expense", level: int = 1,
                    account: int | None = None, within: int | None = None) -> list[dict]:
    """Totals per category, rolled up to `level` (1 = top level). Expenses are returned as positive numbers.
    `within` limits to one category's subtree and rolls up one level below it."""
    tree = cats.load(conn)
    where, args = _where(start, end, account)
    rows = conn.execute(f"""
        SELECT t.category_id AS cid, SUM(t.amount_cents) AS total
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id
        WHERE {where} AND {_kind_sql()} = ?
        GROUP BY t.category_id""", (*args, kind)).fetchall()
    allowed = set(tree.descendants(within)) if within else None
    if within:
        level = min(cats.MAX_DEPTH, tree.by_id[within].depth + 1)
    acc: dict[int | None, int] = {}
    for r in rows:
        if allowed is not None and r["cid"] not in allowed:
            continue
        key = tree.at_level(r["cid"], level)
        acc[key] = acc.get(key, 0) + r["total"]
    sign = -1 if kind == "expense" else 1
    out = []
    for cid, total in acc.items():
        v = sign * total
        if v <= 0:
            continue  # e.g. a category that only had refunds this period
        c = tree.by_id.get(cid) if cid else None
        out.append({"id": cid, "label": (c.path if c else "Uncategorised"), "short": (c.name if c else "Uncategorised"),
                    "value": v, "has_children": bool(c and c.children and c.depth < cats.MAX_DEPTH)})
    out.sort(key=lambda x: -x["value"])
    return out


def income_vs_expense(conn, start: str, end: str, account: int | None = None) -> list[dict]:
    where, args = _where(start, end, account)
    rows = conn.execute(f"""
        SELECT substr(t.posted_date,1,7) AS m, {_kind_sql()} AS kind, SUM(t.amount_cents) AS total
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id
        WHERE {where} AND {_kind_sql()} != 'transfer' GROUP BY 1, 2""", args).fetchall()
    by = {m: {"month": m, "income": 0, "expense": 0} for m in months_between(start, end)}
    for r in rows:
        d = by.setdefault(r["m"], {"month": r["m"], "income": 0, "expense": 0})
        if r["kind"] == "income":
            d["income"] += r["total"]
        else:
            d["expense"] += -r["total"]
    out = [by[k] for k in sorted(by)]
    for d in out:
        d["net"] = d["income"] - d["expense"]
        d["rate"] = (d["net"] / d["income"]) if d["income"] > 0 else None
    return out


def category_trend(conn, start: str, end: str, category: int | None, account: int | None = None) -> list[dict]:
    """Monthly totals for one category (including everything under it), or all spending if None."""
    tree = cats.load(conn)
    where, args = _where(start, end, account)
    if category:
        ids = tree.descendants(category)
        where += f" AND t.category_id IN ({','.join('?' * len(ids))})"
        args += ids
        sign = -1 if tree.by_id[category].kind == "expense" else 1
    else:
        where += f" AND {_kind_sql()} = 'expense'"
        sign = -1
    rows = dict(conn.execute(f"""SELECT substr(t.posted_date,1,7), SUM(t.amount_cents)
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id WHERE {where} GROUP BY 1""", args).fetchall())
    return [{"month": m, "value": sign * rows.get(m, 0)} for m in months_between(start, end)]


def net_worth(conn, start: str, end: str) -> list[dict]:
    """Month-end balances across all accounts: assets, debts and net worth."""
    accts = conn.execute("SELECT id, type, opening_balance_cents FROM accounts").fetchall()
    debt_types = {"credit", "loan"}
    out = []
    for m in months_between(start, end):
        y, mo = int(m[:4]), int(m[5:])
        month_end = (_month_start(date(y, mo, 1), -1) - timedelta(days=1)).isoformat()
        sums = dict(conn.execute("SELECT account_id, SUM(amount_cents) FROM transactions WHERE posted_date <= ? "
                                 "GROUP BY account_id", (month_end,)).fetchall())
        assets = debts = 0
        for a in accts:
            bal = a["opening_balance_cents"] + sums.get(a["id"], 0)
            if a["type"] in debt_types:
                debts += bal
            else:
                assets += bal
        out.append({"month": m, "assets": assets, "debts": debts, "net": assets + debts})
    return out


def top_payees(conn, start: str, end: str, account: int | None = None, limit: int = 15) -> list[dict]:
    where, args = _where(start, end, account)
    rows = conn.execute(f"""
        SELECT t.payee AS payee, -SUM(t.amount_cents) AS spent, COUNT(*) AS n
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id
        WHERE {where} AND {_kind_sql()} = 'expense'
        GROUP BY t.payee HAVING spent > 0 ORDER BY spent DESC LIMIT ?""", (*args, limit)).fetchall()
    return [{"label": r["payee"], "value": r["spent"], "n": r["n"], "id": None} for r in rows]


def merchants(conn, start: str, end: str, account: int | None = None, category: int | None = None,
              limit: int = 25) -> list[dict]:
    """Spending per merchant (the cleaned/renamed payee). Optional category includes its subcategories."""
    where, args = _where(start, end, account)
    if category:
        ids = cats.load(conn).descendants(category) or [-1]
        where += f" AND t.category_id IN ({','.join('?' * len(ids))})"
        args += ids
    rows = conn.execute(f"""
        SELECT t.payee AS payee, -SUM(t.amount_cents) AS spent, COUNT(DISTINCT t.tx_id) AS n,
               MIN(t.posted_date) AS first, MAX(t.posted_date) AS last
        FROM tx_lines t LEFT JOIN categories c ON c.id = t.category_id
        WHERE {where} AND {_kind_sql()} = 'expense'
        GROUP BY t.payee HAVING spent > 0 ORDER BY spent DESC LIMIT ?""", (*args, limit)).fetchall()
    return [{"label": r["payee"], "value": r["spent"], "n": r["n"], "avg": r["spent"] // max(1, r["n"]),
             "first": r["first"], "last": r["last"], "id": None} for r in rows]


def merchant_detail(conn, start: str, end: str, payee: str, account: int | None = None) -> dict:
    where, args = _where(start, end, account)
    where += " AND t.payee = ?"
    args.append(payee)
    monthly = dict(conn.execute(f"""SELECT substr(t.posted_date,1,7), -SUM(t.amount_cents)
        FROM tx_lines t WHERE {where} GROUP BY 1""", args).fetchall())
    tree = cats.load(conn)
    by_cat = conn.execute(f"""SELECT t.category_id AS cid, -SUM(t.amount_cents) AS v FROM tx_lines t
        WHERE {where} GROUP BY 1 ORDER BY v DESC""", args).fetchall()
    n, total = conn.execute(f"SELECT COUNT(DISTINCT t.tx_id), COALESCE(-SUM(t.amount_cents),0) FROM tx_lines t WHERE {where}",
                            args).fetchone()
    return {"trend": [{"month": m, "value": monthly.get(m, 0)} for m in months_between(start, end)],
            "categories": [{"label": tree.by_id[r["cid"]].path if r["cid"] in tree.by_id else "Uncategorised",
                            "value": r["v"], "id": r["cid"]} for r in by_cat if r["v"] > 0],
            "n": n, "total": total, "avg": total // max(1, n)}

"""Monthly budgets per category. A parent's 'spent' includes all of its subcategories."""
import sqlite3
from datetime import date, timedelta

from . import categories as cats


def month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:])
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return f"{month}-01", (nxt - timedelta(days=1)).isoformat()


def amounts_for(conn: sqlite3.Connection, month: str) -> dict[int, dict]:
    """{category_id: {amount, source: 'month'|'default'}} — a month-specific row overrides the default."""
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT category_id, month, amount_cents FROM budgets WHERE month IN ('default', ?)"
                          " ORDER BY month = 'default' DESC", (month,)):
        out[r["category_id"]] = {"amount": r["amount_cents"], "source": "default" if r["month"] == "default" else "month",
                                 "default": out.get(r["category_id"], {}).get("amount") if r["month"] != "default"
                                 else r["amount_cents"]}
    return out


def spent_by_category(conn, month: str) -> dict[int | None, int]:
    """Raw spending per category for the month (expenses positive, refunds reduce it). Uses split lines."""
    start, end = month_bounds(month)
    return {r[0]: -r[1] for r in conn.execute(
        "SELECT category_id, SUM(amount_cents) FROM tx_lines WHERE posted_date BETWEEN ? AND ? GROUP BY category_id",
        (start, end))}


def overview(conn, month: str, kind: str = "expense") -> dict:
    tree = cats.load(conn)
    budgets = amounts_for(conn, month)
    raw = spent_by_category(conn, month)
    sign = 1 if kind == "expense" else -1   # income shows as positive "received"
    rows = []
    for c in tree.ordered():
        if c.kind != kind:
            continue
        spent = sign * sum(raw.get(i, 0) for i in tree.descendants(c.id))
        b = budgets.get(c.id)
        rows.append({"cat": c, "spent": spent, "budget": b["amount"] if b else None,
                     "source": b["source"] if b else None, "default": b.get("default") if b else None,
                     "has_activity": spent != 0})
    # Totals: count a budget only if no ancestor also has one (avoids double counting parent + children).
    budgeted = {r["cat"].id for r in rows if r["budget"] is not None}
    top_budgets = [r for r in rows if r["budget"] is not None and not (set(r["cat"].ancestors) & budgeted)]
    total_budget = sum(r["budget"] for r in top_budgets)
    total_spent_in_budgeted = sum(r["spent"] for r in top_budgets)
    start, end = month_bounds(month)
    uncat = conn.execute("SELECT COALESCE(-SUM(amount_cents), 0) FROM tx_lines WHERE category_id IS NULL"
                         " AND amount_cents < 0 AND posted_date BETWEEN ? AND ?", (start, end)).fetchone()[0]
    total_spent = sum(r["spent"] for r in rows if r["cat"].depth == 1)
    for r in rows:
        r["pct"] = (r["spent"] / r["budget"]) if r["budget"] else None
        r["over"] = r["budget"] is not None and r["spent"] > r["budget"]
    return {"rows": rows, "total_budget": total_budget, "total_spent_budgeted": total_spent_in_budgeted,
            "total_spent": total_spent, "uncategorised": uncat if kind == "expense" else 0}


def set_amount(conn, category_id: int, month: str, amount_cents: int | None) -> None:
    """month: 'default' or 'yyyy-mm'. None removes the row (a month override then falls back to the default)."""
    if amount_cents is None:
        conn.execute("DELETE FROM budgets WHERE category_id = ? AND month = ?", (category_id, month))
    else:
        conn.execute("INSERT INTO budgets(category_id, month, amount_cents) VALUES (?,?,?) "
                     "ON CONFLICT(category_id, month) DO UPDATE SET amount_cents = excluded.amount_cents",
                     (category_id, month, abs(amount_cents)))

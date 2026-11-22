"""Split one transaction across several categories."""
import sqlite3


class SplitError(ValueError):
    pass


def get(conn: sqlite3.Connection, tx_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM splits WHERE transaction_id = ? ORDER BY id", (tx_id,)).fetchall()


def for_transactions(conn, tx_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
    if not tx_ids:
        return {}
    out: dict[int, list] = {}
    for r in conn.execute(f"SELECT * FROM splits WHERE transaction_id IN ({','.join('?' * len(tx_ids))}) ORDER BY id",
                          tx_ids):
        out.setdefault(r["transaction_id"], []).append(r)
    return out


def save(conn, tx_id: int, parts: list[tuple[int | None, int, str | None]]) -> None:
    """parts: (category_id, amount_cents as a positive 'share' in the transaction's direction, memo).
    A share may be negative for unusual cases (e.g. a refund inside a purchase); the total must match exactly."""
    tx = conn.execute("SELECT amount_cents FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        raise SplitError("Transaction not found.")
    parts = [p for p in parts if p[1] != 0]
    if len(parts) < 2:
        raise SplitError("A split needs at least two lines with an amount.")
    sign = -1 if tx["amount_cents"] < 0 else 1
    total = sum(p[1] for p in parts)
    if total != abs(tx["amount_cents"]):
        diff = abs(tx["amount_cents"]) - total
        raise SplitError(f"The lines add up to {total / 100:,.2f} but the transaction is "
                         f"{abs(tx['amount_cents']) / 100:,.2f} ({'short' if diff > 0 else 'over'} by {abs(diff) / 100:,.2f}).")
    conn.execute("DELETE FROM splits WHERE transaction_id = ?", (tx_id,))
    conn.executemany("INSERT INTO splits(transaction_id, category_id, amount_cents, memo) VALUES (?,?,?,?)",
                     [(tx_id, cid, sign * amt, (memo or "").strip() or None) for cid, amt, memo in parts])
    # The transaction's own category no longer applies; lock it so rules leave it alone.
    conn.execute("UPDATE transactions SET category_id = NULL, category_locked = 1 WHERE id = ?", (tx_id,))


def remove(conn, tx_id: int, category_id: int | None = None) -> None:
    conn.execute("DELETE FROM splits WHERE transaction_id = ?", (tx_id,))
    conn.execute("UPDATE transactions SET category_id = ?, category_locked = ? WHERE id = ?",
                 (category_id, 1 if category_id else 0, tx_id))

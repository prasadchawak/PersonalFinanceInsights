"""Build a demo data folder full of MADE-UP transactions (for screenshots and trying the app).

    python tools/make_demo.py demo-data          # creates ./demo-data
    set PFI_DATA_DIR=demo-data && python -m app  # Windows: run the app on it

Nothing here is real: names, amounts and dates are generated.
"""
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(target: str) -> None:
    out = Path(target).resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty; pick a new folder.")
    os.environ["PFI_DATA_DIR"] = str(out)
    os.environ.pop("PFI_DB", None)
    from fastapi.testclient import TestClient
    from app import db, main as app_main

    random.seed(7)
    today = date.today()
    start = date(today.year - (today.month <= 6), (today.month - 6 - 1) % 12 + 1, 1)

    with TestClient(app_main.app) as c:
        def cat(name):
            with db.connect() as conn:
                return str(conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0])

        c.post("/accounts", data={"name": "Everyday Checking", "institution": "Sample Bank", "type": "checking",
                                  "opening_balance": "4200"})
        c.post("/accounts", data={"name": "Rewards Card", "institution": "Sample Card Co", "type": "credit",
                                  "opening_balance": "-380"})
        c.post("/accounts", data={"name": "Emergency Savings", "institution": "Sample Bank", "type": "savings",
                                  "opening_balance": "9000"})

        checking = [("RENT PAYMENT", -1, 2100, 2100, 1), ("CITY UTILITIES", -1, 70, 140, 1),
                    ("METRO INTERNET", -1, 65, 65, 1), ("TRANSFER TO SAVINGS", -1, 400, 400, 1),
                    ("INTEREST PAID", +1, 2, 6, 1)]
        card = [("FRESHMART GROCERY", -1, 35, 190, 4), ("CORNER BAKERY CAFE", -1, 4, 9, 5),
                ("TACO PALACE", -1, 12, 38, 3), ("QUICKFUEL STATION", -1, 30, 62, 3),
                ("BIGBOX STORE", -1, 20, 240, 2), ("STREAMFLIX", -1, 15.49, 15.49, 1),
                ("RIDEGO TRIP", -1, 9, 34, 2), ("CITY PHARMACY", -1, 8, 45, 1),
                ("ONLINE MARKETPLACE", -1, 12, 260, 3), ("GYMPLUS MEMBERSHIP", -1, 39, 39, 1)]

        def ofx(rows):
            body = "".join(f"<STMTTRN><TRNTYPE>X<DTPOSTED>{d:%Y%m%d}<TRNAMT>{a:.2f}<FITID>{i}<NAME>{n}</STMTTRN>"
                           for i, (d, a, n) in enumerate(rows))
            return f"OFXHEADER:100\n<OFX><BANKTRANLIST>{body}</BANKTRANLIST></OFX>".encode()

        def gen(spec):
            rows, m = [], start
            while m <= today:
                for name, sign, lo, hi, n in spec:
                    for _ in range(random.randint(max(1, n - 1), n + 1) if n > 1 else 1):
                        d = m + timedelta(days=random.randint(0, 27))
                        if d <= today:
                            rows.append((d, sign * round(random.uniform(lo, hi), 2), name))
                m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
            return rows

        card_rows = gen(card)
        chk_rows = gen(checking)
        # two paydays a month, and the card paid in full from checking each month
        m = start
        while m <= today:
            for day in (1, 15):
                if m.replace(day=day) <= today:
                    chk_rows.append((m.replace(day=day), 3150.00, "ACME CORP PAYROLL"))
            nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
            spent = -sum(a for d, a, _ in card_rows if m <= d < nxt and a < 0)
            pay_day = nxt.replace(day=5)
            if pay_day <= today and spent > 0:
                chk_rows.append((pay_day, -round(spent, 2), "CARD AUTOPAY"))
                card_rows.append((pay_day, round(spent, 2), "AUTOPAY PAYMENT - THANK YOU"))
            m = nxt
        c.post("/import", data={"account_id": "1"}, files={"file": ("checking.ofx", ofx(chk_rows))})
        c.post("/import", data={"account_id": "2"}, files={"file": ("card.ofx", ofx(card_rows))})
        sav = [(m, 400.0, "TRANSFER FROM CHECKING") for m in
               sorted({d.replace(day=2) for d, _, _ in gen([("x", 1, 1, 1, 1)])})]
        sav.append((today - timedelta(days=20), -640.0, "WITHDRAWAL - CAR REPAIR"))
        c.post("/import", data={"account_id": "3"}, files={"file": ("savings.ofx", ofx(sav))})

        c.post("/categories", data={"name": "FreshMart", "parent_id": cat("Groceries")})
        c.post("/categories", data={"name": "Paid from savings fund", "kind": "expense"})
        rules = [("FRESHMART", "FreshMart", "FreshMart"), ("BAKERY", "Coffee", "Corner Bakery"),
                 ("TACO", "Dining", "Taco Palace"), ("QUICKFUEL", "Fuel", "QuickFuel"),
                 ("STREAMFLIX", "Streaming", "Streamflix"), ("RIDEGO", "Rideshare", "RideGo"),
                 ("PHARMACY", "Pharmacy", "City Pharmacy"), ("GYMPLUS", "Fitness", "GymPlus"),
                 ("PAYROLL", "Salary", "Acme Corp"), ("RENT", "Rent / Mortgage", "Rent"),
                 ("UTILITIES", "Utilities", "City Utilities"), ("INTERNET", "Phone & Internet", "Metro Internet"),
                 ("INTEREST", "Interest & Dividends", None), ("AUTOPAY", "Credit Card Payment", "Card payment"),
                 ("TRANSFER", "Between My Accounts", None), ("BIGBOX", "Household", "BigBox")]
        for pat, catname, rename in rules:
            c.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": pat, "category_id": cat(catname),
                                   "rename_to": rename or "", "reapply": "uncategorized"})
        # an AND rule: big marketplace orders are electronics, small ones household
        c.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "MARKETPLACE", "amount_op": "gt",
                               "amount_v": "150", "direction": "out", "category_id": cat("Electronics"),
                               "rename_to": "Online Marketplace", "priority": "10", "reapply": "uncategorized"})
        c.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "MARKETPLACE",
                               "category_id": cat("Household"), "rename_to": "Online Marketplace",
                               "reapply": "uncategorized"})
        # spending paid from the savings fund is already counted as the monthly transfer: exclude it
        c.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "CAR REPAIR",
                               "category_id": cat("Paid from savings fund"), "set_excluded": "on",
                               "reapply": "uncategorized"})
        # ...and the whole "Paid from savings fund" category is left out of reports & budgets
        c.post(f"/categories/{cat('Paid from savings fund')}/exclude", data={"excluded": "on"})
        # budgets
        b = {"Food": 900, "FreshMart": 450, "Coffee": 60, "Dining": 150, "Housing": 2350, "Transportation": 300,
             "Shopping": 350, "Health": 120, "Bills & Subscriptions": 110}
        c.post("/budgets", data={"month": today.strftime("%Y-%m"), "scope": "default",
                                 **{f"b_{cat(k)}": str(v) for k, v in b.items()}})
        # one split: a BigBox trip that was part groceries
        with db.connect() as conn:
            tid, amt = conn.execute("SELECT id, amount_cents FROM transactions WHERE payee_raw LIKE 'BIGBOX%'"
                                    " AND amount_cents < -6000 ORDER BY posted_date DESC LIMIT 1").fetchone()
        part = abs(amt) - 2500
        c.post(f"/transactions/{tid}/split", data={"category_id": [cat("Household"), cat("Groceries")],
                                                   "amount": [f"{part / 100:.2f}", "25.00"],
                                                   "memo": ["", "snacks"], "action": "save"})
        # a second organisation
        c.post("/orgs", data={"name": "Rental Property"})
        c.post("/accounts", data={"name": "Rental Checking", "type": "checking", "opening_balance": "2500"})  # now open: Rental
    print(f"Demo data written to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo-data")

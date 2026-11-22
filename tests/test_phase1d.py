"""Account deletion, column sorting, multi-condition (AND) rules. All data made up."""
import re
import sqlite3

from test_core import SAMPLES, cat_id, client, make_account, upload  # noqa: F401
from test_phase1b import load_checking, load_ofx, tx_ids


def count(sql, *args):
    from app import db
    with db.connect() as c:
        return c.execute(sql, args).fetchone()[0]


# ---------- delete account

def test_delete_account_removes_everything_for_it(client):
    keep = make_account(client, "Keep")
    gone = make_account(client, "Gone")
    load_ofx(client, keep)
    load_checking(client, gone)
    tid = tx_ids(client, "COSTCO")[0]
    client.post(f"/transactions/{tid}/split", data={"category_id": [cat_id("Groceries"), cat_id("Household")],
                                                    "amount": ["100", "42.18"], "memo": ["", ""], "action": "save"})
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "x", "category_id": cat_id("Dining"),
                                "account_id": str(gone)})
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "y", "category_id": cat_id("Dining")})
    # wrong confirmation is refused, nothing deleted
    r = client.post(f"/accounts/{gone}/delete", data={"confirm_name": "gone"})
    assert "Type the account name exactly" in r.text and count("SELECT COUNT(*) FROM transactions WHERE account_id=?", gone) == 5
    r = client.post(f"/accounts/{gone}/delete", data={"confirm_name": "Gone"})
    assert "Deleted “Gone” and its 5 transactions" in r.text
    assert count("SELECT COUNT(*) FROM accounts WHERE id=?", gone) == 0
    assert count("SELECT COUNT(*) FROM transactions WHERE account_id=?", gone) == 0
    assert count("SELECT COUNT(*) FROM splits") == 0
    assert count("SELECT COUNT(*) FROM imports WHERE account_id=?", gone) == 0
    assert count("SELECT COUNT(*) FROM rules") == 1                        # the any-account rule stays
    assert count("SELECT COUNT(*) FROM rule_conditions") == 1
    assert count("SELECT COUNT(*) FROM transactions WHERE account_id=?", keep) == 2   # other account untouched
    from app import db
    with db.connect() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    for p in ["/", "/transactions", "/reports?report=networth&period=all", "/budgets", "/import", "/rules"]:
        assert client.get(p).status_code == 200, p


# ---------- sorting

def amounts(html):
    return [m for m in re.findall(r'class="num nowrap (?:neg|pos)">(-?\$[\d,.]+)<', html)]


def test_sort_by_each_column(client):
    acct = make_account(client, "Srt")
    load_checking(client, acct)
    base = "/transactions?account=&sort="
    a = amounts(client.get(base + "amount&dir=asc").text)
    assert a[0] == "-$142.18" and a[-1] == "$3,200.00"
    assert amounts(client.get(base + "amount&dir=desc").text)[0] == "$3,200.00"
    dates = re.findall(r'<td class="nowrap">(\d{4}-\d{2}-\d{2})</td>', client.get(base + "date&dir=asc").text)
    assert dates == sorted(dates) and dates[0] == "2026-10-30"
    payees = re.findall(r'<td>([A-Z][^<\n]+?)(?:<br>|\n)', client.get(base + "payee&dir=asc").text)
    assert payees[0].startswith("Acme")
    client.post("/transactions/bulk", data={"ids": tx_ids(client, "STARBUCKS"), "category_id": cat_id("Coffee"),
                                            "scope": "selected"})
    html = client.get(base + "category&dir=asc").text
    assert html.index("Starbucks") < html.index("Costco")  # categorised (Food›Coffee) before uncategorised
    for col in ("date", "payee", "account", "category", "amount"):
        for d in ("asc", "desc"):
            assert client.get(f"{base}{col}&dir={d}").status_code == 200
    assert client.get("/transactions?sort=bogus&dir=sideways").status_code == 200
    # header links flip direction and keep filters
    html = client.get("/transactions?q=STAR&sort=amount&dir=asc").text
    assert 'q=STAR&sort=amount&dir=desc' in html


# ---------- AND rules

def test_and_conditions(client):
    acct = make_account(client, "Rl")
    load_checking(client, acct)
    load_ofx(client, acct)
    big, small = cat_id("Groceries"), cat_id("Coffee")
    # description contains starbucks AND amount < 6 AND money out
    r = client.post("/rules", data={"t_field": ["text", "text"], "t_op": ["contains", "not_contains"],
                                    "t_value": ["STARBUCKS", "refund"], "amount_op": "lt", "amount_v": "6",
                                    "direction": "out", "category_id": small, "reapply": "uncategorized"})
    assert "Rules updated 2 transactions" in r.text
    # costco only when over $100
    client.post("/rules", data={"t_field": "payee", "t_op": "starts_with", "t_value": "costco", "amount_op": "gt",
                                "amount_v": "100", "category_id": big, "reapply": "uncategorized"})
    assert "1 transaction " in client.get(f"/transactions?category={big}").text
    from app import db, rules
    with db.connect() as c:
        rs = rules.load(c)
        assert rules.first_match(rs, acct, "COSTCO WHSE", None, -5000) is None          # amount fails
        assert rules.first_match(rs, acct, "COSTCO WHSE", None, -15000)["category_id"] == int(big)
        assert rules.first_match(rs, acct, "STARBUCKS 1", None, 500) is None            # money in fails
        assert rules.first_match(rs, acct, "STARBUCKS REFUND", None, -500) is None      # not-contains fails
    page = client.get("/rules").text
    assert "AND" in page and "amount (either direction) &lt; $6.00" in page and "money out" in page
    # between, memo field, and validation
    with db.connect() as c:
        rid = rules.save(c, None, [{"field": "memo", "op": "contains", "value": "fuel"},
                                   {"field": "amount", "op": "between", "value": 4000, "value2": 5000}],
                         int(cat_id("Fuel")), None, None, 100)
        rs = rules.load(c)
        assert rules.first_match(rs, acct, "SHELL", "FUEL PURCHASE", -4210)["id"] == rid
        assert rules.first_match(rs, acct, "SHELL", "FUEL PURCHASE", -6000) is None
    r = client.post("/rules", data={"direction": "out", "category_id": small})
    assert "needs at least one text or amount condition" in r.text
    r = client.post("/rules", data={"t_field": "text", "t_op": "regex", "t_value": "([", "category_id": small})
    assert "regex isn" in r.text
    r = client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "x", "amount_op": "between",
                                    "amount_v": "5", "category_id": small})
    assert "needs two amounts" in r.text


def test_edit_keeps_and_replaces_conditions(client):
    make_account(client, "Ed")
    client.post("/rules", data={"t_field": ["text", "memo"], "t_op": ["contains", "contains"], "t_value": ["A", "B"],
                                "category_id": cat_id("Dining")})
    rid = re.search(r'action="/rules/(\d+)"', client.get("/rules").text).group(1)
    client.post(f"/rules/{rid}", data={"t_field": "payee", "t_op": "equals", "t_value": "C", "amount_op": "gte",
                                       "amount_v": "10", "category_id": cat_id("Coffee")})
    assert count("SELECT COUNT(*) FROM rule_conditions WHERE rule_id=?", rid) == 2
    page = client.get("/rules").text
    assert "description equals “C”" in page and "amount (either direction) ≥ $10.00" in page


def test_old_rules_get_a_condition(tmp_path):
    from app import db
    path = tmp_path / "x.db"
    with db.connect(path) as c:
        db.init(c)
        c.execute("DELETE FROM rule_conditions")
        c.execute("INSERT INTO rules(priority, match_type, pattern, category_id) VALUES (100,'starts_with','AMZN',1)")
        c.commit()
        db.init(c)  # upgrade step runs at every start and is idempotent
        db.init(c)
        rows = c.execute("SELECT field, op, value FROM rule_conditions").fetchall()
        assert [tuple(r) for r in rows] == [("text", "starts_with", "AMZN")]


# ---------- signed amounts (regression: "< -50" used to behave like "< 50") and shadowed rules

def test_negative_amounts_compare_signed():
    from app import rules
    txs = {"BIG": -12000, "SMALL": -2000, "REFUND": 6000, "EXACT": -4210}

    def hits(op, v, v2=None):
        [c] = rules.validate_conditions([{"field": "amount", "op": op, "value": v, "value2": v2}])
        return sorted(n for n, a in txs.items() if rules.cond_matches(c, n, None, a))

    assert hits("lt", -5000) == ["BIG"]                         # spent more than $50
    assert hits("gt", -5000) == ["EXACT", "REFUND", "SMALL"]
    assert hits("lte", -12000) == ["BIG"]
    assert hits("between", -1000, -6000) == ["EXACT", "SMALL"]  # order of bounds doesn't matter
    assert hits("between", -3000, 7000) == ["REFUND", "SMALL"]   # -42.10 is below -30
    assert hits("eq", -4210) == ["EXACT"]
    # positive numbers still mean "size, either direction"
    assert hits("gt", 5000) == ["BIG", "REFUND"]
    assert hits("lt", 5000) == ["EXACT", "SMALL"]
    assert hits("eq", 4210) == ["EXACT"]


def test_negative_amount_rule_end_to_end(client):
    acct = make_account(client, "Neg")
    load_checking(client, acct)   # costco -142.18, starbucks -5.75 x2, payroll +3200, utilities -88.40
    r = client.post("/rules", data={"amount_op": "lt", "amount_v": "-100", "category_id": cat_id("Shopping"),
                                    "reapply": "uncategorized"})
    assert "Rules updated 1 transaction." in r.text          # only Costco
    assert "amount &lt; -$100.00" in client.get("/rules").text
    rid = re.search(r'action="/rules/(\d+)"', client.get("/rules").text).group(1)
    # editing keeps the minus sign in the form
    assert 'name="amount_v" size="9" inputmode="decimal" placeholder="e.g. -100" value="-100.00"' in client.get("/rules").text
    client.post(f"/rules/{rid}", data={"amount_op": "between", "amount_v": "-100", "amount_v2": "-50",
                                       "category_id": cat_id("Utilities"), "reapply": "uncategorized"})
    assert "1 transaction " in client.get(f"/transactions?category={cat_id('Utilities')}").text


def test_shadowed_rule_is_flagged(client):
    acct = make_account(client, "Sh")
    load_checking(client, acct)
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS", "category_id": cat_id("Dining")})
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS", "amount_op": "lt",
                                "amount_v": "-5", "category_id": cat_id("Coffee")})
    from app import db, rules
    with db.connect() as c:
        m = rules.match_counts(c)
    general, specific = sorted(m)
    assert m[general]["wins"] == 2 and m[specific] ["wins"] == 0 and m[specific]["shadowed"] == 2
    assert "+2 taken by earlier rule" in client.get("/rules").text
    # giving the specific rule a lower priority number fixes it
    client.post(f"/rules/{specific}", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS",
                                            "amount_op": "lt", "amount_v": "-5", "category_id": cat_id("Coffee"),
                                            "priority": "10", "reapply": "all"})
    with db.connect() as c:
        m = rules.match_counts(c)
    assert m[specific]["wins"] == 2 and m[general]["wins"] == 0


def test_force_reapply_overrides_hand_set_only_when_asked(client):
    acct = make_account(client, "Lk")
    load_checking(client, acct)
    ids = tx_ids(client, "STARBUCKS")
    client.post("/transactions/bulk", data={"ids": ids, "category_id": cat_id("Dining"), "scope": "selected"})  # locks
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS",
                                "category_id": cat_id("Coffee"), "reapply": "all"})
    assert "2 transactions " in client.get(f"/transactions?category={cat_id('Dining')}").text   # untouched
    r = client.post("/rules/apply", data={"scope": "force"})
    assert "Rules updated 2 transactions" in r.text
    assert "2 transactions " in client.get(f"/transactions?category={cat_id('Coffee')}").text
    assert count("SELECT COUNT(*) FROM transactions WHERE payee_raw LIKE 'STARBUCKS%' AND category_locked = 1") == 0


def test_rerun_rules_button_routes_correctly(client):
    """Regression: /rules/apply was captured by the /rules/{id} edit route (422)."""
    make_account(client, "Rt")
    for scope in ("uncategorized", "all", "force"):
        r = client.post("/rules/apply", data={"scope": scope})
        assert r.status_code == 200 and "Rules updated" in r.text, scope

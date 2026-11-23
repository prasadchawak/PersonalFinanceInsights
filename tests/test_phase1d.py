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
    return [m for m in re.findall(r'class="num nowrap (?:neg|pos)">(-?\$[\d,.]+)\s*<', html)]


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
    assert "AND" in page and "amount less than $6.00" in page and "money out" in page
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
    assert "description equals “C”" in page and "amount at least $10.00" in page


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


# ---------- amounts: positive size + direction (a typed minus sign means "money out")

def test_amounts_are_sizes_with_direction():
    from app import rules
    txs = {"BIG": -12000, "SMALL": -2000, "REFUND": 6000, "EXACT": -4210}

    def hits(conds):
        cs = rules.validate_conditions(conds)
        return sorted(n for n, a in txs.items() if all(rules.cond_matches(c, n, None, a) for c in cs))

    amt = lambda op, v, v2=None: {"field": "amount", "op": op, "value": v, "value2": v2}
    out = {"field": "direction", "value": "out"}
    inn = {"field": "direction", "value": "in"}
    assert hits([amt("gt", 5000)]) == ["BIG", "REFUND"]                 # size, either direction
    assert hits([amt("gt", 5000), out]) == ["BIG"]                      # spent more than $50
    assert hits([amt("gt", 5000), inn]) == ["REFUND"]
    assert hits([amt("lt", 5000), out]) == ["EXACT", "SMALL"]
    assert hits([amt("between", 6000, 1000), out]) == ["EXACT", "SMALL"]   # bound order doesn't matter
    assert hits([amt("eq", 4210)]) == ["EXACT"]
    assert hits([amt("eq", 4210), inn]) == []
    # a minus sign typed anyway = money out, same size
    assert hits([amt("gt", -5000)]) == ["BIG"]
    assert hits([amt("eq", -4210)]) == ["EXACT"]
    # "any amount" left selected but a number typed = equals
    assert hits([{"field": "amount", "op": "", "value": 4210, "value2": None}]) == ["EXACT"]
    import pytest
    with pytest.raises(rules.RuleError):
        rules.validate_conditions([amt("eq", -4210), inn])


def test_rules_saved_by_previous_version_still_work():
    """v0.4 stored negative numbers for 'money out' amounts; they keep meaning money out."""
    from app import rules
    c = {"field": "amount", "op": "lt", "value": "-5000", "value2": None}
    assert rules.cond_matches(c, "", None, -12000) is False   # -120 is not "less than 50 out"
    assert rules.cond_matches(c, "", None, -2000) is True
    assert rules.cond_matches(c, "", None, 2000) is False


def test_description_and_amount_rule_end_to_end(client):
    acct = make_account(client, "E2E")
    load_checking(client, acct)   # costco -142.18, starbucks -5.75 x2, payroll +3200, utilities -88.40
    # exactly what the browser sends: blank extra rows included
    form = {"t_field": ["text", "text"], "t_op": ["contains", "contains"], "t_value": ["starbucks", ""],
            "amount_op": "eq", "amount_v": "5.75", "amount_v2": "", "direction": "out", "account_id": "",
            "category_id": cat_id("Coffee"), "rename_to": "", "priority": "100", "reapply": "uncategorized"}
    assert "Rules updated 2 transactions" in client.post("/rules", data=form).text
    page = client.get("/rules").text
    assert "amount equals $5.75" in page and "money out" in page
    # editing shows the positive number back
    assert 'value="5.75"' in page
    # amount alone + direction
    r = client.post("/rules", data={"amount_op": "gt", "amount_v": "100", "direction": "out",
                                    "category_id": cat_id("Shopping"), "reapply": "uncategorized"})
    assert "Rules updated 1 transaction." in r.text           # Costco only (payroll is money in)
    # matches the name shown in the app after a rename, not only the bank's text
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "ACME", "category_id": cat_id("Salary"),
                                "rename_to": "My Employer", "priority": "1", "reapply": "uncategorized"})
    r = client.post("/rules", data={"t_field": "payee", "t_op": "equals", "t_value": "My Employer",
                                    "amount_op": "eq", "amount_v": "3200", "direction": "in",
                                    "category_id": cat_id("Other Income"), "priority": "0", "reapply": "force"})
    assert "1 transaction " in client.get(f"/transactions?category={cat_id('Other Income')}").text


def test_amount_typed_with_any_amount_selected_is_kept(client):
    """Regression: the browser sends amount_op='' when the dropdown is left alone; the amount was dropped."""
    acct = make_account(client, "Any")
    load_checking(client, acct)
    r = client.post("/rules", data={"t_field": ["text", "text"], "t_op": ["contains", "contains"],
                                    "t_value": ["starbucks", ""], "amount_op": "", "amount_v": "5.75", "amount_v2": "",
                                    "direction": "out", "category_id": cat_id("Coffee"), "reapply": "uncategorized"})
    assert "Rules updated 2 transactions" in r.text
    assert "amount equals $5.75" in client.get("/rules").text
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "costco", "amount_op": "",
                                "amount_v": "1.00", "category_id": cat_id("Groceries"), "reapply": "uncategorized"})
    assert "0 transactions" in client.get(f"/transactions?category={cat_id('Groceries')}").text  # $1 ≠ $142.18


def test_shadowed_rule_is_flagged(client):
    acct = make_account(client, "Sh")
    load_checking(client, acct)
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS", "category_id": cat_id("Dining")})
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS", "amount_op": "gt",
                                "amount_v": "5", "direction": "out", "category_id": cat_id("Coffee")})
    from app import db, rules
    with db.connect() as c:
        m = rules.match_counts(c)
    general, specific = sorted(m)
    assert m[general]["wins"] == 2 and m[specific]["wins"] == 0 and m[specific]["shadowed"] == 2
    assert "+2 taken by earlier rule" in client.get("/rules").text
    client.post(f"/rules/{specific}", data={"t_field": "text", "t_op": "contains", "t_value": "STARBUCKS",
                                            "amount_op": "gt", "amount_v": "5", "direction": "out",
                                            "category_id": cat_id("Coffee"), "priority": "10", "reapply": "all"})
    with db.connect() as c:
        m = rules.match_counts(c)
    assert m[specific]["wins"] == 2 and m[general]["wins"] == 0


# ---------- amount filters on Transactions

def test_transaction_amount_filters(client):
    acct = make_account(client, "Flt")
    load_checking(client, acct)
    n = lambda url: int(re.search(r"(\d+) transactions? ·", client.get(url).text).group(1))
    assert n("/transactions?amt_op=eq&amt=5.75") == 2
    assert n("/transactions?amt_op=eq&amt=-5.75") == 2                 # minus sign ignored: it's a size
    assert n("/transactions?amt_op=gt&amt=100") == 2                   # costco + payroll
    assert n("/transactions?amt_op=gt&amt=100&flow=out") == 1
    assert n("/transactions?flow=in") == 1
    assert n("/transactions?amt_op=between&amt=5&amt2=90") == 3        # starbucks x2 + utilities
    assert n("/transactions?amt_op=lte&amt=88.40&flow=out") == 3
    assert n("/transactions?amt=142.18") == 1                          # no operator = equals
    assert n("/transactions?q=5.75") == 2                              # a number in search finds the amount
    assert n("/transactions?q=$3,200.00") == 1
    assert n("/transactions?q=starbucks&amt_op=lt&amt=6") == 2         # text AND amount
    assert n("/transactions?amt_op=gt&amt=abc") == 5                   # junk is ignored, not an error
    # bulk "all matching this filter" respects the amount filter
    r = client.post("/transactions/bulk", data={"scope": "filter", "f_amt_op": "gt", "f_amt": "100", "f_flow": "out",
                                                "category_id": cat_id("Groceries")})
    assert "Updated 1 transaction." in r.text
    assert client.get("/export.csv?amt_op=eq&amt=5.75").text.count("STARBUCKS") == 2
    html = client.get("/transactions?amt_op=gt&amt=100&sort=amount&dir=asc").text
    assert "amt_op=gt&amt=100&sort=amount&dir=desc" in html            # sort links keep the filter


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

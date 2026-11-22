"""Splits, budgets, merchants, subcategory charts, account rename, rule editing. All data made up."""
import re

from test_core import SAMPLES, cat_id, client, make_account, upload  # noqa: F401
from test_phase1b import load_checking, load_ofx, tx_ids


def costco_id(client):
    return tx_ids(client, "COSTCO")[0]


# ---------- splits

def test_split_validation_and_reports(client):
    acct = make_account(client, "S1")
    load_checking(client, acct)
    tid = costco_id(client)  # -142.18
    assert client.get(f"/transactions/{tid}/split").status_code == 200
    groc, house = cat_id("Groceries"), cat_id("Household")
    # wrong total is refused with the difference
    r = client.post(f"/transactions/{tid}/split", data={"category_id": [groc, house], "amount": ["100", "40"],
                                                         "memo": ["", ""], "action": "save"})
    assert "short by 2.18" in r.text
    r = client.post(f"/transactions/{tid}/split", data={"category_id": [groc, house, ""], "amount": ["100.00", "42.18", ""],
                                                         "memo": ["food", "paper towels", ""], "action": "save"})
    assert r.status_code == 200
    page = client.get("/transactions?q=COSTCO").text
    assert "Split · 2" in page and "Shopping › Household" in page
    # both categories find it; uncategorised doesn't
    assert "1 transaction " in client.get(f"/transactions?category={house}").text
    assert "COSTCO" not in client.get("/transactions?category=none").text
    from app import db, reports
    with db.connect() as c:
        lvl2 = {i["label"]: i["value"] for i in reports.category_totals(c, "2026-11-01", "2026-11-30", "expense", 2)}
    assert lvl2["Food › Groceries"] == 10000 and lvl2["Shopping › Household"] == 4218
    # export has one row per split
    csv_text = client.get("/export.csv?q=COSTCO").text
    assert csv_text.count("COSTCO") == 2 and ",yes," in csv_text
    # rules never touch split transactions
    client.post("/rules", data={"match_type": "contains", "pattern": "costco", "category_id": cat_id("Dining"),
                                "apply_now": "on"})
    client.post("/rules/apply", data={"scope": "all"})
    assert "Split · 2" in client.get("/transactions?q=COSTCO").text
    # picking a single category replaces the split
    client.post(f"/transactions/{tid}", data={"category_id": groc})
    assert "Split ·" not in client.get("/transactions?q=COSTCO").text


def test_unsplit_bulk_and_undo_cascade(client):
    acct = make_account(client, "S2")
    load_checking(client, acct)
    tid = costco_id(client)
    groc, house = cat_id("Groceries"), cat_id("Household")
    client.post(f"/transactions/{tid}/split", data={"category_id": [groc, house], "amount": ["100", "42.18"],
                                                    "memo": ["", ""], "action": "save"})
    client.post(f"/transactions/{tid}/unsplit")
    assert "Split ·" not in client.get("/transactions?q=COSTCO").text
    client.post(f"/transactions/{tid}/split", data={"category_id": [groc, house], "amount": ["100", "42.18"],
                                                    "memo": ["", ""], "action": "save"})
    client.post("/transactions/bulk", data={"ids": [tid], "category_id": cat_id("Dining"), "scope": "selected"})
    assert "Split ·" not in client.get("/transactions?q=COSTCO").text
    client.post(f"/transactions/{tid}/split", data={"category_id": [groc, house], "amount": ["100", "42.18"],
                                                    "memo": ["", ""], "action": "save"})
    imp = re.search(r'/imports/(\d+)/undo', client.get("/import").text).group(1)
    client.post(f"/imports/{imp}/undo")
    from app import db
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM splits").fetchone()[0] == 0


# ---------- budgets

def test_budgets_default_override_and_rollup(client):
    acct = make_account(client, "B1")
    load_checking(client, acct)   # Nov: costco 142.18, starbucks 5.75 x2, utilities in Oct
    food, groc, coffee = cat_id("Food"), cat_id("Groceries"), cat_id("Coffee")
    client.post("/transactions/bulk", data={"ids": [costco_id(client)], "category_id": groc, "scope": "selected"})
    client.post("/transactions/bulk", data={"ids": tx_ids(client, "STARBUCKS"), "category_id": coffee, "scope": "selected"})
    r = client.post("/budgets", data={"month": "2026-11", "scope": "default", f"b_{food}": "200", f"b_{groc}": "120",
                                      f"b_{coffee}": "20"})
    assert "Saved 3 budgets" in r.text
    from app import budgets, db
    with db.connect() as c:
        ov = budgets.overview(c, "2026-11")
        rows = {x["cat"].name: x for x in ov["rows"]}
        assert rows["Food"]["spent"] == 14218 + 1150          # parent includes children
        assert rows["Groceries"]["over"] and not rows["Food"]["over"]
        assert ov["total_budget"] == 20000                    # children not double-counted under Food
        assert budgets.overview(c, "2027-01")["total_budget"] == 20000   # default applies to any month
    # one-month override, then the default still applies elsewhere
    client.post("/budgets", data={"month": "2026-12", "scope": "month", f"b_{food}": "350"})
    with db.connect() as c:
        assert {x["cat"].name: x for x in budgets.overview(c, "2026-12")["rows"]}["Food"]["budget"] == 35000
        assert {x["cat"].name: x for x in budgets.overview(c, "2027-02")["rows"]}["Food"]["budget"] == 20000
    page = client.get("/budgets?month=2026-11").text
    assert "over" in page and "<svg class=\"progress\"" in page
    assert "Budgets this month" in client.get("/?month=2026-11").text
    assert client.get("/budgets?month=2026-11&kind=income&show=all").status_code == 200
    # clearing removes it
    client.post("/budgets", data={"month": "2026-11", "scope": "default", f"b_{coffee}": ""})
    with db.connect() as c:
        assert {x["cat"].name: x for x in budgets.overview(c, "2026-11")["rows"]}["Coffee"]["budget"] is None


# ---------- merchants & subcategory charts

def test_merchants_and_detail(client):
    acct = make_account(client, "M1")
    load_checking(client, acct)
    load_ofx(client, acct)
    for view in ("pie", "bar"):
        r = client.get(f"/reports?report=merchants&period=custom&start=2026-10-01&end=2026-11-30&view={view}")
        assert r.status_code == 200 and "Costco" in r.text and "<svg" in r.text
    from app import db, reports
    with db.connect() as c:
        m = {x["label"]: x for x in reports.merchants(c, "2026-10-01", "2026-11-30")}
        assert m["Starbucks Store 0456"]["n"] == 2 and m["Starbucks Store 0456"]["value"] == 1150
    r = client.get("/reports?report=merchants&period=custom&start=2026-10-01&end=2026-11-30&payee=Starbucks+Store+0456")
    assert "Average per transaction" in r.text and "$5.75" in r.text
    assert client.get("/reports?report=payees").status_code == 200  # old link still works


def test_subcategories_of_one_category(client):
    acct = make_account(client, "W1")
    load_checking(client, acct)
    food, groc, coffee = cat_id("Food"), cat_id("Groceries"), cat_id("Coffee")
    client.post("/transactions/bulk", data={"ids": [costco_id(client)], "category_id": groc, "scope": "selected"})
    sb = tx_ids(client, "STARBUCKS")
    client.post("/transactions/bulk", data={"ids": [sb[0]], "category_id": coffee, "scope": "selected"})
    client.post("/transactions/bulk", data={"ids": [sb[1]], "category_id": food, "scope": "selected"})
    for view in ("pie", "bar"):
        r = client.get(f"/reports?report=spending&period=custom&start=2026-11-01&end=2026-11-30&within={food}&view={view}")
        assert "Food › Groceries" in r.text and "Food › Coffee" in r.text and "Food (not in a subcategory)" in r.text
        assert "Housing" not in r.text.split("<table class=\"datatable\">")[1]
    assert "Subcategories of Food" in client.get("/reports?report=spending").text


# ---------- accounts & rules

def test_rename_account(client):
    a = make_account(client, "Old Name")
    make_account(client, "Other")
    r = client.post(f"/accounts/{a}", data={"name": "Joint Checking", "institution": "Bank X", "type": "savings",
                                           "opening_balance": "12.50"})
    assert "Saved." in r.text and "Joint Checking" in r.text
    r = client.post(f"/accounts/{a}", data={"name": "other", "type": "checking", "opening_balance": "0"})
    assert "already an account" in r.text


def test_edit_rule_and_reapply(client):
    acct = make_account(client, "R1")
    load_checking(client, acct)
    client.post("/rules", data={"match_type": "contains", "pattern": "starbux", "category_id": cat_id("Dining")})
    rid = re.search(r'action="/rules/(\d+)"', client.get("/rules").text).group(1)
    assert re.search(r'<td class="num muted">0\s', client.get("/rules").text)
    r = client.post(f"/rules/{rid}", data={"match_type": "starts_with", "pattern": "STARBUCKS", "category_id": cat_id("Coffee"),
                                           "rename_to": "Starbucks", "priority": "10", "reapply": "uncategorized"})
    assert "Rules updated 2 transactions" in r.text
    assert "2 transactions " in client.get(f"/transactions?category={cat_id('Coffee')}").text
    r = client.post(f"/rules/{rid}", data={"match_type": "regex", "pattern": "([", "category_id": cat_id("Coffee")})
    assert "isn" in r.text and "valid" in r.text

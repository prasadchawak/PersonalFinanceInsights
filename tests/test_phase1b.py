"""Subcategories, bulk edit, filter fix, reports. All data made up."""
import re
import sqlite3

import pytest

from test_core import SAMPLES, cat_id, client, make_account, upload  # noqa: F401  (fixture re-export)


def load_ofx(client, acct):
    client.post("/import", data={"account_id": acct},
                files={"file": ("s.ofx", (SAMPLES / "sample.ofx").read_bytes(), "application/x-ofx")})


def load_checking(client, acct):
    r = upload(client, acct, SAMPLES / "checking_single.csv")
    client.post(r.headers["location"], data={
        "header_row": "0", "date_col": "Posting Date", "date_format": "%m/%d/%Y", "description_col": "Description",
        "amount_mode": "single", "amount_col": "Amount", "action": "import"})


def tx_ids(client, q=""):
    return re.findall(r'name="ids" value="(\d+)"', client.get(f"/transactions?q={q}").text)


# ---------- the reported bug

def test_all_accounts_filter_no_longer_errors(client):
    make_account(client, "A")
    for url in ["/transactions?account=&category=&month=&q=", "/transactions?account=&category=none",
                "/export.csv?account=&category=", "/reports?account=&level=&category=", "/rules?category_id="]:
        assert client.get(url).status_code == 200, url


# ---------- subcategories

def test_default_tree_and_depth_limit(client):
    from app import categories as cats, db
    with db.connect() as c:
        tree = cats.load(c)
        food = tree.by_id[int(cat_id("Food"))]
        assert {ch.name for ch in food.children} >= {"Groceries", "Dining"}
        g = int(cat_id("Groceries"))
        costco = cats.create(c, "Costco", "expense", g)
        assert cats.load(c).by_id[costco].path == "Food › Groceries › Costco"
        with pytest.raises(cats.CategoryError):
            cats.create(c, "Too deep", "expense", costco)
        with pytest.raises(cats.CategoryError):   # Food (height 3) can't go under anything
            cats.move(c, int(cat_id("Food")), int(cat_id("Housing")))
        with pytest.raises(cats.CategoryError):   # not into its own subtree
            cats.move(c, g, costco)
        with pytest.raises(cats.CategoryError):   # duplicate name at same level
            cats.rename(c, g, "Dining")


def test_move_rename_delete_via_ui(client):
    make_account(client, "B")
    g, dining, housing = cat_id("Groceries"), cat_id("Dining"), cat_id("Housing")
    client.post("/categories", data={"name": "Costco", "parent_id": g})
    costco = cat_id("Costco")
    # move Groceries (with Costco under it) to Housing; kind follows the new parent
    r = client.post(f"/categories/{g}/move", data={"parent_id": housing})
    assert "msg=" in str(r.url)
    from app import categories as cats, db
    with db.connect() as c:
        assert cats.load(c).by_id[int(costco)].path == "Housing › Groceries › Costco"
    client.post(f"/categories/{g}/rename", data={"name": "Supermarkets"})
    with db.connect() as c:
        assert cats.load(c).by_id[int(costco)].path == "Housing › Supermarkets › Costco"
    # too deep move is refused with a message, nothing changes
    r = client.post(f"/categories/{dining}/move", data={"parent_id": costco})
    assert "error=" in str(r.url)
    # delete middle level: Costco moves up to Housing
    client.post(f"/categories/{g}/delete")
    with db.connect() as c:
        assert cats.load(c).by_id[int(costco)].path == "Housing › Costco"
    assert client.get("/categories").status_code == 200


def test_parent_filter_includes_children_and_rollup(client):
    acct = make_account(client, "C")
    load_checking(client, acct)
    g = cat_id("Groceries")
    client.post("/categories", data={"name": "Costco", "parent_id": g})
    costco = cat_id("Costco")
    ids = tx_ids(client, "COSTCO")
    client.post(f"/transactions/{ids[0]}", data={"category_id": costco})
    food = cat_id("Food")
    assert "1 transaction " in client.get(f"/transactions?category={food}").text
    from app import db, reports
    with db.connect() as c:
        lvl1 = reports.category_totals(c, "2026-10-01", "2026-11-30", "expense", 1)
        lvl3 = reports.category_totals(c, "2026-10-01", "2026-11-30", "expense", 3)
        within = reports.category_totals(c, "2026-10-01", "2026-11-30", "expense", 1, within=int(food))
    assert {"Food": 14218}.items() <= {i["label"]: i["value"] for i in lvl1}.items()
    assert any(i["label"] == "Food › Groceries › Costco" and i["value"] == 14218 for i in lvl3)
    assert [i["label"] for i in within] == ["Food › Groceries"]


def test_migrates_v1_flat_categories(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
      CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, institution TEXT, type TEXT NOT NULL,
        opening_balance_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT);
      CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL);
      CREATE TABLE transactions (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
        posted_date TEXT NOT NULL, amount_cents INTEGER NOT NULL, payee_raw TEXT NOT NULL, payee TEXT NOT NULL, memo TEXT,
        category_id INTEGER REFERENCES categories(id), category_locked INTEGER NOT NULL DEFAULT 0, notes TEXT,
        fingerprint TEXT NOT NULL, import_id INTEGER, raw_json TEXT, created_at TEXT, UNIQUE(account_id, fingerprint));
      INSERT INTO accounts VALUES (1,'X',NULL,'checking',0,NULL);
      INSERT INTO categories VALUES (7,'Groceries','expense'),(8,'Salary','income');
      INSERT INTO transactions(id,account_id,posted_date,amount_cents,payee_raw,payee,category_id,fingerprint)
        VALUES (1,1,'2026-11-01',-500,'SHOP','Shop',7,'f1');""")
    old.commit(); old.close()
    from app import categories as cats, db
    with db.connect(path) as c:
        db.init(c)
        tree = cats.load(c)
        assert tree.by_id[7].name == "Groceries" and tree.by_id[7].depth == 1
        assert c.execute("SELECT category_id FROM transactions WHERE id=1").fetchone()[0] == 7
        assert len(tree.by_id) == 2  # existing user's categories are kept, defaults not re-seeded
        cats.create(c, "Costco", "expense", 7)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ---------- bulk edit

def test_bulk_selected_and_bulk_filter(client):
    acct = make_account(client, "D")
    load_checking(client, acct)
    coffee = cat_id("Coffee")
    ids = tx_ids(client, "STARBUCKS")
    assert len(ids) == 2
    r = client.post("/transactions/bulk", data={"ids": ids, "category_id": coffee, "scope": "selected",
                                                "return_to": "/transactions?q=STARBUCKS"})
    assert "updated=2" in str(r.url) and "Updated 2 transactions" in r.text
    assert "2 transactions " in client.get(f"/transactions?category={coffee}").text
    other = cat_id("Other Expenses")
    r = client.post("/transactions/bulk", data={"scope": "filter", "f_account": "", "f_category": "none",
                                                "category_id": other, "return_to": "/transactions"})
    assert "Updated 3 transactions" in r.text  # costco, payroll, utilities were the uncategorised ones
    assert "0 transactions" in client.get("/transactions?category=none").text
    r = client.post("/transactions/bulk", data={"ids": ids, "category_id": "none", "scope": "selected"})
    assert "2 transactions " in client.get("/transactions?category=none").text


# ---------- reports

@pytest.mark.parametrize("report", ["spending", "income", "cashflow", "trend", "networth", "payees"])
@pytest.mark.parametrize("view", ["pie", "bar"])
def test_reports_render(client, report, view):
    acct = make_account(client, "E")
    load_checking(client, acct)
    load_ofx(client, acct)
    for period in ["this_month", "6m", "all", "ytd", "last_year"]:
        r = client.get(f"/reports?report={report}&period={period}&view={view}")
        assert r.status_code == 200, (report, period)
    r = client.get(f"/reports?report={report}&period=custom&start=2026-10-01&end=2026-11-30&view={view}&level=3")
    assert r.status_code == 200
    if report in ("spending", "cashflow", "networth", "payees"):
        assert "<svg" in r.text


def test_cashflow_numbers(client):
    acct = make_account(client, "F")
    load_checking(client, acct)
    from app import db, reports
    with db.connect() as c:
        rows = reports.income_vs_expense(c, "2026-10-01", "2026-11-30")
    assert [(r["month"], r["income"], r["expense"]) for r in rows] == [("2026-10", 0, 8840), ("2026-11", 320000, 15368)]

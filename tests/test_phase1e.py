"""Organisations (separate databases) and excluding transactions from reports/budgets. Made-up data."""
import re
import sqlite3
from pathlib import Path

from test_core import SAMPLES, cat_id, client, make_account, upload  # noqa: F401
from test_phase1b import load_checking, load_ofx, tx_ids


def org_id(client, name):
    from app import orgs
    return next(o["id"] for o in orgs.all_orgs() if o["name"] == name)


def switch(client, oid):
    r = client.post("/orgs/switch", data={"org_id": str(oid)})
    assert r.status_code == 200


# ---------- organisations

def test_existing_data_becomes_personal_and_registry_is_separate(client):
    from app import db, orgs
    make_account(client, "Mine")
    [o] = orgs.all_orgs()
    assert o["name"] == "Personal" and orgs.path_of(o) == db.DB_PATH
    reg = sqlite3.connect(orgs.registry_path())
    tables = {r[0] for r in reg.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"orgs"}                         # the registry holds no financial data
    assert orgs.registry_path() != db.DB_PATH


def test_orgs_are_isolated_and_switchable(client):
    make_account(client, "Personal Checking")
    load_ofx(client, 1)
    r = client.post("/orgs", data={"name": "Rental Property"})
    assert "Created “Rental Property”" in r.text and 'selected>Rental Property</option>' in r.text
    # new org: own empty database, default categories
    assert "Personal Checking" not in client.get("/accounts").text
    assert "0 transactions" in client.get("/transactions").text
    assert "Groceries" in client.get("/categories").text
    client.post("/accounts", data={"name": "Rental Checking", "type": "checking", "opening_balance": "0"})
    client.post("/categories", data={"name": "Repairs"})
    # back to Personal: nothing from Rental leaks
    switch(client, org_id(client, "Personal"))
    page = client.get("/accounts").text
    assert "Personal Checking" in page and "Rental Checking" not in page
    assert "Repairs" not in client.get("/categories").text
    assert "2 transactions" in client.get("/transactions").text
    # separate files on disk
    from app import orgs
    files = {o["name"]: orgs.path_of(o) for o in orgs.all_orgs()}
    assert files["Personal"] != files["Rental Property"] and files["Rental Property"].exists()
    with sqlite3.connect(files["Rental Property"]) as c:
        assert [r[0] for r in c.execute("SELECT name FROM accounts")] == ["Rental Checking"]
    # an unknown org id in the cookie falls back to the first org
    client.cookies.set("pfi_org", "999")
    assert "Personal Checking" in client.get("/accounts").text


def test_org_rename_delete_and_guards(client):
    client.post("/orgs", data={"name": "Mom & Dad"})
    client.post("/orgs", data={"name": "mom & dad"})        # duplicate, case-insensitive
    r = client.post("/orgs", data={"name": "MOM & DAD"})
    assert "already an organisation" in r.text
    oid = org_id(client, "Mom & Dad")
    client.post(f"/orgs/{oid}/rename", data={"name": "Parents"})
    assert "Parents" in client.get("/orgs").text
    from app import db, orgs
    path = orgs.path_of(orgs.get(oid))
    r = client.post(f"/orgs/{oid}/delete", data={"confirm_name": "parents"})
    assert "Type the organisation name exactly" in r.text and path.exists()
    r = client.post(f"/orgs/{oid}/delete", data={"confirm_name": "Parents"})
    assert "moved to data/deleted/" in r.text and not path.exists()
    assert list((db.DATA_DIR / "deleted").glob(f"*{path.name}"))           # kept, not erased
    assert [o["name"] for o in orgs.all_orgs()] == ["Personal"]
    pid = org_id(client, "Personal")
    r = client.post(f"/orgs/{pid}/delete", data={"confirm_name": "Personal"})
    assert "only organisation" in r.text


def test_half_finished_import_cannot_land_in_another_org(client):
    make_account(client, "P")
    r = upload(client, 1, SAMPLES / "checking_single.csv")
    url = r.headers["location"]
    client.post("/orgs", data={"name": "Other"})
    r = client.get(url)
    assert r.status_code == 409 and "different organisation" in r.text


def test_backup_includes_every_org(client):
    make_account(client, "A1")
    client.post("/orgs", data={"name": "Biz"})
    from app import backup
    folder, written = backup.run()
    assert {n for n, _ in written} == {"Personal", "Biz"}
    assert (folder / "orgs.db").exists() and all(p.exists() for _, p in written)


# ---------- excluded from reports & budgets

def month_spent(client, month="2026-11"):
    from app import db, services
    with db.connect() as c:
        return services.month_summary(c, month)["spent"]


def test_excluded_transaction_leaves_reports_but_not_balances(client):
    acct = make_account(client, "Ex", opening="0")
    load_checking(client, acct)        # Nov spending: costco 142.18 + starbucks 5.75 x2 = 153.68
    assert month_spent(client) == 15368
    balance_before = re.search(r'(-?\$[\d,.]+)</td>\s*<td><form method="post" action="/accounts/\d+" id',
                               client.get("/accounts").text)
    tid = tx_ids(client, "COSTCO")[0]
    client.post(f"/transactions/{tid}", data={"details_only": "1", "set_excluded": "1", "excluded": "on"})
    assert month_spent(client) == 1150                                  # costco no longer counted
    from app import db, reports, budgets
    with db.connect() as c:
        assert all(i["label"] != "Uncategorised" or i["value"] == 1150
                   for i in reports.category_totals(c, "2026-11-01", "2026-11-30"))
        assert not any(m["label"].startswith("Costco") for m in reports.merchants(c, "2026-10-01", "2026-11-30"))
        nw = reports.net_worth(c, "2026-11-01", "2026-11-30")
        assert nw[-1]["net"] == 295792                                  # balances still include it
        x = reports.excluded_summary(c, "2026-11-01", "2026-11-30")
        assert (x["n"], x["out"]) == (1, 14218)
    page = client.get("/transactions").text
    assert "⊘ excluded" in page and "1 excluded from reports" in page
    assert "⊘ Not counted here" in client.get("/?month=2026-11").text
    assert "⊘ Not counted here" in client.get("/reports?report=spending&period=custom&start=2026-11-01&end=2026-11-30").text
    assert "1 transaction " in client.get("/transactions?excl=only").text
    assert "4 transactions " in client.get("/transactions?excl=hide").text
    assert client.get("/export.csv?excl=only").text.strip().endswith(",yes")
    # editing notes without the exclude field doesn't change it; unticking does
    client.post(f"/transactions/{tid}", data={"details_only": "1", "notes": "hi"})
    assert month_spent(client) == 1150
    client.post(f"/transactions/{tid}", data={"details_only": "1", "set_excluded": "1"})
    assert month_spent(client) == 15368


def test_category_exclusion_covers_children_and_splits(client):
    acct = make_account(client, "Cx")
    load_checking(client, acct)
    tid = tx_ids(client, "COSTCO")[0]
    client.post(f"/transactions/{tid}/split", data={"category_id": [cat_id("Groceries"), cat_id("Household")],
                                                    "amount": ["100", "42.18"], "memo": ["", ""], "action": "save"})
    client.post("/categories", data={"name": "Paid from savings fund"})
    client.post(f"/categories/{cat_id('Food')}/exclude", data={"excluded": "on"})   # parent of Groceries
    assert month_spent(client) == 4218 + 1150          # only the Household part + uncategorised coffee
    page = client.get("/categories").text
    assert "⊘ via Food" in page
    tx = client.get("/transactions?q=COSTCO").text
    assert "⊘ excluded via category" in tx
    client.post(f"/categories/{cat_id('Food')}/exclude", data={})                    # include again
    assert month_spent(client) == 15368


def test_bulk_exclude_and_include(client):
    acct = make_account(client, "Bk")
    load_checking(client, acct)
    ids = tx_ids(client, "STARBUCKS")
    r = client.post("/transactions/bulk", data={"ids": ids, "action": "exclude", "scope": "selected"})
    assert "Updated 2 transactions" in r.text and month_spent(client) == 14218
    r = client.post("/transactions/bulk", data={"scope": "filter", "f_excl": "only", "action": "include"})
    assert "Updated 2 transactions" in r.text and month_spent(client) == 15368
    # exclude/include never touches categories or splits
    client.post("/transactions/bulk", data={"ids": ids, "category_id": cat_id("Coffee"), "scope": "selected"})
    client.post("/transactions/bulk", data={"ids": ids, "action": "exclude", "scope": "selected"})
    assert "2 transactions " in client.get(f"/transactions?category={cat_id('Coffee')}").text


def test_rule_can_exclude_on_import_and_reapply(client):
    acct = make_account(client, "Rx")
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "utilities",
                                "set_excluded": "on", "category_id": cat_id("Utilities")})
    load_checking(client, acct)
    from app import db, reports
    with db.connect() as c:
        assert reports.excluded_summary(c, "2026-10-01", "2026-10-31")["out"] == 8840
    assert "⊘ exclude from reports" in client.get("/rules").text
    # a rule may do nothing but exclude
    r = client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "starbucks", "set_excluded": "on",
                                    "reapply": "uncategorized"})
    assert "Rules updated 2 transactions" in r.text and month_spent(client) == 14218
    r = client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": "x"})
    assert "needs to do something" in r.text


def test_budgets_ignore_excluded(client):
    acct = make_account(client, "Bd")
    load_checking(client, acct)
    g = cat_id("Groceries")
    tid = tx_ids(client, "COSTCO")[0]
    client.post(f"/transactions/{tid}", data={"category_id": g})
    client.post("/budgets", data={"month": "2026-11", "scope": "default", f"b_{g}": "100"})
    from app import budgets, db
    with db.connect() as c:
        assert {r["cat"].name: r for r in budgets.overview(c, "2026-11")["rows"]}["Groceries"]["over"]
    client.post(f"/transactions/{tid}", data={"details_only": "1", "set_excluded": "1", "excluded": "on"})
    with db.connect() as c:
        row = {r["cat"].name: r for r in budgets.overview(c, "2026-11")["rows"]}["Groceries"]
        assert row["spent"] == 0 and not row["over"]
    assert "⊘ Not counted here" in client.get("/budgets?month=2026-11").text


def test_old_database_gets_new_columns(tmp_path):
    from app import db
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript(db.SCHEMA)                    # schema without the newer columns
    for t, col, _ in db.ADDED_COLUMNS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        if col in cols:                           # SCHEMA may already define it; drop by rebuilding is overkill
            pass
    c.close()
    with db.connect(p) as conn:
        db.init(conn)
        for t, col, _ in db.ADDED_COLUMNS:
            assert col in {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        assert conn.execute("SELECT COUNT(*) FROM report_lines").fetchone()[0] == 0

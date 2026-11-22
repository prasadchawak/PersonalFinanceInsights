"""All sample data here is made up."""
import os
import re
import tempfile
from pathlib import Path

import pytest

SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("PFI_DATA_DIR", tmp)
    monkeypatch.setenv("PFI_DB", os.path.join(tmp, "t.db"))
    import importlib
    from app import backup, db, main, orgs
    importlib.reload(db)
    importlib.reload(orgs)
    importlib.reload(backup)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


# ---------- pure parsing

def test_amounts():
    from app.normalize import parse_amount_cents as p
    assert p("$1,234.56") == 123456
    assert p("(12.30)") == -1230
    assert p("12.30 CR") == 1230
    assert p("12.30 DR") == -1230
    assert p("12.30-") == -1230
    assert p("-0.10") == -10
    assert p("") is None
    with pytest.raises(ValueError):
        p("abc")


def test_date_detection():
    from app.normalize import detect_date_format
    assert detect_date_format(["11/03/2026", "10/30/2026"]) == "%m/%d/%Y"
    assert detect_date_format(["31/10/2026"]) == "%d/%m/%Y"
    assert detect_date_format(["02-Oct-2026"]) == "%d-%b-%Y"
    assert detect_date_format(["2026-11-03"]) == "%Y-%m-%d"


def test_payee_cleanup():
    from app.normalize import clean_payee
    assert clean_payee("COSTCO WHSE #0123 SEATTLE WA") == "Costco Whse Seattle Wa"
    assert "PPD" not in clean_payee("ACME CORP PAYROLL PPD ID: 999")


def test_suggest_mapping_and_preamble():
    from app.importers import csv_import
    rows = csv_import.read_rows((SAMPLES / "split_with_preamble.csv").read_text())
    m = csv_import.suggest_mapping(rows)
    assert rows[m["header_row"]][0] == "Transaction Date"
    assert m["amount_mode"] == "split"
    assert (m["debit_col"], m["credit_col"]) == ("Withdrawals", "Deposits")
    txns, errs = csv_import.parse(rows, m)
    assert not errs
    assert [t.amount_cents for t in txns] == [50000, -200, 1234]


def test_ofx():
    from app.importers import ofx
    text = (SAMPLES / "sample.ofx").read_text()
    assert ofx.is_ofx(text)
    t = ofx.parse(text)
    assert [(x.posted_date, x.amount_cents, x.payee_raw) for x in t] == [
        ("2026-11-02", -4210, "SHELL OIL 57444"), ("2026-11-03", 2500, "REFUND AMAZON")]


# ---------- end-to-end through the web app

def cat_id(name):
    from app import db
    with db.connect() as c:
        return str(c.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0])


def make_account(client, name="Checking", type_="checking", opening="1000"):
    r = client.post("/accounts", data={"name": name, "type": type_, "opening_balance": opening, "institution": "Bank"})
    assert r.status_code == 200
    from app import db
    with db.connect() as c:
        return c.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()[0]


def upload(client, acct, path):
    return client.post("/import", data={"account_id": acct},
                       files={"file": (path.name, path.read_bytes(), "text/csv")}, follow_redirects=False)


def test_csv_first_import_maps_then_remembers(client):
    acct = make_account(client)
    path = SAMPLES / "checking_single.csv"
    r = upload(client, acct, path)
    assert r.status_code == 303 and "/import/map/" in r.headers["location"]
    token_url = r.headers["location"]
    page = client.get(token_url).text
    assert "Preview" in page and "COSTCO" in page

    form = {"header_row": "0", "date_col": "Posting Date", "date_format": "%m/%d/%Y",
            "description_col": "Description", "amount_mode": "single", "amount_col": "Amount",
            "save_profile": "on", "profile_name": "Sample Bank", "action": "import"}
    r = client.post(token_url, data=form)
    assert "Import finished" in r.text
    assert re.search(r"New</span><b class=\"pos\">5<", r.text)  # both identical Starbucks rows kept

    # Same layout again: recognised automatically, everything is a duplicate.
    r = upload(client, acct, path)
    assert r.status_code == 200 and "Sample Bank" in r.text
    assert re.search(r"Already had</span><b>5<", r.text)

    # Balance = opening 1000 + net of transactions
    assert "$3,957.92" in client.get("/accounts").text


def test_flip_sign_and_rules(client):
    acct = make_account(client, "Card", "credit", "0")
    r = upload(client, acct, SAMPLES / "card_positive_purchases.csv")
    url = r.headers["location"]
    form = {"header_row": "0", "date_col": "Date", "date_format": "%m/%d/%Y", "description_col": "Description",
            "amount_mode": "single", "amount_col": "Amount", "negate": "on", "action": "import"}
    client.post(url, data=form)
    tx = client.get("/transactions").text
    assert "-$1,064.22" in tx and "$500.00" in tx

    groceries = cat_id("Groceries")
    client.post("/rules", data={"match_type": "contains", "pattern": "wholefds", "category_id": groceries,
                                "rename_to": "Whole Foods", "apply_now": "on"})
    tx = client.get(f"/transactions?category={groceries}").text
    assert "Whole Foods" in tx and "1 transaction " in tx


def test_hand_set_category_is_not_overwritten(client):
    acct = make_account(client, "Bank2")
    client.post("/import", data={"account_id": acct},
                files={"file": ("s.ofx", (SAMPLES / "sample.ofx").read_bytes(), "application/x-ofx")})
    fuel, travel = cat_id("Fuel"), cat_id("Travel")
    tx_id = re.search(r'action="/transactions/(\d+)"', client.get("/transactions?q=SHELL").text).group(1)
    client.post(f"/transactions/{tx_id}", data={"category_id": travel})
    client.post("/rules", data={"match_type": "contains", "pattern": "shell", "category_id": fuel})
    client.post("/rules/apply", data={"scope": "all"})
    assert "1 transaction " in client.get(f"/transactions?category={travel}").text


def test_undo_import(client):
    acct = make_account(client, "Bank3")
    client.post("/import", data={"account_id": acct},
                files={"file": ("s.ofx", (SAMPLES / "sample.ofx").read_bytes(), "application/x-ofx")})
    imp = re.search(r'/imports/(\d+)/undo', client.get("/import").text).group(1)
    client.post(f"/imports/{imp}/undo")
    assert "0 transactions" in client.get("/transactions").text


def test_blocks_foreign_host_and_cross_site_post(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403
    r = client.post("/categories", data={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/categories", data={"name": "x"}, headers={"origin": "null"})
    assert r.status_code == 403
    r = client.post("/categories", data={"name": "x"}, headers={"sec-fetch-site": "cross-site", "origin": "null"})
    assert r.status_code == 403
    r = client.post("/categories", data={"name": "x"}, headers={"sec-fetch-site": "same-site"})
    assert r.status_code == 403


def test_firefox_style_same_origin_post_allowed(client):
    # Firefox sends Origin: null on form posts under strict referrer policies; Sec-Fetch-Site says same-origin.
    r = client.post("/accounts", data={"name": "FF", "type": "checking", "opening_balance": "0"},
                    headers={"origin": "null", "sec-fetch-site": "same-origin"})
    assert r.status_code == 200 and "FF" in r.text
    r = client.post("/categories", data={"name": "y"}, headers={"origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_pages_render(client):
    make_account(client, "Bank4")
    for p in ["/", "/accounts", "/import", "/transactions", "/rules", "/categories", "/export.csv"]:
        assert client.get(p).status_code == 200, p


def test_month_summary_splits_uncategorized_by_sign(client):
    acct = make_account(client, "Bank5")
    client.post("/import", data={"account_id": acct},
                files={"file": ("s.ofx", (SAMPLES / "sample.ofx").read_bytes(), "application/x-ofx")})
    from app import db, services
    with db.connect() as c:
        s = services.month_summary(c, "2026-11")
    assert s["spent"] == 4210 and s["earned"] == 2500


def test_legacy_tally_db_is_adopted(tmp_path, monkeypatch):
    monkeypatch.delenv("PFI_DB", raising=False)
    monkeypatch.delenv("TALLY_DB", raising=False)
    monkeypatch.setenv("PFI_DATA_DIR", str(tmp_path))
    import importlib
    import sqlite3
    from app import db
    importlib.reload(db)
    old = sqlite3.connect(tmp_path / "tally.db")
    old.execute("CREATE TABLE marker (x)"); old.commit(); old.close()
    db.adopt_legacy_db()
    assert not (tmp_path / "tally.db").exists() and (tmp_path / "finance.db").exists()
    with db.connect() as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE name='marker'").fetchone()
    db.adopt_legacy_db()  # second run is a no-op

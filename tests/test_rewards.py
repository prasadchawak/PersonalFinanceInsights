"""Credit card rewards. All cards are public catalog products; all data is made up."""
import json
import re
from datetime import date, timedelta

import pytest

from test_core import cat_id, client, make_account  # noqa: F401


def ofx(rows):
    body = "".join(f"<STMTTRN><TRNTYPE>X<DTPOSTED>{d.replace('-', '')}<TRNAMT>{a:.2f}<FITID>{i}-{d}-{a}<NAME>{n}</STMTTRN>"
                   for i, (d, a, n) in enumerate(rows))
    return f"OFXHEADER:100\n<OFX><BANKTRANLIST>{body}</BANKTRANLIST></OFX>".encode()


def load(client, acct, rows):
    client.post("/import", data={"account_id": str(acct)}, files={"file": ("x.ofx", ofx(rows))})


def card(client, name, key):
    a = make_account(client, name, "credit", "0")
    r = client.post(f"/accounts/{a}/rewards/apply", data={"catalog_key": key})
    assert r.status_code == 200 and "Set up as" in r.text, r.text[:300]
    return a


def categorise(client, pattern, cat):
    client.post("/rules", data={"t_field": "text", "t_op": "contains", "t_value": pattern, "category_id": cat_id(cat),
                                "reapply": "uncategorized"})


def summary(start="2026-01-01", end="2026-12-31"):
    from app import db, rewards
    with db.connect() as c:
        return rewards.summarize(c, start, end)


def by_card(s):
    return {c["program"].account_name: c for c in s["by_card"]}


# ---------- catalog

def test_catalog_is_complete_and_consistent():
    from app import db, rewards
    cat = rewards.catalog()
    assert len(cat["cards"]) >= 30
    names_in_default_tree = {n for top, kind, kids in db.DEFAULT_CATEGORIES for n in [top, *kids]}
    for c in cat["cards"]:
        assert c["unit"] in rewards.UNITS and c["network"] in rewards.NETWORKS, c["key"]
        assert c["base"] > 0 and c["cpp"] > 0 and c["annual_fee"] >= 0 and c["checked"]
        for r in c["rules"]:
            assert r["rate"] > 0 and (r.get("categories") or r.get("merchants")), (c["key"], r)
            assert (r.get("cap") is None) == (r.get("period") is None), (c["key"], r)
            for n in r.get("categories", []):
                assert n in names_in_default_tree, (c["key"], n)
    # the catalog is generic: no personal data, just public product terms
    text = json.dumps(cat["cards"]).lower()
    for word in ("account number", "last4", "ssn"):
        assert word not in text


def test_rewards_only_on_credit_accounts(client):
    a = make_account(client, "Checking", "checking")
    r = client.post(f"/accounts/{a}/rewards/apply", data={"catalog_key": "citi-double-cash"})
    assert "only be set up on credit accounts" in r.text
    assert "Change this account's type to <b>credit</b>" in client.get(f"/accounts/{a}/rewards").text


# ---------- earning

def test_flat_card_and_payments_and_refunds(client):
    a = card(client, "Flat", "citi-double-cash")
    load(client, a, [("2026-03-01", -100.00, "SHOP ONE"), ("2026-03-02", -50.00, "SHOP TWO"),
                     ("2026-03-05", 150.00, "AUTOPAY PAYMENT"), ("2026-03-06", 20.00, "SHOP TWO REFUND")])
    categorise(client, "SHOP", "Household")
    categorise(client, "AUTOPAY", "Credit Card Payment")
    s = by_card(summary())["Flat"]
    # 2% of 150 = $3.00; refund of $20 takes back $0.40; the card payment earns nothing
    assert round(s["earned"]) == 260 and s["spend"] == 15000


def test_supermarket_cap_exclusions_and_streaming(client):
    a = card(client, "BCP", "amex-bcp")
    rows = [(f"2026-{m:02d}-10", -1000.00, "FRESHMART GROCERY") for m in range(1, 8)]   # $7,000 groceries
    rows += [("2026-07-15", -200.00, "COSTCO WHSE"), ("2026-07-16", -15.49, "STREAMFLIX")]
    load(client, a, rows)
    categorise(client, "FRESHMART", "Groceries")
    categorise(client, "COSTCO", "Groceries")
    categorise(client, "STREAMFLIX", "Streaming")
    s = by_card(summary())["BCP"]
    # $6,000 at 6% + $1,000 at 1% + Costco (excluded superstore) $200 at 1% + streaming 6%
    expected = 600000 * 0.06 + 100000 * 0.01 + 20000 * 0.01 + 1549 * 0.06
    assert abs(s["earned"] - expected) < 1


def test_purchase_straddling_a_cap_is_split(client):
    a = card(client, "Disc", "discover-it")
    client.post("/rules/apply", data={"scope": "uncategorized"})
    from app import db
    with db.connect() as c:
        c.execute("UPDATE earn_rules SET activated = 1")
    load(client, a, [("2026-10-05", -1400.00, "TACO PALACE"), ("2026-10-06", -300.00, "TACO PALACE")])
    categorise(client, "TACO", "Dining")
    s = by_card(summary())["Disc"]
    # $1,500 at 5% + $200 at 1%
    assert abs(s["earned"] - (150000 * 0.05 + 20000 * 0.01)) < 1


def test_activation_and_dates(client):
    a = card(client, "Rot", "discover-it")
    load(client, a, [("2026-09-30", -100.00, "TACO PALACE"), ("2026-10-02", -100.00, "TACO PALACE")])
    categorise(client, "TACO", "Dining")
    assert round(by_card(summary())["Rot"]["earned"]) == 200            # not activated: 1% + 1%
    from app import db
    with db.connect() as c:
        rid = c.execute("SELECT id FROM earn_rules").fetchone()[0]
    client.post(f"/rewards/rules/{rid}/activate")
    assert round(by_card(summary())["Rot"]["earned"]) == 600            # Sep 1%, Oct 5%


def test_shared_cap_group(client):
    a = card(client, "Cust", "bofa-customized-cash")
    load(client, a, [("2026-01-05", -2000.00, "TACO PALACE"), ("2026-01-06", -1000.00, "FRESHMART GROCERY")])
    categorise(client, "TACO", "Dining")
    categorise(client, "FRESHMART", "Groceries")
    s = by_card(summary())["Cust"]
    # dining $2,000 at 3%, then only $500 of the shared $2,500 left for groceries at 2%, $500 at 1%
    assert abs(s["earned"] - (200000 * 0.03 + 50000 * 0.02 + 50000 * 0.01)) < 1


def test_points_value_and_merchant_rules(client):
    a = card(client, "Hotel", "marriott-boundless")
    load(client, a, [("2026-05-01", -400.00, "MARRIOTT DOWNTOWN"), ("2026-05-02", -100.00, "SHOP")])
    s = by_card(summary())["Hotel"]
    # 6x and 2x points at 0.75 cents each
    assert abs(s["earned"] - (400 * 6 * 0.75 + 100 * 2 * 0.75)) < 1


def test_splits_and_excluded_transactions_still_earn(client):
    a = card(client, "Split", "amex-bcp")
    load(client, a, [("2026-02-01", -100.00, "BIGBOX STORE")])
    from app import db
    with db.connect() as c:
        tid = c.execute("SELECT id FROM transactions").fetchone()[0]
    client.post(f"/transactions/{tid}/split", data={"category_id": [cat_id("Groceries"), cat_id("Household")],
                                                    "amount": ["40", "60"], "memo": ["", ""], "action": "save"})
    client.post(f"/transactions/{tid}", data={"details_only": "1", "set_excluded": "1", "excluded": "on"})
    # groceries part at 6% (BIGBOX isn't an excluded superstore name), household part at 1%
    assert abs(by_card(summary())["Split"]["earned"] - (4000 * 0.06 + 6000 * 0.01)) < 1


# ---------- best card, missed, acceptance, recommendations

def test_missed_rewards_and_network_acceptance(client):
    low = card(client, "Low", "generic-1")
    card(client, "High", "citi-double-cash")
    disc = card(client, "Disc", "discover-it")
    load(client, low, [("2026-04-01", -100.00, "SHOP")])
    load(client, disc, [("2026-04-02", -100.00, "COSTCO WHSE")])
    s = summary()
    # $100 on 1% instead of 2% = $1 missed; Costco: Discover isn't accepted there, so only Visa/MC options
    assert round(s["missed"]) >= 100
    assert s["missed_by_merchant"][0]["best_card"] == "High"
    from app import db, rewards
    with db.connect() as c:
        progs = rewards.load_programs(c)
        line = {"payee_raw": "COSTCO WHSE #12", "payee": "Costco", "memo": None}
        ok = {p.account_name for p in progs.values() if rewards.accepted(p, line)}
    assert "Disc" not in ok and "High" not in ok and "Low" in ok   # Costco US: Visa only (Citi Double Cash is Mastercard)


def test_recommendations_respect_caps(client):
    disc = card(client, "Disc", "discover-it")
    card(client, "Flat2", "wf-active-cash")
    from app import db
    with db.connect() as c:
        c.execute("UPDATE earn_rules SET activated = 1, start_date = ?, end_date = ?",
                  ((date.today() - timedelta(days=30)).isoformat(), (date.today() + timedelta(days=30)).isoformat()))
    d = (date.today() - timedelta(days=3)).isoformat()
    load(client, disc, [(d, -40.00, "TACO PALACE")])
    categorise(client, "TACO", "Dining")
    from app import rewards
    with db.connect() as c:
        recs = rewards.recommendations(c)
    assert recs[0]["best"]["program"].account_name == "Disc" and round(recs[0]["best"]["pct"]) == 5
    assert recs[0]["best"]["cap"]["left"] == 150000 - 4000
    # use up the cap: now the 2% card wins
    load(client, disc, [(d, -1500.00, "TACO PALACE")])
    client.post("/rules/apply", data={"scope": "uncategorized"})
    with db.connect() as c:
        recs = rewards.recommendations(c)
    assert recs[0]["best"]["program"].account_name == "Flat2"


def test_alerts(client):
    a = card(client, "Rot", "chase-freedom-flex")
    from app import db, rewards
    with db.connect() as c:
        c.execute("UPDATE earn_rules SET start_date = '2000-01-01', end_date = '2000-03-31' WHERE cap_group = 'rotating'")
        texts = " ".join(x["text"] for x in rewards.alerts(c))
    assert "no rotating 5% categories for this quarter" in texts
    load(client, a, [("2020-01-01", -10.00, "SHOP")])
    with db.connect() as c:
        texts = " ".join(x["text"] for x in rewards.alerts(c))
        assert "not used since 2020-01-01" in texts
        c.execute("UPDATE earn_rules SET start_date = NULL, end_date = NULL, activated = 0 WHERE cap_group = 'rotating'")
        assert any("activate" in x["text"] for x in rewards.alerts(c))


def test_unmapped_categories_are_reported(client):
    client.post(f"/categories/{cat_id('Dining')}/rename", data={"name": "Eating out"})
    a = make_account(client, "C", "credit", "0")
    r = client.post(f"/accounts/{a}/rewards/apply", data={"catalog_key": "chase-freedom-unlimited"})
    assert "Map these to your categories" in r.text and "Dining" in r.text
    assert "needs a category: Dining" in r.text


# ---------- pages

def test_setup_page_edit_add_delete_rules(client):
    a = card(client, "Ed", "citi-double-cash")
    page = client.get(f"/accounts/{a}/rewards").text
    assert "Citi Double Cash" in page and "Bonus rules" in page
    r = client.post(f"/accounts/{a}/rewards", data={"card_name": "My card", "network": "Visa", "unit": "points",
                                                    "cpp": "1.25", "base_rate": "2", "annual_fee": "95"})
    assert "Card settings saved" in r.text
    r = client.post(f"/accounts/{a}/rewards/rules", data={"label": "Groceries", "rate": "4", "category_ids": [cat_id("Groceries")],
                                                          "merchants": "", "exclude": "walmart, target", "cap": "1000",
                                                          "cap_period": "month"})
    assert "Bonus rule added" in r.text and "WALMART" not in r.text or "walmart" in r.text
    from app import db
    with db.connect() as c:
        rid = c.execute("SELECT id FROM earn_rules WHERE label = 'Groceries'").fetchone()[0]
    r = client.post(f"/rewards/rules/{rid}", data={"label": "Groceries+", "rate": "5", "category_ids": [cat_id("Groceries")],
                                                   "cap": "500", "cap_period": ""})
    assert "Pick whether the cap is per month" in r.text
    r = client.post(f"/rewards/rules/{rid}", data={"label": "Groceries+", "rate": "5", "merchants": "freshmart"})
    assert "Rule saved" in r.text
    r = client.post(f"/accounts/{a}/rewards/rules", data={"label": "x", "rate": "3"})
    assert "needs at least one category or merchant" in r.text
    client.post(f"/rewards/rules/{rid}/delete")
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM earn_rules WHERE id = ?", (rid,)).fetchone()[0] == 0
    client.post(f"/accounts/{a}/rewards/remove")
    assert "set up rewards" in client.get("/accounts").text


def test_rewards_page_transactions_and_overview(client):
    assert "Set up your credit cards" in client.get("/rewards").text
    a = card(client, "Main", "chase-freedom-unlimited")
    card(client, "Other", "amex-bcp")
    t = date.today()
    load(client, a, [((t - timedelta(days=2)).isoformat(), -80.00, "FRESHMART GROCERY"),
                     ((t - timedelta(days=1)).isoformat(), -30.00, "TACO PALACE")])
    categorise(client, "FRESHMART", "Groceries")
    categorise(client, "TACO", "Dining")
    for period in ("this_month", "ytd", "12m", "all"):
        page = client.get(f"/rewards?period={period}").text
        assert "Which card to use, right now" in page and "Missed, by category" in page, period
    page = client.get("/rewards?period=12m").text
    assert "Other" in page and "Groceries" in page
    tx = client.get("/transactions").text
    assert 'class="rw' in tx and "⚑" in tx                    # groceries at 1.5% when BCP pays 6%
    assert "Which card to use" in client.get("/").text
    assert "💳 Chase Freedom Unlimited" in client.get("/accounts").text


def test_deleting_an_account_removes_its_card_setup(client):
    a = card(client, "Gone", "amex-gold")
    client.post(f"/accounts/{a}/delete", data={"confirm_name": "Gone"})
    from app import db
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM card_programs").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM earn_rules").fetchone()[0] == 0

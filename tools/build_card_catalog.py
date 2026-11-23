"""Generates app/data/card_catalog.json — public card terms, NOT anyone's wallet.

Terms change. Every card carries `checked` (when terms were last looked up) and `verify`
(true where a detail could not be confirmed). Edit this file and re-run to update:
    python tools/build_card_catalog.py
Rates are "units per $1": percent for cash back (3 = 3%), points/miles per dollar otherwise.
`cpp` is the default value of one unit in cents (users can change it per card).
Categories refer to the app's default category names; anything a user's tree lacks is shown for mapping.
"""
import json
from pathlib import Path

CHECKED = "2026-11"
DINING = ["Dining", "Coffee"]
GROCERY = ["Groceries"]
GAS = ["Fuel"]
STREAMING = ["Streaming"]
TRAVEL = ["Travel"]            # parent: Flights, Lodging
TRANSIT = ["Rideshare", "Parking & Tolls"]
DRUG = ["Pharmacy"]
SUPERSTORES = ["WALMART", "TARGET", "COSTCO", "SAMS CLUB", "SAM'S CLUB", "BJ'S", "BJS WHOLESALE"]
AMAZON = ["AMAZON", "AMZN", "WHOLE FOODS", "WHOLEFDS"]
COSTCO = ["COSTCO"]


def rule(label, rate, categories=None, merchants=None, exclude=None, cap=None, period=None, group=None,
         start=None, end=None, activation=False, note=None):
    r = {"label": label, "rate": rate}
    if categories: r["categories"] = categories
    if merchants: r["merchants"] = merchants
    if exclude: r["exclude"] = exclude
    if cap: r["cap"] = cap; r["period"] = period; r["group"] = group
    if start: r["start"] = start
    if end: r["end"] = end
    if activation: r["activation"] = True
    if note: r["note"] = note
    return r


def card(key, name, issuer, network, unit, cpp, fee, base, rules=(), verify=False, notes=None, rotating=False,
         membership=None):
    c = {"key": key, "name": name, "issuer": issuer, "network": network, "unit": unit, "cpp": cpp,
         "annual_fee": fee, "base": base, "rules": list(rules), "checked": CHECKED}
    if verify: c["verify"] = True
    if notes: c["notes"] = notes
    if rotating: c["rotating"] = True
    if membership: c["membership"] = membership
    return c


CARDS = [
    # ---- flat-rate cash back
    card("citi-double-cash", "Citi Double Cash", "Citi", "Mastercard", "cash", 1.0, 0, 2.0,
         [rule("Citi Travel hotels, cars, attractions", 5, merchants=["CITI TRAVEL"])]),
    card("wf-active-cash", "Wells Fargo Active Cash", "Wells Fargo", "Visa", "cash", 1.0, 0, 2.0),
    card("c1-quicksilver", "Capital One Quicksilver", "Capital One", "Mastercard", "cash", 1.0, 0, 1.5,
         [rule("Capital One Travel hotels, rentals", 5, merchants=["CAPITAL ONE TRAVEL", "CAP ONE TRAVEL"])]),
    card("bofa-unlimited-cash", "Bank of America Unlimited Cash Rewards", "Bank of America", "Visa", "cash", 1.0, 0, 1.5,
         notes="Preferred Rewards members earn 25–75% more: raise the point value (e.g. 1.75 for +75%)."),
    card("robinhood-gold", "Robinhood Gold Card", "Robinhood", "Visa", "cash", 1.0, 0, 3.0,
         [rule("Robinhood travel portal", 5, merchants=["ROBINHOOD TRAVEL"])],
         membership="Requires Robinhood Gold ($50/yr) — add it as the annual fee if you'd not pay it otherwise."),
    card("apple-card", "Apple Card", "Goldman Sachs", "Mastercard", "cash", 1.0, 0, 2.0,
         [rule("Apple and select partners (Apple Pay)", 3, merchants=["APPLE.COM", "APPLE STORE", "UBER", "NIKE", "WALGREENS"])],
         notes="Base set to 2% assuming Apple Pay; use 1 if you mostly use the physical card."),
    # ---- category cash back
    card("c1-savor", "Capital One Savor", "Capital One", "Mastercard", "cash", 1.0, 0, 1.0,
         [rule("Grocery stores (not superstores)", 3, categories=GROCERY, exclude=["WALMART", "TARGET"]),
          rule("Dining", 3, categories=DINING), rule("Entertainment", 3, categories=["Entertainment"]),
          rule("Popular streaming", 3, categories=STREAMING),
          rule("Capital One Travel", 5, merchants=["CAPITAL ONE TRAVEL"])]),
    card("chase-freedom-unlimited", "Chase Freedom Unlimited", "Chase", "Visa", "cash", 1.0, 0, 1.5,
         [rule("Dining", 3, categories=DINING), rule("Drugstores", 3, categories=DRUG),
          rule("Chase Travel", 5, merchants=["CHASE TRAVEL"])]),
    card("chase-freedom-flex", "Chase Freedom Flex", "Chase", "Mastercard", "cash", 1.0, 0, 1.0,
         [rule("Dining", 3, categories=DINING), rule("Drugstores", 3, categories=DRUG),
          rule("Chase Travel", 5, merchants=["CHASE TRAVEL"]),
          rule("Q1 2027: grocery stores (not Walmart/Target), top streaming", 5, categories=GROCERY + STREAMING,
               exclude=["WALMART", "TARGET"], cap=150000, period="quarter", group="rotating",
               start="2027-01-01", end="2027-03-31", activation=True)],
         rotating=True, notes="5% quarterly categories on up to $1,500 combined; enter each new quarter."),
    card("discover-it", "Discover it Cash Back", "Discover", "Discover", "cash", 1.0, 0, 1.0,
         [rule("Q4 2026: restaurants, entertainment, utilities", 5, categories=DINING + ["Entertainment", "Utilities"],
               cap=150000, period="quarter", group="rotating", start="2026-10-01", end="2026-12-31", activation=True)],
         rotating=True, notes="5% quarterly categories on up to $1,500 combined (activation required); enter each new quarter."),
    card("amex-bcp", "Amex Blue Cash Preferred", "American Express", "Amex", "cash", 1.0, 9500, 1.0,
         [rule("U.S. supermarkets", 6, categories=GROCERY, exclude=SUPERSTORES, cap=600000, period="year", group="supermarkets"),
          rule("Select U.S. streaming", 6, categories=STREAMING),
          rule("U.S. gas stations", 3, categories=GAS), rule("Transit", 3, categories=TRANSIT)]),
    card("amex-bce", "Amex Blue Cash Everyday", "American Express", "Amex", "cash", 1.0, 0, 1.0,
         [rule("U.S. supermarkets", 3, categories=GROCERY, exclude=SUPERSTORES, cap=600000, period="year", group="supermarkets"),
          rule("U.S. online retail", 3, merchants=["AMAZON", "AMZN", "ONLINE"], cap=600000, period="year", group="online"),
          rule("U.S. gas stations", 3, categories=GAS, cap=600000, period="year", group="gas")], verify=True),
    card("bofa-customized-cash", "Bank of America Customized Cash Rewards", "Bank of America", "Visa", "cash", 1.0, 0, 1.0,
         [rule("Choice category (set to dining — change to yours)", 3, categories=DINING, cap=250000, period="quarter", group="choice+grocery"),
          rule("Grocery stores and wholesale clubs", 2, categories=GROCERY, merchants=None, cap=250000, period="quarter", group="choice+grocery")],
         notes="Pick your 3% choice category on the rule. Preferred Rewards adds 25–75%: raise the point value (e.g. 1.75)."),
    card("citi-custom-cash", "Citi Custom Cash", "Citi", "Mastercard", "cash", 1.0, 0, 1.0,
         [rule("Top eligible category (set yours)", 5, categories=DINING, cap=50000, period="month", group="top")],
         notes="5% applies to your highest eligible category each billing cycle; set the rule to the one you use. Closed to new applicants since May 2026."),
    card("us-bank-altitude-go", "U.S. Bank Altitude Go", "U.S. Bank", "Visa", "points", 1.0, 0, 1.0,
         [rule("Dining", 4, categories=DINING), rule("Groceries", 2, categories=GROCERY),
          rule("Gas and EV charging", 2, categories=GAS), rule("Streaming", 2, categories=STREAMING)], verify=True),
    card("wf-autograph", "Wells Fargo Autograph", "Wells Fargo", "Visa", "points", 1.0, 0, 1.0,
         [rule("Restaurants", 3, categories=DINING), rule("Travel", 3, categories=TRAVEL), rule("Gas stations", 3, categories=GAS),
          rule("Transit", 3, categories=TRANSIT), rule("Streaming", 3, categories=STREAMING),
          rule("Phone plans", 3, categories=["Phone & Internet"])]),
    # ---- store / co-brand cash back
    card("prime-visa", "Prime Visa (Amazon)", "Chase", "Visa", "cash", 1.0, 0, 1.0,
         [rule("Amazon, Whole Foods, Chase Travel", 5, merchants=AMAZON + ["CHASE TRAVEL"]),
          rule("Gas stations", 2, categories=GAS), rule("Restaurants", 2, categories=DINING),
          rule("Local transit", 2, categories=TRANSIT)], membership="Requires Amazon Prime."),
    card("amazon-visa", "Amazon Visa (no Prime)", "Chase", "Visa", "cash", 1.0, 0, 1.0,
         [rule("Amazon, Whole Foods, Chase Travel", 3, merchants=AMAZON + ["CHASE TRAVEL"]),
          rule("Gas stations", 2, categories=GAS), rule("Restaurants", 2, categories=DINING),
          rule("Local transit", 2, categories=TRANSIT)]),
    card("costco-anywhere", "Costco Anywhere Visa (Citi)", "Citi", "Visa", "cash", 1.0, 0, 1.0,
         [rule("Costco gas", 5, categories=GAS, merchants=COSTCO, cap=700000, period="year", group="gas"),
          rule("Other gas and EV charging", 4, categories=GAS, exclude=COSTCO, cap=700000, period="year", group="gas"),
          rule("Restaurants", 3, categories=DINING), rule("Eligible travel", 3, categories=TRAVEL),
          rule("Costco and Costco.com", 2, merchants=COSTCO)],
         membership="Requires Costco membership; rewards paid once a year."),
    # ---- travel / transferable points
    card("chase-sapphire-preferred", "Chase Sapphire Preferred", "Chase", "Visa", "points", 1.0, 9500, 1.0,
         [rule("Chase Travel", 5, merchants=["CHASE TRAVEL"]), rule("Dining", 3, categories=DINING),
          rule("Gas stations and EV charging", 3, categories=GAS), rule("Other travel", 2, categories=TRAVEL + ["Parking & Tolls"])],
         verify=True, notes="Earn categories refreshed June 2026; check streaming/online grocery terms."),
    card("chase-sapphire-reserve", "Chase Sapphire Reserve", "Chase", "Visa", "points", 1.0, 79500, 1.0,
         [rule("Chase Travel", 8, merchants=["CHASE TRAVEL"]),
          rule("Flights and hotels booked direct", 4, categories=["Flights", "Lodging"]),
          rule("Dining", 3, categories=DINING)], verify=True),
    card("amex-gold", "American Express Gold", "American Express", "Amex", "points", 1.0, 32500, 1.0,
         [rule("Restaurants worldwide", 4, categories=DINING, cap=5000000, period="year", group="dining"),
          rule("U.S. supermarkets", 4, categories=GROCERY, exclude=SUPERSTORES, cap=2500000, period="year", group="supermarkets"),
          rule("Flights booked direct or AmexTravel", 3, categories=["Flights"])]),
    card("amex-platinum", "American Express Platinum", "American Express", "Amex", "points", 1.0, 89500, 1.0,
         [rule("Flights booked direct or AmexTravel", 5, categories=["Flights"], cap=50000000, period="year", group="flights"),
          rule("Prepaid hotels on AmexTravel", 5, merchants=["AMEX TRAVEL", "AMEXTRAVEL"])]),
    card("c1-venture", "Capital One Venture", "Capital One", "Visa", "miles", 1.0, 9500, 2.0,
         [rule("Capital One Travel hotels, rentals", 5, merchants=["CAPITAL ONE TRAVEL"])]),
    card("c1-venture-x", "Capital One Venture X", "Capital One", "Visa", "miles", 1.0, 39500, 2.0,
         [rule("Capital One Travel hotels and rental cars", 10, merchants=["CAPITAL ONE TRAVEL"]),
          rule("Capital One Travel flights", 5, merchants=["CAPITAL ONE TRAVEL FLIGHT"])]),
    card("citi-strata-premier", "Citi Strata Premier", "Citi", "Mastercard", "points", 1.0, 9500, 1.0,
         [rule("Citi Travel hotels, cars, attractions", 10, merchants=["CITI TRAVEL"]),
          rule("Air travel and hotels", 3, categories=["Flights", "Lodging"]), rule("Restaurants", 3, categories=DINING),
          rule("Supermarkets", 3, categories=GROCERY), rule("Gas and EV charging", 3, categories=GAS)]),
    # ---- airline
    card("delta-blue", "Delta SkyMiles Blue (Amex)", "American Express", "Amex", "miles", 1.2, 0, 1.0,
         [rule("Delta purchases", 2, merchants=["DELTA"]), rule("Restaurants", 2, categories=DINING)]),
    card("delta-gold", "Delta SkyMiles Gold (Amex)", "American Express", "Amex", "miles", 1.2, 15000, 1.0,
         [rule("Delta purchases", 2, merchants=["DELTA"]), rule("Restaurants", 2, categories=DINING),
          rule("U.S. supermarkets", 2, categories=GROCERY)], verify=True),
    card("citi-aadvantage-platinum", "Citi AAdvantage Platinum Select", "Citi", "Mastercard", "miles", 1.4, 9900, 1.0,
         [rule("American Airlines", 2, merchants=["AMERICAN AIR", "AA.COM"]), rule("Restaurants", 2, categories=DINING),
          rule("Gas stations", 2, categories=GAS)], verify=True),
    card("united-explorer", "United Explorer", "Chase", "Visa", "miles", 1.2, 15000, 1.0,
         [rule("United purchases", 2, merchants=["UNITED"]), rule("Dining", 2, categories=DINING),
          rule("Hotel stays", 2, categories=["Lodging"])], verify=True),
    card("southwest-plus", "Southwest Rapid Rewards Plus", "Chase", "Visa", "points", 1.3, 9900, 1.0,
         [rule("Southwest purchases", 2, merchants=["SOUTHWEST"]), rule("Local transit and rideshare", 2, categories=TRANSIT)],
         verify=True),
    # ---- hotel
    card("marriott-boundless", "Marriott Bonvoy Boundless", "Chase", "Visa", "points", 0.75, 9500, 2.0,
         [rule("Marriott hotels", 6, merchants=["MARRIOTT", "WESTIN", "SHERATON", "RITZ", "COURTYARD", "RESIDENCE INN"]),
          rule("Grocery, gas, dining", 3, categories=GROCERY + GAS + DINING, cap=600000, period="year", group="everyday")]),
    card("hilton-honors-amex", "Hilton Honors (Amex, no fee)", "American Express", "Amex", "points", 0.5, 0, 3.0,
         [rule("Hilton hotels", 7, merchants=["HILTON", "HAMPTON", "DOUBLETREE", "EMBASSY SUITES"]),
          rule("Restaurants, supermarkets, gas", 5, categories=DINING + GROCERY + GAS)], verify=True),
    card("hilton-surpass", "Hilton Honors Surpass (Amex)", "American Express", "Amex", "points", 0.5, 15000, 3.0,
         [rule("Hilton hotels", 12, merchants=["HILTON", "HAMPTON", "DOUBLETREE", "EMBASSY SUITES"]),
          rule("Restaurants, supermarkets, gas", 6, categories=DINING + GROCERY + GAS),
          rule("U.S. online retail", 4, merchants=["AMAZON", "AMZN"])], verify=True),
    # ---- generic templates
    card("generic-1", "Generic: flat 1% cash back", "—", "Other", "cash", 1.0, 0, 1.0),
    card("generic-1-5", "Generic: flat 1.5% cash back", "—", "Other", "cash", 1.0, 0, 1.5),
    card("generic-2", "Generic: flat 2% cash back", "—", "Other", "cash", 1.0, 0, 2.0),
]

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "app" / "data" / "card_catalog.json"
    keys = [c["key"] for c in CARDS]
    assert len(keys) == len(set(keys)), "duplicate keys"
    out.write_text(json.dumps({"checked": CHECKED, "cards": CARDS}, indent=1))
    print(f"{len(CARDS)} cards -> {out}")

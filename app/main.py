"""Personal Finance Insights — a private, local-only budgeting app. Run with: python -m app (binds to 127.0.0.1 only)."""
import csv
import io
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import budgets as budgets_mod, categories as cats, charts, db, orgs, reports, rewards, rules as rules_mod, services, splits
from .importers import csv_import, ofx
from .normalize import DATE_FORMATS, header_fingerprint

HERE = Path(__file__).resolve().parent
STAGING = db.DATA_DIR / "staging"
MAX_UPLOAD = 20 * 1024 * 1024
PAGE_SIZE = 100


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.adopt_legacy_db()
    orgs.ensure()
    for o in orgs.all_orgs():
        db.ensure_initialised(orgs.path_of(o))
    STAGING.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Personal Finance Insights", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def money(cents: int | None) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


templates.env.filters["money"] = money


_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
ORG_COOKIE = "pfi_org"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    from fastapi.responses import PlainTextResponse
    # Block DNS-rebinding: a web page can't reach this app by pointing its own domain at 127.0.0.1.
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in _ALLOWED_HOSTS:
        return PlainTextResponse("Forbidden host", status_code=403)
    # Block cross-site form posts (CSRF) from other sites open in your browser.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # Modern browsers (Chrome, Edge, Firefox, Safari) say where a request came from in
        # Sec-Fetch-Site. Trust that first; fall back to Origin for older browsers.
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if site is not None:
            allowed = site in ("same-origin", "none")
        elif origin and origin != "null":
            o_host = re.sub(r"^https?://", "", origin).rsplit(":", 1)[0].strip("[]")
            allowed = o_host in _ALLOWED_HOSTS
        else:
            allowed = origin is None  # non-browser clients (tests, curl) send neither header
        if not allowed:
            return PlainTextResponse("Cross-site request blocked", status_code=403)
    # Which organisation this request works on: chosen in the header switcher, kept in a cookie.
    org = orgs.resolve(request.cookies.get(ORG_COOKIE))
    org_path = orgs.path_of(org)
    db.ensure_initialised(org_path)
    request.state.org = org
    token = db.current_db.set(org_path)
    try:
        resp = await call_next(request)
    finally:
        db.current_db.reset(token)
    # Nothing on these pages may load from or send to anywhere else.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def conn():
    c = db.connect()
    return c


def render(request: Request, name: str, **ctx):
    ctx.setdefault("current_org", getattr(request.state, "org", None))
    ctx.setdefault("all_orgs", orgs.all_orgs())
    return templates.TemplateResponse(request, name, ctx)


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    ref = request.headers.get("referer") or ""
    # Only bounce back within this app.
    target = fallback
    m = re.match(r"^https?://[^/]+(/.*)$", ref)
    if m and m.group(1).startswith("/"):
        target = m.group(1)
    return RedirectResponse(target, status_code=303)


# ---------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, month: str | None = None):
    month = month if month and re.fullmatch(r"\d{4}-\d{2}", month) else date.today().strftime("%Y-%m")
    y, m = int(month[:4]), int(month[5:])
    m_start = f"{month}-01"
    m_end = (reports._month_start(date(y, m, 1), -1).toordinal() - 1)
    m_end = date.fromordinal(m_end).isoformat()
    with conn() as c:
        accounts = services.account_balances(c)
        summary = services.month_summary(c, month)
        spending = charts.fold(reports.category_totals(c, m_start, m_end, "expense", level=1))
        trend = reports.income_vs_expense(c, reports._month_start(date(y, m, 1), 5).isoformat(), m_end)
        uncategorized = c.execute("SELECT COUNT(DISTINCT tx_id) FROM report_lines WHERE category_id IS NULL").fetchone()[0]
        budget = budgets_mod.overview(c, month)
        excluded = reports.excluded_summary(c, m_start, m_end)
        recs = rewards.recommendations(c, limit=5) if month == date.today().strftime("%Y-%m") else []
        rw_alerts = len(rewards.alerts(c)) if recs else 0
        for r in budget["rows"]:
            r["bar"] = charts.progress(r["spent"], r["budget"], width=160) if r["budget"] else None
        months = [r[0] for r in c.execute(
            "SELECT DISTINCT substr(posted_date,1,7) FROM transactions ORDER BY 1 DESC LIMIT 36")]
    assets = sum(a["balance_cents"] for a in accounts if a["type"] not in ("credit", "loan"))
    debts = sum(a["balance_cents"] for a in accounts if a["type"] in ("credit", "loan"))
    if month not in months:
        months.insert(0, month)
    trend_chart = charts.columns([_mlabel(d["month"]) for d in trend], [
        {"name": "Income", "cls": "s1", "values": [d["income"] for d in trend]},
        {"name": "Expenses", "cls": "s2", "values": [d["expense"] for d in trend]}], height=240)
    return render(request, "dashboard.html", accounts=accounts, summary=summary, month=month, months=months,
                  uncategorized=uncategorized, budget=budget, excluded=excluded, recs=recs, rw_alerts=rw_alerts, assets=assets, debts=debts, spending=spending,
                  pie=charts.donut(spending, size=240), trend_chart=trend_chart, m_start=m_start, m_end=m_end)


def _mlabel(ym: str) -> str:
    return date(int(ym[:4]), int(ym[5:]), 1).strftime("%b %y")


def _int(v) -> int | None:
    """Query-string ints that may arrive empty ('All accounts' sends account=)."""
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- accounts

ACCOUNT_TYPES = ["checking", "savings", "credit", "loan", "investment", "cash"]


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, error: str | None = None, msg: str | None = None):
    with conn() as c:
        accounts = services.account_balances(c)
        card_names = dict(c.execute("SELECT account_id, card_name FROM card_programs").fetchall())
    return render(request, "accounts.html", accounts=accounts, types=ACCOUNT_TYPES, error=error, msg=msg,
                  card_names=card_names)


@app.post("/accounts")
def create_account(name: str = Form(...), institution: str = Form(""), type: str = Form(...),
                   opening_balance: str = Form("0")):
    from .normalize import parse_amount_cents
    if type not in ACCOUNT_TYPES:
        raise HTTPException(400, "bad account type")
    try:
        opening = parse_amount_cents(opening_balance) or 0
    except ValueError:
        return RedirectResponse("/accounts?error=Opening+balance+is+not+a+number", status_code=303)
    with conn() as c:
        try:
            c.execute("INSERT INTO accounts(name, institution, type, opening_balance_cents) VALUES (?,?,?,?)",
                      (name.strip(), institution.strip(), type, opening))
        except Exception:
            return RedirectResponse("/accounts?error=An+account+with+that+name+exists", status_code=303)
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/{account_id:int}")
def edit_account(account_id: int, name: str = Form(...), institution: str = Form(""), type: str = Form(...),
                 opening_balance: str = Form("0")):
    from urllib.parse import quote
    from .normalize import parse_amount_cents
    if type not in ACCOUNT_TYPES or not name.strip():
        return RedirectResponse("/accounts?error=" + quote("Name and type are required."), status_code=303)
    try:
        opening = parse_amount_cents(opening_balance) or 0
    except ValueError:
        return RedirectResponse("/accounts?error=" + quote("Opening balance is not a number."), status_code=303)
    with conn() as c:
        if c.execute("SELECT 1 FROM accounts WHERE name = ? COLLATE NOCASE AND id != ?", (name.strip(), account_id)).fetchone():
            return RedirectResponse("/accounts?error=" + quote(f"There's already an account called “{name.strip()}”."),
                                    status_code=303)
        c.execute("UPDATE accounts SET name = ?, institution = ?, type = ?, opening_balance_cents = ? WHERE id = ?",
                  (name.strip(), institution.strip(), type, opening, account_id))
    return RedirectResponse("/accounts?msg=" + quote("Saved."), status_code=303)


@app.post("/accounts/{account_id:int}/delete")
def delete_account(account_id: int, confirm_name: str = Form("")):
    """Permanently delete an account with all its transactions, splits, import history and
    account-specific rules. Requires typing the account's name."""
    from urllib.parse import quote
    with conn() as c:
        acct = c.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acct:
            raise HTTPException(404)
        if confirm_name.strip() != acct["name"]:
            return RedirectResponse("/accounts?error=" + quote(f"Type the account name exactly (“{acct['name']}”) to delete it."),
                                    status_code=303)
        n = c.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)).fetchone()[0]
        c.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))   # splits cascade
        c.execute("DELETE FROM imports WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM rules WHERE account_id = ?", (account_id,))          # conditions cascade
        c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return RedirectResponse("/accounts?msg=" + quote(f"Deleted “{acct['name']}” and its {n} transaction{'s' if n != 1 else ''}."),
                            status_code=303)


@app.post("/accounts/{account_id:int}/opening")
def set_opening(account_id: int, opening_balance: str = Form(...)):
    from .normalize import parse_amount_cents
    with conn() as c:
        c.execute("UPDATE accounts SET opening_balance_cents = ? WHERE id = ?",
                  (parse_amount_cents(opening_balance) or 0, account_id))
    return RedirectResponse("/accounts", status_code=303)


# ---------------------------------------------------------------- import

@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    with conn() as c:
        accounts = c.execute("SELECT * FROM accounts ORDER BY name").fetchall()
        profiles = c.execute("SELECT * FROM import_profiles ORDER BY name").fetchall()
        history = c.execute("""SELECT i.*, a.name AS account FROM imports i JOIN accounts a ON a.id = i.account_id
                               ORDER BY i.id DESC LIMIT 50""").fetchall()
    return render(request, "import.html", accounts=accounts, profiles=profiles, history=history)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Could not read file encoding")


@app.post("/import", response_class=HTMLResponse)
async def upload(request: Request, account_id: int = Form(...), file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large")
    text = _decode(data)
    filename = Path(file.filename or "upload").name
    with conn() as c:
        if not c.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
            raise HTTPException(400, "Unknown account")
        if ofx.is_ofx(text):
            txns = ofx.parse(text)
            res = services.store(c, account_id, filename, services.sha256(data), "ofx", txns)
            return render(request, "import_result.html", result=res, filename=filename)

        rows = csv_import.read_rows(text)
        if len(rows) < 2:
            raise HTTPException(400, "That file has no rows")
        hr = csv_import.find_header_row(rows)
        prof = c.execute("SELECT * FROM import_profiles WHERE header_fingerprint = ?",
                         (header_fingerprint(rows[hr]),)).fetchone()
        if prof:
            mapping = json.loads(prof["mapping_json"])
            txns, errors = csv_import.parse(rows, mapping)
            res = services.store(c, account_id, filename, services.sha256(data), "csv", txns, errors)
            return render(request, "import_result.html", result=res, filename=filename, profile=prof["name"])

    token = secrets.token_urlsafe(16)
    (STAGING / f"{token}.csv").write_text(text, encoding="utf-8")
    (STAGING / f"{token}.json").write_text(json.dumps({"account_id": account_id, "filename": filename,
                                                       "sha256": services.sha256(data),
                                                       "org_db": str(db.current_db.get())}))
    return RedirectResponse(f"/import/map/{token}", status_code=303)


def _staged(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", token):
        raise HTTPException(404)
    p = STAGING / f"{token}.csv"
    if not p.exists():
        raise HTTPException(404, "That upload expired; please upload it again")
    meta = json.loads((STAGING / f"{token}.json").read_text())
    if meta.get("org_db") and meta["org_db"] != str(db.current_db.get()):
        raise HTTPException(409, "This upload was started in a different organisation. Switch back to it, "
                                 "or upload the file again here.")
    return csv_import.read_rows(p.read_text(encoding="utf-8")), meta


def _mapping_from_form(form) -> dict:
    def opt(k):
        v = (form.get(k) or "").strip()
        return v or None
    return {
        "header_row": int(form.get("header_row") or 0),
        "date_col": opt("date_col"), "date_format": opt("date_format"),
        "description_col": opt("description_col"), "memo_col": opt("memo_col"),
        "amount_mode": form.get("amount_mode") or "single",
        "amount_col": opt("amount_col"), "negate": form.get("negate") == "on",
        "debit_col": opt("debit_col"), "credit_col": opt("credit_col"),
    }


def _render_mapping(request, token, rows, meta, mapping, errors, profile_name=""):
    hr = max(0, min(mapping["header_row"], len(rows) - 1))
    mapping["header_row"] = hr
    headers = [h.strip() for h in rows[hr]]
    preview, row_errors = [], []
    if not errors:
        txns, row_errors = csv_import.parse(rows, mapping)
        preview = txns[:15]
    with conn() as c:
        account = c.execute("SELECT * FROM accounts WHERE id = ?", (meta["account_id"],)).fetchone()
    return render(request, "map.html", token=token, headers=headers, raw=rows[: hr + 6], mapping=mapping,
                  errors=errors, preview=preview, row_errors=row_errors[:20], date_formats=DATE_FORMATS,
                  account=account, meta=meta, profile_name=profile_name or (account["institution"] or account["name"]))


@app.get("/import/map/{token}", response_class=HTMLResponse)
def map_page(request: Request, token: str):
    rows, meta = _staged(token)
    mapping = csv_import.suggest_mapping(rows)
    headers = [h.strip() for h in rows[mapping["header_row"]]]
    return _render_mapping(request, token, rows, meta, mapping, csv_import.validate_mapping(mapping, headers))


@app.post("/import/map/{token}", response_class=HTMLResponse)
async def map_submit(request: Request, token: str):
    rows, meta = _staged(token)
    form = await request.form()
    mapping = _mapping_from_form(form)
    hr = max(0, min(mapping["header_row"], len(rows) - 1))
    headers = [h.strip() for h in rows[hr]]
    errors = csv_import.validate_mapping(mapping, headers)
    profile_name = (form.get("profile_name") or "").strip()
    if errors or form.get("action") != "import":
        return _render_mapping(request, token, rows, meta, mapping, errors, profile_name)

    txns, row_errors = csv_import.parse(rows, mapping)
    with conn() as c:
        if form.get("save_profile") == "on":
            c.execute("INSERT OR REPLACE INTO import_profiles(name, header_fingerprint, mapping_json) VALUES (?,?,?)",
                      (profile_name or "Unnamed bank", header_fingerprint(rows[hr]), json.dumps(mapping)))
        res = services.store(c, meta["account_id"], meta["filename"], meta["sha256"], "csv", txns, row_errors)
    for ext in ("csv", "json"):
        (STAGING / f"{token}.{ext}").unlink(missing_ok=True)
    return render(request, "import_result.html", result=res, filename=meta["filename"])


@app.post("/imports/{import_id:int}/undo")
def undo(import_id: int):
    with conn() as c:
        services.undo_import(c, import_id)
    return RedirectResponse("/import", status_code=303)


@app.post("/profiles/{profile_id:int}/delete")
def delete_profile(profile_id: int):
    with conn() as c:
        c.execute("DELETE FROM import_profiles WHERE id = ?", (profile_id,))
    return RedirectResponse("/import", status_code=303)


# ---------------------------------------------------------------- transactions


AMOUNT_FILTER_OPS = {"eq": "=", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "between"}


def _amount_where(op, v1, v2):
    """SQL on the size of the transaction (ABS), so you always type positive numbers."""
    from .normalize import parse_amount_cents
    try:
        a = abs(parse_amount_cents(v1)) if v1 not in (None, "") else None
        b = abs(parse_amount_cents(v2)) if v2 not in (None, "") else None
    except ValueError:
        return None, []
    if a is None:
        return None, []
    op = op if op in AMOUNT_FILTER_OPS else "eq"
    if op == "between":
        lo, hi = sorted([a, b if b is not None else a])
        return "ABS(t.amount_cents) BETWEEN ? AND ?", [lo, hi]
    sql_op = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
    return f"ABS(t.amount_cents) {sql_op} ?", [a]


def _looks_like_amount(q: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\$?\s*\d{1,3}(,\d{3})*(\.\d{1,2})?|[-+]?\$?\s*\d+(\.\d{1,2})?", q.strip()))


def _tx_filters(conn_, account, category, month, q, start=None, end=None, amt_op=None, amt=None, amt2=None,
                flow=None, excl=None):
    where, args = [], []
    account = _int(account)
    if account:
        where.append("t.account_id = ?"); args.append(account)
    # Category filters look at lines, so a split transaction matches any of its split categories.
    if category == "none":
        where.append("t.id IN (SELECT tx_id FROM tx_lines WHERE category_id IS NULL)")
    elif _int(category):
        ids = cats.load(conn_).descendants(_int(category)) or [-1]  # a parent includes its subcategories
        where.append(f"t.id IN (SELECT tx_id FROM tx_lines WHERE category_id IN ({','.join('?' * len(ids))}))")
        args += ids
    if month and re.fullmatch(r"\d{4}-\d{2}", month):
        where.append("substr(t.posted_date,1,7) = ?"); args.append(month)
    if start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        where.append("t.posted_date >= ?"); args.append(start)
    if end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        where.append("t.posted_date <= ?"); args.append(end)
    if q:
        q = q.strip()
        if _looks_like_amount(q):
            # A number in the search box also finds transactions of exactly that size.
            amt_sql, amt_args = _amount_where("eq", q, None)
            where.append(f"(t.payee LIKE ? OR t.payee_raw LIKE ? OR t.memo LIKE ? OR t.notes LIKE ? OR {amt_sql})")
            args += [f"%{q}%"] * 4 + amt_args
        else:
            where.append("(t.payee LIKE ? OR t.payee_raw LIKE ? OR t.memo LIKE ? OR t.notes LIKE ?)")
            args += [f"%{q}%"] * 4
    amt_sql, amt_args = _amount_where(amt_op, amt, amt2)
    if amt_sql:
        where.append(amt_sql); args += amt_args
    if flow == "out":
        where.append("t.amount_cents < 0")
    elif flow == "in":
        where.append("t.amount_cents > 0")
    # Left out of reports & budgets — by the transaction's own flag or by its category.
    if excl == "only":
        where.append("t.id IN (SELECT tx_id FROM tx_lines WHERE excluded = 1)")
    elif excl == "hide":
        where.append("t.id NOT IN (SELECT tx_id FROM tx_lines WHERE excluded = 1)")
    return (" WHERE " + " AND ".join(where)) if where else "", args


# Sortable columns: SQL (direction filled in) and the natural first direction when you click a header.
_CAT_PATH = ("CASE WHEN EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id) THEN '~split' "
             "ELSE COALESCE(c3.name || '/' || c2.name || '/' || c1.name, c2.name || '/' || c1.name, c1.name, '~~') END")
SORTS = {
    "date": ("t.posted_date {d}", "desc"),
    "payee": ("t.payee COLLATE NOCASE {d}, t.posted_date DESC", "asc"),
    "account": ("a.name COLLATE NOCASE {d}, t.posted_date DESC", "asc"),
    "category": (_CAT_PATH + " COLLATE NOCASE {d}, t.posted_date DESC", "asc"),
    "amount": ("t.amount_cents {d}", "asc"),   # asc = biggest spending first
}


@app.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, account: str | None = None, category: str | None = None,
                 month: str | None = None, q: str | None = None, start: str | None = None,
                 end: str | None = None, page: str | None = None, updated: str | None = None,
                 sort: str = "date", dir: str | None = None, amt_op: str | None = None, amt: str | None = None,
                 amt2: str | None = None, flow: str | None = None, excl: str | None = None):
    page = max(_int(page) or 1, 1)
    sort = sort if sort in SORTS else "date"
    dir = dir if dir in ("asc", "desc") else SORTS[sort][1]
    with conn() as c:
        tree = cats.load(c)
        where, args = _tx_filters(c, account, category, month, q, start, end, amt_op, amt, amt2, flow, excl)
        total, total_cents = c.execute(
            f"SELECT COUNT(*), COALESCE(SUM(t.amount_cents),0) FROM transactions t{where}", args).fetchone()
        order = SORTS[sort][0].format(d=dir.upper())
        txns = c.execute(f"""SELECT t.*, a.name AS account_name
                             FROM transactions t JOIN accounts a ON a.id = t.account_id
                             LEFT JOIN categories c1 ON c1.id = t.category_id
                             LEFT JOIN categories c2 ON c2.id = c1.parent_id
                             LEFT JOIN categories c3 ON c3.id = c2.parent_id{where}
                             ORDER BY {order}, t.id {dir.upper()} LIMIT ? OFFSET ?""",
                         (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
        accounts = c.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        split_map = splits.for_transactions(c, [t["id"] for t in txns])
        page_ids = [t["id"] for t in txns] or [-1]
        excluded_ids = {r[0] for r in c.execute(
            f"SELECT DISTINCT tx_id FROM tx_lines WHERE excluded = 1 AND tx_id IN ({','.join('?' * len(page_ids))})",
            page_ids)}
        tx_rewards = _rewards_for(c, txns)
        excl_n, excl_cents = c.execute(
            f"SELECT COUNT(*), COALESCE(SUM(t.amount_cents), 0) FROM transactions t{where}"
            f"{' AND' if where else ' WHERE'} t.id IN (SELECT tx_id FROM tx_lines WHERE excluded = 1)", args).fetchone()
    f = {"account": _int(account), "category": category or "", "month": month or "", "q": q or "",
         "start": start or "", "end": end or "", "sort": sort, "dir": dir, "amt_op": amt_op or "",
         "amt": amt or "", "amt2": amt2 or "", "flow": flow or "", "excl": excl or ""}
    return render(request, "transactions.html", txns=txns, accounts=accounts, cat_list=tree.ordered(),
                  tree=tree, f=f, total=total, total_cents=total_cents, page=page,
                  pages=max(1, -(-total // PAGE_SIZE)), updated=_int(updated), split_map=split_map,
                  sort_defaults={k: v[1] for k, v in SORTS.items()}, amount_ops=AMOUNT_FILTER_OPS,
                  excluded_ids=excluded_ids, excl_n=excl_n, excl_cents=excl_cents, tx_rewards=tx_rewards)


def _rewards_for(c, txns) -> dict:
    """Estimated reward per transaction on this page (credit cards with rewards set up only)."""
    if not txns or not c.execute("SELECT 1 FROM card_programs LIMIT 1").fetchone():
        return {}
    ids = {t["id"] for t in txns}
    lo, hi = min(t["posted_date"] for t in txns), max(t["posted_date"] for t in txns)
    out: dict = {}
    for ln in rewards.compute(c, lo, hi)["lines"]:
        if ln["tx_id"] not in ids or ln["actual"] is None:
            continue
        e = out.setdefault(ln["tx_id"], {"value": 0.0, "best": 0.0, "best_card": None})
        e["value"] += ln["actual"]["value"]
        if ln["best"]:
            e["best"] += ln["best"]["value"]
            e["best_card"] = ln["best_card"].account_name
    for e in out.values():
        e["missed"] = e["best"] - e["value"] if e["best_card"] else 0
    return out


@app.post("/transactions/bulk")
async def bulk_update(request: Request):
    """Set one category on many transactions: the ticked ones, or everything matching the current filter."""
    form = await request.form()
    action = form.get("action") or "category"
    category_id = _int(form.get("category_id"))
    clear = form.get("category_id") == "none"
    if action == "category" and category_id is None and not clear:
        return back(request, "/transactions")
    with conn() as c:
        if form.get("scope") == "filter":
            where, args = _tx_filters(c, form.get("f_account"), form.get("f_category"), form.get("f_month"),
                                      form.get("f_q"), form.get("f_start"), form.get("f_end"), form.get("f_amt_op"),
                                      form.get("f_amt"), form.get("f_amt2"), form.get("f_flow"), form.get("f_excl"))
            ids = [r[0] for r in c.execute(f"SELECT t.id FROM transactions t{where}", args)]
        else:
            ids = [i for i in (_int(v) for v in form.getlist("ids")) if i]
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            ph = ",".join("?" * len(part))
            if action in ("exclude", "include"):
                c.execute(f"UPDATE transactions SET excluded = ? WHERE id IN ({ph})",
                          (1 if action == "exclude" else 0, *part))
                continue
            c.execute(f"DELETE FROM splits WHERE transaction_id IN ({ph})", part)
            if clear:
                c.execute(f"UPDATE transactions SET category_id = NULL, category_locked = 0 WHERE id IN ({ph})", part)
            else:
                c.execute(f"UPDATE transactions SET category_id = ?, category_locked = 1 WHERE id IN ({ph})",
                          (category_id, *part))
    target = form.get("return_to") or "/transactions"
    if not target.startswith("/transactions"):
        target = "/transactions"
    target = re.sub(r"([?&])updated=\d*&?", r"\1", target).rstrip("?&")
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}updated={len(ids)}", status_code=303)


@app.post("/transactions/{tx_id:int}")
def update_tx(request: Request, tx_id: int, category_id: str = Form(""), payee: str = Form(None),
              notes: str = Form(None), details_only: str = Form(None), excluded: str = Form(None),
              set_excluded: str = Form(None)):
    with conn() as c:
        if details_only:
            pass
        else:
            splits.remove(c, tx_id, _int(category_id))  # picking one category replaces any split
        if payee is not None and payee.strip():
            c.execute("UPDATE transactions SET payee = ? WHERE id = ?", (payee.strip(), tx_id))
        if notes is not None:
            c.execute("UPDATE transactions SET notes = ? WHERE id = ?", (notes.strip() or None, tx_id))
        if set_excluded is not None:   # the edit form always sends set_excluded; the checkbox sends excluded
            c.execute("UPDATE transactions SET excluded = ? WHERE id = ?", (1 if excluded else 0, tx_id))
    return back(request, "/transactions")


@app.get("/export.csv")
def export(account: str | None = None, category: str | None = None, month: str | None = None,
           q: str | None = None, start: str | None = None, end: str | None = None, amt_op: str | None = None,
           amt: str | None = None, amt2: str | None = None, flow: str | None = None, excl: str | None = None):
    """One row per line: split transactions export one row per split, marked in the 'split' column."""
    with conn() as c:
        tree = cats.load(c)
        where, args = _tx_filters(c, account, category, month, q, start, end, amt_op, amt, amt2, flow, excl)
        rows = c.execute(f"""SELECT t.posted_date, a.name, t.payee, t.payee_raw, t.memo, l.amount_cents,
                                    l.category_id, COALESCE(t.notes,''), l.is_split, t.amount_cents, l.excluded
                             FROM transactions t JOIN accounts a ON a.id = t.account_id
                             JOIN tx_lines l ON l.tx_id = t.id{where}
                             ORDER BY t.posted_date, t.id""", args).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "account", "payee", "original_description", "memo", "amount", "category", "notes",
                "split", "transaction_total", "excluded_from_reports"])
    for r in rows:
        cat = tree.by_id[r[6]].path if r[6] in tree.by_id else ""
        w.writerow([r[0], r[1], r[2], r[3], r[4] or "", f"{r[5] / 100:.2f}", cat, r[7], "yes" if r[8] else "",
                    f"{r[9] / 100:.2f}", "yes" if r[10] else ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=pfi-transactions.csv"})


# ---------------------------------------------------------------- splits

@app.get("/transactions/{tx_id:int}/split", response_class=HTMLResponse)
def split_page(request: Request, tx_id: int, rows: str | None = None, error: str | None = None):
    with conn() as c:
        tx = c.execute("SELECT t.*, a.name AS account_name FROM transactions t JOIN accounts a ON a.id = t.account_id"
                       " WHERE t.id = ?", (tx_id,)).fetchone()
        if not tx:
            raise HTTPException(404)
        tree = cats.load(c)
        existing = splits.get(c, tx_id)
    lines = [{"category_id": s_["category_id"], "amount": abs(s_["amount_cents"]), "memo": s_["memo"] or ""}
             for s_ in existing]
    if not lines:  # start with the current category holding the full amount, plus an empty line
        lines = [{"category_id": tx["category_id"], "amount": abs(tx["amount_cents"]), "memo": ""}]
    want = max(len(lines) + 1, min(_int(rows) or 0, 30), 3)
    lines += [{"category_id": None, "amount": None, "memo": ""}] * (want - len(lines))
    return render(request, "split.html", tx=tx, lines=lines, cat_list=tree.ordered(), error=error,
                  is_split=bool(existing), back_to=request.query_params.get("back", "/transactions"))


@app.post("/transactions/{tx_id:int}/split", response_class=HTMLResponse)
async def split_save(request: Request, tx_id: int):
    from .normalize import parse_amount_cents
    form = await request.form()
    cats_ = form.getlist("category_id")
    amts = form.getlist("amount")
    memos = form.getlist("memo")
    back_to = form.get("back_to") or "/transactions"
    if not back_to.startswith("/transactions"):
        back_to = "/transactions"
    if form.get("action") == "more":
        return RedirectResponse(f"/transactions/{tx_id}/split?rows={len(amts) + 3}", status_code=303)
    parts, problems = [], []
    for i, raw in enumerate(amts):
        if not (raw or "").strip():
            continue
        try:
            amt = parse_amount_cents(raw)
        except ValueError:
            problems.append(f"Line {i + 1}: “{raw}” isn't an amount.")
            continue
        parts.append((_int(cats_[i]) if i < len(cats_) else None, amt, memos[i] if i < len(memos) else ""))
    from urllib.parse import quote
    if problems:
        return RedirectResponse(f"/transactions/{tx_id}/split?error={quote(' '.join(problems))}", status_code=303)
    try:
        with conn() as c:
            splits.save(c, tx_id, parts)
    except splits.SplitError as e:
        return RedirectResponse(f"/transactions/{tx_id}/split?error={quote(str(e))}", status_code=303)
    return RedirectResponse(back_to, status_code=303)


@app.post("/transactions/{tx_id:int}/unsplit")
def unsplit(request: Request, tx_id: int):
    with conn() as c:
        first = c.execute("SELECT category_id FROM splits WHERE transaction_id = ? ORDER BY amount_cents LIMIT 1",
                          (tx_id,)).fetchone()
        splits.remove(c, tx_id, first["category_id"] if first else None)
    return RedirectResponse("/transactions", status_code=303)


# ---------------------------------------------------------------- budgets

@app.get("/budgets", response_class=HTMLResponse)
def budgets_page(request: Request, month: str | None = None, kind: str = "expense", show: str = "budgeted",
                 msg: str | None = None):
    month = month if month and re.fullmatch(r"\d{4}-\d{2}", month) else date.today().strftime("%Y-%m")
    kind = kind if kind in ("expense", "income") else "expense"
    with conn() as c:
        ov = budgets_mod.overview(c, month, kind)
        excluded = reports.excluded_summary(c, *budgets_mod.month_bounds(month))
    y, m = int(month[:4]), int(month[5:])
    prev_m = reports._month_start(date(y, m, 1), 1).strftime("%Y-%m")
    next_m = reports._month_start(date(y, m, 1), -1).strftime("%Y-%m")
    rows = ov["rows"]
    if show == "budgeted":
        keep = {r["cat"].id for r in rows if r["budget"] is not None or r["has_activity"]}
        # keep parents of anything kept, so the tree still reads correctly
        for r in rows:
            if r["cat"].id in keep:
                keep.update(r["cat"].ancestors)
        rows = [r for r in rows if r["cat"].id in keep]
    for r in rows:
        r["bar"] = charts.progress(r["spent"], r["budget"]) if r["budget"] else None
    return render(request, "budgets.html", month=month, kind=kind, show=show, ov=ov, rows=rows, prev_m=prev_m,
                  next_m=next_m, msg=msg, label=_mlabel(month), excluded=excluded)


@app.post("/budgets")
async def budgets_save(request: Request):
    from .normalize import parse_amount_cents
    from urllib.parse import quote
    form = await request.form()
    month = form.get("month") or ""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400)
    scope = "default" if form.get("scope") == "default" else month
    changed, bad = 0, []
    with conn() as c:
        valid = {r[0] for r in c.execute("SELECT id FROM categories")}
        current = budgets_mod.amounts_for(c, month)
        for key, val in form.items():
            if not key.startswith("b_"):
                continue
            cid = _int(key[2:])
            if cid not in valid:
                continue
            val = (val or "").strip()
            try:
                amt = parse_amount_cents(val) if val else None
            except ValueError:
                bad.append(val)
                continue
            if amt == current.get(cid, {}).get("amount"):
                continue  # unchanged
            budgets_mod.set_amount(c, cid, scope, amt)  # blank removes the budget at this scope
            changed += 1
        if form.get("clear_month") == "on":
            c.execute("DELETE FROM budgets WHERE month = ?", (month,))
    note = f"Saved {changed} budget{'s' if changed != 1 else ''}" + (
        " as your monthly default." if scope == "default" else f" for {month} only.")
    if bad:
        note += f" Ignored values that aren't amounts: {', '.join(bad[:5])}."
    return RedirectResponse(f"/budgets?month={month}&kind={form.get('kind', 'expense')}&show={form.get('show', 'budgeted')}"
                            f"&msg={quote(note)}", status_code=303)


# ---------------------------------------------------------------- reports

REPORTS = [("spending", "Spending by category"), ("income", "Income by category"),
           ("cashflow", "Income vs expenses"), ("trend", "Spending over time"),
           ("merchants", "Merchants"), ("networth", "Net worth")]


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, report: str = "spending", period: str = "6m", start: str | None = None,
                 end: str | None = None, account: str | None = None, level: str | None = None,
                 view: str = "pie", category: str | None = None, within: str | None = None,
                 payee: str | None = None):
    report = "merchants" if report == "payees" else report
    report = report if report in dict(REPORTS) else "spending"
    view = view if view in ("pie", "bar") else "pie"
    lvl = min(max(_int(level) or 1, 1), cats.MAX_DEPTH)
    acct = _int(account)
    with conn() as c:
        tree = cats.load(c)
        s, e = reports.period_range(period, start, end)
        if period == "all":
            b = reports.data_bounds(c)
            s, e = b if b else (date.today().replace(day=1).isoformat(), date.today().isoformat())
        ctx = {"report": report, "reports": REPORTS, "periods": reports.PERIODS, "period": period, "start": s,
               "end": e, "account": acct, "level": lvl, "view": view, "tree": tree, "cat_list": tree.ordered(),
               "accounts": c.execute("SELECT id, name FROM accounts ORDER BY name").fetchall(),
               "category": _int(category), "within": None, "breadcrumb": [], "payee": payee,
               "parents": [x for x in tree.ordered() if x.children and x.depth < cats.MAX_DEPTH],
               "excluded": reports.excluded_summary(c, s, e, acct)}

        if report in ("spending", "income"):
            kind = "expense" if report == "spending" else "income"
            w = _int(within)
            if w and w in tree.by_id:
                ctx["within"] = tree.by_id[w]
                ctx["breadcrumb"] = [tree.by_id[a] for a in tree.by_id[w].ancestors] + [tree.by_id[w]]
            items = reports.category_totals(c, s, e, kind, lvl, acct, within=w if ctx["within"] else None)
            if ctx["within"]:
                for i in items:  # money put on the parent itself, not in any of its subcategories
                    if i["id"] == w:
                        i["label"] = f"{ctx['within'].name} (not in a subcategory)"
                        i["has_children"] = False
            total = sum(i["value"] for i in items)
            folded = charts.fold([dict(i) for i in items])
            ctx.update(items=items, total=total, legend_items=folded,
                       chart=charts.donut(folded) if view == "pie" else charts.hbars(items, single_class="s1"))

        elif report == "cashflow":
            rows = reports.income_vs_expense(c, s, e, acct)
            labels = [_mlabel(r["month"]) for r in rows]
            ctx.update(rows=rows, chart=charts.columns(labels, [
                {"name": "Income", "cls": "s1", "values": [r["income"] for r in rows]},
                {"name": "Expenses", "cls": "s2", "values": [r["expense"] for r in rows]}]),
                net_chart=charts.columns(labels, [{"name": "Net", "cls": "s3", "values": [r["net"] for r in rows]}],
                                         height=200),
                totals={k: sum(r[k] for r in rows) for k in ("income", "expense", "net")})

        elif report == "trend":
            cid = ctx["category"] if ctx["category"] in tree.by_id else None
            rows = reports.category_trend(c, s, e, cid, acct)
            name = tree.by_id[cid].path if cid else "All spending"
            ctx.update(rows=rows, series_name=name, chart=charts.columns(
                [_mlabel(r["month"]) for r in rows], [{"name": name, "cls": "s1", "values": [r["value"] for r in rows]}]),
                total=sum(r["value"] for r in rows), avg=(sum(r["value"] for r in rows) // max(1, len(rows))))

        elif report == "networth":
            rows = reports.net_worth(c, s, e)
            ctx.update(rows=rows, chart=charts.lines([_mlabel(r["month"]) for r in rows], [
                {"name": "Net worth", "cls": "s1", "values": [r["net"] for r in rows]},
                {"name": "Assets", "cls": "s3", "values": [r["assets"] for r in rows]},
                {"name": "Debts", "cls": "s2", "values": [r["debts"] for r in rows]}]))

        elif report == "merchants":
            if payee:
                d = reports.merchant_detail(c, s, e, payee, acct)
                labels = [_mlabel(r["month"]) for r in d["trend"]]
                folded = charts.fold([dict(i) for i in d["categories"]])
                ctx.update(detail=d, chart=charts.columns(labels, [{"name": payee, "cls": "s1",
                                                                    "values": [r["value"] for r in d["trend"]]}]),
                           cat_chart=charts.donut(folded, size=220) if len(folded) > 1 else None, legend_items=folded)
            else:
                cid = ctx["category"] if ctx["category"] in tree.by_id else None
                items = reports.merchants(c, s, e, acct, cid)
                total = sum(i["value"] for i in items)
                folded = charts.fold([dict(i) for i in items])
                ctx.update(items=items, total=total, legend_items=folded,
                           chart=charts.donut(folded) if view == "pie" else charts.hbars(items))
    return render(request, "reports.html", **ctx)


# ---------------------------------------------------------------- rules & categories

@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, pattern: str = "", category_id: str | None = None, applied: str | None = None,
               saved: str | None = None, error: str | None = None, edit: str | None = None):
    with conn() as c:
        tree = cats.load(c)
        rules = rules_mod.load(c)
        names = dict(c.execute("SELECT id, name FROM accounts").fetchall())
        accounts = c.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        matches = rules_mod.match_counts(c)
    for r in rules:
        r["account_name"] = names.get(r["account_id"])
        r["summary"] = [rules_mod.describe(x) for x in r["conds"]]
        r["text_conds"] = [x for x in r["conds"] if x["field"] in rules_mod.TEXT_FIELDS]
        r["amount_cond"] = next((x for x in r["conds"] if x["field"] == "amount"), None)
        r["direction"] = next((x["value"] for x in r["conds"] if x["field"] == "direction"), "")
    return render(request, "rules.html", rules=rules, cat_list=tree.ordered(), tree=tree, accounts=accounts,
                  prefill={"pattern": pattern, "category_id": _int(category_id)}, applied=_int(applied),
                  saved=saved, error=error, matches=matches, edit=_int(edit),
                  text_fields=[(k, rules_mod.FIELD_LABELS[k]) for k in rules_mod.TEXT_FIELDS],
                  text_ops=rules_mod.TEXT_OPS, amount_ops=rules_mod.AMOUNT_OPS)


def _conds_from_form(form) -> list[dict]:
    """Text rows (t_field/t_op/t_value, repeatable) + one amount test + one direction test, all ANDed."""
    from .normalize import parse_amount_cents
    conds = []
    if form.get("pattern"):  # older single-condition form fields
        conds.append({"field": "text", "op": form.get("match_type") or "contains", "value": form.get("pattern")})
    for f, op, v in zip(form.getlist("t_field"), form.getlist("t_op"), form.getlist("t_value")):
        conds.append({"field": f, "op": op, "value": v})
    a_op, a_v, a_v2 = form.get("amount_op") or "", (form.get("amount_v") or "").strip(), (form.get("amount_v2") or "").strip()
    if a_v:  # an amount typed with the test left on "any amount" means "equals"
        a_op = a_op or "eq"
        try:
            v1 = parse_amount_cents(a_v)
            v2 = parse_amount_cents(a_v2) if a_v2 else None
        except ValueError:
            raise rules_mod.RuleError("Amounts must be numbers, e.g. 100 or 49.99.")
        conds.append({"field": "amount", "op": a_op, "value": v1, "value2": v2})
    if form.get("direction") in ("in", "out"):
        conds.append({"field": "direction", "op": "is", "value": form.get("direction")})
    return conds


async def _save_rule(request: Request, rule_id: int | None):
    from urllib.parse import quote
    form = await request.form()
    try:
        with conn() as c:
            rules_mod.save(c, rule_id, _conds_from_form(form), _int(form.get("category_id")),
                           (form.get("rename_to") or "").strip() or None, _int(form.get("account_id")),
                           100 if _int(form.get("priority")) is None else _int(form.get("priority")),
                           set_excluded=form.get("set_excluded") == "on")
            reapply = form.get("reapply") or ("uncategorized" if form.get("apply_now") else "")
            n = (rules_mod.apply_all(c, only_uncategorized=(reapply == "uncategorized"),
                                     include_hand_set=(reapply == "force")) if reapply else None)
    except rules_mod.RuleError as e:
        back_to = f"/rules?error={quote(str(e))}" + (f"&edit={rule_id}" if rule_id else "")
        return RedirectResponse(back_to, status_code=303)
    return RedirectResponse(f"/rules?applied={n}" if n is not None else "/rules?saved=1", status_code=303)


@app.post("/rules")
async def create_rule(request: Request):
    return await _save_rule(request, None)


@app.post("/rules/{rule_id:int}")
async def update_rule(request: Request, rule_id: int):
    return await _save_rule(request, rule_id)


@app.post("/rules/{rule_id:int}/delete")
def delete_rule(rule_id: int):
    with conn() as c:
        c.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return RedirectResponse("/rules", status_code=303)


@app.post("/rules/apply")
def apply_rules(scope: str = Form("uncategorized")):
    with conn() as c:
        n = rules_mod.apply_all(c, only_uncategorized=(scope == "uncategorized"), include_hand_set=(scope == "force"))
    return RedirectResponse(f"/rules?applied={n}", status_code=303)


@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, error: str | None = None, msg: str | None = None):
    with conn() as c:
        tree = cats.load(c)
        counts = dict(c.execute("SELECT category_id, COUNT(*) FROM tx_lines GROUP BY category_id").fetchall())
    rows = []
    for cat in tree.ordered():
        sub = sum(counts.get(i, 0) for i in tree.descendants(cat.id))
        # Valid destinations: top level, or any category shallow enough, not inside this one.
        own = set(tree.descendants(cat.id))
        h = tree.height(cat.id)
        dests = [d for d in tree.ordered() if d.id not in own and d.depth + h <= cats.MAX_DEPTH]
        rows.append({"cat": cat, "n": counts.get(cat.id, 0), "n_all": sub, "dests": dests})
    return render(request, "categories.html", rows=rows, cat_list=tree.ordered(), max_depth=cats.MAX_DEPTH,
                  error=error, msg=msg)


def _cat_action(fn, ok_msg: str):
    from urllib.parse import quote
    try:
        with conn() as c:
            fn(c)
    except cats.CategoryError as e:
        return RedirectResponse(f"/categories?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/categories?msg={quote(ok_msg)}", status_code=303)


@app.post("/categories")
def create_category(name: str = Form(...), kind: str = Form("expense"), parent_id: str = Form("")):
    return _cat_action(lambda c: cats.create(c, name, kind, _int(parent_id)), f"Added “{name.strip()}”.")


@app.post("/categories/{cat_id:int}/rename")
def rename_category(cat_id: int, name: str = Form(...)):
    return _cat_action(lambda c: cats.rename(c, cat_id, name), f"Renamed to “{name.strip()}”.")


@app.post("/categories/{cat_id:int}/move")
def move_category(cat_id: int, parent_id: str = Form(""), kind: str = Form("")):
    return _cat_action(lambda c: cats.move(c, cat_id, _int(parent_id), kind or None), "Moved.")


@app.post("/categories/{cat_id:int}/exclude")
def exclude_category(cat_id: int, excluded: str = Form(None)):
    with conn() as c:
        c.execute("UPDATE categories SET excluded = ? WHERE id = ?", (1 if excluded else 0, cat_id))
    return RedirectResponse(f"/categories?msg={'Excluded from' if excluded else 'Counted in'}+reports+and+budgets"
                            f"#cat{cat_id}", status_code=303)


@app.post("/categories/{cat_id:int}/delete")
def delete_category(cat_id: int):
    return _cat_action(lambda c: cats.delete(c, cat_id), "Deleted.")


# ---------------------------------------------------------------- organisations

def _org_cookie(resp: RedirectResponse, org_id: int) -> RedirectResponse:
    # Strict same-site, not readable by page scripts; it only says which organisation is open.
    resp.set_cookie(ORG_COOKIE, str(org_id), max_age=10 * 365 * 24 * 3600, httponly=True, samesite="strict",
                    path="/")
    return resp


@app.get("/orgs", response_class=HTMLResponse)
def orgs_page(request: Request, error: str | None = None, msg: str | None = None):
    rows = [{"org": o, **orgs.summary(o)} for o in orgs.all_orgs()]
    return render(request, "orgs.html", rows=rows, error=error, msg=msg)


@app.post("/orgs")
def create_org(name: str = Form(...)):
    from urllib.parse import quote
    try:
        org = orgs.create(name)
    except orgs.OrgError as e:
        return RedirectResponse(f"/orgs?error={quote(str(e))}", status_code=303)
    # Open the new organisation straight away, at its Accounts page.
    return _org_cookie(RedirectResponse("/accounts?msg=" + quote(f"Created “{org['name']}”. Add its accounts here."),
                                        status_code=303), org["id"])


@app.post("/orgs/switch")
def switch_org(request: Request, org_id: str = Form(...)):
    org = orgs.get(org_id)
    if not org:
        raise HTTPException(404)
    # Stay on the same kind of page, but drop ids that belong to the old organisation.
    ref = request.headers.get("referer") or ""
    m = re.match(r"^https?://[^/]+(/[a-z]*)", ref)
    page = m.group(1) if m and m.group(1) in ("/", "/transactions", "/budgets", "/reports", "/import", "/rules",
                                               "/categories", "/accounts", "/orgs") else "/"
    return _org_cookie(RedirectResponse(page, status_code=303), org["id"])


@app.post("/orgs/{org_id:int}/rename")
def rename_org(org_id: int, name: str = Form(...)):
    from urllib.parse import quote
    try:
        orgs.rename(org_id, name)
    except orgs.OrgError as e:
        return RedirectResponse(f"/orgs?error={quote(str(e))}", status_code=303)
    return RedirectResponse("/orgs?msg=" + quote("Renamed."), status_code=303)


@app.post("/orgs/{org_id:int}/delete")
def delete_org(request: Request, org_id: int, confirm_name: str = Form("")):
    from urllib.parse import quote
    try:
        dest = orgs.delete(org_id, confirm_name)
    except orgs.OrgError as e:
        return RedirectResponse(f"/orgs?error={quote(str(e))}", status_code=303)
    resp = RedirectResponse("/orgs?msg=" + quote(f"Removed. Its database file was moved to data/deleted/{dest.name} "
                                                  "in case you need it back."), status_code=303)
    if request.state.org["id"] == org_id:
        resp = _org_cookie(resp, orgs.all_orgs()[0]["id"])
    return resp


# ---------------------------------------------------------------- credit card rewards

def _cents(v) -> int | None:
    from .normalize import parse_amount_cents
    v = (v or "").strip()
    if not v:
        return None
    try:
        return abs(parse_amount_cents(v))
    except ValueError:
        raise rewards.RewardsError(f"“{v}” isn't an amount.")


def _float(v, name) -> float:
    try:
        return float(str(v).strip().rstrip("%x"))
    except ValueError:
        raise rewards.RewardsError(f"{name} must be a number.")


def _rw_redirect(account_id: int, msg: str = "", error: str = "", anchor: str = ""):
    from urllib.parse import quote
    q = f"?msg={quote(msg)}" if msg else (f"?error={quote(error)}" if error else "")
    return RedirectResponse(f"/accounts/{account_id}/rewards{q}{anchor}", status_code=303)


@app.get("/accounts/{account_id:int}/rewards", response_class=HTMLResponse)
def card_setup(request: Request, account_id: int, msg: str | None = None, error: str | None = None):
    with conn() as c:
        acct = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acct:
            raise HTTPException(404)
        prog = c.execute("SELECT * FROM card_programs WHERE account_id = ?", (account_id,)).fetchone()
        rules = c.execute("SELECT * FROM earn_rules WHERE account_id = ? ORDER BY rate DESC, id", (account_id,)).fetchall()
        tree = cats.load(c)
        suggestion = None
        if not prog:
            hay = f"{acct['name']} {acct['institution'] or ''}".lower()
            for card in rewards.catalog()["cards"]:
                words = [w for w in card["name"].lower().replace("(", " ").replace(")", " ").split() if len(w) > 3]
                if words and sum(w in hay for w in words) >= 2:
                    suggestion = card["key"]
                    break
    rule_rows = []
    for r in rules:
        ids = {int(x) for x in (r["category_ids"] or "").split(",") if x.strip()}
        rule_rows.append({"r": r, "cat_ids": ids, "cat_names": [tree.by_id[i].path for i in ids if i in tree.by_id]})
    return render(request, "card_setup.html", acct=acct, prog=prog, rules=rule_rows, groups=rewards.catalog_groups(),
                  cat_list=tree.ordered(), msg=msg, error=error, suggestion=suggestion, networks=rewards.NETWORKS,
                  units=rewards.UNITS, catalog=rewards.catalog())


@app.post("/accounts/{account_id:int}/rewards/apply")
def card_apply(account_id: int, catalog_key: str = Form(...)):
    try:
        with conn() as c:
            res = rewards.apply_profile(c, account_id, catalog_key)
    except rewards.RewardsError as e:
        return _rw_redirect(account_id, error=str(e))
    msg = f"Set up as {res['card']['name']}."
    if res["unmapped"]:
        msg += f" Map these to your categories on the rules below: {', '.join(res['unmapped'])}."
    return _rw_redirect(account_id, msg=msg)


@app.post("/accounts/{account_id:int}/rewards")
async def card_settings(request: Request, account_id: int):
    form = await request.form()
    try:
        name = (form.get("card_name") or "").strip() or "Card"
        network = form.get("network") if form.get("network") in rewards.NETWORKS else "Other"
        unit = form.get("unit") if form.get("unit") in rewards.UNITS else "cash"
        cpp = _float(form.get("cpp") or "1", "Value per point")
        base = _float(form.get("base_rate") or "1", "Base rate")
        fee = _cents(form.get("annual_fee")) or 0
        with conn() as c:
            if c.execute("SELECT type FROM accounts WHERE id = ?", (account_id,)).fetchone()["type"] != "credit":
                raise rewards.RewardsError("Rewards can only be set up on credit accounts.")
            c.execute("""INSERT INTO card_programs(account_id, card_name, network, unit, cpp, base_rate, annual_fee_cents)
                         VALUES (?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET card_name=excluded.card_name,
                         network=excluded.network, unit=excluded.unit, cpp=excluded.cpp, base_rate=excluded.base_rate,
                         annual_fee_cents=excluded.annual_fee_cents""", (account_id, name, network, unit, cpp, base, fee))
    except rewards.RewardsError as e:
        return _rw_redirect(account_id, error=str(e))
    return _rw_redirect(account_id, msg="Card settings saved.")


@app.post("/accounts/{account_id:int}/rewards/remove")
def card_remove(account_id: int):
    with conn() as c:
        c.execute("DELETE FROM card_programs WHERE account_id = ?", (account_id,))
    return _rw_redirect(account_id, msg="Rewards removed from this account.")


def _rule_values(form) -> tuple:
    label = (form.get("label") or "").strip() or "Bonus"
    rate = _float(form.get("rate") or "", "Rate")
    ids = [str(_int(v)) for v in form.getlist("category_ids") if _int(v)]
    merchants = "|".join(m.strip() for m in (form.get("merchants") or "").replace(",", "|").split("|") if m.strip())
    exclude = "|".join(m.strip() for m in (form.get("exclude") or "").replace(",", "|").split("|") if m.strip())
    if not ids and not merchants:
        raise rewards.RewardsError("A bonus rule needs at least one category or merchant.")
    cap = _cents(form.get("cap"))
    period = form.get("cap_period") if form.get("cap_period") in ("month", "quarter", "year") else None
    if cap and not period:
        raise rewards.RewardsError("Pick whether the cap is per month, quarter or year.")
    group = (form.get("cap_group") or "").strip() or None
    start = form.get("start_date") or None
    end = form.get("end_date") or None
    for d in (start, end):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise rewards.RewardsError("Dates must be yyyy-mm-dd.")
    needs = 1 if form.get("needs_activation") else 0
    activated = 1 if (form.get("activated") or not needs) else 0
    return (label, rate, ",".join(ids) or None, merchants or None, exclude or None, cap if period else None,
            period if cap else None, group, start, end, needs, activated)


@app.post("/accounts/{account_id:int}/rewards/rules")
async def card_rule_add(request: Request, account_id: int):
    form = await request.form()
    try:
        vals = _rule_values(form)
        with conn() as c:
            if not c.execute("SELECT 1 FROM card_programs WHERE account_id = ?", (account_id,)).fetchone():
                raise rewards.RewardsError("Pick a card or save card settings first.")
            c.execute("""INSERT INTO earn_rules(label, rate, category_ids, merchants, exclude, cap_cents, cap_period,
                         cap_group, start_date, end_date, needs_activation, activated, account_id)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*vals, account_id))
    except rewards.RewardsError as e:
        return _rw_redirect(account_id, error=str(e))
    return _rw_redirect(account_id, msg="Bonus rule added.")


@app.post("/rewards/rules/{rule_id:int}")
async def card_rule_update(request: Request, rule_id: int):
    form = await request.form()
    with conn() as c:
        row = c.execute("SELECT account_id FROM earn_rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    try:
        vals = _rule_values(form)
        with conn() as c:
            c.execute("""UPDATE earn_rules SET label=?, rate=?, category_ids=?, merchants=?, exclude=?, cap_cents=?,
                         cap_period=?, cap_group=?, start_date=?, end_date=?, needs_activation=?, activated=?,
                         unmapped=NULL WHERE id=?""", (*vals, rule_id))
    except rewards.RewardsError as e:
        return _rw_redirect(row["account_id"], error=str(e), anchor=f"#rule{rule_id}")
    return _rw_redirect(row["account_id"], msg="Rule saved.", anchor=f"#rule{rule_id}")


@app.post("/rewards/rules/{rule_id:int}/delete")
def card_rule_delete(rule_id: int):
    with conn() as c:
        row = c.execute("SELECT account_id FROM earn_rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        c.execute("DELETE FROM earn_rules WHERE id = ?", (rule_id,))
    return _rw_redirect(row["account_id"], msg="Rule deleted.")


@app.post("/rewards/rules/{rule_id:int}/activate")
def card_rule_activate(request: Request, rule_id: int):
    with conn() as c:
        c.execute("UPDATE earn_rules SET activated = 1 WHERE id = ?", (rule_id,))
    return back(request, "/rewards")


@app.get("/rewards", response_class=HTMLResponse)
def rewards_page(request: Request, period: str = "ytd", start: str | None = None, end: str | None = None):
    with conn() as c:
        s, e = reports.period_range(period, start, end)
        if period == "all":
            b = reports.data_bounds(c)
            s, e = b if b else (date.today().replace(day=1).isoformat(), date.today().isoformat())
        e = min(e, date.today().isoformat()) if e > date.today().isoformat() and s <= date.today().isoformat() else e
        summ = rewards.summarize(c, s, e)
        recs = rewards.recommendations(c)
        alerts = rewards.alerts(c)
        credit_accounts = c.execute("""SELECT a.id, a.name, cp.card_name FROM accounts a
                                       LEFT JOIN card_programs cp ON cp.account_id = a.id
                                       WHERE a.type = 'credit' ORDER BY a.name""").fetchall()
        pending = c.execute("""SELECT r.id, r.label, a.name FROM earn_rules r JOIN accounts a ON a.id = r.account_id
                               WHERE r.needs_activation = 1 AND r.activated = 0
                                 AND (r.end_date IS NULL OR r.end_date >= ?)""", (date.today().isoformat(),)).fetchall()
    months = reports.months_between(s, e)
    progs = sorted(summ["programs"].values(), key=lambda p: p.account_name)
    cls = [f"s{i + 1}" if i < 7 else "s-other" for i in range(len(progs))]
    chart = charts.columns([_mlabel(m) for m in months], [
        {"name": p.account_name, "cls": cls[i], "values": [int(summ["monthly"].get(m, {}).get(p.account_id, 0)) for m in months]}
        for i, p in enumerate(progs)]) if progs and months else None
    legend_items = [{"cls": cls[i], "label": p.account_name} for i, p in enumerate(progs)]
    return render(request, "rewards.html", summ=summ, recs=recs, alerts=alerts, credit_accounts=credit_accounts,
                  pending=pending, period=period, periods=reports.PERIODS, start=s, end=e, chart=chart,
                  legend_items=legend_items)

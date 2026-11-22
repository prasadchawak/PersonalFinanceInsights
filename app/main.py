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

from . import budgets as budgets_mod, categories as cats, charts, db, reports, rules as rules_mod, services, splits
from .importers import csv_import, ofx
from .normalize import DATE_FORMATS, header_fingerprint

HERE = Path(__file__).resolve().parent
STAGING = db.DATA_DIR / "staging"
MAX_UPLOAD = 20 * 1024 * 1024
PAGE_SIZE = 100


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.adopt_legacy_db()
    with db.connect() as conn:
        db.init(conn)
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
    resp = await call_next(request)
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
        uncategorized = c.execute("SELECT COUNT(DISTINCT tx_id) FROM tx_lines WHERE category_id IS NULL").fetchone()[0]
        budget = budgets_mod.overview(c, month)
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
                  uncategorized=uncategorized, budget=budget, assets=assets, debts=debts, spending=spending,
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
    return render(request, "accounts.html", accounts=accounts, types=ACCOUNT_TYPES, error=error, msg=msg)


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


@app.post("/accounts/{account_id}")
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


@app.post("/accounts/{account_id}/opening")
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
                                                       "sha256": services.sha256(data)}))
    return RedirectResponse(f"/import/map/{token}", status_code=303)


def _staged(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", token):
        raise HTTPException(404)
    p = STAGING / f"{token}.csv"
    if not p.exists():
        raise HTTPException(404, "That upload expired; please upload it again")
    meta = json.loads((STAGING / f"{token}.json").read_text())
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


@app.post("/imports/{import_id}/undo")
def undo(import_id: int):
    with conn() as c:
        services.undo_import(c, import_id)
    return RedirectResponse("/import", status_code=303)


@app.post("/profiles/{profile_id}/delete")
def delete_profile(profile_id: int):
    with conn() as c:
        c.execute("DELETE FROM import_profiles WHERE id = ?", (profile_id,))
    return RedirectResponse("/import", status_code=303)


# ---------------------------------------------------------------- transactions


def _tx_filters(conn_, account, category, month, q, start=None, end=None):
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
        where.append("(t.payee LIKE ? OR t.payee_raw LIKE ? OR t.memo LIKE ? OR t.notes LIKE ?)")
        args += [f"%{q}%"] * 4
    return (" WHERE " + " AND ".join(where)) if where else "", args


@app.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, account: str | None = None, category: str | None = None,
                 month: str | None = None, q: str | None = None, start: str | None = None,
                 end: str | None = None, page: str | None = None, updated: str | None = None):
    page = max(_int(page) or 1, 1)
    with conn() as c:
        tree = cats.load(c)
        where, args = _tx_filters(c, account, category, month, q, start, end)
        total, total_cents = c.execute(
            f"SELECT COUNT(*), COALESCE(SUM(t.amount_cents),0) FROM transactions t{where}", args).fetchone()
        txns = c.execute(f"""SELECT t.*, a.name AS account_name
                             FROM transactions t JOIN accounts a ON a.id = t.account_id{where}
                             ORDER BY t.posted_date DESC, t.id DESC LIMIT ? OFFSET ?""",
                         (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
        accounts = c.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        split_map = splits.for_transactions(c, [t["id"] for t in txns])
    f = {"account": _int(account), "category": category or "", "month": month or "", "q": q or "",
         "start": start or "", "end": end or ""}
    return render(request, "transactions.html", txns=txns, accounts=accounts, cat_list=tree.ordered(),
                  tree=tree, f=f, total=total, total_cents=total_cents, page=page,
                  pages=max(1, -(-total // PAGE_SIZE)), updated=_int(updated), split_map=split_map)


@app.post("/transactions/bulk")
async def bulk_update(request: Request):
    """Set one category on many transactions: the ticked ones, or everything matching the current filter."""
    form = await request.form()
    category_id = _int(form.get("category_id"))
    clear = form.get("category_id") == "none"
    if category_id is None and not clear:
        return back(request, "/transactions")
    with conn() as c:
        if form.get("scope") == "filter":
            where, args = _tx_filters(c, form.get("f_account"), form.get("f_category"), form.get("f_month"),
                                      form.get("f_q"), form.get("f_start"), form.get("f_end"))
            ids = [r[0] for r in c.execute(f"SELECT t.id FROM transactions t{where}", args)]
        else:
            ids = [i for i in (_int(v) for v in form.getlist("ids")) if i]
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            ph = ",".join("?" * len(part))
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


@app.post("/transactions/{tx_id}")
def update_tx(request: Request, tx_id: int, category_id: str = Form(""), payee: str = Form(None),
              notes: str = Form(None), details_only: str = Form(None)):
    with conn() as c:
        if details_only:
            pass
        else:
            splits.remove(c, tx_id, _int(category_id))  # picking one category replaces any split
        if payee is not None and payee.strip():
            c.execute("UPDATE transactions SET payee = ? WHERE id = ?", (payee.strip(), tx_id))
        if notes is not None:
            c.execute("UPDATE transactions SET notes = ? WHERE id = ?", (notes.strip() or None, tx_id))
    return back(request, "/transactions")


@app.get("/export.csv")
def export(account: str | None = None, category: str | None = None, month: str | None = None,
           q: str | None = None, start: str | None = None, end: str | None = None):
    """One row per line: split transactions export one row per split, marked in the 'split' column."""
    with conn() as c:
        tree = cats.load(c)
        where, args = _tx_filters(c, account, category, month, q, start, end)
        rows = c.execute(f"""SELECT t.posted_date, a.name, t.payee, t.payee_raw, t.memo, l.amount_cents,
                                    l.category_id, COALESCE(t.notes,''), l.is_split, t.amount_cents
                             FROM transactions t JOIN accounts a ON a.id = t.account_id
                             JOIN tx_lines l ON l.tx_id = t.id{where}
                             ORDER BY t.posted_date, t.id""", args).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "account", "payee", "original_description", "memo", "amount", "category", "notes",
                "split", "transaction_total"])
    for r in rows:
        cat = tree.by_id[r[6]].path if r[6] in tree.by_id else ""
        w.writerow([r[0], r[1], r[2], r[3], r[4] or "", f"{r[5] / 100:.2f}", cat, r[7], "yes" if r[8] else "",
                    f"{r[9] / 100:.2f}"])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=pfi-transactions.csv"})


# ---------------------------------------------------------------- splits

@app.get("/transactions/{tx_id}/split", response_class=HTMLResponse)
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


@app.post("/transactions/{tx_id}/split", response_class=HTMLResponse)
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


@app.post("/transactions/{tx_id}/unsplit")
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
                  next_m=next_m, msg=msg, label=_mlabel(month))


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
               "parents": [x for x in tree.ordered() if x.children and x.depth < cats.MAX_DEPTH]}

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
               saved: str | None = None, error: str | None = None):
    with conn() as c:
        tree = cats.load(c)
        rules = c.execute("""SELECT r.*, a.name AS account_name FROM rules r
                             LEFT JOIN accounts a ON a.id = r.account_id
                             ORDER BY r.priority, r.id""").fetchall()
        accounts = c.execute("SELECT id, name FROM accounts ORDER BY name").fetchall()
        c_rules = rules_mod.match_counts(c)
    return render(request, "rules.html", rules=rules, cat_list=tree.ordered(), tree=tree, accounts=accounts,
                  prefill={"pattern": pattern, "category_id": _int(category_id)}, applied=_int(applied),
                  saved=saved, error=error, matches=c_rules)


@app.post("/rules")
def create_rule(match_type: str = Form("contains"), pattern: str = Form(...), category_id: str = Form(""),
                rename_to: str = Form(""), account_id: str = Form(""), priority: str = Form("100"),
                apply_now: str = Form(None)):
    if match_type not in ("contains", "starts_with", "equals", "regex"):
        raise HTTPException(400)
    if not _int(category_id) and not rename_to.strip():
        raise HTTPException(400, "A rule needs a category or a rename")
    with conn() as c:
        c.execute("INSERT INTO rules(priority, match_type, pattern, account_id, category_id, rename_to)"
                  " VALUES (?,?,?,?,?,?)",
                  (_int(priority) or 100, match_type, pattern.strip(), _int(account_id), _int(category_id),
                   rename_to.strip() or None))
        n = rules_mod.apply_all(c) if apply_now else None
    return RedirectResponse(f"/rules?applied={n}" if n is not None else "/rules", status_code=303)


@app.post("/rules/{rule_id}")
def update_rule(rule_id: int, match_type: str = Form("contains"), pattern: str = Form(...),
                category_id: str = Form(""), rename_to: str = Form(""), account_id: str = Form(""),
                priority: str = Form("100"), reapply: str = Form(None)):
    if match_type not in ("contains", "starts_with", "equals", "regex"):
        raise HTTPException(400)
    if not _int(category_id) and not rename_to.strip():
        raise HTTPException(400, "A rule needs a category or a rename")
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error:
            return RedirectResponse("/rules?error=That+regex+isn%27t+valid", status_code=303)
    with conn() as c:
        c.execute("UPDATE rules SET match_type=?, pattern=?, category_id=?, rename_to=?, account_id=?, priority=?"
                  " WHERE id=?", (match_type, pattern.strip(), _int(category_id), rename_to.strip() or None,
                                  _int(account_id), _int(priority) or 100, rule_id))
        n = rules_mod.apply_all(c, only_uncategorized=(reapply != "all")) if reapply else None
    return RedirectResponse(f"/rules?applied={n}" if n is not None else "/rules?saved=1", status_code=303)


@app.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int):
    with conn() as c:
        c.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return RedirectResponse("/rules", status_code=303)


@app.post("/rules/apply")
def apply_rules(scope: str = Form("uncategorized")):
    with conn() as c:
        n = rules_mod.apply_all(c, only_uncategorized=(scope != "all"))
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


@app.post("/categories/{cat_id}/rename")
def rename_category(cat_id: int, name: str = Form(...)):
    return _cat_action(lambda c: cats.rename(c, cat_id, name), f"Renamed to “{name.strip()}”.")


@app.post("/categories/{cat_id}/move")
def move_category(cat_id: int, parent_id: str = Form(""), kind: str = Form("")):
    return _cat_action(lambda c: cats.move(c, cat_id, _int(parent_id), kind or None), "Moved.")


@app.post("/categories/{cat_id}/delete")
def delete_category(cat_id: int):
    return _cat_action(lambda c: cats.delete(c, cat_id), "Deleted.")

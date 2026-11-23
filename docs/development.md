# Development guide

Everything you need to change Personal Finance Insights safely. Read [architecture.md](architecture.md) first for how the pieces fit; this page is the how-to. If you are an AI assistant, also read [CLAUDE.md](../CLAUDE.md).

**Contents:** [Set up](#set-up) · [Run while developing](#run-while-developing) · [Tests](#tests) · [Project layout](#project-layout) · [Conventions](#conventions) · [Recipes](#recipes) · [Changing the database](#changing-the-database) · [Demo data and screenshots](#demo-data-and-screenshots) · [Git workflow](#git-workflow) · [Checklist before you commit](#checklist-before-you-commit)

## Set up

You need Python 3.10+ (3.11+ recommended) and git.

**macOS / Linux**
```sh
git clone https://github.com/prasadchawak/PersonalFinanceInsights.git
cd PersonalFinanceInsights
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

**Windows**
```
git clone https://github.com/prasadchawak/PersonalFinanceInsights.git
cd PersonalFinanceInsights
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
```

`requirements.txt` is what the app needs (FastAPI, Uvicorn, Jinja2, python-multipart). `requirements-dev.txt` adds pytest and httpx for the tests. Keep runtime dependencies to that list: see [Conventions](#conventions).

## Run while developing

Use a **separate data folder** so you never touch your real data, and Uvicorn's auto-reload so code changes apply on save:

```sh
.venv/bin/python tools/make_demo.py demo-data          # once: made-up data to look at
PFI_DATA_DIR=demo-data .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
```

Windows:
```
.venv\Scripts\python tools\make_demo.py demo-data
set PFI_DATA_DIR=demo-data
.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
```

Always pass `--host 127.0.0.1`. Templates and static files are read from disk, so a browser refresh shows template and CSS changes.

## Tests

```sh
.venv/bin/python -m pytest -q            # everything
.venv/bin/python -m pytest -q tests/test_core.py -k csv   # a subset
```

- Every test gets a **fresh temporary data folder**: the `client` fixture in `tests/test_core.py` sets `PFI_DATA_DIR` and `PFI_DB`, reloads the app modules, and gives you a FastAPI `TestClient`. Tests never touch `data/`.
- Helpers you can import from `test_core`: `make_account(client, name, type_, opening)`, `upload(client, account_id, path)`, `cat_id("Groceries")`, `SAMPLES` (the `tests/samples/` folder). `test_phase1b` adds `load_ofx`, `load_checking` and `tx_ids`.
- Test through the **HTTP routes** where you can (that's what users do), and call domain functions directly for edge cases.
- **All test data is made up.** Never commit a real statement, even with names changed.
- On Linux you can prove the app makes no network calls by running the tests with networking switched off: `unshare -rn .venv/bin/python -m pytest -q`.

| File | Covers |
|---|---|
| `tests/test_core.py` | Parsing, CSV mapping and saved layouts, rules on import, locks, undo, security middleware, legacy upgrade |
| `tests/test_phase1b.py` | Category tree, depth limit, v1 migration, bulk edit, reports |
| `tests/test_phase1c.py` | Splits and unsplit, budgets and roll-up, merchants, subcategory reports, account rename, editing and re-applying rules |
| `tests/test_phase1d.py` | Account delete, sorting, multi-condition (AND) rules, amount conditions and filters, rule priority and *taken by earlier rule*, re-apply options |
| `tests/test_phase1e.py` | Organisations (isolation, switching, rename/remove), backups, excluding from reports and budgets, upgrading old databases |
| `tests/test_rewards.py` | Catalog consistency, caps, shared caps, refunds, rotating categories, network acceptance, best card, alerts |

## Project layout

```
PersonalFinanceInsights/
├── app/
│   ├── __main__.py          entry point: python -m app
│   ├── main.py              FastAPI app: middleware + every route
│   ├── db.py                settings, connection, schema, DEFAULT_CATEGORIES, views, migrations
│   ├── orgs.py              organisation registry (data/orgs.db)
│   ├── normalize.py         dates, amounts, payee cleanup, fingerprints
│   ├── importers/           ofx.py, csv_import.py → ParsedTxn
│   ├── services.py          import pipeline, balances, month summary
│   ├── rules.py             categorisation rules
│   ├── categories.py        3-level category tree
│   ├── splits.py            split transactions
│   ├── budgets.py           monthly budgets
│   ├── reports.py           report queries
│   ├── charts.py            SVG charts
│   ├── rewards.py           credit card rewards engine
│   ├── data/card_catalog.json   GENERATED: do not edit by hand
│   ├── backup.py            python -m app.backup
│   ├── templates/           Jinja2 pages (base.html = layout + nav)
│   └── static/              style.css, app.js
├── tests/                   pytest suite + samples/ (made-up files)
├── tools/
│   ├── make_demo.py         build a demo data folder
│   ├── build_card_catalog.py    source of the card catalog
│   └── take_screenshots.py  regenerate docs/screenshots
├── docs/                    these documents + screenshots/
├── run.bat / run.sh         start the app (creates .venv on first run)
├── backup.bat / backup.sh   back up every organisation
└── CLAUDE.md                brief for AI assistants
```

`data/`, `.venv/`, `demo-data/` and every `*.db` file are git-ignored. `app/data/` is **not** ignored.

## Conventions

**Non-negotiable** (from [architecture.md → Goals](architecture.md#goals-and-constraints)):
- **No network.** Never add an HTTP client, a call to an outside service, analytics, a CDN link, a web font, or an external image. `grep -rnE "https?://" app` must find only `127.0.0.1`/`localhost`.
- **Loopback only.** Never change the `127.0.0.1` bind or loosen the middleware's host/CSRF checks or the CSP.
- **No new runtime dependency** without a strong reason; prefer the standard library.
- **Money is integer cents.** Parse with `normalize.parse_amount_cents`, display with the `money` Jinja filter. Never store floats for money.
- **Sign:** negative = money out, positive = money in. Where users type amounts (rules, filters, splits) they type positive numbers and choose a direction.
- **Dates** are ISO strings `yyyy-mm-dd` (months `yyyy-mm`), which sort and compare correctly as text.

**Code**
- Domain logic lives in a domain module (`rules.py`, `budgets.py`…) taking `conn` as its first argument; `main.py` only parses the request, calls it, and renders or redirects. Raise a module's own error (`RuleError`, `CategoryError`, `OrgError`, `RewardsError`) for user mistakes; the route turns it into a message.
- SQL uses `?` parameters. Only fixed, whitelisted fragments (sort columns, operators) are inserted with f-strings.
- Reports and budgets read the **`report_lines`** view; category filters read **`tx_lines`**; balances read **`transactions`**. Don't re-implement split or exclusion logic in a query.
- Routes with ids use converters (`/rules/{rule_id:int}`) so `/rules/apply` can't be captured as an id.
- Optional numeric query/form values go through `_int()` (empty string → `None`).
- After a successful POST, redirect with **303** (POST → redirect → GET). Pass a short message with `?msg=` or `?error=`.
- Use `with conn() as c:` in routes; it commits on success.
- Plain HTML forms; JavaScript only as progressive enhancement in `app/static/app.js` (no inline scripts; the CSP forbids them).
- Charts: use `charts.py`; at most 8 series/slices (7 + "Other"); always show a table with the same numbers.
- Wording on pages: plain and short, sentence case, US English for money words, explain what a destructive action will do before it happens.

## Recipes

### Add a page

1. **Route** in `app/main.py`, near related routes:
   ```python
   @app.get("/savings-goals", response_class=HTMLResponse)
   def goals_page(request: Request, msg: str | None = None):
       with conn() as c:
           rows = goals.load(c)                # logic lives in app/goals.py
       return render(request, "goals.html", rows=rows, msg=msg)
   ```
2. **Template** `app/templates/goals.html` extending `base.html`:
   ```jinja
   {% extends "base.html" %}
   {% block title %}Savings goals{% endblock %}
   {% block content %}<h1>Savings goals</h1> … {% endblock %}
   ```
3. **Navigation:** add a link in `app/templates/base.html` (keep the bar to one line; it has 9 items now).
4. **POST handlers** validate input, call the domain module, then `RedirectResponse("/savings-goals?msg=Saved", status_code=303)`.
5. **Tests:** at least "page renders" and one POST that changes data. Add the URL to `test_pages_render` in `tests/test_core.py`.
6. **Docs:** a section in [user-guide.md](user-guide.md) (with a screenshot) and [reference.md](reference.md).

### Add a report

1. Query function in `app/reports.py` taking `(conn, start, end, account=None, …)`, reading `report_lines`.
2. Add `(key, "Title")` to `REPORTS` in `main.py` and a branch in `reports_page` that builds the chart with `charts.py` and the table rows.
3. Add the report to the parametrised `test_reports_render` in `tests/test_phase1b.py`, plus a numbers test.

### Add a rule condition

1. Add the field to the `CHECK` constraint on `rule_conditions.field` in `db.SCHEMA` **and** handle existing databases (see [Changing the database](#changing-the-database): a CHECK change needs a table rebuild in `_migrate`).
2. Handle it in `rules.cond_matches`, `rules.validate_conditions`, `rules.describe`, `FIELD_LABELS`.
3. Add it to the form in `rules.html` and to `_conds_from_form` in `main.py`.
4. Test matching, validation and the form.

### Add an import format

Write `app/importers/<format>.py` returning a list of `ParsedTxn` (see `app/importers/__init__.py`) with a stable `fingerprint` per transaction, detect it in the `upload` route before the CSV path, and pass the list to `services.store`, which does dedupe and rules for you. Add a made-up sample under `tests/samples/`.

### Change the default categories or the card catalog

See [customizing.md](customizing.md).

## Changing the database

`db.init()` runs at every start for **every** organisation database, so every step must be safe to run again (see [architecture.md → Schema changes](architecture.md#schema-changes-and-migrations)).

| Change | How |
|---|---|
| **New table** | Add `CREATE TABLE IF NOT EXISTS …` (and indexes) to `db.SCHEMA`. Done: it appears in old databases on the next start. |
| **New column** | Add `(table, column, "TYPE NOT NULL DEFAULT …")` to `db.ADDED_COLUMNS`. **Don't** also add it to the `CREATE TABLE` in `SCHEMA` (old databases wouldn't get it there anyway, and `ADDED_COLUMNS` covers new ones). It must have a default. |
| **Change a view** | Edit `db.LINES_VIEW`; views are dropped and recreated on every start. |
| **Rename/drop a column, change a CHECK or UNIQUE** | SQLite can't alter these. Add a guarded rebuild to `db._migrate()`: detect the old shape, create `x_v2`, copy rows keeping ids, drop, rename. Follow the existing categories example (it turns off foreign keys during the copy). |
| **Fix up existing data** | Add an idempotent statement in `db.init()` (e.g. `INSERT … WHERE NOT EXISTS`). |

Then:
1. Write a test that **creates an old-shape database by hand**, starts the app on it, and checks the data survived (see `test_migrates_v1_flat_categories` and `test_legacy_tally_db_is_adopted`).
2. Update the ER diagram in [architecture.md](architecture.md#data-model).
3. Never delete user data in a migration. If something must go, move it aside.

## Demo data and screenshots

`tools/make_demo.py <empty-folder>` builds a complete made-up data folder: accounts, about six months of transactions, rules, splits, budgets, a second organisation, and three demo credit cards with rewards set up. It goes through the app's own routes, so it also checks that they work.

Screenshots in `docs/screenshots/` are produced by `tools/take_screenshots.py` with Playwright (development only, not an app dependency):

```sh
.venv/bin/pip install playwright pillow && .venv/bin/playwright install chromium
.venv/bin/python tools/make_demo.py /tmp/demo-data
PFI_DATA_DIR=/tmp/demo-data PFI_NO_BROWSER=1 .venv/bin/python -m app &     # app on :8765
PFI_DATA_DIR=/tmp/demo-data .venv/bin/python tools/take_screenshots.py
```

It writes every image at 1280 px wide and reduces it to 256 colours to keep the repository small. To add a screenshot, add a `shot("name")` call there and reference `screenshots/name.png` from the docs. Screenshots must only ever show demo data.

## Git workflow

- `main` is always releasable: tests pass and docs match the code.
- New features go on a branch (`feature/<name>`) and are merged into `main` only when finished and approved. The docs on each branch describe that branch.
- Commit messages: a short summary line in the imperative ("Add savings goals page"), a blank line, then what and why.
- Never commit `data/`, `demo-data/`, `.venv/`, any `*.db`, or real bank files. `.gitignore` covers the first four.

## Checklist before you commit

- [ ] `pytest -q` passes (and new behaviour has tests).
- [ ] No outside URLs, HTTP clients or new runtime dependencies (`grep -rnE "https?://" app`).
- [ ] Money in cents, signs as documented, SQL parameterised.
- [ ] Old databases still open (if the schema changed: an upgrade test exists).
- [ ] Docs updated: [user-guide.md](user-guide.md) and [reference.md](reference.md) for anything a user sees, [architecture.md](architecture.md) for structure or schema, this page for new conventions, the [README](../README.md) feature list if it's a headline feature.
- [ ] Screenshots regenerated if a pictured page changed, using demo data only.

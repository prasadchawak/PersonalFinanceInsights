# CLAUDE.md

Brief for Claude (or any AI coding assistant) working on this repository. Read it fully before changing anything. Humans may find it a useful summary too.

## What this is

**Personal Finance Insights**: a private, single-user budgeting app that runs only on the user's own computer. Python 3.10+, FastAPI, Jinja2 server-rendered pages, SQLite (one file per organisation), SVG charts drawn in Python, ~90 lines of vanilla JS. No build step.

Start with: [docs/architecture.md](docs/architecture.md) (how it fits together, with diagrams) → [docs/development.md](docs/development.md) (conventions and recipes) → [docs/customizing.md](docs/customizing.md) (default categories, card catalog).

## Rules you must not break

1. **No network access, ever.** Don't add HTTP clients, outside APIs, telemetry, analytics, CDN links, web fonts, or remote images, in code, templates or CSS. `grep -rnE "https?://" app` must only show `127.0.0.1`/`localhost`. The user chose this app specifically so that no cloud or AI service sees their financial data.
2. **Bind to `127.0.0.1` only.** Don't weaken the middleware in `app/main.py` (host check, CSRF check via Sec-Fetch-Site/Origin, CSP `default-src 'self'`, `Referrer-Policy: same-origin`, `Cache-Control: no-store`). No inline `<script>` or `style=""` that needs the CSP relaxed.
3. **Money is integer cents.** Negative = money out, positive = money in. Users type positive amounts and choose direction separately.
4. **Never lose user data.** Schema changes go through `db.init()` and must be idempotent and keep old databases working (see *Changing the database* in development.md). Deletions are explicit and confirmed; removed organisations are moved to `data/deleted/`.
5. **No real personal data in the repo.** Tests, samples, demo data and screenshots are made up. The card catalog holds public card terms only, never which cards the user holds. Never commit `data/`, `demo-data/`, `*.db` or bank exports.
6. **No new runtime dependencies** without asking the user. Dev-only tools (pytest, Playwright for screenshots) are fine.
7. **Branches:** don't commit unfinished features to `main`. Work on a `feature/<name>` branch and merge only when the user says so. Keep the docs on each branch accurate for that branch.

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # set up
.venv/bin/python -m pytest -q                                            # run all tests (must pass)
.venv/bin/python tools/make_demo.py /tmp/demo                            # made-up data folder
PFI_DATA_DIR=/tmp/demo .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
.venv/bin/python tools/build_card_catalog.py                              # regenerate app/data/card_catalog.json
unshare -rn .venv/bin/python -m pytest -q                                 # Linux: prove tests need no network
```

Windows uses `.venv\Scripts\python`. Never point the app at the user's real `data/` folder while developing.

## Where things are

| Need to change… | Look in |
|---|---|
| A page or route | `app/main.py` (all routes) + `app/templates/<page>.html` |
| Navigation | `app/templates/base.html` |
| Schema, views, migrations, **default categories** | `app/db.py` (`SCHEMA`, `ADDED_COLUMNS`, `LINES_VIEW`, `_migrate`, `DEFAULT_CATEGORIES`) |
| Import parsing | `app/importers/ofx.py`, `app/importers/csv_import.py`, `app/normalize.py`; storing: `app/services.py` |
| Rules | `app/rules.py` |
| Categories tree | `app/categories.py` |
| Budgets / reports / charts | `app/budgets.py`, `app/reports.py`, `app/charts.py` |
| Organisations | `app/orgs.py` and the middleware in `app/main.py` |
| Rewards engine | `app/rewards.py` |
| **Card catalog** | `tools/build_card_catalog.py` → generates `app/data/card_catalog.json` (never edit the JSON) |
| Styles | `app/static/style.css` (light + dark) |
| Tests | `tests/` (fixture `client` in `tests/test_core.py`) |

## Key facts that trip people up

- Reports and budgets must read the **`report_lines`** view (splits expanded, excluded removed). Category filters use **`tx_lines`**. Balances use raw **`transactions`**. Don't re-implement split/exclusion logic.
- `db.connect()` opens the **current request's organisation** (a ContextVar set by middleware). Outside a request it opens `finance.db` unless given a path.
- Rules: first match wins, ordered by `priority` then `id`; all conditions are AND. Rules never change split transactions, and skip hand-set (🔒 `category_locked`) ones unless explicitly asked.
- `DEFAULT_CATEGORIES` only applies to **new** organisations (empty categories table).
- The card catalog maps categories **by name** to the default tree; renaming a default category means updating the catalog constants too. Catalog changes don't reach cards users already set up.
- Reward rules with both categories and merchants need both to match; use two rules with the same cap `group` for "either". Rotating quarters use `group="rotating"`.
- Routes with ids use `{x:int}` converters; POST handlers redirect with 303; empty optional numbers go through `_int()`.
- New columns go in `ADDED_COLUMNS` with a default, not in the `CREATE TABLE`.

## How to finish a change

1. Tests: add or update tests for the behaviour; `pytest -q` must pass.
2. Check the rules above (network grep, cents, no personal data).
3. **Update the docs in the same commit:**
   - user-visible change → `docs/user-guide.md` and `docs/reference.md` (rewards: `docs/rewards.md`);
   - structure, schema, security → `docs/architecture.md` (update the Mermaid diagrams);
   - new convention or recipe → `docs/development.md`;
   - headline feature → `README.md` "What it does";
   - changed a pictured page → regenerate screenshots with demo data (`tools/take_screenshots.py`).
4. Commit on the right branch with a clear message; push only when the user asks.
5. Tell the user what changed and anything they need to do (e.g. back up before upgrading).

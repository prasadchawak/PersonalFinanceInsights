# Personal Finance Insights

Private, local-only budgeting and spending insights.

It runs on your own computer and nothing else. There is no account, no cloud, no telemetry, no AI, and no calls to outside services.
- The server listens on `127.0.0.1` only, so other devices on your Wi-Fi can't connect.
- Pages may only load files from the app itself (a Content-Security-Policy blocks every outside source).
- Requests are refused if they don't come from `localhost`/`127.0.0.1` (this stops DNS-rebinding attacks) or if they're cross-site form posts (this stops CSRF). Other websites open in your browser can't reach the app's data.
- All your data is one file: `data\finance.db` (SQLite).

## Run it (Windows)

1. Install Python 3.11 or newer from python.org, and tick **"Add python.exe to PATH"** during setup.
2. Unzip this folder somewhere, for example `C:\Users\you\PersonalFinanceInsights`.
3. Double-click **`run.bat`**. The first run sets up a private Python environment, which takes about a minute. After that, the app opens at http://127.0.0.1:8765.
4. To stop the app, close the black window or press Ctrl+C in it.

On macOS or Linux, run `./run.sh` instead.

## First use

1. **Accounts:** add each account you'll track. Set its opening balance to the balance just before the oldest transaction you'll import. Enter what you owe on cards and loans as a negative number.
2. **Import:** download a statement from your bank and upload it.
   - **OFX/QFX** (often labeled "Quicken", "Money" or "Web Connect") is best. It's a standard format, so it imports with no setup and duplicates are matched exactly.
   - **CSV:** the first time you import from a bank, the app shows the file and guesses which column is which. You confirm the guess, check that purchases show as negative (tick **Flip sign** if they don't), and import. The app remembers that layout from the file's header row, so later imports from the same bank are a single click.
   - Re-importing an overlapping date range is safe because duplicates are skipped. Two identical charges on the same day (two coffees) are both kept.
   - Every import can be undone from the import history.
3. **Categorize:** pick a category on each transaction, or click **+ rule** to make a rule such as "description contains COSTCO → Groceries, rename to Costco". Rules run on every future import. A category you set by hand is locked, so rules never overwrite it.
4. **Categories** can be up to 3 levels deep (e.g. Food › Groceries › Costco). On the Categories page you can add, rename, move a category (along with everything under it) to a new parent, or delete it. When you delete one, its subcategories and transactions move up to its parent. Filtering or reporting on a parent includes everything under it.
5. **Bulk edit:** on Transactions, tick rows (or use the header checkbox to tick the whole page), pick a category in the bar above the table and click Apply. Choose "all N matching this filter" to recategorize every transaction that matches the current filter, not just the visible page.
6. **Reports:** spending by category and income by category (pie or bar, rolled up to level 1, 2 or 3, with a click-through to break any category down further), income vs expenses by month with net and savings rate, spending over time for any category, net worth at each month end, and merchants. Every report has a period picker (this month, last 3/6/12 months, year to date, last year, all time, or custom dates) and an account filter, plus a table you can click through to the matching transactions. Charts are drawn by the app itself; nothing is loaded from the internet.
7. **Split transactions:** click **split** on any transaction to divide it across categories, for example one Costco charge split into Groceries and Household. The editor shows how much is left to assign, and the parts must add up exactly. Reports, budgets and filters all use the split parts. Rules never change a split transaction, and choosing a single category for it (or bulk-setting one) replaces the split.
8. **Budgets:** set a monthly amount for any category or subcategory. Save it as your usual monthly budget, or for one month only. A parent's spending includes everything under it, and totals never count a parent and its children twice. The Overview page shows the budgets closest to their limit.
9. **Merchants report:** spending by merchant as a pie or bar chart, optionally within one category. Click a merchant to see its month-by-month trend and which categories it falls under. To merge name variants into one merchant, use a rule's "rename payee".
10. **Accounts and rules can be edited:** rename an account or change its type and opening balance. To **delete** an account, open "Delete an account" on the Accounts page and type its name to confirm. This permanently removes its transactions (including splits), its import history and any rules limited to that account. Run `backup.bat` first.
11. **Rules with several conditions (AND):** a rule can combine any number of text conditions (description or memo, description only, or memo only; using contains, doesn't contain, starts with, equals or matches regex) with an amount test (=, >, ≥, <, ≤, between; the sign is ignored), a money-in or money-out test, and an account. All of them must match. For example: description contains "AMAZON" **and** doesn't contain "refund" **and** amount > 100 **and** money out → Electronics. For "either A or B", make two rules. Each rule shows how many transactions it currently matches.
12. **Sorting:** click any column header on Transactions (date, payee, account, category or amount) to sort by it, and click again to reverse. Sorting keeps your filters.
13. **Transfers:** card payments and moves between your own accounts belong in a *transfer* category, so they aren't counted as spending twice.

## Backups

Double-click **`backup.bat`** (or run `python -m app.backup`). It writes a consistent snapshot to `data\backups\` and is safe to run while the app is open. Copy those files to an external drive or another place you trust.

Recommended: turn on BitLocker (Windows full-disk encryption) so the database is encrypted at rest if the laptop is lost.

## Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `PFI_PORT` | `8765` | Port to listen on |
| `PFI_DATA_DIR` | `.\data` | Where the database and backups live |
| `PFI_NO_BROWSER` | unset | Set to `1` to stop the app opening the browser on start |

The older `TALLY_*` names still work.

**Upgrading from Tally:** this app used to be called Tally. On first start, an existing `data\tally.db` is renamed to `data\finance.db` automatically (only when no `finance.db` exists yet), so all your data carries over.

## How it's built

```
app/
  db.py              SQLite schema; money is stored as integer cents (no floating-point rounding)
  normalize.py       amount/date parsing, payee cleanup, fingerprints
  importers/ofx.py   OFX/QFX parser (SGML and XML versions)
  importers/csv_import.py   header detection, column guessing, mapping-driven parsing
  rules.py           first-match categorization rules
  splits.py          split transactions
  budgets.py         monthly budgets (default + per-month override, parent roll-up)
  categories.py      3-level category tree: paths, roll-ups, safe rename/move/delete
  reports.py         report queries (period ranges, roll-ups, cash flow, net worth)
  charts.py          server-rendered SVG charts (pie, bar, column, line)
  services.py        import pipeline (dedupe, rules), balances, monthly summary
  main.py            web app (FastAPI + server-rendered pages)
tests/               pytest suite, run against made-up sample files
```

How duplicates are detected: an OFX transaction is identified by the bank's own FITID. A CSV transaction is identified by a hash of `(date, amount, description, n-th occurrence in file)`, stored together with its account.

To run the tests: `pip install -r requirements-dev.txt` then `pytest`.

## Roadmap

- **Phase 2:** monthly budgets compared with actual spending
- **Phase 3:** PDF export of reports
- **Phase 4:** credit-card rewards tracking, mortgage extra-payment scenarios
- **Phase 5:** Docker image, passkey sign-in, encrypted backups, home-server deployment (Option 2)

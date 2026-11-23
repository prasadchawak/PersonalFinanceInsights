# Personal Finance Insights

Private, local-only budgeting and spending insights.

It runs on your own computer and nothing else. There is no account, no cloud, no telemetry, no AI, and no calls to outside services.
- The server listens on `127.0.0.1` only, so other devices on your Wi-Fi can't connect.
- Pages may only load files from the app itself (a Content-Security-Policy blocks every outside source).
- Requests are refused if they don't come from `localhost`/`127.0.0.1` (this stops DNS-rebinding attacks) or if they're cross-site form posts (this stops CSRF). Other websites open in your browser can't reach the app's data.
- All your data is in SQLite files in the `data` folder: `data\finance.db`, plus one file per extra [organisation](#organisations).

## Run it (Windows)

1. Install Python 3.11 or newer from python.org, and tick **"Add python.exe to PATH"** during setup.
2. Unzip this folder somewhere, for example `C:\Users\you\PersonalFinanceInsights`.
3. Double-click **`run.bat`**. The first run sets up a private Python environment, which takes about a minute. After that, the app opens at http://127.0.0.1:8765.
4. To stop the app, close the black window or press Ctrl+C in it.

On macOS or Linux, run `./run.sh` instead.

## Quick help

All screenshots use made-up demo data (see [Try it with demo data](#try-it-with-demo-data)).

**Contents:** [Getting started](#getting-started) · [Importing](#importing-statements) · [Transactions](#working-with-transactions) · [Splitting](#splitting-a-transaction) · [Rules](#rules) · [Categories](#categories) · [Budgets](#budgets) · [Reports](#reports) · [Leaving money out of reports](#leaving-money-out-of-reports) · [Organisations](#organisations) · [Troubleshooting](#troubleshooting)

### Getting started

1. **Accounts →** add each account you track (checking, savings, credit card, loan). Set its *opening balance* to the balance just before the first transaction you'll import. Enter money owed on cards and loans as a negative number.
2. **Import →** upload a statement for each account (see below).
3. **Transactions →** give uncategorised transactions a category. Use **+ rule** on any row so the next import does it for you.
4. **Budgets →** set monthly limits. **Overview** and **Reports** fill in by themselves.

<img src="docs/screenshots/overview.png" alt="Overview page: monthly totals, spending pie, account balances, budgets closest to their limit, and income vs expenses" width="900">

*Overview: this month's totals, spending by category, balances, the budgets closest to their limit, and the last six months.*

### Importing statements

- Download **OFX / QFX** ("Quicken" / "Web Connect") from your bank if it's offered: it imports with no setup.
- With **CSV**, the first file from each bank opens this mapping screen. Check the guessed columns and the preview, then import. Purchases must show red (negative). If they don't, tick **Flip sign**. The layout is remembered, so next time it's one click.
- Re-importing overlapping dates is safe, because duplicates are skipped. Every import can be **undone** from the import history.

<img src="docs/screenshots/import-mapping.png" alt="CSV column mapping screen with the raw file, column choices and a preview" width="900">

### Working with transactions

- **Filter** by account, category (a parent includes its subcategories), month, text, **amount** (equals / more than / at least / less than / at most / between, typed as positive numbers) and **money in or out**. Typing a number such as `24.85` in the search box finds that exact amount.
- **Sort** by clicking any column header; click again to reverse.
- **Bulk edit:** tick rows (the header box ticks the whole page) or choose *all matching this filter*, then set a category, or **⊘ Exclude from reports** / **Include again**.
- On each row: **split** · **+ rule** (make a rule from this payee) · **edit** (rename the payee, add a note, exclude from reports). 🔒 means the category was set by hand, so rules won't change it.
- **Export CSV** downloads exactly what the current filter shows.

<img src="docs/screenshots/transactions.png" alt="Transactions list with filters, sortable headers and two rows ticked for bulk edit" width="900">

### Splitting a transaction

Click **split** to divide one charge across categories, for example a store trip that was mostly household items plus some groceries. Enter each part as a positive number. **Remaining** must reach $0.00, and *Put the remainder on the last filled line* does the arithmetic for you. Reports and budgets use the parts.

<img src="docs/screenshots/split.png" alt="Split editor dividing one purchase between Household and Groceries" width="900">

### Rules

A rule fires when **all** its conditions match:
- any number of text tests: contains, doesn't contain, starts with, equals, regex
- an amount: type a positive number and choose money out or in
- an account

It then sets a category, can rename the payee, and can exclude the transaction from reports.

- **The first matching rule wins.** Lower priority numbers run first. Put specific rules (e.g. *marketplace AND more than $150*) at a lower number than general ones (*marketplace*).
- **Matches** shows how many transactions each rule catches. *"+N taken by earlier rule"* means a higher-priority rule got there first: lower this rule's number.
- Saving a rule only touches **uncategorised** transactions unless you choose *re-apply to all*, or *…including hand-set 🔒*.

<img src="docs/screenshots/rules.png" alt="Rules list with one rule open for editing, showing a text, amount and money-out condition" width="900">

### Categories

Categories can be up to **3 levels** deep, for example Food › Groceries › FreshMart. On this page you can:
- add a category, rename it, or move it (with everything under it) to a new parent
- delete a category: its subcategories and transactions move up to its parent
- **⊘ exclude** a category (see [Leaving money out of reports](#leaving-money-out-of-reports))

Use a *transfer* kind for card payments and moves between your own accounts, so they're never counted as spending.

<img src="docs/screenshots/categories.png" alt="Category tree with rename, move and exclude controls; one category excluded" width="900">

### Budgets

Type a monthly amount next to any category. Then choose to save it as your **usual monthly budget**, or **only for this month** (enter 0 to skip a category for one month). A parent's spending includes its subcategories, and totals never count a parent and its children twice. Bars turn amber at 90% and red with **⚠ over** past 100%.

<img src="docs/screenshots/budgets.png" alt="Budgets page with progress bars; two categories over budget" width="900">

### Reports

Every report has a period picker (this month … all time, or custom dates), an account filter, and a table under the chart. Click any row to see its transactions. Hover over a bar or slice for the exact amount.

| Report | What it answers |
|---|---|
| **Spending / Income by category** | Where the money went. Choose **Pie or Bar**, how deep to break down (levels 1–3), or **Subcategories of** one category. |
| **Income vs expenses** | Monthly income, spending, net and savings rate. |
| **Spending over time** | Month-by-month for all spending or any one category. |
| **Merchants** | Who you pay most, as a pie or bar. Click a merchant for its trend and categories. |
| **Net worth** | Assets, debts and net worth at each month end. |

<img src="docs/screenshots/report-spending-pie.png" alt="Spending by category as a pie chart with a table" width="900">

<img src="docs/screenshots/report-subcategories-bar.png" alt="Bar chart of the subcategories of Food" width="900">

<img src="docs/screenshots/report-income-vs-expenses.png" alt="Income vs expenses by month, with net and savings rate" width="900">

<img src="docs/screenshots/report-merchants.png" alt="Top merchants as a bar chart with counts, averages and totals" width="900">

### Leaving money out of reports

Some money would be counted twice. Example: you already count $400 a month going into a savings fund. If you later pay a $640 car repair *from* that fund and count it too, the same money shows up twice. **⊘ Exclude** the repair, and it stays out of reports and budgets. You can exclude:
- one transaction: **edit** → *Leave out of reports & budgets*
- many at once: the bulk bar
- a whole category, e.g. *Paid from savings fund*: tick **⊘ exclude** on Categories
- automatically: a rule with *and ⊘ exclude from reports & budgets*

Excluded rows stay visible (dimmed, marked ⊘) and **still count in balances and net worth**. Every report says what was left out: *"⊘ Not counted here: 1 excluded transaction ($640.00 out)"*.

### Organisations

Use organisations to keep separate books, for example for another person, a rental property or a business. Each has its own accounts, categories, rules and budgets, in **its own database file**. Switch with the menu at the top right. **⚙** lets you create, rename or remove one; a removed organisation's file is moved to `data/deleted/`, not erased.

<img src="docs/screenshots/organisations.png" alt="Organisations page listing two organisations with their database files" width="900">

### Troubleshooting

| Problem | What to do |
|---|---|
| A rule doesn't change anything | Check **Matches**: *"taken by earlier rule"* means lower its priority number. Transactions with 🔒 (set by hand) or a split are skipped unless you re-apply *including hand-set*. |
| Totals don't match my bank statement | Card payments and transfers should use a *transfer* category, so they aren't counted as spending. Check for **⊘ excluded** transactions (each report shows them). For balances, check each account's **opening balance**. |
| CSV amounts are backwards | Delete that layout under *Import → Saved CSV layouts*, re-import, and tick **Flip sign**. |
| I imported the wrong file | *Import → history → Undo* removes everything that import added. |
| Page says *Cross-site request blocked* | Open the app at `http://127.0.0.1:8765` (not another address or a bookmark to an old port). |
| Port 8765 is already in use | `set PFI_PORT=8766` then `run.bat`. |
| Starting fresh / moving computers | Stop the app and copy the whole `data` folder. Run `backup.bat` before any upgrade. |

### Try it with demo data

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python tools\make_demo.py demo-data
set PFI_DATA_DIR=demo-data
run.bat
```

(Run `run.bat` once first so `.venv` exists.)

This builds a separate folder of made-up accounts and transactions, which are the ones in these screenshots. Your real `data` folder isn't touched. Delete `demo-data` when you're done.

## First use (detailed reference)

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
11. **Rules with several conditions (AND):** a rule can combine any number of text conditions (description or memo, description only, or memo only; using contains, doesn't contain, starts with, equals or matches regex) with an amount test and a money-in or money-out choice, plus an account. All of them must match. "Description" matches both the bank's original text and the name shown in the app after a rename. **Amounts are typed as positive numbers** (e.g. 24.85), with equals, more than, at least, less than, at most or between, and **money out or in is picked separately**. A typed minus sign is read as "money out", and an amount typed while "any amount" is selected means "equals". Example: description contains "AMAZON" **and** amount more than $200 **and** money out → Electronics. For "either A or B", make two rules. Each rule shows how many transactions it currently matches, and flags "taken by earlier rule" when a rule with a lower priority number (or an older one with the same priority) catches them first; give the rule a lower number (0 is allowed) to fix that. Rules never replace categories you set by hand (🔒) unless you choose "re-apply to all, including hand-set".
12. **Sorting:** click any column header on Transactions (date, payee, account, category or amount) to sort by it, and click again to reverse. Sorting keeps your filters.
    **Amount filters:** on Transactions, filter by amount (equals, more than, at least, less than, at most, between; always positive numbers) and by money out or in. These combine with the other filters, sorting, bulk edit and CSV export. Typing a number such as `24.85` or `$3,200` into the search box also finds transactions of exactly that amount.
13. **Transfers:** card payments and moves between your own accounts belong in a *transfer* category, so they aren't counted as spending twice.
14. **Organisations:** keep completely separate sets of accounts, for different people or for groups such as household, rental property or business. Each organisation has its own accounts, transactions, categories, rules and budgets, stored in **its own database file** (`data/finance.db` for the first one, called "Personal"; new ones go in `data/orgs/`). The list of organisations is kept in a separate small file, `data/orgs.db`, which holds only names and file locations. Switch with the menu at the top right. Your browser remembers the last one you opened. Manage organisations with ⚙: create, rename, or remove. Removing one moves its file to `data/deleted/` instead of erasing it.
15. **Leave out of reports and budgets (⊘ exclude):** some money would otherwise be counted twice. For example, you budget and track $X a month going into a savings fund, and later an expense Q is paid from that fund. Counting both X and Q double-counts the same money, so mark Q as excluded. You can exclude:
    - a single transaction (**edit** → "Leave out of reports & budgets"),
    - many at once (tick them, or use "all matching this filter", then **⊘ Exclude from reports** or **Include again**),
    - a whole category and everything under it (Categories page, **⊘ exclude**; e.g. a "Paid from savings fund" category),
    - automatically, with a rule ("and ⊘ exclude from reports & budgets").

    Excluded transactions still show on Transactions (marked ⊘ and dimmed), and the filter there can show only excluded or hide them. They're still part of account balances and net worth, because the money really moved. Overview, Reports and Budgets each say how much was left out, with a link to those transactions. The CSV export has an `excluded_from_reports` column.

## Backups

Double-click **`backup.bat`** (or run `python -m app.backup`). It writes a consistent snapshot of **every organisation's database plus the organisation list** to a dated folder in `data\backups\`, and is safe to run while the app is open. Copy those files to an external drive or another place you trust.

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

## License

[MIT](LICENSE) © 2026 prasadchawak

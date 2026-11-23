# Reference

Every feature in detail. For a shorter tour with screenshots, see the [user guide](user-guide.md).

**Contents:** [Accounts](#accounts) · [Importing](#importing) · [Transactions](#transactions) · [Filters, sorting and export](#filters-sorting-and-export) · [Bulk edit](#bulk-edit) · [Split transactions](#split-transactions) · [Rules](#rules) · [Categories](#categories) · [Transfers](#transfers) · [Budgets](#budgets) · [Reports](#reports) · [Leaving money out of reports and budgets](#leaving-money-out-of-reports-and-budgets) · [Organisations](#organisations) · [Sign convention](#sign-convention)

## Accounts

- **Add** each account you track. Types: checking, savings, credit, loan, investment, cash.
- **Opening balance:** the balance just before the oldest transaction you'll import. Enter what you owe on cards and loans as a **negative** number. Balance = opening balance + every transaction in the account.
- **Edit:** rename an account, or change its institution, type or opening balance, at any time.
- **Delete:** open *Delete an account* on the Accounts page and type the account's name to confirm. This **permanently** removes its transactions (including splits), its import history and any rules limited to that account. Run a backup first ([how](install.md#backups-and-restore)).

## Importing

Download a statement from your bank's website and upload it on **Import**, choosing the account it belongs to.

| Format | How it's read | Duplicates |
|---|---|---|
| **OFX / QFX** (labelled "Quicken", "Money" or "Web Connect") | A standard format: no setup at all. Both the old SGML and the newer XML versions work. | Matched by the bank's own transaction ID (FITID), so they're exact. |
| **CSV** | The first file from each bank opens a mapping screen. The app finds the header row (skipping any preamble), guesses the date, description, amount (or separate debit/credit columns) and memo columns, and shows a preview. Confirm, then import. | Matched by date, amount, description and how many times that same row appears in the file, so two identical coffees on the same day are both kept. |

- **Saved CSV layouts:** the mapping is remembered by the file's header row, so the next file from that bank imports in one click. Delete a saved layout under *Import → Saved CSV layouts* to set it up again.
- **Flip sign:** purchases must be negative (shown in red). Some banks export card purchases as positive numbers; tick **Flip sign** for those.
- **Overlapping dates are safe:** duplicates are skipped and counted in the import result.
- **Undo:** every import can be undone from the import history. That removes exactly the transactions it added.
- **Rules run on import:** each new transaction is checked against your [rules](#rules).
- Files up to 20 MB. Text encodings UTF-8 (with or without BOM), Windows-1252 and Latin-1 are recognised.

## Transactions

Each row shows date, payee, account, category and amount. On each row:
- **Category:** pick one. A category you set by hand is **locked (🔒)**, so rules never overwrite it.
- **edit:** rename the payee, add a note, or leave the transaction out of reports & budgets.
- **split:** divide it across categories (see [Split transactions](#split-transactions)).
- **+ rule:** start a rule from this payee.

## Filters, sorting and export

- **Filters:** account, category (a parent includes everything under it), month, text search, amount, money in/out, and excluded (show only / hide). Filters combine.
- **Amount filter:** equals, more than, at least, less than, at most or between. Always type **positive** numbers; choose money out or in separately.
- **Search box:** text matches description, memo and notes. A number such as `24.85` or `$3,200` finds transactions of exactly that amount.
- **Sorting:** click a column header (date, payee, account, category, amount); click again to reverse. Sorting keeps your filters.
- **Export CSV** downloads exactly what the current filter shows, including an `excluded_from_reports` column.

## Bulk edit

Tick rows (the header checkbox ticks the whole page), or choose **all N matching this filter** to act on every transaction the filter matches, not just the visible page. Then:
- pick a category and **Apply**, or
- **⊘ Exclude from reports** / **Include again**.

Setting one category on a split transaction replaces the split.

## Split transactions

Click **split** to divide one transaction across categories, for example one warehouse-club charge into Groceries and Household.
- Enter each part as a **positive** number. **Remaining** must reach $0.00; the parts must add up exactly. *Put the remainder on the last filled line* does the arithmetic.
- Reports, budgets and category filters use the parts, not the whole.
- Rules never change a split transaction. Choosing a single category for it (one at a time or in bulk) removes the split; so does **unsplit**.

## Rules

A rule fires when **all** of its conditions match (AND). For "either A or B", make two rules.

| Condition | Options |
|---|---|
| Description or memo / Description only / Memo only | contains, doesn't contain, starts with, equals, matches regex (all case-insensitive) |
| Amount | equals, more than, at least, less than, at most, between — typed as **positive** numbers |
| Direction | money out or money in |
| Account | any, or one account |

- "Description" matches both the bank's original text and the name shown in the app after a rename.
- A typed minus sign is read as "money out". An amount typed while *any amount* is selected means "equals".
- **Actions:** set a category, optionally rename the payee, optionally **⊘ exclude from reports & budgets**.
- **Order:** lower priority numbers run first (0 is allowed); with equal numbers, the older rule runs first. **The first matching rule wins.** Put specific rules (*AMAZON and more than $200 and money out → Electronics*) before general ones (*AMAZON → Shopping*).
- **Matches** shows how many transactions each rule catches now, and "*+N taken by earlier rule*" when an earlier rule catches them first.
- **When rules run:** on every import, and when you save a rule. By default only **uncategorised** transactions change. *Re-apply to all* also updates rule-set categories; *…including hand-set 🔒* updates everything except split transactions.

Example: *description contains "AMAZON"* **and** *amount more than 200* **and** *money out* → Electronics.

## Categories

- Up to **3 levels** deep, for example Food › Groceries › Warehouse club.
- **Kinds:** expense, income or transfer, set on the top level and shared by everything under it.
- **Add**, **rename**, or **move** a category (with everything under it) to a new parent. A move that would make the tree deeper than 3 levels is refused.
- **Delete:** its subcategories and transactions move up to its parent (or become uncategorised for a top-level category).
- **⊘ exclude** a category to leave it, and everything under it, out of reports and budgets.
- Filtering or reporting on a parent includes everything under it.
- New organisations start with a default tree. To change that tree for everyone, see [Customizing: default categories](customizing.md#default-categories).

## Transfers

Card payments and moves between your own accounts belong in a **transfer**-kind category (the default tree has *Transfers › Credit Card Payment / Between My Accounts / Investment Contribution*). Transfers are never counted as spending or income, so paying a card bill doesn't count the same purchases twice.

## Budgets

- Set a monthly amount for any category or subcategory.
- Save it as your **usual monthly budget** (applies to every month) or **only for this month** (overrides the usual one; enter 0 to skip a category for a month).
- A parent's spending includes everything under it. Totals never count a parent and its children twice.
- Bars turn amber at 90% and red with **⚠ over** past 100%. The Overview shows the budgets closest to their limit.

## Reports

Every report has a period picker (this month, last 3/6/12 months, year to date, last year, all time, or custom dates), an account filter, and a table you can click through to the matching transactions. Charts are drawn by the app as SVG; nothing loads from the internet.

| Report | Options |
|---|---|
| Spending by category / Income by category | Pie or bar; roll up to level 1, 2 or 3; or **subcategories of** one category. Click any row to break it down further. |
| Income vs expenses | Income, spending, net and savings rate per month. |
| Spending over time | Month by month, for all spending or one category. |
| Merchants | Pie or bar, optionally within one category. Click a merchant for its monthly trend and the categories it falls under. To merge name variants, use a rule's *rename payee*. |
| Net worth | Assets, debts and net worth at each month end. |

## Leaving money out of reports and budgets

Some money would otherwise be counted twice. Example: you budget $400 a month into a savings fund, then later pay a $640 car repair *from* that fund. Counting both double-counts the same money, so mark the repair as excluded (⊘). You can exclude:
- one transaction: **edit** → *Leave out of reports & budgets*;
- many: tick them or use *all matching this filter*, then **⊘ Exclude from reports** (or **Include again**);
- a whole category and everything under it: Categories → **⊘ exclude** (e.g. a *Paid from savings fund* category);
- automatically: a rule with *and ⊘ exclude from reports & budgets*.

Excluded transactions stay visible on Transactions (dimmed and marked ⊘), and they **still count in account balances and net worth**, because the money really moved. Overview, Reports and Budgets each say how much was left out, with a link to those transactions.

## Organisations

Separate sets of books, for example for another person, a rental property or a business.
- Each organisation has its own accounts, transactions, categories, rules and budgets, in **its own database file**: `data/finance.db` for the first one ("Personal"), and `data/orgs/<name>.db` for new ones.
- The list of organisations is kept in a separate small file, `data/orgs.db`, which holds only names and file locations.
- Switch with the menu at the top right; your browser remembers the last one you opened.
- **⚙** lets you create, rename or remove an organisation. Removing one moves its file to `data/deleted/` instead of erasing it.

## Sign convention

Everywhere in the app and its database: **negative = money out**, **positive = money in**. On a credit card, a purchase is negative and a payment or refund is positive. Where you type amounts (rules, filters, splits), you type positive numbers and choose the direction separately.

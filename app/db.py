"""SQLite storage. One file, no server. Money is always stored as integer cents."""
import os
import sqlite3
from contextvars import ContextVar
from pathlib import Path

def _env(name: str) -> str | None:
    # PFI_* settings; the old TALLY_* names still work.
    return os.environ.get(f"PFI_{name}") or os.environ.get(f"TALLY_{name}")


DATA_DIR = Path(_env("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = Path(_env("DB") or DATA_DIR / "finance.db")
LEGACY_DB = DATA_DIR / "tally.db"   # name used before the app was renamed


def adopt_legacy_db() -> None:
    """If an older install left data in tally.db, rename it (and its WAL files) to finance.db once.
    Only runs when no finance.db exists yet, so nothing is ever overwritten."""
    if _env("DB") or DB_PATH.exists() or not LEGACY_DB.exists():
        return
    LEGACY_DB.rename(DB_PATH)
    for ext in ("-wal", "-shm"):
        old = LEGACY_DB.with_name(LEGACY_DB.name + ext)
        if old.exists():
            old.rename(DB_PATH.with_name(DB_PATH.name + ext))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    institution TEXT,
    type TEXT NOT NULL CHECK (type IN ('checking','savings','credit','loan','investment','cash')),
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Up to 3 levels deep (e.g. Food > Groceries > Costco). Children share their top-level parent's kind.
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
    parent_id INTEGER REFERENCES categories(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cat_name ON categories(COALESCE(parent_id, 0), name COLLATE NOCASE);

-- A saved column mapping for one bank's CSV layout, matched by its header row.
CREATE TABLE IF NOT EXISTS import_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    header_fingerprint TEXT NOT NULL UNIQUE,
    mapping_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    format TEXT NOT NULL,
    rows_total INTEGER NOT NULL,
    rows_new INTEGER NOT NULL,
    rows_duplicate INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    posted_date TEXT NOT NULL,              -- ISO yyyy-mm-dd
    amount_cents INTEGER NOT NULL,          -- negative = money out, positive = money in
    payee_raw TEXT NOT NULL,
    payee TEXT NOT NULL,                    -- cleaned / renamed
    memo TEXT,
    category_id INTEGER REFERENCES categories(id),
    category_locked INTEGER NOT NULL DEFAULT 0,  -- 1 = set by hand, rules won't overwrite
    notes TEXT,
    fingerprint TEXT NOT NULL,
    import_id INTEGER REFERENCES imports(id),
    raw_json TEXT,                          -- original row, kept for audit
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(posted_date);
CREATE INDEX IF NOT EXISTS ix_tx_cat ON transactions(category_id);

-- A transaction split across several categories. When splits exist they replace the
-- transaction's own category in every report; amounts always add up to the transaction.
CREATE TABLE IF NOT EXISTS splits (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    amount_cents INTEGER NOT NULL,          -- same sign convention as the transaction
    memo TEXT
);
CREATE INDEX IF NOT EXISTS ix_split_tx ON splits(transaction_id);

-- Monthly budgets. month = 'default' applies to every month unless that month has its own row.
CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    month TEXT NOT NULL,                    -- 'default' or 'yyyy-mm'
    amount_cents INTEGER NOT NULL,          -- positive
    UNIQUE (category_id, month)
);

-- Credit card rewards: one program per credit account, with earn rules. See rewards.py.
CREATE TABLE IF NOT EXISTS card_programs (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    catalog_key TEXT,                     -- which catalog card it started from (informational)
    card_name TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT 'Other',
    unit TEXT NOT NULL DEFAULT 'cash',    -- cash | points | miles
    cpp REAL NOT NULL DEFAULT 1.0,        -- value of one unit in cents
    base_rate REAL NOT NULL DEFAULT 1.0,  -- units per $1 when no earn rule applies
    annual_fee_cents INTEGER NOT NULL DEFAULT 0,
    terms_checked TEXT,                   -- e.g. 2026-11
    notes TEXT
);
CREATE TABLE IF NOT EXISTS earn_rules (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES card_programs(account_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    rate REAL NOT NULL,                   -- units per $1
    category_ids TEXT,                    -- comma-separated category ids (each includes its subcategories)
    merchants TEXT,                       -- '|'-separated: description must contain one of these
    exclude TEXT,                         -- '|'-separated: description must contain none of these
    cap_cents INTEGER,                    -- spend cap at this rate per period (NULL = no cap)
    cap_period TEXT CHECK (cap_period IN ('month','quarter','year') OR cap_period IS NULL),
    cap_group TEXT,                       -- rules sharing a group share one cap
    start_date TEXT, end_date TEXT,       -- active window (inclusive), NULL = always
    needs_activation INTEGER NOT NULL DEFAULT 0,
    activated INTEGER NOT NULL DEFAULT 1,
    unmapped TEXT                         -- catalog category names not found in this organisation
);
CREATE INDEX IF NOT EXISTS ix_earn_acct ON earn_rules(account_id);

-- Every condition of a rule must match (AND). See rules.py for the fields.
CREATE TABLE IF NOT EXISTS rule_conditions (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('text','payee','memo','amount','direction')),
    op TEXT NOT NULL,
    value TEXT NOT NULL,
    value2 TEXT
);
CREATE INDEX IF NOT EXISTS ix_rule_cond ON rule_conditions(rule_id);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    priority INTEGER NOT NULL DEFAULT 100,  -- lower runs first; first match wins
    match_type TEXT NOT NULL CHECK (match_type IN ('contains','starts_with','equals','regex')),
    pattern TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),  -- NULL = any account
    category_id INTEGER REFERENCES categories(id),
    rename_to TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# (name, kind, [children]) — a sensible starting tree; rename/move/delete freely in the app.
DEFAULT_CATEGORIES = [
    ("Food", "expense", ["Groceries", "Dining", "Coffee"]),
    ("Housing", "expense", ["Rent / Mortgage", "Utilities", "Maintenance", "Home Insurance"]),
    ("Transportation", "expense", ["Fuel", "Parking & Tolls", "Rideshare", "Car Maintenance", "Auto Insurance"]),
    ("Shopping", "expense", ["Household", "Clothing", "Electronics"]),
    ("Health", "expense", ["Medical", "Pharmacy", "Fitness"]),
    ("Bills & Subscriptions", "expense", ["Phone & Internet", "Streaming", "Software"]),
    ("Travel", "expense", ["Flights", "Lodging"]),
    ("Entertainment", "expense", []),
    ("Education", "expense", []),
    ("Gifts & Donations", "expense", []),
    ("Fees & Interest", "expense", []),
    ("Taxes", "expense", []),
    ("Other Expenses", "expense", []),
    ("Income", "income", ["Salary", "Interest & Dividends", "Refunds", "Other Income"]),
    ("Transfers", "transfer", ["Credit Card Payment", "Between My Accounts", "Investment Contribution"]),
]


# The organisation database the current request works on (set per request from the org switcher).
current_db: ContextVar[Path | None] = ContextVar("current_db", default=None)
_initialised: set[str] = set()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or current_db.get() or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# "Lines": an unsplit transaction is one line, a split one is a line per split.
# A line is excluded from reports & budgets if its transaction is marked excluded, or its category
# (or any parent of it, up to 3 levels) is marked excluded. report_lines = what reports/budgets count.
LINES_VIEW = """
DROP VIEW IF EXISTS report_lines;
DROP VIEW IF EXISTS tx_lines;
CREATE VIEW tx_lines AS
    WITH base AS (
        SELECT t.id AS tx_id, t.account_id, t.posted_date, t.payee, t.amount_cents, t.category_id,
               0 AS is_split, t.excluded AS tx_excluded
        FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
        UNION ALL
        SELECT t.id, t.account_id, t.posted_date, t.payee, s.amount_cents, s.category_id, 1, t.excluded
        FROM splits s JOIN transactions t ON t.id = s.transaction_id)
    SELECT b.tx_id, b.account_id, b.posted_date, b.payee, b.amount_cents, b.category_id, b.is_split,
           CASE WHEN b.tx_excluded = 1 OR COALESCE(c1.excluded, 0) = 1 OR COALESCE(c2.excluded, 0) = 1
                     OR COALESCE(c3.excluded, 0) = 1 THEN 1 ELSE 0 END AS excluded
    FROM base b
    LEFT JOIN categories c1 ON c1.id = b.category_id
    LEFT JOIN categories c2 ON c2.id = c1.parent_id
    LEFT JOIN categories c3 ON c3.id = c2.parent_id;
CREATE VIEW report_lines AS SELECT * FROM tx_lines WHERE excluded = 0;
"""

# Columns added after the first release: (table, column, definition).
ADDED_COLUMNS = [
    ("transactions", "excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("categories", "excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("rules", "set_excluded", "INTEGER NOT NULL DEFAULT 0"),
]


def _add_columns(conn: sqlite3.Connection) -> None:
    for table, col, ddl in ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by earlier versions in place. Your data is kept."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "categories" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
        if "parent_id" not in cols:
            # v1 had a flat list with UNIQUE(name). Rebuild the table to allow the same name under
            # different parents; ids are preserved so transactions and rules keep their links.
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript("""
                BEGIN;
                CREATE TABLE categories_v2 (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
                    parent_id INTEGER REFERENCES categories(id));
                INSERT INTO categories_v2(id, name, kind, parent_id) SELECT id, name, kind, NULL FROM categories;
                DROP TABLE categories;
                ALTER TABLE categories_v2 RENAME TO categories;
                COMMIT;""")
            conn.execute("PRAGMA foreign_keys = ON")


def init(conn: sqlite3.Connection) -> None:
    _migrate(conn)
    conn.executescript(SCHEMA)
    _add_columns(conn)
    conn.executescript(LINES_VIEW)
    # Rules made before conditions existed: turn their single text test into a condition.
    conn.execute("""INSERT INTO rule_conditions(rule_id, field, op, value)
                    SELECT id, 'text', match_type, pattern FROM rules r
                    WHERE pattern != '' AND NOT EXISTS (SELECT 1 FROM rule_conditions c WHERE c.rule_id = r.id)""")
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        for name, kind, children in DEFAULT_CATEGORIES:
            pid = conn.execute("INSERT INTO categories(name, kind) VALUES (?, ?)", (name, kind)).lastrowid
            conn.executemany("INSERT INTO categories(name, kind, parent_id) VALUES (?, ?, ?)",
                             [(ch, kind, pid) for ch in children])
    conn.commit()


def ensure_initialised(path: Path | str) -> None:
    """Create/upgrade an organisation database once per process."""
    key = str(Path(path).resolve())
    if key in _initialised:
        return
    with connect(path) as c:
        init(c)
    _initialised.add(key)

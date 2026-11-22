"""Organisations: separate sets of accounts, each in its own SQLite file.

The registry of organisations lives in its own small database, data/orgs.db, which stores only
names and file locations — never any financial data. The first organisation is the original
data/finance.db, so existing data simply becomes the "Personal" organisation.
"""
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    db_file TEXT NOT NULL UNIQUE,          -- relative to the data folder (or absolute)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class OrgError(ValueError):
    pass


def registry_path() -> Path:
    return db.DATA_DIR / "orgs.db"


def _reg() -> sqlite3.Connection:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.executescript(REGISTRY_SCHEMA)
    return c


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(db.DATA_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(Path(path).resolve())


def path_of(org: sqlite3.Row | dict) -> Path:
    p = Path(org["db_file"])
    return p if p.is_absolute() else db.DATA_DIR / p


def ensure() -> None:
    """Create the registry; the first time, register the existing database as 'Personal'."""
    with _reg() as c:
        if c.execute("SELECT COUNT(*) FROM orgs").fetchone()[0] == 0:
            c.execute("INSERT INTO orgs(name, db_file) VALUES (?, ?)", ("Personal", _rel(db.DB_PATH)))


def all_orgs() -> list[sqlite3.Row]:
    with _reg() as c:
        return c.execute("SELECT * FROM orgs ORDER BY id").fetchall()


def get(org_id) -> sqlite3.Row | None:
    try:
        oid = int(org_id)
    except (TypeError, ValueError):
        return None
    with _reg() as c:
        return c.execute("SELECT * FROM orgs WHERE id = ?", (oid,)).fetchone()


def resolve(org_id) -> sqlite3.Row:
    """The requested organisation, or the first one if that id is missing/unknown."""
    return get(org_id) or all_orgs()[0]


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise OrgError("Give the organisation a name.")
    if len(name) > 60:
        raise OrgError("Keep the name under 60 characters.")
    return name


def create(name: str) -> sqlite3.Row:
    name = _clean_name(name)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "org"
    rel = f"orgs/{slug}-{secrets.token_hex(3)}.db"
    with _reg() as c:
        if c.execute("SELECT 1 FROM orgs WHERE name = ?", (name,)).fetchone():
            raise OrgError(f"There's already an organisation called “{name}”.")
        path = db.DATA_DIR / rel
        db.ensure_initialised(path)       # new database with the default category tree
        oid = c.execute("INSERT INTO orgs(name, db_file) VALUES (?, ?)", (name, rel)).lastrowid
        return c.execute("SELECT * FROM orgs WHERE id = ?", (oid,)).fetchone()


def rename(org_id: int, name: str) -> None:
    name = _clean_name(name)
    with _reg() as c:
        if c.execute("SELECT 1 FROM orgs WHERE name = ? AND id != ?", (name, org_id)).fetchone():
            raise OrgError(f"There's already an organisation called “{name}”.")
        if c.execute("UPDATE orgs SET name = ? WHERE id = ?", (name, org_id)).rowcount == 0:
            raise OrgError("Organisation not found.")


def delete(org_id: int, confirm_name: str) -> Path:
    """Remove an organisation from the app. Its database file is moved to data/deleted/ (not erased),
    so it can be recovered by hand. Returns where the file went."""
    org = get(org_id)
    if not org:
        raise OrgError("Organisation not found.")
    if (confirm_name or "").strip() != org["name"]:
        raise OrgError(f"Type the organisation name exactly (“{org['name']}”) to delete it.")
    if len(all_orgs()) <= 1:
        raise OrgError("You can't delete the only organisation.")
    src = path_of(org)
    dest_dir = db.DATA_DIR / "deleted"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{stamp}-{src.name}"
    # Fold the write-ahead log into the main file first so the moved file is complete on its own.
    if src.exists():
        with sqlite3.connect(src) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src.rename(dest)
    for ext in ("-wal", "-shm"):
        side = src.with_name(src.name + ext)
        if side.exists():
            side.unlink()
    with _reg() as c:
        c.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
    db._initialised.discard(str(src.resolve()))
    return dest


def summary(org: sqlite3.Row) -> dict:
    """Counts shown on the organisations page."""
    p = path_of(org)
    if not p.exists():
        return {"accounts": 0, "transactions": 0, "size_kb": 0}
    with db.connect(p) as c:
        a = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        t = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return {"accounts": a, "transactions": t, "size_kb": round(p.stat().st_size / 1024)}

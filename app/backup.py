"""Safe snapshot of the database (works while the app is running). Usage: python -m app.backup"""
from datetime import datetime

from . import db

if __name__ == "__main__":
    dest_dir = db.DATA_DIR / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"pfi-{datetime.now():%Y%m%d-%H%M%S}.db"
    with db.connect() as src:
        import sqlite3
        out = sqlite3.connect(dest)
        src.backup(out)
        out.close()
    print(f"Backup written to {dest}")

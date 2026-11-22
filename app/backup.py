"""Safe snapshot of every organisation's database plus the organisation list (works while the app
is running). Usage: python -m app.backup   ->  data/backups/<timestamp>/..."""
import sqlite3
from datetime import datetime

from . import db, orgs


def _copy(src, dest) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as s:
        out = sqlite3.connect(dest)
        s.backup(out)
        out.close()


def run():
    db.adopt_legacy_db()
    orgs.ensure()
    folder = db.DATA_DIR / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    _copy(orgs.registry_path(), folder / "orgs.db")
    written = []
    for o in orgs.all_orgs():
        src = orgs.path_of(o)
        if src.exists():
            from pathlib import Path
            dest = folder / src.name if Path(o["db_file"]).is_absolute() else folder / o["db_file"]
            _copy(src, dest)
            written.append((o["name"], dest))
    return folder, written


if __name__ == "__main__":
    folder, written = run()
    print(f"Backup written to {folder}")
    for name, path in written:
        print(f"  {name}: {path.relative_to(folder)}")
    print("To restore: stop the app and copy these files back into the data folder.")

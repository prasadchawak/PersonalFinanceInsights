"""Category tree (max 3 levels): loading, paths, roll-ups and safe edits."""
import sqlite3
from dataclasses import dataclass, field

MAX_DEPTH = 3
SEP = " › "


@dataclass
class Cat:
    id: int
    name: str
    kind: str
    parent_id: int | None
    depth: int = 1
    path: str = ""
    children: list["Cat"] = field(default_factory=list)
    ancestors: list[int] = field(default_factory=list)  # root first, excluding self
    excluded: bool = False            # marked "leave out of reports & budgets" itself
    excluded_by: str | None = None    # name of the parent it inherits exclusion from, if any

    @property
    def root_id(self) -> int:
        return self.ancestors[0] if self.ancestors else self.id


class Tree:
    def __init__(self, rows):
        self.by_id: dict[int, Cat] = {r["id"]: Cat(r["id"], r["name"], r["kind"], r["parent_id"],
                                                   excluded=bool(r["excluded"]) if "excluded" in r.keys() else False)
                                       for r in rows}
        self.roots: list[Cat] = []
        for c in self.by_id.values():
            parent = self.by_id.get(c.parent_id) if c.parent_id else None
            (parent.children if parent else self.roots).append(c)
        key = lambda c: ({"expense": 0, "income": 1, "transfer": 2}[c.kind], c.name.lower())
        self.roots.sort(key=key)

        def walk(c: Cat, ancestors: list[Cat]):
            c.depth = len(ancestors) + 1
            c.ancestors = [a.id for a in ancestors]
            c.path = SEP.join([a.name for a in ancestors] + [c.name])
            c.excluded_by = next((a.name for a in ancestors if a.excluded), None)
            c.children.sort(key=lambda x: x.name.lower())
            for ch in c.children:
                walk(ch, ancestors + [c])
        for r in self.roots:
            walk(r, [])

    def ordered(self) -> list[Cat]:
        """Depth-first, for indented <select> lists and tables."""
        out: list[Cat] = []

        def walk(c):
            out.append(c)
            for ch in c.children:
                walk(ch)
        for r in self.roots:
            walk(r)
        return out

    def descendants(self, cid: int) -> list[int]:
        """The category itself plus everything under it."""
        c = self.by_id.get(cid)
        if not c:
            return []
        out = [cid]
        for ch in c.children:
            out += self.descendants(ch.id)
        return out

    def height(self, cid: int) -> int:
        """Levels in this subtree, counting itself (a leaf is 1)."""
        c = self.by_id[cid]
        return 1 + max((self.height(ch.id) for ch in c.children), default=0)

    def at_level(self, cid: int | None, level: int) -> int | None:
        """Roll a category up to the given depth (1 = top level). Shallower categories stay as they are."""
        if cid is None or cid not in self.by_id:
            return None
        chain = self.by_id[cid].ancestors + [cid]
        return chain[min(level, len(chain)) - 1]


def load(conn: sqlite3.Connection) -> Tree:
    return Tree(conn.execute("SELECT id, name, kind, parent_id, excluded FROM categories").fetchall())


class CategoryError(ValueError):
    pass


def _name_taken(conn, name: str, parent_id: int | None, exclude: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM categories WHERE COALESCE(parent_id,0) = ? AND name = ? COLLATE NOCASE AND id != ?",
        (parent_id or 0, name, exclude or -1)).fetchone()
    return row is not None


def create(conn, name: str, kind: str, parent_id: int | None) -> int:
    name = name.strip()
    if not name:
        raise CategoryError("Name can't be empty.")
    tree = load(conn)
    if parent_id:
        parent = tree.by_id.get(parent_id)
        if not parent:
            raise CategoryError("Parent category not found.")
        if parent.depth >= MAX_DEPTH:
            raise CategoryError(f"Categories can only be {MAX_DEPTH} levels deep.")
        kind = parent.kind  # children always share the parent's kind
    if kind not in ("expense", "income", "transfer"):
        raise CategoryError("Bad kind.")
    if _name_taken(conn, name, parent_id):
        raise CategoryError(f"“{name}” already exists there.")
    return conn.execute("INSERT INTO categories(name, kind, parent_id) VALUES (?,?,?)",
                        (name, kind, parent_id)).lastrowid


def rename(conn, cid: int, name: str) -> None:
    name = name.strip()
    row = conn.execute("SELECT parent_id FROM categories WHERE id = ?", (cid,)).fetchone()
    if not row:
        raise CategoryError("Category not found.")
    if not name:
        raise CategoryError("Name can't be empty.")
    if _name_taken(conn, name, row["parent_id"], exclude=cid):
        raise CategoryError(f"“{name}” already exists there.")
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))


def move(conn, cid: int, new_parent_id: int | None, kind: str | None = None) -> None:
    """Move a category (with everything under it) under another parent, or to the top level."""
    tree = load(conn)
    c = tree.by_id.get(cid)
    if not c:
        raise CategoryError("Category not found.")
    if new_parent_id:
        if new_parent_id in tree.descendants(cid):
            raise CategoryError("A category can't be moved inside itself.")
        parent = tree.by_id.get(new_parent_id)
        if not parent:
            raise CategoryError("Parent category not found.")
        if parent.depth + tree.height(cid) > MAX_DEPTH:
            raise CategoryError(f"That would make it more than {MAX_DEPTH} levels deep.")
        new_kind = parent.kind
    else:
        new_kind = kind if kind in ("expense", "income", "transfer") else c.kind
    if _name_taken(conn, c.name, new_parent_id, exclude=cid):
        raise CategoryError(f"There's already a “{c.name}” there — rename one first.")
    conn.execute("UPDATE categories SET parent_id = ? WHERE id = ?", (new_parent_id, cid))
    ids = tree.descendants(cid)
    conn.execute(f"UPDATE categories SET kind = ? WHERE id IN ({','.join('?' * len(ids))})", (new_kind, *ids))


def delete(conn, cid: int) -> None:
    """Delete one category. Its children move up to its parent, and its transactions and rules
    move to its parent (or become uncategorised if it was top-level)."""
    row = conn.execute("SELECT parent_id, name FROM categories WHERE id = ?", (cid,)).fetchone()
    if not row:
        return
    parent = row["parent_id"]
    for ch in conn.execute("SELECT id, name FROM categories WHERE parent_id = ?", (cid,)).fetchall():
        if _name_taken(conn, ch["name"], parent, exclude=ch["id"]):
            raise CategoryError(f"Can't delete: “{ch['name']}” would clash with an existing category. Rename it first.")
    conn.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (parent, cid))
    if parent:
        conn.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (parent, cid))
        conn.execute("UPDATE rules SET category_id = ? WHERE category_id = ?", (parent, cid))
    else:
        conn.execute("UPDATE transactions SET category_id = NULL, category_locked = 0 WHERE category_id = ?", (cid,))
        conn.execute("UPDATE rules SET category_id = NULL WHERE category_id = ?", (cid,))
        conn.execute("DELETE FROM rules WHERE category_id IS NULL AND rename_to IS NULL")
    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))

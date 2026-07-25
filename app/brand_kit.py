"""Brand Kit — the single source of truth for a business/site's branding.

Multiple brands, each a persistent reusable package: name, slug, tagline,
short/long descriptions, colors, style notes, links, and image assets
(logo + banner) that live under BRANDS_DIR/<brand_id>/. Instead of
regenerating a new logo/description every time one is needed, other flows
(and the owner) pull the saved package from here.

Schema is self-managed in ensure() (idempotent CREATE + ALTER guards) — the
world_bible.py convention. NOTHING here touches db_schema.py. Asset files live
in the gitignored media dir BRANDS_DIR (repo-root brands/ by default, or under
STORE_DATA_DIR like designs/videos).
"""
import json
import re
import shutil
import time
from pathlib import Path

from config import DATA_DIR
from db import get_conn

# Media dir for brand image assets (logo/banner) — gitignored, one subdir per
# brand id so renames never orphan files. Add `brands/` to .gitignore.
BRANDS_DIR = DATA_DIR / "brands"

ASSET_KINDS = ("logo", "banner")

# JSON-typed columns (stored as TEXT, parsed on read / serialized on write)
_JSON_COLS = ("colors", "links", "meta")


# ── schema ────────────────────────────────────────────────────────────────────
def ensure(conn=None):
    """Create the brands table if missing (+ additive ALTER guards). Idempotent."""
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS brands (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            slug              TEXT NOT NULL UNIQUE,
            tagline           TEXT DEFAULT '',
            description_short TEXT DEFAULT '',
            description_long  TEXT DEFAULT '',
            colors            TEXT DEFAULT '{}',   -- JSON {primary,secondary,accent} hex
            style_notes       TEXT DEFAULT '',
            links             TEXT DEFAULT '{}',   -- JSON {website, twitter, instagram, …}
            logo_path         TEXT,
            banner_path       TEXT,
            is_default        INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now')),
            meta              TEXT DEFAULT '{}'    -- JSON scratch (banner preset, gen params, …)
        );""")
        # additive guards for columns added after first ship (existing rows keep defaults)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(brands)").fetchall()}
        for col, ddl in (("logo_status",   "TEXT"),   # queued | generating | done | failed
                         ("logo_error",    "TEXT"),
                         ("banner_status", "TEXT"),
                         ("banner_error",  "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE brands ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        if own:
            conn.close()


# ── helpers ───────────────────────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60] or "brand"


def _unique_slug(conn, base: str, exclude_id=None) -> str:
    slug = base
    n = 2
    while True:
        if exclude_id is None:
            row = conn.execute("SELECT id FROM brands WHERE slug=?", (slug,)).fetchone()
        else:
            row = conn.execute("SELECT id FROM brands WHERE slug=? AND id<>?",
                               (slug, exclude_id)).fetchone()
        if not row:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _parse(row) -> dict:
    """sqlite Row → dict with JSON columns parsed to objects."""
    d = dict(row)
    for k in _JSON_COLS:
        try:
            v = json.loads(d.get(k) or "{}")
            d[k] = v if isinstance(v, dict) else {}
        except Exception:
            d[k] = {}
    return d


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_brands() -> list:
    ensure()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM brands ORDER BY is_default DESC, id DESC").fetchall()
        return [_parse(r) for r in rows]
    finally:
        conn.close()


def get_brand(brand_id: int):
    """Full brand package as a dict (JSON columns parsed), or None."""
    ensure()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        return _parse(row) if row else None
    finally:
        conn.close()


def create_brand(name: str, fields: dict | None = None) -> int:
    """Insert a brand; `fields` may carry tagline/descriptions/colors/links/…
    JSON-typed values may be dicts (serialized here). Returns the new id."""
    ensure()
    fields = dict(fields or {})
    conn = get_conn()
    try:
        slug = _unique_slug(conn, slugify(fields.pop("slug", "") or name))
        cols, vals = ["name", "slug"], [name.strip(), slug]
        for k in ("tagline", "description_short", "description_long", "style_notes"):
            if fields.get(k) is not None:
                cols.append(k)
                vals.append(str(fields[k]))
        for k in _JSON_COLS:
            if fields.get(k) is not None:
                v = fields[k]
                cols.append(k)
                vals.append(json.dumps(v) if isinstance(v, dict) else str(v))
        cur = conn.execute(
            f"INSERT INTO brands ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals)
        # first brand ever → it is the default
        if conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0] == 1:
            conn.execute("UPDATE brands SET is_default=1 WHERE id=?", (cur.lastrowid,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_brand(brand_id: int, fields: dict) -> bool:
    """Patch the given columns (dicts for JSON columns are serialized). A name
    change does NOT silently change the slug; pass slug explicitly to re-slug."""
    ensure()
    updates = {}
    conn = get_conn()
    try:
        for k, v in (fields or {}).items():
            if v is None:
                continue
            if k == "slug":
                updates["slug"] = _unique_slug(conn, slugify(str(v)), exclude_id=brand_id)
            elif k in _JSON_COLS:
                updates[k] = json.dumps(v) if isinstance(v, dict) else str(v)
            elif k in ("name", "tagline", "description_short", "description_long",
                       "style_notes"):
                updates[k] = str(v)
        if not updates:
            return True
        sets = ",".join(f"{k}=?" for k in updates)
        cur = conn.execute(
            f"UPDATE brands SET {sets},updated_at=datetime('now') WHERE id=?",
            (*updates.values(), brand_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_brand(brand_id: int) -> bool:
    """Delete the row AND its asset files (the whole brands/<id>/ dir)."""
    ensure()
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM brands WHERE id=?", (brand_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM brands WHERE id=?", (brand_id,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(brand_dir(brand_id, create=False), ignore_errors=True)
    return True


def set_default(brand_id: int) -> bool:
    ensure()
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM brands WHERE id=?", (brand_id,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE brands SET is_default=0 WHERE is_default=1")
        conn.execute("UPDATE brands SET is_default=1,updated_at=datetime('now') "
                     "WHERE id=?", (brand_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ── assets ────────────────────────────────────────────────────────────────────
def brand_dir(brand_id: int, create: bool = True) -> Path:
    d = BRANDS_DIR / str(int(brand_id))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def new_asset_path(brand_id: int, kind: str, ext: str = ".png") -> Path:
    """A fresh timestamped file path for a logo/banner (old files are removed
    when the new one is committed via set_asset)."""
    if kind not in ASSET_KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    import uuid
    return brand_dir(brand_id) / f"{kind}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"


def asset_path(brand: dict, kind: str):
    """The stored Path for a brand dict's logo/banner, or None. Only trusts
    files that actually live under this brand's own asset dir."""
    p = (brand or {}).get(f"{kind}_path")
    if not p:
        return None
    path = Path(p)
    try:
        ok_dir = str(path.resolve()).startswith(str(brand_dir(brand["id"], create=False).resolve()) + "/")
    except Exception:
        ok_dir = False
    return path if (ok_dir and path.is_file()) else None


def set_asset(brand_id: int, kind: str, path: str):
    """Commit a new logo/banner: update the path, mark done, delete the old file."""
    if kind not in ASSET_KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    ensure()
    conn = get_conn()
    try:
        row = conn.execute(f"SELECT {kind}_path FROM brands WHERE id=?",
                           (brand_id,)).fetchone()
        old = row[f"{kind}_path"] if row else None
        conn.execute(
            f"UPDATE brands SET {kind}_path=?,{kind}_status='done',{kind}_error=NULL,"
            "updated_at=datetime('now') WHERE id=?", (str(path), brand_id))
        conn.commit()
    finally:
        conn.close()
    if old and old != str(path):
        try:
            op = Path(old)
            if str(op.resolve()).startswith(str(BRANDS_DIR.resolve()) + "/"):
                op.unlink(missing_ok=True)
        except Exception:
            pass


def set_asset_status(brand_id: int, kind: str, status: str, error=None):
    if kind not in ASSET_KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    ensure()
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE brands SET {kind}_status=?,{kind}_error=?,"
            "updated_at=datetime('now') WHERE id=?",
            (status, (str(error)[:500] if error else None), brand_id))
        conn.commit()
    finally:
        conn.close()

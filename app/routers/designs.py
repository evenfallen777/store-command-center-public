"""designs routes."""
import logging
import os
from pathlib import Path as _Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from fastapi.responses import FileResponse
from deps import *
from services import *
from config import DATA_DIR, BASE, MODELS3D_RENDERS, MODELS3D_HERO, VIDEOS_DIR, RESELL_UPLOADS

router = APIRouter()

_log = logging.getLogger("store")
_THUMB_MAX = 400
_THUMB_SUBS = {"pending", "approved", "rejected"}
_IMMUTABLE = "public, max-age=31536000, immutable"
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Explicit allowlist of on-disk roots the general /api/thumb route may read from.
# NOTHING outside these dirs can ever be thumbnailed (no db, code, secrets, configs).
# Computed as realpaths once so the containment check below is cheap + truthful.
_THUMB_ROOTS = [os.path.realpath(str(p)) for p in (
    DATA_DIR / "designs",   # pipeline design images (pending/approved/rejected)
    MODELS3D_RENDERS,       # 3D turntable render PNGs
    MODELS3D_HERO,          # 3D AI hero/marketing PNGs
    VIDEOS_DIR,             # video posters / stills, if any
    RESELL_UPLOADS,         # resell listing photos (BASE/static/resell_uploads)
)]


def _webp_thumb(src: _Path, max_px: int) -> _Path:
    """Generate (if stale/missing) and return a cached <=max_px WebP thumbnail for `src`.
    Cached to <parent>/thumbs/<stem>.webp at the default width, <stem>_<max_px>.webp
    otherwise, so different widths never clobber (and the default cache stays compatible)."""
    stem = src.stem if max_px == _THUMB_MAX else f"{src.stem}_{max_px}"
    thumb = src.parent / "thumbs" / f"{stem}.webp"
    if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
        from PIL import Image
        thumb.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
            im.thumbnail((max_px, max_px))
            im.save(thumb, "WEBP", quality=80, method=4)
    return thumb


@router.get("/thumb/{sub}/{filename}")
def design_thumbnail(sub: str, filename: str):
    """Serve a small (<=400px) WebP thumbnail for a design image, generated on demand
    and cached to disk (designs/<sub>/thumbs/) + the browser (immutable). Gallery grids
    use this; the lightbox still loads the full-res original. Cuts a picture-heavy tab
    from tens of MB down to a few hundred KB. Falls back to the full image on any error."""
    if sub not in _THUMB_SUBS or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(404, "Not found")
    src = DATA_DIR / "designs" / sub / filename
    if not src.is_file():
        raise HTTPException(404, "Not found")
    try:
        thumb = _webp_thumb(src, _THUMB_MAX)
        return FileResponse(str(thumb), media_type="image/webp",
                            headers={"Cache-Control": _IMMUTABLE})
    except Exception:
        _log.exception("thumbnail failed for %s/%s — serving full image", sub, filename)
        return FileResponse(str(src), headers={"Cache-Control": _IMMUTABLE})


@router.get("/api/thumb")
def general_thumbnail(path: str, w: int = _THUMB_MAX):
    """Serve a cached WebP thumbnail for ANY local image that lives under an allowlisted
    root (_THUMB_ROOTS: designs/, models3d/renders+hero/, videos/, static/resell_uploads/).
    `path` is relative to the app root (BASE). Security: the path is joined to BASE, fully
    resolved with realpath (following symlinks), and REJECTED unless os.path.commonpath
    proves it sits inside one of the allowlisted roots — so `..`, absolute paths, and
    symlink escapes can never reach db/code/secrets. Non-image extensions are refused too.
    Falls back to the full image on any decode error; 404 for anything outside the allowlist."""
    # Cheap up-front rejects (defence in depth; the realpath containment check below is authoritative).
    if not path or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
        raise HTTPException(404, "Not found")
    w = max(16, min(int(w), 1024))
    cand = os.path.realpath(os.path.join(str(BASE), path))
    # Realpath containment: cand must be strictly inside an allowlisted root.
    inside = False
    for root in _THUMB_ROOTS:
        try:
            if os.path.commonpath([cand, root]) == root:
                inside = True
                break
        except ValueError:
            continue  # different drive / relativity mismatch → not contained
    if not inside:
        raise HTTPException(404, "Not found")
    src = _Path(cand)
    if src.suffix.lower() not in _IMG_EXTS or not src.is_file():
        raise HTTPException(404, "Not found")
    try:
        thumb = _webp_thumb(src, w)
        return FileResponse(str(thumb), media_type="image/webp",
                            headers={"Cache-Control": _IMMUTABLE})
    except Exception:
        _log.exception("general thumbnail failed for %s — serving full image", path)
        return FileResponse(str(src), headers={"Cache-Control": _IMMUTABLE})


@router.get("/api/designs")
def list_designs(status: str = "review", source: Optional[str] = None):
    # nsfw designs are redacted from every listing; querying source='nsfw' directly
    # is only allowed when the NSFW master AND display toggles are both on.
    import nsfw as _nsfw
    if source == "nsfw" and not _nsfw.visible():
        raise HTTPException(404, "Not found")
    conn = get_conn()
    _sfw = "COALESCE(d.nsfw,0)=0"
    # Approved tab shows both approved and published (published = approved + on Printify)
    if status == "approved":
        where = f"d.status IN ('approved','published') AND (d.source IS NULL OR d.source='pipeline') AND {_sfw}"
        params = ()
    elif source:
        where = "d.status=? AND d.source=?" + ("" if source == "nsfw" else f" AND {_sfw}")
        params = (status, source)
    else:
        # Default review: exclude generator-sourced designs
        where = f"d.status=? AND (d.source IS NULL OR d.source='pipeline') AND {_sfw}"
        params = (status,)
    rows = conn.execute(f"""
        SELECT d.*,
               p.title       AS proposal_title,
               p.tags        AS proposal_tags,
               p.description AS proposal_description
        FROM designs d
        LEFT JOIN generations g ON g.id = d.generation_id
        LEFT JOIN proposals   p ON p.id = g.proposal_id
        WHERE {where} ORDER BY d.created_at DESC
    """, params).fetchall()
    # Attach already-published product_types for each design (same image_path, published + printify_id set)
    result = []
    for r in rows:
        d = dict(r)
        siblings = conn.execute(
            "SELECT product_type FROM designs WHERE image_path=? AND status='published' AND printify_id IS NOT NULL",
            (d["image_path"],)
        ).fetchall()
        d["published_types"] = [s["product_type"] for s in siblings]
        result.append(d)
    conn.close()
    return result

@router.get("/api/designs/{design_id}/export")
def export_design(design_id: int, spec: str = "etsy"):
    """Serve a publish-ready size variant of a design as a download.
    spec='etsy' → 1024²/≤5MB JPEG; 'web' → 1600px medium. Re-derives from the current
    master (correct even after pending→approved moves) and caches to designs/sizes/."""
    import image_sizes
    conn = get_conn()
    row = conn.execute("SELECT image_path FROM designs WHERE id=?", (design_id,)).fetchone()
    conn.close()
    if not row or not row["image_path"]:
        raise HTTPException(404, "design not found")
    try:
        out = image_sizes.one(row["image_path"], spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not out:
        raise HTTPException(404, "source image missing")
    fname = f"design_{design_id}_{spec}.{image_sizes.SPECS[spec]['ext']}"
    return FileResponse(str(out), media_type="image/jpeg",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


class ApproveDesignRequest(BaseModel):
    product_types: list = ["T-Shirt"]

@router.patch("/api/designs/{design_id}/approve")
def approve_design(design_id: int, req: ApproveDesignRequest):
    conn = get_conn()
    row = conn.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Design not found")
    src = Path(row["image_path"])
    dst = DESIGNS_APPROVED / src.name
    first_type = req.product_types[0] if req.product_types else "T-Shirt"
    # Commit DB first — if DB fails, the file is untouched and the user can retry.
    # Moving the file before committing caused a race where the file disappeared
    # from pending but the design stayed 'review' (invisible broken card).
    conn.execute(
        "UPDATE designs SET status='approved',product_type=?,image_path=?,updated_at=datetime('now') WHERE id=?",
        (first_type, str(dst), design_id)
    )
    for ptype in req.product_types[1:]:
        conn.execute(
            "INSERT INTO designs (generation_id,image_path,prompt,product_type,status) VALUES (?,?,?,?,'approved')",
            (row["generation_id"], str(dst), row["prompt"], ptype)
        )
    conn.commit()
    conn.close()
    # Move file after DB is committed. If the move fails the DB already reflects
    # 'approved' with the destination path; serve will fall back gracefully and
    # the file is still in pending (safe to retry / manually fix).
    if src.exists():
        try:
            shutil.move(str(src), str(dst))
        except Exception as e:
            logger.error("approve_design: file move failed for design %d: %s", design_id, e)
    return {"ok": True, "product_types": req.product_types}

@router.delete("/api/designs/{design_id}")
def delete_design(design_id: int):
    """Permanently delete a rejected design from DB and disk."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Design not found")
    for path_str in [row["image_path"]]:
        if path_str:
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink()
            except Exception:
                pass
    conn.execute("DELETE FROM designs WHERE id=?", (design_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.patch("/api/designs/{design_id}/reject")
def reject_design(design_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Design not found")
    # Idempotent — already rejected
    if row["status"] == "rejected":
        conn.close()
        return {"ok": True}
    src = Path(row["image_path"])
    dst = DESIGNS_REJECTED / src.name
    try:
        if src.exists():
            shutil.move(str(src), str(dst))
    except (FileNotFoundError, OSError):
        pass  # Already moved or missing — still mark rejected in DB
    conn.execute(
        "UPDATE designs SET status='rejected',image_path=?,updated_at=datetime('now') WHERE id=?",
        (str(dst), design_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@router.patch("/api/designs/{design_id}/send-to-review")
def send_generator_design_to_review(design_id: int):
    """Move a generator-sourced design into the main pipeline review queue."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Design not found")
    conn.execute(
        "UPDATE designs SET source='pipeline',updated_at=datetime('now') WHERE id=?",
        (design_id,)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@router.post("/api/designs/{design_id}/regen")
def regen_design(design_id: int, background_tasks: BackgroundTasks):
    conn = get_conn()
    row = conn.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    model = _resolve_model(conn, None)
    cur = conn.execute(
        "INSERT INTO generations (prompt,product_type,model) VALUES (?,?,?)",
        (row["prompt"], row["product_type"], model)
    )
    gid = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(run_generation, gid)
    return {"ok": True, "generation_id": gid}

@router.delete("/api/designs/{design_id}/unpublish")
def unpublish_design(design_id: int):
    """Remove a design from Printify and mark it approved (unpublished) in the DB."""
    conn = get_conn()
    row = conn.execute("SELECT printify_id FROM designs WHERE id=?", (design_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Design not found")
    printify_id = row["printify_id"]
    if printify_id:
        try:
            client = _get_printify()
            client.delete_product(printify_id)
        except Exception as e:
            print(f"Printify delete failed (continuing DB update): {e}")
    conn = get_conn()
    conn.execute(
        "UPDATE designs SET status='approved', printify_id=NULL, updated_at=datetime('now') WHERE id=?",
        (design_id,)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Design unpublished"}

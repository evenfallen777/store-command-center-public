"""Brand Kit routes — create/manage persistent brand packages (see brand_kit.py).

Generation policy: image assets (logo/banner) ride the unified GPU queue via
orch.image_acquire/image_release around GENERATE_SCRIPT (the exact
services.run_generation pattern); identity text rides orch.submit_llm →
poll /api/task/{id}. Nothing heavy ever runs in-request. The safety floor
(nsfw.refuse_unsafe) screens every owner-supplied prompt unconditionally.
"""
import io
import zipfile

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional

from deps import *          # orch, get_conn, GENERATE_SCRIPT, DEFAULT_IMAGE_MODEL, _call_lmstudio, logger, …
import nsfw
import brand_kit as bk

router = APIRouter()

# Banner presets: exact deliverable size. Diffusion runs at an aspect-matched,
# /8-aligned working size (≤1024 on the long side — the node's SDXL sweet spot);
# the render is then Lanczos-resized to the exact preset dimensions.
BANNER_PRESETS = {
    "x_header": {"label": "X / Twitter header", "w": 1500, "h": 500},
    "og":       {"label": "Open Graph / social card", "w": 1200, "h": 630},
    "youtube":  {"label": "YouTube channel banner", "w": 2560, "h": 1440},
}

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
_UPLOAD_MAX = 20 * 1024 * 1024   # 20 MB


# ── request bodies ────────────────────────────────────────────────────────────

class BrandCreate(BaseModel):
    name: str
    tagline: str = ""
    description_short: str = ""
    description_long: str = ""
    style_notes: str = ""
    colors: dict = {}
    links: dict = {}


class BrandPatch(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    tagline: Optional[str] = None
    description_short: Optional[str] = None
    description_long: Optional[str] = None
    style_notes: Optional[str] = None
    colors: Optional[dict] = None
    links: Optional[dict] = None
    meta: Optional[dict] = None


class LogoGenReq(BaseModel):
    prompt: str = ""            # optional extra art direction
    model: str = ""             # image checkpoint (default: the store default)
    steps: int = 20
    size: int = 1024            # square


class BannerGenReq(BaseModel):
    preset: str = "x_header"    # x_header | og | youtube
    prompt: str = ""
    model: str = ""
    steps: int = 20


class IdentityReq(BaseModel):
    prompt: str = ""            # what the business is / any direction for the writer


# ── helpers ───────────────────────────────────────────────────────────────────

def _brand_or_404(brand_id: int) -> dict:
    b = bk.get_brand(brand_id)
    if not b:
        raise HTTPException(404, "Brand not found")
    return b


def _brand_context(b: dict) -> str:
    """One text block describing the brand — the shared grounding for every
    generator so all assets stay ON-brand instead of drifting."""
    bits = [f"Brand name: {b['name']}"]
    if b.get("tagline"):
        bits.append(f"Tagline: {b['tagline']}")
    desc = b.get("description_short") or b.get("description_long") or ""
    if desc:
        bits.append(f"About: {desc[:400]}")
    if b.get("style_notes"):
        bits.append(f"Style notes: {b['style_notes'][:300]}")
    colors = b.get("colors") or {}
    cbits = [f"{k} {v}" for k, v in colors.items() if v]
    if cbits:
        bits.append("Brand colors: " + ", ".join(cbits))
    return "\n".join(bits)


def _snap8(n: int) -> int:
    return max(64, int(round(n / 8)) * 8)


def _gen_size(target_w: int, target_h: int) -> tuple:
    """Aspect-matched diffusion working size, long side ≤1024, /8-aligned."""
    if target_w >= target_h:
        w = min(1024, target_w)
        h = _snap8(w * target_h / target_w)
        return _snap8(w), h
    h = min(1024, target_h)
    w = _snap8(h * target_w / target_h)
    return w, _snap8(h)


def _run_brand_asset(brand_id: int, kind: str, prompt: str, gen_w: int, gen_h: int,
                     target: tuple | None, model: str, steps: int):
    """Background worker: generate one brand image on the unified GPU queue —
    the services.run_generation pattern (image_acquire → GENERATE_SCRIPT →
    image_release), writing into the brand's own asset dir."""
    try:
        # Same primitive every image generator uses: waits for LLM work to
        # drain, unloads the LLM from VRAM, serializes against other jobs.
        orch.image_acquire(desc=f"Brand {kind} #{brand_id}", priority=0)
    except Exception as ex:
        bk.set_asset_status(brand_id, kind, "failed", f"GPU acquire failed: {ex}")
        return
    try:
        bk.set_asset_status(brand_id, kind, "generating")
        out_path = bk.new_asset_path(brand_id, kind, ".png")
        seed = str(random.randint(1, 2**31 - 1))
        result = subprocess.run(
            [str(GENERATE_SCRIPT), prompt, str(out_path),
             str(gen_w), str(gen_h), str(steps), seed,
             model or DEFAULT_IMAGE_MODEL, "", "", ""],
            capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not out_path.exists():
            err = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()[-300:] \
                  or "generation failed"
            logger.error("Brand %d %s generation failed: %s", brand_id, kind, err)
            bk.set_asset_status(brand_id, kind, "failed", err)
            return
        if target and (gen_w, gen_h) != tuple(target):
            try:   # exact deliverable size (banner presets) — best-effort
                from PIL import Image
                im = Image.open(out_path).convert("RGB")
                im.resize(tuple(target), Image.LANCZOS).save(out_path)
            except Exception as ex:
                logger.warning("Brand %d %s resize skipped: %s", brand_id, kind, ex)
        bk.set_asset(brand_id, kind, str(out_path))
        logger.info("Brand %d %s done: %s", brand_id, kind, out_path)
    except subprocess.TimeoutExpired:
        bk.set_asset_status(brand_id, kind, "failed",
                            "Timed out after 5 min — try fewer steps")
    except Exception as ex:
        logger.error("Brand %d %s exception: %s", brand_id, kind, ex)
        bk.set_asset_status(brand_id, kind, "failed", str(ex)[:300])
    finally:
        orch.image_release()   # always — same policy as run_generation


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/api/brands")
def list_brands_ep():
    return bk.list_brands()


@router.get("/api/brands/presets")
def banner_presets():
    return [{"key": k, **v} for k, v in BANNER_PRESETS.items()]


@router.post("/api/brands")
def create_brand_ep(req: BrandCreate):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "Give the brand a name")
    nsfw.refuse_unsafe(name, req.tagline, req.description_short, req.description_long)
    bid = bk.create_brand(name, {
        "tagline": req.tagline.strip(),
        "description_short": req.description_short.strip(),
        "description_long": req.description_long.strip(),
        "style_notes": req.style_notes.strip(),
        "colors": req.colors or {},
        "links": req.links or {},
    })
    return bk.get_brand(bid)


@router.get("/api/brands/{brand_id}")
def get_brand_ep(brand_id: int):
    return _brand_or_404(brand_id)


@router.patch("/api/brands/{brand_id}")
def patch_brand_ep(brand_id: int, req: BrandPatch):
    _brand_or_404(brand_id)
    fields = req.dict()
    if fields.get("name") is not None and not fields["name"].strip():
        raise HTTPException(400, "name cannot be empty")
    nsfw.refuse_unsafe(*[v for v in fields.values() if isinstance(v, str)])
    bk.update_brand(brand_id, fields)
    return bk.get_brand(brand_id)


@router.delete("/api/brands/{brand_id}")
def delete_brand_ep(brand_id: int):
    if not bk.delete_brand(brand_id):
        raise HTTPException(404, "Brand not found")
    return {"ok": True}


@router.post("/api/brands/{brand_id}/set-default")
def set_default_ep(brand_id: int):
    if not bk.set_default(brand_id):
        raise HTTPException(404, "Brand not found")
    return {"ok": True}


# ── generation (unified GPU queue) ────────────────────────────────────────────

@router.post("/api/brands/{brand_id}/generate/logo")
def generate_logo_ep(brand_id: int, req: LogoGenReq, background_tasks: BackgroundTasks):
    """Queue a square logo render (GPU queue). Poll GET /api/brands/{id} —
    logo_status goes queued → generating → done|failed."""
    b = _brand_or_404(brand_id)
    if b.get("logo_status") in ("queued", "generating"):
        raise HTTPException(409, "A logo is already generating for this brand")
    nsfw.refuse_unsafe(req.prompt)
    extra = (req.prompt or "").strip()
    prompt = (f"minimalist vector logo for \"{b['name']}\", professional brand mark, "
              f"flat design, clean simple shapes, centered on a plain background")
    ctx = _brand_context(b)
    if ctx:
        prompt += ". Brand context: " + ctx.replace("\n", "; ")
    if extra:
        prompt += ". Art direction: " + extra
    size = _snap8(min(max(int(req.size or 1024), 512), 1536))
    bk.set_asset_status(brand_id, "logo", "queued")
    background_tasks.add_task(_run_brand_asset, brand_id, "logo", prompt,
                              size, size, None, req.model.strip(),
                              min(max(int(req.steps or 20), 5), 60))
    return {"ok": True, "status": "queued"}


@router.post("/api/brands/{brand_id}/generate/banner")
def generate_banner_ep(brand_id: int, req: BannerGenReq, background_tasks: BackgroundTasks):
    """Queue a wide banner render at a preset size (GPU queue). Poll the brand —
    banner_status goes queued → generating → done|failed."""
    b = _brand_or_404(brand_id)
    if b.get("banner_status") in ("queued", "generating"):
        raise HTTPException(409, "A banner is already generating for this brand")
    preset = BANNER_PRESETS.get((req.preset or "").strip().lower())
    if not preset:
        raise HTTPException(400, f"preset must be one of: {', '.join(BANNER_PRESETS)}")
    nsfw.refuse_unsafe(req.prompt)
    extra = (req.prompt or "").strip()
    prompt = (f"wide banner artwork for the brand \"{b['name']}\", cohesive branded "
              f"header image, professional, high quality, no text")
    ctx = _brand_context(b)
    if ctx:
        prompt += ". Brand context: " + ctx.replace("\n", "; ")
    if extra:
        prompt += ". Art direction: " + extra
    gen_w, gen_h = _gen_size(preset["w"], preset["h"])
    # remember which preset the banner is (export metadata / UI label)
    meta = dict(b.get("meta") or {})
    meta["banner_preset"] = req.preset
    bk.update_brand(brand_id, {"meta": meta})
    bk.set_asset_status(brand_id, "banner", "queued")
    background_tasks.add_task(_run_brand_asset, brand_id, "banner", prompt,
                              gen_w, gen_h, (preset["w"], preset["h"]),
                              req.model.strip(), min(max(int(req.steps or 20), 5), 60))
    return {"ok": True, "status": "queued", "preset": req.preset,
            "size": [preset["w"], preset["h"]]}


_IDENTITY_SYS = """You are a senior brand copywriter. From the brand information given, write brand identity copy.
Respond with ONLY a JSON object, no prose, in exactly this shape:
{"names": ["3-5 alternative brand name ideas"],
 "tagline": "a punchy tagline under 10 words",
 "description_short": "1-2 sentences (under 200 characters) describing the brand",
 "description_long": "2-3 paragraphs telling the brand story, what it offers and why it matters"}
Keep the voice consistent with any style notes given. Never invent facts (locations, awards, dates)."""


@router.post("/api/brands/{brand_id}/generate/description")
def generate_description_ep(brand_id: int, req: IdentityReq):
    """LLM writes name ideas + tagline + short/long descriptions from the brand
    info (+ an optional prompt), on the unified queue. Returns {task_id}; poll
    /api/task/{id} → result {names, tagline, description_short, description_long}.
    Nothing is auto-saved — the owner reviews and applies in the UI."""
    b = _brand_or_404(brand_id)
    extra = (req.prompt or "").strip()
    nsfw.refuse_unsafe(extra)
    user = _brand_context(b)
    if extra:
        user += f"\nOwner's brief: {extra}"
    if not user.strip():
        raise HTTPException(400, "Add some brand info or a prompt first")

    def _work():
        raw = _call_lmstudio(_IDENTITY_SYS, user, max_tokens=1800)
        import re as _re
        cleaned = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
        mo = _re.search(r"\{.*\}", cleaned, _re.DOTALL)
        if mo:
            try:
                data = json.loads(mo.group())
            except Exception:
                data = None
            if isinstance(data, dict):
                names = data.get("names")
                return {
                    "names": [str(n).strip() for n in names][:5] if isinstance(names, list) else [],
                    "tagline": str(data.get("tagline") or "").strip(),
                    "description_short": str(data.get("description_short") or "").strip(),
                    "description_long": str(data.get("description_long") or "").strip(),
                }
        # fallback: the whole reply as the long description
        return {"names": [], "tagline": "", "description_short": "",
                "description_long": cleaned[:2000]}

    tid = orch.submit_llm(_work, desc=f"Brand identity: {b['name'][:40]}",
                          priority=0, source="brand_kit")   # user waiting
    return {"task_id": tid}


# ── bring-your-own assets ─────────────────────────────────────────────────────

@router.post("/api/brands/{brand_id}/upload/{asset}")
async def upload_asset_ep(brand_id: int, asset: str, file: UploadFile = File(...)):
    """Upload your own logo/banner — stored exactly like the generated ones."""
    _brand_or_404(brand_id)
    if asset not in bk.ASSET_KINDS:
        raise HTTPException(400, f"asset must be one of: {', '.join(bk.ASSET_KINDS)}")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _IMG_EXT:
        raise HTTPException(400, f"Use one of: {', '.join(sorted(_IMG_EXT))}")
    dest = bk.new_asset_path(brand_id, asset, ext)
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > _UPLOAD_MAX:
                    raise HTTPException(400, "File too large (max 20 MB)")
                f.write(chunk)
        if not size:
            raise HTTPException(400, "That file was empty")
        if ext != ".svg":     # raster files must actually be images
            try:
                from PIL import Image
                Image.open(dest).verify()
            except Exception:
                raise HTTPException(400, "That file is not a readable image")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    bk.set_asset(brand_id, asset, str(dest))
    return {"ok": True, "path": dest.name, "size": size}


# ── download / export ─────────────────────────────────────────────────────────

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".svg": "image/svg+xml"}


@router.get("/api/brands/{brand_id}/download/{asset}")
def download_asset_ep(brand_id: int, asset: str, inline: int = 0):
    """Download (or inline-preview with ?inline=1) the saved logo/banner file."""
    b = _brand_or_404(brand_id)
    if asset not in bk.ASSET_KINDS:
        raise HTTPException(400, f"asset must be one of: {', '.join(bk.ASSET_KINDS)}")
    p = bk.asset_path(b, asset)
    if not p:
        raise HTTPException(404, f"This brand has no {asset} yet")
    mt = _MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream")
    if inline:
        return FileResponse(str(p), media_type=mt)
    return FileResponse(str(p), media_type=mt,
                        filename=f"{b['slug']}-{asset}{p.suffix.lower()}")


@router.get("/api/brands/{brand_id}/export")
def export_brand_ep(brand_id: int):
    """The whole package as one zip: brand.json + the asset files — hand it to a
    designer, a site builder, or another tool and the branding stays consistent."""
    b = _brand_or_404(brand_id)
    pkg = {k: b.get(k) for k in
           ("id", "name", "slug", "tagline", "description_short", "description_long",
            "colors", "style_notes", "links", "is_default", "meta",
            "created_at", "updated_at")}
    pkg["assets"] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for kind in bk.ASSET_KINDS:
            p = bk.asset_path(b, kind)
            if p:
                arc = f"{b['slug']}-{kind}{p.suffix.lower()}"
                z.write(str(p), arc)
                pkg["assets"][kind] = arc
        z.writestr("brand.json", json.dumps(pkg, indent=2))
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{b["slug"]}-brand-kit.zip"'})

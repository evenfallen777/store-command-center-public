"""3D model pipeline (Cults3D): local turntable render, SDXL hero image, image→3D
mesh generation on the GPU box, and Cults3D publish. Split out of services.py for
size; re-exported by it (from services_3d import *)."""
from deps import *
import model_paths as _mp

# ═══════════════════════════════════════════════════════════════════════════
# 3D MODELS (Cults3D pipeline)
# ═══════════════════════════════════════════════════════════════════════════

def render_model3d(model_id: int):
    """Render turntable PNGs for a backlog model (local CPU, matplotlib).
    Updates render_paths + primary_image; promotes backlog → review."""
    import render3d
    conn = get_conn()
    row = conn.execute("SELECT * FROM models3d WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        return
    conn.close()
    MODELS3D_RENDERS.mkdir(parents=True, exist_ok=True)
    try:
        paths = render3d.render_turntable(
            row["file_path"], str(MODELS3D_RENDERS), prefix=f"m{model_id}")
        conn = get_conn()
        primary = row["primary_image"] or (paths[0] if paths else None)
        new_status = "review" if row["status"] == "backlog" else row["status"]
        conn.execute(
            "UPDATE models3d SET render_paths=?,primary_image=COALESCE(primary_image,?),"
            "status=?,publish_error=NULL,updated_at=datetime('now') WHERE id=?",
            (json.dumps(paths), primary, new_status, model_id))
        conn.commit()
        conn.close()
        logger.info("Rendered %d turntable views for model3d #%d", len(paths), model_id)
    except Exception as e:
        logger.error("render_model3d #%d failed: %s", model_id, e)
        conn = get_conn()
        conn.execute("UPDATE models3d SET publish_error=?,updated_at=datetime('now') WHERE id=?",
                     (f"render failed: {str(e)[:200]}", model_id))
        conn.commit()
        conn.close()


def generate_model3d_hero(model_id: int, prompt: str, model_name: str | None = None):
    """Generate an SDXL hero/marketing image for a 3D model, on the GPU box.
    Appends the result to hero_paths. Reuses the imagegen script + GPU lock."""
    orch.image_acquire()
    conn = get_conn()
    row = conn.execute("SELECT * FROM models3d WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close(); orch.image_release(); return
    MODELS3D_HERO.mkdir(parents=True, exist_ok=True)
    out_path = MODELS3D_HERO / f"m{model_id}_hero_{int(datetime.now().timestamp())}.png"
    try:
        seed = str(random.randint(1, 2**31 - 1))
        mdl = model_name or DEFAULT_IMAGE_MODEL
        result = subprocess.run(
            [str(GENERATE_SCRIPT), prompt, str(out_path), "1024", "1024", "20", seed, mdl],
            capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and out_path.exists():
            heroes = json.loads(row["hero_paths"] or "[]")
            heroes.append(str(out_path))
            primary = row["primary_image"] or str(out_path)
            conn.execute(
                "UPDATE models3d SET hero_paths=?,primary_image=?,updated_at=datetime('now') WHERE id=?",
                (json.dumps(heroes), primary, model_id))
            conn.commit()
            logger.info("Hero image for model3d #%d: %s", model_id, out_path)
        else:
            err = (result.stderr or "")[:300]
            logger.error("Hero gen for model3d #%d failed: %s", model_id, err)
    except Exception as e:
        logger.error("generate_model3d_hero #%d exception: %s", model_id, e)
    finally:
        conn.close()
        orch.image_release()


def publish_model3d(model_id: int, asset_base: str):
    """Publish an approved 3D model to Cults3D via createCreation.
    `asset_base` is the public, token-scoped URL prefix for this model's assets:
    the app serves {asset_base}/file/<name> and {asset_base}/img/<name>."""
    from cults import create_creation, CultsError
    conn = get_conn()
    row = conn.execute("SELECT * FROM models3d WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close(); return
    if row["cults3d_id"]:
        logger.warning("model3d #%d already published (%s) — skipping", model_id, row["cults3d_id"])
        conn.close(); return

    # Build public asset URLs. File name is exposed so Cults sees the extension.
    file_name = row["file_name"] or Path(row["file_path"]).name
    file_urls = [f"{asset_base}/file/{file_name}"]
    imgs = json.loads(row["render_paths"] or "[]") + json.loads(row["hero_paths"] or "[]")
    # Put the chosen cover first (primary_image may be a full path or just a basename).
    if row["primary_image"]:
        cover = Path(row["primary_image"]).name
        imgs = sorted(imgs, key=lambda p: 0 if Path(p).name == cover else 1)
    image_urls = [f"{asset_base}/img/{Path(p).name}" for p in imgs][:10]

    try:
        if not image_urls:
            raise CultsError("No images to publish — render the mesh or add a hero image first")
        res = create_creation(
            name=row["title"] or file_name,
            description=row["description"] or (row["title"] or file_name),
            image_urls=image_urls, file_urls=file_urls,
            locale=CULTS_DEFAULT_LOCALE,
            price=(row["price_cents"] or 0) / 100.0,
            currency=row["currency"] or CULTS_DEFAULT_CURRENCY,
            tag_names=[t.strip() for t in (row["tags"] or "").split(",") if t.strip()],
            license_code=row["license_code"] or CULTS_DEFAULT_LICENSE,
            made_with_ai=bool(row["made_with_ai"]),
        )
        conn.execute(
            "UPDATE models3d SET status='published',cults3d_id=?,cults3d_url=?,"
            "publish_error=NULL,updated_at=datetime('now') WHERE id=?",
            (res["id"], res.get("url"), model_id))
        conn.commit()
        logger.info("Published model3d #%d to Cults3D: %s", model_id, res.get("url"))
    except Exception as e:
        logger.error("publish_model3d #%d failed: %s", model_id, e)
        conn.execute("UPDATE models3d SET status='error',publish_error=?,updated_at=datetime('now') WHERE id=?",
                     (str(e)[:300], model_id))
        conn.commit()
    finally:
        conn.close()


def generate_model3d_mesh(model_id: int, image_path: str, gen_script: str = None, device: str = "auto"):
    """Generate a 3D mesh from an image via an image→3D model on the GPU box, then render it.
    Copies the source image to the box, runs `gen_script` (default TripoSR), pulls the mesh back.
    device: 'auto'|'gpu'|'cpu' — 'cpu' runs models that support it without needing VRAM (slow)."""
    gen_script = gen_script or GEN_3D_SCRIPT
    # Standalone image→3D models (TripoSG/Hunyuan/SF3D/TRELLIS) need the WHOLE GPU, so
    # use video_acquire — it frees ComfyUI's cached model (~6.7 GB after SDXL) AND the LLM.
    # image_acquire only frees the LLM, leaving ComfyUI resident → 3D OOMs on the 12 GB card.
    try:
        orch.video_acquire()
    except RuntimeError as e:
        # GPU could not be freed (offender named in the message) — fail clearly.
        # A failed acquire holds nothing: no video_release().
        logger.error("generate_model3d_mesh #%d GPU acquire failed: %s", model_id, e)
        conn = get_conn()
        conn.execute("UPDATE models3d SET status='error',progress_msg='❌ GPU busy',"
                     "publish_error=?,updated_at=datetime('now') WHERE id=?",
                     (str(e)[:250], model_id))
        conn.commit(); conn.close()
        return
    _gpu_held = True
    conn = get_conn()
    row = conn.execute("SELECT * FROM models3d WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close(); orch.video_release(); return
    # Generated meshes live in their OWN folder — never mixed into your backlog.
    MODELS3D_GENERATED.mkdir(parents=True, exist_ok=True)
    ts = int(datetime.now().timestamp())
    remote_in = f"/tmp/m3d_in_{model_id}_{ts}.png"
    remote_out = f"/tmp/m3d_out_{model_id}_{ts}.glb"
    local_out = MODELS3D_GENERATED / f"gen_{model_id}_{ts}.glb"
    try:
        # 1. push the source image to the box
        scp_up = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                  image_path, f"{GPU_SSH_USER}@{GPU_HOST}:{remote_in}"]
        subprocess.run(scp_up, check=True, capture_output=True, text=True, timeout=60)
        # 2. run the chosen image→3D model on the box (HF_HOME → the 3D model folder;
        #    HF token for gated models like SF3D; device env for CPU fallback)
        run = BOX_SSH + [f"{_device_env(device)}{_hf_token_env()}HF_HOME={_mp.primary("3d")} "
                         f"bash {gen_script} {remote_in} {remote_out}"]
        r = subprocess.run(run, capture_output=True, text=True, timeout=1200)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "generate script failed")[-300:])
        # 3. pull the mesh back
        scp_dn = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                  f"{GPU_SSH_USER}@{GPU_HOST}:{remote_out}", str(local_out)]
        subprocess.run(scp_dn, check=True, capture_output=True, text=True, timeout=120)
        if not local_out.exists():
            raise RuntimeError("mesh not returned from box")
        size = local_out.stat().st_size
        fhash = _hashlib.sha256(local_out.read_bytes()).hexdigest()
        conn.execute(
            "UPDATE models3d SET file_path=?,file_name=?,file_ext='glb',file_size=?,"
            "file_hash=?,status='backlog',publish_error=NULL,"
            "progress_msg='🖼 Rendering preview…',updated_at=datetime('now') WHERE id=?",
            (str(local_out), local_out.name, size, fhash, model_id))
        conn.commit()
        conn.close()
        orch.video_release(); _gpu_held = False   # release before the local CPU render
        render_model3d(model_id)   # auto-render the new mesh → review
        c = get_conn()
        c.execute("UPDATE models3d SET progress_msg='✅ Done — waiting in Review' WHERE id=?", (model_id,))
        c.commit(); c.close()
        return
    except Exception as e:
        logger.error("generate_model3d_mesh #%d failed: %s", model_id, e)
        conn.execute("UPDATE models3d SET status='error',progress_msg='❌ Failed',"
                     "publish_error=?,updated_at=datetime('now') WHERE id=?",
                     (f"3D gen failed: {str(e)[:250]}", model_id))
        conn.commit()
        conn.close()
    finally:
        if _gpu_held:
            orch.video_release()


def _hf_token_env() -> str:
    """`HUGGING_FACE_HUB_TOKEN=… ` prefix for remote commands, from the hf_token setting
    (empty if unset). Needed for gated models like Stable Fast 3D."""
    tok = (get_setting("hf_token", "") or "").strip()
    return f"HUGGING_FACE_HUB_TOKEN={tok} " if tok else ""


def _device_env(device: str = "auto") -> str:
    """`STORE_FORCE_DEVICE=cpu ` prefix when the user picks CPU (run big models without
    enough VRAM — slow but works). '' for auto/gpu. Scripts/models honor this env."""
    return "STORE_FORCE_DEVICE=cpu " if (device or "").lower() == "cpu" else ""


def test_gen_model(key: str) -> dict:
    """Run a REAL one-shot generation for a 3D generator on a sample image and report
    pass/fail — replaces the weak marker-based 'installed' badge. Goes through the GPU
    orchestrator (video_acquire) so it never collides with a running gen."""
    cat = {m["key"]: m for m in RECOMMENDED_3D_MODELS}
    m = cat.get(key)
    if not m:
        return {"ok": False, "error": "unknown model"}
    script = m["script"]
    ts = int(datetime.now().timestamp())
    remote_out = f"/tmp/test_{key}_{ts}.glb"
    # a sample image that ships with TripoSR; fall back to any png on the box
    sample = "$HOME/TripoSR/examples/chair.png"
    try:
        orch.video_acquire()
    except RuntimeError as e:
        # a failed acquire holds nothing — do not video_release()
        return {"ok": False, "error": str(e)[:260]}
    t0 = time.time()
    try:
        pick = (f'IMG={sample}; [ -f "$IMG" ] || IMG=$(find $HOME/TripoSR/examples '
                f'-name "*.png" 2>/dev/null | head -1); '
                f'{_hf_token_env()}HF_HOME={_mp.primary("3d")} bash {script} "$IMG" {remote_out}')
        r = subprocess.run(BOX_SSH + [pick], capture_output=True, text=True, timeout=1200)
        secs = int(time.time() - t0)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "generation failed")[-260:]
            return {"ok": False, "error": err, "secs": secs}
        chk = subprocess.run(BOX_SSH + [f"test -f {remote_out} && stat -c%s {remote_out} || echo 0"],
                             capture_output=True, text=True, timeout=30)
        size = int((chk.stdout or "0").strip() or 0)
        subprocess.run(BOX_SSH + [f"rm -f {remote_out}"], capture_output=True, text=True, timeout=15)
        if size > 1000:
            return {"ok": True, "size": size, "secs": secs}
        return {"ok": False, "error": "ran but produced no mesh", "secs": secs}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out (>20 min)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:260]}
    finally:
        orch.video_release()


# ── 3D generator VRAM preflight ───────────────────────────────────────────────
# TRELLIS needs ~16 GB VRAM; this node has a 12 GB card and can never run it —
# without this gate, picking TRELLIS launches straight into the cryptic
# `OSError: libcudart.so.13: cannot open shared object file` (shadow-torch) failure
# deep in the pipeline. Fail fast at request time instead, with a clear message.
_gpu_capacity_cache = {"mb": None, "checked_at": 0.0}
_GPU_CAPACITY_TTL = 600  # seconds — total VRAM is a hardware constant; cache generously


def gpu_capacity_mb():
    """Total VRAM (MiB) on the node's GPU, via `nvidia-smi --query-gpu=memory.total`.
    Cached for _GPU_CAPACITY_TTL. Returns None on ANY failure (node unreachable, no
    nvidia-smi, parse error) — never raises. Callers must treat None as 'unknown' and
    must NOT block on it (only refuse when capacity is positively known to be too small)."""
    now = time.time()
    cached = _gpu_capacity_cache["mb"]
    if cached is not None and (now - _gpu_capacity_cache["checked_at"]) < _GPU_CAPACITY_TTL:
        return cached
    try:
        r = subprocess.run(
            BOX_SSH + ["nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and (r.stdout or "").strip():
            mb = int((r.stdout or "").strip().splitlines()[0].strip())
            _gpu_capacity_cache.update({"mb": mb, "checked_at": now})
            return mb
    except Exception:
        pass
    return None


def check_3d_vram_or_raise(gen: dict) -> None:
    """Preflight for the /api/models3d/generate path: if `gen`'s min_vram_mb exceeds
    this node's GPU capacity, raise a clear HTTPException instead of letting the job
    launch into a cryptic mid-pipeline failure (TRELLIS's shadow-torch libcudart crash,
    or a plain OOM). Unknown capacity (node unreachable / nvidia-smi missing) is NOT a
    reason to block — we only refuse when we POSITIVELY know it won't fit. Never raises
    anything other than the intended HTTPException."""
    need = gen.get("min_vram_mb")
    if not need:
        return
    try:
        cap = gpu_capacity_mb()
    except Exception:
        cap = None
    if cap is not None and cap < need:
        label = gen.get("label", gen.get("key", "this generator"))
        raise HTTPException(
            400,
            f"{label} needs ~{need // 1000}GB VRAM; this node's GPU has "
            f"~{cap // 1000}GB — use TripoSR/TripoSG/SF3D instead."
        )


# Export everything (incl. single-underscore helpers used across modules).
__all__ = [n for n in dir() if not n.startswith('__')]

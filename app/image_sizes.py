"""Derive publish-ready size variants from a master render (WordPress-style sizes).

Masters can be up to 4096² / ~20 MB — unusable for Etsy (needs 1024², ≤5 MB) and
wasteful for web display. This produces cached derivatives in a shared sizes/ dir,
keyed by the master's (unique) stem so they survive a pending→approved file move.
Print keeps the full master untouched.

Two surfaces use this:
  • auto-derive: run_generation() pre-warms variants as soon as a render lands.
  • on-demand:   GET /api/designs/{id}/export?spec=etsy re-derives from the current
                 master (always correct even after the file moves), and the Etsy
                 publish path uploads the 'etsy' variant instead of the raw master.
"""
from __future__ import annotations

import logging
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("store")

SIZES_DIR = DATA_DIR / "designs" / "sizes"

# spec -> rendering rules.
#   box       : target box in px
#   square    : True → center-crop to box×box (Etsy wants square); False → fit longest side
#   fmt/ext   : output format
#   max_bytes : step JPEG quality down until the file fits under this
SPECS = {
    # Etsy listing image: exactly 1024×1024, JPEG, hard ≤5 MB ceiling.
    "etsy": {"box": 1024, "square": True,  "fmt": "JPEG", "ext": "jpg", "max_bytes": 5_000_000},
    # Web/medium: bound to 1600 longest side so the store never ships a 20 MB master.
    "web":  {"box": 1600, "square": False, "fmt": "JPEG", "ext": "jpg", "max_bytes": 2_000_000},
}


def _target(stem: str, spec: str) -> Path:
    return SIZES_DIR / f"{stem}__{spec}.{SPECS[spec]['ext']}"


def _render(src: Path, spec: str, dst: Path) -> None:
    from PIL import Image
    s = SPECS[spec]
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if s["square"]:
        m = min(w, h)
        left, top = (w - m) // 2, (h - m) // 2
        im = im.crop((left, top, left + m, top + m)).resize((s["box"], s["box"]), Image.LANCZOS)
    else:
        im.thumbnail((s["box"], s["box"]), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    while True:
        im.save(dst, s["fmt"], quality=q, optimize=True)
        if dst.stat().st_size <= s["max_bytes"] or q <= 55:
            break
        q -= 6


def one(src_path, spec: str) -> Path | None:
    """Return a cached derivative for `spec`, (re)generating it if stale/missing.
    Returns None if the source is gone; raises ValueError on an unknown spec."""
    if spec not in SPECS:
        raise ValueError(f"unknown size spec: {spec!r} (have: {', '.join(SPECS)})")
    src = Path(src_path)
    if not src.is_file():
        return None
    dst = _target(src.stem, spec)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        _render(src, spec, dst)
    return dst


def derive(src_path, specs=None) -> dict:
    """Pre-warm size variants. Best-effort — never raises (generation must not break)."""
    out = {}
    for spec in (specs or SPECS.keys()):
        try:
            p = one(src_path, spec)
            if p:
                out[spec] = str(p)
        except Exception as e:  # noqa: BLE001 — pre-warm is advisory
            logger.warning("size derive (%s) skipped for %s: %s", spec, src_path, e)
    return out

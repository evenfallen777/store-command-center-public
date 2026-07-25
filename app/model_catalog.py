"""
Model catalogs (image / LoRA / video / 3D) — split out of config.py.

These are large, static data structures. config.py re-exports them via
`from model_catalog import *` so the public `config` surface is unchanged.
The COMFY_* directory names are env-var-derived config values; they are
imported from config, which is fully defined by the time this module is
imported (the re-export sits at the very end of config.py, so there is no
import cycle).
"""
from config import COMFY_CKPT, COMFY_LORA, COMFY_UPSCALE, COMFY_CONTROLNET, COMFY_RMBG

# ── 3D generation models (image/text → mesh, on the GPU box) ─────────────────
# Analogous to RECOMMENDED_MODELS / RECOMMENDED_VIDEO_MODELS but for 3D gen. Each
# entry is installed as a repo + venv on the box (not a single file), so it carries
# an install script rather than a download_url. `marker` is a path that exists once
# installed. TripoSR is the default, set up 2026-07-12.
RECOMMENDED_3D_MODELS = [
    {
        "key": "triposr",
        "label": "TripoSR (fast, default)",
        "style": "Image → 3D · fast (~2 min on a 3060) · good for figurines/props",
        "vram": "~6 GB",
        "min_vram_mb": 6000,   # machine-readable VRAM floor, for the preflight gate
        "marker": "~/TripoSR/run.py",
        "script": "~/.openclaw/tools/model3d/generate_3d.sh",
        "license": "MIT",
        "commercial": True,   # safe to SELL the output
        "note": "Lightweight and reliable. MIT-licensed — safe for models you sell. Uses PyMCubes.",
        "install": (
            "cd ~ && ([ -d TripoSR ] || git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git) && "
            "python3 -m venv --system-site-packages ~/TripoSR/venv && "
            "echo /home/user/ComfyUI/venv/lib/python3.12/site-packages > "
            "~/TripoSR/venv/lib/python3.12/site-packages/zzz_comfyui.pth && "
            "~/TripoSR/venv/bin/pip install -q 'transformers==4.35.0' 'tokenizers>=0.14,<0.15' "
            "'huggingface-hub<0.18' 'omegaconf==2.3.0' 'einops==0.7.0' PyMCubes xatlas rembg imageio moderngl"
        ),
    },
    {
        "key": "triposg",
        "label": "TripoSG ★ (MIT — sellable, higher quality)",
        "style": "Image → 3D · sharper geometry than TripoSR · commercial-safe",
        "vram": "~8–10 GB",
        "min_vram_mb": 10000,  # machine-readable VRAM floor (upper end of the range above)
        "marker": "~/TripoSG/venv/bin/python",
        "script": "~/.openclaw/tools/model3d/generate_triposg.sh",
        "license": "MIT",
        "commercial": True,      # safe to SELL the output
        "size": "~2–3 GB",
        "note": "MIT — safe to sell. Cleaner geometry than TripoSR. Reuses ComfyUI's torch and "
                "uses the CPU mesh extractor (no CUDA toolkit needed). Recommended upgrade. ~2–3 GB.",
        "install": "bash ~/.openclaw/tools/model3d/install_triposg.sh",
    },
    # Hunyuan3D-2 mini was removed: NON-commercial license (can't be sold) AND its
    # shapegen loader is broken on this box. Re-add only for personal/non-sale use.
    {
        "key": "sf3d",
        "label": "Stable Fast 3D (fast + textured)",
        "style": "Image → 3D · fast (~1 min) · textured · needs CUDA toolkit",
        "vram": "~7 GB",
        "min_vram_mb": 7000,   # machine-readable VRAM floor, for the preflight gate
        "marker": "~/stable-fast-3d/venv/bin/python",
        "script": "~/.openclaw/tools/model3d/generate_sf3d.sh",
        "license": "Stability Community (free < $1M rev)",
        "commercial": True,      # sellable if your revenue is under $1M/yr
        "needs_cuda": True,
        "size": "~3 GB",
        "note": "Community license — free to sell if your revenue is under $1M/yr. Textured meshes. "
                "Needs the CUDA toolkit (builds texture-baker ops) AND a HuggingFace token "
                "(gated model — accept the license on huggingface.co/stabilityai/stable-fast-3d).",
        "install": "bash ~/.openclaw/tools/model3d/install_sf3d.sh",
    },
    {
        "key": "trellis",
        "label": "TRELLIS (Microsoft) — top quality, experimental",
        "style": "Image → 3D · best open quality · heavy build · needs CUDA toolkit",
        "vram": "~16 GB (great on 2×3060/24 GB)",
        "min_vram_mb": 16000,  # machine-readable VRAM floor — this node's 12 GB card can't run it
        "marker": "~/TRELLIS/venv/bin/python",
        "script": "~/.openclaw/tools/model3d/generate_trellis.sh",
        "license": "MIT",
        "commercial": True,      # MIT — safe to sell
        "needs_cuda": True,
        "size": "~10 GB",
        "note": "MIT — safe to sell. Highest open quality, but a HEAVY / experimental install "
                "(custom CUDA ops: nvdiffrast, spconv, flash-attn). Needs the CUDA toolkit. "
                "May need a follow-up if a build step fails — tell me and I'll fix it.",
        "install": "bash ~/.openclaw/tools/model3d/install_trellis.sh",
    },
]

# ── Recommended image models catalog ─────────────────────────────────────────
RECOMMENDED_MODELS = [
    {
        "filename": "sdxl_base_1.0.safetensors",
        "label": "SDXL Base 1.0",
        "style": "General purpose",
        "vram": "~6.7 GB",
        "source": "HuggingFace (no auth)",
        "auto_download": True,
        "download_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
        "download": "wget -O ~/ComfyUI/models/checkpoints/sdxl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
    },
    {
        "filename": "dreamshaperXL_lightningDPMSDE.safetensors",
        "label": "DreamShaper XL Lightning",
        "style": "Artistic / Illustration — great for merch",
        "vram": "~6.7 GB",
        "source": "CivitAI (no auth — direct link)",
        "auto_download": True,
        "download_url": "https://civitai.com/api/download/models/354657",
        "download": "wget -O ~/ComfyUI/models/checkpoints/dreamshaperXL_lightningDPMSDE.safetensors 'https://civitai.com/api/download/models/354657'",
        "note": "No login needed. Lightning model: use steps=6–8 for best results.",
    },
    {
        "filename": "realvisxlV50_v50LightningBakedvae.safetensors",
        "label": "RealVisXL V5 Lightning",
        "style": "Photorealistic — portraits, products, scenes",
        "vram": "~6.7 GB",
        "source": "CivitAI (no auth — direct link)",
        "auto_download": True,
        "download_url": "https://civitai.com/api/download/models/798204",
        "download": "wget -O ~/ComfyUI/models/checkpoints/realvisxlV50_v50LightningBakedvae.safetensors 'https://civitai.com/api/download/models/798204'",
        "note": "No login needed. Lightning: use steps=6–8, CFG=1.5–2 for best results.",
    },
    {
        "filename": "sd_xl_turbo_1.0_fp16.safetensors",
        "label": "SDXL Turbo (fast, 4-step)",
        "style": "Fast generation / previews",
        "vram": "~6 GB",
        "source": "HuggingFace (no auth, Stability AI)",
        "auto_download": True,
        "download_url": "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors",
        "download": "wget -O ~/ComfyUI/models/checkpoints/sd_xl_turbo_1.0_fp16.safetensors https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors",
        "note": "Turbo: use steps=4, CFG=1.0 for best results.",
    },
    {
        "filename": "flux1-schnell-fp8.safetensors",
        "label": "FLUX.1 schnell (fp8) ★",
        "style": "State-of-the-art quality + excellent text rendering, fast (4-step)",
        "vram": "~17 GB (needs 24 GB)",
        "source": "HuggingFace (no auth, Comfy-Org)",
        "auto_download": True,
        "download_url": "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors",
        "download": "wget -O ~/ComfyUI/models/checkpoints/flux1-schnell-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors",
        "note": "Best open image model — ideal for your dual-3060 (24 GB). Steps 4, CFG 1.",
    },
    {
        "filename": "juggernautXL_juggXIByRundiffusion.safetensors",
        "label": "Juggernaut XL XI",
        "style": "Top-tier photorealism — products, people, scenes",
        "vram": "~6.7 GB",
        "source": "CivitAI (no auth — direct link)",
        "auto_download": True,
        "download_url": "https://civitai.com/api/download/models/782002",
        "download": "wget -O ~/ComfyUI/models/checkpoints/juggernautXL_juggXIByRundiffusion.safetensors 'https://civitai.com/api/download/models/782002'",
        "note": "Community favourite for realistic merch mockups.",
    },
]

RECOMMENDED_LORAS = [
    {
        "filename": "pixel-art-xl.safetensors",
        "label": "Pixel Art XL",
        "style": "Pixel-art LoRA for SDXL — the world / sprite generator",
        "vram": "~170 MB",
        "source": "HuggingFace (no auth)",
        "kind": "lora",
        "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/nerijs/pixel-art-xl/resolve/main/pixel-art-xl.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/pixel-art-xl.safetensors https://huggingface.co/nerijs/pixel-art-xl/resolve/main/pixel-art-xl.safetensors",
        "note": "Powers The Company's pixel-art sprite generator (Settings → 🧠 Models → LoRA · world sprites, default strength 0.9).",
    },
    {
        "filename": "pixel-art-slider.safetensors",
        "label": "Pixel Art Slider (ntc-ai)",
        "style": "SDXL pixel-art slider LoRA — dial pixel-ness up/down",
        "vram": "~9 MB",
        "source": "HuggingFace (no auth)",
        "kind": "lora",
        "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/ntc-ai/SDXL-LoRA-slider.pixel-art/resolve/main/pixel%20art.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/pixel-art-slider.safetensors \"https://huggingface.co/ntc-ai/SDXL-LoRA-slider.pixel-art/resolve/main/pixel%20art.safetensors\"",
        "note": "Second, adjustable pixel style axis for sprites — pair with pixel-art-xl, tune strength for more/less pixelation.",
    },
    {
        "filename": "pixel-art-wzqacky.safetensors",
        "label": "Pixel Art XL (wzqacky)",
        "style": "SDXL pixel-art LoRA — grittier base style",
        "vram": "~23 MB",
        "source": "HuggingFace (no auth)",
        "kind": "lora",
        "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/wzqacky/pixel-art-model-sdxl-lora/resolve/main/pytorch_lora_weights.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/pixel-art-wzqacky.safetensors https://huggingface.co/wzqacky/pixel-art-model-sdxl-lora/resolve/main/pytorch_lora_weights.safetensors",
        "note": "Alternate pixel base — grittier than pixel-art-xl; good for prop/building sprites.",
    },
    # ── Merch / design-style LoRAs (map to Etsy/Printify product types) ──
    {
        "filename": "SDXL-StickerSheet-Lora.safetensors",
        "label": "Sticker Sheet (Norod78)",
        "style": "SDXL LoRA — die-cut sticker-sheet style · trigger: StickerSheet",
        "vram": "~29 MB", "source": "HuggingFace (no auth)", "kind": "lora", "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/Norod78/SDXL-StickerSheet-Lora/resolve/main/SDXL-StickerSheet-Lora.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/SDXL-StickerSheet-Lora.safetensors https://huggingface.co/Norod78/SDXL-StickerSheet-Lora/resolve/main/SDXL-StickerSheet-Lora.safetensors",
        "note": "Sticker sheets / die-cut sticker packs → Etsy/Printify sticker products.",
    },
    {
        "filename": "Canopus-Pencil-Art-LoRA.safetensors",
        "label": "Pencil / Line Art (Canopus)",
        "style": "SDXL LoRA — B&W line-art / coloring-book outlines",
        "vram": "~456 MB", "source": "HuggingFace (no auth)", "kind": "lora", "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/prithivMLmods/Canopus-Pencil-Art-LoRA/resolve/main/Canopus-Pencil-Art-LoRA.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/Canopus-Pencil-Art-LoRA.safetensors https://huggingface.co/prithivMLmods/Canopus-Pencil-Art-LoRA/resolve/main/Canopus-Pencil-Art-LoRA.safetensors",
        "note": "Line-art / coloring-book tees & printables.",
    },
    {
        "filename": "SDXL_Yarn_Art_Style.safetensors",
        "label": "Yarn / Embroidery (Norod78)",
        "style": "SDXL LoRA — yarn / embroidered-patch aesthetic · trigger: Yarn art style",
        "vram": "~57 MB", "source": "HuggingFace (no auth)", "kind": "lora", "dest_dir": COMFY_LORA,
        "auto_download": True,
        "download_url": "https://huggingface.co/Norod78/SDXL-YarnArtStyle-LoRA/resolve/main/SDXL_Yarn_Art_Style.safetensors",
        "download": "wget -O ~/ComfyUI/models/loras/SDXL_Yarn_Art_Style.safetensors https://huggingface.co/Norod78/SDXL-YarnArtStyle-LoRA/resolve/main/SDXL_Yarn_Art_Style.safetensors",
        "note": "Embroidered-patch / crafty look → patch & craft product lines.",
    },
]

# ── Specialty ComfyUI models grouped by type (upscalers / controlnet / matting) ──
# Each group has its own ComfyUI dir. Downloads reuse the same flow (dest_dir per entry).
EXTRA_MODEL_GROUPS = [
    {
        "key": "upscalers", "label": "\U0001F50D Upscalers", "dir": COMFY_UPSCALE,
        "sub": "ESRGAN upscalers (models/upscale_models/) — print-quality merch before Etsy/Printify",
        "models": [
            {"filename": "4x-UltraSharp.pth", "label": "4x-UltraSharp", "vram": "~67 MB",
             "style": "Sharp general upscaler — the print-quality default",
             "download_url": "https://huggingface.co/uwg/upscaler/resolve/main/ESRGAN/4x-UltraSharp.pth",
             "note": "Default 4× upscale for merch art before print-on-demand."},
            {"filename": "4x_foolhardy_Remacri.pth", "label": "4x Remacri", "vram": "~67 MB",
             "style": "Detail-preserving upscaler",
             "download_url": "https://huggingface.co/uwg/upscaler/resolve/main/ESRGAN/4x_foolhardy_Remacri.pth",
             "note": "Sharper detail for posters / large-format prints."},
            {"filename": "4x_NMKD-Siax_200k.pth", "label": "4x NMKD-Siax", "vram": "~67 MB",
             "style": "Clean upscaler for illustration/vector art",
             "download_url": "https://huggingface.co/uwg/upscaler/resolve/main/ESRGAN/4x_NMKD-Siax_200k.pth",
             "note": "Clean upscale for illustration/vector-style merch."},
        ],
    },
    {
        "key": "bgremoval", "label": "✂️ Background removal", "dir": COMFY_RMBG,
        "sub": "Matting model (models/rmbg/) — transparent stickers + cleaner sprite cutouts",
        "models": [
            {"filename": "birefnet.safetensors", "label": "BiRefNet (MIT)", "vram": "~444 MB",
             "style": "High-quality matting — commercially safe (MIT)",
             "download_url": "https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors",
             "note": "Transparent PNGs for die-cut stickers + sprite alpha. Use the ComfyUI-RMBG / BiRefNet node (models/rmbg/)."},
        ],
    },
    {
        "key": "controlnet", "label": "\U0001F39B️ ControlNet", "dir": COMFY_CONTROLNET,
        "sub": "SDXL ControlNet (models/controlnet/) — consistent poses/layouts across a product line",
        "models": [
            {"filename": "controlnet-union-sdxl-promax.safetensors", "label": "ControlNet++ Union SDXL ProMax", "vram": "~2.5 GB",
             "style": "All-in-one: pose / depth / canny / tile",
             "download_url": "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors",
             "note": "One model = 12+ control types → coherent poses/layouts across a merch collection."},
        ],
    },
]

# Every downloadable model, flattened with a dest_dir — used by the download endpoint.
def all_downloadable_models():
    items = []
    for m in RECOMMENDED_MODELS:
        items.append({**m, "dest_dir": m.get("dest_dir", COMFY_CKPT)})
    for m in RECOMMENDED_LORAS:
        items.append({**m, "dest_dir": m.get("dest_dir", COMFY_LORA)})
    for g in EXTRA_MODEL_GROUPS:
        for m in g["models"]:
            items.append({**m, "dest_dir": m.get("dest_dir", g["dir"]), "auto_download": m.get("auto_download", True), "kind": g["key"]})
    return items

# ── Recommended video models catalog ─────────────────────────────────────────
# Each entry carries a `gen_defaults` block: the per-model generation params the
# owner can tune (Models tab → Video Models → ⚙ Tune defaults, stored as
# overrides in the `video_model_settings` settings key and merged by
# services_media.video_gen_settings). Every value below is EXACTLY what the
# pipeline effectively used before this block existed — width/height/frames/
# steps/fps/strength were the request-body defaults in routers/videos.py, and
# `guidance` mirrors the guidance_scale the node's store_videogen.py hardcodes
# per model family — so an untuned install behaves identically.
RECOMMENDED_VIDEO_MODELS = [
    {
        "model_id": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "label": "Wan2.1 T2V 1.3B (Default)",
        "style": "General purpose text-to-video",
        "vram": "~8 GB",
        "min_vram_mb": 8000,   # machine-readable VRAM floor, for the preflight gate
        "size": "~5.5 GB",
        "source": "HuggingFace (no auth)",
        "note": "Auto-downloads on first video gen. Recommended starting point.",
        "rec_steps": 20,
        "steps_options": [15, 20, 30],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 5.0, "strength": 0.7},
    },
    {
        "model_id": "Lightricks/LTX-Video",
        "label": "LTX-Video (Fast)",
        "style": "Fast generation, good for previews",
        "vram": "~10 GB",
        "min_vram_mb": 10000,  # machine-readable VRAM floor, for the preflight gate
        "size": "~9 GB",
        "source": "HuggingFace (no auth)",
        "note": "Use steps=6–10 for best results. bfloat16.",
        "rec_steps": 8,
        "steps_options": [6, 8, 10, 15],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 3.0, "strength": 0.7},
    },
    {
        "model_id": "THUDM/CogVideoX-2b",
        "label": "CogVideoX 2B",
        "style": "High quality, cinematic",
        "vram": "~12 GB",
        "min_vram_mb": 12000,  # tight but runs on the node's 12 GB card
        "size": "~9 GB",
        "source": "HuggingFace (no auth)",
        "note": "Use steps=50, CFG=6.0. May be tight on 12 GB VRAM.",
        "rec_steps": 50,
        "steps_options": [30, 40, 50],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 6.0, "strength": 0.7},
    },
    {
        "model_id": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
        "label": "Wan2.1 T2V 14B ★ (high quality)",
        "style": "Much sharper motion & detail than the 1.3B default",
        "vram": "~20-24 GB",
        "min_vram_mb": 20000,  # machine-readable VRAM floor — the node's 12 GB card can't run it
        "size": "~28 GB",
        "source": "HuggingFace (no auth)",
        "note": "Great fit for your dual-3060 (24 GB).",
        "rec_steps": 25,
        "steps_options": [20, 25, 30],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 5.0, "strength": 0.7},
    },
    {
        "model_id": "THUDM/CogVideoX-5b",
        "label": "CogVideoX 5B",
        "style": "Higher fidelity than 2B",
        "vram": "~16-24 GB",
        "min_vram_mb": 16000,  # machine-readable VRAM floor — the node's 12 GB card can't run it
        "size": "~20 GB",
        "source": "HuggingFace (no auth)",
        "note": "Steps 50, CFG 6. Needs 24 GB comfortably.",
        "rec_steps": 50,
        "steps_options": [30, 40, 50],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 6.0, "strength": 0.7},
    },
    {
        "model_id": "tencent/HunyuanVideo",
        "label": "Hunyuan Video (SOTA)",
        "style": "State-of-the-art open video — cinematic",
        "vram": "~24 GB+ (with offload)",
        "min_vram_mb": 24000,  # machine-readable VRAM floor — the node's 12 GB card can't run it
        "size": "~40 GB",
        "source": "HuggingFace (no auth)",
        "note": "Very heavy; use CPU offload even on 24 GB.",
        "rec_steps": 30,
        "steps_options": [20, 30, 50],
        "gen_defaults": {"width": 832, "height": 480, "num_frames": 49,
                         "steps": 20, "fps": 16, "guidance": 5.0, "strength": 0.7},
    },
]

# Validation table for owner overrides of gen_defaults: key → (type, min, max).
# Anything outside this table (or these bounds) is rejected/ignored, so a bad
# saved value can never wedge the generation pipeline.
VIDEO_GEN_TUNABLES = {
    "width":      (int,   64,   1920),
    "height":     (int,   64,   1920),
    "num_frames": (int,   9,    481),
    "steps":      (int,   1,    100),
    "fps":        (int,   4,    60),
    "guidance":   (float, 0.0,  20.0),
    "strength":   (float, 0.05, 1.0),
}


def video_model_gen_defaults(model_id: str) -> dict:
    """Catalog gen_defaults for a video model (as a copy, safe to mutate).
    Unknown/off-catalog models get the global defaults routers/videos.py has
    always applied — note: no `guidance` key, so nothing extra is ever passed
    to the generator for models we don't know (preserves old behavior)."""
    for m in RECOMMENDED_VIDEO_MODELS:
        if m.get("model_id") == model_id and isinstance(m.get("gen_defaults"), dict):
            return dict(m["gen_defaults"])
    return {"width": 832, "height": 480, "num_frames": 49,
            "steps": 20, "fps": 16, "strength": 0.7}

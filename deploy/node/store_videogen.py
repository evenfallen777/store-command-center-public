#!/usr/bin/env python3
"""
store_videogen.py — generate video using diffusers on RTX 3060.
Supports multiple models: Wan2.1-T2V-1.3B, LTX-Video, CogVideoX-2b
Supports modes:
  t2v  — standard text-to-video (default)
  v2v  — video-to-video continuation from prev_video_path
Usage: python3 store_videogen.py <args_json_file>
Args JSON keys: prompt, output, width, height, frames, steps, seed, fps, model_id,
                mode (t2v|v2v), prev_video_path (v2v only), strength (v2v, default 0.7),
                guidance (optional CFG override; omitted → the per-model default below)
"""
import sys, json, os, time

# Per-model configs
MODEL_CONFIGS = {
    "Wan-AI/Wan2.1-T2V-1.3B-Diffusers": {
        "dtype": "float16",
        "guidance_scale": 5.0,
        "t2v_pipeline": "WanPipeline",
        "v2v_pipeline": "WanVideoToVideoPipeline",
        "use_negative_prompt": True,
    },
    "Lightricks/LTX-Video": {
        "dtype": "bfloat16",
        "guidance_scale": 3.0,
        "t2v_pipeline": "LTXPipeline",
        "v2v_pipeline": "LTXConditionPipeline",   # image-conditioned continuation
        "use_negative_prompt": True,
    },
    "THUDM/CogVideoX-2b": {
        "dtype": "float16",
        "guidance_scale": 6.0,
        "t2v_pipeline": "CogVideoXPipeline",
        "v2v_pipeline": "CogVideoXVideoToVideoPipeline",
        "use_negative_prompt": False,
    },
}

NEGATIVE_PROMPT = (
    "bad quality, worst quality, watermark, text, blurry, distorted, "
    "deformed, mutation, ugly, artifacts, noise"
)


def _progress_kwargs(pipe, total):
    """Emit '[progress] step/total' each diffusion step, if the pipeline supports a
    step callback. The Store reads these lines to drive a real progress bar."""
    try:
        import inspect
        if 'callback_on_step_end' in inspect.signature(pipe.__call__).parameters:
            def _cb(_p, _step, _t, _k):
                try:
                    print(f"[progress] {int(_step)+1}/{total}", flush=True)
                except Exception:
                    pass
                return _k
            return {'callback_on_step_end': _cb}
    except Exception:
        pass
    return {}


def _resolve_cfg(model_id):
    cfg = MODEL_CONFIGS.get(model_id)
    if not cfg:
        if "LTX" in model_id or "Lightricks" in model_id:
            cfg = MODEL_CONFIGS["Lightricks/LTX-Video"]
        elif "CogVideoX" in model_id or "THUDM" in model_id:
            cfg = MODEL_CONFIGS["THUDM/CogVideoX-2b"]
        else:
            cfg = MODEL_CONFIGS["Wan-AI/Wan2.1-T2V-1.3B-Diffusers"]
    return cfg


def _guidance(args, cfg):
    """CFG for this run: explicit per-request value (the Store's per-model tuning)
    wins; otherwise the model family's default above — exactly the old behavior.
    Not `or`-chained: 0.0 is a legitimate override (guidance off)."""
    g = args.get("guidance")
    try:
        return float(g) if g is not None else cfg["guidance_scale"]
    except (TypeError, ValueError):
        return cfg["guidance_scale"]


def load_frames(video_path):
    """Load all frames from a video as PIL Images."""
    import imageio
    from PIL import Image
    reader = imageio.get_reader(video_path)
    frames = [Image.fromarray(frame) for frame in reader]
    reader.close()
    return frames


def generate_t2v(args):
    """Standard text-to-video generation."""
    import torch
    from diffusers.utils import export_to_video

    model_id       = args.get("model_id", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers")
    prompt         = args["prompt"]
    output         = args["output"]
    width          = int(args.get("width", 832))
    height         = int(args.get("height", 480))
    num_frames     = int(args.get("frames", 49))
    steps          = int(args.get("steps", 20))
    seed           = int(args.get("seed", 42))
    fps            = int(args.get("fps", 16))

    cfg            = _resolve_cfg(model_id)
    dtype_str      = cfg["dtype"]
    guidance_scale = _guidance(args, cfg)
    pipeline_name  = cfg["t2v_pipeline"]
    use_neg        = cfg["use_negative_prompt"]
    dtype          = torch.bfloat16 if dtype_str == "bfloat16" else torch.float16

    model_short = model_id.split("/")[-1]
    print(f"[videogen] T2V: Loading {model_id} ({dtype_str}, {pipeline_name})…", flush=True)
    t0 = time.time()

    if pipeline_name == "LTXPipeline":
        from diffusers import LTXPipeline
        pipe = LTXPipeline.from_pretrained(model_id, torch_dtype=dtype)
    elif pipeline_name == "CogVideoXPipeline":
        from diffusers import CogVideoXPipeline
        pipe = CogVideoXPipeline.from_pretrained(model_id, torch_dtype=dtype)
    else:
        from diffusers import WanPipeline
        pipe = WanPipeline.from_pretrained(model_id, torch_dtype=dtype)

    print(f"[videogen] Loaded in {time.time()-t0:.1f}s — enabling sequential CPU offload…", flush=True)
    # MUST use sequential (not model) CPU offload: T5-XXL text encoder alone is ~10.8 GB.
    # enable_model_cpu_offload moves the full component to GPU at once (OOM on 12 GB).
    # enable_sequential_cpu_offload moves individual transformer layers one at a time
    # (~450 MB each), so peak VRAM = largest single layer, not the whole encoder.
    pipe.enable_sequential_cpu_offload()
    pipe.enable_attention_slicing()

    print(f"[videogen] Generating {width}x{height} {num_frames}f {steps}steps seed={seed} [{model_short}]", flush=True)
    generator = torch.Generator(device="cuda").manual_seed(seed)

    kwargs = dict(
        prompt=prompt,
        height=height, width=width,
        num_frames=num_frames,
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )
    if use_neg:
        kwargs["negative_prompt"] = NEGATIVE_PROMPT

    result = pipe(**kwargs, **_progress_kwargs(pipe, steps))

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    export_to_video(result.frames[0], output, fps=fps)
    print(f"[videogen] DONE: {output} ({time.time()-t0:.1f}s total)", flush=True)


def generate_v2v(args):
    """
    Video-to-video continuation.
    Loads prev_video_path, passes all frames into the V2V pipeline with the new prompt.
    strength: 0=copy, 1=ignore input. ~0.7 gives smooth visual continuity.
    """
    import torch
    from diffusers.utils import export_to_video

    model_id       = args.get("model_id", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers")
    prompt         = args["prompt"]
    output         = args["output"]
    width          = int(args.get("width", 832))
    height         = int(args.get("height", 480))
    num_frames     = int(args.get("frames", 49))
    steps          = int(args.get("steps", 20))
    seed           = int(args.get("seed", 42))
    fps            = int(args.get("fps", 16))
    prev_video     = args["prev_video_path"]
    strength       = float(args.get("strength", 0.7))

    cfg            = _resolve_cfg(model_id)
    dtype_str      = cfg["dtype"]
    guidance_scale = _guidance(args, cfg)
    pipeline_name  = cfg["v2v_pipeline"]
    use_neg        = cfg["use_negative_prompt"]
    dtype          = torch.bfloat16 if dtype_str == "bfloat16" else torch.float16

    model_short = model_id.split("/")[-1]
    print(f"[videogen] V2V: Loading {model_id} ({dtype_str}, {pipeline_name})…", flush=True)
    print(f"[videogen] Prev video: {prev_video}, strength={strength}", flush=True)
    t0 = time.time()

    # Load previous video frames
    print("[videogen] Loading previous video frames…", flush=True)
    prev_frames = load_frames(prev_video)
    print(f"[videogen] Loaded {len(prev_frames)} frames from previous segment", flush=True)

    # Resize frames to target resolution if needed
    if prev_frames[0].size != (width, height):
        from PIL import Image
        prev_frames = [f.resize((width, height), Image.LANCZOS) for f in prev_frames]

    generator = torch.Generator(device="cuda").manual_seed(seed)

    if pipeline_name == "WanVideoToVideoPipeline":
        from diffusers import AutoencoderKLWan, WanVideoToVideoPipeline
        # WanVideoToVideoPipeline hardcodes the input video / initial latents to
        # float32 before self.vae.encode() (pipeline_wan_video2video.py, "dtype=torch.float32"),
        # so an fp16 VAE fails with "Input type (float) and bias type (c10::Half)".
        # Upstream's documented usage is to load the VAE in float32 (see the
        # pipeline's own docstring example); the transformer/text encoder stay fp16.
        # Decode also works: the pipeline casts latents to self.vae.dtype.
        # This survives enable_sequential_cpu_offload(), which moves modules
        # between devices but never changes their dtype.
        vae = AutoencoderKLWan.from_pretrained(
            model_id, subfolder="vae", torch_dtype=torch.float32
        )
        pipe = WanVideoToVideoPipeline.from_pretrained(model_id, vae=vae, torch_dtype=dtype)
        print(f"[videogen] Loaded in {time.time()-t0:.1f}s — enabling CPU offload…", flush=True)
        pipe.enable_sequential_cpu_offload()
        pipe.enable_attention_slicing()
        print(f"[videogen] V2V generating {width}x{height} strength={strength} seed={seed} [{model_short}]", flush=True)
        # height/width MUST be passed: without them WanVideoToVideoPipeline falls
        # back to ITS defaults (832×480) regardless of the input frames' size, so
        # any non-default chain got continuation segments at the model-native size
        # and the compile had to letterbox them (the uniform-segment bug). For the
        # default 832×480 chain this is a no-op.
        kwargs = dict(
            video=prev_frames,
            prompt=prompt,
            height=height, width=width,
            strength=strength,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
        )
        if use_neg:
            kwargs["negative_prompt"] = NEGATIVE_PROMPT
        result = pipe(**kwargs, **_progress_kwargs(pipe, steps))

    elif pipeline_name == "CogVideoXVideoToVideoPipeline":
        from diffusers import CogVideoXVideoToVideoPipeline
        pipe = CogVideoXVideoToVideoPipeline.from_pretrained(model_id, torch_dtype=dtype)
        print(f"[videogen] Loaded in {time.time()-t0:.1f}s — enabling sequential CPU offload…", flush=True)
        pipe.enable_sequential_cpu_offload()
        pipe.enable_attention_slicing()
        print(f"[videogen] V2V generating {width}x{height} strength={strength} seed={seed} [{model_short}]", flush=True)
        result = pipe(
            video=prev_frames,
            prompt=prompt,
            height=height, width=width,
            num_inference_steps=steps,
            strength=strength,
            guidance_scale=guidance_scale,
            generator=generator,
            **_progress_kwargs(pipe, steps),
        )

    elif pipeline_name == "LTXConditionPipeline":
        # LTX: use last frame as image conditioning
        from diffusers import LTXConditionPipeline
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
        pipe = LTXConditionPipeline.from_pretrained(model_id, torch_dtype=dtype)
        print(f"[videogen] Loaded in {time.time()-t0:.1f}s — enabling sequential CPU offload…", flush=True)
        pipe.enable_sequential_cpu_offload()
        pipe.enable_attention_slicing()
        print(f"[videogen] V2V (LTX condition) generating {width}x{height} strength={strength} seed={seed} [{model_short}]", flush=True)
        # Use last frame as image condition at frame_index=0
        last_frame = prev_frames[-1]
        condition = LTXVideoCondition(image=last_frame, frame_index=0)
        kwargs = dict(
            conditions=[condition],
            prompt=prompt,
            negative_prompt=NEGATIVE_PROMPT if use_neg else None,
            width=width, height=height,
            num_frames=num_frames,
            frame_rate=fps,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            generator=generator,
            denoise_strength=strength,
        )
        result = pipe(**kwargs, **_progress_kwargs(pipe, steps))

    else:
        # Fallback: just do T2V if unknown pipeline
        print(f"[videogen] WARN: Unknown V2V pipeline '{pipeline_name}', falling back to T2V", flush=True)
        return generate_t2v(args)

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    export_to_video(result.frames[0], output, fps=fps)
    print(f"[videogen] DONE: {output} ({time.time()-t0:.1f}s total)", flush=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: store_videogen.py <args_json_file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        args = json.load(f)

    mode = args.get("mode", "t2v")
    if mode == "v2v" and args.get("prev_video_path"):
        generate_v2v(args)
    else:
        generate_t2v(args)


if __name__ == "__main__":
    main()

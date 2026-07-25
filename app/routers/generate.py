"""generate routes."""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Form, UploadFile, File, Body
from deps import *
from services import *

router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str
    product_type: str = "T-Shirt"
    width: int = 1024
    height: int = 1024
    steps: int = 20
    variations: int = 2
    model: Optional[str] = None  # None → use default_model setting
    source: str = "pipeline"   # 'pipeline' | 'generator'

@router.post("/api/collection")
def make_collection_ep(background_tasks: BackgroundTasks, body: dict = Body(...)):
    """Generate a matching product-line COLLECTION from one design via ControlNet — each
    variant shares the source's composition. {design_id, variants:[prompt,…], strength?}.
    Fire-and-forget: variants land in the review queue as they finish."""
    import collection
    did = body.get("design_id")
    variants = [v for v in (body.get("variants") or []) if (v or "").strip()]
    if not did or not variants:
        raise HTTPException(400, "design_id and non-empty variants[] required")
    if not collection.controlnet_installed():
        raise HTTPException(400, "ControlNet Union model isn't installed — download it in Studio → Image → 🎛️ ControlNet")
    background_tasks.add_task(collection.make_collection, int(did), variants, float(body.get("strength", 0.8)))
    return {"ok": True, "queued": len(variants)}


@router.post("/api/generate")
def manual_generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    conn = get_conn()
    model = _resolve_model(conn, req.model)
    gen_ids = []
    for _ in range(req.variations):
        cur = conn.execute(
            "INSERT INTO generations (prompt,product_type,width,height,steps,model,source) VALUES (?,?,?,?,?,?,?)",
            (req.prompt, req.product_type, req.width, req.height, req.steps, model, req.source)
        )
        gen_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    for gid in gen_ids:
        background_tasks.add_task(run_generation, gid)
    return {"ok": True, "generation_ids": gen_ids}

@router.get("/api/generations")
def list_generations(status: Optional[str] = None):
    conn = get_conn()
    # nsfw-flagged jobs never appear here — they live only in /api/nsfw/library.
    if status:
        rows = conn.execute(
            "SELECT * FROM generations WHERE status=? AND COALESCE(nsfw,0)=0 "
            "ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM generations WHERE COALESCE(nsfw,0)=0 "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

class EnhanceRequest(BaseModel):
    prompt: str

@router.delete("/api/generations/{gen_id}")
def delete_generation(gen_id: int):
    """Delete a generation — used to clear FAILED image gens from the Studio / NSFW
    galleries (they're generations rows with no design, so the design-delete never
    reached them). Also removes any orphaned design row + on-disk file it produced."""
    import os
    conn = get_conn()
    row = conn.execute("SELECT image_path FROM generations WHERE id=?", (gen_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "generation not found")
    for d in conn.execute("SELECT image_path FROM designs WHERE generation_id=?", (gen_id,)).fetchall():
        try:
            if d["image_path"] and os.path.exists(d["image_path"]):
                os.remove(d["image_path"])
        except Exception:
            pass
    conn.execute("DELETE FROM designs WHERE generation_id=?", (gen_id,))
    if row["image_path"]:
        try:
            if os.path.exists(row["image_path"]):
                os.remove(row["image_path"])
        except Exception:
            pass
    conn.execute("DELETE FROM generations WHERE id=?", (gen_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/generations/{gen_id}/retry")
def retry_generation(gen_id: int, background_tasks: BackgroundTasks):
    """Re-run a failed (or any) generation with the SAME params — clones its row into a
    fresh generation and queues it, preserving model, source, dimensions, nsfw + category."""
    conn = get_conn()
    g = conn.execute("SELECT * FROM generations WHERE id=?", (gen_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "generation not found")
    keys = g.keys()
    cur = conn.execute(
        "INSERT INTO generations (prompt,product_type,width,height,steps,model,source,nsfw,nsfw_category) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (g["prompt"], g["product_type"], g["width"], g["height"], g["steps"], g["model"],
         g["source"] if "source" in keys else "pipeline",
         g["nsfw"] if "nsfw" in keys else 0,
         g["nsfw_category"] if "nsfw_category" in keys else None)
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    background_tasks.add_task(run_generation, new_id)
    return {"ok": True, "id": new_id}


@router.post("/api/enhance-prompt")
def enhance_prompt(req: EnhanceRequest):
    """Queue an LLM enhance task. Returns {task_id} for polling."""
    prompt = req.prompt
    def _work():
        text = _call_lmstudio(_get_enhance_system(), prompt, max_tokens=2000)
        return {"enhanced": text, "original": prompt}
    tid = orch.submit_llm(
        _work,
        desc=f"Enhance: {prompt[:50]}",
        retry_meta={"type": "enhance", "prompt": prompt},
        priority=0,   # user clicked Enhance and is waiting
        task="image_enhance",
    )
    return {"task_id": tid}

@router.post("/api/research-image")
async def research_from_image(request: Request):
    """Accept a base64 image, describe it via vision LLM, return as research result."""
    body = await request.json()
    b64  = body.get("image")      # data:image/...;base64,...
    if not b64:
        raise HTTPException(400, "No image provided")
    # Strip data URL prefix if present
    if ";base64," in b64:
        b64 = b64.split(";base64,", 1)[1]

    IMAGE_DESCRIBE_SYSTEM = """You are a creative merch designer. Describe the provided image in detail to create a print-on-demand merchandise prompt.
Focus on: visual style, subject matter, color palette, mood, any text/logos visible, what makes it funny or interesting.
Then suggest how it could be adapted into a merch design.
Return ONLY valid JSON with keys:
  description: detailed visual description (2-3 sentences)
  enhanced_prompt: vivid Stable Diffusion image generation prompt based on this image, 80-120 words
  title: suggested product title (max 8 words)
  tags: comma-separated tags (6-10)"""

    def _work():
        body_req = {
            "model":    ENHANCE_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text",      "text": "Describe this image for merch design:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]
            }],
            "max_tokens": 1000,
        }
        # Override system via first message
        body_req["messages"].insert(0, {"role": "system", "content": get_prompt('image_describe')})
        r = httpx.post(f"{LMSTUDIO_URL}/chat/completions", json=body_req, timeout=90)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            data = json.loads(raw)
        except Exception:
            data = {"description": raw, "enhanced_prompt": raw, "title": "Image Design", "tags": ""}
        return {"result": data}

    tid = orch.submit_llm(_work, desc="Image research", task="image_research")
    return {"task_id": tid}

@router.post("/api/research-prompt")
def research_prompt(req: EnhanceRequest):
    """Queue a deep-research LLM task. Returns {task_id} for polling."""
    prompt = req.prompt
    def _work():
        raw = _call_lmstudio(get_prompt('image_research'), prompt, max_tokens=3000, json_mode=True)
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            data = json.loads(raw)
        except Exception:
            data = {"research_summary": "", "enhanced_prompt": raw,
                    "title": prompt[:60], "tags": ""}
        return {"result": data, "original": prompt}
    tid = orch.submit_llm(
        _work,
        desc=f"Research: {prompt[:50]}",
        retry_meta={"type": "research", "prompt": prompt},
        task="image_research",
    )
    return {"task_id": tid}

@router.post("/api/designs/{design_id}/generate-listing")
def generate_listing_info(design_id: int):
    """Generate listing title/desc/tags. Uses proposal data if available, else LLM from prompt."""
    conn = get_conn()
    row = conn.execute("""
        SELECT d.prompt, p.title AS proposal_title,
               p.description AS proposal_description, p.tags AS proposal_tags
        FROM designs d
        LEFT JOIN generations g ON g.id = d.generation_id
        LEFT JOIN proposals   p ON p.id = g.proposal_id
        WHERE d.id=?
    """, (design_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Design not found")
    # Proposal has everything — return immediately, no LLM needed
    if row["proposal_title"] and row["proposal_description"] and row["proposal_tags"]:
        return {
            "task_id":     None,
            "ready":       True,
            "title":       row["proposal_title"],
            "description": row["proposal_description"],
            "tags":        row["proposal_tags"],
            "source":      "proposal",
        }
    # No proposal — generate via LLM
    prompt = row["prompt"] or ""
    def _work():
        import re as _re
        raw = _call_lmstudio(get_prompt('listing_copy'), f"Design concept/prompt: {prompt[:600]}", max_tokens=600)
        logger.info("generate_listing raw LLM response: %r", raw[:500])
        # Strip <think>...</think> reasoning blocks (Gemma/DeepSeek style)
        raw = _re.sub(r'<think>.*?</think>', '', raw, flags=_re.DOTALL).strip()
        title = desc = tags = ""
        for line in raw.splitlines():
            # Strip markdown bold/italic/backtick markers from the line
            l = _re.sub(r'^[*#`\s]+', '', line).strip()
            lu = l.upper()
            if lu.startswith("TITLE:"):              title = l[6:].strip()[:100]
            elif lu.startswith("DESCRIPTION:"):      desc  = l[12:].strip()[:400]
            elif lu.startswith("DESC:"):             desc  = l[5:].strip()[:400]
            elif lu.startswith("TAGS:"):             tags  = l[5:].strip()
            elif lu.startswith("TAG:"):              tags  = l[4:].strip()
            # accumulate description continuation lines
            elif desc and not title.endswith(l) and not lu.startswith("TITLE") and not lu.startswith("TAG"):
                if len(desc) < 380 and not lu.startswith(("{", "[")):
                    pass  # skip continuation to keep it simple
        if not title: title = prompt[:60]
        return {"title": title, "description": desc, "tags": tags, "source": "ai"}
    tid = orch.submit_llm(_work, desc=f"Listing info: {prompt[:40]}", task="listing_copy")
    return {"task_id": tid, "ready": False}

@router.post("/api/ai/suggest-price")
def ai_suggest_price(data: dict):
    """Ask the LLM for a market-aware retail price suggestion.
    Input: {product_type, title, base_cost_cents, margin_pct}
    Output: {price_cents, price_dollars, reasoning}
    """
    product_type  = data.get("product_type", "T-Shirt")
    title         = data.get("title", "") or ""
    base_cents    = int(data.get("base_cost_cents") or BASE_COSTS.get(product_type, 950))
    margin_pct    = float(data.get("margin_pct") or 40)

    # Math-based floor
    math_retail = calc_retail_price(base_cents, margin_pct)
    math_dollars = math_retail / 100

    PRICE_SYSTEM = (
        "You are a pricing expert for print-on-demand products sold on Etsy. "
        "Respond ONLY with valid JSON: {\"price\": <number>, \"reasoning\": \"<1-2 sentence reason>\"}"
    )
    user_msg = (
        f"Product: {product_type}\n"
        f"Design niche/title: {title or 'general humor/meme design'}\n"
        f"Printify production cost: ${base_cents/100:.2f}\n"
        f"Target gross margin: {margin_pct:.0f}%\n"
        f"Math-based minimum retail: ${math_dollars:.2f}\n"
        f"Etsy platform fees: ~6.5% transaction + ~3% payment processing + $0.20 listing ≈ 9.5% total\n\n"
        "Suggest the optimal retail price (as a decimal like 24.99). Consider:\n"
        "1. Competitive Etsy pricing for this niche/product type\n"
        "2. Buyer psychology (e.g. $24.99 beats $25.00)\n"
        "3. Must be at or above the math-based minimum\n"
        "4. Don't overprice vs. market (hurts conversion rate)\n\n"
        'Return ONLY: {"price": 24.99, "reasoning": "brief reason"}'
    )

    def _work():
        import re as _re
        raw = _call_lmstudio(get_prompt('pricing'), user_msg, max_tokens=800)
        # Try to extract JSON from the response (handles reasoning models that wrap JSON in thinking)
        price, reason = None, None
        # First: try direct JSON parse after stripping fences
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            parsed = json.loads(clean)
            price  = float(parsed.get("price", 0)) or None
            reason = str(parsed.get("reasoning", ""))
        except Exception:
            pass
        # Second: try regex extract of {"price": ...} from anywhere in the text
        if not price:
            m = _re.search(r'\{[^{}]*"price"\s*:\s*([\d.]+)[^{}]*\}', raw)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    price  = float(parsed.get("price", 0)) or None
                    reason = str(parsed.get("reasoning", ""))
                except Exception:
                    try: price = float(m.group(1))
                    except Exception: pass
        # Third: try bare number extraction
        if not price:
            m2 = _re.search(r'\$?([\d]{1,3}(?:\.\d{1,2})?)', raw)
            if m2:
                try: price = float(m2.group(1))
                except Exception: pass
        if not price:
            price  = math_dollars
            reason = f"Math-based price for {margin_pct:.0f}% margin on ${base_cents/100:.2f} base cost."
        price = max(price, math_dollars)  # never below math floor
        price_cents = int(round(price * 100))
        return {"price_cents": price_cents, "price_dollars": price_cents / 100, "reasoning": reason or f"AI-suggested ${price:.2f} for {product_type}"}

    tid = orch.submit_llm(_work, desc=f"Price suggest: {product_type}", task="pricing")
    return {"task_id": tid}

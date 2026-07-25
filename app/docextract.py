"""Document extract — the reusable "photo -> structured fields" primitive.

One generic endpoint (POST /api/extract) takes an uploaded image + a `schema` name
(receipt, bill, paycheck, generic, …) and returns a task_id. The task rides the
UNIFIED QUEUE like every other model call in this app (orch.submit_llm — see
orchestrator.py; it is the single authority for GPU model loading), calls a
vision-capable LM Studio model with the image + a schema-specific prompt, and
returns structured JSON that matches the schema's field list.

Reusable by design: SCHEMAS is a plain dict of name -> {defaults, prompt}. A new
consumer (Bills, Paychecks, an "attach doc" button anywhere) just adds a schema
entry here and calls the same endpoint / the same frontend widget
(static/js/doc-extract.js docExtract({schema, onResult})) — no new plumbing.

Vision-through-the-queue precedent: app/world_vision.py already does this for
sprite QA (resident-vision-model-preferred, orch.submit_llm via world_defs.run_llm_job)
and app/routers/resell/ai.py does it for resale photo analysis (orch.submit_llm with
an explicit vision model + image_url content parts). This module follows the same
shape: an explicit `model=` on submit_llm so the orch guarantees that model is
resident before the job runs, and a multimodal `content: [text, image_url]` message
— the format LM Studio's /v1/chat/completions accepts (confirmed by both call sites
above; app/llm_client.py's `_call_lmstudio` is text-only and NOT used here).
"""
import base64
import copy
import json
import logging
import mimetypes
import random
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, UploadFile, File

from config import DOCEXTRACT_UPLOADS
from deps import IMAGE_EXTS, LMSTUDIO_URL, _llm_headers
from orchestrator import orch

log = logging.getLogger("store")
router = APIRouter()


# ── schema registry — data, not code. Add a consumer by adding an entry. ──────
SCHEMAS = {
    "receipt": {
        "label": "Receipt",
        "defaults": {
            "merchant": "", "date": "", "total": None, "tax": None,
            "payment_method": "", "is_fuel": False, "gallons": None,
            "line_items": [],
        },
        "prompt": (
            "You are reading a photo of a purchase RECEIPT. Extract the following as "
            "STRICT JSON and output NOTHING else — no markdown, no commentary, no "
            "code fences:\n"
            "{\n"
            '  "merchant": "<store/business name, empty string if unreadable>",\n'
            '  "date": "<YYYY-MM-DD if legible, else empty string>",\n'
            '  "total": <final total paid, a plain number, no currency symbol>,\n'
            '  "tax": <sales tax amount, a plain number, or null if not shown>,\n'
            '  "payment_method": "<cash|credit|debit|card ending 1234, or empty string>",\n'
            '  "is_fuel": <true if this is a GAS STATION / FUEL pump receipt, else false>,\n'
            '  "gallons": <gallons of fuel purchased, a plain number, or null if not fuel>,\n'
            '  "line_items": [\n'
            '    {"description": "<item name>", "qty": <number, default 1>,\n'
            '     "unit_price": <number or null>, "amount": <line total, a number>,\n'
            '     "category": "<one of: gas/fuel, grocery, food, general, other>"}\n'
            "  ]\n"
            "}\n"
            "If a field is not visible on the receipt, use an empty string / null / empty "
            "list as shown above — never invent a value. For a gas station receipt: set "
            'is_fuel true, add ONE line item with category "gas/fuel" for the fuel '
            "purchase, and fill gallons from the pump printout if shown."
        ),
    },
    "bill": {
        "label": "Bill / statement",
        "defaults": {
            "biller": "", "amount_due": None, "due_date": "",
            "account_number": "", "category": "",
        },
        "prompt": (
            "You are reading a photo of a BILL or statement. Extract STRICT JSON, "
            "nothing else, no code fences:\n"
            "{\n"
            '  "biller": "<company/utility name>",\n'
            '  "amount_due": <plain number, no currency symbol>,\n'
            '  "due_date": "<YYYY-MM-DD if legible, else empty string>",\n'
            '  "account_number": "<as printed, or empty string>",\n'
            '  "category": "<one of: utilities, rent, insurance, subscription, medical, other>"\n'
            "}\n"
            "Use an empty string / null when a field is not visible — never invent one."
        ),
    },
    "paycheck": {
        "label": "Paycheck / pay stub",
        "defaults": {
            "employer": "", "pay_date": "", "gross_pay": None,
            "net_pay": None, "taxes_withheld": None, "hours": None,
        },
        "prompt": (
            "You are reading a photo of a PAY STUB. Extract STRICT JSON, nothing "
            "else, no code fences:\n"
            "{\n"
            '  "employer": "<employer/company name>",\n'
            '  "pay_date": "<YYYY-MM-DD if legible, else empty string>",\n'
            '  "gross_pay": <plain number, before withholding>,\n'
            '  "net_pay": <plain number, take-home>,\n'
            '  "taxes_withheld": <plain number, total taxes+deductions, or null>,\n'
            '  "hours": <hours worked this period, a number, or null if salaried/unknown>\n'
            "}\n"
            "Use null when a field is not visible — never invent one."
        ),
    },
    "generic": {
        "label": "Generic document",
        "defaults": {"title": "", "date": "", "amount": None, "notes": ""},
        "prompt": (
            "You are reading a photo of a document. Extract STRICT JSON, nothing "
            "else, no code fences:\n"
            "{\n"
            '  "title": "<what this document is / its heading>",\n'
            '  "date": "<YYYY-MM-DD if a date is legible, else empty string>",\n'
            '  "amount": <the single most relevant dollar amount, a plain number, or null>,\n'
            '  "notes": "<one short sentence summarizing the document>"\n'
            "}\n"
            "Use an empty string / null when a field is not visible — never invent one."
        ),
    },
}


# ── model selection: prefer a resident vision model (no swap), else the ──────
# configured slot (doc_extract_model, falling back to the sprite-reviewer slot).
# Mirrors app/world_vision.py's VISION_HINTS heuristic (reused, not duplicated).
def _vision_model() -> str:
    try:
        from orchestrator import _loaded_llms
        import world_vision as _wv
        for m in (_loaded_llms() or []):
            if m and _wv._is_vision(m):
                return m
    except Exception:
        pass
    import model_registry
    return model_registry.resolve("doc_extract_model") or model_registry.resolve("world_vision_model")


def _call_vision(model: str, data_url: str, prompt: str, max_tokens: int = 900) -> str:
    """Runs inside the orchestrator worker thread. LM Studio's /v1/chat/completions
    accepts a multimodal `content` list — [{"type":"text",...}, {"type":"image_url",...}]
    — exactly like app/routers/resell/ai.py's resell_analyze and app/world_vision.py's
    _call_vision use.

    reasoning_effort=none: confirmed live against this node — the default vision
    model (google/gemma-4-12b-qat) is a reasoning model that, without this, spends
    the ENTIRE token budget narrating a bullet-point plan and never emits the JSON
    (finish_reason=length, same failure mode app/llm_client.py's _call_lmstudio
    already works around for text calls). No-op for non-reasoning models."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "reasoning_effort": "none",
    }
    r = httpx.post(f"{LMSTUDIO_URL}/chat/completions", json=body, headers=_llm_headers(), timeout=120)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


# ── robust JSON extraction — strips <think> blocks and ``` fences the way the ──
# lyrics enhancer (app/routers/audio.py generate_lyrics) strips a fenced block,
# then falls back to the outermost {...} span so stray preamble/postamble text
# from a chatty model doesn't break json.loads.
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*(.*?)\s*```", re.S)


def _extract_json(raw: str) -> dict:
    if not raw:
        return {}
    txt = _THINK_RE.sub("", raw).strip()
    m = _FENCE_RE.search(txt)
    if m:
        txt = m.group(1).strip()
    start, end = txt.find("{"), txt.rfind("}")
    candidate = txt[start:end + 1] if (start != -1 and end != -1 and end > start) else txt
    for attempt in (candidate, txt):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _to_float(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except Exception:
        return default


def _normalize(schema: str, spec: dict, data: dict) -> dict:
    """Merge the model's (possibly partial/messy) JSON onto the schema's defaults so
    every consumer always gets the full, correctly-typed shape back."""
    out = copy.deepcopy(spec["defaults"])
    if isinstance(data, dict):
        for k, v in data.items():
            if k in out:
                out[k] = v
    for k in ("total", "tax", "gallons", "amount", "amount_due", "gross_pay",
              "net_pay", "taxes_withheld", "hours"):
        if k in out:
            out[k] = _to_float(out[k])
    if "is_fuel" in out:
        out["is_fuel"] = bool(out["is_fuel"])
    if "line_items" in out:
        items = []
        for it in (out["line_items"] or []):
            if not isinstance(it, dict):
                continue
            desc = str(it.get("description") or it.get("name") or "").strip()
            if not desc:
                continue
            items.append({
                "description": desc,
                "qty": _to_float(it.get("qty"), 1.0) or 1.0,
                "unit_price": _to_float(it.get("unit_price")),
                "amount": _to_float(it.get("amount")),
                "category": str(it.get("category") or "").strip().lower(),
            })
        out["line_items"] = items
    for k, v in out.items():
        if isinstance(v, str):
            out[k] = v.strip()
    out["schema"] = schema
    return out


@router.get("/api/extract/schemas")
def extract_schemas():
    """List available schemas (label + default field shape) so any future consumer
    can discover what's already supported before adding its own."""
    return {name: {"label": s["label"], "fields": list(s["defaults"].keys())}
            for name, s in SCHEMAS.items()}


@router.post("/api/extract")
async def extract_document(file: UploadFile = File(...),
                           doc_schema: str = Form("generic", alias="schema")):
    """Accept a photo + schema name, submit a vision job on the unified queue, return
    {task_id}. Poll GET /api/task/{task_id} — result is the normalized schema dict.
    (Python param is `doc_schema` / wire field stays "schema" — pydantic's BaseModel
    already owns a `.schema()` method, so a field literally named `schema` warns.)"""
    spec = SCHEMAS.get(doc_schema) or SCHEMAS["generic"]
    schema_key = doc_schema if doc_schema in SCHEMAS else "generic"

    ext = Path(file.filename or "doc.jpg").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, "Upload a JPG, PNG, WebP, GIF, or BMP image.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty upload")

    DOCEXTRACT_UPLOADS.mkdir(parents=True, exist_ok=True)
    safe_name = f"extract_{int(time.time())}_{random.randint(1000, 9999)}{ext}"
    (DOCEXTRACT_UPLOADS / safe_name).write_bytes(content)

    b64 = base64.b64encode(content).decode()
    mime = mimetypes.guess_type(safe_name)[0] or "image/jpeg"
    data_url = f"data:{mime};base64,{b64}"

    model = _vision_model()
    prompt = spec["prompt"]

    def _work():
        raw = _call_vision(model, data_url, prompt)
        data = _extract_json(raw)
        return _normalize(schema_key, spec, data)

    # priority=0: a person is standing there holding the receipt up to the camera.
    tid = orch.submit_llm(_work, desc=f"extract:{schema_key} {safe_name}",
                          model=model, priority=0, task="doc_extract")
    return {"task_id": tid, "schema": schema_key, "image_path": f"docextract_uploads/{safe_name}"}

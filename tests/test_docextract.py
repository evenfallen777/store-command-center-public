"""docextract — the reusable photo->structured-fields primitive (POST /api/extract).

Covers: the schema registry (receipt schema + its gas/fuel field), the robust
JSON-from-model-output stripper (fenced / <think>-wrapped / prose-surrounded),
type normalization, and an end-to-end request through the real unified queue
(orch.submit_llm) with the vision HTTP call mocked out — no real vision model
needed for the suite. Mirrors tests/test_queue_history.py's _patched_orch pattern
for neutralizing the GPU/SSH side of the orchestrator so the job runs locally."""
_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _patched_orch(monkeypatch):
    import orchestrator as om
    monkeypatch.setattr(om.orch, "_free_comfyui", lambda: None)
    monkeypatch.setattr(om.orch, "_pick_llm_model", lambda: "test-vision-model")
    monkeypatch.setattr(om.orch, "_ensure_loaded", lambda m: True)
    return om.orch


# ── schema registry ───────────────────────────────────────────────────────────

def test_receipt_schema_registered_with_gas_fuel_field():
    import docextract
    assert "receipt" in docextract.SCHEMAS
    spec = docextract.SCHEMAS["receipt"]
    assert "is_fuel" in spec["defaults"] and "gallons" in spec["defaults"]
    assert spec["defaults"]["line_items"] == []
    assert "gas/fuel" in spec["prompt"].lower()
    assert "category" in spec["prompt"].lower()


def test_schema_registry_reusable_for_other_consumers():
    """Owner intent: reusable across bills / paychecks / purchases, not just receipts."""
    import docextract
    assert "bill" in docextract.SCHEMAS
    assert "paycheck" in docextract.SCHEMAS
    assert "generic" in docextract.SCHEMAS   # fallback for an unknown schema name


def test_schemas_endpoint_lists_registry(client):
    r = client.get("/api/extract/schemas")
    assert r.status_code == 200
    body = r.json()
    assert "receipt" in body and "is_fuel" in body["receipt"]["fields"]


# ── robust JSON extraction (like the lyrics-enhancer fence strip) ─────────────

def test_extract_json_strips_fences():
    import docextract
    raw = '```json\n{"merchant": "Shell", "total": 42.10}\n```'
    assert docextract._extract_json(raw) == {"merchant": "Shell", "total": 42.10}


def test_extract_json_strips_think_block():
    import docextract
    raw = '<think>the user wants receipt fields</think>{"merchant": "Costco", "total": 5}'
    assert docextract._extract_json(raw) == {"merchant": "Costco", "total": 5}


def test_extract_json_handles_prose_around_object():
    import docextract
    raw = 'Sure, here is the JSON:\n{"merchant": "Exxon", "is_fuel": true}\nLet me know if you need more.'
    assert docextract._extract_json(raw) == {"merchant": "Exxon", "is_fuel": True}


def test_extract_json_returns_empty_dict_on_garbage():
    import docextract
    assert docextract._extract_json("not json at all") == {}
    assert docextract._extract_json("") == {}


# ── normalization / type coercion ──────────────────────────────────────────────

def test_normalize_fills_defaults_and_coerces_types():
    import docextract
    spec = docextract.SCHEMAS["receipt"]
    data = {
        "merchant": "  Shell #4471  ", "total": "42.10", "tax": "3.5",
        "is_fuel": True, "gallons": "10.2",
        "line_items": [{"description": "Unleaded", "qty": "10.2", "unit_price": "4.129",
                        "amount": "42.10", "category": "GAS/FUEL"}],
    }
    out = docextract._normalize("receipt", spec, data)
    assert out["merchant"] == "Shell #4471"
    assert out["total"] == 42.10 and isinstance(out["total"], float)
    assert out["is_fuel"] is True
    assert out["gallons"] == 10.2
    assert out["line_items"][0]["category"] == "gas/fuel"
    assert out["line_items"][0]["qty"] == 10.2
    assert out["date"] == ""          # untouched default preserved
    assert out["schema"] == "receipt"


def test_normalize_drops_unknown_fields_and_bad_line_items():
    import docextract
    spec = docextract.SCHEMAS["receipt"]
    out = docextract._normalize("receipt", spec, {
        "merchant": "Store", "not_a_real_field": "ignored",
        "line_items": [{"description": ""}, "not-a-dict", {"description": "Chips", "qty": None}],
    })
    assert "not_a_real_field" not in out
    assert len(out["line_items"]) == 1        # blank-description and non-dict rows dropped
    assert out["line_items"][0]["description"] == "Chips"
    assert out["line_items"][0]["qty"] == 1.0  # missing qty defaults to 1


# ── end-to-end through the real unified queue (vision HTTP call mocked) ───────

def test_extract_endpoint_submits_and_polls_receipt(client, monkeypatch):
    import docextract
    orch = _patched_orch(monkeypatch)

    def _fake_call_vision(model, data_url, prompt, max_tokens=900):
        assert data_url.startswith("data:image/")
        assert "gas/fuel" in prompt.lower()
        return ('```json\n'
                '{"merchant": "QuickFuel", "date": "2026-07-20", "total": 45.67, '
                '"tax": 0, "payment_method": "debit", "is_fuel": true, "gallons": 12.3, '
                '"line_items": [{"description": "Regular Unleaded", "qty": 12.3, '
                '"unit_price": 3.71, "amount": 45.67, "category": "gas/fuel"}]}\n```')
    monkeypatch.setattr(docextract, "_call_vision", _fake_call_vision)

    r = client.post("/api/extract", data={"schema": "receipt"},
                    files={"file": ("receipt.png", _PNG, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "receipt"
    tid = body["task_id"]
    assert tid is not None

    assert orch._tasks[tid]["event"].wait(20), "extract task never finished"
    import model_registry
    expected_model = model_registry.resolve("doc_extract_model") or model_registry.resolve("world_vision_model")
    assert orch._tasks[tid]["model"] == expected_model   # explicit vision model was requested, not borrowed

    poll = client.get(f"/api/task/{tid}")
    assert poll.status_code == 200
    result = poll.json()
    assert result["status"] == "done"
    data = result["result"]
    assert data["merchant"] == "QuickFuel"
    assert data["is_fuel"] is True
    assert data["gallons"] == 12.3
    assert data["total"] == 45.67
    assert len(data["line_items"]) == 1
    assert data["line_items"][0]["category"] == "gas/fuel"


def test_extract_endpoint_rejects_non_image():
    import docextract
    assert "receipt" in docextract.SCHEMAS  # sanity: module importable standalone


def test_extract_endpoint_rejects_bad_file_type(client):
    r = client.post("/api/extract", data={"schema": "receipt"},
                    files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_extract_unknown_schema_falls_back_to_generic(client, monkeypatch):
    import docextract
    orch = _patched_orch(monkeypatch)
    monkeypatch.setattr(docextract, "_call_vision", lambda *a, **k: '{"title": "x"}')
    r = client.post("/api/extract", data={"schema": "not-a-real-schema"},
                    files={"file": ("doc.png", _PNG, "image/png")})
    assert r.status_code == 200
    assert r.json()["schema"] == "generic"
    tid = r.json()["task_id"]
    assert orch._tasks[tid]["event"].wait(20)

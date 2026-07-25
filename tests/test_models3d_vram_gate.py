"""3D generator VRAM preflight (services_3d.check_3d_vram_or_raise / gpu_capacity_mb)
and the `generator` column persisted on the models3d row.

Calls generate_model3d_ep() as a plain function (not through the TestClient/ASGI
lifecycle) so its BackgroundTasks are recorded but never actually RUN — the real
pipeline (SSH to the GPU box, SDXL render, etc.) never executes; we only assert on
the synchronous INSERT + the preflight gate.
"""
import pytest
from fastapi import BackgroundTasks, HTTPException

import services_3d
from db import get_conn
from routers.models3d import generate as gen_router


def _row_generator(model_id):
    conn = get_conn()
    row = conn.execute("SELECT generator FROM models3d WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row["generator"] if row else None


def test_check_3d_vram_or_raise_blocks_undersized(monkeypatch):
    monkeypatch.setattr(services_3d, "gpu_capacity_mb", lambda: 12000)
    gen = {"key": "trellis", "label": "TRELLIS (Microsoft) — top quality, experimental",
           "min_vram_mb": 16000}
    with pytest.raises(HTTPException) as exc:
        services_3d.check_3d_vram_or_raise(gen)
    assert exc.value.status_code == 400
    msg = exc.value.detail
    assert "16GB" in msg
    assert "12GB" in msg
    assert "TripoSR" in msg


def test_check_3d_vram_or_raise_allows_when_capacity_fits(monkeypatch):
    monkeypatch.setattr(services_3d, "gpu_capacity_mb", lambda: 24000)
    gen = {"key": "trellis", "label": "TRELLIS", "min_vram_mb": 16000}
    services_3d.check_3d_vram_or_raise(gen)  # must not raise


def test_check_3d_vram_or_raise_does_not_block_on_unknown_capacity(monkeypatch):
    # Node unreachable / nvidia-smi missing -> gpu_capacity_mb() returns None.
    # We only refuse when capacity is POSITIVELY known to be too small.
    monkeypatch.setattr(services_3d, "gpu_capacity_mb", lambda: None)
    gen = {"key": "trellis", "label": "TRELLIS", "min_vram_mb": 16000}
    services_3d.check_3d_vram_or_raise(gen)  # must not raise


def test_check_3d_vram_or_raise_noop_without_min_vram_field():
    services_3d.check_3d_vram_or_raise({"key": "mystery"})  # no min_vram_mb -> no-op


def test_generate_endpoint_refuses_trellis_on_this_node(client, monkeypatch):
    monkeypatch.setattr(services_3d, "gpu_capacity_mb", lambda: 12000)
    req = gen_router.Generate3dRequest(image_path="/tmp/does-not-matter.png", generator="trellis")
    with pytest.raises(HTTPException) as exc:
        gen_router.generate_model3d_ep(req, BackgroundTasks())
    assert exc.value.status_code == 400
    assert "TRELLIS" in exc.value.detail
    assert "16GB" in exc.value.detail and "12GB" in exc.value.detail


def test_generate_endpoint_allows_triposr_and_persists_generator(client, monkeypatch):
    monkeypatch.setattr(services_3d, "gpu_capacity_mb", lambda: 12000)
    req = gen_router.Generate3dRequest(image_path="/tmp/does-not-matter.png", generator="triposr")
    result = gen_router.generate_model3d_ep(req, BackgroundTasks())  # BackgroundTasks never run
    assert result["ok"] is True
    assert result["generator"] == "triposr"
    assert _row_generator(result["model_id"]) == "triposr"

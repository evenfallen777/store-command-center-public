"""Systems status board hygiene — app/systems_registry.py.

Covers the 2026-07-21 hygiene pass: the board snapshot still builds; the removed
world_require_review setting has no ghost entry; the de-duplicated board rows
(world_vision_model, world_layout_autosave) are gone; the reclassified rows
(world_rank, world_learn, world_balance) carry their new classify/visibility;
and the newly-added rows (esp. world_public_snapshot) exist with a real toggle.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def _by_key(systems, key):
    return next((s for s in systems if s["key"] == key), None)


def test_snapshot_builds_and_never_raises():
    import systems_registry
    snap = systems_registry.snapshot()
    assert snap["systems"], "catalog should not be empty"
    assert snap["counts"]["total"] == len(snap["systems"])


def test_world_require_review_fully_removed():
    import systems_registry
    import world_settings

    snap = systems_registry.snapshot()
    keys = {s["key"] for s in snap["systems"]}
    setting_keys = {s.get("setting_key") for s in snap["systems"]}
    assert "dup_require_review" not in keys
    assert "world_require_review" not in setting_keys
    assert "world_require_review" not in world_settings.DEFAULTS
    assert "world_require_review" not in world_settings.BOOL_KEYS


def test_duplicate_board_surfaces_dropped():
    import systems_registry
    snap = systems_registry.snapshot()
    keys = {s["key"] for s in snap["systems"]}
    assert "orphan_world_vision_model" not in keys
    assert "orphan_world_layout_autosave" not in keys
    # world_vision_model is still governed (Settings -> Models); just not free-text on the board.
    setting_keys = {s.get("setting_key") for s in snap["systems"]}
    assert "world_vision_model" not in setting_keys


def test_reclassified_rows():
    import systems_registry
    snap = systems_registry.snapshot()
    rank = _by_key(snap["systems"], "world_rank")
    learn = _by_key(snap["systems"], "world_learn")
    balance = _by_key(snap["systems"], "world_balance")
    assert rank["classify"] == "always" and rank["world_visible"] is True
    assert learn["classify"] == "always" and learn["world_visible"] is True
    assert balance["classify"] == "infra"
    assert rank["status"] == "enabled"
    assert learn["status"] == "enabled"
    assert balance["status"] == "infra"


def test_new_rows_added_for_previously_uncataloged_systems():
    import systems_registry
    snap = systems_registry.snapshot()
    systems = snap["systems"]
    for key in ("world_era", "world_era_sprites", "world_run", "world_sell",
                "world_taste", "world_renew", "world_public_snapshot"):
        row = _by_key(systems, key)
        assert row is not None, f"missing catalog row for {key}"

    snapshot_row = _by_key(systems, "world_public_snapshot")
    assert snapshot_row["setting_key"] == "world_public_snapshot"
    assert snapshot_row["classify"] == "toggle"
    assert snapshot_row["editable"] is True
    assert snapshot_row["setting_type"] == "bool"
    # default OFF (no row written in the throwaway test DB yet)
    assert snapshot_row["status"] == "disabled"


def test_world_public_snapshot_toggle_writes_through_settings_api(client):
    """The board's inline toggle for the new security-relevant row goes through the
    real generic settings write path, same as every other board toggle."""
    r = client.patch("/api/settings", json={"world_public_snapshot": "1"})
    assert r.status_code == 200
    r = client.get("/api/settings")
    assert r.json().get("world_public_snapshot") == "1"

    import systems_registry
    row = _by_key(systems_registry.snapshot()["systems"], "world_public_snapshot")
    assert row["status"] == "enabled"

    # leave it off again — default posture for this outbound-public path
    client.patch("/api/settings", json={"world_public_snapshot": "0"})

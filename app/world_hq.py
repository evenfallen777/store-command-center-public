"""THE COMPANY — HQ progression STAGES (era snapshots of the headquarters).

The HQ no longer has one eternal look: it evolves through STAGES (ages). Each
stage is a row in a ``world_meta`` JSON blob describing an era of the building:
its footprint/section layout and its style/look parameters. When the company
ADVANCES a stage, the current look is snapshotted INTO the stage being left
(so every earlier age is preserved and can be looked back on), and the next
stage's layout becomes the active one the client renders.

Stage ladder (today):
  1. founding    — the original single-rect modern block (13 dept rooms off a
                   central hallway, chamfered corners). Seeded as a SNAPSHOT of
                   whatever the HQ currently looks like.
  2. iron_steel  — the Iron & Steel Age: a multi-section industrial COMPOUND
                   (office wing + warehouse & shipping + utilities + loading
                   yard), steel/rivet walls with modern glass elements. NOT one
                   big rectangle. Gated on the world_tech IRON tier.

Advancing is tied to the EXISTING tech-tier progression (``world_tech.TIERS``:
wood→stone→bronze→iron→steel): a stage unlocks once ``tier_index`` reaches its
``tier_required``. Switching back to a prior stage is always allowed (viewing
an earlier age); switching forward re-checks the gate.

State (world_meta, like world_era's blob — no new table / migration needed):
  hq_stages        JSON list of stage objects
  hq_stage_active  the active stage id (int)

The client (static/js/world-map.js WM.setHqStage/applyHqStage) consumes the
GET snapshot and patches the HQ building descriptor before rasterizing. This
module is decoupled + import-safe and does NO LLM/GPU work (nothing here may
touch the orchestrator queue; if cognition is ever added it must ride
``world_defs.run_llm_job``).
"""
import json
import time

from world_defs import mget, mset, log_town
import world_tech as WT

STAGES_KEY = "hq_stages"
ACTIVE_KEY = "hq_stage_active"

# ── the Iron & Steel Age compound: bbox 30×18 tiles, four connected sections ──
# Section rects are bbox-LOCAL (lc/lr); ``doors`` are bbox-local wall tiles the
# client carves to FLOOR (external entrances + the links BETWEEN sections). The
# uncovered bbox cells (the pipe-alley courtyard NE of the office, the strip
# east of utilities) stay open plaza. ``open`` sections have no wall ring — the
# loading yard is a paved, crated work apron, not a room.
IRON_STEEL_LAYOUT = {
    "kind": "compound",
    "w": 30, "h": 18, "door": "S",             # main dock is on the warehouse's south wall (bbox door)
    "sections": [
        {"key": "office",    "label": "Office Wing",          "lc": 0,  "lr": 0, "w": 17, "h": 8,
         "theme": "#9aa3ad", "accent": "glass",
         "depts": ["storefront", "devlab", "resell", "trends", "portal", "social", "finance", "netsec"]},
        {"key": "utilities", "label": "Utilities",            "lc": 17, "lr": 0, "w": 8,  "h": 6,
         "theme": "#77675a", "accent": "rivet"},
        {"key": "warehouse", "label": "Warehouse & Shipping", "lc": 0,  "lr": 8, "w": 26, "h": 10,
         "theme": "#7e8894", "accent": "rivet",
         "depts": ["image", "video", "audio", "models3d", "publishing"]},
        {"key": "yard",      "label": "Loading Yard",         "lc": 26, "lr": 8, "w": 4,  "h": 10,
         "theme": "#8a8f99", "open": True},
    ],
    "doors": [
        {"lc": 0,  "lr": 3},                    # office staff entrance (west, onto the plaza)
        {"lc": 7,  "lr": 7},  {"lc": 7,  "lr": 8},    # office ↔ warehouse link (double wall)
        {"lc": 16, "lr": 2},  {"lc": 17, "lr": 2},    # office ↔ utilities link (double wall)
        {"lc": 20, "lr": 5},                    # utilities → pipe-alley courtyard
        {"lc": 20, "lr": 8},                    # courtyard → warehouse
        {"lc": 14, "lr": 17}, {"lc": 15, "lr": 17},   # MAIN shipping dock (south, bbox door)
        {"lc": 25, "lr": 12}, {"lc": 25, "lr": 13},   # warehouse → loading-yard dock (east)
    ],
}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hq_from_layout(c):
    """The HQ building descriptor from the user's saved map layout (play-god),
    or None when the map is procedural. This IS the 'current look' we snapshot."""
    try:
        raw = mget(c, "layout")
        if not raw:
            return None
        lay = json.loads(raw)
        for b in (lay or {}).get("buildings") or []:
            if b.get("kind") == "hq":
                keep = {k: b[k] for k in ("c", "r", "w", "h", "door", "label", "color", "interior") if k in b}
                return keep
    except Exception:
        pass
    return None


def _default_stages(c):
    """Seed: stage 1 = SNAPSHOT of the current HQ (the look it has today),
    stage 2 = the Iron & Steel Age compound, gated on the iron tech tier."""
    snap = _hq_from_layout(c) or {"c": None, "r": None, "w": 26, "h": 14, "door": "S",
                                  "label": "⬢ THE COMPANY HQ", "color": "#8fb3ff"}
    return [
        {"id": 1, "key": "founding", "name": "Founding Era", "emoji": "🏢",
         "tier_required": 0, "created_at": _now(), "snapshot_at": _now(),
         "style": {"wall": "#8b909c", "accent": None,
                   "desc": "Modern flat-roof block: chamfered corners, 13 department rooms "
                           "off a central hallway, skylights, glass face."},
         "layout": {"kind": "single", "w": snap.get("w", 26), "h": snap.get("h", 14),
                    "door": snap.get("door", "S"), "snapshot": snap}},
        {"id": 2, "key": "iron_steel", "name": "Iron & Steel Age", "emoji": "🏭",
         "tier_required": 3,                       # world_tech.TIERS index 3 = iron
         "created_at": _now(), "snapshot_at": None,
         "style": {"wall": "#8f98a4", "accent": "rivet",
                   "desc": "Industrial compound: steel-and-rivet office wing with modern glass "
                           "bands, a warehouse & shipping hall with production bays and a "
                           "conveyor dock, an iron utilities block (boiler/generator), and an "
                           "open loading yard. Multi-section — not one rectangle."},
         "layout": IRON_STEEL_LAYOUT},
    ]


def _save(c, stages):
    mset(c, STAGES_KEY, json.dumps(stages))


def stages(c):
    """The stage list, seeding it (and snapshotting today's HQ as stage 1) on
    first touch. New ladder rungs added to the code later are merged by key."""
    raw = mget(c, STAGES_KEY)
    if raw:
        try:
            lst = json.loads(raw)
            if isinstance(lst, list) and lst:
                have = {s.get("key") for s in lst}
                added = False
                for d in _default_stages(c):
                    if d["key"] not in have:
                        d["id"] = max(s.get("id", 0) for s in lst) + 1
                        lst.append(d)
                        added = True
                if added:
                    _save(c, lst)
                return lst
        except Exception:
            pass
    lst = _default_stages(c)
    _save(c, lst)
    if mget(c, ACTIVE_KEY) is None:
        mset(c, ACTIVE_KEY, 1)
    return lst


def active_id(c):
    try:
        return int(float(mget(c, ACTIVE_KEY, 1) or 1))
    except Exception:
        return 1


def _by_id(lst, sid):
    for s in lst:
        if s.get("id") == sid:
            return s
    return None


def _event(c, text):
    try:
        c.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)", ("", "hq", text))
    except Exception:
        pass


def _snapshot_into(c, lst, stage):
    """Preserve the CURRENT look inside ``stage`` before we leave it: the live
    HQ descriptor from the saved map layout (if the map was ever hand-edited)
    plus a timestamp. For procedural maps the seeded defaults already describe
    the look, so only the timestamp moves."""
    snap = _hq_from_layout(c)
    lay = stage.setdefault("layout", {})
    if snap and lay.get("kind") == "single":
        lay["snapshot"] = snap
        lay["w"], lay["h"] = snap.get("w", lay.get("w")), snap.get("h", lay.get("h"))
        lay["door"] = snap.get("door", lay.get("door", "S"))
    stage["snapshot_at"] = _now()
    _save(c, lst)


def advance(c):
    """Move to the NEXT stage on the ladder, gated on the tech tier. The stage
    being left keeps a snapshot of the current look (it becomes a browsable
    earlier age). Returns (ok, payload-or-reason)."""
    lst = stages(c)
    cur_id = active_id(c)
    cur = _by_id(lst, cur_id) or lst[0]
    nxt = _by_id(lst, cur["id"] + 1)
    if not nxt:
        return False, "The HQ is already in its final stage."
    tier = WT.tier_index(c)
    if tier < int(nxt.get("tier_required", 0)):
        need = WT.TIERS[min(len(WT.TIERS) - 1, int(nxt.get("tier_required", 0)))]
        return False, (f"The {nxt['name']} needs the {need['emoji']} {need['name']} tech tier "
                       f"(currently {WT.TIERS[tier]['name']}).")
    _snapshot_into(c, lst, cur)
    if not nxt.get("advanced_at"):
        nxt["advanced_at"] = _now()
        _save(c, lst)
    mset(c, ACTIVE_KEY, nxt["id"])
    _event(c, f"{nxt.get('emoji', '🏗️')} THE COMPANY HQ enters the {nxt['name']} — "
              f"the {cur['name']} look is preserved in the company archives.")
    log_town(f"HQ: rebuilt for the {nxt['name']}; the {cur['name']} era was archived.")
    return True, nxt


def set_active(c, sid):
    """Switch the rendered stage. Backward (viewing an earlier age) is always
    allowed; forward re-checks the tech-tier gate. Returns (ok, payload/reason)."""
    lst = stages(c)
    st = _by_id(lst, int(sid))
    if not st:
        return False, f"No HQ stage with id {sid}."
    cur_id = active_id(c)
    if st["id"] == cur_id:
        return True, st
    tier = WT.tier_index(c)
    if st["id"] > cur_id and tier < int(st.get("tier_required", 0)):
        need = WT.TIERS[min(len(WT.TIERS) - 1, int(st.get("tier_required", 0)))]
        return False, f"Locked — needs the {need['emoji']} {need['name']} tech tier."
    cur = _by_id(lst, cur_id)
    if cur:
        _snapshot_into(c, lst, cur)     # never lose the look we're leaving
    mset(c, ACTIVE_KEY, st["id"])
    _event(c, f"🏛️ HQ view switched to the {st['name']} stage.")
    return True, st


def snapshot(c):
    """Full read model for the client + HUD: every stage (with layouts, so a
    prior age can be rendered), the active id, and the live tech-tier gate."""
    lst = stages(c)
    act = active_id(c)
    tier = WT.tier_index(c)
    out = []
    for s in lst:
        req = int(s.get("tier_required", 0))
        out.append({**s, "active": s["id"] == act, "unlocked": tier >= req or s["id"] <= act,
                    "tier_required_name": WT.TIERS[min(len(WT.TIERS) - 1, req)]["name"]})
    nxt = _by_id(lst, act + 1)
    return {"stages": out, "active": act, "tier": tier,
            "tier_name": WT.TIERS[tier]["name"],
            "next": ({"id": nxt["id"], "name": nxt["name"],
                      "ready": tier >= int(nxt.get("tier_required", 0))} if nxt else None)}

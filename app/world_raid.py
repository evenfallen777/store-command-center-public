"""THE COMPANY — the Raid / Debug Mode (system D, combat v2).

Real security signals become monsters that assault the town in WAVES. Enemies
spawn from a random map edge and advance on the HQ; the town raises WALLS the
enemies must break through, and the defenders split into FIGHTERS (attack the
horde) and BUILDERS (raise & repair the walls) — so attack and defense skills
grow on different people. Waves keep coming until the real background work the
town is defending has finished (a manual DRILL runs a fixed set of waves ending
in a boss). Enemies come in TIERS and every few waves a BOSS leads them.

Defeating a real-threat monster runs the real defensive action:
- domain (Pi-hole blocked) → denylist it IF world_raid_autoblock (default OFF).
- finding (security scan) → mark it 'remediated'.
Generic 'raider' monsters (the endless assault) just give combat XP.

Storage: world_threats (per monster) + world_walls (4 sides). Phase via
world_orchestra. Decoupled; degrades gracefully.
"""
import time

from world_defs import mget, mset, log_town
import world_orchestra as WO
import world_skills as WS
import world_settings as ws

SIDES = ["N", "E", "S", "W"]

# enemy tiers (weak → strong); wave picks the tier, boss waves add a leader
TIERS = [
    {"mob": "skeleton_base",    "name": "goblin", "hp": 18, "size": 30, "dps": 2.0},
    {"mob": "orc_rogue",        "name": "raider", "hp": 30, "size": 34, "dps": 3.0},
    {"mob": "orc_warrior",      "name": "orc",    "hp": 48, "size": 38, "dps": 5.0},
    {"mob": "skeleton_warrior", "name": "wraith", "hp": 72, "size": 40, "dps": 7.0},
]
BOSS = {"mob": "skeleton_mage", "name": "WARLORD", "hp": 240, "size": 58, "dps": 14.0}

MAX_THREATS = 14                # cap on simultaneous on-screen enemies
WAVE_INTERVAL_SEC = 24          # a fresh wave at most this often
DRILL_WAVES = 3                 # a manual drill: 3 waves + a boss wave, then it ends
NETSEC_BACKLOG_RAID_THRESHOLD = 20   # pending security_findings this deep is a raid-worthy condition on its own

# ── combat v3: enemy TRAITS (behaviour derived from the mob) ──────────────────
# healer  — orc_shaman: hangs back and heals the most-wounded fellow monster
# runner  — rogues: fast, and they charge the WEAKEST wall (not their own edge)
# smash   — the boss: double wall damage + a periodic shockwave that chips the line
def _trait(mob, is_boss=0):
    if is_boss:
        return "smash"
    if mob == "orc_shaman":
        return "healer"
    if "rogue" in (mob or ""):
        return "runner"
    return "brute"

SHAMAN_HEAL_RATE = 10.0         # HP a shaman restores to a wounded monster / min
BOSS_SMASH_EVERY = 20.0         # seconds between the boss's shockwaves
BOSS_SMASH_CHIP = 3.0           # HP each front-liner loses to a shockwave (through cover)

# ── watchtower turrets: built structures fight back ───────────────────────────
TOWER_DPS = 9.0                 # damage per built watchtower / min

# ── drill readiness: practice pays off in real raids ──────────────────────────
READY_DECAY_PER_HR = 1.2        # readiness drifts back toward the floor over ~2 days
READY_FLOOR = 30.0
WALL_MAX = 120
WALL_START = 45                 # walls begin half-built; builders raise them
BASE_FIGHT_DPS = 5.0            # party attack per fighter / min (before skill)
BASE_BUILD_RATE = 9.0          # wall HP per builder / min (before skill)
HP_PER_SEVERITY = 8
# ── combat depth (#8): cover + downed-not-dead + doctor/rescue ──
DEFENDER_HP = 100.0            # each defender's raid health pool
COVER_MAX = 0.75              # intact walls block up to 75% of the damage that reaches defenders
MEDIC_FRACTION = 0.2         # ~1 in 5 defenders is a medic (min 1) — the highest-Knowledge ones
MEDIC_BASE_RATE = 22.0       # HP a medic restores to the wounded / min (before Knowledge)
REVIVE_AT = 55.0             # a downed agent tended back to this HP rejoins the fight
BASE_ENEMY_MELEE = 0.6       # fraction of a breached enemy's DPS that lands on defenders
ORC_MOBS = ["orc_warrior", "orc_rogue", "orc_shaman"]
SKELETON_MOBS = ["skeleton_warrior", "skeleton_rogue", "skeleton_mage", "skeleton_base"]


# ── schema ──
def _ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS world_threats(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raid_id INTEGER, kind TEXT, mob TEXT, label TEXT, ref TEXT,
        severity INTEGER, hp INTEGER, max_hp INTEGER,
        status TEXT DEFAULT 'active', slot INTEGER,
        created_at TEXT DEFAULT (datetime('now')))""")
    for col, ddl in (("edge", "edge TEXT"), ("tier", "tier INTEGER DEFAULT 0"),
                     ("is_boss", "is_boss INTEGER DEFAULT 0"), ("spawn_t", "spawn_t REAL"),
                     ("wave", "wave INTEGER DEFAULT 1"), ("size", "size INTEGER DEFAULT 32"),
                     ("dps", "dps REAL DEFAULT 3")):
        if not _has_cols(c, "world_threats", (col,)):
            try:
                c.execute(f"ALTER TABLE world_threats ADD COLUMN {ddl}")
            except Exception:
                pass
    c.execute("""CREATE TABLE IF NOT EXISTS world_walls(
        side TEXT PRIMARY KEY, hp INTEGER, max_hp INTEGER)""")


def _reset_combat(c):
    """Full HP, nobody downed — called when a raid begins."""
    try:
        c.execute("UPDATE world_agents SET raid_hp=?, downed=0, downed_at=0 "
                  "WHERE kind IN ('worker','openclaw')", (DEFENDER_HP,))
    except Exception:
        pass


def combat_roles(c, keys, raid_id):
    """Assign every defender a raid role. Medics = the highest-Knowledge fifth (min 1);
    the rest split fight/build by a per-raid hash so attack & defense skills spread.
    Single source of truth shared by the sim (for display) and raid_tick (for effect)."""
    if not keys:
        return {}
    ranked = sorted(keys, key=lambda k: -WS.level_of(WS.get_xp(c, k, "knowledge")))
    nmed = max(1, int(len(keys) * MEDIC_FRACTION))
    medics = set(ranked[:nmed])
    roles = {}
    for k in keys:
        if k in medics:
            roles[k] = "medic"
        else:
            roles[k] = "build" if (hash((k, raid_id)) % 2) else "fight"
    return roles


def _has_cols(c, table, cols):
    try:
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        return set(cols) <= have
    except Exception:
        return False


def _event(c, kind, text):
    try:
        c.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)", ("", kind, text))
    except Exception:
        pass


# ── scan real signals → threat specs ──
def scan_threats(c, limit=8):
    threats = []
    try:
        import pihole
        if pihole.configured():
            counts = {}
            for q in pihole.get_queries(200):
                if q.get("blocked") and q.get("domain"):
                    counts[q["domain"]] = counts.get(q["domain"], 0) + 1
            for i, (dom, n) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])[:4]):
                threats.append({"kind": "domain", "mob": ORC_MOBS[i % len(ORC_MOBS)],
                                "label": dom[:40], "ref": dom, "severity": min(10, 1 + n // 3)})
    except Exception:
        pass
    try:
        # FIFO (oldest first): LIFO here let a steady drip of NEW findings permanently
        # starve the backlog — a raid always grabbed the 4 newest and the oldest ones
        # never surfaced. Oldest-first means every finding eventually gets its raid turn.
        rows = c.execute("SELECT id, issue, priority FROM security_findings "
                         "WHERE status='pending' ORDER BY id ASC LIMIT 4").fetchall()
        pr = {"High": 8, "Medium": 5, "Low": 3}
        for i, r in enumerate(rows):
            threats.append({"kind": "finding", "mob": SKELETON_MOBS[i % len(SKELETON_MOBS)],
                            "label": (r["issue"] or "vulnerability")[:40], "ref": str(r["id"]),
                            "severity": pr.get(r["priority"], 4)})
    except Exception:
        pass
    # REAL attackers from the Command Center (SSH brute-force, web scanners) lead
    # the horde — the game finally fights what's actually knocking.
    try:
        import secaudit
        from cache import cached
        sev = {"critical": 10, "high": 8, "medium": 5, "low": 3}
        for i, a in enumerate(cached("sec:threats-lite", 300, secaudit.threats).get("threats", [])[:3]):
            threats.append({"kind": "attacker", "mob": ORC_MOBS[(i + 1) % len(ORC_MOBS)],
                            "label": f"{a.get('type', 'attacker')} {a.get('ip', '')}"[:40],
                            "ref": (a.get("block") or "")[:120],
                            "severity": sev.get(a.get("severity"), 5)})
    except Exception:
        pass
    return threats[:limit]


def _work_in_flight(c):
    """Real background work the town is 'defending' — the raid runs while this is > 0."""
    n = 0
    for sql in ("SELECT COUNT(*) FROM generations WHERE status IN ('processing','queued','running')",
                "SELECT COUNT(*) FROM videos WHERE status IN ('processing','queued','running')",
                "SELECT COUNT(*) FROM audio_clips WHERE status IN ('processing','queued','running')",
                "SELECT COUNT(*) FROM automation_log WHERE status='running' AND created_at > datetime('now','-10 minutes')"):
        try:
            n += c.execute(sql).fetchone()[0] or 0
        except Exception:
            pass
    return n


def active_threats(c):
    _ensure(c)
    return [dict(r) for r in c.execute(
        "SELECT * FROM world_threats WHERE status='active' ORDER BY slot").fetchall()]


def walls(c):
    _ensure(c)
    return {r["side"]: dict(r) for r in c.execute("SELECT * FROM world_walls").fetchall()}


# ── spawn a wave ──
def _spawn_wave(c, raid_id, wave, real_specs=None):
    is_boss_wave = wave % 4 == 0
    tier_i = min(wave // 2, len(TIERS) - 1)
    tier = TIERS[tier_i]
    target = min(MAX_THREATS, 6 + wave * 2)   # always a proper horde (>= ~8), grows fast each wave
    now = time.time()
    base_slot = int(float(mget(c, "threat_slot_seq", 0) or 0))
    specs = []
    if real_specs:                          # wave 1 carries the real threats first
        for s in real_specs[:target]:
            t = TIERS[min(len(TIERS) - 1, max(0, s["severity"] // 3))]
            specs.append({**s, "mob": s["mob"], "hp": max(t["hp"], s["severity"] * HP_PER_SEVERITY),
                          "size": t["size"], "dps": t["dps"], "tier": TIERS.index(t), "is_boss": 0})
    for i in range(max(0, target - len(specs))):   # fill up to the target with generic tier raiders
        specs.append({"kind": "raider", "mob": tier["mob"], "label": tier["name"], "ref": "",
                      "severity": tier_i + 1, "hp": tier["hp"], "size": tier["size"],
                      "dps": tier["dps"], "tier": tier_i, "is_boss": 0})
    if is_boss_wave:
        specs.append({"kind": "raider", "mob": BOSS["mob"], "label": BOSS["name"], "ref": "",
                      "severity": 12, "hp": BOSS["hp"], "size": BOSS["size"],
                      "dps": BOSS["dps"], "tier": len(TIERS), "is_boss": 1})
    for i, s in enumerate(specs):
        edge = SIDES[(base_slot + i) % 4] if not s.get("is_boss") else "S"
        c.execute("""INSERT INTO world_threats(raid_id,kind,mob,label,ref,severity,hp,max_hp,status,slot,
                        edge,tier,is_boss,spawn_t,wave,size,dps)
                     VALUES(?,?,?,?,?,?,?,?, 'active', ?, ?,?,?,?,?,?,?)""",
                  (raid_id, s["kind"], s["mob"], s["label"], s["ref"], s["severity"], s["hp"], s["hp"],
                   base_slot + i, edge, s["tier"], s.get("is_boss", 0), now, wave, s["size"], s["dps"]))
    mset(c, "threat_slot_seq", base_slot + len(specs))
    mset(c, "raid_wave", wave)
    mset(c, "last_wave_t", now)
    if is_boss_wave:
        _event(c, "raid", f"👹 WAVE {wave}: a {BOSS['name']} leads the assault!")
    else:
        _event(c, "raid", f"🌊 Wave {wave} — {len(specs)} {tier['name']}s charge the walls.")
    return len(specs)


# ── raise a raid ──
def trigger_raid(c, reason="security alert", drill=False):
    _ensure(c)
    if WO.phase(c) == "raid":
        return {"ok": False, "msg": "already raiding"}
    try:
        import world_security                       # REAL scan first: review every log + Pi-hole,
        world_security.run_security_scan(c)          # persist findings, and queue a model review
    except Exception:
        pass
    specs = scan_threats(c)
    if not specs and not drill:
        return {"ok": False, "msg": "no threats detected — all clear"}
    raid_id = int(float(mget(c, "raid_counter", 0) or 0)) + 1
    mset(c, "raid_counter", raid_id)
    mset(c, "raid_mode", "drill" if drill else "auto")
    mset(c, "raid_wave", 0)
    mset(c, "threat_slot_seq", 0)
    mset(c, "raid_kills", "{}")
    mset(c, "boss_smash_t", 0)
    wbonus = 1.0 + float(mget(c, "research_wall_bonus", 0.0) or 0.0)   # research: Security Systems (#7)
    # the REAL defenses are the town's walls: with all 14 Command-Center defenses on
    # the perimeter is reinforced; turn them off and the walls raise weaker.
    shield = None
    try:
        import world_security
        p = world_security.real_posture(c)
        shield = p.get("shield")
        if shield is not None:
            wbonus *= 0.55 + 0.6 * float(shield)
    except Exception:
        pass
    if not drill:                            # drilled crews raise sturdier walls when it's real
        wbonus *= 0.85 + 0.3 * (readiness(c) / 100.0)
    wmax, wstart = int(WALL_MAX * wbonus), int(WALL_START * wbonus)
    for side in SIDES:                       # walls start half-built (sturdier with Security research)
        c.execute("INSERT INTO world_walls(side,hp,max_hp) VALUES(?,?,?) "
                  "ON CONFLICT(side) DO UPDATE SET hp=?, max_hp=?", (side, wstart, wmax, wstart, wmax))
    _reset_combat(c)                         # everyone at full HP, nobody downed
    mset(c, "last_raid_t", time.time())      # stamp the cooldown clock (keeps raids occasional)
    WO.set_phase(c, "raid", reason)
    _event(c, "raid", "🚨 RAID — the perimeter is under attack! Fighters to the front, builders to the walls.")
    if shield is not None:
        _event(c, "raid", f"🛡️ Perimeter integrity {int(shield * 100)}% — real defenses online reinforce the walls.")
    n = _spawn_wave(c, raid_id, 1, real_specs=specs or None)
    log_town(f"RAID #{raid_id} ({reason}) — wave 1: {n} enemies.")
    return {"ok": True, "raid_id": raid_id, "threats": n}


def _more_waves_coming(c):
    mode = mget(c, "raid_mode", "auto")
    wave = int(float(mget(c, "raid_wave", 1) or 1))
    if mode == "drill":
        return wave < DRILL_WAVES + 1        # +1 for the closing boss wave
    return _work_in_flight(c) > 0            # auto raid runs while real work is in flight


# ── real defensive action on a kill ──
def _defeat(c, t, killer_key, killer_name=None):
    kind = t["kind"]
    if killer_key is None:                       # a turret kill — real action still runs, no skill XP
        _do_real_action = True
        killer_key = ""                           # WS.add_xp on '' is harmless (no such agent row)
    if kind == "domain":
        if ws.b("world_raid_autoblock", None):
            try:
                import pihole
                pihole.add_domain(t["ref"], "deny", comment="Blocked during Company raid")
                _event(c, "raid", f"🛡️ Blocked hostile domain {t['label']} (Pi-hole denylist).")
            except Exception as ex:
                _event(c, "raid", f"⚔️ Downed {t['label']} — block failed: {ex}.")
        else:
            _event(c, "raid", f"⚔️ Downed intruder {t['label']} (auto-block off — flagged for review).")
        WS.add_xp(c, killer_key, "attack", 40)
    elif kind == "finding":
        try:
            c.execute("UPDATE security_findings SET status='remediated', updated_at=datetime('now') WHERE id=?", (t["ref"],))
            # audit-alert-backed findings acknowledge the real alert when slain
            row = c.execute("SELECT fkey FROM security_findings WHERE id=?", (t["ref"],)).fetchone()
            if row and (row["fkey"] or "").startswith("event:"):
                c.execute("UPDATE security_events SET seen=1 WHERE id=?", (row["fkey"][6:],))
        except Exception:
            pass
        _event(c, "raid", f"🐛 Squashed bug: {t['label']}.")
        WS.add_xp(c, killer_key, "knowledge", 30)
        WS.add_xp(c, killer_key, "attack", 20)
    elif kind == "attacker":
        _event(c, "raid", f"⚔️ Repelled {t['label']}"
               + (f" — to ban for real: {t['ref']}" if t.get("ref") else " — review in Network Security → Threats") + ".")
        WS.add_xp(c, killer_key, "attack", 50)
        WS.add_xp(c, killer_key, "defense", 20)
    elif t.get("is_boss"):
        _event(c, "raid", f"🏆 The {t['label']} has fallen!")
        WS.add_xp(c, killer_key, "attack", 80)
    c.execute("UPDATE world_threats SET status='defeated' WHERE id=?", (t["id"],))


# ── combat, resolved each tick while phase == 'raid' ──
def raid_tick(c, dt):
    if WO.phase(c) != "raid":
        # a raid ended from outside (timeout backstop / stand-down) → any enemies
        # still standing slink away instead of haunting the next battle
        try:
            _ensure(c)
            n = c.execute("UPDATE world_threats SET status='retreated' WHERE status='active'").rowcount
            if n:
                _event(c, "raid", f"🏳️ {n} attacker(s) retreat as the raid winds down.")
        except Exception:
            pass
        return
    _ensure(c)
    # SELF-HEAL: a threat whose hp was persisted as 0 (legacy int() truncation of a
    # sub-1 hp) while status stayed 'active' can never be re-targeted — the fighter/
    # tower loops only hit hp>0 — so it lingers as a phantom "active" enemy and holds
    # the raid open until the 3× timeout backstop. Reap any dead-but-active threat so
    # the field can actually clear and the raid resolves normally.
    try:
        reaped = c.execute("UPDATE world_threats SET status='defeated' "
                           "WHERE status='active' AND hp<=0").rowcount
        if reaped:
            _event(c, "raid", f"⚰️ {reaped} fallen attacker(s) cleared from the field.")
    except Exception:
        pass
    raid_id = int(float(mget(c, "raid_counter", 0) or 0))
    wave = int(float(mget(c, "raid_wave", 1) or 1))
    last_wave = float(mget(c, "last_wave_t", 0) or 0)
    threats = active_threats(c)

    # spawn the next wave on cadence while enemies thin out and more are coming
    if _more_waves_coming(c) and (time.time() - last_wave) >= WAVE_INTERVAL_SEC and len(threats) <= 8:
        _spawn_wave(c, raid_id, wave + 1)
        threats = active_threats(c)

    # end the raid: nothing left AND no more coming → victory → recovery
    if not threats and not _more_waves_coming(c):
        WO.set_phase(c, "recovery", "field cleared")
        _event(c, "raid", f"✅ Raid repelled after {wave} wave(s) — stand down and recover.")
        # DRILLS GRADE THE TOWN: performance → readiness, which strengthens the
        # walls and the fighters in the NEXT real raid. Real victories drill too.
        try:
            wl_end = walls(c)
            cov = sum(w["hp"] for w in wl_end.values()) / (sum(w["max_hp"] for w in wl_end.values()) or 1)
            ndown = c.execute("SELECT COUNT(*) FROM world_agents WHERE downed=1 "
                              "AND kind IN ('worker','openclaw')").fetchone()[0] or 0
            ntot = c.execute("SELECT COUNT(*) FROM world_agents "
                             "WHERE kind IN ('worker','openclaw')").fetchone()[0] or 1
            score = cov * 50 + (1 - ndown / ntot) * 50
            if mget(c, "raid_mode", "auto") == "drill":
                grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"
                _set_readiness(c, score)
                _event(c, "raid", f"📋 Drill report: grade {grade} — walls {int(cov*100)}%, "
                                  f"{ndown} wounded. Readiness {int(score)}%.")
                log_town(f"DRILL grade {grade} (readiness {int(score)}%).")
            else:
                _set_readiness(c, readiness(c) + 6)     # real combat is the best practice
        except Exception:
            pass
        # any still-downed defenders are carried off and recover in the aftermath
        stillwounded = [r["key"] for r in c.execute(
            "SELECT key FROM world_agents WHERE downed=1 AND kind IN ('worker','openclaw')").fetchall()]
        _reset_combat(c)
        try:                                            # town-wide catharsis mood boost
            import world_mood
            keys = [r["key"] for r in c.execute("SELECT key FROM world_agents WHERE kind IN ('worker','openclaw')").fetchall()]
            world_mood.add_thought_all(c, keys, "survived the raid together!", 25, hours=12)
            for k in stillwounded:
                world_mood.add_thought(c, k, "wounded defending the town", -6, hours=14, unique=True)
        except Exception:
            pass
        return
    # NB: do NOT early-return when threats is momentarily empty — medics must keep
    # tending the wounded during the lull between waves (below). Empty threat loops
    # below are no-ops, so it's safe to fall through.

    # roster + combat state (raid_hp / downed) — single-source role split incl. medics
    rows = c.execute("SELECT key, raid_hp, downed, downed_at, blessed_until FROM world_agents "
                     "WHERE kind IN ('worker','openclaw')").fetchall()
    keys = [r["key"] for r in rows]
    if not keys:
        return
    hp = {r["key"]: (r["raid_hp"] if r["raid_hp"] is not None else DEFENDER_HP) for r in rows}
    down = {r["key"]: bool(r["downed"]) for r in rows}
    blessed = {r["key"]: (r["blessed_until"] or 0) for r in rows}   # god's buff fights too
    roles = combat_roles(c, keys, raid_id)
    live = lambda role: [k for k in keys if roles.get(k) == role and not down[k]]
    fighters, builders, medics = live("fight"), live("build"), live("medic")
    if not fighters:                               # tiny town / all fighters down → everyone standing swings
        fighters = [k for k in keys if not down[k]]
    m = dt / 60.0

    # BUILDERS raise / repair the walls (most-damaged first) + defense/construction XP
    wl = walls(c)
    build_power = (BASE_BUILD_RATE * len(builders) +
                   sum(WS.level_of(WS.get_xp(c, k, "defense")) for k in builders)) * m
    for side in sorted(wl, key=lambda s: wl[s]["hp"]):
        if build_power <= 0:
            break
        w = wl[side]
        need = w["max_hp"] - w["hp"]
        if need <= 0:
            continue
        add = min(need, build_power)
        c.execute("UPDATE world_walls SET hp=? WHERE side=?", (int(w["hp"] + add), side))
        build_power -= add
    for k in builders:
        WS.add_xp(c, k, "defense", max(1, int(7 * m)))
        WS.add_xp(c, k, "construction", max(1, int(4 * m)))

    # ENEMIES (v3, trait-driven): brutes batter their edge; RUNNERS charge the
    # weakest wall; the SHAMAN hangs back healing wounded monsters; the BOSS
    # smashes double-strength and looses periodic shockwaves. A breached wall
    # lets enemies through to maul the line. COVER = intact walls absorb damage.
    now = time.time()
    cover_frac = (sum(w["hp"] for w in wl.values()) /
                  (sum(w["max_hp"] for w in wl.values()) or 1))
    breach_dps = 0.0
    smash_waves = 0                              # boss shockwaves this tick (cover-piercing, flat chip)
    weakest = min(wl, key=lambda s: wl[s]["hp"]) if wl else "S"
    wounded_mobs = sorted((t for t in threats if t["hp"] < t["max_hp"]), key=lambda t: t["hp"])
    for t in threats:
        if now - (t["spawn_t"] or now) < 6:      # still marching in
            continue
        trait = _trait(t["mob"], t["is_boss"])
        if trait == "healer":                    # mends the horde instead of fighting
            if wounded_mobs:
                pt = wounded_mobs[0]
                nh = min(pt["max_hp"], pt["hp"] + SHAMAN_HEAL_RATE * m)
                c.execute("UPDATE world_threats SET hp=? WHERE id=?", (int(nh), pt["id"]))
                pt["hp"] = nh
            continue
        side = weakest if trait == "runner" else (t["edge"] or "S")
        dps = t["dps"] * (2.0 if trait == "smash" else 1.0)
        w = wl.get(side)
        if w and w["hp"] > 0:
            nh = max(0, w["hp"] - dps * m)
            if nh <= 0 and w["hp"] > 0:
                _event(c, "raid", f"🧱 The {side} wall is breached — get the wounded back!")
            c.execute("UPDATE world_walls SET hp=? WHERE side=?", (int(nh), side))
            wl[side]["hp"] = nh
        else:                                    # wall down on this edge → melee the line
            breach_dps += t["dps"]
        if trait == "smash":                     # shockwave chips everyone on the line
            last_sm = float(mget(c, "boss_smash_t", 0) or 0)
            if now - last_sm >= BOSS_SMASH_EVERY:
                mset(c, "boss_smash_t", now)
                smash_waves += 1
                _event(c, "raid", f"💥 The {t['label']} slams the ground — the whole line staggers!")

    # the FRONT LINE (who the breach can maul) is fight/build by role — medics stay back
    # tending the wounded, so they're not pulled into the damage line by the offense fallback.
    front = [k for k in keys if roles.get(k) in ("fight", "build") and not down[k]]
    # damage that lands on the defenders, reduced by remaining cover, spread over the living
    incoming = breach_dps * BASE_ENEMY_MELEE * (1.0 - COVER_MAX * cover_frac) * m
    # BOSS SHOCKWAVE (through cover, per the design): a flat CHIP on EVERY front-liner
    # per slam. The old form routed it through breach_dps with a stray 1/60 factor AND
    # the cover multiplier — the m's cancelled to ~0.008 HP/agent, i.e. the shockwave
    # visually fired but never actually hurt anyone. Now it lands as designed.
    chip = BOSS_SMASH_CHIP * smash_waves
    # FOCUS FIRE: enemies concentrate on the most-exposed (lowest-HP) defender, dropping
    # them one at a time — so medics can revive individuals while the rest hold the line.
    for k in sorted(front, key=lambda k: hp[k]):
        if incoming <= 0 and chip <= 0:
            break
        dealt = chip                              # the slam staggers everyone equally
        take = min(hp[k], incoming)
        incoming -= take
        dealt += take
        if dealt <= 0:
            continue
        hp[k] = max(0.0, hp[k] - dealt)
        if hp[k] <= 0 and not down[k]:
            down[k] = True
            c.execute("UPDATE world_agents SET raid_hp=0, downed=1, downed_at=? WHERE key=?", (now, k))
            nm = c.execute("SELECT name FROM world_agents WHERE key=?", (k,)).fetchone()
            _event(c, "raid", f"🩸 {nm['name'] if nm else 'A defender'} goes down at the breach — medic!")
        else:
            c.execute("UPDATE world_agents SET raid_hp=? WHERE key=?", (hp[k], k))

    # MEDICS tend the wounded (worst-hurt first): heal, and revive at REVIVE_AT → back in the fight
    downed_now = [k for k in keys if down[k]]
    if downed_now:
        tend = (MEDIC_BASE_RATE * max(1, len(medics)) +
                sum(WS.level_of(WS.get_xp(c, k, "knowledge")) for k in medics)) * m
        for k in sorted(downed_now, key=lambda k: hp[k]):
            if tend <= 0:
                break
            heal = min(tend, DEFENDER_HP - hp[k])
            hp[k] += heal
            tend -= heal
            if hp[k] >= REVIVE_AT:
                down[k] = False
                c.execute("UPDATE world_agents SET raid_hp=?, downed=0, downed_at=0 WHERE key=?", (hp[k], k))
                nm = c.execute("SELECT name FROM world_agents WHERE key=?", (k,)).fetchone()
                _event(c, "raid", f"⛑️ {nm['name'] if nm else 'A defender'} is patched up and back on their feet!")
                for md in medics:
                    WS.add_xp(c, md, "knowledge", 12)
                    try:
                        import world_rank
                        world_rank.add_assist(c, md, "revive")   # helping is a stat
                    except Exception:
                        pass
                try:
                    import world_mood
                    world_mood.add_thought(c, k, "pulled from the brink by a medic", 8, hours=10, unique=True)
                    for md in medics:
                        world_mood.add_thought(c, md, "saved a comrade under fire", 6, hours=10, unique=True)
                except Exception:
                    pass
            else:
                c.execute("UPDATE world_agents SET raid_hp=? WHERE key=?", (hp[k], k))
    for k in medics:
        WS.add_xp(c, k, "knowledge", max(1, int(4 * m)))

    # WATCHTOWERS fire first — every built tower is a real turret on the wall.
    alive = sorted((t for t in threats if t["hp"] > 0), key=lambda t: (t["is_boss"] or 0, t["id"]))
    ntowers = _towers(c)
    tpower = TOWER_DPS * ntowers * m
    while tpower > 0 and alive:
        t = alive[0]
        hit = min(t["hp"], tpower)
        t["hp"] -= hit
        tpower -= hit
        if t["hp"] <= 0:
            _defeat(c, t, None, killer_name="🏹 the Watchtower")
            _tally(c, "🏹 Watchtower")
            alive.pop(0)
        else:
            # floor of 1: a still-living threat (this branch is hp>0) must never persist
            # as hp=0, or it drops out of the hp>0 target list yet stays status='active'
            # and wedges the raid open forever (never re-hit → never _defeat).
            c.execute("UPDATE world_threats SET hp=? WHERE id=?", (max(1, round(t["hp"])), t["id"]))

    # FIGHTERS (v3): individual DUELS — each fighter locks a target and lands
    # their OWN damage (skill + blessing + drilled readiness + organised-line
    # bonus). Kills are credited to the actual killer. Engaging a through-the-
    # breach enemy costs you: it claws back at YOU specifically.
    rmult = 0.9 + 0.3 * (readiness(c) / 100.0)
    for k in fighters:
        WS.add_xp(c, k, "attack", max(1, int(6 * m)))
        if not alive:
            break
        t = alive[hash(k) % len(alive)]
        bless = 1.25 if (blessed.get(k) or 0) > now else 1.0
        dmg = (BASE_FIGHT_DPS + WS.level_of(WS.get_xp(c, k, "attack"))) \
            * (1.0 + 0.3 * cover_frac) * bless * rmult * m
        t["hp"] -= dmg
        side = t["edge"] or "S"
        if wl.get(side, {}).get("hp", 1) <= 0 and not down[k]:      # duel through the breach
            hp[k] = max(0.0, hp[k] - t["dps"] * 0.12 * m)
            c.execute("UPDATE world_agents SET raid_hp=? WHERE key=?", (hp[k], k))
        if t["hp"] <= 0:
            nm = c.execute("SELECT name FROM world_agents WHERE key=?", (k,)).fetchone()
            _defeat(c, t, k)
            _tally(c, nm["name"] if nm else k)
            try:
                from world_defs import log_agent
                log_agent(k, nm["name"] if nm else k, f"⚔️ Slew the {t['label']} (wave {t['wave']}).")
            except Exception:
                pass
            alive = [x for x in alive if x["id"] != t["id"]]
        else:
            # floor of 1: a still-living threat (this branch is hp>0) must never persist
            # as hp=0, or it drops out of the hp>0 target list yet stays status='active'
            # and wedges the raid open forever (never re-hit → never _defeat).
            c.execute("UPDATE world_threats SET hp=? WHERE id=?", (max(1, round(t["hp"])), t["id"]))


# ── readiness (drills matter) + kill tally + turret helpers ──────────────────
def readiness(c):
    """0-100: how drilled the town is. Decays toward the floor between drills."""
    val = float(mget(c, "raid_readiness", READY_FLOOR) or READY_FLOOR)
    ts = float(mget(c, "raid_readiness_t", 0) or 0)
    if ts:
        val -= (time.time() - ts) / 3600.0 * READY_DECAY_PER_HR
    return max(READY_FLOOR, min(100.0, val))


def _set_readiness(c, val):
    mset(c, "raid_readiness", round(max(READY_FLOOR, min(100.0, val)), 1))
    mset(c, "raid_readiness_t", time.time())


def _towers(c):
    try:
        return int(c.execute("SELECT COUNT(*) FROM world_structures "
                             "WHERE kind='watchtower' AND status='built'").fetchone()[0] or 0)
    except Exception:
        return 0


def _tally(c, name):
    import json as _json
    try:
        kills = _json.loads(mget(c, "raid_kills", "{}") or "{}")
        kills[name] = kills.get(name, 0) + 1
        mset(c, "raid_kills", _json.dumps(kills))
    except Exception:
        pass


# ── ticker cadence: scan in peacetime, auto-raise a raid when threats mount ──
def maybe_trigger(c):
    if ws.b("world_raid_disabled", None):
        return
    ph = WO.phase(c)
    if ph not in ("peace", "watch"):     # watch keeps scanning too (it used to dead-end)
        return
    now = time.time()
    last = float(mget(c, "last_raid_scan", 0) or 0)
    if last and now - last < 300:
        return
    mset(c, "last_raid_scan", now)
    if not last:
        return
    # Trigger on REAL subsystem FAILURES — NOT on normal Pi-hole tracker-blocking
    # (that's steady-state and would raid constantly). Blocked domains still become
    # enemies once a raid is on; here they only matter as an anomalous spike.
    issues = 0
    try:
        import world_security
        issues = sum(h["issues"] for h in world_security.scan_systems(c).values())
    except Exception:
        pass
    # a fresh unacknowledged high/critical audit alert is a raid on its own — the
    # nightly audit's regressions (new fail, new risky port, SSH login) hit the town
    alerts = 0
    try:
        alerts = int(c.execute(
            "SELECT COUNT(*) FROM security_events WHERE seen=0 AND severity IN ('high','critical') "
            "AND created_at > datetime('now','-2 hours')").fetchone()[0] or 0)
    except Exception:
        pass
    # The pending NetSec backlog is a trigger in its own right — findings pile up
    # continuously (world_security._persist_finding) but raids used to only fire on
    # UNRELATED subsystem/alert triggers, so a growing queue never got itself noticed.
    # A deep backlog now pressures a raid on its own (still gated by the cooldown below).
    backlog = 0
    try:
        backlog = int(c.execute(
            "SELECT COUNT(*) FROM security_findings WHERE status='pending'").fetchone()[0] or 0)
    except Exception:
        pass
    # Keep raids OCCASIONAL. A real COOLDOWN between raids was missing — only the 5-min
    # SCAN was throttled, so routine job flakiness re-raided every ~5 min (raid→recovery→
    # peace→raid). Tunable via world_raid_min_gap_min (default 60 min).
    try:
        gap_min = int(ws.i("world_raid_min_gap_min") or 60)
    except Exception:
        gap_min = 60
    last_raid = float(mget(c, "last_raid_t", 0) or 0)
    in_cooldown = bool(last_raid) and (now - last_raid) < gap_min * 60
    # Only a genuine SPIKE, a high/critical alert, or a deep NetSec backlog becomes a
    # raid — NOT 2-3 steady-state failed generation jobs (normal GPU contention).
    # Sub-threshold trouble is amber "watch" (unease, no combat), which is the honest
    # signal without a full assault every few minutes.
    backlog_spike = backlog >= NETSEC_BACKLOG_RAID_THRESHOLD
    real_attack = bool(alerts) or issues >= 4 or backlog_spike
    if real_attack and not in_cooldown:
        reason = "audit regression alert" if alerts else ("attack detected" if issues >= 4 else "netsec backlog overflow")
        trigger_raid(c, reason=reason)
    elif issues:
        WO.set_phase(c, "watch", "a subsystem is failing")
    elif ph == "watch":
        WO.set_phase(c, "peace", "all systems healthy again")


# ── snapshot for API/frontend ──
def snapshot(c):
    _ensure(c)
    ph = WO.phase(c)
    if ph not in ("raid", "recovery"):
        return {"phase": ph, "threats": [], "active": 0, "walls": {}, "wave": 0,
                "autoblock": bool(ws.b("world_raid_autoblock", None))}
    threats = [dict(r) for r in c.execute(
        "SELECT id,kind,mob,label,severity,hp,max_hp,status,slot,edge,tier,is_boss,spawn_t,wave,size,dps "
        "FROM world_threats WHERE raid_id=(SELECT MAX(raid_id) FROM world_threats) ORDER BY slot").fetchall()]
    active = [t for t in threats if t["status"] == "active"]
    wl = walls(c)
    cover = int((sum(w["hp"] for w in wl.values()) /
                 (sum(w["max_hp"] for w in wl.values()) or 1)) * 100)
    downed = c.execute("SELECT COUNT(*) FROM world_agents WHERE downed=1 "
                       "AND kind IN ('worker','openclaw')").fetchone()[0]
    import json as _json
    try:
        kills = sorted(_json.loads(mget(c, "raid_kills", "{}") or "{}").items(),
                       key=lambda kv: -kv[1])[:3]
    except Exception:
        kills = []
    for t in threats:
        t["trait"] = _trait(t["mob"], t["is_boss"])   # frontend behaviour hints
    return {"phase": ph, "threats": threats, "active": len(active),
            "walls": wl, "wave": int(float(mget(c, "raid_wave", 0) or 0)),
            "cover": cover, "downed": downed,        # combat depth (#8): cover % + wounded count
            "towers": _towers(c), "readiness": int(readiness(c)),
            "kills": kills, "mode": mget(c, "raid_mode", "auto"),
            "verdict": mget(c, "sec_last_verdict", None),   # the AI's security analysis (system D/chunk 2)
            "autoblock": bool(ws.b("world_raid_autoblock", None))}

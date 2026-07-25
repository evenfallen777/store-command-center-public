"""
The Company — voice & governance.

Everything the crew *says*: idle thoughts, business opinions, and the town meetings
where they vote the single top priority to fix next. Every LLM call is submitted to
the orchestrator queue via world_defs.run_llm_job (inside the submitted job we may
call the model directly — by then the orchestrator has arranged the GPU), so nothing
here ever collides with an image/video render. Meetings are pure CPU (no model).
"""
import time, json, random, re

from deps import get_conn, _call_lmstudio, orch
from world_defs import (DEPARTMENTS, run_llm_job, mget, mset, log_agent, log_town)

# ── canned fallbacks (used when the local model is busy/weak) ──────────────────
CANNED_THOUGHTS = {
    "working": ["Heads down on {d} work — almost there!", "Another one shipped. The {d} never sleeps.",
                "Coffee in, focus on, {d} humming.", "If I nail this I'm buying myself a pixel donut."],
    "leisure": ["Break time — I earned every pixel of it.", "Wonder if the arcade has a new high score.",
                "One more round, then back to the grind.", "Town's quiet today. I like it."],
    "sleep":   ["Zzz… dreaming of clean commits.", "Five more minutes… okay, ten.",
                "Do androids dream of tidy queues?"],
    "idle":    ["Is anyone going to give me a job?", "Broke and bored — I need work.",
                "Idle hands… I should tidy my desk."],
}
CANNED_OPINIONS = [
    ("Bundle slow-moving designs into discounted packs to clear inventory.", "pricing"),
    ("Auto-generate 3 title variants per listing and keep the best performer.", "marketing"),
    ("Re-run our top-selling design themes as fresh seasonal variants.", "products"),
    ("Schedule image generation overnight so the GPU is free by day.", "ops"),
    ("Cross-post every new product to the resell channels automatically.", "automation"),
    ("Tighten the review step — reject blurry renders before they reach Etsy.", "quality"),
    ("Raise prices 10% on the designs that never get discounted anyway.", "pricing"),
    ("Spin up a weekly trends scan and turn the top 3 into products.", "products"),
]


# ── text cleaning ─────────────────────────────────────────────────────────────
def _first_sentence(raw):
    t = (raw or "").strip()
    for ln in t.splitlines():
        ln = ln.strip().strip("*-•>#").strip()
        if ln:
            t = ln; break
    t = re.sub(r"[*_`]{1,3}", "", t)
    t = re.sub(r"^\s*(line|answer|idea|suggestion)\s*[:\-–]\s*", "", t, flags=re.I)
    t = t.strip().strip('"“”').strip()
    if "." in t:
        t = t.split(".")[0].strip() + "."
    return t[:200] if t else None

def _clean_thought(raw, name):
    t = (raw or "").strip()
    for line in t.splitlines():
        line = line.strip()
        if line:
            t = line; break
    t = t.strip("*-•>#").strip()
    t = re.sub(rf"^\s*{re.escape(name)}\s*[:\-–]\s*", "", t, flags=re.I)
    t = re.sub(r"^\s*(line|answer|name|role|character|thought|monologue)\s*[:\-–]\s*", "", t, flags=re.I)
    t = re.sub(r"[*_`]", "", t)                     # strip stray inline markdown
    t = t.strip().strip('"“”').strip()
    low = t.lower()
    bad = ("pixel-art game character" in low or "who works" in low or "job:" in low
           or "line:" in low or low.startswith(("job", "character", "role", "name", "answer"))
           or low.rstrip(".").strip() == name.lower() or len(t.split()) < 3)
    return None if (not t or bad) else t[:140]

def _canned_thought(a):
    pool = CANNED_THOUGHTS.get(a["state"], CANNED_THOUGHTS["idle"])
    dept = DEPARTMENTS.get(a["dept"], (a["dept"], ""))[0]
    return pool[(a["id"] + int(time.time() / 37)) % len(pool)].format(d=dept)


# ── thoughts ──────────────────────────────────────────────────────────────────
def agent_think(agent_id=None, wait=45):
    """One agent voices an inner thought (queued LLM; canned fallback)."""
    conn = get_conn()
    if agent_id:
        row = conn.execute("SELECT * FROM world_agents WHERE id=?", (agent_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM world_agents ORDER BY updated_at ASC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    a = dict(row)
    dept = DEPARTMENTS.get(a["dept"], (a["dept"], ""))[0]
    state_desc = {"working": f"busy doing {dept} work", "leisure": "on a break in town",
                  "sleep": "asleep at home", "idle": "waiting for work"}.get(a["state"], "around town")
    system = "You write one short, playful first-person line for a game character. Output the line only."
    mem = ""
    try:
        import world_memory
        m = world_memory.remember_context(a)      # their own lived moments, retrieved
        if m:
            mem = f"Recent memories: {m}\n"
    except Exception:
        pass
    prompt = ("Write ONE short in-character sentence (max 14 words). No name, no label, no quotes. "
              "If memories are given, let one colour the line.\n\n"
              "Job: a barista, on a break.\nLine: Ugh, I'd trade my apron for a nap right now.\n\n"
              "Job: a rocket engineer, busy at work.\nLine: Three more bolts and this baby is ready to fly!\n\n"
              f"Job: {a['name']} who works the {dept}, {state_desc}.\n{mem}Line:")

    def _job():
        text = _clean_thought(orch.llm_borrow(lambda: _call_lmstudio(system, prompt, 40)), a["name"]) or _canned_thought(a)
        c2 = get_conn()
        c2.execute("UPDATE world_agents SET mood=?, updated_at=datetime('now') WHERE id=?", (text, a["id"]))
        c2.execute("INSERT INTO world_events (agent_key,kind,text) VALUES (?,?,?)",
                   (a["key"], "thought", f"{a['name']}: {text}"))
        c2.commit(); c2.close()
        return {"agent_id": a["id"], "name": a["name"], "thought": text}

    res = run_llm_job(_job, "world:think", wait=wait)
    if res:
        return res
    return {"agent_id": a["id"], "name": a["name"], "thought": _canned_thought(a), "queued": True}


# ── opinions ──────────────────────────────────────────────────────────────────
def _store_suggestion(c, a, text, category):
    c.execute("INSERT INTO world_suggestions (agent_key,text,category) VALUES (?,?,?)",
              (a["key"], text[:200], category))
    c.execute("INSERT INTO world_events (agent_key,kind,text) VALUES (?,?,?)",
              (a["key"], "opinion", f"💡 {a['name']}: {text[:120]}"))
    c.execute("UPDATE world_agents SET fulfillment=MIN(100,COALESCE(fulfillment,0)+6) WHERE id=?", (a["id"],))

def generate_opinion(agent_id=None, wait=0):
    """One agent voices an improvement idea (queued LLM; canned fallback).
    wait=0 → fire-and-forget (the job persists itself); wait>0 → return the result."""
    conn = get_conn()
    if agent_id:
        row = conn.execute("SELECT * FROM world_agents WHERE id=?", (agent_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM world_agents ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    a = dict(row)
    system = "You are a candid employee brainstorming how to grow a small print-on-demand shop. Answer in one concrete sentence only."
    prompt = (f"You are {a['name']}, who works the {a['dept']}. Suggest ONE specific, actionable idea to "
              f"make the store more profitable or efficient. One sentence, no preamble.")

    def _job():
        text, category = None, "ops"
        line = _first_sentence(orch.llm_borrow(lambda: _call_lmstudio(system, prompt, 48)) or "")
        if line and len(line.split()) >= 4 and not line.lower().startswith(("you are", "job", "line", "answer")):
            text = line
        if not text:
            text, category = random.choice(CANNED_OPINIONS)
        c2 = get_conn()
        _store_suggestion(c2, a, text, category)
        c2.commit(); c2.close()
        log_agent(a["key"], a["name"], f"Suggested: {text}")
        return {"agent": a["name"], "text": text, "category": category}

    return run_llm_job(_job, "world:opinion", wait=wait)


# ── scheduled cognition batch ─────────────────────────────────────────────────
def run_cognition(conn, n_thoughts=6, n_opinions=2):
    """The crew 'wakes up' to think. Submits a batch of thought + opinion jobs at
    once; the orchestrator runs them back-to-back on a SINGLE model load, then the
    model can unload. This is the only scheduled LLM activity — no 24/7 generation.
    Each job is fire-and-forget (persists itself); returns how many were queued."""
    c = conn.cursor()
    ids = [r["id"] for r in c.execute(
        "SELECT id FROM world_agents ORDER BY updated_at ASC LIMIT ?", (n_thoughts,)).fetchall()]
    for aid in ids:
        try: agent_think(aid, wait=0)          # queued; persists a real thought
        except Exception: pass
    for _ in range(n_opinions):
        try: generate_opinion(wait=0)
        except Exception: pass
    return len(ids) + n_opinions


# ── town meetings (pure CPU — no model) ───────────────────────────────────────
def hold_meeting(conn):
    """The town votes on the single top priority to tackle next. If there aren't
    enough open suggestions, top up with canned ones (never blocks on the GPU)."""
    c = conn.cursor()
    open_s = [dict(r) for r in c.execute(
        "SELECT * FROM world_suggestions WHERE status='open' ORDER BY id DESC LIMIT 12").fetchall()]
    while len(open_s) < 3:
        text, cat = random.choice(CANNED_OPINIONS)
        who = c.execute("SELECT * FROM world_agents ORDER BY RANDOM() LIMIT 1").fetchone()
        if not who:
            break
        _store_suggestion(c, dict(who), text, cat)
        conn.commit()
        open_s = [dict(r) for r in c.execute(
            "SELECT * FROM world_suggestions WHERE status='open' ORDER BY id DESC LIMIT 12").fetchall()]
    if not open_s:
        return None
    candidates = open_s[:5]
    agents = [dict(r) for r in c.execute("SELECT * FROM world_agents").fetchall()]
    tally = {s["id"]: 0 for s in candidates}
    for a in agents:                       # each agent casts one weighted vote
        pick = random.choice(candidates)
        tally[pick["id"]] += 1 + (1 if (a["level"] or 1) >= 4 else 0)
    winner = max(candidates, key=lambda s: tally[s["id"]])
    for s in candidates:
        c.execute("UPDATE world_suggestions SET votes=?, status=? WHERE id=?",
                  (tally[s["id"]], "chosen" if s["id"] == winner["id"] else "shelved", s["id"]))
    tally_list = sorted(({"text": s["text"], "by": s["agent_key"], "votes": tally[s["id"]]}
                         for s in candidates), key=lambda x: -x["votes"])
    decision = winner["text"]
    c.execute("INSERT INTO world_meetings (topic,decision,tally) VALUES (?,?,?)",
              ("Top priority for the system", decision, json.dumps(tally_list)))
    mset(c, "priority", decision)
    # Turn the vote into the town's live, actionable mandate (supersede the old one).
    c.execute("UPDATE world_directives SET status='dropped', resolved_at=datetime('now') "
              "WHERE status='active'")
    c.execute("INSERT INTO world_directives (text,source) VALUES (?, 'meeting')", (decision,))
    c.execute("INSERT INTO world_events (agent_key,kind,text) VALUES (?,?,?)",
              (None, "meeting", f"🏛️ Town meeting: voted to prioritise — {decision[:120]}"))
    conn.commit()
    log_town(f"MEETING — voted top priority: {decision}  |  tally: " +
             ", ".join(f'{t["votes"]}×"{t["text"][:40]}"' for t in tally_list))
    return {"decision": decision, "tally": tally_list}

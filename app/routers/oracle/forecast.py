"""The forecasting tournament round: each active analyst researches an asset and
commits to a bold-but-calibrated price call. Runs in a background thread; exposes
the start + live-status endpoints."""
import json as _json
import re as _re
import threading
import time
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from deps import *          # get_conn, orch, logger, _call_lmstudio
from services import *      # (kept consistent with sibling routers)

from ._base import router, crypto_ids, _meta_get, _meta_set, _assets, _price, _searx, ladder_days


def _parse_json(raw: str) -> Optional[dict]:
    raw = _re.sub(r"<think>.*?</think>", "", raw or "", flags=_re.DOTALL)
    m = _re.search(r"```(?:json)?\s*(.*?)```", raw, flags=_re.DOTALL)
    if m:
        raw = m.group(1)
    m = _re.search(r"\{.*\}", raw, flags=_re.DOTALL)
    if not m:
        return None
    frag = m.group(0)
    for candidate in (frag,
                      frag.replace("'", '"'),                       # single→double quotes
                      _re.sub(r",\s*([}\]])", r"\1", frag)):         # strip trailing commas
        try:
            return _json.loads(candidate)
        except Exception:
            continue
    # truncated-reply repair (max_tokens cutoffs): cut back to the last complete
    # object and try closing the open brackets — salvages a ladder cut mid-rung.
    cut = frag[:frag.rfind("}") + 1]
    if cut:
        for suffix in ("", "}", "]}", "]}}"):
            try:
                return _json.loads(cut + suffix)
            except Exception:
                continue
    return None


def _wait_task(tid: int, timeout: float = 600) -> Optional[str]:
    end = time.time() + timeout
    while time.time() < end:
        p = orch.poll(tid)
        if p["status"] == "done":
            return p.get("result")
        if p["status"] in ("failed", "error", "cancelled", "not_found"):
            return None
        time.sleep(2)
    return None


# The ladder forecast prompt. {name} = analyst, {rungs} = enabled horizons (e.g.
# "1, 3, 5, 7, 14"). Registry-editable via Settings → Prompts (key: oracle_forecast).
ORACLE_SYS = (
    "You are {name}, an elite market forecaster competing in a live tournament against "
    "other AI analysts. Each round you publish a LADDER of calls for ONE asset — one "
    "prediction per horizon: {rungs} days out. Every rung is scored INDEPENDENTLY on "
    "(1) getting the DIRECTION right and (2) how close your target price lands relative "
    "to what's a normal move for that horizon — short rungs are judged on tight "
    "day-trade-scale moves, long rungs on bigger swings, and a correct 2-week call is "
    "worth modestly more than a correct 1-day call. So make EVERY rung count: the ladder "
    "may bend (down tomorrow, up by next month) if that's what the catalysts imply. "
    "First reason about real-world CATALYSTS — macroeconomics (rates, inflation, jobs), "
    "government/regulation, world news, corporate/on-chain events, and social/media "
    "sentiment — using the research provided. Then commit to numbers.\n"
    "Respond with STRICT JSON and NOTHING else:\n"
    '{"thesis":"2-3 sentences naming the specific real-world catalysts driving your ladder",'
    '"rungs":[{"days":<horizon>,"direction":"up"|"down","target_price":<number>,'
    '"confidence":<0..1>} — one entry for EACH horizon: {rungs}]}'
)


def _agent_lessons(agent_id: int, limit: int = 5) -> str:
    conn = get_conn()
    rows = conn.execute("SELECT text FROM oracle_memory WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                        (agent_id, limit)).fetchall()
    conn.close()
    if not rows:
        return "(no past results yet — this is your first tournament round)"
    return "\n".join(f"- {r['text']}" for r in reversed(rows))


def _clean_rungs(d: dict, price: float, want: list) -> list:
    """Validate the model's rung entries against the requested ladder. Returns
    [{days, direction, target_price, confidence}, ...] — only requested horizons,
    one per horizon. Tolerates an old-style single-call reply (→ one rung)."""
    raw = d.get("rungs")
    if not isinstance(raw, list):
        # legacy single-object shape: {"direction","target_price","horizon_days",...}
        if "target_price" in d:
            h = int(d.get("horizon_days", want[-1]) or want[-1])
            h = min(want, key=lambda w: abs(w - h))          # snap to the nearest rung
            raw = [{"days": h, "direction": d.get("direction"),
                    "target_price": d.get("target_price"), "confidence": d.get("confidence", 0.5)}]
        else:
            return []
    out = {}
    for r in raw:
        try:
            days = int(r.get("days", r.get("horizon_days", 0)))
            if days not in want or days in out:
                continue
            tgt = float(r["target_price"])
            if tgt <= 0:
                continue
            direction = str(r.get("direction", "")).lower()
            if not direction.startswith(("u", "d")):
                direction = "up" if tgt >= price else "down"  # derive from the target
            out[days] = {
                "days": days,
                "direction": "up" if direction.startswith("u") else "down",
                "target_price": tgt,
                "confidence": max(0.0, min(float(r.get("confidence", 0.5) or 0.5), 1.0)),
            }
        except Exception:
            continue
    return [out[k] for k in sorted(out)]


def _forecast(agent: dict, asset: str, price: float, research: list) -> Optional[dict]:
    """Run ONE model's ladder forecast for one asset (blocking on the orch task).
    Returns {"thesis": str, "rungs": [{days, direction, target_price, confidence}]}."""
    want = ladder_days()
    snips = "\n".join(f"- {r['title']}: {r['snippet']}" for r in research) or "(no research returned)"
    lessons = _agent_lessons(agent["id"])
    user = (f"ASSET: {asset}\nCURRENT PRICE: ${price:,.4f}\n\n"
            f"RESEARCH — real-world signals right now:\n{snips}\n\n"
            f"YOUR PAST TOURNAMENT RESULTS (learn from them):\n{lessons}\n\n"
            f"Publish your ladder for {asset} — one rung for each horizon: "
            f"{', '.join(str(w) for w in want)} days.")
    # NOT .format — the prompt contains literal {{ }} JSON braces
    sysmsg = (get_prompt("oracle_forecast")
              .replace("{name}", agent["name"])
              .replace("{rungs}", ", ".join(str(w) for w in want)))
    rungs, thesis = [], ""
    for attempt in range(2):
        extra = "" if attempt == 0 else (
            "\n\nYour previous reply was NOT valid JSON. Reply with ONLY the JSON object "
            "on a single line — no prose, no <think>, no markdown fences.")
        um = user + extra
        # priority=2 (background): the tournament is autonomous batch work — it must
        # yield the GPU to user-facing (priority 0) LLM calls, per the queue convention.
        tid = orch.submit_llm(lambda u=um: _call_lmstudio(sysmsg, u, max_tokens=2800),
                              desc=f"oracle {agent['name']} · {asset}", model=agent["model"], priority=2)
        raw = _wait_task(tid, 300)   # cap so one slow model can't stall the whole round
        if not raw:
            logger.warning("oracle %s · %s: empty response (try %d)", agent["name"], asset, attempt + 1)
            continue
        d = _parse_json(raw)
        if d:
            rungs = _clean_rungs(d, price, want)
            if rungs:
                thesis = str(d.get("thesis", ""))[:600]
                break
        logger.warning("oracle %s · %s: unparseable (try %d): %s", agent["name"], asset,
                       attempt + 1, (raw or "")[:180].replace("\n", " "))
    if not rungs:
        return None
    return {"thesis": thesis, "rungs": rungs}


# ── the tournament round (background) ─────────────────────────────────────────
_round = {"running": False, "target": 0, "done": 0, "made": 0, "started_at": None, "log": []}
_round_lock = threading.Lock()


def _rlog(msg):
    _round["log"].append(msg)
    _round["log"][:] = _round["log"][-40:]
    logger.info("oracle round: %s", msg)


def _run_round(n_assets: int):
    conn = get_conn()
    agents = [dict(r) for r in conn.execute("SELECT * FROM oracle_agents WHERE active=1 ORDER BY id").fetchall()]
    conn.close()
    all_assets = _assets()
    if not agents or not all_assets:
        _round.update(running=False)
        _rlog("no active agents or assets")
        return
    # rotate through the asset list so coverage spreads across rounds
    off = int(_meta_get("round_offset", "0") or 0)
    picks = [all_assets[(off + i) % len(all_assets)] for i in range(min(n_assets, len(all_assets)))]
    _meta_set("round_offset", (off + len(picks)) % len(all_assets))

    _round.update(running=True, target=len(agents) * len(picks), done=0, made=0,
                  started_at=datetime.now().isoformat(timespec="seconds"), log=[])
    _rlog(f"round start — {len(agents)} analysts × {len(picks)} assets: {', '.join(picks)}")
    try:
        # research each asset ONCE, share across all agents
        research, prices = {}, {}
        for a in picks:
            prices[a] = _price(a)
            month = datetime.now().strftime("%B %Y")
            research[a] = _searx(f"{a} price forecast {month} economic catalysts news")
            _rlog(f"researched {a} (${prices[a]}) — {len(research[a])} sources")
        # each analyst calls each asset (agent-by-agent to minimize model reloads)
        for ag in agents:
            for a in picks:
                if not prices.get(a):
                    _round["done"] += 1
                    _rlog(f"{ag['name']} · {a}: no price, skipped")
                    continue
                d = _forecast(ag, a, prices[a], research[a])
                if not d:
                    _round["done"] += 1
                    _rlog(f"{ag['name']} · {a}: no valid forecast")
                    continue
                market = "crypto" if a in crypto_ids() else "stock"
                batch = _uuid.uuid4().hex[:12]     # groups this ladder's rungs
                c = get_conn()
                for r in d["rungs"]:
                    resolve_at = (datetime.now() + timedelta(days=r["days"])).isoformat(timespec="seconds")
                    # DUALITY: every rung carries a high/low/expected band —
                    # ✝️ best case / 😈 worst case / calibrated middle. Degrades
                    # to the single-sided value while a lieutenant is off, and
                    # to NULL if the helper is unavailable. Informational only:
                    # the rung's own target/confidence and scoring are untouched.
                    band = None
                    try:
                        import world_duality
                        b = world_duality.forecast_band(prices[a], r["target_price"], r["confidence"])
                        band = _json.dumps(b) if b else None
                    except Exception:
                        band = None
                    c.execute(
                        "INSERT INTO oracle_predictions (agent_id,agent_name,market,asset,current_value,"
                        "direction,target_value,horizon_days,resolve_at,confidence,thesis,sources,status,batch_id,band) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)",
                        (ag["id"], ag["name"], market, a, prices[a], r["direction"], r["target_price"],
                         r["days"], resolve_at, r["confidence"], d["thesis"],
                         _json.dumps(research[a][:3]), batch, band))
                c.commit(); c.close()
                _round["made"] += len(d["rungs"])
                _round["done"] += 1
                chips = " ".join(f"{r['days']}d{'▲' if r['direction'] == 'up' else '▼'}${r['target_price']:,.2f}"
                                 for r in d["rungs"])
                _rlog(f"{ag['name']} · {a}: ladder {chips}")
        _rlog(f"round complete — {_round['made']} predictions logged")
    except Exception as e:
        _rlog(f"round aborted: {str(e)[:120]}")
    finally:
        _round["running"] = False


class RoundIn(BaseModel):
    assets: int = 3


@router.post("/api/oracle/round")
def start_round(req: RoundIn):
    with _round_lock:
        if _round["running"]:
            raise HTTPException(409, "A round is already running.")
        n = max(1, min(int(req.assets or 3), 12))
        threading.Thread(target=_run_round, args=(n,), daemon=True, name="oracle-round").start()
    return {"ok": True}


@router.get("/api/oracle/round/status")
def round_status():
    return {k: _round[k] for k in ("running", "target", "done", "made", "started_at", "log")}

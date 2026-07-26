"""📺 TV layer — shows, episodes, canon (docs/TV-DESIGN.md §2/§3/§5, P1).

Sits ON TOP of the Director studio: an episode IS a studio_projects row, so
storyboard → render → audio → assemble all reuse services_studio unmodified.
This module adds the writers' room (§3) — three LLM roles that all ride
orch.submit_llm exactly like submit_storyboard (robust JSON extraction via
services_studio._extract_json, hard server-side validation/clamping — never
trust the model — and NO auto-retry loops):

  showrunner         once per show: owner prompt → the show bible JSON
                     (premise/tone/world/style_guide/characters/running_gags/
                     season_arc/canon). Show status: new → bible → ready.
  episode writer     per episode: bible + recap (prior episodes' memory) →
                     title/synopsis/beats, then a studio project (kind long,
                     the show's render settings) via submit_storyboard with
                     the style_guide + character visual descriptors injected
                     into the notes so every shot prompt inherits them (§4
                     style lock). Episode status: writing → ready happens
                     LAZILY in the GET handlers once the storyboard lands.
  continuity editor  after produce: writes episode.memory and appends durable
                     new facts to bible.canon — the next episode inherits truth.

produce_episode() is the P1 manual per-episode driver: the UNMODIFIED
services_studio.produce_project() (whose _working registry keeps
project_active() truthful for the busy checks), then the project's final is
copied onto the episode for the TV player.
"""
import json
import logging
import shutil
from pathlib import Path

from db import get_conn
from orchestrator import orch
import services_studio as _studio
from services_studio import _extract_json, _as_text

logger = logging.getLogger("store")

_RECAP_EPISODES = 3     # how many prior episodes' memory feed the "previously on"
_MAX_CHARACTERS = 8     # bible cast clamp
_MAX_BEATS = 8          # episode beats clamp (storyboarder clamps scenes to 6 anyway)
_MAX_NEW_CANON = 8      # durable facts one episode may add
_CANON_CAP = 200        # bible.canon never grows unbounded


def _upd_episode(episode_id: int, **fields):
    """Set columns on an episode row (always touches updated_at)."""
    conn = get_conn()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE tv_episodes SET {sets},updated_at=datetime('now') WHERE id=?",
                 (*fields.values(), episode_id))
    conn.commit()
    conn.close()


def bible_of(show: dict) -> dict:
    """The show's bible parsed to a dict ({} when absent/corrupt)."""
    try:
        b = json.loads(show.get("bible") or "")
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _validate_bible(data: dict, style_hint: str = "") -> dict:
    """§3 hard clamp — never trust the model. Normalizes every bible field,
    requires a usable cast, and runs the UNCONDITIONAL safety floor over all
    model-authored text (a hit hard-fails the bible, same as storyboards)."""
    import nsfw

    chars = []
    for c in [c for c in (data.get("characters") or []) if isinstance(c, dict)][:_MAX_CHARACTERS]:
        name = _as_text(c.get("name"))[:60]
        if not name:
            continue
        chars.append({"name": name,
                      "visual_desc": _as_text(c.get("visual_desc"))[:400],
                      "voice": _as_text(c.get("voice"))[:200],
                      "quirks": _as_text(c.get("quirks"))[:300]})
    if not chars:
        raise ValueError("bible has no usable characters")
    style_guide = _as_text(data.get("style_guide"))[:600] or str(style_hint or "").strip() \
        or "2D adult animation, thick outlines, flat colors"
    # season_arc may arrive as text or as an object carrying an
    # episodes_per_season hint (consumed by the season/number auto-assign).
    arc = data.get("season_arc")
    if isinstance(arc, dict):
        per = arc.get("episodes_per_season")
        try:
            per = max(1, min(int(per), 24)) if per is not None else None
        except (TypeError, ValueError):
            per = None
        arc = {"summary": _as_text(arc.get("summary") or arc.get("arc"))[:1500],
               **({"episodes_per_season": per} if per else {})}
    else:
        arc = _as_text(arc)[:1500]
    bible = {
        "title":        _as_text(data.get("title"))[:120],
        "premise":      _as_text(data.get("premise"))[:2000],
        "tone":         _as_text(data.get("tone"))[:500],
        "world":        _as_text(data.get("world"))[:2000],
        "style_guide":  style_guide,
        "characters":   chars,
        "running_gags": [_as_text(g)[:200] for g in (data.get("running_gags") or [])
                         if _as_text(g)][:8],
        "season_arc":   arc,
        "canon":        [_as_text(x)[:300] for x in (data.get("canon") or [])
                         if _as_text(x)][:_CANON_CAP],
    }
    # ── safety floor (unconditional — mirrors _validate_storyboard)
    blob = [bible["premise"], bible["world"], bible["style_guide"],
            arc["summary"] if isinstance(arc, dict) else arc]
    blob += [f"{c['visual_desc']} {c['quirks']}" for c in chars]
    reason = nsfw.safety_check(*blob)
    if reason:
        raise ValueError(f"safety floor refused the bible: {reason}")
    return bible


def _bible_brief(bible: dict, canon_tail: int = 40) -> str:
    """Compact bible JSON for prompt context (canon capped to the recent tail)."""
    b = dict(bible)
    if isinstance(b.get("canon"), list) and len(b["canon"]) > canon_tail:
        b["canon"] = b["canon"][-canon_tail:]
    return json.dumps(b, ensure_ascii=False)


# ═══ §3.1 Showrunner — owner prompt → the bible ══════════════════════════════

def submit_showrunner(show_id: int) -> int:
    """Queue the showrunner LLM turn on the unified queue: owner prompt → the
    show bible. Status flips to 'bible' here; 'ready' (bible stored, title
    back-filled) or 'failed' in the worker. Returns the orchestrator task id."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM tv_shows WHERE id=?", (show_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"tv show {show_id} not found")
    show = dict(row)
    conn.execute("UPDATE tv_shows SET status='bible',error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (show_id,))
    conn.commit()
    conn.close()

    user_msg = (
        f"The owner wants to watch: {show['prompt']}\n"
        f"Episode format: ~{show.get('format_seconds') or 150} seconds each\n"
        f"Visual style preference: {show.get('style') or 'your choice — animation preferred'}")

    def _work():
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        raw = ""
        try:
            raw = _call_lmstudio(get_prompt("tv_showrunner"), user_msg, max_tokens=4000)
            bible = _validate_bible(_extract_json(raw), show.get("style") or "")
            c = get_conn()
            c.execute(
                "UPDATE tv_shows SET bible=?,title=COALESCE(NULLIF(title,''),?),"
                "style=COALESCE(NULLIF(style,''),?),status='ready',error=NULL,"
                "updated_at=datetime('now') WHERE id=?",
                (json.dumps(bible), bible["title"] or (show["prompt"] or "")[:60],
                 bible["style_guide"], show_id))
            c.commit()
            c.close()
            # Every new show gets box art + banner + blurb automatically. A
            # plain thread (NOT this LLM worker): art_task submits its own LLM
            # turn and then takes image_acquire — doing that inline here would
            # block the queue worker on the very queue it serves.
            import threading
            _upd_show(show_id, art_status="queued")
            threading.Thread(target=art_task, args=(show_id,), daemon=True).start()
            return {"show_id": show_id, "characters": len(bible["characters"])}
        except Exception as ex:
            # Fail LOUDLY, no auto-retry: the owner gets the reason + raw tail.
            err = f"{ex}"
            if raw and "model output" not in err and "did not parse" not in err:
                err += f" · raw tail: …{raw[-200:]}"
            c = get_conn()
            c.execute("UPDATE tv_shows SET status='failed',error=?,"
                      "updated_at=datetime('now') WHERE id=?", (err[:800], show_id))
            c.commit()
            c.close()
            logger.error("TV showrunner %d failed: %s\nRAW MODEL OUTPUT:\n%s",
                         show_id, ex, raw)
            raise

    return orch.submit_llm(_work, desc=f"TV showrunner: {(show['prompt'] or '')[:40]}",
                           priority=0, task="tv_showrunner", source="tv")


# ═══ §3.2 Episode writer — recap + bible → synopsis/beats → studio project ═══

def next_slot(conn, show_id: int, bible: dict) -> tuple[int, int]:
    """Auto-assign (season, number): the next number in season 1 unless
    bible.season_arc carries an episodes_per_season hint that rolls the season."""
    last = conn.execute(
        "SELECT season, number FROM tv_episodes WHERE show_id=? "
        "ORDER BY season DESC, number DESC LIMIT 1", (show_id,)).fetchone()
    if not last:
        return 1, 1
    per = None
    arc = bible.get("season_arc")
    if isinstance(arc, dict):
        try:
            per = int(arc.get("episodes_per_season") or 0) or None
        except (TypeError, ValueError):
            per = None
    if per and (last["number"] or 0) >= per:
        return (last["season"] or 1) + 1, 1
    return last["season"] or 1, (last["number"] or 0) + 1


def _storyboard_notes(bible: dict, beats: list) -> str:
    """The consistency injection (§4 style lock): the storyboard turn gets the
    style_guide as a mandatory prompt prefix + each character's FIXED visual
    descriptor, so every shot prompt of every episode inherits them."""
    lines = [f"STYLE GUIDE — prefix EVERY shot's video_prompt with this exact "
             f"style thread: {bible.get('style_guide') or ''}",
             "CHARACTERS — whenever one appears in a shot, describe them with "
             "their fixed visual descriptor VERBATIM:"]
    for c in bible.get("characters") or []:
        lines.append(f"- {c.get('name')}: {c.get('visual_desc')}"
                     + (f" (voice: {c['voice']})" if c.get("voice") else ""))
    lines.append("EPISODE BEATS — one scene per beat, in order:")
    lines += [f"{i + 1}. {b}" for i, b in enumerate(beats)]
    return "\n".join(lines)


def submit_episode_writer(episode_id: int, notes: str = "") -> int:
    """Queue the episode-writer LLM turn: recap from prior episodes' memory +
    the bible → title/synopsis/beats → a studio_projects row (kind long, the
    show's render settings) handed to submit_storyboard with the style/
    character notes injected. Episode status flips to 'writing' here; 'ready'
    happens lazily in reconcile_episode once the storyboard lands ('draft')."""
    conn = get_conn()
    ep = conn.execute("SELECT * FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
    show = conn.execute("SELECT * FROM tv_shows WHERE id=?",
                        (ep["show_id"],)).fetchone() if ep else None
    if not ep or not show:
        conn.close()
        raise ValueError(f"tv episode {episode_id} not found")
    ep, show = dict(ep), dict(show)
    bible = bible_of(show)
    if not bible:
        conn.close()
        raise ValueError("show has no bible yet")
    # "previously on": the last N episodes' memory blocks (synopsis fallback)
    priors = conn.execute(
        "SELECT season,number,title,memory,synopsis FROM tv_episodes WHERE show_id=? "
        "AND id!=? AND (memory IS NOT NULL OR synopsis IS NOT NULL) "
        "ORDER BY season DESC, number DESC LIMIT ?",
        (show["id"], episode_id, _RECAP_EPISODES)).fetchall()
    recap = "\n".join(
        f"S{p['season']}E{p['number']} {p['title'] or ''}: "
        f"{(p['memory'] or p['synopsis'] or '').strip()}"
        for p in reversed(priors)) or "This is the very first episode — no prior events."
    conn.execute("UPDATE tv_episodes SET status='writing',recap=?,error=NULL,"
                 "updated_at=datetime('now') WHERE id=?", (recap, episode_id))
    conn.commit()
    conn.close()

    user_msg = (
        f"SHOW BIBLE (JSON): {_bible_brief(bible)}\n"
        f"PREVIOUSLY ON (memory of recent episodes):\n{recap}\n"
        f"Write episode S{ep['season']}E{ep['number']}.\n"
        f"Target length: ~{show.get('format_seconds') or 150} seconds.")
    if (notes or "").strip():
        user_msg += f"\nOwner notes for this episode: {notes.strip()}"

    def _work():
        import nsfw
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        raw = ""
        try:
            raw = _call_lmstudio(get_prompt("tv_episode_writer"), user_msg, max_tokens=4000)
            data = _extract_json(raw)
            title = _as_text(data.get("title"))[:120] or f"Episode {ep['number']}"
            synopsis = _as_text(data.get("synopsis"))[:4000]
            beats = [_as_text(b)[:400] for b in (data.get("beats") or [])
                     if _as_text(b)][:_MAX_BEATS]
            if not synopsis or not beats:
                raise ValueError("episode writer returned no synopsis/beats")
            reason = nsfw.safety_check(synopsis, *beats)
            if reason:
                raise ValueError(f"safety floor refused the episode: {reason}")
            idea = (f"TV episode S{ep['season']}E{ep['number']} \"{title}\" of the show "
                    f"\"{show.get('title') or bible.get('title') or ''}\". {synopsis}")
            c = get_conn()
            cur = c.execute(
                "INSERT INTO studio_projects (idea,kind,style,target_seconds,model_id,"
                "width,height,fps,steps,nsfw,status,title) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'new',?)",
                (idea[:2000], "long", bible.get("style_guide") or show.get("style") or "",
                 show.get("format_seconds") or 150, show["model_id"], show["width"],
                 show["height"], show["fps"], show["steps"], show.get("nsfw") or 0,
                 f"📺 S{ep['season']}E{ep['number']} {title}"[:80]))
            project_id = cur.lastrowid
            c.execute("UPDATE tv_episodes SET title=?,synopsis=?,project_id=?,"
                      "updated_at=datetime('now') WHERE id=?",
                      (title, synopsis, project_id, episode_id))
            c.commit()
            c.close()
            # the EXISTING storyboarder does the scenes/shots/cues, style-locked
            _studio.submit_storyboard(project_id, notes=_storyboard_notes(bible, beats))
            return {"episode_id": episode_id, "project_id": project_id,
                    "beats": len(beats)}
        except Exception as ex:
            err = f"{ex}"
            if raw and "model output" not in err and "did not parse" not in err:
                err += f" · raw tail: …{raw[-200:]}"
            c = get_conn()
            c.execute("UPDATE tv_episodes SET status='failed',error=?,"
                      "updated_at=datetime('now') WHERE id=?", (err[:800], episode_id))
            c.commit()
            c.close()
            logger.error("TV episode writer %d failed: %s\nRAW MODEL OUTPUT:\n%s",
                         episode_id, ex, raw)
            raise

    return orch.submit_llm(
        _work, desc=f"TV episode S{ep['season']}E{ep['number']}: "
                    f"{(show.get('title') or show.get('prompt') or '')[:30]}",
        priority=0, task="tv_episode_writer", source="tv")


# ═══ Produce + lazy reconciliation ═══════════════════════════════════════════

def episode_busy(ep: dict) -> bool:
    """True while the episode is genuinely producing (same stale-status idea as
    routers/studio._require_idle: a restart kills the background thread, and a
    90 s grace covers a just-queued task that hasn't registered yet)."""
    if ep.get("status") != "producing":
        return False
    if ep.get("project_id") and _studio.project_active(ep["project_id"]):
        return True
    conn = get_conn()
    row = conn.execute(
        "SELECT (strftime('%s','now') - strftime('%s', COALESCE(updated_at, "
        "created_at))) AS age FROM tv_episodes WHERE id=?", (ep["id"],)).fetchone()
    conn.close()
    return bool(row and row["age"] is not None and row["age"] < 90)


def _finish_episode(episode_id: int) -> str | None:
    """Project done → copy the final onto the episode (its OWN file under
    VIDEOS_DIR, so a later re-produce of the project can't yank an aired
    episode), mark done, and queue the continuity editor."""
    from config import VIDEOS_DIR
    conn = get_conn()
    ep = conn.execute("SELECT * FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
    proj = conn.execute("SELECT final_path FROM studio_projects WHERE id=?",
                        (ep["project_id"],)).fetchone() if ep and ep["project_id"] else None
    conn.close()
    if not ep or not proj or not proj["final_path"] or not Path(proj["final_path"]).exists():
        return None
    ep = dict(ep)
    dest = Path(VIDEOS_DIR) / (f"tv_show{ep['show_id']}_s{ep['season']:02d}"
                               f"e{ep['number']:02d}_{episode_id}.mp4")
    try:
        shutil.copy2(proj["final_path"], dest)
        final = str(dest)
    except OSError:
        final = proj["final_path"]     # serve the project's own final instead
    _upd_episode(episode_id, status="done", final_path=final, error=None)
    logger.info("TV episode %d done → %s", episode_id, final)
    submit_continuity(episode_id)
    return final


def produce_episode(episode_id: int):
    """P1 manual produce (blocking; run as a FastAPI background task): the
    whole UNMODIFIED studio pipeline for the episode's project, then finish or
    fail the episode loudly. services_studio's _working registry keeps
    project_active() truthful for the 409 busy checks meanwhile."""
    conn = get_conn()
    ep = conn.execute("SELECT * FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
    conn.close()
    if not ep or not ep["project_id"]:
        return
    pid = ep["project_id"]
    try:
        _studio.produce_project(pid)
        conn = get_conn()
        proj = conn.execute("SELECT status,error,final_path FROM studio_projects "
                            "WHERE id=?", (pid,)).fetchone()
        conn.close()
        if proj and proj["status"] == "done" and proj["final_path"] \
                and Path(proj["final_path"]).exists() and _finish_episode(episode_id):
            return
        _upd_episode(episode_id, status="failed",
                     error=((proj["error"] if proj else None)
                            or "produce did not finish")[:500])
    except Exception as ex:
        # Belt & braces: nothing may strand the episode in 'producing'.
        logger.exception("TV episode %d produce crashed", episode_id)
        _upd_episode(episode_id, status="failed", error=str(ex)[:400])


def reconcile_episode(ep: dict) -> dict:
    """Lazy status reconciliation (called from the GET handlers — no polling
    threads): 'writing' becomes 'ready' when the linked project's storyboard
    landed (project status 'draft'), or 'failed' when the storyboard failed;
    a 'producing' episode orphaned by a restart adopts its project's terminal
    state (mirrors services_studio._adopt_finished_chain). Mutates + returns ep."""
    pid = ep.get("project_id")
    if not pid or ep.get("status") not in ("writing", "producing"):
        return ep
    conn = get_conn()
    proj = conn.execute("SELECT status,error,final_path FROM studio_projects WHERE id=?",
                        (pid,)).fetchone()
    conn.close()
    if not proj:
        return ep
    if ep["status"] == "writing":
        if proj["status"] == "failed":
            err = (proj["error"] or "storyboard failed")[:500]
            _upd_episode(ep["id"], status="failed", error=err)
            ep.update(status="failed", error=err)
        elif proj["status"] not in ("new", "storyboarding"):
            _upd_episode(ep["id"], status="ready", error=None)
            ep.update(status="ready", error=None)
    elif not _studio.project_active(pid):          # producing, but no live thread
        if proj["status"] == "done" and proj["final_path"] \
                and Path(proj["final_path"]).exists():
            final = _finish_episode(ep["id"])
            if final:
                ep.update(status="done", final_path=final, error=None)
        elif proj["status"] == "failed":
            err = (proj["error"] or "produce failed")[:500]
            _upd_episode(ep["id"], status="failed", error=err)
            ep.update(status="failed", error=err)
    return ep


# ═══ §3.3 Continuity editor — episode.memory + bible.canon ═══════════════════

def submit_continuity(episode_id: int) -> int | None:
    """Queue the continuity-editor LLM turn: reads the bible + what was
    actually storyboarded/rendered → episode.memory + durable new bible.canon
    facts. A failure here never un-does a finished episode — it only logs (the
    next writer falls back to the synopsis as this episode's memory)."""
    conn = get_conn()
    ep = conn.execute("SELECT * FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
    show = conn.execute("SELECT * FROM tv_shows WHERE id=?",
                        (ep["show_id"],)).fetchone() if ep else None
    scenes = conn.execute(
        "SELECT idx,title,summary,status FROM studio_scenes WHERE project_id=? "
        "ORDER BY idx", (ep["project_id"],)).fetchall() if ep and ep["project_id"] else []
    conn.close()
    if not ep or not show:
        return None
    ep, show = dict(ep), dict(show)
    bible = bible_of(show)
    as_rendered = "\n".join(
        f"Scene {s['idx'] + 1} [{s['status']}] {s['title'] or ''}: {s['summary'] or ''}"
        for s in scenes)
    user_msg = (
        f"SHOW BIBLE (JSON): {_bible_brief(bible)}\n"
        f"EPISODE S{ep['season']}E{ep['number']} \"{ep['title'] or ''}\" synopsis: "
        f"{ep['synopsis'] or ''}\n"
        f"AS RENDERED (scene list with statuses — failed scenes did NOT air):\n"
        f"{as_rendered}")

    def _work():
        import nsfw
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        raw = ""
        try:
            raw = _call_lmstudio(get_prompt("tv_continuity"), user_msg, max_tokens=1500)
            data = _extract_json(raw)
            memory = _as_text(data.get("memory"))[:2000]
            new_facts = [_as_text(f)[:300] for f in (data.get("canon") or [])
                         if _as_text(f)][:_MAX_NEW_CANON]
            reason = nsfw.safety_check(memory, *new_facts)
            if reason:
                raise ValueError(f"safety floor refused the continuity notes: {reason}")
            if memory:
                _upd_episode(episode_id, memory=memory)
            if new_facts:
                # merge durable facts into bible.canon (deduped, capped) — the
                # bible is RE-READ here so an owner edit since produce survives
                c = get_conn()
                row = c.execute("SELECT bible FROM tv_shows WHERE id=?",
                                (show["id"],)).fetchone()
                try:
                    b = json.loads(row["bible"] or "") if row else {}
                except Exception:
                    b = {}
                if isinstance(b, dict) and b:
                    canon = [x for x in (b.get("canon") or []) if isinstance(x, str)]
                    canon += [f for f in new_facts if f not in canon]
                    b["canon"] = canon[-_CANON_CAP:]
                    c.execute("UPDATE tv_shows SET bible=?,updated_at=datetime('now') "
                              "WHERE id=?", (json.dumps(b), show["id"]))
                    c.commit()
                c.close()
            return {"episode_id": episode_id, "canon_added": len(new_facts)}
        except Exception as ex:
            # the episode is already done — continuity failure only logs
            logger.error("TV continuity %d failed: %s\nRAW MODEL OUTPUT:\n%s",
                         episode_id, ex, raw)
            raise

    return orch.submit_llm(_work, desc=f"TV continuity: S{ep['season']}E{ep['number']}",
                           priority=1, task="tv_continuity", source="tv")


# ═══ Show art — box art, banner & blurb (art-director LLM + the image node) ══

_ART_ACTIVE: set[int] = set()   # show ids with a live art thread in THIS process


def _upd_show(show_id: int, **fields):
    """Set columns on a show row (always touches updated_at)."""
    conn = get_conn()
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE tv_shows SET {sets},updated_at=datetime('now') WHERE id=?",
                 (*fields.values(), show_id))
    conn.commit()
    conn.close()


def _art_dir() -> Path:
    """designs/tv — rides the existing /designs static mount (immutable-cached,
    so every render gets a fresh timestamped filename)."""
    from config import DESIGNS_PENDING
    d = DESIGNS_PENDING.parent / "tv"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _wait_llm(tid: int, timeout_s: int) -> dict | None:
    """Poll an orch LLM task to completion. PATIENT on purpose: overnight
    episode renders hold the GPU across whole scene chains (video_exclusive),
    so queued LLM work legitimately waits hours before its turn."""
    import time as _t
    end = _t.time() + timeout_s
    while _t.time() < end:
        st = orch.poll(tid)
        if st.get("status") == "done":
            return st.get("result") if isinstance(st.get("result"), dict) else None
        if st.get("status") in ("failed", "not_found"):
            return None
        _t.sleep(5)
    return None


def _art_director(show: dict, bible: dict) -> tuple[str, str, str]:
    """One LLM pass → (description, boxart_prompt, banner_prompt). Falls back
    to bible-derived prompts when the model flakes — art never hard-fails on
    the LLM leg."""
    title = show.get("title") or (show.get("prompt") or "")[:60]
    user_msg = (f"Show title: {title}\n{_bible_brief(bible, canon_tail=6)}\n"
                f"Owner's original request: {show.get('prompt') or ''}")

    def _work():
        from llm_client import _call_lmstudio
        from prompts import get_prompt
        raw = _call_lmstudio(get_prompt("tv_art_director"), user_msg, max_tokens=1200)
        return _extract_json(raw)

    tid = orch.submit_llm(_work, desc=f"TV art director: {title[:40]}",
                          priority=0, task="tv_art_director", source="tv")
    data = _wait_llm(tid, timeout_s=6 * 3600) or {}
    style = _as_text(bible.get("style_guide")) or _as_text(show.get("style")) \
        or "2D adult animation, bold outlines, flat colors"
    premise = _as_text(bible.get("premise")) or _as_text(show.get("prompt"))
    desc = _as_text(data.get("description")).strip()[:600]
    box = _as_text(data.get("boxart_prompt")).strip()[:500]
    ban = _as_text(data.get("banner_prompt")).strip()[:500]
    if not box:
        box = (f"TV series poster key art, {premise[:180]}, {style}, "
               "main characters grouped, dramatic lighting, clean composition, no text, no words")
    if not ban:
        ban = (f"ultrawide cinematic TV series banner, establishing shot, {premise[:180]}, "
               f"{style}, atmospheric, room for overlay text on the left, no text, no words")
    if not desc:
        desc = premise[:400]
    return desc, box, ban


def _tv_image(prompt: str, out_path: Path, w: int, h: int,
              errors: list, label: str) -> str:
    """One frame via the store's node-side generate script — the exact call
    shape of services.run_generation, minus the designs-gallery row (show art
    is not a product design). Caller order: image_acquire serializes on the
    unified queue, so this politely waits out an episode render."""
    import random
    import subprocess
    from config import GENERATE_SCRIPT
    from deps import DEFAULT_IMAGE_MODEL
    try:
        orch.image_acquire()
    except Exception as ex:
        errors.append(f"{label}: GPU acquire failed: {str(ex)[:160]}")
        return ""
    try:
        seed = str(random.randint(1, 2**31 - 1))
        r = subprocess.run(
            [str(GENERATE_SCRIPT), prompt, str(out_path), str(w), str(h),
             "20", seed, DEFAULT_IMAGE_MODEL, "", "", ""],
            capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and Path(out_path).exists():
            return str(out_path)
        errors.append(f"{label}: {((r.stderr or r.stdout or 'no output').strip())[-200:]}")
        return ""
    except Exception as ex:
        errors.append(f"{label}: {str(ex)[:200]}")
        return ""
    finally:
        orch.image_release()


def art_task(show_id: int):
    """Background task: description + box art (2:3 poster) + banner (wide hero)
    for a show. Regen-safe (timestamped filenames beat the immutable cache;
    old files are removed on success). Both images ride image_acquire; the
    blurb/prompt pass rides the LLM queue — everything defers to an active
    episode render and drains between scene chains."""
    if show_id in _ART_ACTIVE:
        return
    _ART_ACTIVE.add(show_id)
    try:
        conn = get_conn()
        row = conn.execute("SELECT * FROM tv_shows WHERE id=?", (show_id,)).fetchone()
        conn.close()
        if not row:
            return
        show = dict(row)
        bible = bible_of(show)
        _upd_show(show_id, art_status="generating", art_error=None)
        desc, box_p, ban_p = _art_director(show, bible)
        if desc:
            _upd_show(show_id, description=desc)
        errors: list[str] = []
        ts = int(__import__("time").time())
        box = _tv_image(box_p, _art_dir() / f"show{show_id}_boxart_{ts}.png",
                        832, 1216, errors, "boxart")
        ban = _tv_image(ban_p, _art_dir() / f"show{show_id}_banner_{ts}.png",
                        1344, 768, errors, "banner")
        fields: dict = {}
        if box:
            fields["boxart_path"] = box
        if ban:
            fields["banner_path"] = ban
        if box or ban:
            fields["art_status"] = "done"
            fields["art_error"] = ("partial: " + "; ".join(errors))[:400] if errors else None
            # sweep this show's superseded art files (best-effort)
            for old in (show.get("boxart_path"), show.get("banner_path")):
                try:
                    if old and Path(old).exists() and old not in (box, ban):
                        Path(old).unlink()
                except OSError:
                    pass
        else:
            fields["art_status"] = "failed"
            fields["art_error"] = ("; ".join(errors) or "image generation failed")[:400]
        _upd_show(show_id, **fields)
        logger.info("TV show %d art: %s (box=%s banner=%s)",
                    show_id, fields["art_status"], bool(box), bool(ban))
    except Exception as ex:
        logger.exception("TV show %d art crashed", show_id)
        _upd_show(show_id, art_status="failed", art_error=str(ex)[:400])
    finally:
        _ART_ACTIVE.discard(show_id)


def art_busy(show: dict) -> bool:
    """True while show art is genuinely in flight (same stale-status pattern as
    episode_busy: a restart kills the thread; 90 s grace for a just-queued one)."""
    if show.get("art_status") not in ("queued", "generating"):
        return False
    if show["id"] in _ART_ACTIVE:
        return True
    conn = get_conn()
    row = conn.execute(
        "SELECT (strftime('%s','now') - strftime('%s', COALESCE(updated_at, "
        "created_at))) AS age FROM tv_shows WHERE id=?", (show["id"],)).fetchone()
    conn.close()
    return bool(row and row["age"] is not None and row["age"] < 90)


def reconcile_show_art(show: dict) -> dict:
    """Lazy art-status reconciliation for the GET handlers: a queued/generating
    art_status with no live thread (restart orphan) becomes failed. Mutates +
    returns show."""
    if show.get("art_status") in ("queued", "generating") and not art_busy(show):
        show["art_status"] = "failed"
        show["art_error"] = "Interrupted (server restarted) — hit Generate art again"
        _upd_show(show["id"], art_status="failed", art_error=show["art_error"])
    return show

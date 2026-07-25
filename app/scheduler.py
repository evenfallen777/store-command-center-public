"""Background security monitor scheduler — periodically snapshots Pi-hole activity
into device profiles, and (optionally) runs the config scan and AI threat hunt.

All controlled by DB settings (Network Security → Auto), so it's togglable at runtime:
  security_monitor_enabled      "1"/"0"   master switch
  security_monitor_interval     minutes   how often to refresh device profiles
  security_autoscan_enabled     "1"/"0"   also run the Pi-hole config scan
  security_scan_interval        minutes
  security_autoanalyze_enabled  "1"/"0"   also run the AI threat hunt (uses the GPU)
  security_analyze_interval     minutes
"""
import threading
import time
import logging

import pihole
from db import get_conn

log = logging.getLogger("secsched")


def _setting(key, default):
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        if row and row["value"] not in (None, ""):
            return row["value"]
    except Exception:
        pass
    return default


def _minutes(key, default_min, floor_min=1):
    try:
        return max(floor_min, int(_setting(key, str(default_min)))) * 60
    except Exception:
        return default_min * 60


class SecurityScheduler:
    JOBS = ("tick", "scan", "analyze", "audit", "backup", "guardian", "aiwatch", "agentwatch",
            "worldsnap")

    def __init__(self):
        self.last = {j: 0.0 for j in self.JOBS}
        self.started_at = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sec-scheduler")

    def _seed_from_db(self):
        """Load persisted last-run times so a restart doesn't immediately re-fire
        every job (restarts used to re-run the full audit each time)."""
        try:
            import defense
            for j in self.JOBS:
                self.last[j] = max(self.last[j], defense.persisted_last(j))
        except Exception as e:
            log.warning("seeding last-run times failed: %s", e)

    def _ran(self, job, note=""):
        self.last[job] = time.time()
        try:
            import defense
            defense.record_run(job, note)
        except Exception:
            pass

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()

    def _loop(self):
        # small initial delay so the app finishes starting
        time.sleep(20)
        self._seed_from_db()
        while True:
            try:
                self._check()
            except Exception as e:
                log.warning("scheduler tick failed: %s", e)
            time.sleep(30)

    def _check(self):
        now = time.time()

        # ── Dev-swarm cron: keep working cron-enabled WIP jobs on a schedule ──
        # Runs independently of the security monitor. The per-job toggle is the switch;
        # only advances non-gated, non-terminal jobs that aren't already running.
        # Company control plane gate: the master switch cascades into this setting.
        try:
          if _setting("swarm_cron_enabled", "1") == "1":
            import swarm
            conn = get_conn()
            rows = conn.execute(
                "SELECT id, cron_interval FROM swarm_jobs "
                "WHERE cron_enabled=1 AND status IN ('proposed','coding','reviewing','testing','paused')"
            ).fetchall()
            conn.close()
            for r in rows:
                key = f"swarm_{r['id']}"
                iv = max(1, int(r["cron_interval"] or 30)) * 60
                if now - self.last.get(key, 0) >= iv and not swarm.is_running(r["id"]):
                    self.last[key] = now
                    if swarm.start_job(r["id"]):
                        log.info("swarm cron: advancing job %s", r["id"])
        except Exception as e:
            log.warning("swarm cron failed: %s", e)

        # ── GPU idle-TTL sweep: unload LLMs that have sat idle past model_idle_ttl ──
        # LM Studio auto-unloads models the store loaded (lms load --ttl), but a model
        # loaded outside the store (dev-swarm/OpenClaw, bare JIT) can hold VRAM forever.
        # This is the safety net; it no-ops unless the GPU is fully idle. Owner disables
        # it by setting model_idle_ttl=0.
        try:
            from orchestrator import orch
            r = orch.sweep_idle_llms()
            if r.get("swept"):
                log.info("gpu idle-sweep: unloaded %s", r["swept"])
        except Exception as e:
            log.warning("gpu idle-sweep failed: %s", e)

        # ── Research Lab: recurring rechecks — Geniuses re-verify material prices
        # on projects given a cadence (recur_days). Master toggle + per-project
        # cadence; recur_tick() bumps next_run_at up front so failures can't loop.
        if _setting("research_recur_enabled", "on") != "off" and \
                now - self.last.get("research", 0) >= _minutes("research_recur_interval", 30, floor_min=5):
            self.last["research"] = now
            try:
                import research_lab_market
                r = research_lab_market.recur_tick()
                if r.get("started"):
                    log.info("research recheck: started projects %s", r["started"])
            except Exception as e:
                log.warning("research recheck failed: %s", e)

        # ── Proposal review gate: LLM judge scores pending Etsy/Printify proposals
        # and weeds/queues them per proposal_gate_mode. Opt-in (default off); the
        # submit is non-blocking — the judging runs as ONE background LLM task.
        if _setting("proposal_gate_enabled", "0") == "1" and \
                now - self.last.get("proposalgate", 0) >= _minutes("proposal_gate_interval_min", 360, floor_min=15):
            self.last["proposalgate"] = now
            try:
                import proposal_gate
                r = proposal_gate.gate_tick()
                if not r.get("skipped"):
                    log.info("proposal gate: %s", r)
            except Exception as e:
                log.warning("proposal gate tick failed: %s", e)

        # ── Mail auto-reply gate: triage new inbox mail, draft profile-driven
        # replies, and (full_auto only, hard-guardrailed) send routine ones.
        # Opt-in (default off + default mode 'manual'); submit is non-blocking —
        # the batch runs as ONE background LLM task (see mail_gate.py).
        if _setting("mail_gate_enabled", "0") == "1" and \
                now - self.last.get("mailgate", 0) >= _minutes("mail_gate_interval_min", 15, floor_min=5):
            self.last["mailgate"] = now
            try:
                import mail_gate
                r = mail_gate.gate_tick()
                if not r.get("skipped"):
                    log.info("mail gate: %s", r)
            except Exception as e:
                log.warning("mail gate tick failed: %s", e)

        # ── Proposal desk: intake side of the funnel — harvests the crew's
        # "Suggested:" ideas into proposals, keeps the Etsy demand snapshot warm,
        # and kicks the multi-lane feed scan. Opt-in (default off); a Company
        # control-plane system, so the master pause stops it like everything else.
        if _setting("proposal_desk_enabled", "0") == "1" and \
                now - self.last.get("proposaldesk", 0) >= _minutes("proposal_desk_interval_min", 240, floor_min=15):
            self.last["proposaldesk"] = now
            try:
                import proposal_desk
                r = proposal_desk.desk_tick()
                if not r.get("skipped"):
                    log.info("proposal desk: %s", r)
            except Exception as e:
                log.warning("proposal desk tick failed: %s", e)

        # ── Company security audit: periodic snapshot + regression alerts ──
        # Independent of the Pi-hole monitor; gated by the control plane.
        if _setting("security_audit_enabled", "0") == "1" and \
                now - self.last.get("audit", 0) >= _minutes("security_audit_interval", 1440, floor_min=30):
            self._ran("audit")
            try:
                import secaudit
                r = secaudit.run_scheduled_audit()
                log.info("security audit: %s", r)
                self._ran("audit", f"grade {r.get('grade')} ({r.get('score')}), {r.get('alerts')} alert(s)")
            except Exception as e:
                log.warning("security audit failed: %s", e)
                self._ran("audit", f"failed: {e}")

        # ── Nightly DB backup (local + off-box drive) ──
        if _setting("backup_enabled", "1") == "1" and \
                now - self.last.get("backup", 0) >= _minutes("backup_interval_min", 1440, floor_min=60):
            self._ran("backup")
            try:
                import backups
                r = backups.run_scheduled_backup()
                log.info("scheduled DB backup: %d copies%s", len(r["copies"]),
                         (" ERRORS: " + "; ".join(r["errors"])) if r["errors"] else "")
                self._ran("backup", f"{len(r['copies'])} copies"
                          + (f", errors: {'; '.join(r['errors'])}" if r["errors"] else ""))
            except Exception as e:
                log.warning("scheduled backup failed: %s", e)
                self._ran("backup", f"failed: {e}")

        # ── Network Guardian: auto-block clear trackers (opt-in) ──
        if _setting("netguard_auto_enabled", "0") == "1" and \
                now - self.last.get("guardian", 0) >= _minutes("netguard_interval", 360, floor_min=30):
            self._ran("guardian")
            try:
                import netguard
                r = netguard.guardian_tick()
                log.info("network guardian: %s", r)
                self._ran("guardian", str(r)[:160])
            except Exception as e:
                log.warning("network guardian failed: %s", e)
                self._ran("guardian", f"failed: {e}")

        # ── Agent Watcher: diagnose failed/paused/stalled swarm + media jobs so
        # agents (and the human) know what went wrong and how to fix it. On by
        # default; every behaviour has its own toggle (see watcher.py).
        if _setting("agent_watcher_enabled", "1") == "1" and \
                now - self.last.get("agentwatch", 0) >= _minutes("agent_watcher_interval", 5, floor_min=2):
            self._ran("agentwatch")
            try:
                import watcher
                r = watcher.watch_tick()
                self._ran("agentwatch", f"{r.get('new', 0)} new, {r.get('open', 0)} open")
            except Exception as e:
                log.warning("agent watcher failed: %s", e)
                self._ran("agentwatch", f"failed: {e}")

        # ── Public world snapshot: render The Company and push the picture out to
        # the public site. Outbound only, opt-in, defaults OFF. The module owns its
        # own interval + gated-content + leak checks; we just poke it.
        if _setting("world_public_snapshot", "") in ("1", "true", "on", "yes"):
            try:
                import world_snapshot
                r = world_snapshot.tick()
                if not r.get("skipped"):
                    self._ran("worldsnap", str(r)[:160])
                    log.info("world snapshot: %s", r)
            except Exception as e:
                log.warning("world snapshot tick failed: %s", e)
                self._ran("worldsnap", f"failed: {e}")

        # ── AI Shield: watch agents for rogue behaviour (opt-in) ──
        if _setting("ai_watch_enabled", "0") == "1" and \
                now - self.last.get("aiwatch", 0) >= _minutes("ai_watch_interval", 60, floor_min=15):
            self._ran("aiwatch")
            try:
                import aishield
                r = aishield.anomaly_tick()
                log.info("ai shield watch: %s", r)
                self._ran("aiwatch", str(r)[:160])
            except Exception as e:
                log.warning("ai shield watch failed: %s", e)
                self._ran("aiwatch", f"failed: {e}")

        if _setting("security_monitor_enabled", "0") != "1":
            return
        if not pihole.configured():
            return
        # Lazy import to avoid an import cycle (routers import deps/services/pihole).
        from routers import security

        if now - self.last["tick"] >= _minutes("security_monitor_interval", 15):
            self._ran("tick")
            try:
                r = security.monitor_tick()
                log.info("auto monitor: %s", r)
                self._ran("tick", f"{r.get('clients')} devices from {r.get('queries_scanned')} queries")
            except Exception as e:
                log.warning("auto monitor failed: %s", e)
                self._ran("tick", f"failed: {e}")

        if _setting("security_autoscan_enabled", "0") == "1" and \
                now - self.last["scan"] >= _minutes("security_scan_interval", 360, floor_min=5):
            self._ran("scan")
            try:
                security.trigger_security_scan()
                log.info("auto config scan ran")
                self._ran("scan", "config scan ran")
            except Exception as e:
                log.warning("auto scan failed: %s", e)
                self._ran("scan", f"failed: {e}")

        if _setting("security_autoanalyze_enabled", "0") == "1" and \
                now - self.last["analyze"] >= _minutes("security_analyze_interval", 120, floor_min=15):
            self._ran("analyze")
            try:
                security.analyze_logs()  # queues an LLM job via the orchestrator
                log.info("auto AI analyze queued")
                self._ran("analyze", "AI hunt queued")
            except Exception as e:
                log.warning("auto analyze failed: %s", e)
                self._ran("analyze", f"failed: {e}")


scheduler = SecurityScheduler()


def start():
    scheduler.start()

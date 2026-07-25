"""
AI Shield — defenses for an AI-heavy stack.

Four fronts:
  1. ai_surface()      — audit the AI attack surface: exposed model/tool endpoints
                         (LM Studio, ComfyUI, MCP) + verify the Company's agent
                         gates (spending/code/payout can't auto-fire).
  2. scan_injection()  — detect prompt-injection / jailbreak / exfil attempts in
                         any text an agent is about to ingest (web research, files,
                         the Bible), so a poisoned page can't hijack your agents.
  3. bots()            — bot governance: ALLOW the reputable AI crawlers (they
                         index example.com and recommend your products), BLOCK
                         the bad scrapers.
  4. agent_anomalies() — baseline what agents normally do; alert if one goes
                         rogue (a burst of payout/code/publish, an unknown actor).

All read-only + advisory unless you act; findings flow to the security score and
the God Console.
"""
import json, logging, os, re, subprocess, time
from collections import defaultdict
import httpx
from deps import get_conn, get_setting

logger = logging.getLogger("store")


def _chk(title, status, detail="", fix=""):
    return {"title": title, "status": status, "detail": detail, "fix": fix}


# ═══ 1. AI ATTACK-SURFACE AUDIT ═══════════════════════════════════════════════
def _probe(url, headers=None, timeout=4):
    try:
        r = httpx.get(url, headers=headers or {}, timeout=timeout)
        return r.status_code
    except Exception:
        return None


def ai_surface():
    checks = []
    gpu = get_setting("gpu_host", "127.0.0.1") or "127.0.0.1"

    # LM Studio — should require a token
    c = _probe(f"http://{gpu}:1234/v1/models")
    if c in (401, 403):
        checks.append(_chk("LM Studio API", "pass", "Token-protected (401) — good."))
    elif c == 200:
        checks.append(_chk("LM Studio API", "fail", "OPEN with no auth — anyone who reaches it can run your models / read prompts.",
                           "Enable the LM Studio API key + bind to LAN only."))
    else:
        checks.append(_chk("LM Studio API", "info", f"Not reachable from here (status {c})."))

    # ComfyUI — usually no auth; flag if reachable
    c = _probe(f"http://{gpu}:8188/system_stats")
    if c == 200:
        checks.append(_chk("ComfyUI", "warn", "Reachable with NO authentication — any LAN host can queue jobs / pull outputs.",
                           "Put it behind the reverse proxy w/ auth, or restrict :8188 to localhost/VPN."))
    elif c in (401, 403):
        checks.append(_chk("ComfyUI", "pass", "Auth required."))
    else:
        checks.append(_chk("ComfyUI", "info", f"Not reachable from here (status {c})."))

    # MCP / store: the localhost auth-bypass must NOT be spoofable via headers
    try:
        r = httpx.get("http://127.0.0.1:8787/api/world/ops/summary",
                      headers={"X-Forwarded-For": "203.0.113.9"}, timeout=4)
        ext_ok = r.status_code
    except Exception:
        ext_ok = None
    spoof = _probe("http://127.0.0.1:8787/api/world/ops/summary",
                   headers={"X-Forwarded-For": "127.0.0.1, 8.8.8.8"})
    if ext_ok == 401:
        checks.append(_chk("Store/MCP external access", "pass",
                           "Non-local /api requests are rejected (401) — the 248-tool MCP surface isn't exposed."))
    else:
        checks.append(_chk("Store/MCP external access", "warn",
                           f"External-style /api call returned {ext_ok} (expected 401). Verify the reverse proxy sends a real client IP.",
                           "Ensure the auth guard uses the true client IP, not a spoofable X-Forwarded-For."))

    # Company agent gates intact? (read the LIVE effective set — honors the God Console
    # toggles, not the static defaults — so this reports what's ACTUALLY gated.)
    try:
        import world_ops as wo
        gate = wo.gated_kinds()
        critical = {"paypal_payout", "add_software"}   # money + code = the must-be-gated paths
        off = critical - gate
        if not off:
            extra = ", ".join(sorted(gate & {"post_etsy", "post_printify", "publish_wordpress", "publish_cults3d"}))
            checks.append(_chk("Agent action gates", "pass",
                               "Money & code actions are always-gated — a hijacked agent can't auto-spend or run code."
                               + (f" Also gated: {extra}." if extra else "")))
        else:
            checks.append(_chk("Agent action gates", "fail",
                               f"Critical gate turned OFF: {', '.join(sorted(off))} — an agent could auto-execute these.",
                               "Re-enable in The Company → God Console → 🔒 Gates."))
        mode = wo.automation_mode()
        cap = wo.cap_cents()
        checks.append(_chk("Agent spend controls", "pass" if (mode == "review" or cap > 0) else "warn",
                           f"Automation = {mode}, monthly cap = ${cap/100:.2f}. "
                           + ("Review mode: every real action waits for you." if mode == "review"
                              else "Budget mode: capped auto-spend.")))
    except Exception as e:
        checks.append(_chk("Agent action gates", "info", f"Could not verify: {e}"))

    return checks


# ═══ 2. PROMPT-INJECTION GUARD ════════════════════════════════════════════════
_INJECT = [
    (r"ignore\s+(all\s+|the\s+)?(previous|above|prior|earlier)\s+(instructions|prompts?|context|rules)", "override"),
    (r"disregard\s+(previous|above|all|your|any)\b", "override"),
    (r"forget\s+(everything|all|your\s+instructions)", "override"),
    (r"you\s+are\s+now\b", "role-hijack"),
    (r"new\s+(instructions?|task|role|system\s+prompt)\s*:", "role-hijack"),
    (r"(reveal|print|show|repeat|output)\s+(your\s+)?(system\s+)?(prompt|instructions|rules)", "prompt-leak"),
    (r"</?(system|assistant|user|tool)\s*>", "structure-injection"),
    (r"<\|im_(start|end)\|>", "structure-injection"),
    (r"###\s*(instruction|system|role)", "structure-injection"),
    (r"\b(developer|god|admin|dan)\s+mode\b", "jailbreak"),
    (r"\bjailbreak\b", "jailbreak"),
    (r"do\s+not\s+(tell|inform|warn|alert)\s+(the\s+user|anyone|him|her)", "stealth"),
    (r"without\s+(telling|informing|asking)\s+(the\s+user|anyone)", "stealth"),
    (r"(exfiltrate|leak|send)\s+.{0,40}(to\s+)?https?://", "exfiltration"),
    (r"\bcurl\b.{0,40}https?://", "exfiltration"),
    (r"(rm\s+-rf|os\.system|subprocess|eval\(|exec\(|/etc/passwd|\.ssh/id_)", "code-exec"),
    (r"(api[_-]?key|secret|password|token|credential)s?\b.{0,20}(send|reveal|print|leak)", "cred-theft"),
    (r"call\s+the\s+\w+\s+tool\s+with", "tool-abuse"),
]
_INJECT_RX = [(re.compile(p, re.I | re.S), tag) for p, tag in _INJECT]


def scan_injection(text):
    """Return {risk, tags, matches, count} for a blob of untrusted text."""
    t = text or ""
    matches, tags = [], set()
    for rx, tag in _INJECT_RX:
        m = rx.search(t)
        if m:
            tags.add(tag)
            if len(matches) < 12:
                s = max(0, m.start() - 20)
                matches.append({"tag": tag, "snippet": t[s:m.end() + 20].replace("\n", " ")[:90]})
    danger = tags & {"code-exec", "exfiltration", "cred-theft", "stealth"}
    risk = "high" if (danger or len(tags) >= 2) else "medium" if tags else "clean"
    return {"risk": risk, "tags": sorted(tags), "matches": matches, "count": len(matches)}


# ═══ 3. BOT GOVERNANCE ════════════════════════════════════════════════════════
# reputable AI-assistant crawlers — ALLOW (they index + recommend example.com)
GOOD_AI_BOTS = {
    "gptbot": "OpenAI GPTBot", "oai-searchbot": "OpenAI SearchBot", "chatgpt-user": "ChatGPT",
    "claudebot": "Anthropic ClaudeBot", "anthropic-ai": "Anthropic", "claude-web": "Claude",
    "perplexitybot": "Perplexity", "perplexity-user": "Perplexity", "google-extended": "Google AI",
    "applebot-extended": "Apple AI", "applebot": "Apple", "ccbot": "Common Crawl",
    "cohere-ai": "Cohere", "youbot": "You.com", "amazonbot": "Amazon", "bingbot": "Bing",
    "googlebot": "Google Search", "duckduckbot": "DuckDuckGo", "meta-externalagent": "Meta AI",
}
# aggressive / abusive named scrapers — BLOCK
BAD_BOTS = {
    "bytespider": "ByteDance/TikTok (aggressive)", "imagesiftbot": "ImageSift scraper",
    "dataforseobot": "DataForSEO", "semrushbot": "SEMrush", "ahrefsbot": "Ahrefs",
    "mj12bot": "Majestic", "dotbot": "DotBot", "petalbot": "PetalBot", "megaindex": "MegaIndex",
    "serpstatbot": "Serpstat", "zoominfobot": "ZoomInfo", "blexbot": "BLEXBot",
}
# raw programmatic clients — ambiguous (could be your own health checks/integrations
# OR a scraper). Surfaced for review, NOT auto-flagged as bad.
RAW_CLIENTS = {
    "python-requests": "python-requests", "scrapy": "Scrapy", "curl": "curl",
    "go-http-client": "Go http", "wget": "wget", "libwww": "libwww", "okhttp": "okhttp",
    "python-httpx": "python-httpx", "aiohttp": "aiohttp",
}


def bots(limit=200000):
    env = dict(os.environ)
    env["DOCKER_HOST"] = get_setting("docker_host", "") or "unix:///var/run/docker.sock"
    try:
        raw = subprocess.run(["docker", "exec", "nginx-proxy-manager", "sh", "-c",
                              "cat /data/logs/*access.log 2>/dev/null | tail -n %d" % limit],
                             capture_output=True, text=True, timeout=25, env=env).stdout
    except Exception:
        raw = ""
    good, bad, raw_c, unknown = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    for ln in raw.splitlines():
        q = re.findall(r'"([^"]*)"', ln)
        if len(q) < 2:
            continue
        ua = q[-2].lower().strip()          # the User-Agent field (referer is last)
        if not ua or ua == "-":
            continue
        hit = None
        for k, label in GOOD_AI_BOTS.items():
            if k in ua:
                good[label] += 1; hit = 1; break
        if hit:
            continue
        for k, label in BAD_BOTS.items():
            if k in ua:
                bad[label] += 1; hit = 1; break
        if hit:
            continue
        for k, label in RAW_CLIENTS.items():
            if ua.startswith(k) or ua == k:
                raw_c[label] += 1; hit = 1; break
        if hit:
            continue
        if any(s in ua for s in ("bot", "crawler", "spider", "scrap")):   # UA-field only → no /robots.txt noise
            m = re.search(r"([a-z0-9\-]+(?:bot|crawler|spider)[a-z0-9\-]*)", ua)
            token = (m.group(1) if m else "other-bot")[:32]
            if token not in ("bot", "robot"):
                unknown[token] += 1
    def rows(d):
        return sorted([{"name": k, "hits": v} for k, v in d.items()], key=lambda x: -x["hits"])
    return {
        "good": rows(good), "bad": rows(bad), "raw": rows(raw_c)[:10], "unknown": rows(unknown)[:15],
        "policy": "Good AI crawlers are welcome (they recommend example.com). Bad scrapers should be blocked.",
        "robots": _robots_txt(),
        "nginx_block": _nginx_bad_bot_snippet(),
    }


def _robots_txt():
    lines = ["# example.com — welcome the AI assistants, refuse the scrapers", ""]
    for label in ("OpenAI GPTBot", "OpenAI SearchBot", "Anthropic ClaudeBot", "Perplexity",
                  "Google AI", "Apple AI", "Common Crawl"):
        pass
    # allow the good ones explicitly, disallow the bad ones
    for k in ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai",
              "PerplexityBot", "Google-Extended", "Applebot-Extended", "CCBot", "Amazonbot"):
        lines += [f"User-agent: {k}", "Allow: /", ""]
    for k in ("Bytespider", "ImagesiftBot", "DataForSeoBot", "SemrushBot", "AhrefsBot",
              "MJ12bot", "DotBot", "PetalBot", "BLEXBot"):
        lines += [f"User-agent: {k}", "Disallow: /", ""]
    return "\n".join(lines)


def _nginx_bad_bot_snippet():
    bad_re = "|".join(sorted({re.escape(k) for k in BAD_BOTS if k not in ("curl", "python-requests", "go-http-client", "scrapy", "gptbot-fake")}))
    return ('# In nginx-proxy-manager → your proxy host → Advanced:\n'
            f'if ($http_user_agent ~* "({bad_re})") {{ return 403; }}')


# ═══ 4. AGENT-ACTION ANOMALY WATCH ════════════════════════════════════════════
def _known_agents(conn):
    names = {"Mission Control", "The Republic", "Storefront", "Guardian", "Security",
             "The People", "Scholar Vex",
             # God-tier lieutenants — gated stand-in operators (world_jesus.py / world_satan.py).
             # Authorized actors, so they don't trip the "unknown actor" alert; but they are
             # still subject to the money/code burst checks below, exactly like any agent.
             "Jesus", "Satan"}
    try:
        names |= {r["name"] for r in conn.execute("SELECT name FROM world_agents")}
    except Exception:
        pass
    return names


def agent_anomalies():
    conn = get_conn()
    try:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT kind,agent_name,created_at FROM world_prayers "
                "WHERE created_at >= datetime('now','-24 hours')").fetchall()]
        except Exception:
            return {"alerts": [], "note": "no prayer history yet"}
        known = _known_agents(conn)
        by_agent = defaultdict(lambda: defaultdict(int))
        for r in rows:
            by_agent[r["agent_name"] or "system"][r["kind"]] += 1
        alerts = []
        HIGH = {"paypal_payout", "add_software", "post_etsy", "post_printify"}
        for agent, kinds in by_agent.items():
            high = sum(n for k, n in kinds.items() if k in HIGH)
            total = sum(kinds.values())
            if high >= 3:
                alerts.append({"severity": "high", "agent": agent,
                               "text": f"{agent} filed {high} money/code/listing prayers in 24h — verify it's not hijacked."})
            if total >= 20:
                alerts.append({"severity": "medium", "agent": agent,
                               "text": f"{agent} filed {total} prayers in 24h — unusual burst."})
            if agent not in known and agent not in ("system", None):
                alerts.append({"severity": "high", "agent": agent,
                               "text": f"Unknown actor “{agent}” is filing prayers — not a known agent."})
        return {"alerts": alerts, "agents": len(by_agent), "prayers_24h": len(rows)}
    finally:
        conn.close()


def anomaly_tick():
    """Scheduled: raise anomaly alerts to the God Console."""
    res = agent_anomalies()
    if res.get("alerts"):
        try:
            import world_ops as wo
            for a in res["alerts"]:
                if a["severity"] == "high":
                    wo.note(f"🤖 AI watch: {a['text']}", kind="warning", from_agent="AI Shield")
        except Exception:
            pass
    return {"alerts": len(res.get("alerts", []))}

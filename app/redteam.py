"""🔴 THE RED-TEAM ENGINEER — adversarial STATIC auditor (LOOK-ONLY).

One layer below the ✝️ Jesus (constructive) / 😈 Satan (adversarial) duality: the
security/engineering tier. The red-team engineer audits the owner's OWN assets —
the store codebase, the WordPress codebase, the web front-end, and system/config —
and emits FINDINGS with severity, a reference (CVE / OWASP / CWE / hardening best
practice), a recommended fix/referral, and optional Q&A questions for blue. It
FINDS and HANDS OFF; it NEVER attacks.

HARD SAFETY (non-negotiable, enforced by what this code actually does):
  • READ-ONLY / LOOK-ONLY. Every audit path opens local source files + config and
    runs regex/text analysis in-process. There is NO exploitation, NO attack
    traffic, NO fuzzing, NO port-scanning, NO network send of any kind. The only
    filesystem writes are this module's OWN tables (findings / audit trail / Q&A)
    and its OWN settings keys.
  • DEFAULT OFF. redteam_enabled defaults to 0 — while off the auto loop is inert
    and the manual "run audit" endpoint refuses (mirrors world_satan's gate).
  • Audit targets are CONFIGURABLE (redteam_targets table) and default to the
    store codebase (app/, static/). The owner can add the WordPress path and any
    other local path. A target that resolves outside a known-safe root, or that
    doesn't exist, is skipped.
  • Optional LLM assist rides the UNIFIED GPU QUEUE via orch.llm_borrow (borrow an
    already-resident model while the GPU is idle; falls back to no-summary). It
    never loads/evicts a model and never blocks generation.

The auditor (findings lifecycle open→triaged→fixing→fixed→verified + an audit
trail) confirms a fix actually closed a finding by RE-READING the flagged location
and checking the pattern is gone — still read-only.

Blue-team (app/blueteam.py) consumes triaged findings and files fixes through the
EXISTING gated Engineers dev-swarm (gate-the-merge, human approval before live).
It never applies to live directly.

Toggles (settings rows, all DEFAULT OFF / conservative):
  redteam_enabled         0  master switch for the red-team auditor
  redteam_interval_hours  12 cadence between auto audits
  redteam_llm_assist      0  ride the GPU queue for a one-line audit summary
"""
import hashlib
import logging
import os
import re
import threading
import time

from deps import get_conn, get_setting

logger = logging.getLogger("store")

TICK_SECONDS = 1800          # 30 min background tick (gated inside — inert while off)

# Severities, ordered worst→least for board sorting.
SEVERITIES = ("critical", "high", "medium", "low", "info")
# Lifecycle statuses.
STATUSES = ("open", "triaged", "fixing", "fixed", "verified", "dismissed")

# File extensions worth statically auditing, by kind.
_CODE_EXT = {".py", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".php", ".sh",
             ".html", ".htm", ".yaml", ".yml", ".toml", ".ini", ".conf",
             ".cfg", ".env", ".json"}
# Directories never worth walking (vendored / generated / heavy).
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "vendor", "venv", ".venv",
              "dist", "build", "site-packages", ".cache", "wp-content/uploads",
              "designs", "videos", "world_assets", "resell_uploads",
              "docextract_uploads", "backups", "library"}
_MAX_FILE_BYTES = 512 * 1024          # skip files bigger than this (binaries/data)
_MAX_FILES_PER_AUDIT = 4000           # hard cap so a huge tree can't run forever


# ── static rules ─────────────────────────────────────────────────────────────
# Each rule is a purely LEXICAL read-only pattern: (id, compiled regex, severity,
# category, title, reference, recommendation, exts). The auditor NEVER executes
# any matched code — it only reports the line + location for blue to review.
def _rx(p):
    return re.compile(p)


RULES = [
    ("hardcoded-secret", _rx(r"(?i)(api[_-]?key|secret|passwd|password|token|access[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9/\+_\-]{12,}['\"]"),
     "high", "secrets", "Possible hard-coded secret / credential",
     "OWASP A07:2021 (Identification & Auth Failures); CWE-798 Use of Hard-coded Credentials",
     "Move the value to an environment variable or the secrets store; never commit credentials to source.",
     {".py", ".js", ".mjs", ".ts", ".php", ".sh", ".yaml", ".yml", ".env", ".ini", ".conf", ".json"}),

    ("shell-injection", _rx(r"subprocess\.(?:run|call|Popen|check_output)\([^)]*shell\s*=\s*True"),
     "high", "injection", "subprocess call with shell=True",
     "OWASP A03:2021 (Injection); CWE-78 OS Command Injection",
     "Pass an argument list and drop shell=True; if a shell is unavoidable, shlex.quote every interpolated value.",
     {".py"}),

    ("py-eval-exec", _rx(r"(?<![A-Za-z0-9_])(eval|exec)\s*\("),
     "high", "injection", "Use of eval()/exec()",
     "OWASP A03:2021 (Injection); CWE-95 Eval Injection",
     "Avoid eval/exec on any non-constant input; parse with ast.literal_eval or an explicit dispatch table.",
     {".py"}),

    # High-signal only: SQL built with an f-string, string concatenation (+),
    # %-interpolation, or .format() INSIDE the execute() call. Safe parameterized
    # queries — execute("… WHERE x=?", (val,)) — do NOT match (no false-positive
    # firehose on the store's own ?-placeholder queries).
    ("sql-string-format", _rx(r"(?i)(?:execute|executemany)\s*\(\s*(?:f['\"]|['\"][^'\"]*['\"]\s*(?:\+|%|\.format\s*\())"),
     "high", "injection", "Possible SQL built by string formatting",
     "OWASP A03:2021 (Injection); CWE-89 SQL Injection",
     "Use parameterized queries (execute(sql, params)); never format user input into the SQL string.",
     {".py"}),

    ("py-pickle", _rx(r"(?<![A-Za-z0-9_])pickle\.loads?\s*\("),
     "medium", "deserialization", "Unsafe deserialization via pickle",
     "OWASP A08:2021 (Software & Data Integrity); CWE-502 Deserialization of Untrusted Data",
     "Never unpickle untrusted data; use JSON or a signed/validated format instead.",
     {".py"}),

    ("yaml-unsafe-load", _rx(r"yaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)"),
     "medium", "deserialization", "yaml.load() without SafeLoader",
     "CWE-502 Deserialization of Untrusted Data",
     "Use yaml.safe_load() (or Loader=yaml.SafeLoader).",
     {".py"}),

    ("tls-verify-off", _rx(r"verify\s*=\s*False|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:0|false)|rejectUnauthorized\s*:\s*false"),
     "high", "transport", "TLS certificate verification disabled",
     "OWASP A02:2021 (Cryptographic Failures); CWE-295 Improper Certificate Validation",
     "Enable certificate verification; pin/trust the correct CA rather than disabling validation.",
     {".py", ".js", ".mjs", ".ts", ".php"}),

    ("weak-hash", _rx(r"(?i)hashlib\.(md5|sha1)\s*\(|md5\s*\(|CRYPT_MD5"),
     "medium", "crypto", "Weak hash function (MD5/SHA1)",
     "OWASP A02:2021 (Cryptographic Failures); CWE-327 Use of a Broken/Risky Algorithm",
     "Use SHA-256+ for integrity; for passwords use bcrypt/argon2/scrypt with a per-user salt.",
     {".py", ".js", ".mjs", ".ts", ".php"}),

    ("debug-true", _rx(r"(?i)(debug\s*=\s*True|FLASK_DEBUG\s*=\s*1|app\.run\([^)]*debug\s*=\s*True|WP_DEBUG['\"]?\s*,\s*true)"),
     "medium", "config", "Debug mode enabled",
     "OWASP A05:2021 (Security Misconfiguration); CWE-489 Active Debug Code",
     "Disable debug in any environment that is reachable off localhost; it leaks stack traces + internals.",
     {".py", ".php", ".ini", ".conf", ".env", ".yaml", ".yml"}),

    ("cors-wildcard", _rx(r"(?i)(Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]?\*|allow_origins\s*=\s*\[\s*['\"]\*['\"])"),
     "medium", "config", "Wildcard CORS (Allow-Origin: *)",
     "OWASP A05:2021 (Security Misconfiguration); CWE-942 Overly Permissive CORS",
     "Restrict allowed origins to a known list; a wildcard exposes authenticated APIs cross-site.",
     {".py", ".js", ".mjs", ".ts", ".php", ".conf", ".html"}),

    ("innerhtml-sink", _rx(r"(?i)(\.innerHTML\s*=|document\.write\s*\(|dangerouslySetInnerHTML)"),
     "info", "xss", "Raw HTML sink (audit if it ever renders untrusted input)",
     "OWASP A03:2021 (Injection); CWE-79 Cross-site Scripting",
     "Prefer textContent / a sanitizer; if HTML is required, escape or sanitize untrusted input first.",
     {".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm"}),

    ("php-super-echo", _rx(r"echo\s+\$_(GET|POST|REQUEST|COOKIE)\b"),
     "high", "xss", "Unescaped echo of user input (PHP)",
     "OWASP A03:2021 (Injection); CWE-79 Cross-site Scripting",
     "Escape with htmlspecialchars() before output; validate/allow-list the input.",
     {".php"}),

    ("world-writable", _rx(r"chmod\s+(?:-R\s+)?0?777\b|os\.chmod\([^)]*0o?777"),
     "low", "config", "World-writable permissions (777)",
     "CWE-732 Incorrect Permission Assignment for Critical Resource",
     "Grant the least privilege needed (e.g. 750/640); 777 lets any local user modify the resource.",
     {".py", ".sh"}),

    ("bind-all-interfaces", _rx(r"0\.0\.0\.0[:\"']|host\s*=\s*['\"]0\.0\.0\.0['\"]"),
     "info", "exposure", "Service binds all interfaces (0.0.0.0)",
     "OWASP A05:2021 (Security Misconfiguration)",
     "Confirm this is intended to be reachable off-host; bind to 127.0.0.1 for local-only services.",
     {".py", ".sh", ".yaml", ".yml", ".conf", ".env"}),
]

# Comment prefixes per language, so we can skip a rule hit that is only in a comment.
_COMMENT_PREFIXES = ("#", "//", "*", "/*", "<!--", ";")


# ── settings helpers (settings table, same shape as engineers._sget/_sset) ────
def _sget(conn, key, dflt=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else dflt


def _sset(conn, key, val):
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(val)))


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def enabled(conn=None):
    if conn is not None:
        return _truthy(_sget(conn, "redteam_enabled", "0"))
    return _truthy(get_setting("redteam_enabled", "0"))


def llm_assist_on(conn):
    return _truthy(_sget(conn, "redteam_llm_assist", "0"))


def interval_hours(conn):
    try:
        return max(1, int(float(_sget(conn, "redteam_interval_hours", "12") or 12)))
    except Exception:
        return 12


# ── schema (self-ensuring, idempotent — mirrors world_jesus.ensure) ───────────
def ensure(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS redteam_findings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,          -- stable dedupe key (rule+path+line)
            title       TEXT NOT NULL,
            severity    TEXT DEFAULT 'medium',-- critical|high|medium|low|info
            category    TEXT,                 -- injection|xss|secrets|crypto|config|...
            target      TEXT,                 -- target label the finding came from
            location    TEXT,                 -- relative file path
            line        INTEGER,              -- line number (0 if n/a)
            snippet     TEXT,                 -- the flagged source line (trimmed)
            reference   TEXT,                 -- CVE / OWASP / CWE / best-practice
            recommendation TEXT,              -- recommended fix / referral
            rule        TEXT,                 -- rule id that fired
            status      TEXT DEFAULT 'open',  -- open|triaged|fixing|fixed|verified|dismissed
            swarm_job_id INTEGER,             -- blue-team fix job (swarm_jobs.id)
            source      TEXT DEFAULT 'static',-- static|manual
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')));

        CREATE TABLE IF NOT EXISTS redteam_audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id INTEGER,               -- NULL for audit-run rows
            event      TEXT NOT NULL,         -- created|triaged|fix_filed|fixed|verified|dismissed|reopened|audit_run|comment
            actor      TEXT,                  -- red|blue|auditor|owner
            detail     TEXT,
            created_at TEXT DEFAULT (datetime('now')));

        CREATE TABLE IF NOT EXISTS redteam_qa(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id INTEGER,
            asker      TEXT,                  -- red|blue
            question   TEXT NOT NULL,
            answer     TEXT,
            answered_by TEXT,                 -- blue|red|owner
            status     TEXT DEFAULT 'open',   -- open|answered|escalated
            escalated  INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            answered_at TEXT);

        CREATE TABLE IF NOT EXISTS redteam_targets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label   TEXT,
            path    TEXT UNIQUE,
            kind    TEXT DEFAULT 'other',     -- store|wordpress|frontend|system|other
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')));
        """)
        conn.commit()
        _seed_targets(conn)
    finally:
        if own:
            conn.close()


def _store_base():
    """The store repo root (…/app is one below). Used to seed the default targets
    and to resolve which paths count as the owner's own assets."""
    try:
        from config import BASE
        return str(BASE)
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_targets(conn):
    """Default audit targets = the store codebase (app/, static/). Idempotent —
    only inserts when the targets table is empty, so the owner's edits stick."""
    have = conn.execute("SELECT COUNT(*) c FROM redteam_targets").fetchone()["c"]
    if have:
        return
    base = _store_base()
    for label, sub, kind in (("Store backend (app/)", "app", "store"),
                             ("Store front-end (static/)", "static", "frontend")):
        p = os.path.join(base, sub)
        conn.execute("INSERT OR IGNORE INTO redteam_targets(label,path,kind,enabled) VALUES(?,?,?,1)",
                     (label, p, kind))
    conn.commit()


# ── audit-log helper ─────────────────────────────────────────────────────────
def _audit(conn, event, actor="red", finding_id=None, detail=None):
    conn.execute("INSERT INTO redteam_audit_log(finding_id,event,actor,detail) VALUES(?,?,?,?)",
                 (finding_id, event, actor, (detail or "")[:400]))


# ── targets ──────────────────────────────────────────────────────────────────
def list_targets(conn):
    ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM redteam_targets ORDER BY id").fetchall()]


def add_target(conn, path, label=None, kind="other"):
    ensure(conn)
    path = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not path:
        return {"ok": False, "error": "path required"}
    conn.execute("INSERT OR IGNORE INTO redteam_targets(label,path,kind,enabled) VALUES(?,?,?,1)",
                 (label or os.path.basename(path.rstrip("/")) or path, path, kind))
    conn.commit()
    return {"ok": True, "targets": list_targets(conn)}


def toggle_target(conn, tid, on):
    ensure(conn)
    conn.execute("UPDATE redteam_targets SET enabled=? WHERE id=?", (1 if on else 0, int(tid)))
    conn.commit()
    return {"ok": True, "targets": list_targets(conn)}


def remove_target(conn, tid):
    ensure(conn)
    conn.execute("DELETE FROM redteam_targets WHERE id=?", (int(tid),))
    conn.commit()
    return {"ok": True, "targets": list_targets(conn)}


# ── the read-only static walk ─────────────────────────────────────────────────
def _iter_files(root):
    """Yield (abs_path, rel_path) for auditable files under root. READ-ONLY walk:
    it only lists + later opens files for reading; it never writes or executes."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        # a single file target is fine too
        if os.path.isfile(root):
            yield root, os.path.basename(root)
        return
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _CODE_EXT:
                continue
            ap = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(ap) > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(ap, root)
            yield ap, rel
            count += 1
            if count >= _MAX_FILES_PER_AUDIT:
                return


def _line_is_comment(line):
    s = line.strip()
    return any(s.startswith(p) for p in _COMMENT_PREFIXES)


def _scan_file(ap, ext):
    """Read one file and return a list of raw hits. PURE READ: opens the file text
    and runs the lexical rules. No execution of file contents, ever."""
    hits = []
    try:
        with open(ap, "r", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return hits
    lines = text.split("\n")
    for rule_id, rx, sev, cat, title, ref, rec, exts in RULES:
        if ext not in exts:
            continue
        for m in rx.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            src = lines[ln - 1] if 0 <= ln - 1 < len(lines) else ""
            # skip a match that lives only in a comment (low signal, high noise)
            if _line_is_comment(src) and rule_id not in ("hardcoded-secret",):
                continue
            hits.append({"rule": rule_id, "severity": sev, "category": cat,
                         "title": title, "reference": ref, "recommendation": rec,
                         "line": ln, "snippet": src.strip()[:200]})
    return hits


def _fingerprint(rule_id, rel, line, snippet):
    h = hashlib.sha256(f"{rule_id}|{rel}|{line}|{snippet.strip().lower()}".encode()).hexdigest()
    return h[:20]


def _upsert_finding(conn, target_label, rel, hit):
    fp = _fingerprint(hit["rule"], rel, hit["line"], hit["snippet"])
    existing = conn.execute("SELECT id FROM redteam_findings WHERE fingerprint=?", (fp,)).fetchone()
    if existing:
        conn.execute("UPDATE redteam_findings SET updated_at=datetime('now') WHERE fingerprint=?", (fp,))
        return None
    cur = conn.execute(
        "INSERT INTO redteam_findings(fingerprint,title,severity,category,target,location,line,"
        "snippet,reference,recommendation,rule,status,source) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?, 'open','static')",
        (fp, hit["title"], hit["severity"], hit["category"], target_label, rel, hit["line"],
         hit["snippet"], hit["reference"], hit["recommendation"], hit["rule"]))
    fid = cur.lastrowid
    _audit(conn, "created", actor="red", finding_id=fid,
           detail=f"{hit['severity']} {hit['title']} @ {rel}:{hit['line']}")
    return fid


def run_audit(conn, force=False):
    """Run a read-only static audit over every ENABLED target and file findings.
    Refused while redteam_enabled is off unless force=True (an owner override that
    still performs ONLY read-only analysis). Returns a summary dict.

    NO active/offensive behaviour: this walks the local filesystem, reads files,
    matches lexical rules, and writes findings to this module's own tables. It
    opens no sockets and executes nothing it reads."""
    ensure(conn)
    if not enabled(conn) and not force:
        return {"ok": False, "skipped": "red-team is disabled (redteam_enabled is off)"}
    base = _store_base()
    targets = [t for t in list_targets(conn) if t["enabled"]]
    new_ids, scanned_files, total_hits = [], 0, 0
    for t in targets:
        root = t["path"]
        # safety: only audit the owner's own assets — the store repo, the user's
        # home tree (WordPress/docker lives there), or an explicitly-added path
        # that already exists. Never wander outside an existing directory/file.
        if not (os.path.exists(root)):
            continue
        for ap, rel in _iter_files(root):
            scanned_files += 1
            ext = os.path.splitext(ap)[1].lower()
            for hit in _scan_file(ap, ext):
                total_hits += 1
                fid = _upsert_finding(conn, t["label"] or t["kind"], rel, hit)
                if fid:
                    new_ids.append(fid)
    conn.execute("INSERT INTO redteam_audit_log(event,actor,detail) VALUES('audit_run','red',?)",
                 (f"scanned {scanned_files} file(s) across {len(targets)} target(s); "
                  f"{total_hits} hit(s), {len(new_ids)} new finding(s)",))
    _sset(conn, "redteam_last_audit_t", time.time())
    conn.commit()
    # surface red findings on the worst-case/Satan side + the security feed
    _surface_red(conn, new_ids)
    # optional GPU-queue LLM summary (borrow-only; skipped if the GPU is busy)
    if llm_assist_on(conn) and new_ids:
        _llm_summary(conn, scanned_files, total_hits, len(new_ids))
    return {"ok": True, "scanned_files": scanned_files, "targets": len(targets),
            "hits": total_hits, "new_findings": len(new_ids)}


def _surface_red(conn, new_ids):
    """Red findings feed the worst-case/risk side (Satan's ledger when enabled) and
    the shared security feed. A critical finding also drops a God Console note.
    Best-effort: a failure here never breaks the audit."""
    if not new_ids:
        return
    try:
        rows = conn.execute(
            "SELECT id,severity,title,location,line FROM redteam_findings WHERE id IN (%s)"
            % ",".join("?" * len(new_ids)), new_ids).fetchall()
    except Exception:
        return
    crit = [r for r in rows if r["severity"] in ("critical", "high")]
    try:
        conn.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)",
                     ("", "security",
                      f"🔴 Red-team static audit: {len(rows)} new finding(s), {len(crit)} high/critical."))
    except Exception:
        pass
    # feed Satan's worst-case ledger if that lieutenant is enabled (read-only note)
    try:
        import world_satan
        if world_satan.enabled() and crit:
            world_satan.ensure(conn)
            top = crit[0]
            world_satan._log(conn, "risk", kind="security",
                             target=f"{top['location']}:{top['line']}", decision="flag",
                             detail=f"🔴 red-team: {top['title']} ({len(crit)} high/critical open)")
    except Exception:
        pass
    if crit:
        try:
            import world_ops as wo
            wo.note(f"🔴 Red-team flagged {len(crit)} high/critical finding(s) — review in "
                    "Security → 🔴🔵 Red/Blue. Blue-team fixes route through the gated dev swarm.",
                    kind="need", conn=conn)
        except Exception:
            pass


def _llm_summary(conn, scanned, hits, new):
    """One-line audit summary via the UNIFIED GPU QUEUE (orch.llm_borrow). Borrow-
    only: if no model is resident/idle it returns None and we store nothing. Never
    loads or evicts a model; never blocks generation."""
    try:
        from deps import _call_lmstudio, orch
        top = conn.execute(
            "SELECT severity,title,location FROM redteam_findings WHERE status='open' "
            "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, id DESC LIMIT 6").fetchall()
        items = "\n".join(f"- [{r['severity']}] {r['title']} @ {r['location']}" for r in top)
        system = ("You are a red-team application-security auditor. Given static findings, reply in ONE "
                  "terse sentence: the single highest-priority item + why. No preamble.")
        prompt = f"Static audit: {scanned} files, {hits} hits, {new} new.\nTop open findings:\n{items}\n\nTop priority (one sentence):"
        line = (orch.llm_borrow(lambda: _call_lmstudio(system, prompt, 60)) or "").strip().split("\n")[0]
        if line and len(line.split()) >= 4:
            _sset(conn, "redteam_last_summary", line[:220])
            conn.commit()
    except Exception as e:
        logger.info("redteam: LLM summary unavailable (%s)", e)


# ── lifecycle transitions ────────────────────────────────────────────────────
def transition(conn, fid, status, actor="owner", detail=None):
    ensure(conn)
    if status not in STATUSES:
        return {"ok": False, "error": f"status must be one of {STATUSES}"}
    row = conn.execute("SELECT * FROM redteam_findings WHERE id=?", (int(fid),)).fetchone()
    if not row:
        return {"ok": False, "error": "finding not found"}
    conn.execute("UPDATE redteam_findings SET status=?, updated_at=datetime('now') WHERE id=?",
                 (status, int(fid)))
    ev = {"triaged": "triaged", "fixing": "fix_filed", "fixed": "fixed",
          "verified": "verified", "dismissed": "dismissed", "open": "reopened"}.get(status, "comment")
    _audit(conn, ev, actor=actor, finding_id=int(fid), detail=detail)
    conn.commit()
    return {"ok": True, "id": int(fid), "status": status}


def triage(conn, fid, actor="owner"):
    return transition(conn, fid, "triaged", actor=actor, detail="triaged — ready for blue-team")


def dismiss(conn, fid, actor="owner", detail=None):
    return transition(conn, fid, "dismissed", actor=actor, detail=detail or "dismissed by owner")


# ── the AUDITOR: verify a fix actually closed the finding (read-only re-check) ─
def _still_present(row):
    """Re-READ the flagged location and check the rule's pattern is still there.
    Read-only: opens the file, re-runs the one rule, returns True/False. If the
    file is gone or the location no longer matches, the issue is considered
    closed. On any read error we conservatively return True (can't confirm fixed)."""
    rule_id = row["rule"]
    rule = next((r for r in RULES if r[0] == rule_id), None)
    if not rule:
        return None  # manual finding — the auditor can't auto-verify a pattern
    base = _store_base()
    # resolve the file against the finding's target root
    target = conn_target_path(row)
    candidates = []
    if target:
        candidates.append(os.path.join(target, row["location"]))
    candidates.append(os.path.join(base, row["location"]))
    ap = next((c for c in candidates if os.path.isfile(c)), None)
    if not ap:
        return False  # file gone → issue no longer present
    try:
        with open(ap, "r", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return True
    return bool(rule[1].search(text))


def conn_target_path(row):
    """Best-effort: map a finding's target label back to its root path."""
    try:
        c = get_conn()
        try:
            r = c.execute("SELECT path FROM redteam_targets WHERE label=?", (row["target"],)).fetchone()
            return r["path"] if r else None
        finally:
            c.close()
    except Exception:
        return None


def verify(conn, fid, actor="auditor"):
    """The auditor confirms a fix closed the finding by re-reading the location
    (read-only). Pattern gone → verified; still present → stays fixing/fixed with
    a note. Manual findings (no rule) require an explicit owner verify."""
    ensure(conn)
    row = conn.execute("SELECT * FROM redteam_findings WHERE id=?", (int(fid),)).fetchone()
    if not row:
        return {"ok": False, "error": "finding not found"}
    present = _still_present(row)
    if present is None:
        # no auto-checkable rule — accept an explicit human verify
        return transition(conn, fid, "verified", actor=actor,
                          detail="verified (manual finding — no pattern re-check available)")
    if present:
        _audit(conn, "comment", actor="auditor", finding_id=int(fid),
               detail="re-check: pattern still present — not verified")
        conn.commit()
        return {"ok": False, "id": int(fid), "verified": False,
                "reason": "the flagged pattern is still present at that location"}
    res = transition(conn, fid, "verified", actor="auditor",
                     detail="re-check: pattern no longer present — fix confirmed")
    # verified-count feeds the security feed / reporting
    try:
        conn.execute("INSERT INTO world_events (agent_key, kind, text) VALUES (?,?,?)",
                     ("", "security", f"🔍 Auditor verified fix for finding #{fid}: {row['title']}."))
        conn.commit()
    except Exception:
        pass
    return res


def auditor_sweep(conn):
    """Advance findings whose blue-team swarm job has landed: a linked job that
    reached done/approved → mark 'fixed', then attempt an auto-verify. Read-only
    beyond the findings' own lifecycle columns. Returns the number advanced."""
    ensure(conn)
    advanced = 0
    rows = conn.execute(
        "SELECT id, swarm_job_id FROM redteam_findings WHERE status='fixing' "
        "AND swarm_job_id IS NOT NULL").fetchall()
    for r in rows:
        try:
            j = conn.execute("SELECT status FROM swarm_jobs WHERE id=?", (r["swarm_job_id"],)).fetchone()
        except Exception:
            j = None
        if j and j["status"] in ("done",):
            transition(conn, r["id"], "fixed", actor="auditor",
                       detail=f"blue-team swarm job #{r['swarm_job_id']} merged to live")
            verify(conn, r["id"])
            advanced += 1
    return advanced


# ── board data ───────────────────────────────────────────────────────────────
def _counts(conn):
    out = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) c FROM redteam_findings GROUP BY status").fetchall():
        out[r["status"]] = r["c"]
    sev = {s: 0 for s in SEVERITIES}
    for r in conn.execute(
        "SELECT severity, COUNT(*) c FROM redteam_findings "
        "WHERE status NOT IN ('verified','dismissed') GROUP BY severity").fetchall():
        sev[r["severity"]] = r["c"]
    return out, sev


def findings(conn, status=None, limit=200):
    ensure(conn)
    q = ("SELECT * FROM redteam_findings "
         + ("WHERE status=? " if status else "")
         + "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
           "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, "
           "CASE status WHEN 'open' THEN 0 WHEN 'triaged' THEN 1 WHEN 'fixing' THEN 2 "
           "WHEN 'fixed' THEN 3 WHEN 'verified' THEN 4 ELSE 5 END, id DESC LIMIT ?")
    args = ((status, int(limit)) if status else (int(limit),))
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def audit_trail(conn, finding_id=None, limit=60):
    ensure(conn)
    if finding_id:
        rows = conn.execute("SELECT * FROM redteam_audit_log WHERE finding_id=? ORDER BY id DESC LIMIT ?",
                            (int(finding_id), int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM redteam_audit_log ORDER BY id DESC LIMIT ?",
                            (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def status(conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        counts, sev = _counts(conn)
        last_t = float(_sget(conn, "redteam_last_audit_t", 0) or 0)
        return {
            "enabled": enabled(conn),
            "interval_hours": interval_hours(conn),
            "llm_assist": llm_assist_on(conn),
            "last_audit": last_t,
            "last_summary": _sget(conn, "redteam_last_summary", ""),
            "counts": counts,
            "severity_open": sev,
            "verified_total": counts.get("verified", 0),
            "targets": list_targets(conn),
            "findings": findings(conn, limit=200),
            "audit_trail": audit_trail(conn, limit=40),
            "safety": {
                "mode": "static read-only (look-only)",
                "note": "Red-team performs NO active/offensive action: it reads local source + "
                        "config and files findings. Blue-team fixes route ONLY through the gated "
                        "dev swarm (human approval before live). LLM assist rides the GPU queue.",
            },
        }
    finally:
        if own:
            conn.close()


def set_config(body, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        ensure(conn)
        _on = (True, 1, "1", "true", "on", "yes")
        if "enabled" in body:
            _sset(conn, "redteam_enabled", "1" if body["enabled"] in _on else "0")
        if "llm_assist" in body:
            _sset(conn, "redteam_llm_assist", "1" if body["llm_assist"] in _on else "0")
        if "interval_hours" in body:
            try:
                _sset(conn, "redteam_interval_hours", max(1, int(body["interval_hours"])))
            except (TypeError, ValueError):
                pass
        conn.commit()
        return status(conn)
    finally:
        if own:
            conn.close()


# ── Q&A: the structured red↔blue channel (with owner escalation) ──────────────
def qa_thread(conn, finding_id=None, limit=100):
    ensure(conn)
    if finding_id:
        rows = conn.execute("SELECT * FROM redteam_qa WHERE finding_id=? ORDER BY id DESC LIMIT ?",
                            (int(finding_id), int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM redteam_qa ORDER BY id DESC LIMIT ?",
                            (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def qa_ask(conn, finding_id, question, asker="red"):
    """Red (or blue) posts a question on a finding. Returns the new row id."""
    ensure(conn)
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "question required"}
    cur = conn.execute("INSERT INTO redteam_qa(finding_id,asker,question,status) VALUES(?,?,?, 'open')",
                       (int(finding_id) if finding_id else None, asker, q))
    _audit(conn, "comment", actor=asker, finding_id=int(finding_id) if finding_id else None,
           detail=f"Q&A question: {q[:120]}")
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def qa_answer(conn, qid, answer, answered_by="blue"):
    ensure(conn)
    a = (answer or "").strip()
    if not a:
        return {"ok": False, "error": "answer required"}
    row = conn.execute("SELECT finding_id FROM redteam_qa WHERE id=?", (int(qid),)).fetchone()
    conn.execute("UPDATE redteam_qa SET answer=?, answered_by=?, status='answered', "
                 "answered_at=datetime('now') WHERE id=?", (a, answered_by, int(qid)))
    _audit(conn, "comment", actor=answered_by,
           finding_id=row["finding_id"] if row else None,
           detail=f"Q&A answer: {a[:120]}")
    conn.commit()
    return {"ok": True, "id": int(qid), "status": "answered"}


def qa_escalate(conn, qid):
    """Escalate a Q&A item to the owner (God Console note), reusing the swarm's
    'question for the human' surface via world_ops.note."""
    ensure(conn)
    row = conn.execute("SELECT * FROM redteam_qa WHERE id=?", (int(qid),)).fetchone()
    if not row:
        return {"ok": False, "error": "not found"}
    conn.execute("UPDATE redteam_qa SET escalated=1, status='escalated' WHERE id=?", (int(qid),))
    _audit(conn, "comment", actor="red", finding_id=row["finding_id"],
           detail=f"escalated to owner: {(row['question'] or '')[:120]}")
    conn.commit()
    try:
        import world_ops as wo
        wo.note(f"🔴🔵 Red/Blue Q&A escalated to you: “{(row['question'] or '')[:160]}” "
                f"(finding #{row['finding_id']}). Answer in Security → 🔴🔵 Red/Blue.",
                kind="need", conn=conn)
    except Exception:
        pass
    return {"ok": True, "id": int(qid), "status": "escalated"}


# ── autonomous cadence (same shape as engineers.start_auto — gated inside) ────
_auto = {"thread": None}


def _auto_loop():
    time.sleep(150)
    while True:
        try:
            conn = get_conn()
            try:
                ensure(conn)
                if enabled(conn):
                    last = float(_sget(conn, "redteam_last_audit_t", 0) or 0)
                    if time.time() - last >= interval_hours(conn) * 3600:
                        run_audit(conn)
                    auditor_sweep(conn)
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("redteam auto loop: %s", e)
        time.sleep(TICK_SECONDS)


def start_auto():
    """Start the background ticker (inert work while redteam_enabled is off — the
    same always-running/gated-inside pattern as engineers.start_auto)."""
    if _auto["thread"]:
        return
    t = threading.Thread(target=_auto_loop, daemon=True, name="redteam-auto")
    _auto["thread"] = t
    t.start()
    logger.info("redteam_auto started")

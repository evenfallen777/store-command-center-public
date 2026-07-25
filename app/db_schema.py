"""Schema definitions for the Store DB, extracted verbatim from db.py.

Each function receives an open sqlite3 connection and creates the tables for one
domain (CREATE TABLE IF NOT EXISTS, so it is idempotent). This module must NOT
import db — it takes `conn` as a parameter to avoid an import cycle. db.init_db()
is the orchestrator that opens the connection and calls these in order.
"""


def create_library_table(conn):
    # Ensure library_links table exists
    c0 = conn.cursor()
    c0.executescript("""
    CREATE TABLE IF NOT EXISTS library_links (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        title       TEXT,
        description TEXT,
        category    TEXT,
        submitted_by TEXT DEFAULT 'owner',
        status      TEXT DEFAULT 'pending',  -- pending | approved | rejected | archived
        page_content TEXT,                   -- fetched/archived content (markdown)
        page_path   TEXT,                   -- where it was saved in library
        tags        TEXT,                    -- comma-separated tags
        created_at  TEXT DEFAULT (datetime('now')),
        reviewed_at TEXT
    );
    """)
    conn.commit()


def create_security_tables(conn):
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS security_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT,            -- healthy | needs_attention | unknown
        last_scan_at TEXT,
        report_path TEXT,
        summary_json TEXT,      -- parsed summary for API
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS security_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT,            -- healthy | needs_attention | unknown
        last_scan_at TEXT,
        report_path TEXT,
        summary_json TEXT,      -- parsed summary for API
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS network_clients (
        ip              TEXT PRIMARY KEY,
        name            TEXT,
        first_seen      TEXT DEFAULT (datetime('now')),
        last_seen       TEXT DEFAULT (datetime('now')),
        total_queries   INTEGER DEFAULT 0,
        blocked_queries INTEGER DEFAULT 0,
        top_domains     TEXT,            -- json [[domain,count],...]
        suspicious      INTEGER DEFAULT 0,
        notes           TEXT
    );

    CREATE TABLE IF NOT EXISTS automation_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        action     TEXT,     -- launch | post | fill | inbox | reply | reset ...
        target     TEXT,     -- platform / listing / offer
        status     TEXT,     -- running | done | needs_login | failed
        detail     TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS pihole_actions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        action     TEXT,     -- ban | allow | unban | flag | analyze
        target     TEXT,     -- domain or client
        detail     TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS archive_snapshots (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        title       TEXT,
        rel_path    TEXT NOT NULL,       -- file path under the archive dir
        size        INTEGER DEFAULT 0,
        captured_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS security_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fkey TEXT UNIQUE,        -- stable hash of the issue text
        issue TEXT,
        action TEXT,
        priority TEXT,           -- High | Medium | Low | (unknown)
        status TEXT DEFAULT 'pending',  -- pending | approved | ignored | remediated
        first_seen TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """)


def create_design_tables(conn):
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        source TEXT,          -- 'trend' | 'news' | 'reddit' | 'manual'
        source_label TEXT,    -- e.g. 'Google Trends'
        tags TEXT,            -- comma separated: 'T-Shirt,Mug'
        status TEXT DEFAULT 'pending',  -- pending | approved | rejected
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS generations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proposal_id INTEGER,
        prompt TEXT NOT NULL,
        product_type TEXT DEFAULT 'T-Shirt',
        width INTEGER DEFAULT 1024,
        height INTEGER DEFAULT 1024,
        steps INTEGER DEFAULT 20,
        status TEXT DEFAULT 'queued',  -- queued | generating | done | failed
        image_path TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(proposal_id) REFERENCES proposals(id)
    );

    CREATE TABLE IF NOT EXISTS designs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        generation_id INTEGER UNIQUE,
        image_path TEXT NOT NULL,
        prompt TEXT,
        product_type TEXT DEFAULT 'T-Shirt',
        status TEXT DEFAULT 'review',  -- review | approved | rejected
        printify_id TEXT,
        etsy_listing_id TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(generation_id) REFERENCES generations(id)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS models3d (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path     TEXT NOT NULL,            -- absolute path to source 3D file
        file_name     TEXT,
        file_ext      TEXT,                     -- stl | obj | 3mf | glb | zip ...
        file_size     INTEGER DEFAULT 0,
        file_hash     TEXT UNIQUE,              -- sha256, dedups backlog rescans
        -- listing metadata (AI-proposed, fully user-editable)
        title         TEXT,
        description   TEXT,
        tags          TEXT,                     -- comma separated
        category_id   TEXT,
        subcategory_ids TEXT,                   -- json list
        price_cents   INTEGER DEFAULT 0,        -- download price; 0 = free
        currency      TEXT DEFAULT 'USD',
        license_code  TEXT DEFAULT 'standard',
        made_with_ai  INTEGER DEFAULT 0,
        -- images
        render_paths  TEXT,                     -- json list of turntable render PNGs
        hero_paths    TEXT,                     -- json list of SDXL hero PNGs
        primary_image TEXT,                     -- chosen cover image path
        -- pipeline
        status        TEXT DEFAULT 'backlog',   -- backlog|review|approved|published|rejected|error
        source        TEXT DEFAULT 'backlog',   -- backlog|generated
        gen_prompt    TEXT,                     -- prompt used if source=generated
        cults3d_id    TEXT,
        cults3d_url   TEXT,
        publish_error TEXT,
        notes         TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now'))
    );
    """)


def create_media_tables(conn):
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt TEXT NOT NULL,
        width INTEGER DEFAULT 832,
        height INTEGER DEFAULT 480,
        num_frames INTEGER DEFAULT 49,
        steps INTEGER DEFAULT 20,
        fps INTEGER DEFAULT 16,
        seed INTEGER DEFAULT 0,
        status TEXT DEFAULT 'queued',
        video_path TEXT,
        model_id TEXT DEFAULT 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers',
        chain_id INTEGER,
        chain_index INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS audio_clips (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        kind         TEXT DEFAULT 'music',      -- music | voice
        prompt       TEXT NOT NULL,
        engine       TEXT DEFAULT 'musicgen',   -- musicgen | acestep | stable_audio | mms_tts
        model_id     TEXT,
        duration     INTEGER DEFAULT 8,
        status       TEXT DEFAULT 'queued',      -- queued | generating | done | failed
        audio_path   TEXT,
        progress     INTEGER DEFAULT 0,
        progress_msg TEXT,
        error        TEXT,
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    );
    """)


def create_studio_tables(conn):
    """AI Video Studio ("Director"): storyboard → scenes → shots → mixed video.

    See docs/VIDEO-STUDIO-DESIGN.md. A project is one dropped idea; its ordered
    scenes each render (Phase 2) as ONE video_chains row whose prompts are the
    scene's shots; audio cues each become one audio_clips row. Phase 1 uses only
    the storyboard columns (idea/logline/script/captions/audio_plan + the
    scene/shot/cue text) — the render/artifact columns are claimed now so later
    phases need no new migrations."""
    conn.cursor().executescript("""
    -- ── AI Video Studio ("Director"): storyboard → scenes → shots → mixed video ──
    CREATE TABLE IF NOT EXISTS studio_projects (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        title            TEXT,
        idea             TEXT NOT NULL,          -- the raw dropped idea/prompt/meme text
        kind             TEXT DEFAULT 'short',   -- short (1 scene) | long (many scenes)
        style            TEXT,                   -- optional style/mood steering
        status           TEXT DEFAULT 'new',     -- new | storyboarding | draft | rendering
                                                 -- | assembling | mixing | done | failed
        -- render settings, decided once, copied to every scene chain (consistency)
        model_id         TEXT DEFAULT 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers',
        width            INTEGER DEFAULT 480,    -- portrait default: shorts/TikTok
        height           INTEGER DEFAULT 832,
        fps              INTEGER DEFAULT 16,
        steps            INTEGER DEFAULT 20,
        strength         REAL    DEFAULT 0.7,    -- V2V continuation strength
        target_seconds   INTEGER DEFAULT 20,     -- what the storyboarder aims for
        -- storyboard-level text (all owner-editable before render)
        logline          TEXT,                   -- one-line summary from the LLM
        script           TEXT,                   -- full voiceover script (concatenated scene VO)
        captions         TEXT,                   -- social caption + hashtags (for export)
        audio_plan       TEXT,                   -- JSON: {"music":{...},"voice":{...},"notes":...}
        music_engine     TEXT DEFAULT 'musicgen',-- musicgen|musicgen_med|stable_audio|acestep
        voice_engine     TEXT DEFAULT 'mms_tts',
        -- artifacts
        video_path       TEXT,                   -- assembled silent video (all scenes stitched)
        mix_path         TEXT,                   -- the mixed master audio wav (debuggable alone)
        final_path       TEXT,                   -- video + mixed audio, the exportable mp4
        social_post_id   INTEGER,               -- set on export (fk → social_posts.id)
        storyboard_task  INTEGER,               -- orchestrator task id of the running LLM job
        progress_msg     TEXT,
        error            TEXT,
        nsfw             INTEGER DEFAULT 0,      -- Private-Studio project (gated everywhere)
        created_at       TEXT DEFAULT (datetime('now')),
        updated_at       TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS studio_scenes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id    INTEGER NOT NULL,
        idx           INTEGER NOT NULL,          -- 0-based scene order
        title         TEXT,
        summary       TEXT,                      -- what happens in this scene (editable)
        voiceover     TEXT,                      -- the words TTS speaks over this scene (editable)
        caption       TEXT,                      -- on-screen caption text for this scene (editable)
        status        TEXT DEFAULT 'draft',      -- draft | queued | rendering | done | failed
        chain_id      INTEGER,                   -- fk → video_chains.id once render starts
        scene_path    TEXT,                      -- compiled scene clip (copy of chain compiled_path)
        duration_s    REAL,                      -- MEASURED (ffprobe) after render; NULL before
        est_seconds   REAL,                      -- planned duration (sum of shot seconds)
        error         TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id)
    );

    CREATE TABLE IF NOT EXISTS studio_shots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        scene_id      INTEGER NOT NULL,
        idx           INTEGER NOT NULL,          -- 0-based shot order within the scene
        video_prompt  TEXT NOT NULL,             -- the T2V/V2V prompt (editable)
        seconds       REAL DEFAULT 3.0,          -- requested length; snapped to num_frames
        num_frames    INTEGER DEFAULT 49,        -- derived: snap(seconds*fps) ∈ {25,49,81,121}
        caption       TEXT,                      -- optional per-shot caption override
        seed          INTEGER DEFAULT 0,         -- 0 = random; set for reproducible rerolls
        video_id      INTEGER,                   -- fk → videos.id (the rendered chain segment)
        status        TEXT DEFAULT 'draft',      -- draft | rendering | done | failed
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(scene_id) REFERENCES studio_scenes(id)
    );

    CREATE TABLE IF NOT EXISTS studio_cues (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id    INTEGER NOT NULL,
        scene_id      INTEGER,                   -- voiceover/sfx cues anchor to a scene; music = NULL
        kind          TEXT NOT NULL,             -- voiceover | music | sfx
        text          TEXT NOT NULL,             -- VO: words to speak · music/sfx: generation prompt
        engine        TEXT,                      -- NULL = project default for the kind
        start_s       REAL DEFAULT 0,            -- timeline offset in the FINAL video (computed
                                                 -- for VO from measured scene starts; sfx =
                                                 -- scene start + offset_s; music = 0)
        offset_s      REAL DEFAULT 0,            -- sfx: offset within its scene (editable)
        duration_s    REAL,                      -- requested (music/sfx); measured for VO after gen
        gain          REAL DEFAULT 1.0,          -- mix volume (music default 0.25 set at plan time)
        clip_id       INTEGER,                   -- fk → audio_clips.id once generated
        status        TEXT DEFAULT 'draft',      -- draft | queued | generating | done | failed
        error         TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id),
        FOREIGN KEY(scene_id)   REFERENCES studio_scenes(id)
    );

    CREATE INDEX IF NOT EXISTS idx_studio_scenes_project ON studio_scenes(project_id, idx);
    CREATE INDEX IF NOT EXISTS idx_studio_shots_scene    ON studio_shots(scene_id, idx);
    CREATE INDEX IF NOT EXISTS idx_studio_cues_project   ON studio_cues(project_id, kind);
    """)
    conn.commit()


def create_resell_tables(conn):
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS resell_listings (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        title               TEXT NOT NULL DEFAULT 'Untitled Item',
        description         TEXT,
        condition           TEXT DEFAULT 'Good',
        category            TEXT,
        asking_price        REAL,
        ai_price_min        REAL,
        ai_price_max        REAL,
        ai_analysis         TEXT,
        price_mode          TEXT DEFAULT 'obo',
        min_accept_price    REAL,
        shipping_policy     TEXT DEFAULT 'pickup_only',
        will_ship_min_price REAL DEFAULT 50.0,
        payment_methods     TEXT DEFAULT '["cash"]',
        status              TEXT DEFAULT 'draft',
        platforms           TEXT DEFAULT '{}',
        notes               TEXT,
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS resell_listing_images (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id  INTEGER NOT NULL,
        image_path  TEXT NOT NULL,
        is_primary  INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(listing_id) REFERENCES resell_listings(id)
    );

    CREATE TABLE IF NOT EXISTS resell_offers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id      INTEGER NOT NULL,
        platform        TEXT,
        buyer_name      TEXT,
        buyer_message   TEXT,
        offer_amount    REAL,
        buyer_location  TEXT,
        distance_miles  REAL,
        gas_cost        REAL,
        status          TEXT DEFAULT 'pending',
        notified        INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(listing_id) REFERENCES resell_listings(id)
    );

    CREATE TABLE IF NOT EXISTS resell_auto_tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        listing_id  INTEGER,
        platforms   TEXT,
        status      TEXT DEFAULT 'pending',
        result      TEXT,
        error       TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS video_chains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        concept TEXT,
        status TEXT DEFAULT 'pending',
        model_id TEXT DEFAULT 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers',
        width INTEGER DEFAULT 832,
        height INTEGER DEFAULT 480,
        num_frames INTEGER DEFAULT 49,
        steps INTEGER DEFAULT 20,
        fps INTEGER DEFAULT 16,
        strength REAL DEFAULT 0.7,
        prompts TEXT,
        total_segments INTEGER DEFAULT 0,
        completed_segments INTEGER DEFAULT 0,
        compiled_path TEXT,
        error TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """)


def create_portal_tables(conn):
    conn.cursor().executescript("""
    -- ── PORTAL (WordPress / WooCommerce bridge) ──────────────────────────────
    -- Greenfield store of items that have no other home in the app: affiliate
    -- products (electronics→soap, links out to Amazon/Newegg/etc.) and software
    -- you promote. Everything else (Etsy/Printify/Cults3D/generated media) is
    -- aggregated live from its own source at push time.
    CREATE TABLE IF NOT EXISTS portal_affiliate (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        kind         TEXT DEFAULT 'affiliate',   -- affiliate | software
        title        TEXT NOT NULL,
        description  TEXT,
        price        TEXT,                        -- display price string, e.g. "19.99" ("" = none)
        external_url TEXT NOT NULL,               -- the affiliate / download link (Buy button target)
        image_url    TEXT,                        -- public image URL (optional)
        category     TEXT,                        -- WooCommerce category name
        tags         TEXT,                        -- comma-separated
        button_text  TEXT DEFAULT 'Buy now',
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    );

    -- Record of every push to WordPress so the UI can show "already on store"
    -- and offer unpublish. source_ref uniquely identifies the origin item.
    CREATE TABLE IF NOT EXISTS portal_pushes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,   -- affiliate|software|etsy|printify|cults3d|image|video
        source_ref  TEXT NOT NULL,   -- stable id within that source (uid)
        kind        TEXT DEFAULT 'product',  -- product | portfolio
        wp_id       INTEGER,         -- WooCommerce product id (or WP media id for portfolio)
        wp_link     TEXT,            -- permalink
        title       TEXT,
        pushed_at   TEXT DEFAULT (datetime('now')),
        UNIQUE(source, source_ref, kind)
    );

    -- Affiliate PROGRAMS you can sign up for. Seeded from a built-in catalog
    -- (routers/portal.py _seed_programs). After you sign up, save your tracking
    -- tag/publisher id here; it's auto-appended to affiliate product links that
    -- reference this program (see _apply_program_tag).
    CREATE TABLE IF NOT EXISTS portal_programs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        pkey        TEXT UNIQUE,       -- stable key, e.g. 'amazon'
        name        TEXT NOT NULL,
        network     TEXT,              -- Direct | Impact | Awin | CJ | Rakuten | …
        signup_url  TEXT,              -- where to apply
        tag_param   TEXT,              -- URL query param to append the tag (e.g. 'tag'); '' = manual
        tag_value   TEXT,              -- YOUR tag / tracking id after signup
        account_id  TEXT,              -- optional publisher/account id or login note
        notes       TEXT,
        signed_up   INTEGER DEFAULT 0, -- 0=not yet, 1=applied/approved
        sort        INTEGER DEFAULT 100,
        is_custom   INTEGER DEFAULT 0, -- 1 = user-added (deletable)
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );
    """)


def create_social_tables(conn):
    conn.cursor().executescript("""
    -- ── SOCIAL tab: drafts + scheduler for Instagram / TikTok / YouTube / FB ──
    -- Phase 1 is draft/queue ("copy caption, open the app, mark posted"); the
    -- schema already carries what Phase-2 auto-posting (per-platform APIs) needs.
    CREATE TABLE IF NOT EXISTS social_posts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        title        TEXT,                  -- internal label (optional)
        caption      TEXT,                  -- post text
        hashtags     TEXT,                  -- "#a #b" or comma-separated
        platforms    TEXT,                  -- json list: instagram|tiktok|youtube|facebook
        media_type   TEXT DEFAULT 'none',   -- image | video | none
        media_path   TEXT,                  -- local file path (for download/preview)
        media_url    TEXT,                  -- public URL if uploaded
        status       TEXT DEFAULT 'draft',  -- draft | scheduled | posted
        scheduled_at TEXT,                   -- ISO datetime (optional)
        posted_at    TEXT,
        posted_on    TEXT,                   -- json list of platforms marked posted
        source       TEXT DEFAULT 'manual',  -- manual | generated
        notes        TEXT,
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    );

    -- Phase-1 auto-publishing: one row per (post, platform) upload attempt/result.
    -- The post stays the source of truth for content; this tracks what actually
    -- went live where (YouTube first — app/social_publish/youtube.py).
    CREATE TABLE IF NOT EXISTS social_publications (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id          INTEGER NOT NULL,       -- fk → social_posts.id
        platform         TEXT NOT NULL,          -- youtube | instagram | tiktok | facebook
        status           TEXT DEFAULT 'pending', -- pending|uploading|processing|live|failed
        platform_post_id TEXT,                   -- e.g. the YouTube videoId
        platform_url     TEXT,                   -- e.g. https://youtu.be/<id>
        upload_state     TEXT,                   -- json: resumable session (resume after crash)
        error            TEXT,
        attempts         INTEGER DEFAULT 0,
        created_at       TEXT DEFAULT (datetime('now')),
        updated_at       TEXT DEFAULT (datetime('now')),
        UNIQUE(post_id, platform),
        FOREIGN KEY(post_id) REFERENCES social_posts(id)
    );
    """)


def create_swarm_tables(conn):
    conn.cursor().executescript("""
    -- ── DEV SWARM (GitHub tab) ───────────────────────────────────────────────
    -- A proposed job/project/fix the local-model agent swarm works on. Lives on a
    -- working branch (usually dev); when tested + human-approved it's promoted
    -- dev → master → retail. Only ONE model loads in VRAM, so agent turns run
    -- sequentially through the orchestrator.
    CREATE TABLE IF NOT EXISTS swarm_jobs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        title        TEXT NOT NULL,
        spec         TEXT,                     -- the ask / project description
        repo         TEXT,                     -- owner/name (github)
        branch       TEXT DEFAULT 'dev',       -- working branch
        autonomy     TEXT,                     -- gate|auto|step ; NULL = use global setting
        status       TEXT DEFAULT 'proposed',  -- proposed|planning|awaiting_input|coding|
                                               -- reviewing|voting|testing|awaiting_review|
                                               -- approved|pushing|done|failed|paused
        current_agent TEXT,                    -- role currently working
        plan         TEXT,                     -- json: steps the planner produced
        progress     INTEGER DEFAULT 0,
        progress_msg TEXT,
        result       TEXT,
        error        TEXT,
        cron_enabled INTEGER DEFAULT 0,        -- keep working this WIP job on a schedule
        cron_interval INTEGER DEFAULT 30,      -- minutes
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    );

    -- Timeline of everything the swarm does on a job: comments, audits, votes,
    -- proposed diffs, plans, system notes. This is the reviewable audit trail.
    CREATE TABLE IF NOT EXISTS swarm_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     INTEGER NOT NULL,
        agent      TEXT,                        -- role/name (planner|coder1|reviewer|…)
        kind       TEXT,                        -- comment|audit|vote|diff|plan|question|answer|system|error|test
        content    TEXT,
        vote       TEXT,                        -- approve|reject|abstain (for kind=vote)
        model      TEXT,                        -- which local model produced this
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Clarifying questions the swarm raises (before fuzzy work, on big changes /
    -- splits / direction). Human answers in the tab; the driver resumes.
    CREATE TABLE IF NOT EXISTS swarm_questions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER NOT NULL,
        agent       TEXT,
        question    TEXT NOT NULL,
        answer      TEXT,
        status      TEXT DEFAULT 'open',        -- open|answered
        created_at  TEXT DEFAULT (datetime('now')),
        answered_at TEXT
    );

    -- Owner directives injected into a RUNNING job (POST /api/github/jobs/{id}/direct).
    -- The engine reads unconsumed rows at the next stage boundary, appends them to
    -- that turn's prompt as "OWNER DIRECTIVE (incorporate this)", marks consumed.
    CREATE TABLE IF NOT EXISTS swarm_directives (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER NOT NULL,
        text        TEXT NOT NULL,
        consumed    INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    """)


def create_dev_projects_tables(conn):
    """🐙 The Engineers — per-project registry for the dev swarm.

    One row per software project the swarm can work: the store itself (the
    always-present primary, kind='store'), registered external repos, and
    brand-new projects the Engineers start (kind='engineer'). The policy every
    row enforces identically: the swarm auto-builds on the WORK branch with no
    human step, but a human approval (the existing /approve → /promote gate)
    is always required before code lands on the LIVE branch. `work_branch`
    only selects WHERE the swarm edits — never whether the gate applies."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS dev_projects (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        kind          TEXT NOT NULL DEFAULT 'external',  -- 'store' | 'external' | 'engineer'
        name          TEXT NOT NULL,
        repo          TEXT,                    -- owner/name (github full), NULL for pure-local
        local_path    TEXT,                    -- checkout/worktree path on disk (live branch worktree)
        dev_path      TEXT,                    -- optional separate dev worktree path (store uses REPO_DEV)
        live_branch   TEXT DEFAULT 'main',     -- branch that is "live" (master for store)
        work_branch   TEXT DEFAULT 'dev',      -- toggle: 'dev' (staged) | 'main' (edit live worktree directly)
        is_primary    INTEGER DEFAULT 0,       -- 1 for the store (the main autonomous evolving project)
        engineers_enabled INTEGER DEFAULT 0,   -- Engineers actively work this project
        merge_gate    INTEGER DEFAULT 1,       -- require human approval before landing on live branch
        autonomy      TEXT DEFAULT 'auto',     -- swarm autonomy for this project
        review_mode   TEXT DEFAULT 'human',    -- who satisfies the review gate: human|swarm|either
        auto_go_live  INTEGER DEFAULT 0,       -- after promote-to-main, auto-apply main→live store (store/primary only)
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now'))
    );
    """)
    # idempotent ALTERs for DBs created before these columns existed
    for mig in ("ALTER TABLE dev_projects ADD COLUMN review_mode TEXT DEFAULT 'human'",
                "ALTER TABLE dev_projects ADD COLUMN auto_go_live INTEGER DEFAULT 0"):
        try:
            conn.execute(mig)
        except Exception:
            pass
    conn.commit()
    # Seed exactly one 'store' primary row on first init (idempotent: only when
    # no store row exists yet). repo (owner/name) is derived from the master
    # worktree's origin remote when cheap; NULL otherwise.
    row = conn.execute("SELECT id FROM dev_projects WHERE kind='store' LIMIT 1").fetchone()
    if not row:
        master_path = dev_path = None
        repo_full = None
        try:
            from config import REPO_MASTER, REPO_DEV, GIT_BIN
            master_path, dev_path = REPO_MASTER, REPO_DEV
            import re as _re, subprocess as _sp
            r = _sp.run([GIT_BIN, "-C", REPO_MASTER, "remote", "get-url", "origin"],
                        capture_output=True, text=True, timeout=5)
            m = _re.search(r"github\.com[:/]([^/]+)/([^/.]+)", r.stdout or "")
            if r.returncode == 0 and m:
                repo_full = f"{m.group(1)}/{m.group(2)}"
        except Exception:
            pass
        conn.execute(
            "INSERT INTO dev_projects (kind,name,repo,local_path,dev_path,live_branch,"
            "work_branch,is_primary,engineers_enabled,merge_gate,autonomy) "
            "VALUES ('store','Store Command Center',?,?,?,'master','dev',1,0,1,'auto')",
            (repo_full, master_path, dev_path))
        conn.commit()


def create_world_tables(conn):
    conn.cursor().executescript("""
    -- ── The Company: gamified pixel-art world ────────────────────────────────
    -- Persistent, named characters. Each maps to a real job class or OpenClaw
    -- agent; identity/XP/level survive restarts (hybrid binding).
    CREATE TABLE IF NOT EXISTS world_agents (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        key         TEXT UNIQUE,                 -- stable id (openclaw agent id or worker slot)
        name        TEXT,                        -- display name (user-renameable)
        kind        TEXT DEFAULT 'worker',       -- openclaw | worker
        job_class   TEXT,                        -- image|video|audio|models3d|etsy|resell|portal|trends|agent
        dept        TEXT,                        -- department desk key
        color       TEXT,                        -- hex accent for the sprite
        sprite      TEXT,                         -- generated sprite PNG path (nullable)
        xp          INTEGER DEFAULT 0,
        level       INTEGER DEFAULT 1,
        coins       INTEGER DEFAULT 0,           -- spendable wallet (earned by REAL completed work)
        coins_earned INTEGER DEFAULT 0,          -- lifetime coins earned
        earn_mult   REAL DEFAULT 1.0,            -- earnings multiplier (raised by upgrades)
        upgrades    TEXT DEFAULT '[]',           -- JSON list of purchased upgrade ids
        debt        INTEGER DEFAULT 0,           -- unpaid rent/bills (drives 'broke' mood)
        -- Sims-style needs (0..100); decay over time, restored by activities/places
        energy      REAL DEFAULT 80,
        fun         REAL DEFAULT 70,
        social      REAL DEFAULT 70,
        fulfillment REAL DEFAULT 55,             -- sense of purpose; only real work refills it
        hunger      REAL DEFAULT 80,
        mood_emoji  TEXT DEFAULT '🙂',
        mood_label  TEXT DEFAULT 'settling in',
        goal        TEXT,                        -- current behavior goal (why they're moving)
        state       TEXT DEFAULT 'idle',         -- idle|working|leisure|sleep|commute
        location    TEXT DEFAULT 'home',         -- symbolic location key
        mood        TEXT,                        -- latest thought/want
        jobs_done   INTEGER DEFAULT 0,
        last_active TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS world_props (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT DEFAULT 'furniture',    -- building|furniture|decor
        label       TEXT,                        -- castle|chair|computer|table
        location    TEXT,                        -- symbolic location / zone
        x           REAL,
        y           REAL,
        image_path  TEXT,                        -- generated pixel PNG (nullable → placeholder)
        prompt      TEXT,
        status      TEXT DEFAULT 'placeholder',  -- placeholder|queued|generating|done
        owner_key   TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS world_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_key   TEXT,
        kind        TEXT,                        -- thought|want|levelup|job_start|job_done|system|bill|opinion|meeting|move
        text        TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    -- key/value store for sim bookkeeping (last tick, seen-work counters, priority…)
    CREATE TABLE IF NOT EXISTS world_meta (
        key         TEXT PRIMARY KEY,
        value       TEXT
    );
    -- every coin movement (wage/bonus/bill/purchase) for audit + per-agent logs
    CREATE TABLE IF NOT EXISTS world_ledger (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_key     TEXT,
        delta         INTEGER,
        reason        TEXT,
        balance_after INTEGER,
        created_at    TEXT DEFAULT (datetime('now'))
    );
    -- agents' opinions on how to improve the business (feed town meetings)
    CREATE TABLE IF NOT EXISTS world_suggestions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_key   TEXT,
        text        TEXT,
        category    TEXT,                        -- products|marketing|ops|pricing|quality|automation
        votes       INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'open',         -- open|chosen|shelved|done
        created_at  TEXT DEFAULT (datetime('now'))
    );
    -- town meetings: what the crew voted the top priority to fix/build next
    CREATE TABLE IF NOT EXISTS world_meetings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        topic       TEXT,
        decision    TEXT,
        tally       TEXT,                        -- JSON: [{suggestion,votes}]
        created_at  TEXT DEFAULT (datetime('now'))
    );
    -- the town's current actionable mandate (from the latest meeting/vote)
    CREATE TABLE IF NOT EXISTS world_directives (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        text        TEXT,
        source      TEXT DEFAULT 'meeting',      -- meeting|manual
        status      TEXT DEFAULT 'active',       -- active|done|dropped
        created_at  TEXT DEFAULT (datetime('now')),
        resolved_at TEXT
    );
    -- company milestones earned (data-driven registry in world_balance)
    CREATE TABLE IF NOT EXISTS world_achievements (
        id          TEXT PRIMARY KEY,
        label       TEXT,
        earned_at   TEXT DEFAULT (datetime('now'))
    );
    """)


def create_queue_history_table(conn):
    conn.cursor().executescript("""
    -- ── UNIFIED QUEUE completion history ─────────────────────────────────────
    -- The orchestrator's task dict is in-memory only, so finished LLM work used
    -- to vanish from the unified queue the moment it completed (and entirely on
    -- restart). Every LLM task now writes ONE row here at its terminal
    -- transition (done|error|cancelled) — see Orchestrator._record_history.
    -- Media jobs (image/video/audio/3D) already persist their lifecycle in
    -- generations/videos/video_chains/audio_clips, so they are NOT duplicated
    -- here; GET /api/queue/history unions them in at read time.
    CREATE TABLE IF NOT EXISTS queue_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT DEFAULT 'llm',   -- llm (media kinds union in at read time)
        label       TEXT,                 -- the task desc shown in the queue
        task        TEXT,                 -- prompt-registry task key (if any)
        source      TEXT,                 -- system that submitted it (world|proxy|studio|…)
        model       TEXT,
        status      TEXT,                 -- done | error | cancelled
        error       TEXT,                 -- truncated failure reason
        enqueued_at TEXT,
        started_at  TEXT,
        finished_at TEXT,
        duration_s  REAL
    );
    CREATE INDEX IF NOT EXISTS idx_qhist_finished ON queue_history(finished_at);
    """)


def create_bills_tables(conn):
    """REAL personal bills tracker (Finance → 📆 Bills pane; routers/money/bills.py).

    NOT the game world's in-game bills — those live in world_bills (world_bills.py).
    No credentials are stored here by design: portal_url + notes only.
    Called from routers/money/bills.py at import (same one-time-ensure pattern as
    the money package's _base._ensure_schema), so db.py needs no wiring."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS bills (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        category     TEXT DEFAULT '',      -- free-text user category ("utilities", …)
        portal_url   TEXT DEFAULT '',      -- the biller's payment/login page (link only — never passwords)
        portal_note  TEXT DEFAULT '',      -- account nickname / how-to-pay notes (keep secrets out)
        amount_cents INTEGER,              -- NULL = variable amount (prompted at mark-paid)
        cycle        TEXT DEFAULT 'monthly', -- monthly|weekly|yearly|quarterly|once|custom-N-days
        due_day      INTEGER,              -- day-of-month anchor (1-31) so a 31st bill survives Feb
        next_due     TEXT,                 -- YYYY-MM-DD of the next expected payment
        autopay      INTEGER DEFAULT 0,
        active       INTEGER DEFAULT 1,    -- 0 = deactivated (kept for history)
        extra        TEXT DEFAULT '{}',    -- JSON object of arbitrary user-defined fields
        created_at   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS bill_payments (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id      INTEGER NOT NULL,
        paid_at      TEXT DEFAULT (datetime('now')),  -- YYYY-MM-DD (or full datetime)
        amount_cents INTEGER DEFAULT 0,
        note         TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_bills_next_due ON bills(active, next_due);
    CREATE INDEX IF NOT EXISTS idx_bill_payments_bill ON bill_payments(bill_id, paid_at);
    """)
    conn.commit()


def create_mail_tables(conn):
    """📬 Mail & Quotes — config-driven mail desk (routers/mail.py + mail_engine.py +
    mail_gate.py).

    Four tables replace the old single-mailbox `mail_*` settings hardcode:

      mail_profiles   editable BUSINESS PROFILES (name, what the business does, the
                      non-negotiable reply/quote terms, a pricing-model JSON, tone,
                      signature). The reply drafter prompt (registry key `mail_quote`)
                      is a template these fields fill — NO business identity lives in
                      code anymore.
      mail_accounts   multiple mail accounts, each provider 'imap' (generic
                      IMAP/SMTP: host/port/security/verify-cert) or 'gmail'
                      (OAuth via IMAP/SMTP XOAUTH2). Credentials are Fernet-encrypted
                      IN THE ROW (crypto.enc, same at-rest scheme as secret settings).
                      Each account binds one business profile and opts in/out of the
                      auto-reply gate.
      mail_faq        the FAQ / Q&A knowledge base, per-profile (NULL = all profiles).
                      Incoming mail is matched locally (word overlap) + confirmed by
                      the classifier; a hit answers the mail from YOUR words.
      mail_log        the gate's REVIEW TRAIL: one row per processed message —
                      classification, matched FAQ, linked order context, the draft,
                      and what the gate did (drafted|held|sent|skipped) with the
                      reason. UNIQUE(account_id, uid) is the reprocess guard.

    SEED (idempotent, owner-preserving): when the legacy single-mailbox settings
    (mail_user + friends) exist and no accounts do, the old setup is migrated into
    a default profile + account so the existing workflow keeps working. A fresh
    install has no mail_user → seeds NOTHING → public forks start clean."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS mail_profiles (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,               -- business name (used in the From line + prompt)
        business_type TEXT DEFAULT '',             -- short label: carpentry | store | ...
        description   TEXT DEFAULT '',             -- what the business is/does (prompt context)
        terms         TEXT DEFAULT '',             -- non-negotiable reply/quote rules (one per line)
        pricing       TEXT DEFAULT '{}',           -- json: {hourly_rate,minimum_hours,materials_policy,tax_note,currency}
        tone          TEXT DEFAULT 'warm, concise, professional',
        signature     TEXT DEFAULT '',             -- how replies sign off
        is_default    INTEGER DEFAULT 0,           -- used when an account has no profile bound
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS mail_accounts (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        label          TEXT NOT NULL,              -- display label in the UI
        provider       TEXT DEFAULT 'imap',        -- imap | gmail
        email          TEXT DEFAULT '',            -- the mailbox address
        display_name   TEXT DEFAULT '',            -- From-header display name
        username       TEXT DEFAULT '',            -- login (blank = email)
        password_enc   TEXT DEFAULT '',            -- Fernet-encrypted (crypto.enc)
        imap_host      TEXT DEFAULT '',
        imap_port      INTEGER DEFAULT 993,
        imap_security  TEXT DEFAULT 'ssl',         -- ssl | starttls | plain
        smtp_host      TEXT DEFAULT '',
        smtp_port      INTEGER DEFAULT 587,
        smtp_security  TEXT DEFAULT 'starttls',    -- ssl | starttls | plain
        verify_cert    INTEGER DEFAULT 1,          -- 0 = self-signed OK (e.g. local Mailcow)
        gmail_access_token_enc  TEXT DEFAULT '',   -- gmail provider: OAuth tokens (encrypted)
        gmail_refresh_token_enc TEXT DEFAULT '',
        gmail_token_expires     INTEGER DEFAULT 0,
        signature      TEXT DEFAULT '',            -- per-account signature override ('' = profile's)
        profile_id     INTEGER,                    -- fk → mail_profiles.id (bound business profile)
        enabled        INTEGER DEFAULT 1,
        gate_enabled   INTEGER DEFAULT 1,          -- this account participates in the auto-reply gate
        last_error     TEXT DEFAULT '',
        created_at     TEXT DEFAULT (datetime('now')),
        updated_at     TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS mail_faq (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id  INTEGER,                       -- NULL = applies to every profile
        question    TEXT NOT NULL,
        answer      TEXT NOT NULL,
        enabled     INTEGER DEFAULT 1,
        hits        INTEGER DEFAULT 0,             -- times this FAQ answered a mail
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS mail_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id  INTEGER NOT NULL,
        uid         TEXT NOT NULL,                 -- IMAP UID within the account's INBOX
        message_id  TEXT DEFAULT '',
        from_email  TEXT DEFAULT '',
        from_name   TEXT DEFAULT '',
        subject     TEXT DEFAULT '',
        intent      TEXT DEFAULT '',               -- quote_request|order_support|faq|spam|other
        confidence  INTEGER,                       -- classifier confidence 0-100
        routine     INTEGER DEFAULT 0,             -- 1 = reply commits no money/price/promise
        faq_id      INTEGER,                       -- matched FAQ row
        order_ref   TEXT DEFAULT '',               -- json: linked designs/orders/proposals context
        draft       TEXT DEFAULT '',               -- the drafted reply body
        status      TEXT DEFAULT 'new',            -- new|drafted|held|sent|skipped|dismissed|error
        reason      TEXT DEFAULT '',               -- why held/skipped/errored
        sent_at     TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(account_id, uid)
    );
    CREATE INDEX IF NOT EXISTS idx_mail_log_status ON mail_log(status, id);
    CREATE INDEX IF NOT EXISTS idx_mail_faq_profile ON mail_faq(profile_id, enabled);
    """)
    conn.commit()
    seed_mail_defaults(conn)


def seed_mail_defaults(conn):
    """One-time migration of the legacy single-mailbox mail_* settings into the new
    profile + account tables, so the owner's existing workflow keeps working with
    zero re-setup. Fires ONLY when a legacy mailbox is actually configured
    (mail_user set) and nothing has been seeded/created yet — a fresh clone seeds
    nothing, so public installs start with a clean, unbranded mail desk. The legacy
    settings rows are left in place (integrations status + back-compat read them)."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='mail_seeded_v1'").fetchone()
        if row and row["value"] == "1":
            return
        have_accounts = conn.execute("SELECT COUNT(*) c FROM mail_accounts").fetchone()["c"]

        def _s(key, default=""):
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return (r["value"] if r and r["value"] not in (None, "") else default)

        mail_user = _s("mail_user")
        if mail_user and not have_accounts:
            # The owner's original carpentry terms — previously hardcoded in the
            # mail_quote prompt, now DATA in their default profile (fully editable).
            desc = ("a solo precision carpenter in your local area. "
                    "Labor-only handyman/carpentry work; the customer supplies materials.")
            terms = (
                "- Labor only: $40/hour, 4-hour minimum. The clock starts when the job starts.\n"
                "- NO fixed-price bids or contracts — only a simple labor agreement. Give an "
                "HOURLY estimate as a RANGE of hours (e.g. 'roughly 6-10 hours'), never a fixed "
                "total price promise.\n"
                "- The customer buys/provides all materials from a list the carpenter gives them; the carpenter does not supply materials.\n"
                "- No work, no charge. If unhappy, they can ask him to stop and pay only for hours worked.\n"
                "- The carpenter does NOT do: full re-siding, sheetrock finishing (tape/bed/paint — he hangs it), "
                "shingles (he dry-ins watertight), concrete beyond a fence post or two, custom "
                "cabinets from scratch, or windows past a one-person (~5'6\") reach. If the request "
                "is clearly outside this, say so kindly and suggest what he CAN do.")
            pricing = ('{"hourly_rate": 40, "minimum_hours": 4, '
                       '"materials_policy": "customer buys/provides all materials", '
                       '"tax_note": "", "currency": "USD"}')
            cur = conn.execute(
                "INSERT INTO mail_profiles (name,business_type,description,terms,pricing,tone,"
                "signature,is_default) VALUES (?,?,?,?,?,?,?,1)",
                ("Acme Carpentry", "carpentry", desc, terms, pricing,
                 "warm, concise, confident", "Wes — Acme Carpentry"))
            carpentry_id = cur.lastrowid
            # A second profile for the POD storefront's customer service, so buyer
            # questions (Etsy/Printify) draft with store context instead of quotes.
            store_name = _s("store_name", "The store")
            conn.execute(
                "INSERT INTO mail_profiles (name,business_type,description,terms,pricing,tone,signature) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"{store_name} customer service", "store",
                 "print-on-demand storefront (Etsy via Printify) customer service — order "
                 "status, shipping, sizing and product questions from buyers.",
                 "- Never promise refunds, replacements or exact delivery dates — offer to "
                 "look into it and follow up.\n"
                 "- Production and shipping are handled by the print provider; typical "
                 "production is a few business days.\n"
                 "- Be helpful and specific when order context is provided; otherwise ask "
                 "for the order number.",
                 "{}", "friendly, helpful, professional", store_name))
            # The Mailcow account itself (the encrypted mail_pass row is copied
            # VERBATIM — it is already Fernet-encrypted at rest).
            pass_enc = ""
            r = conn.execute("SELECT value FROM settings WHERE key='mail_pass'").fetchone()
            if r and r["value"]:
                pass_enc = r["value"]
            conn.execute(
                "INSERT INTO mail_accounts (label,provider,email,display_name,username,password_enc,"
                "imap_host,imap_port,imap_security,smtp_host,smtp_port,smtp_security,verify_cert,"
                "profile_id,enabled,gate_enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)",
                ("Support mailbox", "imap", mail_user, "Acme Carpentry", mail_user, pass_enc,
                 _s("mail_imap_host", "127.0.0.1"), int(_s("mail_imap_port", "993") or 993), "ssl",
                 _s("mail_smtp_host", "127.0.0.1"), int(_s("mail_smtp_port", "587") or 587), "starttls",
                 0, carpentry_id))
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('mail_seeded_v1','1')")
        conn.commit()
    except Exception:
        pass   # seeding must never block init_db; it re-tries next boot (flag unset on failure)


def run_migrations(conn):
    c = conn.cursor()
    # Migrations — add columns that might be missing in older DBs
    for migration in [
        "ALTER TABLE generations ADD COLUMN model TEXT DEFAULT 'sdxl_base_1.0.safetensors'",
        "ALTER TABLE designs ADD COLUMN printify_image_id TEXT",
        "ALTER TABLE videos ADD COLUMN model_id TEXT DEFAULT 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers'",
        "ALTER TABLE videos ADD COLUMN chain_id INTEGER",
        "ALTER TABLE videos ADD COLUMN chain_index INTEGER",
        "ALTER TABLE generations ADD COLUMN source TEXT DEFAULT 'pipeline'",
        "ALTER TABLE designs ADD COLUMN source TEXT DEFAULT 'pipeline'",
        # Resell migrations
        "ALTER TABLE resell_listings ADD COLUMN price_mode TEXT DEFAULT 'obo'",
        "ALTER TABLE resell_listings ADD COLUMN min_accept_price REAL",
        "ALTER TABLE resell_listings ADD COLUMN shipping_policy TEXT DEFAULT 'pickup_only'",
        "ALTER TABLE resell_listings ADD COLUMN will_ship_min_price REAL DEFAULT 50.0",
        "ALTER TABLE resell_listings ADD COLUMN payment_methods TEXT DEFAULT '[\"cash\"]'",
        "ALTER TABLE resell_listings ADD COLUMN image_path TEXT",
        # Resell v2 — seller context fields
        "ALTER TABLE resell_listings ADD COLUMN seller_description TEXT",
        "ALTER TABLE resell_listings ADD COLUMN why_selling TEXT",
        "ALTER TABLE resell_listings ADD COLUMN whats_included TEXT",
        "ALTER TABLE resell_listings ADD COLUMN known_defects TEXT",
        "ALTER TABLE resell_listings ADD COLUMN tags TEXT",
        # Network security: link findings to a specific domain (for ban action)
        "ALTER TABLE security_findings ADD COLUMN domain TEXT",
        # Video: store the failure reason so the UI isn't a black box
        "ALTER TABLE videos ADD COLUMN error TEXT",
        # Video: live progress (0-100) + a human phase message, for a real progress bar
        "ALTER TABLE videos ADD COLUMN progress INTEGER DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN progress_msg TEXT",
        # 3D models: preserve source folder structure as reference/review context
        "ALTER TABLE models3d ADD COLUMN rel_dir TEXT",     # path relative to backlog root
        "ALTER TABLE models3d ADD COLUMN category TEXT",    # top-level folder name
        # 3D generation: live progress message so the UI isn't a black box
        "ALTER TABLE models3d ADD COLUMN progress_msg TEXT",
        # 3D generation observability: which generator (triposr|triposg|sf3d|trellis)
        # produced this row, so triage doesn't have to guess
        "ALTER TABLE models3d ADD COLUMN generator TEXT",
        # Video→audio bridge: a muxed copy with music/voice + its own status
        "ALTER TABLE videos ADD COLUMN audio_path TEXT",
        "ALTER TABLE videos ADD COLUMN audio_status TEXT",
        "ALTER TABLE videos ADD COLUMN audio_error TEXT",
        # Audio clips: lyrics for ACE-Step (songs with vocals)
        "ALTER TABLE audio_clips ADD COLUMN lyrics TEXT",
        # Portal: link an affiliate product to a signup program (for tag auto-append)
        "ALTER TABLE portal_affiliate ADD COLUMN program_id INTEGER",
        # Portal programs: two-level model — network (join this) vs merchant (apply inside a network)
        "ALTER TABLE portal_programs ADD COLUMN ptype TEXT DEFAULT 'merchant'",  # network | merchant
        "ALTER TABLE portal_programs ADD COLUMN via TEXT",                       # hosting network(s) for merchants
        # The Company world economy: coin wallet + upgrade multiplier
        "ALTER TABLE world_agents ADD COLUMN coins INTEGER DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN coins_earned INTEGER DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN earn_mult REAL DEFAULT 1.0",
        "ALTER TABLE world_agents ADD COLUMN upgrades TEXT DEFAULT '[]'",
        # The Company simulation: Sims-style needs + mood + bills
        "ALTER TABLE world_agents ADD COLUMN debt INTEGER DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN energy REAL DEFAULT 80",
        "ALTER TABLE world_agents ADD COLUMN fun REAL DEFAULT 70",
        "ALTER TABLE world_agents ADD COLUMN social REAL DEFAULT 70",
        "ALTER TABLE world_agents ADD COLUMN fulfillment REAL DEFAULT 55",
        "ALTER TABLE world_agents ADD COLUMN hunger REAL DEFAULT 80",
        "ALTER TABLE world_agents ADD COLUMN mood_emoji TEXT DEFAULT '🙂'",
        "ALTER TABLE world_agents ADD COLUMN mood_label TEXT DEFAULT 'settling in'",
        "ALTER TABLE world_agents ADD COLUMN goal TEXT",
        # The Company state-machine v2: dwell hysteresis (stop the jitter) + idle sub-state + raid role
        "ALTER TABLE world_agents ADD COLUMN dwell_until REAL DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN substate TEXT",
        "ALTER TABLE world_agents ADD COLUMN role TEXT",
        # RimWorld-style mood: mental-break state (breakdown timer + kind)
        "ALTER TABLE world_agents ADD COLUMN break_until REAL DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN break_kind TEXT",
        # combat depth (#8): raid HP + downed/rescue state
        "ALTER TABLE world_agents ADD COLUMN raid_hp REAL DEFAULT 100",
        "ALTER TABLE world_agents ADD COLUMN downed INTEGER DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN downed_at REAL DEFAULT 0",
        # flux system: monotony streaks (penalise grinding one activity) + god's blessing buff
        "ALTER TABLE world_agents ADD COLUMN streak_state TEXT",
        # self-generated appearance: custom pixel sprite (agents earn a makeover)
        "ALTER TABLE world_agents ADD COLUMN sprite_path TEXT",
        "ALTER TABLE world_agents ADD COLUMN streak_since REAL DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN blessed_until REAL DEFAULT 0",
        # play-god pick-up/drop: post an agent to a spot/task (RCT-style)
        "ALTER TABLE world_agents ADD COLUMN posted_to TEXT",
        "ALTER TABLE world_agents ADD COLUMN posted_kind TEXT",
        "ALTER TABLE world_agents ADD COLUMN posted_until REAL DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN posted_col INTEGER DEFAULT 0",
        "ALTER TABLE world_agents ADD COLUMN posted_row INTEGER DEFAULT 0",
        # Oracle personas: the world body's display `name` is a persona (Delphi,
        # Pythia, …) — model_id keeps the real LM Studio model id for the detail panel.
        "ALTER TABLE world_agents ADD COLUMN model_id TEXT",
        # The Company world-builder's eyes: vision score + notes on each prop
        "ALTER TABLE world_props ADD COLUMN score INTEGER",
        "ALTER TABLE world_props ADD COLUMN verdict TEXT",
        # god's like/reject on a world creation (+1 like / -1 reject / null unrated) → taste
        "ALTER TABLE world_props ADD COLUMN user_verdict INTEGER",
        # NSFW ("Private Studio") mode: nsfw jobs are NORMAL rows flagged nsfw=1 —
        # same pipelines, but regular listings exclude them and only the gated
        # /api/nsfw/library returns them (see app/nsfw.py for the toggle model).
        "ALTER TABLE generations ADD COLUMN nsfw INTEGER DEFAULT 0",
        "ALTER TABLE generations ADD COLUMN nsfw_category TEXT",   # Private-Studio category name
        "ALTER TABLE generations ADD COLUMN nsfw_agent TEXT",      # world agent key when a Company agent made it
        "ALTER TABLE designs ADD COLUMN nsfw INTEGER DEFAULT 0",
        "ALTER TABLE designs ADD COLUMN nsfw_category TEXT",
        "ALTER TABLE videos ADD COLUMN nsfw INTEGER DEFAULT 0",
        "ALTER TABLE video_chains ADD COLUMN nsfw INTEGER DEFAULT 0",
        "ALTER TABLE audio_clips ADD COLUMN nsfw INTEGER DEFAULT 0",
        "ALTER TABLE models3d ADD COLUMN nsfw INTEGER DEFAULT 0",
        # Income Phase 1: generalize `paychecks` into any-kind income, additively.
        # Existing rows need no backfill — the defaults make them paycheck/manual.
        # Phase 1 is manual entry ONLY: external_source is always 'manual' here;
        # a later phase (PayPal/on-chain/Printify importers) will write real values.
        "ALTER TABLE paychecks ADD COLUMN income_type TEXT DEFAULT 'paycheck'",  # paycheck|sale|refund|gift|dividend|interest|crypto_receive|payout|other
        "ALTER TABLE paychecks ADD COLUMN currency TEXT DEFAULT 'USD'",
        "ALTER TABLE paychecks ADD COLUMN amount_native REAL",       # coin/share amount when currency != USD; amount_cents stays USD-at-receipt
        "ALTER TABLE paychecks ADD COLUMN external_source TEXT DEFAULT 'manual'",  # manual|paypal|square|printify|btc|…
        "ALTER TABLE paychecks ADD COLUMN external_txn_id TEXT",     # NULL for manual entries
        "ALTER TABLE paychecks ADD COLUMN voided INTEGER DEFAULT 0",
        # Dedup guard for future importers: one row per (source, external id). Must
        # run AFTER the two ALTERs above add their columns — see the ordered list.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_income_ext ON paychecks(external_source, external_txn_id) "
        "WHERE external_txn_id IS NOT NULL",
        # Social publishing Phase 0: link a post to its source video (single video OR
        # compiled chain), carry a thumbnail + per-platform metadata overrides, and
        # leave room for taste-scoring / prayer-linked automation later.
        "ALTER TABLE social_posts ADD COLUMN video_id INTEGER",       # fk → videos.id
        "ALTER TABLE social_posts ADD COLUMN chain_id INTEGER",       # fk → video_chains.id
        "ALTER TABLE social_posts ADD COLUMN thumb_path TEXT",        # local thumbnail file
        "ALTER TABLE social_posts ADD COLUMN per_platform TEXT",      # json: {youtube:{title,description,tags,privacy},…}
        "ALTER TABLE social_posts ADD COLUMN taste REAL",             # taste score (auto-curation later)
        "ALTER TABLE social_posts ADD COLUMN prayer_id INTEGER",      # optional prayer/automation linkage
        # Social Phase 3a — real auto-scheduler (app/social_scheduler.py): the loop
        # CLAIMS a due post by stamping auto_state before uploading anything, and a
        # claimed post is never picked again → structurally cannot double-post.
        # NULL=untouched | publishing | done | dry_run | failed:<why>
        "ALTER TABLE social_posts ADD COLUMN auto_state TEXT",
        # Social Phase 3b — post analytics → taste model: platform performance stats
        # fetched after publish (YouTube videos.list statistics; TikTok best-effort)
        "ALTER TABLE social_publications ADD COLUMN stats TEXT",      # json: {available,views,likes,comments,shares,…}
        "ALTER TABLE social_publications ADD COLUMN stats_at TEXT",   # last analytics refresh (UTC)
        # AI Video Studio ("Director"): trace chains/clips back to their studio objects
        "ALTER TABLE video_chains ADD COLUMN studio_scene_id INTEGER",   # fk → studio_scenes.id
        "ALTER TABLE audio_clips  ADD COLUMN studio_cue_id INTEGER",     # fk → studio_cues.id
        # Director scenes need per-shot lengths inside one chain: JSON list of
        # per-segment num_frames; NULL = uniform num_frames (fully backward-compatible)
        "ALTER TABLE video_chains ADD COLUMN frames_json TEXT",
        # Director Phase 2: the simple mix is ONE music bed + ONE whole-script
        # voiceover clip per project (per-scene VO timeline = Phase 3), so the
        # generated audio_clips ids live on the project row.
        "ALTER TABLE studio_projects ADD COLUMN music_clip_id INTEGER",  # fk → audio_clips.id
        "ALTER TABLE studio_projects ADD COLUMN vo_clip_id INTEGER",     # fk → audio_clips.id
        # Director Phase 2: owner-uploaded footage used as a shot's source clip
        # (Phase 2 supports uploads as the LEADING shots of a scene; the chain
        # then V2V-continues from the last uploaded clip)
        "ALTER TABLE studio_shots ADD COLUMN source_path TEXT",
        # Chain layered audio (owner bug A): opt-in music/narration/SFX generated
        # for a compiled video chain and muxed on (services_media_chain.
        # render_chain_audio). Default OFF — plain chains behave exactly as before.
        "ALTER TABLE video_chains ADD COLUMN audio_enabled INTEGER DEFAULT 0",
        "ALTER TABLE video_chains ADD COLUMN audio_settings TEXT",  # json: layers/volumes/engines/narration
        "ALTER TABLE video_chains ADD COLUMN audio_status TEXT",    # NULL|queued|generating|done|failed
        "ALTER TABLE video_chains ADD COLUMN audio_error TEXT",
        "ALTER TABLE video_chains ADD COLUMN final_path TEXT",      # compiled video WITH the mixed audio
        "ALTER TABLE audio_clips ADD COLUMN chain_id INTEGER",      # fk → video_chains.id (keeps chain layers out of the Audio gallery)
        # Proposal review gate: an LLM judge rates each pending proposal so the
        # good ones surface and the weeds get gated out (see proposal_gate.py).
        # Existing rows keep NULLs = "not yet reviewed"; no backfill needed.
        "ALTER TABLE proposals ADD COLUMN score INTEGER",        # 0-100 judge score
        "ALTER TABLE proposals ADD COLUMN score_reason TEXT",    # judge's one-liner
        "ALTER TABLE proposals ADD COLUMN verdict TEXT",         # reject | hold | approve
        "ALTER TABLE proposals ADD COLUMN reviewed_at TEXT",     # UTC, when judged
        # Proposal desk lanes: which themed generator originated the proposal
        # (humor|news|tech|gaming|evergreen|market — trends.LANES; 'agent'
        # suggestions get their lane from the suggesting agent's dept).
        # Existing rows keep NULL = pre-lane era; nothing breaks.
        "ALTER TABLE proposals ADD COLUMN lane TEXT",
    ]:
        try:
            c.execute(migration)
            conn.commit()
        except Exception:
            pass   # column already exists


def create_ledger_tables(conn):
    """💵 personal money ledger — paychecks IN + purchases OUT (routers/money/ledger.py).

    The sibling of create_bills_tables(). Bills own recurring obligations and their
    payment history (bill_payments); this owns income and NON-bill spending.
    `purchases` deliberately has NO bill_id: bill payments already live in
    bill_payments, so a bill entered here too would double-count in every total.
    Outgoings = purchases + bill_payments, two disjoint sets.

    Called from routers/money/ledger.py at import (same one-time-ensure pattern as
    create_bills_tables), so db.py needs no wiring.

    Income Phase 1 (generalizing `paychecks` into any-kind income, additively) put
    its new columns (income_type/currency/amount_native/external_source/
    external_txn_id/voided) directly on the CREATE TABLE below — not only on the
    run_migrations() ALTERs — because this function's CREATE TABLE IF NOT EXISTS
    runs on router import, which for a brand-new DB can happen BEFORE db.init_db()
    (and its run_migrations() pass) ever runs (e.g. the test suite builds the DB
    via db.init_db() before `import main` pulls routers/ledger.py in). The
    run_migrations() ALTERs remain the path that backfills these columns onto an
    EXISTING paychecks table from before this change; both are idempotent so
    running both (fresh installs will) is harmless.

    NOT the game world's economy (world_ledger / world_ops_ledger) — unrelated."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS paychecks (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        source            TEXT NOT NULL,        -- employer or client name
        amount_cents      INTEGER NOT NULL,     -- net (take-home); may be derived from hours × rate
        gross_cents       INTEGER,              -- before withholding, optional
        received_at       TEXT,                 -- YYYY-MM-DD the money landed
        hours             REAL,                 -- optional, for hourly/contract work
        hourly_rate_cents INTEGER,              -- per-entry rate — never a hardcoded constant
        cycle             TEXT DEFAULT 'irregular', -- weekly|biweekly|semimonthly|monthly|irregular
        notes             TEXT DEFAULT '',
        extra             TEXT DEFAULT '{}',    -- JSON object of arbitrary user-defined fields
        created_at        TEXT DEFAULT (datetime('now')),
        -- ── Income Phase 1: generalized "paycheck" -> any-kind income ──────────
        income_type       TEXT DEFAULT 'paycheck', -- paycheck|sale|refund|gift|dividend|interest|crypto_receive|payout|other
        currency          TEXT DEFAULT 'USD',      -- display currency; amount_cents is always USD-at-receipt
        amount_native     REAL,                    -- coin/share amount when currency != USD
        external_source   TEXT DEFAULT 'manual',   -- manual|paypal|square|printify|btc|… (Phase 1 only ever writes 'manual')
        external_txn_id   TEXT,                    -- NULL for manual entries; dedup key for future importers
        voided            INTEGER DEFAULT 0        -- 1 = excluded from every summary/series total
    );
    CREATE TABLE IF NOT EXISTS purchases (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        purchased_at  TEXT,                     -- YYYY-MM-DD
        merchant      TEXT NOT NULL,
        amount_cents  INTEGER NOT NULL,
        category      TEXT DEFAULT '',          -- free text; shares the bills category vocabulary
        method        TEXT DEFAULT '',          -- card / cash / transfer / …
        notes         TEXT DEFAULT '',
        extra         TEXT DEFAULT '{}',        -- JSON object of arbitrary user-defined fields
        created_at    TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_paychecks_received ON paychecks(received_at);
    CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchased_at);
    CREATE INDEX IF NOT EXISTS idx_purchases_category ON purchases(category, purchased_at);
    -- ── Income Phase 2: per-source READ-ONLY importer state (app/income_import.py).
    --    One row per source (paypal|printify|onchain). `running` is the
    --    anti-double-run guard (same pattern as world_auto's persisted timers);
    --    it is reset to 0 on every daemon start so a crash can never wedge a
    --    source. Money rows land in `paychecks` and dedupe on the existing
    --    idx_income_ext unique index (external_source, external_txn_id).
    CREATE TABLE IF NOT EXISTS income_import_state (
        source       TEXT PRIMARY KEY,       -- paypal | printify | onchain
        running      INTEGER DEFAULT 0,      -- 1 while an import for this source is in flight
        last_run_at  TEXT,                   -- when an import last finished (any outcome)
        last_ok_at   TEXT,                   -- when it last finished without error
        last_added   INTEGER DEFAULT 0,      -- rows inserted on the last run
        last_seen    INTEGER DEFAULT 0,      -- candidate records the last run examined
        last_error   TEXT DEFAULT '',        -- '' = last run was clean
        cursor       TEXT DEFAULT ''         -- per-source resume marker (free-form)
    );
    """)
    conn.commit()
    # Best-effort here (own try/except, NOT part of the script above): on a fresh
    # DB the columns just got created above, so this succeeds immediately. On an
    # EXISTING pre-Income-Phase-1 DB the columns don't exist yet, this fails, and
    # run_migrations() picks it up later (same ordered-list pattern, after its
    # ALTERs add the columns) — see run_migrations() for why this can't be
    # unconditional in the script above without risking that upgrade path.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_income_ext ON paychecks(external_source, external_txn_id) "
            "WHERE external_txn_id IS NOT NULL")
        conn.commit()
    except Exception:
        pass


def create_game_listing_tables(conn):
    """🎮 Games → shop listing drafts (routers/games_publish.py).

    ONE row per shop listing the owner is hand-building for a game project. This is
    deliberately a private, opt-in table: a project only appears here because the
    owner opened the publish editor on it. Nothing scans projects into this table,
    and nothing in the app lists these rows publicly.

    `wp_id` is set only after an explicit push, and the pushed product is always a
    WooCommerce DRAFT — re-pushing updates that same id rather than duplicating.
    Images live as files under DATA_DIR/game_listings/<id>/; the `images` JSON array
    records {file, kind, label, wp_url?} for each one.

    Called from routers/games_publish.py at import (same one-time-ensure pattern as
    create_bills_tables / create_ledger_tables), so db.py needs no wiring."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS game_listings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_path  TEXT NOT NULL,          -- where the project lives on the node (NEVER pushed)
        project_name  TEXT DEFAULT '',
        engine        TEXT DEFAULT '',        -- godot | unity | unreal
        title         TEXT NOT NULL,
        slug          TEXT DEFAULT '',
        price_cents   INTEGER DEFAULT 0,      -- money is cents everywhere in this app
        short_desc    TEXT DEFAULT '',
        long_desc     TEXT DEFAULT '',
        category      TEXT DEFAULT 'Games',
        tags          TEXT DEFAULT '',        -- comma separated
        external_url  TEXT DEFAULT '',        -- optional link/download target
        button_text   TEXT DEFAULT 'Get the game',
        images        TEXT DEFAULT '[]',      -- JSON array of image records
        status        TEXT DEFAULT 'draft',   -- draft (local only) | pushed
        wp_id         INTEGER,                -- WooCommerce product id, once pushed
        wp_link       TEXT DEFAULT '',
        wp_admin_url  TEXT DEFAULT '',
        wp_status     TEXT DEFAULT '',        -- what Woo reported back; always 'draft' from here
        pushed_at     TEXT,
        created_at    TEXT DEFAULT (datetime('now')),
        updated_at    TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_game_listings_project ON game_listings(project_path);
    """)
    conn.commit()


def create_budget_tables(conn):
    """🧮 Budget + grocery planner — item-level purchases, envelopes, AI plan drafts.

    The three tables the budget engine adds on top of the existing money schema
    (bills / bill_payments / paychecks / purchases). Nothing here duplicates an
    amount that already lives elsewhere:

      purchase_items    the line items OF an existing `purchases` row. The parent
                        purchase keeps owning the authoritative amount_cents (a
                        receipt total includes tax/fees the lines do not), so
                        totals and budgets are ALWAYS summed from `purchases`,
                        never from these lines. Lines exist to answer "what did I
                        buy, how much of it, at what unit price, how often" —
                        they are detail, not a second copy of the money.
                        `normalized_name` is the match key across trips
                        ("Milk 1 gal" and "milk gallon" both normalize to "milk");
                        `name` keeps the raw text exactly as typed, for display.

      budget_envelopes  one row per category the owner budgets (food, gas,
                        savings, other, …). `kind` is 'fixed' (amount_cents) or
                        'percent' (percent of the period's income basis). The
                        allocation is COMPUTED per period, never stored — a
                        stored allocation would go stale the moment income moves.

      budget_plans      AI grocery-list drafts. ADVISORY ONLY: a plan is never
                        applied to a budget and never becomes a purchase on its
                        own. It sits at status 'draft' until the owner accepts or
                        rejects it, and turning an accepted plan into a real
                        purchase is a separate, explicit owner action.
                        `items` / `observations` / `llm_notes` are JSON.

    The pay cycle and the feature toggles live in `settings`
    (budget_pay_cycle / budget_period_anchor / budget_planner_enabled /
    budget_calendar_predictions), not here — they are single scalars.

    Called from routers/money/budget.py at import (same one-time-ensure pattern as
    create_bills_tables / create_ledger_tables), so db.py needs no wiring."""
    conn.cursor().executescript("""
    CREATE TABLE IF NOT EXISTS purchase_items (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_id      INTEGER NOT NULL,     -- FK -> purchases.id (lines die with the purchase)
        name             TEXT NOT NULL,        -- raw, as typed — shown to the owner verbatim
        normalized_name  TEXT NOT NULL,        -- match key across trips (see normalize_item_name)
        qty              REAL DEFAULT 1,       -- how many units this line covers
        unit             TEXT DEFAULT '',      -- gal / lb / ct / pack / … free text
        unit_price_cents INTEGER,              -- price of ONE unit; NULL when not known
        line_total_cents INTEGER NOT NULL,     -- what this line cost (qty × unit price, or as entered)
        category         TEXT DEFAULT '',      -- falls back to the parent purchase's category
        created_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS budget_envelopes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        category     TEXT NOT NULL UNIQUE,     -- matches purchases.category (case-insensitive)
        kind         TEXT DEFAULT 'fixed',     -- fixed | percent
        amount_cents INTEGER DEFAULT 0,        -- used when kind='fixed'
        percent      REAL DEFAULT 0,           -- used when kind='percent' (of income basis)
        active       INTEGER DEFAULT 1,
        sort         INTEGER DEFAULT 0,
        notes        TEXT DEFAULT '',
        created_at   TEXT DEFAULT (datetime('now')),
        updated_at   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS budget_plans (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        period_start   TEXT,                   -- YYYY-MM-DD the plan was drawn for
        period_end     TEXT,
        envelope_cents INTEGER,                -- food envelope REMAINING at generation time
        est_total_cents INTEGER DEFAULT 0,     -- sum of the validated lines, priced from OUR history
        status         TEXT DEFAULT 'generating', -- generating | draft | accepted | rejected | failed
        items          TEXT DEFAULT '[]',      -- JSON: validated grocery lines
        rejected_items TEXT DEFAULT '[]',      -- JSON: lines the validator dropped, with the reason
        observations   TEXT DEFAULT '[]',      -- JSON: OUR computed facts (not the model's)
        llm_notes      TEXT DEFAULT '[]',      -- JSON: the model's advisory notes, labelled as such
        error          TEXT DEFAULT '',
        task_id        INTEGER,                -- orchestrator task that produced it
        purchase_id    INTEGER,                -- set only if the owner turned it into a real purchase
        created_at     TEXT DEFAULT (datetime('now')),
        updated_at     TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_purchase_items_purchase ON purchase_items(purchase_id);
    CREATE INDEX IF NOT EXISTS idx_purchase_items_norm ON purchase_items(normalized_name);
    CREATE INDEX IF NOT EXISTS idx_budget_plans_status ON budget_plans(status, id);
    """)
    conn.commit()

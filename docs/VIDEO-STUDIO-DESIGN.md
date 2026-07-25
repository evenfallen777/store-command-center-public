# AI Video Studio ("Director") — Design Blueprint

**Status: DESIGN ONLY — nothing here is implemented yet.**

A storyboard-driven studio that turns a dropped idea/prompt/meme into a finished
video with matched, layered audio, then hands it to the existing Social/publish
pipeline. This document is the implementable blueprint: real DDL, real endpoint
signatures, real ffmpeg sketches, real JSON schemas.

Naming note: the frontend already has a **Studio hub** (`static/js/app-studio.js`
— Image / Video / Audio / 3D / Queue sub-tabs). The new feature is a new
first-class sub-tab of that hub called **🎞️ Director** (view name `director`,
file `static/js/tab-director.js`). The backend namespace `/api/studio/*` is
unused today and is claimed by this feature; DB tables are prefixed `studio_`.

---

## 0. What is reused (nothing parallel gets invented)

| Need | Existing thing reused | Where |
|---|---|---|
| Shot → clip | `videos` rows + `run_chain_generation` (T2V seg 0, V2V continuation after) | `app/services_media_chain.py` |
| Shots → scene (stitched clip) | `video_chains` + `_compile_chain_video` (xfade, concat fallback) | `app/services_media_chain.py` |
| Scenes → full video | `_compile_chain_video` again, over scene clips (it is just "list of mp4s → one mp4") | same |
| Voiceover (TTS) | `mms_tts` engine via `audio_clips` + `run_audio_clip` / `_node_audio("voice", …)` | `app/services_media_audio.py` |
| Music bed | `musicgen` / `musicgen_med` / `stable_audio` / `acestep` via same path | same |
| SFX | short (2–4 s) `musicgen`/`stable_audio` clips via same path | same |
| GPU discipline | `orch.submit_llm`, `orch.video_acquire`, `orch.video_exclusive`, `_VIDEO_RUN_LOCK` | `app/orchestrator.py`, `app/services_media.py` |
| LLM | `_call_lmstudio` inside `orch.submit_llm` workers; prompt-registry (`app/prompts.py`) | `app/llm_client.py` |
| NSFW gating | `nsfw.enabled()/visible()/screen()` safety floor; `nsfw` column convention; nsfw-model routing in `submit_llm` | `app/nsfw.py`, `app/orchestrator.py` |
| Export | `social_posts` (`video_id`/`chain_id`/`media_path`/`per_platform`) + `/api/social/posts/{id}/publish` | `app/routers/social.py` |
| Mux/mix | ffmpeg on the store host (CPU only, GPU already released) — same pattern as `_mux_audio` | `app/services_media_audio.py` |
| Frontend | vanilla per-tab JS, `api()`, `esc()`, `toast()`, `viewRoot()`, poll-with-setTimeout galleries | `static/js/tab-videos.js`, `tab-audio.js` |

---

## 1. Data model

### 1.1 Object model

```
studio_projects                (the dropped idea → one finished video)
 └─ studio_scenes   (ordered)  (each scene = one video_chains row when rendered)
     └─ studio_shots (ordered) (each shot = one prompts[] entry of the scene's
                                chain = one `videos` segment row when rendered)
 └─ studio_cues     (audio timeline: voiceover / music / sfx; each generated
                     cue = one `audio_clips` row)
```

- A **scene renders as exactly one `video_chains` row** whose `prompts` JSON is
  the ordered list of its shots' `video_prompt`s. `run_chain_generation` then
  does everything we already trust: T2V for shot 0, V2V continuation for the
  rest, per-segment `videos` rows, auto-xfade-compile into one scene clip
  (`video_chains.compiled_path`). `studio_scenes.chain_id` is the FK.
- A **shot links to its rendered segment** after the fact via
  `studio_shots.video_id → videos.id` (the row `run_chain_generation` inserted
  with `chain_id`+`chain_index`); resolved by `(chain_id, chain_index=idx)`.
- An **audio cue links to its generated clip** via
  `studio_cues.clip_id → audio_clips.id`, so generation status/progress/error
  reuse the `audio_clips` lifecycle and even show up in the existing Audio
  gallery machinery.
- The **full video** (scene clips stitched, still silent) and the **final mixed
  video** live on the project row (`video_path`, `final_path`).

### 1.2 New tables (add `create_studio_tables(conn)` to `app/db_schema.py`, called from `db.init_db()` right after `create_media_tables`)

```sql
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
```

### 1.3 Migrations on existing tables (append to `run_migrations()` list, same try/pass style)

```python
# AI Video Studio ("Director"): trace chain/segments/clips back to their studio objects
"ALTER TABLE video_chains ADD COLUMN studio_scene_id INTEGER",   # fk → studio_scenes.id
"ALTER TABLE audio_clips  ADD COLUMN studio_cue_id INTEGER",     # fk → studio_cues.id
```

Notes:
- `videos` needs **no new column**: segments are found via `(chain_id, chain_index)`.
- Studio-owned chains/clips are **hidden from the normal galleries** by adding
  `AND studio_scene_id IS NULL` / `AND studio_cue_id IS NULL` to
  `GET /api/video-chains` and `GET /api/audio` list queries (one-line changes),
  so scene renders don't spam the Video tab. The Director tab lists them itself.
- One required behavior fix in `run_chain_generation`: the per-segment
  `INSERT INTO videos (...)` must copy the chain's `nsfw` flag
  (`SELECT nsfw FROM video_chains` is already in hand as `chain["nsfw"]`) —
  today an NSFW chain's segments land with `nsfw=0` and would leak into
  `GET /api/videos`. This fix benefits the existing NSFW chain path too.

---

## 2. AI storyboarding flow

### 2.1 Flow

```
POST /api/studio/projects {idea, kind, style, target_seconds, nsfw?}
  → INSERT studio_projects (status='storyboarding')
  → tid = orch.submit_llm(_storyboard_work, desc=f"Storyboard: {idea[:40]}",
                          priority=0, task="studio_storyboard")
  → UPDATE studio_projects SET storyboard_task=tid
  → return {id, task_id}                      (UI polls /api/studio/projects/{id})

_storyboard_work():
  raw  = _call_lmstudio(get_prompt('studio_storyboard'), user_msg, max_tokens=4000)
  json = first {...} block (re.search(r'\{.*\}', raw, re.DOTALL)), json.loads
  validate + clamp (see 2.3) → write scenes/shots/cues rows, status='draft'
  on any parse/validation failure → status='failed', error=tail of raw
```

`user_msg` template:

```
Idea: {idea}
Video kind: {kind}                 # short → exactly 1 scene; long → 2-6 scenes
Target length: ~{target_seconds} seconds total
Style/mood: {style or 'your choice — match the idea'}
Constraints: each shot is 1.5-7.5 seconds; a scene has 1-5 shots;
allowed shot lengths (seconds): 1.5, 3, 5, 7.5
```

### 2.2 The exact JSON schema the LLM must return

```json
{
  "title": "string — 3-8 word working title",
  "logline": "string — one sentence: what this video is",
  "style": "string — the visual style thread reused in every shot prompt",
  "scenes": [
    {
      "title": "string",
      "summary": "string — what happens, 1-2 sentences",
      "voiceover": "string — the words spoken over THIS scene (may be empty '')",
      "caption": "string — short on-screen text for this scene (may be '')",
      "shots": [
        {
          "video_prompt": "string — full self-contained text-to-video prompt: subject, action, camera, lighting, style (repeat the style thread; the model has NO memory between shots)",
          "seconds": 3,
          "caption": ""
        }
      ],
      "sfx": [
        { "prompt": "string — the sound itself, e.g. 'single deep whoosh, cinematic transition sound, no music'",
          "at_shot": 0,
          "offset_s": 0.0 }
      ]
    }
  ],
  "music": {
    "prompt": "string — MusicGen-style description: genre, mood, instruments, tempo",
    "mood": "string — 2-3 words"
  },
  "caption": "string — the social post caption for publishing",
  "hashtags": "string — '#a #b #c'"
}
```

### 2.3 Server-side validation/clamping (never trust the model)

- `scenes`: SHORT → force length 1 (take first); LONG → clamp 2..6.
- `shots` per scene: clamp 1..5; `seconds` snapped to the nearest of
  `{1.5, 3, 5, 7.5}` → `num_frames ∈ {25, 49, 81, 121}` at fps 16
  (`num_frames = snap(seconds) * fps + 1`, same table `tab-videos.js` uses).
- Every `video_prompt` < 20 chars → prepend scene summary + style.
- Missing `music.prompt` → fall back to `f"{style} background music, instrumental"`.
- Rows written per scene: one `studio_scenes`; per shot one `studio_shots`;
  one `voiceover` cue per scene with non-empty `voiceover` (engine=`voice_engine`);
  one `music` cue (kind=music, scene_id NULL, `gain=0.25`, duration set at
  mix-time); one `sfx` cue per sfx entry (duration_s default 3, `gain=0.9`,
  `offset_s = sum(seconds of shots < at_shot) + offset_s`).
- `project.script` = scenes' voiceovers joined with blank lines;
  `project.captions` = `caption + "\n" + hashtags`;
  `project.audio_plan` = the raw `music` object + engines (JSON) for display.

### 2.4 Prompt registry entry (`app/prompts.py`, Studio category)

```python
PromptDef("studio_storyboard", "Director: idea → storyboard JSON", "Studio",
          inline="""You are a video director for short AI-generated videos.
Turn the user's idea into a complete storyboard as ONE JSON object and NOTHING else
(no markdown, no code fences, no commentary).
Schema: {"title","logline","style","scenes":[{"title","summary","voiceover","caption",
"shots":[{"video_prompt","seconds","caption"}],"sfx":[{"prompt","at_shot","offset_s"}]}],
"music":{"prompt","mood"},"caption","hashtags"}
Rules:
- Respect the scene count and target length in the request.
- Each shot's video_prompt must be SELF-CONTAINED (the video model has no memory):
  restate subject + the style thread every time. 30-60 words. Concrete visuals,
  camera movement, lighting. No text overlays in the prompt (captions are separate).
- Consecutive shots in a scene continue one motion/moment (they are generated as a
  continuation chain); scene boundaries may cut to a new setting.
- voiceover: natural spoken words, ~2.5 words per second of scene length, may be "".
- music.prompt: genre, mood, instruments, tempo — instrumental, no artist names.
- sfx: at most 2 per scene, each a short single sound description ending in ", no music".
- caption/hashtags: a catchy social caption and 4-8 hashtags.""",
          help="The Director sub-tab storyboarder: turns a dropped idea into the "
               "scenes/shots/script/captions/audio-plan JSON. Editable here; a "
               "specific model can be pinned with the per-prompt picker."),
PromptDef("studio_scene_regen", "Director: rewrite one scene", "Studio",
          inline="""You are revising ONE scene of an existing storyboard. You get the
project idea, style thread, the current scene JSON, and the owner's notes. Return ONLY
the revised scene as one JSON object with the same schema
{"title","summary","voiceover","caption","shots":[...],"sfx":[...]} — nothing else.
Keep shot count/lengths unless the notes ask otherwise; keep the style thread.""",
          help="Regenerate/steer a single scene from the Director editor."),
```

### 2.5 NSFW / uncensored variant (gated)

- Project rows carry `nsfw` (0/1). Creating an NSFW project requires
  `nsfw.enabled()` (else 404, same convention as `/api/nsfw/*`); listing NSFW
  projects requires `nsfw.visible()` — `GET /api/studio/projects` filters
  `COALESCE(nsfw,0)=0` unless visible.
- **Model routing**: add `"studio_storyboard"` and `"studio_scene_regen"` to the
  task tuple in `Orchestrator.submit_llm` that reroutes to
  `model_registry.resolve("nsfw_model")` when `nsfw.enabled()` — the same
  mechanism `image_enhance`/`video_chain` already use. `_call_lmstudio` already
  appends the `_NSFW_PERMIT` system suffix when the global toggle is on.
- **Safety floor is unconditional**: run `nsfw.screen()` on (a) the dropped idea
  before the LLM call and (b) every model-authored `video_prompt`/`voiceover`
  before rows are saved — minors / real-person likeness / non-consent refusals
  hard-fail the project regardless of any toggle. This mirrors how the Private
  Studio screens both inputs and model output today.
- **Propagation**: `project.nsfw` → `video_chains.nsfw` on every scene chain →
  (with the §1.3 fix) every segment `videos` row → `audio_clips.nsfw` on every
  cue clip → export blocked to Social unless the owner explicitly confirms
  (Social has no NSFW lane; the export button is hidden for nsfw projects,
  final file downloadable from the gated tab only).

---

## 3. Rendering pipeline (shot → scene → full video)

### 3.1 Scene render = one chain (reuses everything)

`render_scene(scene_id)` (background task):

1. Read scene + its shots (ordered by `idx`).
2. Create the chain:
   ```sql
   INSERT INTO video_chains
     (title, concept, model_id, width, height, num_frames, steps, fps, strength,
      prompts, total_segments, nsfw, studio_scene_id)
   VALUES (?, ?, <project settings…>, json([shot.video_prompt…]), n_shots,
           project.nsfw, scene.id)
   ```
   Caveat: `video_chains.num_frames` is one value for the whole chain; per-shot
   `seconds` therefore requires `run_chain_generation` to accept an optional
   per-segment frame list. **Design choice: extend `video_chains` reading in
   `run_chain_generation` minimally** — store `prompts` as today AND add a
   migration `"ALTER TABLE video_chains ADD COLUMN frames_json TEXT"` (JSON list
   of per-segment num_frames; NULL = uniform `num_frames`, fully
   backward-compatible). `run_chain_generation` uses
   `frames[idx] if frames_json else num_frames` at the single point where
   `num_frames` is passed to the script. This is the only touch inside the
   existing chain engine besides the nsfw copy fix.
3. `UPDATE studio_scenes SET chain_id=?, status='queued'`.
4. Call `run_chain_generation(chain_id)` — unmodified flow: preflight once,
   `orch.video_exclusive(model=model_id, desc=f"Studio scene {scene.id}")` for
   the whole chain, per-segment nested `video_acquire`, T2V for shot 0, V2V
   (`strength=0.7`) for shots 1+, per-segment `videos` rows with live progress,
   auto-xfade-compile with concat fallback.
5. On completion: `scene_path = chain.compiled_path`,
   `duration_s = ffprobe(scene_path)`, link each shot:
   `UPDATE studio_shots SET video_id=(SELECT id FROM videos WHERE chain_id=? AND
   chain_index=?), status='done'`. On chain failure: scene `failed` with the
   chain's error; `resume_chain_generation(chain_id)` is the retry path (free —
   already built).

### 3.2 Full-video assembly

`assemble_project(project_id)` (background thread, **CPU-only**, no GPU
acquire — same rationale as the chain auto-compile):

1. Require every scene `status='done'`; else 409.
2. `paths = [scene.scene_path ordered by idx]`.
3. SHORT (1 scene): `video_path = copy(scene_path)` (that is exactly what
   `_compile_chain_video` does for a single input).
   LONG: `video_path = _compile_chain_video(paths, VIDEOS_DIR/f"studio_{id}_full.mp4",
   fps=project.fps)` — scenes crossfade into each other with the same 0.5 s fade
   and the same normalization (scale/pad to first clip's WxH, setsar, fps,
   yuv420p), so mixed-resolution disasters are already handled.
4. Measure per-scene **timeline starts** for the audio stage (xfade overlaps!):
   `start[i] = sum(dur[0..i-1]) - i*0.5` (mirror of the offset formula in
   `_compile_chain_video`; 0.5 = `fade_duration`). Persist nothing — recompute
   from ffprobe when mixing (durations are facts on disk).

### 3.3 Duration / size / fps consistency

- **Decided once at the project row** (`model_id,width,height,fps,steps,strength`)
  and copied into every scene chain — one knob, no drift.
- Portrait 480×832@16fps default (shorts); the editor exposes the same three
  resolutions as `tab-videos.js`.
- Shot `seconds` → `num_frames` snapping (§2.3) keeps requests inside what the
  1.3B Wan model renders reliably on the 3060.
- SHORT: 1 scene, 1–5 shots ⇒ ~3–25 s, no scene-level stitch.
  LONG: 2–6 scenes ⇒ up to ~2.5 min; each scene is an independent chain so a
  failure only re-renders that scene, and V2V continuity is intentionally
  scoped *within* scenes (across scenes a cut/crossfade is the desired look).

### 3.4 Orchestration of a multi-scene render

`render_all(project_id)` background task renders scenes **sequentially**
(`for scene in pending: render_scene(scene)`), because each scene's chain takes
`video_exclusive` and the node has one GPU — parallelism would just deadlock in
the gate. Project `status='rendering'`, `progress_msg="Scene 2/4 — shot 1/3"`
derived from the chain's `completed_segments`.

---

## 4. Layered audio pipeline

### 4.1 Generating the layers (all via existing engines + queue)

`render_audio(project_id)` background task, run **after** scenes are done (VO
alignment needs measured durations). For each cue in (`voiceover` by scene idx,
then `sfx`, then `music`):

1. Insert an `audio_clips` row
   (`kind` = voice|music, `engine`, `prompt=cue.text`, `duration`,
   `lyrics` NULL, `nsfw=project.nsfw`, `studio_cue_id=cue.id`) and set
   `cue.clip_id`, `cue.status='queued'`.
2. Call `run_audio_clip(clip_id)` — unmodified: preflight, `_VIDEO_RUN_LOCK`,
   `orch.video_acquire()` (GPU queue), `_node_audio` on the node, wav copied
   back, status/progress on the clip row. Sequential, one clip at a time —
   same reasoning as scenes.
3. Post-process per kind:
   - **voiceover**: `duration_s = ffprobe(wav)`; `start_s = scene_start(scene.idx)`
     (from §3.2 formula, recomputed at mix time).
   - **music**: request `duration = clamp(ceil(total_video_s)+1, 8, engine max)`
     (musicgen tops out usefully ~30–60 s — the mixer **loops** the bed, so a
     short bed is fine and cheap). `start_s = 0`.
   - **sfx**: `duration = 3` (engine minimum-ish); `start_s = scene_start + cue.offset_s`.

Engine notes: MMS-TTS ignores duration (speech length follows text — that's the
whole alignment problem, §4.4). SFX through MusicGen works best with prompts
that end in “, no music” (the storyboard prompt enforces this); `stable_audio`
is the better SFX engine when the HF token is configured — `run_audio_clip`'s
existing gated-engine fallback to musicgen already covers the tokenless case.

### 4.2 Timeline model

```
final timeline (seconds) ── video is ALWAYS the master clock
0                    s1=start of scene1                 T=video duration
│ scene0 ▓▓▓▓▓▓▓▓▓▓▓▓╳ scene1 ▓▓▓▓▓▓▓▓▓▓╳ scene2 ▓▓▓▓▓│   ╳ = 0.5s xfade
│ vo0 ─────────┐      vo1 ────────┐        vo2 ──────┐ │   VO starts at scene starts
│ music bed ────────────────────────────────────────── │   looped, vol 0.25, ducked
│      sfx@2.1 ▌            sfx@11.4 ▌                 │   spot sounds
```

`scene_start(i) = sum(measured_dur[0..i-1]) − i*0.5` (matches the video xfade
offsets exactly, so VO never drifts against the picture it was written for).

### 4.3 The ffmpeg mixing chain (concrete)

One command, built dynamically (adelay takes **milliseconds**, one value per
channel with `|`):

```bash
ffmpeg -y \
  -i studio_42_full.mp4 \                       # 0: assembled silent video
  -stream_loop -1 -i cue_music.wav \            # 1: music bed (looped forever; -t clamps)
  -i cue_vo_s0.wav -i cue_vo_s1.wav \           # 2,3: per-scene voiceover
  -i cue_sfx_a.wav \                            # 4: sfx
  -filter_complex "\
    [1:a]volume=0.25[bg];\
    [2:a]adelay=0|0,volume=1.0[vo0];\
    [3:a]adelay=8300|8300,volume=1.0[vo1];\
    [4:a]adelay=2100|2100,volume=0.9[fx0];\
    [bg][vo0][vo1][fx0]amix=inputs=4:duration=first:normalize=0,\
    alimiter=limit=0.891[a]" \
  -map 0:v -map "[a]" -t 27.50 \
  -c:v copy -c:a aac -b:a 192k studio_42_final.mp4
```

- `duration=first` + the infinite-looped bed as first mix input + explicit
  `-t {video_duration}` ⇒ output length is exactly the video's length (same
  belt-and-suspenders as `_mux_audio`).
- `normalize=0` keeps authored levels (amix would otherwise divide by N);
  `alimiter` catches VO+SFX peaks instead.
- `-c:v copy`: mixing never re-encodes the picture.
- Optionally write the mixed track alone first for debugging/preview
  (`-map "[a]" mix_42.wav` in a first pass → `project.mix_path`), then mux —
  two cheap CPU passes, and the UI can preview the mix before committing.
- **Nicety (Phase 3): sidechain ducking** — music dips under speech instead of a
  flat 0.25:
  ```
  [vo0][vo1]amix=inputs=2:duration=longest:normalize=0[voall];
  [bg][voall]sidechaincompress=threshold=0.03:ratio=8:attack=50:release=400[bgduck];
  [bgduck][voall][fx0]amix=inputs=3:duration=first:normalize=0[a]
  ```
- **Nicety (Phase 4): burned captions** — generate an `.srt` from scene captions
  (start=scene_start, end=next start) and add `subtitles=studio_42.srt:force_style=
  'FontSize=18,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2'` to a
  `-filter_complex` video branch (this pass does re-encode: `-c:v libx264 -crf 23`).

### 4.4 Duration reconciliation (audio vs video)

Video is the master; audio is fitted to it:

| Case | Resolution |
|---|---|
| VO for scene *i* longer than scene *i* | If `vo_dur ≤ scene_dur × 1.3`: speed up with `atempo={vo_dur/scene_dur}` (pitch-preserving, ≤1.3 stays natural) inserted in that VO's filter leg. If still longer: let it spill into the next scene (speech continuity beats a hard cut) and flag `progress_msg` so the owner can trim the line. |
| VO much longer than the whole video (last scene spill past T) | Extend the tail: re-run assembly with `tpad=stop_mode=clone:stop_duration={overrun}` on the last scene's leg (freeze-frame hold), then remix. Offered as a one-click "Hold last frame to fit narration" action, never automatic. |
| Music shorter than video | `-stream_loop -1` (already in the sketch). |
| Music longer than video | `-t` clamps; nothing to do. |
| SFX cue beyond T (edited timings) | Drop the cue at mix time with a warning in `progress_msg`. |
| A shot/scene missing (failed) | Mix refuses (409) until all scenes are done or the owner deletes the failed scene (then indices re-pack and starts recompute — durations are always measured fresh from disk). |

---

## 5. API surface (`app/routers/studio.py`, included from `main.py` like every router)

All heavy work returns immediately and runs as `BackgroundTasks` /
`orch.submit_llm`; the UI polls the project GET. Bodies are Pydantic models in
the router, matching the `videos.py` style.

```
# ── projects / storyboard ──
POST   /api/studio/projects
       {idea, kind:'short'|'long'='short', style?='', target_seconds?=20,
        model_id?, width?, height?, fps?, steps?, nsfw?=false}
       → {id, task_id}                      # inserts + kicks the storyboard LLM
GET    /api/studio/projects                  → [project summaries]  (nsfw filtered)
GET    /api/studio/projects/{id}             → project + scenes[ + shots[]] + cues[]
PATCH  /api/studio/projects/{id}             {title?, style?, script?, captions?,
                                              music_engine?, voice_engine?,
                                              model_id?, width?, height?, fps?, steps?}
DELETE /api/studio/projects/{id}             # cascades scenes/shots/cues; keeps rendered
                                             # chains/clips unless ?purge=1
POST   /api/studio/projects/{id}/storyboard  {notes?=''} → {task_id}
       # (re)runs the storyboard LLM; wipes draft scenes/shots/cues, keeps settings

# ── tweaking (the owner edits everything before render) ──
PATCH  /api/studio/scenes/{sid}              {title?, summary?, voiceover?, caption?}
POST   /api/studio/scenes/{sid}/regen        {notes?=''} → {task_id}   # LLM rewrites ONE scene
POST   /api/studio/scenes/{sid}/shots        {video_prompt, seconds=3, caption?=''} → {id}
PATCH  /api/studio/shots/{shid}              {video_prompt?, seconds?, caption?, seed?}
DELETE /api/studio/shots/{shid}
POST   /api/studio/cues                      {project_id, scene_id?, kind, text,
                                              engine?, offset_s?, duration_s?, gain?} → {id}
PATCH  /api/studio/cues/{cid}                {text?, engine?, offset_s?, gain?}
DELETE /api/studio/cues/{cid}

# ── rendering ──
POST   /api/studio/scenes/{sid}/render       → {chain_id}     # one scene → chain
POST   /api/studio/scenes/{sid}/resume       → {ok}           # resume_chain_generation
POST   /api/studio/shots/{shid}/reroll       → {ok}
       # new seed: deletes that segment's videos row, resume_chain_generation from idx,
       # recompile scene
POST   /api/studio/projects/{id}/render      → {ok}           # all pending scenes, sequential
POST   /api/studio/projects/{id}/audio       → {ok}           # generate all cues (post-render)
POST   /api/studio/projects/{id}/assemble    {duck?=false, burn_captions?=false,
                                              hold_last_frame?=false}
       → {ok}   # stitch scenes → mix cues → mux → final_path   (CPU only)

# ── export ──
POST   /api/studio/projects/{id}/export
       {platforms=['youtube','tiktok'], title?, publish?=false}
       → {post_id}
       # creates a social_posts row: caption=project.captions, media_type='video',
       #  media_path=final_path, chain_id=(single-scene ? scene.chain_id : NULL),
       #  source='generated', per_platform seeded from title/hashtags;
       # publish=true additionally hits the existing /api/social/posts/{pid}/publish
       # 404s for nsfw projects (no NSFW lane on Social)
```

Serving files: `final_path`/`scene_path` live under `VIDEOS_DIR`, so the
existing `/videos/{filename}` static route serves previews with zero new code.

---

## 6. UI — the 🎞️ Director sub-tab (`static/js/tab-director.js`)

Wire-up: add `{ k:'director', view:'director', label:'🎞️ Director', fn: () =>
renderDirector() }` to `STUDIO_SUBS` in `app-studio.js`, `case 'director'` in
`app-nav.js` `renderView`, and the `<script>` tag in `index.html`. No build
step; everything renders into `viewRoot()` with `api()`/`esc()`/`toast()`.

### 6.1 Inbox view (default)

- **Header**: "🎞️ Director" / sub "Drop an idea — get a storyboard, a video, and a soundtrack".
- **Drop card**: big `<textarea id="dir-idea">` ("Paste an idea, a meme, a
  shower thought…"), row of selects: Kind (Short ~15 s / Long 1–2 min), Style
  (free text), Length (10/20/30/60/90 s), Resolution (the three from
  tab-videos), Model (from `/api/video-models`, installed only). One primary
  button **"🎬 Storyboard it"** → `POST /api/studio/projects` → toast →
  project appears below with a purple "storyboarding…" badge.
- **Project cards grid** (statuses drive the card face, exactly like
  `videoCard`): `draft` → "📋 Storyboard ready — open to edit";
  `rendering/assembling/mixing` → progress bar + `progress_msg`;
  `done` → inline `<video>` preview of `final_path` + "📤 Export" shortcut;
  `failed` → red error box + Retry. Click opens the editor. Poll every 2.5 s
  while any project is active (setTimeout pattern from `refreshVideoGallery`).

### 6.2 Editor view (one project)

Single scrollable column, top to bottom:

1. **Header bar**: back button, editable title, status badge, and the action
   rail whose buttons enable by state:
   `[🪄 Re-storyboard] [🎬 Render all] [🎙 Generate audio] [🎞 Assemble & mix] [📤 Export]`
   — each disabled with a tooltip until its precondition is met (audio needs
   all scenes done; assemble needs scenes+cues done; export needs final_path).
2. **Settings strip** (collapsible): model/resolution/fps/steps/engines —
   PATCHes the project on change; locked (read-only) once any scene rendered.
3. **Script & caption card**: two textareas — full script (regenerating VO cues
   from it on save re-splits by scene separator lines) and the social
   caption+hashtags. Save button per card (`PATCH /projects/{id}`).
4. **Audio plan card**: music prompt textarea + engine select, voice engine
   select, list of SFX cue rows (`text`, scene select, offset number input,
   gain slider, 🗑) + "＋ SFX cue". Music/VO cue statuses shown as chips
   (queued/generating/done/failed with error tooltip) reusing the audio-card
   status colors.
5. **Scene cards** (ordered): header `Scene 2 · ~8s · [status badge]` with
   `[🪄 Regen scene] [🎬 Render scene] [▶ preview]`; body:
   - summary + voiceover + caption textareas (PATCH scene on blur),
   - **shot rows**: `#` · video_prompt textarea · seconds select
     (1.5/3/5/7.5) · caption input · `[🎲 Reroll]` (post-render) · 🗑 ·
     "＋ shot" at the bottom,
   - while rendering: the exact progress-bar block from `videoCard`
     (`progress`/`progress_msg` come from the chain's segment rows),
   - when done: inline `<video>` of `scene_path`, muted, loop.
6. **Final card** (appears when `final_path` exists): `<video controls>` of the
   final mp4, mix options (duck music ☑, burn captions ☑, hold last frame ☑ →
   re-run assemble), Download button, and **Export**: platform checkboxes
   (YouTube/TikTok from `/api/social/platforms`), optional title override,
   "Create draft post" vs "Publish now" → `POST /export` → success links to
   the Social tab (`switchView('social')`).

NSFW: the Director inbox shows an "🔒 Private" toggle only when
`/api/nsfw/status` reports visible; NSFW projects render with the standard
blurred-until-hover treatment used by the Private Studio tab and hide Export.

---

## 7. Queue integration (nothing bypasses the unified queue)

| Step | Submission | Notes |
|---|---|---|
| Storyboard / scene regen | `orch.submit_llm(fn, desc, priority=0, task='studio_storyboard'│'studio_scene_regen')` | user-facing priority; NSFW model routing via the task tuple (§2.5); runs `_call_lmstudio` inside the worker like every LLM feature |
| Scene render (chain) | `run_chain_generation` → `orch.video_exclusive(model=model_id, desc='Studio scene N')` spanning the whole chain + nested `video_acquire` per segment | unchanged code path; media ticket in `_media_gate` means queued LLM work drains first, affinity batches same-model chains |
| Shot reroll | `resume_chain_generation(chain_id)` after deleting the segment row | inherits the same exclusivity |
| Voiceover / music / SFX | `run_audio_clip(clip_id)` → `_VIDEO_RUN_LOCK` + `orch.video_acquire()` per clip | unchanged; clips run one at a time |
| Assemble / mix / mux / captions | plain background thread, **no acquire** — pure CPU ffmpeg on the store host | same policy as the existing chain auto-compile ("GPU already released") |
| Export/publish | existing Social publish worker | no GPU |

Ordering/dependency rules enforced by the project driver
(`services_studio.py :: run_project_pipeline(project_id, stages)`):

```
storyboard ──▶ (owner edits) ──▶ render scenes (sequential, GPU) 
     ──▶ generate cues (sequential, GPU; needs measured scene durations)
     ──▶ assemble+mix (CPU) ──▶ export
```

The driver never holds any GPU primitive across stages — each inner call takes
and releases its own acquire, so LLM work interleaves between scenes/clips
exactly as the scheduler intends (aging + affinity keep it fair). A "Do
everything" convenience button just runs the stages back-to-back through the
same driver.

Startup reconcile: extend `reconcile_stuck_media()` with
`UPDATE studio_projects SET status='failed', error='Interrupted by a server
restart' WHERE status IN ('storyboarding','rendering','assembling','mixing')`
(and matching scene/cue sweeps) so the Director tab never polls a ghost.

---

## 8. Build phasing (dependency-ordered)

**Phase 1 — Storyboard core** *(no GPU code at all)*
- `create_studio_tables` + migrations; `studio_storyboard` PromptDef;
  `routers/studio.py` with create/get/list/patch project + scene/shot/cue
  PATCH endpoints; the storyboard LLM worker with validation/clamping;
  `tab-director.js` inbox + editor (edit-only).
- Delivers: drop an idea → editable storyboard. Unblocks everything.

**Phase 2 — The smallest end-to-end slice ("a good first video")**
- `render_scene` (chain creation + `frames_json` migration + nsfw copy fix),
  single-scene assemble (copy), **simple mix**: music bed + ONE whole-video
  voiceover via the existing `_mux_audio`-shaped command, mux, `final_path`,
  export-to-Social endpoint (draft post), render/preview/export UI states.
- Delivers: **SHORT project: idea → 1 scene × 3 shots (~9 s) → stitched clip →
  music + narration → draft Social post.** This is the demo milestone; ship it
  before touching timeline math.

**Phase 3 — LONG videos + real layered timeline**
- Multi-scene sequential render + scene-level `_compile_chain_video` assembly;
  per-scene VO cues with `adelay` starts, SFX cues, the full `amix` chain,
  `atempo` reconciliation, sidechain ducking option, `run_project_pipeline`
  driver + "Do everything".
- Delivers: 1–2 minute multi-scene videos with aligned audio.

**Phase 4 — Steering & polish**
- Scene regen (`studio_scene_regen`), shot reroll, hold-last-frame fit,
  burned captions (.srt + subtitles filter), publish-now export,
  NSFW project lane end-to-end, reconcile-on-startup sweeps, gallery filters
  (`studio_scene_id`/`studio_cue_id` exclusions).

Each phase is independently shippable; nothing in a later phase reworks an
earlier one (the Phase-2 "simple mix" literally is the Phase-3 chain with one
VO leg and no adelay).

---

## 9. Edge cases & risks

1. **TTS timing drift (top risk)** — MMS-TTS length is uncontrollable; a
   40-word scene VO can run 16 s over an 8 s scene. Mitigations: the storyboard
   prompt budgets ~2.5 words/sec; per-scene alignment resets drift at every
   scene boundary (errors never accumulate); `atempo` up to 1.3×; spill-over
   with a visible warning; owner-triggered freeze-frame extension. Residual
   risk: robotic pacing on dense scripts — surfaced in the editor ("VO is 4.2 s
   long for a 3 s scene") so the owner trims words, which is the real fix.
2. **VRAM on the 3060 (12 GB)** — every stage already runs under
   `video_acquire`'s free-and-verify (≥8 GB check) and the chain-long
   `video_exclusive` hold; audio engines ride the same gate. Danger spots:
   ACE-Step (10–12 GB) as music engine right after a chain — fine serially, but
   never run it as SFX×N (design keeps SFX on musicgen-small/stable_audio);
   and the storyboard LLM between scene renders is naturally fenced out by the
   exclusive hold. A LONG project is simply a lot of sequential 3060-minutes:
   the UI must set expectations (~2–4 min per shot ⇒ show an ETA).
3. **Model-JSON fragility** — local models wrap JSON in prose/fences or drop
   keys. Mitigations: regex-extract first `{…}` (the `chain-prompts` pattern),
   hard clamping (§2.3), `max_tokens=4000`, and a failed parse lands as
   `status='failed'` with the raw tail in `error` + a one-click "Try again".
   Never auto-retry in a loop (GPU time is expensive).
4. **Audio longer/shorter than video** — fully specified in §4.4; the invariant
   "video is the master clock, `-t` clamps, bed loops" makes every mismatch
   recoverable without regenerating video.
5. **Missing/failed segment** — scene fails with the chain's error;
   `resume_chain_generation` restarts at the failed segment (already built,
   already used by the gpu-guard); assemble refuses on incomplete scenes rather
   than silently shipping holes. A deleted scene re-packs indices and all
   timeline starts are recomputed from ffprobe at mix time, so stored numbers
   can never go stale.
6. **xfade vs. audio alignment** — scene starts must use the same
   `−i*fade_duration` formula the compiler uses; if the compiler falls back to
   plain concat (its xfade-failure path), the mix must use fade=0 in the start
   formula. Concrete fix: `_compile_chain_video` grows an optional
   `return`-metadata (list of applied offsets) or the assembler re-probes the
   final video and scales starts by `T_actual/T_expected` — the design mandates
   the re-probe (cheap, always truthful).
7. **NSFW threading** — flag flows project → chains → segments (needs the §1.3
   one-line fix) → clips; listings filter on it; the safety floor screens both
   the idea and all model-authored text unconditionally; export to Social is
   blocked for NSFW projects. The gate model is toggles-for-access,
   no-toggle-for-the-floor — identical to the Private Studio.
8. **Restart mid-pipeline** — reconcile sweep (§7) fails orphans loudly;
   because every artifact (chains, segments, clips, scene_paths) is on disk and
   re-derivable, "Retry" from any stage only redoes the missing stage.

# TV — prompt-to-television (shows, episodes, canon)

The owner's goal, verbatim intent: *"enter a small or large prompt of what I
want to watch and get a whole show with depth and story — each episode comes
to an end but keeps its context into the next episode, like American Dad /
Rick and Morty / mature animation. Shows and episodes as first-class things.
HD optional; animation preferred; as long as we can make it."* Motive: has
watched everything, wants something new to watch.

This layer sits ON TOP of the Director studio (docs/VIDEO-STUDIO-DESIGN.md):
**an episode IS a studio project**. TV adds what a single project can't hold:
the show bible, episode-to-episode continuity, character/voice/style
consistency, and the batch loop that renders "the next episode" unattended.

## 1. The honest math (measured 2026-07-25, RTX 3060, Wan2.1-1.3B, 20 steps)

- 49-frame (~3 s) segment ≈ 9–10 min · 81-frame (~5 s) ≈ 15 min
- ≈ **3 GPU-minutes per finished second** → 1 min of show ≈ 3 h of render
- Episode ladder (what "as long as we can" means in practice):
  - **Pilot ladder rung 1 — 2–3 min episode ≈ 6–9 h — one overnight.** Start here.
  - Rung 2 — 5 min ≈ 15 h — a day+night. · Rung 3 — 11 min (standard
    "quarter-hour" adult-swim slot) ≈ 33 h — a weekend.
  - 22 min network-length ≈ 66 h — only worth it after the speed lane lands.
- **Speed lane (multiplies the ladder, Phase 4):** LTX-Video (fast, supports
  image-conditioning) and/or a distilled few-step Wan variant for draft/final
  tiers; TeaCache-style step caching; 12 fps for 2D-animation look (2D tolerates
  low fps far better than photoreal — it's how real TV animation works).
  Target: 5–10× faster → 11-min episode overnight. Animation style is not just
  aesthetic, it's the length strategy.

## 2. Object model (new tables — episode reuses studio_projects wholesale)

```
tv_shows
  id, title, prompt          -- the owner's original "what I want to watch"
  bible TEXT                 -- JSON show bible (see §3): premise, tone, world,
                             --   style_guide (prompt prefix every shot gets),
                             --   characters[] {name, visual_desc, voice, quirks},
                             --   running_gags[], season_arc, canon[] (grows)
  style TEXT                 -- e.g. "2D adult animation, thick outlines, flat colors"
  format_seconds INTEGER     -- target episode length (the ladder rung)
  model_id/width/height/fps/steps  -- render settings copied to every episode
  status                     -- new | bible | ready | failed
  nsfw INTEGER DEFAULT 0     -- mature-themes lane rides EXISTING nsfw gates;
                             -- default is SFW-mature (adult-swim tone, no explicit)
  created_at, updated_at

tv_episodes
  id, show_id, season, number, title
  synopsis TEXT              -- episode-writer output (beats, cold open, A/B plot, button)
  recap TEXT                 -- "previously on" IN: what the writer was told
  memory TEXT                -- OUT: post-render canon summary (feeds next episode)
  project_id INTEGER         -- fk → studio_projects (the whole render pipeline)
  status                     -- draft | writing | ready | producing | done | failed
  final_path TEXT            -- copy of the project's final for the TV player
  created_at, updated_at
```

Continuity = `recap` (in) + `memory` (out) + `bible.canon` (accumulated facts).
Every episode ends resolved (episodic close) but `memory` carries forward what
changed — exactly the American Dad model: standalone plots, persistent world.

## 3. The writers' room (all local LLM via orch.submit_llm, no new GPU paths)

1. **Showrunner** (once per show): owner prompt (small or large) → the bible.
   Small prompts get expanded (invent premise, 4–6 characters with distinct
   voices/quirks/visual descriptions, world rules, season arc, running gags);
   large prompts get honored and structured. Owner can edit the bible anytime.
2. **Episode writer** (per episode): bible + season arc + last N episode
   `memory` blocks → synopsis with act structure (cold open / A-plot / B-plot /
   button for the length budget), scene beats, and per-scene dialogue-vs-
   narration notes. Then it calls the EXISTING storyboarder with the beats +
   style_guide + character visual descriptors injected, producing the
   scenes/shots/cues of a studio project sized to format_seconds.
3. **Continuity editor** (after produce): watches nothing — reads the script/
   storyboard as-rendered + what failed/changed → writes `episode.memory`,
   appends durable new facts to `bible.canon`. Next episode inherits truth.

## 4. Consistency kit (the hard problem for a SHOW vs a video) — Phase 3

- **Style lock**: `bible.style_guide` is a mandatory prompt prefix on every
  shot of every episode (the single cheapest consistency win).
- **Character sheets**: per character, generate reference stills with the
  existing image pipeline; store on the show. Used (a) in every shot prompt as
  the character's fixed visual descriptor text, (b) later as image-conditioning
  anchors for I2V shots (LTX supports this) so faces/outfits stop drifting.
- **Voice casting**: per-character TTS voice. Piper (already deployed for
  Jarvis, CPU, many voices) joins mms_tts as a voice engine; the bible maps
  character → voice; dialogue cues carry the character name → its voice.
  This is what makes it feel like a cast instead of one narrator.
- Optional later: per-show LoRA trained from the character sheets (the real
  fix for visual drift; heavy — only after the rest works).

## 5. Pipeline per episode (all existing machinery)

write (LLM) → storyboard (existing) → produce_project (existing: scenes as
chains on the unified GPU queue → Phase-3 timeline audio → assemble/mix) →
continuity editor → episode done → TV tab. The batch driver ("season mode")
walks episodes one at a time, respects the queue (gpu-guard "AI yields to
Tdarr" untouched), resumes after restarts (resume_scene exists), and can run
"write next episode while rendering this one" (LLM box ≠ render GPU).

## 6. Watching it (🍿 TV tab)

Shows grid ("create show" = the one prompt box) → show page: bible summary,
cast, seasons/episodes with statuses, ▶ play. Player autoplays the next
episode ("channel mode"). HD option = a per-episode upscale pass (Real-ESRGAN
video 2×, CPU/GPU idle-time job) — off by default, exactly as the owner said.

## 7. Build phases

- **P0 (foundation, in flight)**: Director Phase-3 timeline audio — per-scene
  VO placed at measured scene starts, SFX cues, sidechain ducking, atempo
  reconciliation. Without this an episode's sound can't line up, agent or not.
- **P1 TV layer**: tables + routers + showrunner/episode-writer/continuity
  prompts + TV tab. Episode = studio project. Manual per-episode produce.
  Deliverable: prompt → show → written episode → produced 2–3 min pilot.
- **P2 Season mode**: the batch/agent driver — scene-by-scene adapt loop
  (measure → re-roll failed shots → adjust next scene budgets), overnight
  "produce next episode", pause-at-episode-boundary approval toggle.
- **P3 Consistency kit**: §4 (style lock ships in P1; sheets, Piper casting,
  I2V anchors here).
- **P4 Speed lane + length climb**: LTX/distilled-Wan draft-final tiers,
  12 fps animation profile, 11-min episodes overnight.
- **P5 Conditioned audio**: MMAudio per-shot foley + LatentSync lip-sync on
  dialogue shots (see audio overhaul discussion) — this is when mouths match
  words and doors slam on the frame.

Ship order inside each phase: smallest end-to-end slice first, then widen.

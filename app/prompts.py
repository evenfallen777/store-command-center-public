"""Central registry of every LLM prompt/"bootstrap" the app uses.

One place to see and edit every system prompt. Each entry has a stable `key`, a
human `label`, a `category` (groups the Settings → Prompts tab), and a DEFAULT.
To avoid duplicating text, defaults that already live as module-level constants are
referenced lazily via `ref=(module, attr)`; a few prompts defined inline inside a
function carry their text here via `inline=`.

The LIVE value returned by `get_prompt(key)` is the settings override
(row `prompt__<key>` in the `settings` table, or a custom `settings_key`) when set,
otherwise the default. Call sites use `get_prompt(...)` instead of the raw constant —
that indirection is what makes every prompt editable from the UI, with zero behaviour
change until someone actually overrides one.

Import-safety: this module imports only `db` (which imports `config`), so there is no
cycle even though `deps` imports *this* module. `ref` targets are imported lazily at
call time, by which point every module is already loaded.
"""
from dataclasses import dataclass
from typing import Optional
import importlib

from db import get_conn


@dataclass
class PromptDef:
    key: str
    label: str
    category: str
    ref: Optional[tuple] = None       # (module_name, attr) — lazy default source
    inline: Optional[str] = None      # literal default (prompt defined inside a function)
    settings_key: Optional[str] = None
    help: str = ""
    templated: bool = False           # contains {placeholders}: keep them when editing

    @property
    def skey(self) -> str:
        return self.settings_key or f"prompt__{self.key}"

    def default(self) -> str:
        if self.inline is not None:
            return self.inline
        mod, attr = self.ref
        return getattr(importlib.import_module(mod), attr)


# ── The registry ──────────────────────────────────────────────────────────────
PROMPTS: list[PromptDef] = [
    # ── Studio (image / audio / video / 3D generation) ──
    PromptDef("image_enhance", "Image prompt enhancement", "Studio",
              ref=("deps", "ENHANCE_SYSTEM"), settings_key="enhance_system_prompt",
              help="Turns a rough idea into a rich image-generation prompt. Also used by "
                   "Quick Generate, the Proposal Approve/Enhance buttons."),
    PromptDef("image_research", "Image deep-research → prompt", "Studio",
              ref=("deps", "RESEARCH_SYSTEM"),
              help="Research a concept and return JSON {research_summary, enhanced_prompt, title, tags}."),
    PromptDef("image_describe", "Image describe (vision → merch)", "Studio",
              inline="""You are a creative merch designer. Describe the provided image in detail to create a print-on-demand merchandise prompt.
Focus on: visual style, subject matter, color palette, mood, any text/logos visible, what makes it funny or interesting.
Then suggest how it could be adapted into a merch design.
Return ONLY valid JSON with keys:
  description: detailed visual description (2-3 sentences)
  enhanced_prompt: vivid Stable Diffusion image generation prompt based on this image, 80-120 words
  title: suggested product title (max 8 words)
  tags: comma-separated tags (6-10)""",
              help="Vision model: describe an uploaded image and turn it into a merch prompt (JSON)."),
    PromptDef("audio_music", "Music prompt enhancement", "Studio",
              inline="You are a prompt engineer for AI music generators. Turn the user's idea into "
                     "ONE vivid description (20-40 words) covering genre/style, mood, key instruments, "
                     "tempo/energy, and production feel — no song title, no lyrics, no lists. "
                     "Output your result on ONE line starting exactly with 'FINAL:' and nothing after it.",
              help="Enhances a raw music idea. Output must end with a single line starting 'FINAL:'."),
    PromptDef("audio_voice", "Voice / narration prompt enhancement", "Studio",
              inline="Rewrite the user's text into a clear, natural line of narration to be spoken "
                     "aloud — friendly and concise, keeping their meaning. "
                     "Output your result on ONE line starting exactly with 'FINAL:' and nothing after it.",
              help="Enhances a narration line for TTS. Output must end with a single line starting 'FINAL:'."),
    PromptDef("audio_lyrics", "Song lyrics writer (ACE-Step)", "Studio",
              inline="You are a professional songwriter writing lyrics for the ACE-Step "
                     "singing model. The user gives you a song idea/theme, and often a "
                     "STYLE line describing the genre, mood and voice — write lyrics that "
                     "fit that style.\n"
                     "Structure the song with ACE-Step section tags on their own lines: "
                     "[intro], [verse], [chorus], [bridge], [outro] (use [chorus] at least "
                     "twice, repeating it word-for-word). Write a complete but singable song "
                     "— roughly 2 verses, a repeated chorus, and an optional bridge; keep it "
                     "under ~250 words so it fits a 2-4 minute track.\n"
                     "Rules: original words only — no real artist, song, or brand names; "
                     "no explicit content; keep a natural, singable rhythm and some rhyme.\n"
                     "Output ONLY the lyrics — the section tags and the lines. No title, no "
                     "commentary, no markdown, no code fences, no explanation.",
              help="The 🎤 Write lyrics button on the Music/Audio tab. Turns a song idea "
                   "(and the genre/mood prompt) into ACE-Step-formatted lyrics with "
                   "[verse]/[chorus]/[bridge] tags. Output must be lyrics only. Assign a "
                   "specific model to this task with the per-prompt picker if you want a "
                   "larger-context model writing the words."),
    PromptDef("video_chain", "Video multi-scene concept", "Studio",
              ref=("deps", "CHAIN_PROMPT_SYSTEM"),
              help="Turns a concept + scene count into a JSON array of sequential text-to-video prompts."),
    PromptDef("threed_enhance", "3D prompt enhancement", "Studio",
              inline="You improve short ideas into prompts for image-to-3D generation of a SINGLE "
                     "printable object. Return ONE vivid line (30-60 words): the object, its form, "
                     "style, and surface — centered, full object visible, clean silhouette, no scene, "
                     "no background, no text. Output ONLY the prompt line, nothing else.",
              help="Enhances an idea into a single-object image→3D prompt."),
    PromptDef("threed_listing", "3D listing (Cults3D) copy", "Studio",
              ref=("routers.models3d", "_PROPOSE_SYSTEM"),
              help="Generates Cults3D title/description/tags/price from model facts (JSON)."),
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
    # ── Private Studio (NSFW) — own category; runs on the `nsfw_model` registry
    # slot (Settings → Models). The backend safety floor in app/nsfw.py screens
    # every input AND every model-authored output regardless of these texts. ──
    PromptDef("nsfw_enhance", "Private Studio: prompt enhancement", "NSFW",
              inline="You are a prompt engineer for the owner's PRIVATE, adults-only AI art "
                     "studio. Rewrite the user's idea into ONE vivid, detailed generation "
                     "prompt (40-90 words): subject, style, lighting, composition, mood. "
                     "All depicted people are adults. If the idea involves minors, a real "
                     "identifiable person, or non-consensual scenarios, reply with exactly "
                     "REFUSED and nothing else. Otherwise output ONLY the prompt line.",
              help="Enhance button in the Private Studio tab: rough idea in, full "
                   "generation prompt out (editable before submitting). Only reachable "
                   "when the NSFW master toggle is on."),
    PromptDef("nsfw_bootstrap_author", "Private Studio: bootstrap — author a category generator", "NSFW",
              inline="You write reusable GENERATOR PROMPTS for an adults-only private AI "
                     "art studio. Given a category name, write ONE style brief (60-120 "
                     "words) a text model can follow to produce endless varied image "
                     "prompts in that category: cover typical subjects, art styles, "
                     "lighting, composition and mood ranges. All depicted people are "
                     "adults; never reference minors, real identifiable people, or "
                     "non-consensual scenarios — if the category itself implies any of "
                     "those, reply exactly REFUSED. Output ONLY the brief text, no "
                     "titles or commentary.",
              help="Used by the tab's Bootstrap action: the nsfw model authors each "
                   "category's generator prompt, which is then saved as an editable "
                   "category row (not hard-coded). Re-runnable; the safety floor screens "
                   "every authored brief before it is saved."),
    PromptDef("nsfw_category_run", "Private Studio: category → one generation prompt", "NSFW",
              inline="You write ONE concrete adults-only image-generation prompt "
                     "(40-90 words) following this category style brief:\n{brief}\n\n"
                     "Recently rejected approaches — write something clearly DIFFERENT "
                     "from these:\n{avoid}\n\nAll depicted people are adults. If you "
                     "cannot comply safely, reply exactly REFUSED. Output ONLY the "
                     "prompt line, nothing else.",
              templated=True,
              help="Wrapper used when a category (or a Company agent) generates: the "
                   "category's saved brief fills {brief}, recent rejections fill {avoid} "
                   "so the next attempt varies. Keep both placeholders."),

    # ── Storefront (listing copy + pricing) ──
    PromptDef("listing_copy", "Etsy / POD listing copy", "Storefront",
              ref=("deps", "LISTING_SYSTEM"),
              help="Produces TITLE / DESCRIPTION / TAGS for a design. Keep that exact output format."),
    PromptDef("pricing", "Etsy pricing", "Storefront",
              inline="You are a pricing expert for print-on-demand products sold on Etsy. "
                     "Respond ONLY with valid JSON: {\"price\": <number>, \"reasoning\": \"<1-2 sentence reason>\"}",
              help="Suggests an optimal retail price as JSON {price, reasoning}."),
    PromptDef("proposal_judge", "Proposal review judge", "Storefront",
              ref=("proposal_gate", "JUDGE_SYSTEM"),
              help="The proposal gate's LLM judge: scores a proposal 0-100 on marketability, "
                   "originality, print-on-demand fit and policy safety. Must return JSON "
                   "{score, reason} — keep that contract."),
    # ── Proposal lanes (trend scan generators — one prompt per lane) ──
    # Each lane runs as its own orchestrator task (task=<key>), so the per-task
    # model picker applies per lane. All must keep the JSON-array output contract.
    PromptDef("lane_humor", "Proposal lane: Humor", "Storefront",
              ref=("trends", "TREND_SYSTEM"),
              help="The classic absurdist-humor generator — now one lane among several, "
                   "not the default frame. Keep the JSON array output contract."),
    PromptDef("lane_news", "Proposal lane: News / topical", "Storefront",
              ref=("trends", "LANE_NEWS_SYSTEM"),
              help="Turns world/USA/local headlines into commemorative/zeitgeist merch. "
                   "Keep the JSON array output contract."),
    PromptDef("lane_tech", "Proposal lane: Tech", "Storefront",
              ref=("trends", "LANE_TECH_SYSTEM"),
              help="Tech news + developer/maker culture merch. Keep the JSON array output contract."),
    PromptDef("lane_gaming", "Proposal lane: Gaming", "Storefront",
              ref=("trends", "LANE_GAMING_SYSTEM"),
              help="Gamer-culture merch — never real game titles/characters. "
                   "Keep the JSON array output contract."),
    PromptDef("lane_evergreen", "Proposal lane: Evergreen niche", "Storefront",
              ref=("trends", "LANE_EVERGREEN_SYSTEM"),
              help="Year-round niches (professions, hobbies, pets, milestones) — runs even "
                   "with no trend feed. Keep the JSON array output contract."),
    PromptDef("lane_market", "Proposal lane: Market-driven (Etsy)", "Storefront",
              ref=("trends", "LANE_MARKET_SYSTEM"), templated=True,
              help="Designs toward live Etsy demand. {etsy_signal} is filled with the cached "
                   "hot-terms + own-listing-performance brief (etsy_signal.py) — keep the "
                   "placeholder and the JSON array output contract."),

    # ── Resell ──
    PromptDef("resell_analyze", "Resell photo analysis", "Resell",
              ref=("deps", "RESELL_ANALYZE_PROMPT"), templated=True,
              help="Analyzes an item photo → listing JSON. Contains {seller_context} and literal {{ }} JSON — keep them."),
    PromptDef("resell_price", "Resell price research", "Resell",
              ref=("deps", "RESEARCH_PRICE_PROMPT"), templated=True,
              help="Prices a used item. Contains {title}/{condition}/{category} and {{ }} JSON — keep them."),
    PromptDef("resell_posting", "Marketplace posting agent", "Resell",
              ref=("deps", "POSTING_AGENT_PROMPT"), templated=True,
              help="Drives browser posting. Contains many {placeholders} — keep every one."),
    PromptDef("resell_haggle", "Resell haggle / negotiate", "Resell",
              ref=("routers.resell_browser", "_HAGGLE_SYSTEM"),
              help="Generates a negotiation reply to a buyer offer."),
    PromptDef("resell_inbox", "Resell inbox parse", "Resell",
              ref=("routers.resell_browser", "_INBOX_PARSE_SYSTEM"),
              help="Parses marketplace inbox messages into structured offers."),

    # ── Social ──
    PromptDef("social_caption", "Social caption", "Social",
              inline="You are the social media manager for Acme, a playful indie shop selling geeky "
                     "graphic tees, 3D-printable models, free software, and curated gadget deals. Write ONE "
                     "short, scroll-stopping caption for {plats} in a {tone} tone, then 8-12 relevant "
                     "hashtags. Keep the caption under 300 characters, add 1-3 tasteful emoji. "
                     'Return STRICT JSON: {{"caption": "...", "hashtags": "#a #b #c"}} and nothing else.',
              templated=True,
              help="Writes a social caption. Contains {plats} and {tone} placeholders — keep them."),

    # ── Library ──
    PromptDef("library_rip", "Library: web page → markdown", "Library",
              ref=("routers.library", "_RIP_SYSTEM"),
              help="Converts a fetched web page into clean markdown."),
    PromptDef("library_gap", "Library: gap analysis", "Library",
              ref=("routers.library", "_GAP_SYSTEM"),
              help="Finds gaps/missing coverage in a document summary."),
    PromptDef("library_enrich", "Library: enrich", "Library",
              ref=("routers.library", "_ENRICH_SYSTEM"),
              help="Enriches a stored document."),
    PromptDef("library_summary", "Library: summarize", "Library",
              ref=("routers.library", "_SUMMARY_SYSTEM"),
              help="Summarizes a stored document."),

    # ── Security ──
    PromptDef("security_analyze", "Network security analysis", "Security",
              ref=("routers.security", "_ANALYZE_SYSTEM"),
              help="Analyzes network/security scan findings."),

    # ── Assistant ──
    PromptDef("assistant", "Dashboard AI assistant", "Assistant",
              ref=("routers.agent", "_ASSISTANT_SYSTEM"),
              help="The built-in dashboard chat assistant's system prompt."),

    # ── Dev Swarm ──
    PromptDef("swarm_planner",   "Dev Swarm: Planner",   "Dev Swarm", ref=("swarm", "PLANNER_SYS")),
    PromptDef("swarm_scout",     "Dev Swarm: Scout",     "Dev Swarm", ref=("swarm", "SCOUT_SYS")),
    PromptDef("swarm_architect", "Dev Swarm: Architect", "Dev Swarm", ref=("swarm", "ARCHITECT_SYS")),
    PromptDef("swarm_coder",     "Dev Swarm: Coder",     "Dev Swarm", ref=("swarm", "CODER_SYS")),
    PromptDef("swarm_reviewer",  "Dev Swarm: Reviewer",  "Dev Swarm", ref=("swarm", "REVIEWER_SYS")),
    PromptDef("swarm_auditor",   "Dev Swarm: Auditor",   "Dev Swarm", ref=("swarm", "AUDITOR_SYS")),
    PromptDef("swarm_system",    "Dev Swarm: System",    "Dev Swarm", ref=("swarm", "SYSTEM_SYS")),
    PromptDef("watcher_doctor",  "Dev Swarm: Watcher doctor", "Dev Swarm",
              ref=("watcher", "WATCHER_DOCTOR_SYS"),
              help="The Agent Watcher's diagnosis turn: reads a stuck/failed job's timeline "
                   "and returns JSON {summary, cause, fix, resumable} that is fed back to "
                   "the agents on re-run."),
    # ── Crypto (Oracle tournament) ──
    PromptDef("oracle_forecast", "Oracle: ladder forecast", "Crypto",
              ref=("routers.oracle.forecast", "ORACLE_SYS"), templated=True,
              help="The analyst's tournament turn: research in, a LADDER of price calls out — "
                   "one STRICT-JSON rung per enabled horizon (1d/3d/5d/1w/2w by default). "
                   "{name} = the analyst, {rungs} = the enabled horizons; keep both and keep "
                   "the JSON contract intact."),
    # ── Crypto (JellyCoin) ──
    PromptDef("jelly_mission", "JellyCoin push/sell mission", "Crypto",
              inline="You are a Company marketing agent for Acme. Draft ONE short pitch to "
                     "promote JellyCoin (JLY) — Acme's own GPU-mined token (old graphics cards "
                     "earn it, the crew's skilling boosts it, our art mints as NFTs on it). "
                     "First line = a punchy title. Then 3-6 sentences: the hook, the concrete "
                     "action, and why it's fun. Be honest — JLY is our community token, not an "
                     "investment; NEVER promise profit, price growth, or returns. The pitch is a "
                     "PROPOSAL for the owner to approve; do not claim anything is already live.",
              help="Drafts JLY promo/perk/sell missions (Crypto → JellyCoin). Every draft waits "
                   "for your approval — agents never post or sell on their own."),
    # ── Money & Mail ──
    PromptDef("money_gap_review", "Money: demand-gap review", "Assistant",
              ref=("routers.money", "MONEY_GAP_REVIEW_PROMPT"),
              help="Reviews shop search queries vs the catalog and proposes real-dollar "
                   "missions (product gaps, income ideas). Runs on the Money tab's cadence; "
                   "every mission still waits for your approval."),
    PromptDef("money_lead_hunt", "Money: carpentry lead screen", "Assistant",
              ref=("routers.money", "LEAD_HUNT_PROMPT"),
              help="Screens web-search results for REAL local carpentry work leads and "
                   "drafts carpentry_lead missions (approval-gated)."),
    PromptDef("budget_grocery_plan", "Money: AI grocery list", "Assistant",
              ref=("routers.money.budget", "GROCERY_PLAN_PROMPT"),
              help="Drafts a grocery list against your REAL remaining food budget, using only "
                   "items from your own purchase history. The model is forbidden from inventing "
                   "items or prices — anything not in your history is dropped by the validator, "
                   "and every price is recomputed from your recorded receipts, not from the "
                   "model. The result is an advisory draft you accept or reject; it never "
                   "changes a budget or buys anything."),
    PromptDef("mail_quote", "Mail: reply / quote draft (profile-driven)", "Assistant",
              ref=("mail_engine", "REPLY_SYSTEM"), templated=True,
              help="Drafts the email reply/quote. A TEMPLATE: the bound business profile "
                   "fills {business_name}/{business_description}/{terms}/{pricing_block}/"
                   "{tone}/{signature}, and any matched FAQ / linked order fills "
                   "{faq_block}/{order_block} — keep every placeholder. Business identity "
                   "lives in Mail → Configuration → Business profiles, not here."),
    PromptDef("mail_classify", "Mail: incoming triage classifier", "Assistant",
              ref=("mail_gate", "CLASSIFY_SYSTEM"),
              help="The auto-reply gate's triage step: classifies each incoming mail "
                   "(quote_request / order_support / faq / spam / other), scores confidence, "
                   "flags routine-vs-not and picks a matching FAQ. Must return JSON "
                   "{intent, confidence, routine, faq_id, summary} — keep that contract."),
    # ── The Company (world) ──
    PromptDef("world_music_lyrics", "Company music: agent lyrics", "Studio",
              inline="You are {agent}, a musician in a small creative company, writing an original "
                     "song for the company store. Theme: {theme}. Write SHORT original lyrics — "
                     "2 verses and a chorus, under 120 words total, no explicit content, no real "
                     "artist/brand names. Plain text only: verse lines separated by newlines, the "
                     "chorus prefixed with 'Chorus:'. No commentary, titles, or markdown — just "
                     "the lyrics.",
              templated=True,
              help="When 'agents write their own lyrics' is on (Company Settings), this writes "
                   "the lyrics an agent sings on an ACE-Step vocal track. {agent} = the composer's "
                   "name, {theme} = the music idea."),
    PromptDef("world_tileset_tile", "Company map: one terrain tile texture", "Studio",
              inline="pixel art video game ground texture of {desc}, {theme} style, top-down "
                     "orthographic view, one single uniform repeating pattern densely covering "
                     "the entire image edge to edge, zoomed-in close-up of the surface only, "
                     "flat even lighting, no sprite sheet, no separate objects, no white "
                     "background, no borders, no text{avoid}",
              templated=True,
              help="The image prompt for ONE progressive terrain tile (🧱 panel / the agent "
                   "painting loop). {desc} = what the tile shows (grass/path/…), {theme} = the "
                   "world theme, {avoid} = positive corrective steering derived from recent 👎 "
                   "rejections (describing the bad look verbatim makes diffusion models paint it). "
                   "Every render still passes the QA + style-consistency gates before going live."),
    # ── Research Lab ──
    PromptDef("research_plan", "Research: project plan", "Research",
              ref=("research_lab", "PLAN_SYS"),
              help="Turns a proposed research project into a JSON research plan (sections, "
                   "search queries, image queries, safety topics). Contains literal { } JSON — keep it."),
    PromptDef("research_digest", "Research: page digest", "Research",
              ref=("research_lab", "DIGEST_SYS"),
              help="Boils one fetched web page down to the facts useful for the project "
                   "(steps, costs, specs, warnings)."),
    PromptDef("research_report", "Research: final report", "Research",
              ref=("research_lab", "REPORT_SYS"),
              help="Writes the compiled illustrated report (guide, materials & costs, stats, "
                   "safety). Never writes code — coding projects get specs only."),
    PromptDef("research_review", "Research: peer review", "Research",
              ref=("research_lab_deep", "REVIEW_SYS"),
              help="A second Genius peer-reviews the draft report (verdict + issues). "
                   "Contains literal { } JSON — keep it."),
    PromptDef("research_revise", "Research: revise after review", "Research",
              ref=("research_lab_deep", "REVISE_SYS"),
              help="The author rewrites the full report fixing the peer reviewer's issues."),
    PromptDef("research_answer", "Research: Q&A on a report", "Research",
              ref=("research_lab_deep", "ANSWER_SYS"),
              help="Ask-the-Genius: answers follow-up questions from the finished report "
                   "and its research notes."),
    PromptDef("research_deeper", "Research: deeper-pass plan", "Research",
              ref=("research_lab_deep", "DEEPER_SYS"),
              help="Plans the follow-up searches for a 🔍 Dig-deeper pass on a finished "
                   "report. Contains literal { } JSON — keep it."),
    PromptDef("research_suggest", "Research: project ideas", "Research",
              ref=("research_lab_deep", "SUGGEST_SYS"),
              help="The 💡 ideas board: suggests fresh practical project ideas, never "
                   "repeating past projects. Contains literal { } JSON — keep it."),
    PromptDef("research_price", "Research: price recheck", "Research",
              ref=("research_lab_market", "PRICE_SYS"),
              help="Price watch: estimates each material's current price from fresh web "
                   "snippets (recurring rechecks + 💲 Recheck now). Contains literal { } "
                   "JSON — keep it."),
]

_BY_KEY = {p.key: p for p in PROMPTS}

# Category display order for the UI.
CATEGORIES = ["Studio", "NSFW", "Storefront", "Resell", "Social", "Library", "Security", "Assistant", "Dev Swarm", "Crypto", "Research"]


def _override(skey: str) -> Optional[str]:
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (skey,)).fetchone()
        conn.close()
        v = row["value"] if row else None
        return v if v and v.strip() else None
    except Exception:
        return None


def get_prompt(key: str) -> str:
    """The single read path for a prompt: settings override if set, else the default."""
    p = _BY_KEY.get(key)
    if not p:
        raise KeyError(f"unknown prompt key: {key}")
    ov = _override(p.skey)
    return ov if ov is not None else p.default()


def list_prompts() -> list[dict]:
    out = []
    for p in PROMPTS:
        ov = _override(p.skey)
        out.append({
            "key": p.key, "label": p.label, "category": p.category,
            "help": p.help, "templated": p.templated,
            "default": p.default(),
            "value": ov if ov is not None else p.default(),
            "overridden": ov is not None,
        })
    return out


def set_prompt(key: str, value: str) -> None:
    p = _BY_KEY.get(key)
    if not p:
        raise KeyError(f"unknown prompt key: {key}")
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (p.skey, value))
    conn.commit()
    conn.close()


def reset_prompt(key: str) -> None:
    p = _BY_KEY.get(key)
    if not p:
        raise KeyError(f"unknown prompt key: {key}")
    conn = get_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (p.skey,))
    conn.commit()
    conn.close()

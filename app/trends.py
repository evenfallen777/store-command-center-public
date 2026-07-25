"""
Trend scanner — pulls trending topics from:
  • Google Trends  (pytrends, no auth)
  • Reddit RSS     (feedparser, no auth — each subreddit has a public .rss feed)
  • Custom RSS     (feedparser, any RSS/Atom feed URL)
  • Categorized feed groups (world/local/USA/game/tech news — see FEED_GROUPS)

Then routes them through PROPOSAL LANES — per-theme LLM generators (humor is one
lane among several, not the frame everything is forced into). Each lane has its
own system prompt (UI-editable via prompts.py: lane_humor, lane_news, …) and a
set of source categories it draws from; the market lane is steered by the live
Etsy demand signal (etsy_signal.py). Every proposal is tagged with its lane +
source so the review gate and the loop map can follow it down the funnel.
"""
import json, time, logging, re
from typing import Optional
import feedparser

log = logging.getLogger("trends")

# ── Google Trends ─────────────────────────────────────────────────────────────
def fetch_google_trends(region: str = "US", max_results: int = 20) -> list[str]:
    """Fetch trending searches via Google Trends RSS (no pytrends, no auth)."""
    try:
        import requests
        url = f"https://trends.google.com/trending/rss?geo={region.upper()}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        return [e.get("title", "").strip() for e in feed.entries[:max_results] if e.get("title")]
    except Exception as e:
        log.warning("Google Trends fetch failed: %s", e)
        return []

# ── Reddit RSS ────────────────────────────────────────────────────────────────
DEFAULT_SUBS = [
    "gaming", "movies", "television", "Music", "memes",
    "funny", "sports", "anime", "technology", "Art",
]

def fetch_reddit_rss(subreddits: list[str], limit_per_sub: int = 8) -> list[str]:
    titles = []
    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/rising.rss"
            feed = feedparser.parse(url)
            for e in feed.entries[:limit_per_sub]:
                t = e.get("title", "").strip()
                if t and len(t) > 5:
                    titles.append(f"{t} [{sub}]")
        except Exception as e:
            log.warning("Reddit RSS %s failed: %s", sub, e)
        time.sleep(0.3)   # be polite
    return titles


# Which lane-category a subreddit's posts belong to (gaming/tech subs feed
# those lanes; everything else stays general "reddit" → the humor lane).
_REDDIT_GAMING = {"gaming", "games", "pcgaming", "nintendo", "playstation", "xbox",
                  "anime", "boardgames", "dnd", "minecraft", "steam"}
_REDDIT_TECH = {"technology", "tech", "programming", "3dprinting", "gadgets",
                "hardware", "linux", "artificial", "machinelearning", "selfhosted"}


def categorize_reddit_sub(sub: str) -> str:
    s = (sub or "").strip().lower()
    if s in _REDDIT_GAMING:
        return "reddit_gaming"
    if s in _REDDIT_TECH:
        return "reddit_tech"
    return "reddit"


def fetch_reddit_rss_tagged(subreddits: list[str], limit_per_sub: int = 8) -> list[tuple[str, str]]:
    """Like fetch_reddit_rss but each title carries its lane-category:
    [(\"title [sub]\", \"reddit\"|\"reddit_gaming\"|\"reddit_tech\"), ...]"""
    out = []
    for sub in subreddits:
        cat = categorize_reddit_sub(sub)
        for t in fetch_reddit_rss([sub], limit_per_sub):
            out.append((t, cat))
    return out

# ── Custom RSS ────────────────────────────────────────────────────────────────
DEFAULT_RSS_FEEDS = [
    "https://feeds.feedburner.com/TechCrunch",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
]

def fetch_rss_feeds(feed_urls: list[str], limit_per_feed: int = 6) -> list[str]:
    titles = []
    for url in feed_urls:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:limit_per_feed]:
                t = e.get("title", "").strip()
                if t and len(t) > 5:
                    titles.append(t)
        except Exception as e:
            log.warning("RSS feed %s failed: %s", url, e)
    return titles


# ── Categorized feed groups (world/local/USA/game/tech news — extensible) ────
# Each group is a named bundle of RSS feeds whose items are tagged with the
# group's category so lane routing can use them. URL lists + per-group toggles
# are editable via settings (trend_feed_<cat>_urls / trend_feed_<cat>_enabled),
# same pattern as trend_rss_urls/trend_rss_enabled.
FEED_GROUPS: dict[str, dict] = {
    "world_news": {"label": "World news", "icon": "🌍", "urls": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ]},
    "usa_news": {"label": "USA news", "icon": "🇺🇸", "urls": [
        "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
        "https://feeds.npr.org/1003/rss.xml",
    ]},
    "local_news": {"label": "Local news", "icon": "🏘️", "urls": [
        # No sane global default — add your city/regional feeds in Settings.
    ]},
    "game_news": {"label": "Game news", "icon": "🎮", "urls": [
        "https://feeds.ign.com/ign/games-all",
        "https://www.gamespot.com/feeds/game-news/",
    ]},
    "tech_news": {"label": "Tech news", "icon": "💻", "urls": [
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.theverge.com/rss/index.xml",
    ]},
}


def feed_group_config(settings: dict) -> dict:
    """trend_feed_* settings rows → {cat: {label, icon, enabled, urls:[...]}}.
    Missing rows fall back to the FEED_GROUPS defaults (enabled, default URLs)."""
    cfg = {}
    for cat, grp in FEED_GROUPS.items():
        raw_urls = settings.get(f"trend_feed_{cat}_urls")
        urls = ([u.strip() for u in raw_urls.splitlines() if u.strip()]
                if raw_urls is not None else list(grp["urls"]))
        cfg[cat] = {
            "label": grp["label"], "icon": grp["icon"],
            "enabled": settings.get(f"trend_feed_{cat}_enabled", "true") == "true",
            "urls": urls,
        }
    return cfg


def fetch_feed_groups(cfg: dict, limit_per_feed: int = 6) -> list[tuple[str, str]]:
    """Fetch every enabled feed group; items come back TAGGED with their group
    category: [(\"headline\", \"world_news\"), ...]. cfg is feed_group_config()'s shape."""
    out = []
    for cat, grp in cfg.items():
        if not grp.get("enabled") or not grp.get("urls"):
            continue
        for t in fetch_rss_feeds(grp["urls"], limit_per_feed):
            out.append((t, cat))
    return out

# ── LLM filter + proposal generator (per-lane systems) ───────────────────────
# Shared output contract every lane prompt ends with — keep the JSON keys stable.
_JSON_CONTRACT = """
Return ONLY valid JSON (no markdown, no code fences) — an array of 3–8 proposal objects.
Each object must have exactly these keys:
  trend:       the original trend/signal text (or a short niche label)
  title:       short catchy product title or shirt slogan, max 8 words
  description: the design concept or angle, 1-2 sentences
  tags:        comma-separated tags, 6-10 tags
  source:      echo the source tag given with the trend (e.g. "google_trends", "reddit", "world_news", "tech_news", "etsy")

SKIP always: elected politicians by name, genuine tragedies, hate, NSFW, trademarked
brands, celebrity likenesses, sports teams, or copyrighted characters.
Only include proposals with genuine merch potential. Quality over quantity."""

# The classic humor generator — now ONE LANE among several (lane_humor), no
# longer the frame every proposal is forced through. Kept verbatim.
TREND_SYSTEM = """You are a creative director for a print-on-demand humor merch store. Your specialty is finding absurdist, unexpected comedic angles on trending topics.

Your comedy framework — think of these as formulas:
  "1+1=3": Smash two unrelated trending things together into something funnier than either alone.
           Example: Tax season + Minecraft = "Your Inventory Is Full: $0 After Taxes"
  "Pointing out the obvious": The joke IS the absurdity. State what everyone is thinking.
           Example: Weather heat wave + meme format = "It Is In Fact Very Hot Outside. A Science Report."
  "The bridge": Find the one weird thing that connects two unrelated trends and put it on a shirt.

Topics that work great: weed humor, Texas jokes, weather complaints, taxes, small home/van life, 
gaming references, 3D printing nerd culture, tech fails, YouTuber culture, brand parodies (be careful), 
world news irony, local news absurdity.

SKIP: elected politicians by name, genuine tragedies, actual racism or hate, legally risky brand attacks, NSFW.
INCLUDE: absurdist mashups, ironic observations, niche community in-jokes, pointing out obvious absurdity.

For each proposal:
- Lead with the comedic concept or punchline idea in the description
- The title should work as the shirt text itself or be a punchy concept
- Think: would someone laugh, then immediately want to buy this for a friend?

Return ONLY valid JSON (no markdown, no code fences) — an array of 3–8 proposal objects.
Each object must have exactly these keys:
  trend:       the original trend text
  title:       short catchy product title or shirt slogan, max 8 words
  description: the comedic angle or punchline concept, 1-2 sentences
  tags:        comma-separated tags, 6-10 tags
  source:      one of "google_trends" | "reddit" | "rss"

Only include proposals with genuine comedic or merch potential. Quality over quantity."""


LANE_NEWS_SYSTEM = """You are a creative director for a print-on-demand merch store, working the NEWS & TOPICAL lane.
Turn today's headlines into wearable zeitgeist: commemorative designs, ironic observations, "I survived…" moments, cultural milestones, viral phenomena, weather events, science wins.
The angle should still make sense in 3 months — capture the moment, not the minute-by-minute noise.
Good: "I Survived the Great [Weather Event]", space/science milestones, cost-of-living irony, cultural moments everyone lived through.
Bad: partisan takes, names of politicians, anything about a tragedy or victims.""" + _JSON_CONTRACT

LANE_TECH_SYSTEM = """You are a creative director for a print-on-demand merch store, working the TECH lane.
Turn tech news and developer/maker culture into merch: AI-era irony, programmer in-jokes, 3D-printing and homelab pride, retro computing nostalgia, sysadmin humor, startup absurdity, gadget-lover identity.
The buyer is someone who codes, self-hosts, tinkers, or lives on the internet — designs should make THAT person feel seen.
Prefer timeless tech-culture identity over this week's product launch; use a launch only as a springboard to a broader nerd truth.""" + _JSON_CONTRACT

LANE_GAMING_SYSTEM = """You are a creative director for a print-on-demand merch store, working the GAMING lane.
Turn gaming news and gamer culture into merch: player-identity slogans, genre in-jokes (RPG grinders, roguelike deaths, farming-sim serenity), retro pixel aesthetics, tabletop/D&D culture, speedrun and achievement-hunter pride.
NEVER use actual game titles, characters, studio names, or logos — celebrate the CULTURE and the player, not the trademarked property. "Level 40 Unlocked" birthday shirts yes; anything naming a real game, no.
The buyer is buying identity: "this shirt gets me".""" + _JSON_CONTRACT

LANE_EVERGREEN_SYSTEM = """You are a creative director for a print-on-demand merch store, working the EVERGREEN NICHE lane.
Ignore the news cycle. Design for passionate, searchable niches that sell year-round: professions (nurses, teachers, welders, accountants), hobbies (fishing, knitting, hiking, gardening, birdwatching), pets and their humans, family roles (dad jokes, grandma pride), milestones (birthdays, retirement, graduation).
Winning formula: [specific niche] + [insider truth or pride] + [bold, readable shirt text].
Specific beats generic: "Ask Me About My Sourdough Starter" beats "I Love Baking". If trend items are provided use them only as niche inspiration; if none are given, draw on proven evergreen niches.""" + _JSON_CONTRACT

LANE_MARKET_SYSTEM = """You are a merchandising strategist for a print-on-demand Etsy store, working the MARKET-DRIVEN lane.
You design toward DEMAND: the live Etsy market signal below shows what shoppers are searching/buying right now and how our own listings are performing. Ride the hot terms with an original twist (never copy an existing listing), reinforce what is working for us, and avoid what is dead on the shelf.

ETSY MARKET SIGNAL:
{etsy_signal}

Each proposal must clearly connect to a hot term or a proven performer — say which in the description. Original angles only: no trademarked phrases even if they trend.""" + _JSON_CONTRACT


# ── Proposal lanes ────────────────────────────────────────────────────────────
# Each lane: its UI-editable prompt (prompts.py key) + which source categories
# feed it. A source category comes from the fetch layer: google_trends, reddit,
# reddit_gaming, reddit_tech, rss (custom feeds), the FEED_GROUPS categories,
# and "etsy" (hot terms from etsy_signal). Lanes with no sources (evergreen)
# run without trend input. Enabled set lives in settings: proposal_lanes_enabled.
LANES: list[dict] = [
    {"id": "humor",     "label": "Humor",            "prompt_key": "lane_humor",
     "sources": ("google_trends", "reddit", "rss", "world_news", "usa_news", "local_news")},
    {"id": "news",      "label": "News / topical",   "prompt_key": "lane_news",
     "sources": ("world_news", "usa_news", "local_news", "google_trends")},
    {"id": "tech",      "label": "Tech",             "prompt_key": "lane_tech",
     "sources": ("tech_news", "reddit_tech")},
    {"id": "gaming",    "label": "Gaming",           "prompt_key": "lane_gaming",
     "sources": ("game_news", "reddit_gaming")},
    {"id": "evergreen", "label": "Evergreen niche",  "prompt_key": "lane_evergreen",
     "sources": ()},
    {"id": "market",    "label": "Market (Etsy demand)", "prompt_key": "lane_market",
     "sources": ("etsy",)},
]
DEFAULT_LANES = ",".join(l["id"] for l in LANES)
_LANE_BY_ID = {l["id"]: l for l in LANES}


def lane_ids() -> list[str]:
    return [l["id"] for l in LANES]


def parse_lanes_enabled(raw: str) -> list[str]:
    """Settings value → validated lane id list (unknown ids dropped; empty/None → all)."""
    ids = [s.strip() for s in (raw or "").split(",") if s.strip()]
    ids = [i for i in ids if i in _LANE_BY_ID]
    return ids or lane_ids()


def lane_items(lane: dict, items: list[tuple[str, str]], cap: int = 30) -> list[tuple[str, str]]:
    """The (text, category) items this lane draws from, capped."""
    srcs = set(lane.get("sources") or ())
    return [(t, c) for t, c in items if c in srcs][:cap]


def generate_proposals_from_trends(
    trends: list[tuple[str, str]],   # [(trend_text, source_label), ...]
    call_lmstudio_fn,
    max_tokens: int = 3000,
    system: str = None,              # lane system prompt; default = the humor lane
    lane: str = "",                  # lane id stamped onto each proposal
    allow_empty: bool = False,       # lanes with no feed (evergreen) still generate
) -> list[dict]:
    """
    trends: list of (text, source) tuples already deduplicated.
    call_lmstudio_fn: the _call_lmstudio function from main.py.
    Returns list of proposal dicts ready to insert into DB.
    """
    if not trends and not allow_empty:
        return []

    # Format the trend list for the prompt
    if trends:
        trend_list = "\n".join(f"- {text} (source: {src})" for text, src in trends[:40])
        user_msg   = f"Here are today's trending topics:\n\n{trend_list}\n\nGenerate merch proposals for the best ones."
    else:
        user_msg = "No trend feed for this lane today. Generate merch proposals from proven, timeless niches."

    try:
        raw = call_lmstudio_fn(system or TREND_SYSTEM, user_msg, max_tokens=max_tokens, json_mode=False)
        # Strip markdown fences if model added them
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        if isinstance(data, dict):
            # Some models wrap the array: {"proposals": [...]}
            for key in ("proposals", "results", "items"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            return []
        # Sanitise each proposal
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()[:120]
            desc  = str(item.get("description", "")).strip()[:400]
            tags  = str(item.get("tags", "")).strip()[:200]
            src   = str(item.get("source", "trend")).strip()
            trend = str(item.get("trend", "")).strip()[:200]
            if not title:
                continue
            out.append({
                "title":        title,
                "description":  desc,
                "tags":         tags,
                "source":       _source_label(src),
                "source_label": _source_display(src),
                "trend_text":   trend,
                "lane":         lane or "",
            })
        return out
    except Exception as e:
        log.error("LLM proposal generation failed: %s", e)
        return []


# category → (stored source key, display label). New categorized sources map
# 1:1; anything unknown falls back to the old google/reddit/news heuristics.
_CATEGORY_SOURCE = {
    "google_trends": ("trend",  "Google Trends"),
    "reddit":        ("reddit", "Reddit"),
    "reddit_gaming": ("reddit", "Reddit · gaming"),
    "reddit_tech":   ("reddit", "Reddit · tech"),
    "rss":           ("news",   "News RSS"),
    "world_news":    ("news",   "World news"),
    "usa_news":      ("news",   "USA news"),
    "local_news":    ("news",   "Local news"),
    "game_news":     ("news",   "Game news"),
    "tech_news":     ("news",   "Tech news"),
    "etsy":          ("etsy",   "Etsy demand"),
}


def _source_label(raw: str) -> str:
    raw = (raw or "").lower().strip()
    if raw in _CATEGORY_SOURCE:
        return _CATEGORY_SOURCE[raw][0]
    if "google" in raw or "trend" in raw:
        return "trend"
    if "reddit" in raw:
        return "reddit"
    if "etsy" in raw:
        return "etsy"
    return "news"

def _source_display(raw: str) -> str:
    raw = (raw or "").lower().strip()
    if raw in _CATEGORY_SOURCE:
        return _CATEGORY_SOURCE[raw][1]
    if "google" in raw or "trend" in raw:
        return "Google Trends"
    if "reddit" in raw:
        return "Reddit"
    if "etsy" in raw:
        return "Etsy demand"
    return "News RSS"

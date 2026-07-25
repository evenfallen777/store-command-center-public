"""Shared kernel: imports, config, DB/setting helpers, clients, LLM helper, prompts.
Everything here is re-exported via `from deps import *` (see __all__ at bottom)."""

import subprocess, os, json, shutil, random, threading, time, hashlib as _hashlib, logging, math
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Optional
import sqlite3

import httpx
import secrets as _secrets
import hmac as _hmac
from db import get_conn, init_db
# Central prompt registry — every LLM system prompt is editable via get_prompt(key).
# Re-exported through `from deps import *` so all routers can call get_prompt(...).
from prompts import get_prompt, list_prompts, set_prompt, reset_prompt, PROMPTS
# Secret-settings encryption at rest (see crypto.py). Re-exported via `from deps import *`.
from crypto import (enc as _enc, dec as _dec, dec_secrets as _dec_secrets,
                    is_secret as _is_secret, SECRET_KEYS, migrate_encrypt_secrets)

from printify import PrintifyClient
from etsy_client import EtsyClient, EtsyAuthError, generate_pkce, build_auth_url, exchange_code, refresh_access_token
from orchestrator import orch
from library import (
    list_sections, list_subsections, list_documents, read_document, search_library,
    add_link, list_links, get_link, review_link, delete_link, update_link,
    render_markdown_simple,
)
from trends import (
    fetch_google_trends, fetch_reddit_rss, fetch_rss_feeds,
    generate_proposals_from_trends, DEFAULT_SUBS, DEFAULT_RSS_FEEDS,
    FEED_GROUPS, LANES, DEFAULT_LANES, feed_group_config, fetch_feed_groups,
    fetch_reddit_rss_tagged, lane_items, parse_lanes_enabled,
)

from config import *

def get_setting(key: str, default=None):
    try:
        c = get_conn()
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        c.close()
        if row and row["value"] not in (None, ""):
            return _dec(row["value"])   # passthrough for non-encrypted values
    except Exception:
        pass
    return default

# Live settings (DB value wins, else config default / env override)

LMSTUDIO_URL = get_setting("llm_url", LLM_URL_DEFAULT)

ENHANCE_MODEL = get_setting("enhance_model", ENHANCE_MODEL_DEFAULT)

DEFAULT_IMAGE_MODEL = get_setting("default_model", DEFAULT_IMAGE_MODEL_DEFAULT)

def calc_retail_price(base_cents: int, margin_pct: float) -> int:
    """Retail price (in cents) that hits the target gross margin, rounded UP to $X.99.

    margin is on the RETAIL price, so the minimum retail is base / (1 - margin). We then
    take the smallest $X.99 price that is >= that minimum. margin_pct is clamped to [1, 99].
    (Behavior-preserving rewrite of the old version — identical output across 50k+ combos,
    guarded by tests/test_pricing.py.)
    """
    margin = min(max(margin_pct, 1), 99) / 100
    min_retail = (base_cents / (1 - margin)) / 100          # dollars needed for the margin
    price = math.floor(min_retail) + 0.99                   # the $X.99 at this integer
    if price < min_retail - 1e-9:                           # not enough → next $X.99 up
        price += 1
    return int(round(price * 100))

_dl_jobs: dict[str, dict] = {}  # filename -> {status, error}
_dl_video_jobs: dict[str, dict] = {}  # model_key -> {status, error}

def _hf_model_key(model_id: str) -> str:
    """Convert 'Org/Name' to HF cache dir key 'Org--Name'."""
    return model_id.replace("/", "--")

def _get_enhance_system() -> str:
    """Return the enhance system prompt from DB or fall back to the hardcoded default."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='enhance_system_prompt'").fetchone()
    conn.close()
    return row["value"] if row and row["value"] else ENHANCE_SYSTEM

ENHANCE_SYSTEM = """You are an expert Stable Diffusion prompt engineer specializing in humor and pop-culture print-on-demand merch art.
Given a rough concept, expand it into a vivid image generation prompt.
Lean hard into any comedic or absurdist angle — exaggerate it, make it visually punchy and immediately readable on a t-shirt.
Think: bold illustration style, strong silhouette, high contrast, instantly recognizable subject.
Include: art style, composition, key visual elements, color palette, mood/tone.
Format: one flowing paragraph, max 120 words. No bullet points. No explanation. Return ONLY the enhanced prompt.
Optimized for apparel and merchandise printing."""

RESEARCH_SYSTEM = """You are a creative research analyst helping build detailed print-on-demand product concepts.
Given a rough idea, dig into: who the characters/subjects are, their visual design and iconic traits,
historical or cultural context, why people love them, what makes a great composition, color palette, art style.
Then craft a detailed image generation prompt capturing all of it.

Return ONLY valid JSON (no markdown, no code fences) with exactly these keys:
  research_summary: 2-3 sentences of context/why it works as merch
  enhanced_prompt: vivid detailed image gen prompt, 100-150 words, optimized for apparel/merchandise
  title: short punchy product title (max 8 words)
  tags: comma-separated tags for search (8-12 tags)

Focus on making it visually striking, recognizable, and sellable as a print product."""

logger = logging.getLogger("store")

# --- Auth core + LLM client: extracted for modularity (deps.py was a god-module). ---
# Bodies moved VERBATIM to auth_core.py and llm_client.py, re-imported here so the
# `from deps import *` public surface is unchanged: these single-underscore names are
# re-bound in deps' namespace and picked up by the __all__ at the bottom of this file.
# Dependency direction is acyclic at import time:
#   deps -> auth_core   (auth_core imports db + stdlib only)
#   deps -> llm_client  (llm_client imports orchestrator at module level; the few
#                        deps-resident settings it reads — get_setting, LMSTUDIO_URL,
#                        ENHANCE_MODEL, DEFAULT_IMAGE_MODEL — are imported lazily inside
#                        its function bodies, so it never imports deps at module load).
from auth_core import (
    _ensure_db, _get_or_create_secret, _pw_hash, _verify_pw,
    _get_stored_hash, _set_stored_hash, _check_password,
    _LOGIN_HTML, _AUTH_BYPASS,
)
from llm_client import (
    _resolve_model, _nsfw_on, _NSFW_PERMIT, _llm_headers, _call_lmstudio,
)

LISTING_SYSTEM = """You are a creative copywriter for a print-on-demand apparel and merchandise store.
Given an image design concept/prompt, produce a short catchy product listing.
Return EXACTLY this format with no extra text, commentary, or markdown:
TITLE: <catchy product title, 50 chars max, human-readable>
DESCRIPTION: <engaging 1-2 sentence product description, 200 chars max>
TAGS: <10-13 comma-separated SEO tags: theme, style, audience, occasion>"""

_trend_scan: dict = {"status": "idle", "message": "", "last_run": None, "last_count": 0}

DEFAULT_PRODUCT_TYPES: list[str] = [
    'T-Shirt','Hoodie','Sweatshirt','Tank Top','Mug','Tumbler',
    'Poster','Sticker','Tote Bag','Phone Case','Mouse Pad','Pillow',
    "Men's Underwear","Women's Underwear","Men's Swim Trunks","Women's Swimsuit",
    'Bumper Sticker','Hat','Beanie','Socks'
]

def _get_printify() -> PrintifyClient:
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    s = _dec_secrets({r["key"]: r["value"] for r in rows})
    # DB setting (Settings tab) wins; fall back to config env var for headless deploys.
    key  = s.get("printify_key") or PRINTIFY_API_KEY
    shop = s.get("printify_shop_id") or PRINTIFY_SHOP_ID
    if not key or not shop:
        raise HTTPException(400, "Printify API key and shop ID required — add them in Settings")
    return PrintifyClient(key, shop)

def _get_etsy_settings() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    return _dec_secrets({r["key"]: r["value"] for r in rows})

CHAIN_PROMPT_SYSTEM = """You are a video storytelling AI. Given a creative concept and number of scenes, generate exactly that many sequential text-to-video prompts that tell a visually continuous story.

Rules:
- Each prompt must be a vivid scene description (1-3 sentences)
- Scenes should flow naturally — same characters, setting, and lighting carry between segments unless the story demands a change
- Use cinematic, present-tense language: camera movement, lighting, action
- Describe what is VISUALLY happening — no narration, no dialogue
- Each scene should feel like a natural continuation of the previous one
- End of scene N should naturally lead to start of scene N+1
- Include enough visual anchors (colors, objects, characters) for continuity
- Prompts should be suitable for text-to-video AI (descriptive, visual, specific)

Return ONLY a JSON array of strings. No other text, no markdown, no explanation."""

import base64, mimetypes

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

RESELL_ANALYZE_PROMPT = """You are a resale pricing expert. Analyze the photo of an item for sale.
{seller_context}
Return ONLY valid JSON (no markdown, no explanation) with these exact keys:
{{
  "title": "short descriptive product name (max 8 words)",
  "category": "eBay category string (e.g. Video Games, Electronics, Clothing)",
  "condition_guess": "New|Like New|Good|Fair|Poor",
  "description": "2-3 sentence listing description, highlight key selling points",
  "price_low": <number, conservative USD price>,
  "price_fair": <number, fair market USD price>,
  "price_high": <number, optimistic USD price>,
  "key_features": ["feature1", "feature2", "feature3"]
}}
Be realistic about prices based on typical resale markets (eBay, Facebook Marketplace, OfferUp)."""

PLATFORM_TEMPLATES = {
    "facebook": "{title} - ${price}\n\nCondition: {condition}\n{defects_line}\n{included_line}\n{description}\n\nDM to purchase. {shipping_line}",
    "offerup":  "{title}\n${price} | {condition}\n\n{description}\n{defects_line}\n{included_line}\n{tags_line}",
    "craigslist": "{title} - ${price}\n\nCondition: {condition}\nCategory: {category}\n\n{description}\n{defects_line}\n{included_line}\n\n{shipping_line} Reply to this post or text to arrange pickup.",
    "mercari":  "{title}\n\nCondition: {condition}\n\n{description}\n{defects_line}\n{included_line}\n\nKey features:\n{features}\n{tags_line}",
}

RESEARCH_PRICE_PROMPT = """You are an expert at pricing used items for local resale (Facebook Marketplace, OfferUp, Craigslist).
Given the item: "{title}" in condition: "{condition}", category: "{category}".

Based on your knowledge of typical resale market prices, provide:
Return ONLY valid JSON with these keys:
{{
  "market_low": <USD number, items sitting long or damaged>,
  "market_fair": <USD number, typical quick-sale price>,
  "market_high": <USD number, patient seller in great condition>,
  "suggested_list": <USD number, what to list at (room to negotiate)>,
  "suggested_minimum": <USD number, lowest you should accept>,
  "price_rationale": "1-2 sentence explanation",
  "sell_fast_tip": "one tip to sell faster",
  "comparable_items": ["example sold item 1", "example sold item 2"]
}}"""

POSTING_AGENT_PROMPT = """Post this item to {platform} marketplace using browser automation.

Item details:
- Title: {title}
- Price: ${price} ({price_mode})
- Condition: {condition}
- Category: {category}
- Description: {description}
- Shipping: {shipping_note}
- Payment: {payment_note}
- Photos (local paths): {photos}

Platform-specific instructions:
{platform_instructions}

After posting successfully, return the listing URL or ID.
If you hit a login screen, stop and report: NEEDS_LOGIN:{platform}
If you encounter a CAPTCHA, stop and report: CAPTCHA:{platform}"""

PLATFORM_INSTRUCTIONS = {
    "facebook": """Navigate to https://www.facebook.com/marketplace/create/item
Fill in: Title, Price, Category, Condition, Description, Location (pickup only unless shipping noted).
Upload photos. Set 'Meet in public' or 'Door pickup' as preferred.
Click Publish/Next until listing goes live. Return the listing URL.""",

    "offerup": """Navigate to https://offerup.com/post
Fill in: Title, Category, Condition, Price, Description, Location.
Upload photos. Set payment + meeting preferences.
Click Post. Return the listing URL.""",

    "craigslist": """Navigate to https://post.craigslist.org
Select 'For Sale by Owner'. Choose appropriate category.
Fill in: Title, Price, Description (include condition + payment info). 
Add photos. Set location. Submit. Return the listing URL or confirmation ID.""",

    "mercari": """Navigate to https://www.mercari.com/sell
Fill in: Photo upload, Title, Category, Brand (if known), Condition, Description, Price, Shipping (if applicable).
Click List. Return the listing URL.""",
}

__all__ = [n for n in dir() if not n.startswith('__')]

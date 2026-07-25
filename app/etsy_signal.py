"""Etsy demand signal — "what's hot / what's not", cached in settings.

Two inputs, both optional and failure-tolerant:
  • The connected Etsy API (services.build_etsy_client → get_shop_stats): our
    OWN listing performance — top performers by views/favorites and the shelf-
    warmers with zero views.
  • A light scrape of Etsy's trending page for market-wide hot search terms
    (opt-in via etsy_signal_scrape_enabled, default off; an Etsy layout change
    just yields an empty list, never an error).

The snapshot is cached as JSON in settings (etsy_signal_cache) with a TTL
(etsy_signal_ttl_min, default 360) so proposal generation and the review gate
can read it constantly without hammering Etsy. Consumers:
  • trends.LANE_MARKET_SYSTEM — {etsy_signal} is filled with signal_text() so
    the market lane designs toward live demand.
  • proposal_gate.review_proposal_sync — appends a MARKET SIGNAL line to the
    judge's concept so marketability scoring sees real demand.

Import-safe: module level touches only stdlib + db; services/httpx are imported
lazily inside functions.
"""
import json
import logging
import re
import time
import urllib.parse

from db import get_conn

log = logging.getLogger("store")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/126.0 Safari/537.36")
_TRENDING_URL = "https://www.etsy.com/market/trending_now"


# ── settings ──────────────────────────────────────────────────────────────────
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


def _int_setting(key, default):
    try:
        return int(float(_setting(key, str(default))))
    except Exception:
        return default


def _save(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


# ── own-listing performance (Etsy API) ────────────────────────────────────────
def _fetch_own_stats() -> dict:
    """Our shop's real numbers via the connected Etsy API. {} when Etsy isn't
    configured / auth is stale (build_etsy_client already handles that quietly)."""
    try:
        from services import build_etsy_client
        client = build_etsy_client()
        if not client:
            return {}
        s = client.get_shop_stats()
        listings = s.get("top_listings", [])
        top = [{"title": (l.get("title") or "")[:90],
                "views": l.get("views", 0) or 0,
                "favorites": l.get("num_favorers", 0) or 0} for l in listings[:10]]
        return {
            "top": [t for t in top if t["views"] or t["favorites"]],
            "cold": [t["title"] for t in top if not t["views"] and not t["favorites"]][:5],
            "listing_count": s.get("listing_count", 0),
            "total_views": s.get("total_views", 0),
            "total_favorites": s.get("total_favorites", 0),
            "sold": s.get("transaction_sold_count", 0),
        }
    except Exception as e:
        log.info("etsy signal: own-stats fetch skipped: %s", e)
        return {}


# ── market-wide trending terms (guarded scrape) ───────────────────────────────
def parse_trending_html(html: str) -> list[str]:
    """Pull candidate trending search terms out of an Etsy market page.
    Deliberately layout-agnostic: /market/<slug> hrefs are the most stable
    artifact across Etsy redesigns. Pure function → unit-testable offline."""
    terms: list[str] = []
    for m in re.finditer(r'href="[^"]*/market/([a-zA-Z0-9_\-%]+)', html or ""):
        slug = urllib.parse.unquote(m.group(1)).replace("_", " ").replace("-", " ").strip().lower()
        if slug == "trending now":
            continue
        if 3 <= len(slug) <= 60 and slug not in terms:
            terms.append(slug)
    return terms[:40]


def _scrape_trending() -> list[str]:
    """Opt-in (etsy_signal_scrape_enabled=1) fetch of Etsy's trending page.
    Any failure — blocked, redesigned, offline — just returns []."""
    if str(_setting("etsy_signal_scrape_enabled", "0")).strip() != "1":
        return []
    try:
        import httpx
        r = httpx.get(_TRENDING_URL, timeout=15, follow_redirects=True,
                      headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code != 200:
            log.info("etsy signal: trending page HTTP %s — skipping", r.status_code)
            return []
        return parse_trending_html(r.text)
    except Exception as e:
        log.info("etsy signal: trending scrape skipped: %s", e)
        return []


# our top titles → the keywords that are actually pulling views
_STOP = {"shirt", "tshirt", "tee", "shirts", "gift", "gifts", "mug", "poster",
         "sticker", "hoodie", "sweatshirt", "funny", "cute", "custom", "with",
         "your", "this", "that", "from", "made", "design", "print", "unisex"}


def _title_terms(titles: list[str], cap: int = 10) -> list[str]:
    freq: dict[str, int] = {}
    for t in titles:
        for w in re.findall(r"[a-zA-Z][a-zA-Z']{3,}", (t or "").lower()):
            if w not in _STOP:
                freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))][:cap]


# ── the snapshot ──────────────────────────────────────────────────────────────
def refresh() -> dict:
    """Rebuild + cache the demand snapshot. Never raises."""
    own = _fetch_own_stats()
    hot = _scrape_trending()
    for t in _title_terms([x["title"] for x in own.get("top", [])]):
        if t not in hot:
            hot.append(t)
    snap = {"fetched_at": int(time.time()), "hot_terms": hot[:40], "own": own}
    try:
        _save("etsy_signal_cache", json.dumps(snap))
    except Exception as e:
        log.warning("etsy signal: cache write failed: %s", e)
    return snap


def snapshot(max_age_min: int = None) -> dict:
    """The cached demand snapshot, refreshed when older than the TTL
    (etsy_signal_ttl_min, default 360). Never raises; may be {} on first run
    with nothing configured."""
    ttl = max_age_min if max_age_min is not None else _int_setting("etsy_signal_ttl_min", 360)
    try:
        snap = json.loads(_setting("etsy_signal_cache", "") or "{}")
    except Exception:
        snap = {}
    if snap.get("fetched_at") and time.time() - snap["fetched_at"] < max(1, ttl) * 60:
        return snap
    try:
        return refresh()
    except Exception as e:
        log.warning("etsy signal: refresh failed: %s", e)
        return snap


def signal_text(snap: dict = None, max_len: int = 600) -> str:
    """Human/LLM-readable one-paragraph brief of the snapshot; "" when there is
    no signal at all (nothing configured, nothing cached)."""
    snap = snap if snap is not None else snapshot()
    if not snap:
        return ""
    bits = []
    hot = snap.get("hot_terms") or []
    if hot:
        bits.append("HOT on Etsy now: " + ", ".join(hot[:12]))
    own = snap.get("own") or {}
    top = [t for t in own.get("top", []) if t.get("views") or t.get("favorites")]
    if top:
        bits.append("Our best performers: " + "; ".join(
            f"{t['title'][:50]} ({t.get('views', 0)} views, {t.get('favorites', 0)} favs)"
            for t in top[:3]))
    cold = own.get("cold") or []
    if cold:
        bits.append("NOT moving for us: " + "; ".join(t[:50] for t in cold[:3]))
    if own.get("sold"):
        bits.append(f"Lifetime sales: {own['sold']}")
    return ". ".join(bits)[:max_len]

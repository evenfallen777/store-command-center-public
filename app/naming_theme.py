"""
The Company — NAMING THEME: a display-label layer over the god-tier roles.

One setting, `naming_theme` (settings table, via the normal PATCH /api/settings
flow), picks how the operator/security tier is PRESENTED to the user:

  themed  (DEFAULT) — the current look, exactly: 🌟 God, ✝️ Jesus, 😈 Satan,
                      🔴 Red Team / 🔵 Blue Team, ⛪ Church, 🍺 Bar, 🔞 store.
  neutral           — a professional presentation of the SAME system: Owner,
                      Constructive/Adversarial Lieutenant, Advisory Hall, …

STRICTLY COSMETIC. Nothing here (or downstream of here) renames an internal
ID, an API actor (actor='jesus'/'satan'), a settings key (world_jesus_enabled,
world_satan_enabled, …), an agent_name in the security known-actor registry
("Jesus"/"Satan" in aishield._known_agents), a table, or an endpoint. Only the
strings the USER SEES change; every gate and hard floor is untouched, and with
the setting at its default the rendered output is byte-identical to today.

The resolver is a plain dict-of-dicts so a future CUSTOM map (e.g. a
user-supplied THEMES entry loaded from a setting) drops in without touching
any call site — only `themed` and `neutral` ship today.

API:
  theme()            → the active theme id ('themed' | 'neutral')
  label(role_id)     → display name for a canonical role/venue id
  icon(role_id)      → its icon (may be '')
  display(role_id)   → "icon label" (or just the label when icon is '')
  pick(themed, neutral) → literal passthrough: the exact current string in
                       themed mode, the neutral wording otherwise. Call sites
                       use this to GUARANTEE the default output is unchanged.
  client_state()     → {"theme", "themes", "map"} for the frontend
                       (GET /api/world/theme + /api/world/state).
"""
from deps import get_setting

SETTING_KEY = "naming_theme"
DEFAULT_THEME = "themed"

# canonical role/venue id → {label, icon} per theme.
# The `themed` side IS today's strings — do not "improve" it.
THEMES = {
    "themed": {
        "god":        {"label": "God",         "icon": "🌟"},
        "jesus":      {"label": "Jesus",       "icon": "✝️"},
        "satan":      {"label": "Satan",       "icon": "😈"},
        "redteam":    {"label": "Red Team",    "icon": "🔴"},
        "blueteam":   {"label": "Blue Team",   "icon": "🔵"},
        "church":     {"label": "Church",      "icon": "⛪"},
        "bar":        {"label": "Bar",         "icon": "🍺"},
        "nsfw_store": {"label": "Video Store", "icon": "🔞"},
    },
    "neutral": {
        "god":        {"label": "Owner",                   "icon": "🌟"},
        "jesus":      {"label": "Constructive Lieutenant", "icon": "🔺"},
        "satan":      {"label": "Adversarial Lieutenant",  "icon": "🔻"},
        "redteam":    {"label": "Red Team (audit)",        "icon": "🔴"},
        "blueteam":   {"label": "Blue Team (fix)",         "icon": "🔵"},
        "church":     {"label": "Advisory Hall",           "icon": "🧭"},
        "bar":        {"label": "Social Lounge",           "icon": "🛋️"},
        "nsfw_store": {"label": "Private Studio",          "icon": "🎬"},
    },
}


def theme():
    """The active naming theme; unknown/unset values fall back to the default
    (themed) so a bad write can never change what a default install shows."""
    try:
        t = (get_setting(SETTING_KEY, DEFAULT_THEME) or DEFAULT_THEME).strip().lower()
    except Exception:
        return DEFAULT_THEME
    return t if t in THEMES else DEFAULT_THEME


def _entry(role_id, thm=None):
    m = THEMES.get(thm or theme()) or THEMES[DEFAULT_THEME]
    return m.get(role_id) or THEMES[DEFAULT_THEME].get(role_id) or {"label": role_id, "icon": ""}


def label(role_id, thm=None):
    return _entry(role_id, thm)["label"]


def icon(role_id, thm=None):
    return _entry(role_id, thm)["icon"]


def display(role_id, thm=None):
    e = _entry(role_id, thm)
    return f"{e['icon']} {e['label']}".strip()


def pick(themed, neutral):
    """Literal string passthrough: the EXACT current string while the theme is
    at its default, the neutral wording otherwise. Any future theme that isn't
    'neutral' also gets the themed original (safe fallback)."""
    return neutral if theme() == "neutral" else themed


def client_state():
    """What the frontend needs: the active theme + its resolved role map."""
    t = theme()
    return {"theme": t, "themes": sorted(THEMES), "map": THEMES[t]}

"""Interface every social publishing adapter implements.

Adapters are stateless facades over a platform API: credentials live in the
settings table (encrypted via app/crypto.py), upload progress lives in the
social_publications table (owned by routers/social.py). Keeping this surface
tiny means adding TikTok/Instagram later is "write one module, register it".
"""


class PublishAdapter:
    """Contract for a platform publisher (YouTube, TikTok, …)."""

    platform: str = ""   # e.g. "youtube"

    def connected(self) -> bool:
        """True when OAuth tokens for this platform are stored."""
        raise NotImplementedError

    def auth_url(self, state: str) -> str:
        """The provider's authorize URL for the OAuth connect popup.
        Raises ValueError with a human message if prerequisites (client id)
        aren't configured yet."""
        raise NotImplementedError

    def upload(self, video_path: str, meta: dict) -> dict:
        """Upload a local video file. `meta` keys (all optional unless noted):
        title (required), description, tags (list[str]), privacy,
        width/height (for aspect warnings).

        Returns {platform_post_id, url, state (json-able resume info),
        warnings (list[str])}. Raises on failure — the caller records the
        error on the social_publications row."""
        raise NotImplementedError

    def status(self, platform_post_id: str) -> dict:
        """Post-upload processing status for a published item.
        Returns {status: processing|live|failed, detail: str}."""
        raise NotImplementedError

    def stats(self, platform_post_id: str, **kwargs) -> dict:
        """OPTIONAL performance stats for a live item (analytics → taste model).
        Returns {available: True, views, likes, comments, …} or
        {available: False, detail: str}. Implementations must be best-effort:
        prefer returning available=False over raising, so a platform whose
        stats API is gated (e.g. unaudited TikTok apps) never breaks the
        analytics pipeline."""
        return {"available": False, "detail": f"{self.platform or 'platform'} has no stats support"}

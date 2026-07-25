"""Social auto-publishing adapters (Phase 1: YouTube).

One module per platform, each implementing social_publish.base.PublishAdapter:
connected() / auth_url() / upload(video_path, meta) / status(id). Routers
(routers/social.py) own the HTTP endpoints + DB rows (social_publications);
adapters own the platform API talk only.
"""

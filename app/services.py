"""Background jobs and service functions (image/video/chain generation, publishing, geo, agent posting)."""

import signal
from deps import *

# Video + audio generation moved to services_media.py (kept importable here).
from services_media import *
# 3D pipeline + geo helpers split out for size; re-exported so `from services import *`
# is unchanged (identical export surface).
from services_3d import *
from geo_util import *


def _run_trend_scan():
    """Multi-lane trend scan: fetch every enabled source (Google/Reddit/custom
    RSS + categorized feed groups + the Etsy demand signal), route items into
    the enabled proposal LANES (trends.LANES), and run each lane's own prompt
    as its own orchestrator LLM task (task=<lane prompt key>, so the per-task
    model picker applies). Proposals insert lane-tagged as each lane finishes."""
    global _trend_scan
    _trend_scan = {"status": "running", "message": "Starting scan…", "last_run": None, "last_count": 0}

    conn = get_conn()
    rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE 'trend_%'").fetchall()
    lanes_raw = conn.execute("SELECT value FROM settings WHERE key='proposal_lanes_enabled'").fetchone()
    conn.close()
    cfg = {r["key"]: r["value"] for r in rows}

    google_on  = cfg.get("trend_google_enabled",  "true") == "true"
    reddit_on  = cfg.get("trend_reddit_enabled",  "true") == "true"
    rss_on     = cfg.get("trend_rss_enabled",     "true") == "true"
    region     = cfg.get("trend_google_region",   "US")
    subs       = [s.strip() for s in cfg.get("trend_reddit_subs", ",".join(DEFAULT_SUBS)).split(",") if s.strip()]
    rss_urls   = [u.strip() for u in cfg.get("trend_rss_urls", "\n".join(DEFAULT_RSS_FEEDS)).splitlines() if u.strip()]

    import trends as _trends
    lanes_on = _trends.parse_lanes_enabled(lanes_raw["value"] if lanes_raw else "")

    raw: list[tuple[str, str]] = []      # (text, source category)

    if google_on:
        _trend_scan["message"] = "🔍 Fetching Google Trends…"
        for t in fetch_google_trends(region=region):
            raw.append((t, "google_trends"))

    if reddit_on:
        _trend_scan["message"] = "🗨 Fetching Reddit RSS…"
        raw.extend(fetch_reddit_rss_tagged(subs))

    if rss_on:
        _trend_scan["message"] = "📰 Fetching RSS feeds…"
        for t in fetch_rss_feeds(rss_urls):
            raw.append((t, "rss"))

    _trend_scan["message"] = "🗞 Fetching categorized news feeds…"
    try:
        raw.extend(fetch_feed_groups(feed_group_config(cfg)))
    except Exception as e:
        logger.warning("feed groups fetch failed: %s", e)

    # Etsy demand signal → the market lane's trend items + the {etsy_signal} brief
    etsy_brief = ""
    try:
        import etsy_signal
        _trend_scan["message"] = "🛍 Reading the Etsy demand signal…"
        snap = etsy_signal.snapshot()
        etsy_brief = etsy_signal.signal_text(snap)
        for term in (snap.get("hot_terms") or [])[:20]:
            raw.append((term, "etsy"))
    except Exception as e:
        logger.warning("etsy signal read failed: %s", e)

    # Which lanes can actually run this pass?
    lane_runs = []                        # (lane dict, items)
    for lane in LANES:
        if lane["id"] not in lanes_on:
            continue
        items = lane_items(lane, raw)
        if items or not lane["sources"]:  # source-less lanes (evergreen) always run
            lane_runs.append((lane, items))
    if not lane_runs:
        _trend_scan = {"status": "idle", "message": "No trends fetched — check sources/lanes", "last_run": None, "last_count": 0}
        return

    def _insert(proposals):
        """Insert one lane's proposals, skipping near-duplicates of recent ones."""
        n = 0
        conn = get_conn()
        recent_titles = set(
            row[0].lower() for row in
            conn.execute("SELECT title FROM proposals WHERE created_at > datetime('now','-14 days')").fetchall()
        )
        for p in proposals:
            title_words = set(p["title"].lower().split())
            if any(len(title_words & set(rt.split())) >= 4 for rt in recent_titles):
                continue
            try:
                conn.execute(
                    "INSERT INTO proposals (title,description,source,source_label,tags,lane) VALUES (?,?,?,?,?,?)",
                    (p["title"], p["description"], p["source"], p["source_label"],
                     p.get("tags", ""), p.get("lane", ""))
                )
                recent_titles.add(p["title"].lower())
                n += 1
            except Exception:
                pass
        conn.commit()
        conn.close()
        return n

    count, failed = 0, []
    for lane, items in lane_runs:
        _trend_scan["message"] = f"🤖 {lane['label']} lane: evaluating {len(items)} item(s)…"
        system = get_prompt(lane["prompt_key"])
        if lane["id"] == "market":
            system = system.replace("{etsy_signal}", etsy_brief or "(no signal available — fall back to broad Etsy best-seller intuition)")

        def _llm_work(items=items, system=system, lane_id=lane["id"], allow_empty=not lane["sources"]):
            return generate_proposals_from_trends(items, _call_lmstudio, system=system,
                                                  lane=lane_id, allow_empty=allow_empty)

        task_id = orch.submit_llm(_llm_work, desc=f"Trend scan: {lane['label']} lane",
                                  priority=2, task=lane["prompt_key"])   # background
        # Block until this lane is done (we're already in a background thread)
        for _ in range(180):
            t = orch.poll(task_id)
            if t["status"] == "done":
                count += _insert(t["result"] or [])
                break
            if t["status"] in ("error", "cancelled"):
                failed.append(lane["id"])
                break
            time.sleep(1)

    from datetime import datetime
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("trend_last_run",  now))
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("trend_last_count", str(count)))
    conn.commit()
    conn.close()

    msg = f"✓ Scan complete — {count} proposal(s) added across {len(lane_runs)} lane(s)"
    if failed:
        msg += f" (failed lanes: {', '.join(failed)})"
    _trend_scan = {
        "status":     "idle",
        "message":    msg,
        "last_run":   now,
        "last_count": count,
    }

def _do_publish(design_id, title, description, tags, product_type, image_path, retail_price_cents=None):
    try:
        # Convert tags string to list for Printify API
        tag_list = [t.strip() for t in (tags or "").split(',') if t.strip()]
        conn = get_conn()
        # Final guard (background thread) — re-check before hitting Printify
        already = conn.execute(
            "SELECT id, printify_id FROM designs WHERE image_path=? AND product_type=? AND status='published' AND printify_id IS NOT NULL",
            (image_path, product_type)
        ).fetchone()
        conn.close()
        if already:
            logger.warning("Skipping duplicate publish: %s %s already live as design #%s / printify %s",
                        product_type, image_path, already["id"], already["printify_id"])
            return

        client = _get_printify()

        # Reuse existing Printify image_id if already uploaded — avoids re-uploading
        # when publishing a second product type for the same design (T-Shirt→Hoodie etc.)
        conn2 = get_conn()
        existing_img = conn2.execute(
            "SELECT printify_image_id FROM designs WHERE image_path=? AND printify_image_id IS NOT NULL LIMIT 1",
            (image_path,)
        ).fetchone()
        conn2.close()

        if existing_img and existing_img["printify_image_id"]:
            printify_image_id = existing_img["printify_image_id"]
            logger.info("Reusing existing Printify image_id %s for %s", printify_image_id, image_path)
        else:
            img_data = client.upload_image(image_path, f"design_{design_id}.png")
            printify_image_id = img_data["id"]

        product = client.create_product(title, description, printify_image_id, product_type, tag_list,
                                         retail_price_cents=retail_price_cents)
        client.publish_product(product["id"])

        conn = get_conn()
        # Find the design row for this image+type to update, or the source row to clone
        target = conn.execute(
            "SELECT id FROM designs WHERE image_path=? AND product_type=?",
            (image_path, product_type)
        ).fetchone()
        if target:
            # Row already exists for this type — just stamp it published
            conn.execute(
                "UPDATE designs SET printify_id=?,printify_image_id=?,status='published',updated_at=datetime('now') WHERE id=?",
                (product["id"], printify_image_id, target["id"])
            )
        else:
            # New type for this image — insert sibling row; use NULL generation_id to avoid
            # the UNIQUE constraint on that column (SQLite allows multiple NULLs in UNIQUE cols)
            conn.execute(
                """INSERT INTO designs (generation_id,image_path,prompt,product_type,status,printify_id,printify_image_id)
                   SELECT NULL,image_path,prompt,?,'published',?,?
                   FROM designs WHERE id=?""",
                (product_type, product["id"], printify_image_id, design_id)
            )
        # Backfill printify_image_id on all rows sharing this image path
        conn.execute(
            "UPDATE designs SET printify_image_id=? WHERE image_path=? AND printify_image_id IS NULL",
            (printify_image_id, image_path)
        )
        conn.commit()
        conn.close()
        logger.info("Published to Printify: %s (%s) → %s", title, product_type, product["id"])
    except Exception as e:
        logger.error("Printify publish error: %s", e)

def build_etsy_client():
    """A ready EtsyClient from settings (token refreshed + persisted if expiring).
    Returns None if Etsy isn't configured OR its auth is stale (reconnect needed) —
    stale auth is logged once by etsy_client.mark_auth_stale, then callers get a
    quiet None instead of raw 400s. Shared by publish + revenue sync."""
    import etsy_client as _ec
    s = _get_etsy_settings()
    key, access_token = s.get("etsy_key", ""), s.get("etsy_access_token", "")
    shop_id, refresh_tok = s.get("etsy_shop_id", ""), s.get("etsy_refresh_token", "")
    if not key or not access_token or not shop_id:
        return None
    secret = s.get("etsy_shared_secret", "")
    try:
        expires_at = int(s.get("etsy_token_expires", "0") or 0)
    except Exception:
        expires_at = 0
    if time.time() >= expires_at - 120 and refresh_tok:
        if _ec.auth_needs_reconnect():
            return None   # known-dead refresh token — don't re-spam Etsy or the log
        try:
            tokens = refresh_access_token(key, refresh_tok, client_secret=secret or None)
            access_token = tokens["access_token"]
            new_exp = int(time.time()) + tokens.get("expires_in", 3600)
            conn = get_conn()
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_access_token", _enc(access_token)))
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_token_expires", str(new_exp)))
            conn.commit(); conn.close()
        except EtsyAuthError:
            return None   # already flagged + logged once inside refresh_access_token
        except Exception as e:
            logger.warning("etsy token refresh failed: %s", e)
    return EtsyClient(key, access_token, shop_id, shared_secret=secret)


def _do_etsy_publish(design_id, title, description, tags, price, product_type, image_path):
    try:
        # Guard: if design is already published to Printify, Etsy gets it via sales channel sync —
        # creating a direct listing on top would duplicate it.
        _guard_conn = get_conn()
        _guard_row  = _guard_conn.execute(
            "SELECT printify_id, etsy_listing_id FROM designs WHERE id=?", (design_id,)
        ).fetchone()
        _guard_conn.close()
        if _guard_row and _guard_row["printify_id"]:
            logger.warning(
                "Etsy direct publish blocked for design #%s — already on Etsy via Printify "
                "sales channel sync (printify_id=%s). Skipping to prevent duplicate listing.",
                design_id, _guard_row["printify_id"]
            )
            return
        if _guard_row and _guard_row["etsy_listing_id"]:
            logger.warning(
                "Etsy direct publish skipped for design #%s — etsy_listing_id already set: %s",
                design_id, _guard_row["etsy_listing_id"]
            )
            return
        # Convert tags string to list for Etsy API
        tag_list = [t.strip() for t in (tags or "").split(',') if t.strip()]
        s            = _get_etsy_settings()
        key          = s.get("etsy_key", "")
        access_token = s.get("etsy_access_token", "")
        refresh_tok  = s.get("etsy_refresh_token", "")
        expires_at   = int(s.get("etsy_token_expires", "0"))
        shop_id      = s.get("etsy_shop_id", "")
        if not key or not access_token or not shop_id:
            raise ValueError("Etsy not configured (key / token / shop missing)")
        secret = s.get("etsy_shared_secret", "")
        # Refresh token if expired or expiring soon
        if time.time() >= expires_at - 120 and refresh_tok:
            tokens       = refresh_access_token(key, refresh_tok, client_secret=secret or None)
            access_token = tokens["access_token"]
            new_exp      = int(time.time()) + tokens.get("expires_in", 3600)
            conn = get_conn()
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_access_token", _enc(access_token)))
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", ("etsy_token_expires", str(new_exp)))
            conn.commit()
            conn.close()
        client  = EtsyClient(key, access_token, shop_id, shared_secret=secret)
        listing = client.create_draft_listing(title, description, price, tag_list, product_type)
        lid     = listing["listing_id"]
        # Upload the Etsy-spec variant (1024²/≤5MB JPEG), not the raw master — a
        # 4096²/20MB PNG is over Etsy's limits and gets rejected. Fall back to the
        # master if derivation fails for any reason.
        try:
            import image_sizes
            etsy_img = image_sizes.one(image_path, "etsy")
        except Exception:
            etsy_img = None
        client.upload_listing_image(lid, str(etsy_img) if etsy_img else image_path)
        # Take the draft LIVE (toggle etsy_auto_activate, default on). Best-effort: Etsy
        # rejects activation if the shop lacks a shipping profile etc. — stay draft then.
        if get_setting("etsy_auto_activate", "1") == "1":
            try:
                client.update_listing(lid, state="active")
                logger.info("Etsy listing %s activated (live)", lid)
            except Exception as ae:
                logger.warning("Etsy listing %s stayed draft (activate failed: %s)", lid, ae)
        conn = get_conn()
        conn.execute(
            "UPDATE designs SET etsy_listing_id=?,updated_at=datetime('now') WHERE id=?",
            (str(lid), design_id)
        )
        conn.commit()
        conn.close()
        logger.info("Published to Etsy: '%s' (%s) → listing %s", title, product_type, lid)
    except EtsyAuthError:
        # Stale OAuth — already flagged + logged ONCE by etsy_client.mark_auth_stale.
        # Stay quiet here; the UI surfaces the 'Reconnect Etsy' CTA from /api/etsy/status.
        pass
    except Exception as e:
        logger.error("Etsy publish error: %s", e)

def run_generation(gen_id: int):
    # Acquire GPU: waits for LLM to finish, unloads LLM, marks image busy
    orch.image_acquire()

    conn = get_conn()
    row  = conn.execute("SELECT * FROM generations WHERE id=?", (gen_id,)).fetchone()
    if not row:
        conn.close()
        orch.image_release()
        return

    conn.execute(
        "UPDATE generations SET status='generating',updated_at=datetime('now') WHERE id=?", (gen_id,)
    )
    conn.commit()

    out_path = DESIGNS_PENDING / f"gen_{gen_id}_{int(datetime.now().timestamp())}.png"
    try:
        seed   = str(random.randint(1, 2**31 - 1))
        model_name = row["model"] if row["model"] else DEFAULT_IMAGE_MODEL
        # Pick the right specialty model for this product type: LoRA (sticker/line-art/…),
        # upscaler (print-quality tees/posters), + a prompt nudge. Gated on what's
        # installed, so this is a no-op until those models are downloaded.
        import gen_models
        sel = gen_models.resolve(row["product_type"] or "T-Shirt")
        gen_prompt = row["prompt"] + (", " + sel["prompt_add"] if sel["prompt_add"] else "")
        result = subprocess.run(
            [str(GENERATE_SCRIPT), gen_prompt, str(out_path),
             str(row["width"] or 1024), str(row["height"] or 1024),
             str(row["steps"] or 20), seed, model_name, sel["lora"], sel["upscale"], sel.get("matte", "")],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0 and out_path.exists():
            # transparent cutout for stickers: BiRefNet does it IN the workflow (sel.matte);
            # fall back to the Python flood-fill only when no matte model is installed.
            if sel["cutout"] and not sel.get("matte"):
                try:
                    import img_cutout
                    img_cutout.knockout(str(out_path))
                except Exception as _e:
                    logger.warning("cutout skipped for gen %d: %s", gen_id, _e)
            conn.execute(
                "UPDATE generations SET status='done',image_path=?,updated_at=datetime('now') WHERE id=?",
                (str(out_path), gen_id)
            )
            gen_source = row["source"] if row["source"] else "pipeline"
            # Propagate the nsfw flag (+ category) onto the design row so it stays out
            # of every regular gallery and lives only in the Private Studio tab.
            gen_nsfw = row["nsfw"] if "nsfw" in row.keys() else 0
            gen_nsfw_cat = row["nsfw_category"] if "nsfw_category" in row.keys() else None
            conn.execute(
                "INSERT INTO designs (generation_id,image_path,prompt,product_type,source,nsfw,nsfw_category) VALUES (?,?,?,?,?,?,?)",
                (gen_id, str(out_path), row["prompt"], row["product_type"] or "T-Shirt", gen_source, gen_nsfw or 0, gen_nsfw_cat)
            )
            # Auto-derive publish-ready sizes (Etsy 1024²/≤5MB + web/medium) from the
            # master so a 4096²/20MB render is immediately usable. Best-effort — the
            # module swallows its own errors, but guard the import too.
            try:
                import image_sizes
                image_sizes.derive(out_path)
            except Exception as _e:
                logger.warning("size derive skipped for gen %d: %s", gen_id, _e)
        else:
            err_msg = (result.stderr or "")[:300] if result.returncode != 0 else "output file missing"
            logger.error("Generation %d failed (rc=%d): %s", gen_id, result.returncode, err_msg)
            conn.execute(
                "UPDATE generations SET status='failed',updated_at=datetime('now') WHERE id=?", (gen_id,)
            )
    except Exception as ex:
        logger.error("Generation %d exception: %s", gen_id, ex)
        conn.execute(
            "UPDATE generations SET status='failed',updated_at=datetime('now') WHERE id=?", (gen_id,)
        )
    finally:
        conn.commit()
        conn.close()
        orch.image_release()   # release GPU FIRST — always, before anything else
        # If proposal has no successful/pending generations, reset it to pending
        # so it reappears in the queue for retry
        try:
            proposal_id = row["proposal_id"]
        except Exception:
            proposal_id = None
        if proposal_id:
            try:
                conn2 = get_conn()
                remaining = conn2.execute(
                    "SELECT COUNT(*) FROM generations WHERE proposal_id=? AND status IN ('pending','generating','done')",
                    (proposal_id,)
                ).fetchone()[0]
                if remaining == 0:
                    conn2.execute(
                        "UPDATE proposals SET status='pending',updated_at=datetime('now') WHERE id=?",
                        (proposal_id,)
                    )
                    logger.info("All generations failed for proposal %d — reset to pending", proposal_id)
                    conn2.commit()
                conn2.close()
            except Exception as ex:
                logger.error("Proposal reset error: %s", ex)


def _do_post_via_agent(task_id: int, lid: int, platforms: list[str]):
    """Background thread: call openclaw agent to browser-post to each platform."""
    conn = get_conn()
    conn.execute("UPDATE resell_auto_tasks SET status='running' WHERE id=?", (task_id,))
    conn.commit()

    # Fetch listing + photos
    row = conn.execute("SELECT * FROM resell_listings WHERE id=?", (lid,)).fetchone()
    if not row:
        conn.execute("UPDATE resell_auto_tasks SET status='failed',error='listing not found' WHERE id=?", (task_id,))
        conn.commit(); conn.close(); return

    photos = conn.execute(
        "SELECT image_path FROM resell_listing_images WHERE listing_id=? ORDER BY is_primary DESC", (lid,)
    ).fetchall()
    photo_paths = [str(BASE / "static" / p["image_path"]) for p in photos]

    price_mode_labels = {"firm": "firm price, no negotiation", "obo": "or best offer", "haggle": "negotiable"}
    ship_map = {"never": "NO SHIPPING — local pickup only",
                "pickup_only": "local pickup only",
                "possible": "possible if buyer covers shipping cost"}

    results = {}
    for platform in platforms:
        prompt = POSTING_AGENT_PROMPT.format(
            platform=platform.title(),
            title=row["title"],
            price=f"{row['asking_price']:.2f}" if row["asking_price"] else "TBD",
            price_mode=price_mode_labels.get(row["price_mode"] or "obo", "negotiable"),
            condition=row["condition"] or "Good",
            category=row["category"] or "Other",
            description=row["description"] or "",
            shipping_note=ship_map.get(row["shipping_policy"] or "pickup_only", "local pickup only"),
            payment_note=", ".join(json.loads(row["payment_methods"] or '["cash"]')),
            photos=", ".join(photo_paths[:4]) if photo_paths else "none",
            platform_instructions=PLATFORM_INSTRUCTIONS.get(platform, "Post to the platform as normal."),
        )
        try:
            result = subprocess.run(
                [OPENCLAW_BIN, "agent", "--agent", OPENCLAW_AGENT, "--json"],
                input=prompt, capture_output=True, text=True, timeout=300
            )
            output = result.stdout.strip()
            if "NEEDS_LOGIN" in output:
                results[platform] = {"status": "needs_login", "message": output}
            elif "CAPTCHA" in output:
                results[platform] = {"status": "captcha", "message": output}
            else:
                results[platform] = {"status": "posted", "output": output[:500]}
                # Try to update listing platforms JSON
                try:
                    plats = json.loads(conn.execute(
                        "SELECT platforms FROM resell_listings WHERE id=?", (lid,)
                    ).fetchone()["platforms"] or "{}")
                    plats[platform] = {"status": "posted", "posted_at": datetime.utcnow().isoformat(), "output": output[:200]}
                    conn.execute("UPDATE resell_listings SET platforms=?,status='listed',updated_at=datetime('now') WHERE id=?",
                                 (json.dumps(plats), lid))
                    conn.commit()
                except Exception:
                    pass
        except subprocess.TimeoutExpired:
            results[platform] = {"status": "timeout"}
        except Exception as ex:
            results[platform] = {"status": "error", "message": str(ex)}

    conn.execute("UPDATE resell_auto_tasks SET status='done',result=? WHERE id=?",
                 (json.dumps(results), task_id))
    conn.commit()
    conn.close()


__all__ = [n for n in dir() if not n.startswith('__')]

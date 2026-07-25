"""
The Company — DUALITY: the Jesus/Satan hi-lo band around every single number.

The owner ("God") manages two lieutenants at the operator tier:
  ✝️ Jesus (world_jesus.py) — the CONSTRUCTIVE, best-case, optimistic side.
  😈 Satan (world_satan.py) — the ADVERSARIAL, worst-case, red-team side.

Every prediction / review / forecast used to be a single optimistic number.
This module turns that number into a calibrated RANGE — "a 10 to a 0":

    {best, worst, expected}

  best     — the Jesus anchor: how good it plausibly gets.
  worst    — the Satan anchor: how bad it plausibly gets.
  expected — the calibrated middle the owner should actually plan around.

GRACEFUL BY DESIGN: each side of the band exists only while its lieutenant is
switched on (enabled OR its observe-only shadow). With a side off, that side
falls back to the single-sided value; with both off, band() degrades to the
plain input — it NEVER errors and NEVER changes behavior for anyone who hasn't
turned the lieutenants on (everything ships default OFF).

READ-ONLY: nothing in this module mutates state, moves money, or touches any
gate. It computes numbers and composes text. The hard floors (minors/CSAM +
real-person deepfake/NCII detection, ALWAYS_GATE money kinds) live elsewhere
and are not reachable from here.
"""
import logging

logger = logging.getLogger("store")


# ── which sides of the band are live ─────────────────────────────────────────
def _jesus_side_on():
    """The optimistic anchor is available when Jesus is enabled or shadowing."""
    try:
        import world_jesus
        return world_jesus.enabled() or world_jesus.shadow_on()
    except Exception:
        return False


def _satan_side_on():
    """The pessimistic anchor is available when Satan is enabled or shadowing."""
    try:
        import world_satan
        return world_satan.enabled() or world_satan.shadow_on()
    except Exception:
        return False


def sides():
    return {"jesus": _jesus_side_on(), "satan": _satan_side_on()}


# ── the core band: a 0..1 score becomes {best, worst, expected} ──────────────
def band(base, jesus_on=None, satan_on=None):
    """Turn one 0..1 score into a calibrated hi-lo band.

    The spread is driven by UNCERTAINTY: a score near 0.5 (the model doesn't
    know) gets a wide band; a score near 0 or 1 (the model is sure) gets a
    tight one. best/worst are the optimistic/pessimistic anchors; expected is
    the calibrated middle of whatever sides are live.

    Disabled sides collapse onto the base value — single-sided fallback,
    never an error. Both sides off ⇒ best == worst == expected == base."""
    try:
        s = max(0.0, min(1.0, float(base)))
    except (TypeError, ValueError):
        s = 0.5
    if jesus_on is None:
        jesus_on = _jesus_side_on()
    if satan_on is None:
        satan_on = _satan_side_on()
    # uncertainty: 1.0 at s=0.5, 0.0 at s=0 or 1 — the band width earns itself
    u = 1.0 - abs(2.0 * s - 1.0)
    half = 0.5 * u * 0.5                     # max half-width 0.25 at full doubt
    best = min(1.0, s + half) if jesus_on else s
    worst = max(0.0, s - half) if satan_on else s
    expected = round((best + worst) / 2.0, 3)
    return {"best": round(best, 3), "worst": round(worst, 3),
            "expected": expected, "base": round(s, 3),
            "sides": {"jesus": bool(jesus_on), "satan": bool(satan_on)}}


def band_note(b):
    """One-line human rendering of a band, or '' when it adds nothing."""
    if not b or (b["best"] == b["worst"] == b.get("base", b["expected"])):
        return ""
    return (f"band 🔻{int(b['worst'] * 100)}%–🔺{int(b['best'] * 100)}% "
            f"(exp {int(b['expected'] * 100)}%)")


# ── forecast band: a price call becomes {high, low, expected} ────────────────
def forecast_band(current, target, confidence, jesus_on=None, satan_on=None):
    """Wrap one price forecast (current → target at `confidence`) in a hi-lo
    band. best case (Jesus) = the full favorable move lands; worst case
    (Satan) = an adverse move of the same magnitude; expected = the move
    discounted by the analyst's own confidence.

    Sides that are off collapse to the expected value (single-sided fallback);
    any bad input returns None instead of raising."""
    try:
        cur, tgt = float(current), float(target)
        conf = max(0.0, min(1.0, float(confidence if confidence is not None else 0.5)))
        if cur <= 0 or tgt <= 0:
            return None
    except (TypeError, ValueError):
        return None
    if jesus_on is None:
        jesus_on = _jesus_side_on()
    if satan_on is None:
        satan_on = _satan_side_on()
    move = tgt - cur
    expected = round(cur + move * conf, 6)
    high = round(cur + abs(move), 6) if jesus_on else expected
    low = round(max(0.0, cur - abs(move) * (1.0 - conf * 0.5)), 6) if satan_on else expected
    return {"high": high, "low": low, "expected": expected,
            "sides": {"jesus": bool(jesus_on), "satan": bool(satan_on)}}


# ── prayer arguments: Jesus argues FOR, Satan argues AGAINST ─────────────────
def prayer_arguments(conn, p):
    """For ONE pending prayer dict, compose the two lieutenants' cases:
      for      — Jesus's constructive/best-case argument (None while his side is off)
      against  — Satan's adversarial/worst-case argument (None while his side is off)
      band     — the taste band around the stored taste score
    PURELY read-only and purely presentational: the approval gate itself is
    untouched — only the human's (or an enabled delegate's) verdict resolves
    anything. Degrades to {} when both sides are off (the default)."""
    jesus_on, satan_on = _jesus_side_on(), _satan_side_on()
    if not (jesus_on or satan_on):
        return {}
    # display-label theming only (app/naming_theme.py): themed default renders
    # exactly today's ✝️/😈 prefixes; neutral swaps the icons. Cosmetic.
    try:
        import naming_theme as _nt
        _jicon, _sicon = _nt.icon("jesus"), _nt.icon("satan")
    except Exception:
        _jicon, _sicon = "✝️", "😈"
    out = {}
    taste = p.get("taste")
    cost = int(p.get("cost_cents") or 0)
    bok, mok = p.get("boss_ok"), p.get("mayor_ok")
    if taste is not None:
        out["band"] = band(taste, jesus_on, satan_on)
    if jesus_on:
        bits = []
        if bok == 1 and mok == 1:
            bits.append("both leaders endorse it")
        elif bok == 1 or mok == 1:
            bits.append("one leader endorses it")
        if taste is not None:
            bits.append(f"taste {int(float(taste) * 100)}% — "
                        + ("clears your bar" if _clears_taste(taste) else "near your bar"))
        if cost == 0:
            bits.append("costs nothing")
        out["for"] = _jicon + " " + ("; ".join(bits) if bits else
                                     "no objection on record — routine work")
    if satan_on:
        risks = _prayer_risks(p)
        out["against"] = _sicon + " " + ("; ".join(risks) if risks else
                                         "no material downside found — worst case is wasted effort")
    return out


def _clears_taste(taste):
    try:
        import world_ops as wo
        return float(taste) >= wo.taste_min()
    except Exception:
        return float(taste) >= 0.35


def _prayer_risks(p):
    """Satan's deterministic worst-case checklist for one prayer (read-only)."""
    risks = []
    kind = p.get("kind") or ""
    cost = int(p.get("cost_cents") or 0)
    try:
        import world_ops as wo
        if kind in wo.ALWAYS_GATE:
            risks.append("IRREVERSIBLE real-money/secret kind — only your explicit hand may move it")
    except Exception:
        pass
    if kind == "add_software":
        risks.append("code change — approved code could alter the safety floor itself")
    if cost > 0:
        risks.append(f"costs real money (${cost / 100:.2f}) — an approval spends it")
    if p.get("boss_ok") == 0:
        risks.append("Boss blocked it")
    if p.get("mayor_ok") == 0:
        risks.append("Mayor blocked it")
    taste = p.get("taste")
    if taste is not None and not _clears_taste(taste):
        risks.append(f"taste {int(float(taste) * 100)}% is under your bar — likely a reject")
    if kind in ("social_publish", "publish_cults3d", "publish_wordpress"):
        risks.append("public + hard to un-publish once live")
    return risks


# ── the two-lieutenant owner digest ──────────────────────────────────────────
def lieutenants_digest(conn=None):
    """The owner's briefing from both lieutenants:
      Jesus — what's happening / what I'd do (constructive summary).
      Satan — what could go wrong / the risks (adversarial summary).
    Read-only. Each side reports even while disabled (clearly marked inert) so
    the owner always sees the seat, but neither side ever acts from here."""
    from deps import get_conn
    own = conn is None
    if own:
        conn = get_conn()
    try:
        digest = {"sides": sides()}
        # shared ground truth both lieutenants report on
        try:
            import world_ops as wo
            wo.ensure(conn)
            pend = [dict(r) for r in conn.execute(
                "SELECT id, kind, title, cost_cents, taste, boss_ok, mayor_ok "
                "FROM world_prayers WHERE status='pending' ORDER BY id ASC LIMIT 40").fetchall()]
            bal = wo.balance_cents(conn)
            spend = wo.cycle_spend_cents(conn)
            cap = wo.cap_cents()
        except Exception:
            pend, bal, spend, cap = [], 0, 0, 0
        # ✝️ Jesus: what's happening / what I'd do
        j = {"enabled": False, "summary": [], "ledger": []}
        try:
            import world_jesus
            j["enabled"] = world_jesus.enabled()
            actionable = would_approve = would_reject = 0
            for p in pend:
                if world_jesus._floor_blocked(p):
                    continue
                actionable += 1
                if p.get("taste") is None:
                    continue                       # unendorsed — stay read-only here
                if p.get("boss_ok") == 1 and p.get("mayor_ok") == 1 and _clears_taste(p["taste"]):
                    would_approve += 1
                elif p.get("boss_ok") == 0 and p.get("mayor_ok") == 0:
                    would_reject += 1
            j["summary"] = [
                f"{len(pend)} prayer(s) pending; {actionable} within my seat "
                f"(the rest are human-only: money/secrets/code)",
                f"I'd approve {would_approve} and reject {would_reject} on your routine bar; "
                f"the split calls wait for you",
                f"treasury {'+' if bal >= 0 else ''}{bal / 100:.2f} · "
                f"${spend / 100:.2f} of the ${cap / 100:.2f} monthly cap spent",
            ]
            j["ledger"] = world_jesus.ledger(6, conn=conn)
        except Exception:
            logger.exception("lieutenant digest: jesus side failed")
        digest["jesus"] = j
        # 😈 Satan: what could go wrong
        s = {"enabled": False, "risks": [], "ledger": []}
        try:
            import world_satan
            s["enabled"] = world_satan.enabled()
            s["risks"] = world_satan.risk_report(conn, pend, bal, spend, cap)
            s["ledger"] = world_satan.ledger(6, conn=conn)
        except Exception:
            logger.exception("lieutenant digest: satan side failed")
        digest["satan"] = s
        return digest
    finally:
        if own:
            conn.close()

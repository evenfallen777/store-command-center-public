"""NetSec queue-starvation fix — app/world_raid.py + app/world_security.py.

Covers: FIFO (oldest-first) finding pickup for raids, the non-combat
remediation drain of the Low/Medium backlog, the netsec dept typo fix
(assign_beats wires the real netsec agent to its own beat), and the
backlog-depth raid trigger.
"""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def _conn():
    from deps import get_conn
    return get_conn()


def _clear_findings(c):
    c.execute("DELETE FROM security_findings")


def _insert_finding(c, fkey, issue, priority):
    c.execute("""INSERT INTO security_findings(fkey, issue, priority, status)
        VALUES(?,?,?, 'pending')""", (fkey, issue, priority))
    return c.lastrowid


def test_scan_threats_picks_oldest_findings_first(client):
    """world_raid.scan_threats() must surface the OLDEST pending findings (FIFO),
    not the newest (LIFO) — LIFO is exactly the queue-starvation bug: a steady
    drip of new findings permanently outruns old ones."""
    import world_raid as wr
    conn = _conn(); c = conn.cursor()
    _clear_findings(c)
    ids = [_insert_finding(c, f"fifo-{i}", f"issue {i}", "Medium") for i in range(6)]
    conn.commit()
    threats = wr.scan_threats(c, limit=8)
    picked = [int(t["ref"]) for t in threats if t["kind"] == "finding"]
    assert picked == ids[:4], f"expected the 4 OLDEST ids {ids[:4]}, got {picked}"
    conn.close()


def test_remediate_backlog_drains_oldest_low_medium_only(client):
    """world_security.remediate_backlog() closes the oldest Low/Medium pending
    findings without a raid, oldest-first, and leaves High findings for combat."""
    import world_security as wsec
    from world_defs import mset
    conn = _conn(); c = conn.cursor()
    _clear_findings(c)
    mset(c, "netsec_remediate_t", 0)   # clear any cooldown from a previous test
    old_low = _insert_finding(c, "rem-1", "old low finding", "Low")
    old_med = _insert_finding(c, "rem-2", "old medium finding", "Medium")
    high = _insert_finding(c, "rem-3", "should stay for combat", "High")
    newer_low = _insert_finding(c, "rem-4", "newer low finding", "Low")
    conn.commit()

    n = wsec.remediate_backlog(c, {"key": "test_netsec", "name": "Gale"}, batch=2)
    conn.commit()
    assert n == 2

    rows = {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM security_findings").fetchall()}
    assert rows[old_low] == "remediated"
    assert rows[old_med] == "remediated"
    assert rows[high] == "pending", "High findings must be reserved for raid combat"
    assert rows[newer_low] == "pending", "batch=2 should only drain the 2 OLDEST low/med findings"

    # cooldown: an immediate second call must be a no-op (steady drain, not instant zero)
    n2 = wsec.remediate_backlog(c, {"key": "test_netsec", "name": "Gale"}, batch=2)
    conn.commit()
    assert n2 == 0, "remediate_backlog must be throttled — no draining the whole queue in one tick"

    # simulate the cooldown having elapsed → the next-oldest batch drains
    mset(c, "netsec_remediate_t", time.time() - 999)
    conn.commit()
    n3 = wsec.remediate_backlog(c, {"key": "test_netsec", "name": "Gale"}, batch=2)
    conn.commit()
    assert n3 == 1   # only 'newer_low' was left eligible
    row = c.execute("SELECT status FROM security_findings WHERE id=?", (newer_low,)).fetchone()
    assert row["status"] == "remediated"
    conn.close()


def test_operate_workgiver_wires_netsec_remediation(client):
    """world_work._wg_operate must give the netsec dept agent the non-combat
    remediation job when there's low/med backlog to work — not just raid combat."""
    import world_work as ww
    from world_defs import mset
    conn = _conn(); c = conn.cursor()
    _clear_findings(c)
    mset(c, "netsec_remediate_t", 0)
    _insert_finding(c, "wg-1", "backlog item", "Low")
    conn.commit()
    agent = {"id": 1, "key": "w_sec_1", "name": "Gale", "dept": "netsec"}
    job = ww.choose_work(c, agent, {"has_work": False, "t": 0})
    conn.commit()
    assert job["work_type"] == "operate" and job["state"] == "working"
    assert "finding" in job["goal"]
    conn.close()


def test_netsec_dept_typo_fixed_and_beats_wire_correctly(client):
    """world_security.py:82 had SYSTEMS['netsec']['dept'] == 'trends' (typo), which
    made assign_beats() hand the netsec beat to the TRENDS agent instead of the real
    NetSec agent (w_sec_1 / Gale)."""
    import world_security as wsec
    conn = _conn(); c = conn.cursor()
    assert wsec.SYSTEMS["netsec"]["dept"] == "netsec"

    wsec._ensure(c)
    c.execute("DELETE FROM world_beats")
    conn.commit()
    agents = [
        {"key": "w_sec_1", "dept": "netsec"},
        {"key": "w_trends_1", "dept": "trends"},
    ]
    beats = wsec.assign_beats(c, agents)
    conn.commit()
    assert beats["w_sec_1"] == "netsec", "the real NetSec agent must watch its own beat"
    assert beats["w_trends_1"] != "netsec", "the trends agent must not steal the netsec beat"
    conn.close()


def test_backlog_depth_is_a_raid_trigger_condition(client):
    """world_raid.maybe_trigger(): a deep pending-findings backlog must be able to
    raise a raid on its own, independent of the unrelated subsystem/alert triggers,
    while still respecting the cooldown."""
    import world_raid as wr
    assert wr.NETSEC_BACKLOG_RAID_THRESHOLD > 0

    import world_orchestra as WO
    from world_defs import mset, mget
    conn = _conn(); c = conn.cursor()
    _clear_findings(c)
    for i in range(wr.NETSEC_BACKLOG_RAID_THRESHOLD + 5):
        _insert_finding(c, f"trig-{i}", f"backlog {i}", "Medium")
    # prime the scan cadence so this call isn't swallowed as the "first observation"
    mset(c, "last_raid_scan", time.time() - 400)
    mset(c, "last_raid_t", 0)          # no cooldown in effect
    WO.set_phase(c, "peace", "test setup")
    conn.commit()

    wr.maybe_trigger(c)
    conn.commit()
    assert WO.phase(c) == "raid", "a backlog past the threshold must raise a raid on its own"
    conn.close()

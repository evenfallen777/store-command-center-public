"""Oracle analysts (app/routers/oracle/_base.py DEFAULT_ANALYSTS) are named after
their LLMs — great on the tournament board, awful as a citizen's name. Covers the
fix in app/world_defs.py: ORACLE_PERSONAS, seed() using a persona for the world
body's display name (raw model id kept in model_id for the detail panel), and
_migrate_oracle_personas() renaming the 5 pre-fix rows exactly once."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def _conn():
    from deps import get_conn
    return get_conn()


def _oracle_key(model_name):
    return "oracle_" + re.sub(r"[^a-z0-9]+", "_", model_name.lower()).strip("_")


def test_persona_map_covers_the_default_analysts():
    import world_defs as wd
    from routers.oracle._base import DEFAULT_ANALYSTS
    for name, _model in DEFAULT_ANALYSTS:
        assert name in wd.ORACLE_PERSONAS, f"{name} has no persona mapped"
        assert wd.ORACLE_PERSONAS[name] != name        # persona must not just echo the model id


def test_persona_applied_for_new_oracle_agent(client):
    import world_defs as wd
    conn = _conn(); c = conn.cursor()

    # a known analyst → its mapped persona
    model_name = "Qwen-Coder-32B"
    key = _oracle_key(model_name)
    c.execute("DELETE FROM world_agents WHERE key=?", (key,))
    c.execute("DELETE FROM oracle_agents WHERE name=?", (model_name,))
    c.execute("INSERT INTO oracle_agents (name, model, active) VALUES (?,?,1)", (model_name, model_name))
    conn.commit()
    wd.seed(conn)
    row = c.execute("SELECT name, model_id, job_class FROM world_agents WHERE key=?", (key,)).fetchone()
    assert row is not None
    assert row["name"] == "Cassandra"                  # persona shown as the citizen's name
    assert row["model_id"] == model_name                # raw model id preserved, detail-panel only
    assert row["job_class"] == "oracle"

    # an unmapped/custom analyst → falls back to "Oracle <short id>", never the raw id verbatim
    custom_model = "totally-custom-model-9000"
    ckey = _oracle_key(custom_model)
    c.execute("DELETE FROM world_agents WHERE key=?", (ckey,))
    c.execute("DELETE FROM oracle_agents WHERE name=?", (custom_model,))
    c.execute("INSERT INTO oracle_agents (name, model, active) VALUES (?,?,1)", (custom_model, custom_model))
    conn.commit()
    wd.seed(conn)
    crow = c.execute("SELECT name, model_id FROM world_agents WHERE key=?", (ckey,)).fetchone()
    assert crow["name"] == "Oracle " + custom_model[:12]
    assert crow["name"] != custom_model
    assert crow["model_id"] == custom_model

    c.execute("DELETE FROM world_agents WHERE key IN (?,?)", (key, ckey))
    c.execute("DELETE FROM oracle_agents WHERE name IN (?,?)", (model_name, custom_model))
    conn.commit()


def test_migration_renames_existing_rows_once_and_is_idempotent(client):
    import world_defs as wd
    conn = _conn(); c = conn.cursor()

    model_name = "GLM-4.7"
    key = _oracle_key(model_name)
    c.execute("DELETE FROM world_meta WHERE key='world_oracle_personas_migrated'")
    c.execute("DELETE FROM world_agents WHERE key=?", (key,))
    # simulate a pre-fix row: name is the raw model id, no model_id yet
    c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state) "
              "VALUES (?,?,?,?,?,?,?,?)",
              (key, model_name, "worker", "oracle", "devlab", "#8b5cf6", "home", "idle"))
    conn.commit()

    wd._migrate_oracle_personas(conn, c)
    conn.commit()
    row = c.execute("SELECT name, model_id FROM world_agents WHERE key=?", (key,)).fetchone()
    assert row["name"] == "Delphi"
    assert row["model_id"] == model_name

    # idempotent: a later run (flag now set) must not touch a name the owner changed
    c.execute("UPDATE world_agents SET name='Renamed By Owner' WHERE key=?", (key,))
    conn.commit()
    wd._migrate_oracle_personas(conn, c)
    conn.commit()
    row = c.execute("SELECT name FROM world_agents WHERE key=?", (key,)).fetchone()
    assert row["name"] == "Renamed By Owner"

    c.execute("DELETE FROM world_agents WHERE key=?", (key,))
    c.execute("DELETE FROM world_meta WHERE key='world_oracle_personas_migrated'")
    conn.commit()


def test_migration_leaves_already_renamed_row_alone(client):
    import world_defs as wd
    conn = _conn(); c = conn.cursor()

    key = _oracle_key("GLM-4.6v")
    c.execute("DELETE FROM world_meta WHERE key='world_oracle_personas_migrated'")
    c.execute("DELETE FROM world_agents WHERE key=?", (key,))
    # this row is already a persona (or a user rename) — NOT the raw model id
    c.execute("INSERT INTO world_agents (key,name,kind,job_class,dept,color,location,state) "
              "VALUES (?,?,?,?,?,?,?,?)",
              (key, "My Custom Name", "worker", "oracle", "devlab", "#8b5cf6", "home", "idle"))
    conn.commit()

    wd._migrate_oracle_personas(conn, c)
    conn.commit()
    row = c.execute("SELECT name FROM world_agents WHERE key=?", (key,)).fetchone()
    assert row["name"] == "My Custom Name"    # WHERE name=<old model id> guard skipped it

    c.execute("DELETE FROM world_agents WHERE key=?", (key,))
    c.execute("DELETE FROM world_meta WHERE key='world_oracle_personas_migrated'")
    conn.commit()

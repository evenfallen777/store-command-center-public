import sqlite3, os
from pathlib import Path

try:
    from config import DB_PATH        # honors STORE_DATA_DIR
except Exception:
    DB_PATH = Path(__file__).parent.parent / "store.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Wait up to 5s for a write lock instead of failing instantly with
    # "database is locked" — many writers now (world ticker every 8s, auto
    # creation, strategy/bible study threads) contend for the single db.
    conn.execute("PRAGMA busy_timeout=5000")
    # WAL lets readers proceed while one writer commits — with a ticker every 8s
    # plus creation/strategy/study threads, rollback-journal mode still threw
    # "database is locked" past the busy_timeout. NORMAL sync is safe with WAL.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    # Table-creation / migration SQL lives in db_schema.py (moved verbatim).
    # Imported inside init_db so db.py's `from db import *` surface stays clean.
    from db_schema import (
        create_library_table,
        create_security_tables,
        create_design_tables,
        create_media_tables,
        create_studio_tables,
        create_resell_tables,
        create_portal_tables,
        create_social_tables,
        create_swarm_tables,
        create_dev_projects_tables,
        create_world_tables,
        create_queue_history_table,
        create_mail_tables,
        run_migrations,
    )
    conn = get_conn()
    # Create tables per domain, in the same order as before (all IF NOT EXISTS).
    create_library_table(conn)   # commits internally
    create_security_tables(conn)
    create_design_tables(conn)
    create_media_tables(conn)
    create_studio_tables(conn)
    create_resell_tables(conn)
    create_portal_tables(conn)
    create_social_tables(conn)
    create_swarm_tables(conn)
    create_dev_projects_tables(conn)
    create_world_tables(conn)
    create_queue_history_table(conn)
    create_mail_tables(conn)   # + one-time seed from the legacy mail_* settings
    conn.commit()
    # Migrations — add columns that might be missing in older DBs
    run_migrations(conn)
    conn.close()

"""Lightweight SQL migration runner.

Applies numbered SQL migration files from the migrations/ directory in order.
Tracks applied migrations in a `schema_migrations` table. Idempotent — safe
to call on every startup.

No external dependencies beyond psycopg2 (already required).
"""

import os
import re

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def _ensure_migrations_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                filename    TEXT NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


def _get_applied_versions(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        return {row[0] for row in cur.fetchall()}


def _discover_migrations():
    """Return sorted list of (version, filename) from migrations/ directory."""
    pattern = re.compile(r"^(\d+)_.+\.sql$")
    migrations = []
    if not os.path.isdir(MIGRATIONS_DIR):
        return migrations
    for name in os.listdir(MIGRATIONS_DIR):
        m = pattern.match(name)
        if m:
            migrations.append((int(m.group(1)), name))
    migrations.sort()
    return migrations


def run_migrations(conn):
    """Apply any pending migrations. Returns list of newly applied filenames."""
    _ensure_migrations_table(conn)
    applied = _get_applied_versions(conn)
    available = _discover_migrations()

    newly_applied = []
    for version, filename in available:
        if version in applied:
            continue
        path = os.path.join(MIGRATIONS_DIR, filename)
        with open(path) as f:
            sql = f.read()

        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                (version, filename),
            )
        conn.commit()
        newly_applied.append(filename)

    return newly_applied

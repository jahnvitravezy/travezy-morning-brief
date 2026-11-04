"""Postgres for production: one schema holding the outbox, the usage ledger, the shared
cache and the places table, so several copies of the service share them.

Used when DATABASE_URL is set; without it, everything falls back to local files (dev, tests).
The schema is MB_DB_SCHEMA (default morning_brief), so it can sit in Travezy's production
database next to other tables. Migrations are migrations/*.sql, applied once each, in order,
under an advisory lock:

    python -m mb.db             # apply migrations (the service also does this on start-up)
    python -m mb.db cleanup     # delete rows past their retention (config/app.yaml), e.g. daily
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
LOCK_ID = 726017
_conn = None


def enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def schema() -> str:
    return os.environ.get("MB_DB_SCHEMA", "morning_brief")


def migrate(conn) -> list[str]:
    from psycopg import sql
    applied = []
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))   # two processes starting together take turns
    try:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema())))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema())))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, "
                     "applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name not in done:
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied


def conn():
    """One shared connection per process, migrated on first use."""
    global _conn
    if _conn is None or _conn.closed:
        import psycopg
        c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
        migrate(c)
        _conn = c
    return _conn


RETENTION_SQL = {   # table: (time column, setting in config/app.yaml retention_days)
    "outbox": ("created_at", "outbox"),
    "cache": ("updated_at", "cache"),
    "usage_ledger": ("ts", "usage_ledger"),
    "place_searches": ("searched_at", "place_searches"),
}


def cleanup(c, days: dict[str, int]) -> dict[str, int]:
    """Delete what is past its retention. The places table is kept and refreshed, never expired."""
    out = {}
    for table, (col, key) in RETENTION_SQL.items():
        cur = c.execute(f"DELETE FROM {table} WHERE {col} < now() - make_interval(days => %s)", (int(days[key]),))
        out[table] = cur.rowcount
    return out


if __name__ == "__main__":
    import sys
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        print("applied:", migrate(c) or "nothing new")
        if sys.argv[1:] == ["cleanup"]:
            from mb.config import load_config
            print("deleted:", cleanup(c, load_config().app.retention_days))

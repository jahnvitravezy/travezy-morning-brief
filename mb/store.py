"""Where generated briefs wait to be sent: the outbox.

FileStore (default): data/outbox/<date>/<trip_id>.json, no setup.
PostgresStore: used when DATABASE_URL is set (docker compose starts a Postgres for it).
One brief per trip per date: saving again replaces it, and the batch skips trips already done.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class OutboxItem:
    trip_id: str
    date: str                 # the trip day, YYYY-MM-DD
    timezone: str             # where the traveller wakes up
    send_at_local: str        # HH:MM in that time zone
    send_at_utc: str          # ISO timestamp: when the notification should go out
    verdict: str | None       # pass / fix / fallback
    score: int | None
    badge: str | None
    brief: dict               # the MorningBrief, as JSON


class BriefStore(Protocol):
    def has(self, trip_id: str, date: str) -> bool: ...
    def save(self, item: OutboxItem) -> None: ...
    def for_date(self, date: str) -> list[OutboxItem]: ...


class FileStore:
    def __init__(self, root: Path = ROOT / "data" / "outbox"):
        self.root = root

    def _path(self, trip_id: str, date: str) -> Path:
        return self.root / date / f"{trip_id}.json"

    def has(self, trip_id: str, date: str) -> bool:
        return self._path(trip_id, date).exists()

    def save(self, item: OutboxItem) -> None:
        p = self._path(item.trip_id, item.date)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(item), indent=1, ensure_ascii=False), encoding="utf-8")

    def for_date(self, date: str) -> list[OutboxItem]:
        d = self.root / date
        return [OutboxItem(**json.loads(p.read_text(encoding="utf-8"))) for p in sorted(d.glob("*.json"))] if d.exists() else []


class PostgresStore:
    LOCK_ID = 726017                              # any fixed number: the outbox's schema lock
    SCHEMA = """CREATE TABLE IF NOT EXISTS outbox (
        trip_id text NOT NULL, date date NOT NULL, timezone text NOT NULL,
        send_at_local text NOT NULL, send_at_utc timestamptz NOT NULL,
        verdict text, score int, badge text, brief jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (trip_id, date))"""

    def __init__(self, url: str):
        import psycopg
        self.conn = psycopg.connect(url, autocommit=True)
        # Two processes starting together (the API and a batch run) can both try to create the table:
        # "IF NOT EXISTS" alone isn't safe against that race in Postgres. A lock makes them take turns.
        self.conn.execute("SELECT pg_advisory_lock(%s)", (self.LOCK_ID,))
        try:
            self.conn.execute(self.SCHEMA)
        except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateTable):
            pass                                  # created by the other process a moment ago
        finally:
            self.conn.execute("SELECT pg_advisory_unlock(%s)", (self.LOCK_ID,))

    def has(self, trip_id: str, date: str) -> bool:
        return self.conn.execute("SELECT 1 FROM outbox WHERE trip_id=%s AND date=%s", (trip_id, date)).fetchone() is not None

    def save(self, item: OutboxItem) -> None:
        from psycopg.types.json import Jsonb
        self.conn.execute(
            """INSERT INTO outbox (trip_id, date, timezone, send_at_local, send_at_utc, verdict, score, badge, brief)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (trip_id, date) DO UPDATE SET timezone=EXCLUDED.timezone,
                 send_at_local=EXCLUDED.send_at_local, send_at_utc=EXCLUDED.send_at_utc, verdict=EXCLUDED.verdict,
                 score=EXCLUDED.score, badge=EXCLUDED.badge, brief=EXCLUDED.brief, created_at=now()""",
            (item.trip_id, item.date, item.timezone, item.send_at_local, item.send_at_utc, item.verdict,
             item.score, item.badge, Jsonb(item.brief)))

    def for_date(self, date: str) -> list[OutboxItem]:
        rows = self.conn.execute(
            """SELECT trip_id, date::text, timezone, send_at_local, send_at_utc, verdict, score, badge, brief
               FROM outbox WHERE date=%s ORDER BY send_at_utc, trip_id""", (date,)).fetchall()
        return [OutboxItem(r[0], r[1], r[2], r[3], r[4].astimezone(dt.timezone.utc).isoformat(), *r[5:]) for r in rows]


def get_store() -> BriefStore:
    url = os.environ.get("DATABASE_URL")
    return PostgresStore(url) if url else FileStore()

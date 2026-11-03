"""A cache for external calls: same request, same answer, no second bill.

PgCache (shared by every copy of the service) when DATABASE_URL is set; JsonCache on disk otherwise.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class JsonCache:
    def __init__(self, name: str, root: Path = ROOT / "data" / "cache"):
        self.dir = root / name

    def _path(self, key: dict) -> Path:
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]
        return self.dir / f"{digest}.json"

    def get(self, key: dict):
        p = self._path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def set(self, key: dict, value) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding="utf-8")


def _digest(key: dict) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]


class PgCache:
    def __init__(self, name: str):
        from mb import db
        self.name, self.conn = name, db.conn()

    def get(self, key: dict):
        row = self.conn.execute("SELECT value FROM cache WHERE name=%s AND key=%s", (self.name, _digest(key))).fetchone()
        return row[0] if row else None

    def set(self, key: dict, value) -> None:
        from psycopg.types.json import Jsonb
        self.conn.execute("""INSERT INTO cache (name, key, value) VALUES (%s,%s,%s)
                             ON CONFLICT (name, key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                          (self.name, _digest(key), Jsonb(value)))


def get_cache(name: str):
    from mb import db
    return PgCache(name) if db.enabled() else JsonCache(name)

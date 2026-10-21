"""A small on-disk cache for external calls: same request, same answer, no second bill."""
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
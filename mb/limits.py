"""Timeouts and retries, read from config/limits.yaml (see that file)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

FILE = Path(__file__).resolve().parents[1] / "config" / "limits.yaml"


@lru_cache(maxsize=1)
def limits() -> dict:
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


def api(name: str) -> dict:
    return limits()["apis"][name]


def api_client(name: str):
    """An HTTP client with this API's timeout; retries cover dropped connections."""
    import httpx
    a = api(name)
    return httpx.AsyncClient(timeout=a["timeout_s"], transport=httpx.AsyncHTTPTransport(retries=a["retries"]))

"""Exchange rates for the traveller's spend line, fetched live once a day.

The brief tells a traveller "about ₹5,032 per person", so the rate can't be a number typed into a
config file. Rules:
  1. Today's rate from the live source (open.er-api.com, free, no key, updated daily), cached for
     the day in data/cache/fx/, so it is fetched once a day, not once per brief.
  2. If the source can't be reached: the most recent cached rate, if it is at most 2 days old.
  3. Otherwise the rate is unverified: the brief shows the local amount only ("about AED 210 per
     person") and drops the home-currency figure, rather than show a number we aren't sure of.
MB_FX=fixed uses config/fx.yaml instead: for tests and for evaluation runs that must be repeatable.
In production, Travezy can point this at its own rate source (the same one its checkout uses).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import yaml

from mb.config import CONFIG_DIR
from mb.sources.cache import ROOT

URL = "https://open.er-api.com/v6/latest/USD"
CACHE = ROOT / "data" / "cache" / "fx"
MAX_AGE_DAYS = 2
TIMEOUT_S = 4


class Rates(dict):
    """Units per 1 USD, plus where they came from. source: live, cached, fixed or unverified."""
    def __init__(self, rates: dict, source: str, as_of: dt.date | None = None):
        super().__init__(rates)
        self.source, self.as_of = source, as_of

    @property
    def trusted(self) -> bool:
        return self.source != "unverified"


def fixed() -> Rates:
    return Rates(yaml.safe_load((CONFIG_DIR / "fx.yaml").read_text(encoding="utf-8")), "fixed")


def _fetch() -> dict:
    import httpx
    r = httpx.get(URL, timeout=TIMEOUT_S)
    r.raise_for_status()
    body = r.json()
    if body.get("result") != "success" or "INR" not in body.get("rates", {}):
        raise ValueError(f"unexpected answer from the rate source: {str(body)[:120]}")
    return body["rates"]


def _cached(cache: Path, day: dt.date) -> dict | None:
    p = cache / f"{day.isoformat()}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def live(today: dt.date | None = None, fetch=_fetch, cache_dir: Path | None = None) -> Rates:
    cache = cache_dir or CACHE
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if (hit := _cached(cache, today)) is not None:
        return Rates(hit, "live", today)
    try:
        rates = fetch()
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{today.isoformat()}.json").write_text(json.dumps(rates), encoding="utf-8")
        return Rates(rates, "live", today)
    except Exception:
        for back in range(1, MAX_AGE_DAYS + 1):
            day = today - dt.timedelta(days=back)
            if (hit := _cached(cache, day)) is not None:
                return Rates(hit, "cached", day)
        return Rates(fixed(), "unverified")


def load_rates() -> Rates:
    return fixed() if os.environ.get("MB_FX", "live").lower() == "fixed" else live()

"""Test sets: fixed trip days, the same on every run so results can be compared."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mb.config import CONFIG_DIR

REQUIRED_COVERAGE = {"sightseeing", "arrival", "departure", "free_day", "weather_clash", "closure",
                     "booking", "diet", "pace", "family", "no_surprise"}


class TestDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^td\.\d{2}$")
    trip_id: str
    date: dt.date
    weather: str
    covers: list[str]


class TestSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    use_case: str
    days: list[TestDay]


def load_testset(name: str = "morning_brief.v1", folder: Path = CONFIG_DIR / "testsets") -> TestSet:
    return TestSet(**yaml.safe_load((folder / f"{name}.yaml").read_text(encoding="utf-8")))

"""Loads every config file and validates it, including cross-references, at startup.

A typo in any file stops the service before a single brief runs.
"""
from __future__ import annotations

import datetime as dt

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # unknown keys are errors


class AppSettings(Strict):
    itinerary_loader: Literal["file", "api"]
    itinerary_dir: str
    monthly_budget_inr: float = Field(gt=0)
    budget_alert_pct: list[int] = [50, 80]   # alerts, once a month each
    budget_stop_pct: int = Field(default=100, gt=0)   # the one hard stop
    inr_per_usd: float = Field(gt=0)
    inr_per_usd_checked: str = ""      # the day the rate was checked, shown with every rupee figure
    default_send_local: str = Field(pattern=r"^\d{2}:\d{2}$")
    send_lead_min: int = Field(ge=0)
    briefs_enabled: bool = True
    weather_provider: Literal["weatherapi", "open_meteo"] = "weatherapi"
    places_hours_refresh_days: int = Field(default=3, gt=0)
    places_search_refresh_days: int = Field(default=30, gt=0)


class ModelSpec(Strict):
    family: Literal["openai", "anthropic", "google"]
    temperature: bool = True     # False: the model only runs at its own default (Claude 5.5 models)
    structured: Literal["tools", "json"] = "tools"   # json: no forced tool call (Claude Sonnet 5.5)
    end_of_life: dt.date | None = None    # the provider's retirement date; warned about ahead of time


class SourceSpec(Strict):
    id: str = Field(pattern=r"^(src|out|ctx)\.[a-z_.]+$")
    kind: Literal["input", "output", "context"]
    description: str
    provider: str | None = None


class AgentSpec(Strict):
    id: str = Field(pattern=r"^[a-z]+\.[a-z_]+\.v\d+$")
    use_case: str
    job: str
    inputs: list[str]
    output: str
    model: str
    temperature: float = 0
    timeout_s: float = Field(default=30, gt=0)   # set in config/limits.yaml
    retries: int = Field(default=1, ge=0, le=3)
    prompt_version: str
    rubric: str | None = None
    status: Literal["active", "disabled"] = "active"


class Badge(Strict):
    green: int = Field(ge=0, le=100)
    amber: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def ordered(self):
        if self.amber >= self.green:
            raise ValueError("badge.amber must be below badge.green")
        return self


class Criterion(Strict):
    id: str
    weight: float = Field(gt=0, le=1)
    asks: str


class Rubric(Strict):
    id: str = Field(pattern=r"^rubric\.[a-z_]+\.v\d+$")
    use_case: str
    judge_model: str
    temperature: float = 0
    scale: Literal["0-100"]
    badge: Badge
    max_fix_attempts: int = Field(ge=0, le=3)
    rule_checks: dict[str, int | bool]   # generic, so a new use case can bring its own checks
    criteria: list[Criterion]

    @model_validator(mode="after")
    def weights_and_ids(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.id}: duplicate criterion ids")
        total = sum(c.weight for c in self.criteria)
        if abs(total - 1) > 1e-6:
            raise ValueError(f"{self.id}: criterion weights sum to {total:.3f}, not 1")
        return self


class Config(Strict):
    app: AppSettings
    models: dict[str, ModelSpec]
    sources: dict[str, SourceSpec]
    agents: dict[str, AgentSpec]
    rubrics: dict[str, Rubric]

    @model_validator(mode="after")
    def cross_references(self):
        errors = []
        for a in self.agents.values():
            if a.model not in self.models:
                errors.append(f"{a.id}: model '{a.model}' is not in models.yaml")
            for i in a.inputs:
                if i not in self.sources:
                    errors.append(f"{a.id}: input '{i}' is not in sources.yaml")
            out = self.sources.get(a.output)
            if out is None or out.kind != "output":
                errors.append(f"{a.id}: output '{a.output}' is not an output source")
            if a.rubric:
                r = self.rubrics.get(a.rubric)
                if r is None:
                    errors.append(f"{a.id}: rubric '{a.rubric}' not found")
                elif r.judge_model != a.model:
                    errors.append(f"{a.id}: model must match {r.id}.judge_model (one source of truth)")
        for r in self.rubrics.values():
            if r.judge_model not in self.models:
                errors.append(f"{r.id}: judge_model '{r.judge_model}' is not in models.yaml")
        if errors:
            raise ValueError("Config errors:\n  " + "\n  ".join(errors))
        return self


def _read(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    lim = (_read(config_dir / "limits.yaml") or {}).get("agents", {}) if (config_dir / "limits.yaml").exists() else {}
    agents = [AgentSpec(**(a | lim.get(a["id"], {}))) for a in _read(config_dir / "agents.yaml")]
    sources = [SourceSpec(**s) for s in _read(config_dir / "sources.yaml")]
    rubrics = [Rubric(**_read(p)) for p in sorted((config_dir / "rubrics").glob("*.yaml"))]
    for kind, items in (("agent", agents), ("source", sources), ("rubric", rubrics)):
        ids = [x.id for x in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {kind} ids: {ids}")
    return Config(
        app=AppSettings(**_read(config_dir / "app.yaml")),
        models={k: ModelSpec(**v) for k, v in _read(config_dir / "models.yaml").items()},
        sources={s.id: s for s in sources},
        agents={a.id: a for a in agents},
        rubrics={r.id: r for r in rubrics},
    )

EOL_WARN_DAYS = 90


def eol_warnings(cfg: Config, today: dt.date | None = None, within_days: int = EOL_WARN_DAYS) -> list[str]:
    """Models in use (agents and judges) that retire within `within_days`, or already have."""
    today = today or dt.date.today()
    used = {a.model for a in cfg.agents.values() if a.status == "active"} | {r.judge_model for r in cfg.rubrics.values()}
    out = []
    for m in sorted(used):
        eol = cfg.models[m].end_of_life if m in cfg.models else None
        if eol and (eol - today).days <= within_days:
            left = (eol - today).days
            out.append(f"{m} reaches end of life on {eol} ({'already past' if left < 0 else f'{left} days'}): "
                       f"switch it in config/agents.yaml")
    return out

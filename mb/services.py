"""Everything a run needs, built once: config (validated), budget, loader, sources, model client.

Shared by the API and the evaluation runner, so both run exactly the same pipeline.
"""
from __future__ import annotations

from mb.budget import Budget
import logging

from mb.config import eol_warnings, load_config
from mb.judge.rules import validate_rules
from mb.llm import LLM
from mb.sources.candidates import PlacesCandidates, WebCandidates
from mb.sources.itinerary import get_loader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.weatherapi import weather_source
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes
from mb.sources.tavily import Tavily


class Services:
    """Built once at startup: config is validated here, so a bad config stops the service."""
    def __init__(self):
        self.cfg = load_config()
        for rubric in self.cfg.rubrics.values():
            validate_rules(rubric)              # an unknown rule check stops startup
        self.model_warnings = eol_warnings(self.cfg)   # models nearing end of life
        for w in self.model_warnings:
            logging.getLogger("mb.models").warning(w)
        self.budget = Budget.from_config(self.cfg)
        self.loader = get_loader(self.cfg)
        self.meteo = weather_source(self.cfg.app.weather_provider)
        self.places = GooglePlaces(self.budget, hours_refresh_days=self.cfg.app.places_hours_refresh_days,
                                   search_refresh_days=self.cfg.app.places_search_refresh_days)
        self.routes = GoogleRoutes(self.budget)
        self.providers = [PlacesCandidates(self.places), WebCandidates(Tavily(self.budget))]
        self.llm = LLM(self.budget, self.cfg.models)


def briefs_allowed(cfg) -> bool:
    """The kill switch: app.yaml briefs_enabled: false, or MB_KILL_SWITCH=1, stops every new brief."""
    import os
    return cfg.app.briefs_enabled and os.environ.get("MB_KILL_SWITCH", "").lower() not in ("1", "true", "yes")

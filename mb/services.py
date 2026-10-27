"""Everything a run needs, built once: config (validated), budget, loader, sources, model client.

Shared by the API and the evaluation runner, so both run exactly the same pipeline.
"""
from __future__ import annotations

from mb.budget import Budget
from mb.config import load_config
from mb.judge.rules import validate_rules
from mb.llm import LLM
from mb.sources.candidates import PlacesCandidates, WebCandidates
from mb.sources.itinerary import get_loader
from mb.sources.openmeteo import OpenMeteo
from mb.sources.places import GooglePlaces
from mb.sources.routes import GoogleRoutes
from mb.sources.tavily import Tavily


class Services:
    """Built once at startup: config is validated here, so a bad config stops the service."""
    def __init__(self):
        self.cfg = load_config()
        for rubric in self.cfg.rubrics.values():
            validate_rules(rubric)              # an unknown rule check stops startup
        self.budget = Budget.from_config(self.cfg)
        self.loader = get_loader(self.cfg)
        self.meteo = OpenMeteo()
        self.places = GooglePlaces(self.budget)
        self.routes = GoogleRoutes(self.budget)
        self.providers = [PlacesCandidates(self.places), WebCandidates(Tavily(self.budget))]
        self.llm = LLM(self.budget, self.cfg.models)

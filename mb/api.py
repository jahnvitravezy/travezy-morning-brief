"""HTTP service: the agents as a separate Python service the app can call.

POST /brief  {trip_id, date, weather_scenario?}  ->  one Morning Brief in the output contract.
GET  /health                                     ->  liveness and budget status.
GET  /trips                                      ->  the trips the loader knows.
Interactive docs (the API contract) are served at /docs.
"""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mb.budget import BudgetExceeded
from mb.context import build_full_context
from mb.contracts import MorningBrief
from mb.pipeline import run_brief
from mb.services import Services


class BriefRequest(BaseModel):
    trip_id: str
    date: dt.date
    weather_scenario: str | None = None   # stored scenario for test days beyond the forecast range


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = Services()
    yield


class UTF8JSONResponse(JSONResponse):
    """Say the charset explicitly: some clients (Windows PowerShell 5.1) otherwise misread ₹ and °."""
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Travezy Morning Brief", version="0.1", lifespan=lifespan,
              default_response_class=UTF8JSONResponse)


@app.get("/health")
async def health():
    return {"ok": True, "budget": app.state.services.budget.status()}


@app.get("/trips")
async def trips():
    return app.state.services.loader.trip_ids()


@app.post("/brief", response_model=MorningBrief)
async def brief(req: BriefRequest):
    s: Services = app.state.services
    try:
        trip = s.loader.load(req.trip_id)
    except KeyError:
        raise HTTPException(404, f"Unknown trip '{req.trip_id}'")
    day = next((d for d in trip.days if d.date == req.date), None)
    if day is None:
        raise HTTPException(404, f"{req.trip_id} has no day on {req.date}")
    try:
        ctx = await build_full_context(trip, day.index, s.meteo, req.weather_scenario, places=s.places,
                                       routes=s.routes, run_id=f"api-{req.trip_id}-{req.date}",
                                       providers=s.providers)
        run = await run_brief(ctx, s.cfg, s.llm, run_id=f"api-{ctx.ctx_id}")
    except BudgetExceeded as e:
        raise HTTPException(503, f"Usage budget reached: {e}")
    return run.brief

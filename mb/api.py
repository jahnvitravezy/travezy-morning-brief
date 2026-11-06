"""HTTP service: the agents as a separate Python service the app can call.

POST /brief  {trip_id, date, weather_scenario?}  ->  one Morning Brief in the output contract, judged.
POST /judge  {trip_id, date, weather_scenario?, brief}  ->  the judge's verdict on a given brief.
GET  /outbox/{date}                              ->  briefs the batch runner prepared for that day.
GET  /preview/{date}/{trip_id}                   ->  a prepared brief as a phone-sized web page.
GET  /health                                     ->  liveness and budget status.
GET  /trips                                      ->  the trips the loader knows.
Interactive docs (the API contract) are served at /docs.
"""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from mb.auth import require_key
from mb.budget import BudgetExceeded
from mb.context import build_full_context
from mb.contracts import MorningBrief, Verdict
from mb.judge.judge import judge_brief
from mb.pipeline import agent_for, run_brief
from mb.services import Services, briefs_allowed
from mb.store import get_store


class BriefRequest(BaseModel):
    trip_id: str
    date: dt.date
    weather_scenario: str | None = None   # stored scenario for test days beyond the forecast range


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = Services()
    app.state.store = get_store()
    yield


class UTF8JSONResponse(JSONResponse):
    """Say the charset explicitly: some clients (Windows PowerShell 5.1) otherwise misread ₹ and °."""
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Travezy Morning Brief", version="0.1", lifespan=lifespan,
              default_response_class=UTF8JSONResponse)


@app.get("/health")
async def health():
    """Open, for load balancers and uptime checks: says only that the service is up."""
    return {"ok": True}


@app.get("/status", dependencies=[Depends(require_key)])
async def status():
    """This month's spend and models nearing end of life (behind the API key)."""
    s = app.state.services
    return {"budget": s.budget.status(), "model_warnings": s.model_warnings}


@app.get("/trips", dependencies=[Depends(require_key)])
async def trips():
    return app.state.services.loader.trip_ids()


class JudgeRequest(BriefRequest):
    brief: MorningBrief


async def _context(req: BriefRequest):
    s: Services = app.state.services
    try:
        trip = s.loader.load(req.trip_id)
    except KeyError:
        raise HTTPException(404, f"Unknown trip '{req.trip_id}'")
    day = next((d for d in trip.days if d.date == req.date), None)
    if day is None:
        raise HTTPException(404, f"{req.trip_id} has no day on {req.date}")
    return await build_full_context(trip, day.index, s.meteo, req.weather_scenario, places=s.places,
                                    routes=s.routes, run_id=f"api-{req.trip_id}-{req.date}",
                                    providers=s.providers)


@app.post("/brief", response_model=MorningBrief, dependencies=[Depends(require_key)])
async def brief(req: BriefRequest):
    s: Services = app.state.services
    if not briefs_allowed(s.cfg):
        raise HTTPException(503, "Briefs are switched off (kill switch)")
    try:
        ctx = await _context(req)
        run = await run_brief(ctx, s.cfg, s.llm, run_id=f"api-{ctx.ctx_id}")
    except BudgetExceeded as e:
        raise HTTPException(503, f"Usage budget reached: {e}")
    return run.brief


@app.post("/judge", response_model=Verdict, dependencies=[Depends(require_key)])
async def judge(req: JudgeRequest):
    """The judge as its own service: score any brief for a trip day against the rubric."""
    s: Services = app.state.services
    a4 = agent_for(s.cfg, "out.verdict")
    try:
        ctx = await _context(req)
        verdict, _ = await judge_brief(ctx, req.brief, s.cfg.rubrics[a4.rubric], a4, s.llm,
                                       run_id=f"api-judge-{ctx.ctx_id}", attempts_left=0)
    except BudgetExceeded as e:
        raise HTTPException(503, f"Usage budget reached: {e}")
    return verdict


@app.get("/outbox", dependencies=[Depends(require_key)])
async def outbox_due(due_from: dt.datetime, due_to: dt.datetime, include_sent: bool = False):
    """Briefs whose send time (UTC) is in [due_from, due_to): what the backend should send next.
    Early briefs can go out the evening before in UTC, so the backend asks by send time, not trip date."""
    items = app.state.store.due(due_from.isoformat(), due_to.isoformat(), unsent_only=not include_sent)
    return [{k: v for k, v in vars(i).items() if k != "brief"} | {"title": i.brief["notification"]["title"]}
            for i in items]


@app.get("/briefs/{brief_id}", dependencies=[Depends(require_key)])
async def stored_brief(brief_id: str):
    """One stored brief, as JSON, with its send time and whether it has been sent."""
    item = app.state.store.get(brief_id)
    if item is None:
        raise HTTPException(404, f"No stored brief {brief_id}")
    return {"brief_id": item.brief_id, "send_at_utc": item.send_at_utc, "sent_at": item.sent_at, "brief": item.brief}


@app.post("/briefs/{brief_id}/sent", dependencies=[Depends(require_key)])
async def mark_sent(brief_id: str):
    """The backend calls this after sending. Safe to call twice."""
    if not app.state.store.mark_sent(brief_id):
        raise HTTPException(404, f"No stored brief {brief_id}")
    return {"brief_id": brief_id, "sent": True}


@app.get("/outbox/{date}", dependencies=[Depends(require_key)])
async def outbox(date: dt.date):
    """What the batch runner has prepared for a date, in send order."""
    return [{k: v for k, v in vars(i).items() if k != "brief"} | {"title": i.brief["notification"]["title"]}
            for i in app.state.store.for_date(date.isoformat())]


@app.get("/preview/{date}/{trip_id}", response_class=HTMLResponse, dependencies=[Depends(require_key)])
async def preview(date: dt.date, trip_id: str):
    """How a prepared brief looks to the traveller (for review and demos)."""
    from mb.preview import render
    item = next((i for i in app.state.store.for_date(date.isoformat()) if i.trip_id == trip_id), None)
    if item is None:
        raise HTTPException(404, f"No prepared brief for {trip_id} on {date}")
    return HTMLResponse(render(MorningBrief.model_validate(item.brief)))

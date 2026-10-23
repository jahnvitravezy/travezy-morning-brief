import datetime as dt

import pytest
from fastapi.testclient import TestClient

import mb.api as api
from mb.agents.composer import safe_version
from mb.context import build_context
from mb.pipeline import BriefRun


@pytest.fixture
def client(monkeypatch):
    for k in ("GOOGLE_MAPS_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "test")

    async def fake_context(trip, index, *a, **kw):
        return build_context(trip, index)

    async def fake_run(ctx, cfg, llm, run_id, **kw):
        return BriefRun(brief=safe_version(ctx, "test"))

    monkeypatch.setattr(api, "build_full_context", fake_context)
    monkeypatch.setattr(api, "run_brief", fake_run)
    with TestClient(api.app) as c:
        yield c


def test_health_and_trips(client):
    assert client.get("/health").json()["ok"] is True
    assert "dubai_up01" in client.get("/trips").json()


def test_brief_returns_the_output_contract(client):
    r = client.post("/brief", json={"trip_id": "dubai_up01", "date": "2026-11-21"})
    assert r.status_code == 200
    body = r.json()
    assert body["trip_id"] == "dubai_up01" and body["timeline"] and "surprises" in body


def test_unknown_trip_and_date_are_404(client):
    assert client.post("/brief", json={"trip_id": "nope", "date": "2026-11-21"}).status_code == 404
    assert client.post("/brief", json={"trip_id": "dubai_up01", "date": "2030-01-01"}).status_code == 404

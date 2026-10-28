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


def test_judge_endpoint_returns_a_verdict(client, monkeypatch):
    from mb.contracts import Verdict

    async def fake_judge(ctx, brief, rubric, spec, llm, run_id, attempts_left):
        return Verdict(agent_id=spec.id, rubric_id=rubric.id, decision="pass", score=90, badge="green"), None

    monkeypatch.setattr(api, "judge_brief", fake_judge)
    b = client.post("/brief", json={"trip_id": "dubai_up01", "date": "2026-11-21"}).json()
    r = client.post("/judge", json={"trip_id": "dubai_up01", "date": "2026-11-21", "brief": b})
    assert r.status_code == 200 and r.json()["badge"] == "green"


def test_outbox_lists_prepared_briefs(client, tmp_path):
    from mb.store import FileStore, OutboxItem
    api.app.state.store = FileStore(tmp_path)
    api.app.state.store.save(OutboxItem("dubai_up01", "2026-11-21", "Asia/Dubai", "07:30", "2026-11-21T03:30:00+00:00",
                                        "pass", 90, "green", {"notification": {"title": "Day 2 in Dubai"}}))
    r = client.get("/outbox/2026-11-21").json()
    assert r == [{"trip_id": "dubai_up01", "date": "2026-11-21", "timezone": "Asia/Dubai", "send_at_local": "07:30",
                  "send_at_utc": "2026-11-21T03:30:00+00:00", "verdict": "pass", "score": 90, "badge": "green",
                  "title": "Day 2 in Dubai"}]

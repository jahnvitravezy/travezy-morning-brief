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
    with TestClient(api.app, headers={"X-API-Key": "test-key"}) as c:
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


def test_kill_switch_stops_new_briefs(client, monkeypatch):
    monkeypatch.setenv("MB_KILL_SWITCH", "1")
    assert client.post("/brief", json={"trip_id": "dubai_up01", "date": "2026-11-21"}).status_code == 503


def test_preview_renders_a_prepared_brief(client, tmp_path):
    from mb.agents.composer import safe_version
    from mb.context import build_context
    from mb.sources.itinerary import FileLoader
    from mb.store import FileStore, OutboxItem
    b = safe_version(build_context(FileLoader().load("dubai_up01"), 1), "t")
    api.app.state.store = FileStore(tmp_path)
    api.app.state.store.save(OutboxItem("dubai_up01", "2026-11-21", "Asia/Dubai", "07:30", "2026-11-21T03:30:00Z",
                                        "pass", 90, "green", b.model_dump(mode="json")))
    r = client.get("/preview/2026-11-21/dubai_up01")
    assert r.status_code == 200 and b.notification.title in r.text and not b.notification.title.startswith("Day ") and "Al Fahidi" in r.text
    assert client.get("/preview/2026-11-21/nobody").status_code == 404


def test_calls_without_the_right_key_are_refused(client, monkeypatch):
    assert client.get("/trips", headers={"X-API-Key": "wrong"}).status_code == 401
    with TestClient(api.app) as bare:
        assert bare.get("/trips").status_code == 401
        assert bare.get("/health").status_code == 200          # health stays open
        assert bare.get("/trips", params={"key": "test-key"}).status_code == 200
    monkeypatch.delenv("MB_API_KEY")
    assert client.get("/trips").status_code == 503              # no key configured: fail closed


def test_badge_covers_only_the_ai_written_parts():
    from mb.contracts import AI_PARTS, Confidence
    from mb.preview import render
    assert Confidence().applies_to == AI_PARTS == ["heads_ups", "surprises", "local_tip"]
    from mb.sources.itinerary import FileLoader
    html = render(safe_version(build_context(FileLoader().load("dubai_up01"), 1), "test"))
    assert html.index("From your itinerary") < html.index("Suggestions for today") < html.index("badge style")

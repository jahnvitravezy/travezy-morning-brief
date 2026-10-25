"""Runner and blinded pack, on stub agents: no network, no spend."""
import json

import pytest
from openpyxl import load_workbook

import mb.evals.blinded_pack as pack
import mb.evals.runner as runner
from mb.agents.composer import safe_version
from mb.config import load_config
from mb.context import build_context
from mb.evals.testset import REQUIRED_COVERAGE, load_testset
from mb.pipeline import BriefRun
from mb.sources.itinerary import FileLoader


class FakeServices:
    def __init__(self):
        self.cfg, self.loader = load_config(), FileLoader()
        self.llm = self.meteo = self.places = self.routes = None
        self.providers = []


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CONTEXTS", tmp_path / "contexts")
    monkeypatch.setattr(runner, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(pack, "CONTEXTS", tmp_path / "contexts")
    monkeypatch.setattr(pack, "RUNS", tmp_path / "runs")
    built = []

    async def fake_context(trip, index, meteo, weather, **kw):
        built.append((trip.trip_id, index))
        return build_context(trip, index)

    async def fake_run(ctx, cfg, llm, run_id, budget_s=90):
        return BriefRun(brief=safe_version(ctx, run_id), events=["stub"])

    monkeypatch.setattr(runner, "build_full_context", fake_context)
    monkeypatch.setattr(runner, "run_brief", fake_run)
    return tmp_path, built


async def test_runner_stores_briefs_records_and_reuses_contexts(run_dir):
    tmp, built = run_dir
    ts = load_testset()
    recs = await runner.run_testset("t1", ts, FakeServices())
    assert len(recs) == len(ts.days) == len(built)
    lines = (tmp / "runs" / "t1" / "records.jsonl").read_text().splitlines()
    assert len(lines) == len(ts.days) and json.loads(lines[0])["ctx_id"]
    assert len(list((tmp / "runs" / "t1" / "briefs").glob("*.json"))) == len(ts.days)
    await runner.run_testset("t2", ts, FakeServices())
    assert len(built) == len(ts.days)                   # second run: contexts loaded, not rebuilt
    assert runner.summary(recs)["days"] == len(ts.days)


async def test_blinded_pack_hides_scores_and_keeps_coverage(run_dir):
    tmp, _ = run_dir
    await runner.run_testset("t1", load_testset(), FakeServices())
    pack.main("t1")
    out = tmp / "runs" / "t1"
    page = (out / "pack" / "briefs.html").read_text()
    assert page.count("class=card") == 20
    for hidden in ("openai", "gpt", "badge", "confidence", "td."):
        assert hidden not in page.lower()
    key = json.loads((out / "pack_key.json").read_text())["codes"]
    assert list(key) == [f"B{i:02d}" for i in range(1, 21)]
    days = {r["day_id"]: r for r in map(json.loads, (out / "records.jsonl").read_text().splitlines())}
    assert REQUIRED_COVERAGE <= {c for v in key.values() for c in days[v["day_id"]]["covers"]}
    ws = load_workbook(out / "pack" / "scoring_sheet.xlsx")["Scores"]
    assert [c.value for c in ws[1]][:6] == ["code", "accuracy", "relevance", "feasibility", "format", "tone"]
    assert ws.max_row == 21


def test_choose_is_repeatable():
    recs = [{"day_id": d.id, "covers": d.covers} for d in load_testset().days]
    assert pack.choose(recs) == pack.choose(recs)
